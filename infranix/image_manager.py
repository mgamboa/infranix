"""Image Manager — Fase 3.

Resuelve la disponibilidad de imágenes/templates solicitadas por el manifiesto.

Flujo cuando una imagen no está disponible en el hypervisor:
  1. Resolver la fuente de descarga (mirror oficial por distro+versión).
  2. Descargar el ISO/OVA/cloud-image a un cache local (~/.infranix/images).
  3. Subirlo al datastore del ESXi (carpeta 'ISO') usando govc.
  4. Registrar en el catálogo local para evitar re-descargas.

En fases posteriores: construir un template booteable/clonable con Packer
(kickstart / cloud-init / autounattend) para que Terraform lo cloné.

Esto resuelve tu requerimiento clave: si la versión de Linux pedida no está
en el hypervisor, el sistema la baja y la sube para hacerla disponible.
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


# ─────────────────────────── Fuentes de descarga ───────────────────────────

# Cada resolver devuelve la URL más probable del ISO para (distro, version).
def _rocky(distro: str, version: str) -> str:
    # Rocky mirrors: https://dl.rockylinux.org/pub/rocky/<ver>/isos/x86_64/
    return (f"https://dl.rockylinux.org/pub/rocky/{version}/isos/x86_64/"
            f"Rocky-{version}-x86_64-minimal.iso")


def _rhel(distro: str, version: str) -> Optional[str]:
    # RHEL requiere suscripción; los ISOs no son de descarga pública libre.
    # Se usa un mirror interno (p. ej. la ISO que ya está en el datastore).
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
    """Registro de una imagen gestionada por el Image Manager."""
    name: str              # 'rhel-9.5'
    distro: str            # 'rhel'
    version: str           # '9.5'
    source_url: str = ""
    local_path: Optional[Path] = None
    datastore_path: Optional[str] = None  # ej: /ISO/rhel-9.5.iso
    status: str = "unknown"  # missing | downloading | available | template-ready


@dataclass
class ImageManagerResult:
    record: ImageRecord
    action: str                 # none | downloaded | uploaded | template-required
    message: str = ""


class ImageManager:
    """Gestiona la disponibilidad de imágenes en el hypervisor."""

    def __init__(self, config: InfraConfig):
        self.config = config
        self.cache_dir = config.image_cache
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.catalog_file = self.cache_dir / "catalog.json"

    # ── Catálogo local (evita re-descargar) ──
    def _load_catalog(self) -> dict:
        import json
        if self.catalog_file.exists():
            return json.loads(self.catalog_file.read_text())
        return {}

    def _save_catalog(self, catalog: dict) -> None:
        import json
        self.catalog_file.write_text(json.dumps(catalog, indent=2))

    # ── Resolución de URL ──
    def resolve_source(self, distro: str, version: str) -> str:
        resolver = RESOLVERS.get(distro.lower())
        if not resolver:
            raise ValueError(f"No hay resolver para distro '{distro}'")
        url = resolver(distro.lower(), version)
        if not url:
            raise ValueError(
                f"RHEL {version} requiere suscripción; usa un mirror interno "
                f"(p. ej. el ISO rhel-{version} ya subido al datastore).")
        return url

    # ── Descarga ──
    def _download(self, url: str, dest: Path) -> Path:
        if dest.exists() and dest.stat().st_size > 0:
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"    Descargando {url} ...")
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

    # ── Subir al datastore via govc ──
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
            raise RuntimeError(f"govc datastore.upload falló: {res.stderr.strip()}")
        return f"/{dst}"

    # ── Orquestación principal ──
    def ensure(self, name: str, distro: str, version: str,
               available_remotes: Optional[list[str]] = None) -> ImageManagerResult:
        """Asegura que la imagen pedida esté disponible en el hypervisor.

        available_remotes: nombres de ISO ya presentes en el datastore.
        Si la imagen (por distro+versión) ya existe como ISO remoto, se
        considera disponible sin descargar.
        """
        record = ImageRecord(name=name, distro=distro, version=version)
        catalog = self._load_catalog()
        cached = catalog.get(name)

        # 1) ¿Ya hay un ISO remoto en el datastore que matchee distro+version?
        if available_remotes:
            match = self._match_remote(available_remotes, distro, version)
            if match:
                record.datastore_path = f"/ISO/{match}"
                record.status = "available"
                return ImageManagerResult(
                    record, "none",
                    f"imagen '{name}' ya disponible en datastore como '{match}'.")

        # 2) ¿Está en el catálogo local (ya descargado/subido antes)?
        if cached and cached.get("datastore_path"):
            record = ImageRecord(**cached)
            record.status = "available"
            return ImageManagerResult(
                record, "none",
                f"imagen '{name}' en catálogo: {cached['datastore_path']}.")

        # 3) Descargar + subir
        try:
            url = self.resolve_source(distro, version)
        except ValueError as e:
            record.status = "template-required"
            return ImageManagerResult(
                record, "template-required",
                f"Sin fuente libre para {distro} {version}: {e}")

        local_name = self._iso_local_name(name, distro, version)
        local_path = self.cache_dir / local_name
        try:
            local_path = self._download(url, local_path)
        except Exception as e:
            record.status = "missing"
            return ImageManagerResult(record, "none",
                                      f"Error descargando: {e}")

        try:
            remote = self._upload_iso(local_path, local_name)
            record.local_path = str(local_path)
            record.datastore_path = remote
            record.status = "available"
            catalog[name] = vars(record)
            self._save_catalog(catalog)
            return ImageManagerResult(
                record, "uploaded",
                f"Imagen '{name}' descargada y subida a {remote}.")
        except Exception as e:
            record.status = "downloading"
            return ImageManagerResult(record, "none",
                                      f"Subida falló (descarga en cache): {e}")

    # ── Heurística de matching ──
    @staticmethod
    def _match_remote(remotes: list[str], distro: str, version: str) -> Optional[str]:
        """Busca un ISO cuyo nombre contenga la distro Y la versión."""
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
