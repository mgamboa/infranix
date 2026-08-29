"""Image Manager — Phase 3.

Resolves the availability of images/templates requested by the manifest.

Flow when an image is not available on the hypervisor:
  1. Resolve the download source (official mirror by distro+version).
  2. Download the ISO/OVA/cloud-image to a local cache (~/.infranix/images).
  3. Upload it to the ESXi datastore (the 'ISO' folder) using govc.
  4. Register it in the local catalog to avoid re-downloads.

In later phases: build a bootable/cloneable template with Packer
(kickstart / cloud-init / autounattend) so that Terraform can clone it.

This solves your key requirement: if the requested Linux version is not
on the hypervisor, the system downloads and uploads it to make it available.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Callable

import requests

from infranix.config import InfraConfig
from infranix.adapters.discovery import ESXiScanner
from infranix.models import Image


# ─────────────────────────── Download sources ───────────────────────────

# Each resolver returns the most probable ISO URL for (distro, version).
def _rocky(distro: str, version: str) -> str:
    # Rocky mirrors: https://dl.rockylinux.org/pub/rocky/<ver>/isos/x86_64/
    return (f"https://dl.rockylinux.org/pub/rocky/{version}/isos/x86_64/"
            f"Rocky-{version}-x86_64-minimal.iso")


def _rhel(distro: str, version: str) -> Optional[str]:
    # RHEL requires a subscription; the ISOs are not free for public download.
    # An internal mirror is used (e.g. the ISO already on the datastore).
    return None


def _ubuntu(distro: str, version: str) -> str:
    return (f"https://releases.ubuntu.com/{version}/"
            f"ubuntu-{version}-live-server-amd64.iso")


def _debian(distro: str, version: str) -> str:
    return (f"https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/"
            f"debian-{version}-amd64-netinst.iso")


def _centos_stream(distro: str, version: str) -> str:
    return (f"https://mirror.stream.centos.org/{version}-stream/BaseOS/x86_64/"
            f"iso/CentOS-Stream-{version}-x86_64-latest-boot.iso")


RESOLVERS: dict[str, Callable[[str, str], Optional[str]]] = {
    "rocky": _rocky,
    "rhel": _rhel,
    "ubuntu": _ubuntu,
    "debian": _debian,
    "centos": _centos_stream,
}


@dataclass
class ImageRecord:
    """Record of an image managed by the Image Manager."""
    name: str              # 'rhel-9.5'
    distro: str            # 'rhel'
    version: str           # '9.5'
    source_url: str = ""
    local_path: Optional[Path] = None
    datastore_path: Optional[str] = None  # e.g. /ISO/rhel-9.5.iso
    status: str = "unknown"  # missing | downloading | available | template-ready


@dataclass
class ImageManagerResult:
    record: ImageRecord
    action: str                 # none | downloaded | uploaded | template-required
    message: str = ""


class ImageManager:
    """Manages the availability of images on the hypervisor."""

    def __init__(self, config: InfraConfig):
        self.config = config
        self.cache_dir = config.image_cache
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.catalog_file = self.cache_dir / "catalog.json"

    # ── Local catalog (avoids re-downloading) ──
    def _load_catalog(self) -> dict:
        import json
        if self.catalog_file.exists():
            return json.loads(self.catalog_file.read_text())
        return {}

    def _save_catalog(self, catalog: dict) -> None:
        import json
        self.catalog_file.write_text(json.dumps(catalog, indent=2))

    # ── URL resolution ──
    def resolve_source(self, distro: str, version: str) -> str:
        resolver = RESOLVERS.get(distro.lower())
        if not resolver:
            raise ValueError(f"No resolver for distro '{distro}'")
        url = resolver(distro.lower(), version)
        if not url:
            raise ValueError(
                f"RHEL {version} requires a subscription; use an internal mirror "
                f"(e.g. the rhel-{version} ISO already uploaded to the datastore).")
        return url

    # ── Download ──
    def _download(self, url: str, dest: Path) -> Path:
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"    Downloading {url} ...")
        with requests.get(url, stream=True, timeout=(30, 600), allow_redirects=True) as r:
            r.raise_for_status()
            total = int(r.headers.get("Content-Length", 0))
            done = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = int(done * 100 / total)
                        if pct % 25 == 0:
                            print(f"      {pct}%")
        return dest

    # ── Upload to the datastore via govc ──
    def _upload_iso(self, local_path: Path, target_name: str) -> str:
        url = (f"https://root:{self.config.password}@{self.config.host}/sdk")
        env = dict(os.environ)
        env["GOVC_URL"] = url
        env["GOVC_INSECURE"] = "true"
        dst = f"ISO/{target_name}"
        res = subprocess.run(
            ["govc", "datastore.upload", str(local_path), dst],
            capture_output=True, text=True, env=env, timeout=1800)
        if res.returncode != 0:
            raise RuntimeError(f"govc datastore.upload failed: {res.stderr.strip()}")
        return f"/{dst}"

    # ── Main orchestration ──
    def ensure(self, name: str, distro: str, version: str,
               available_remotes: Optional[list[str]] = None) -> ImageManagerResult:
        """Ensure the requested image is available on the hypervisor.

        available_remotes: names of ISOs already present on the datastore.
        If the image (by distro+version) already exists as a remote ISO, it is
        considered available without downloading.
        """
        record = ImageRecord(name=name, distro=distro, version=version)
        catalog = self._load_catalog()
        cached = catalog.get(name)

        # 1) Is there already a remote ISO on the datastore matching distro+version?
        if available_remotes:
            match = self._match_remote(available_remotes, distro, version)
            if match:
                record.datastore_path = f"/ISO/{match}"
                record.status = "available"
                return ImageManagerResult(
                    record, "none",
                    f"image '{name}' already available on the datastore as '{match}'.")

        # 2) Is it in the local catalog (already downloaded/uploaded before)?
        if cached and cached.get("datastore_path"):
            record = ImageRecord(**cached)
            record.status = "available"
            return ImageManagerResult(
                record, "none",
                f"image '{name}' in catalog: {cached['datastore_path']}.")

        # 3) Download + upload
        try:
            url = self.resolve_source(distro, version)
        except ValueError as e:
            record.status = "template-required"
            return ImageManagerResult(
                record, "template-required",
                f"No free source for {distro} {version}: {e}")

        local_name = self._iso_local_name(name, distro, version)
        local_path = self.cache_dir / local_name
        try:
            local_path = self._download(url, local_path)
        except Exception as e:
            record.status = "missing"
            return ImageManagerResult(record, "none",
                                      f"Download error: {e}")

        try:
            remote = self._upload_iso(local_path, local_name)
            record.local_path = str(local_path)
            record.datastore_path = remote
            record.status = "available"
            catalog[name] = vars(record)
            self._save_catalog(catalog)
            return ImageManagerResult(
                record, "uploaded",
                f"Image '{name}' downloaded and uploaded to {remote}.")
        except Exception as e:
            record.status = "downloading"
            return ImageManagerResult(record, "none",
                                      f"Upload failed (download cached): {e}")

    # ── Matching heuristic ──
    @staticmethod
    def _match_remote(remotes: list[str], distro: str, version: str) -> Optional[str]:
        """Find an ISO whose name contains the distro AND the version."""
        d = ImageManager._norm(distro)
        v = ImageManager._norm(version)
        if not d or not v:
            return None
        for r in remotes:
            n = ImageManager._norm(r.replace(".iso", ""))
            if d in n and v in n:
                return r
        return None

    @staticmethod
    def _norm(s: str) -> str:
        return re.sub(r"[^a-z0-9]", "", s.lower())

    @staticmethod
    def _iso_local_name(name: str, distro: str, version: str) -> str:
        safe = re.sub(r"[^a-z0-9.-]", "-", name.lower())
        return f"{safe}-{distro}{version}.iso"

    # ── Template build with Packer (via the BUILD collection) ──
    def build_template(self, name: str, distro: str, version: str,
                       checksum: str = "none") -> ImageManagerResult:
        """Build a cloneable template from the ISO (Packer).

        Requires the ISO to be in the local cache (ensured with `ensure`).
        The logic lives in the collection with the BUILD capability: the core
        only delegates through the protocol, it does not import Packer internals.
        """
        from infranix.core.registry import get_registry
        from infranix.pluginbase import Capability, PluginContext

        record = ImageRecord(name=name, distro=distro, version=version)
        local_name = self._iso_local_name(name, distro, version)
        local_path = self.cache_dir / local_name

        if not local_path.exists():
            return ImageManagerResult(
                record, "none",
                f"ISO '{local_name}' is not in cache. Run 'infra image ensure' "
                f"first (or set the cache in ~/.infranix/images).")

        provider = get_registry().resolve(Capability.BUILD)
        if provider is None:
            return ImageManagerResult(
                record, "none",
                "No collection with the BUILD capability enabled. "
                "Check 'infra collection list' (Packer).")

        image = Image(name=name, distro=distro, version=version,
                      build={"builder": "packer"})
        work_dir = self.cache_dir / f"packer-{name}"
        ctx = PluginContext(config=self.config, manifest=None,
                            image=image,
                            work_dir=work_dir,
                            extras={"iso_path": str(local_path)})
        report = provider.apply(ctx)
        print(f"    [{provider.name}] {report.message}")
        if report.ok:
            record.status = "template-ready"
            record.local_path = str(local_path)
            catalog = self._load_catalog()
            catalog[name] = vars(record)
            self._save_catalog(catalog)
            return ImageManagerResult(
                record, "template-ready",
                f"Template '{name}' built with Packer and ready to clone.")
        return ImageManagerResult(
            record, "none", report.message)
