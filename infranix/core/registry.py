"""Collection registry — discovery and loading of providers.

Discovers collections in two ways:
  1. Entry points in the `infranix.collections` group (external packages that
     declare the entry point in their pyproject.toml).
  2. *Builtin* collections: subpackages under `infranix.collections.*` that
     implement the protocol (they serve as reference and need no install).

The core asks the registry "who has capability X" and NEVER imports a
collection's internals: only the declared Provider class.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import metadata, import_module
from importlib.metadata import EntryPoint
from pathlib import Path
from typing import Optional

from infranix.pluginbase import Capability, PluginProvider

ENTRY_GROUP = "infranix.collections"
BUILTIN_PACKAGE = "infranix.collections"


@dataclass
class CollectionRecord:
    """Record of a discovered collection."""
    name: str
    version: str
    description: str
    capabilities: frozenset[Capability]
    provider: type[PluginProvider]
    installed: bool   # True if it comes from an external entry point
    enabled: bool = True


class CollectionRegistry:
    """Central registry of available collections."""

    def __init__(self):
        self._records: dict[str, CollectionRecord] = {}
        self._disabled: set[str] = set()

    # ── discovery ──

    @staticmethod
    def _discover_entrypoints() -> list[CollectionRecord]:
        """Load providers declared in [project.entry-points]."""
        records: list[CollectionRecord] = []
        try:
            eps: list[EntryPoint] = metadata.entry_points(
                group=ENTRY_GROUP)
        except Exception:
            eps = []
        for ep in eps:
            try:
                # value may be "module:ClassName" or just "module"
                value = ep.value
                if ":" in value:
                    mod_name, attr = value.split(":", 1)
                else:
                    mod_name, attr = value, "provider"
                mod = import_module(mod_name)
                provider = getattr(mod, attr, None)
                if not provider:
                    continue
                records.append(_make_record(provider,
                                            name=ep.name,
                                            installed=True))
            except Exception as e:
                print(f"[registry] collection '{ep.name}' failed to load: {e}")
        return records

    @staticmethod
    def _discover_builtin() -> list[CollectionRecord]:
        """Discover subpackages under `infranix.collections.*`."""
        records: list[CollectionRecord] = []
        pkg_dir = Path(__file__).resolve().parent.parent / "collections"
        if not pkg_dir.exists():
            return records
        for entry in sorted(pkg_dir.iterdir()):
            if not (entry / "__init__.py").exists():
                continue
            try:
                mod = import_module(f"{BUILTIN_PACKAGE}.{entry.name}")
                for attr in ("provider", "Provider"):
                    cls = getattr(mod, attr, None)
                    if cls is not None:
                        records.append(_make_record(cls, installed=False))
                        break
            except Exception as e:
                print(f"[registry] builtin '{entry.name}' failed to load: {e}")
        return records

    def discover(self) -> "CollectionRegistry":
        """(Re)discover external entry points + builtins."""
        self._records.clear()
        for rec in self._discover_entrypoints() + self._discover_builtin():
            self._records[rec.name] = rec
        return self

    # ── consulta ──

    def all(self) -> list[CollectionRecord]:
        return sorted(self._records.values(), key=lambda r: r.name)

    def get(self, name: str) -> Optional[CollectionRecord]:
        return self._records.get(name)

    def is_enabled(self, name: str) -> bool:
        return name not in self._disabled

    def with_capability(self, cap: Capability) -> list[CollectionRecord]:
        return [r for r in self._records.values()
                if cap in r.capabilities and self.is_enabled(r.name)]

    def resolve(self, cap: Capability,
                prefer: Optional[list[str]] = None
                ) -> Optional[PluginProvider]:
        """Return the collection instance providing the requested capability.

        `prefer`: collection names to prioritize (e.g. those declared in the
        manifest). If one is explicitly requested, it is used. Otherwise *builtin*
        collections (trusted by the core) are preferred over external ones; on a
        tie, alphabetical order.
        """
        candidates = self.with_capability(cap)
        if not candidates:
            return None

        # 1) If an enabled collection was explicitly requested, prioritize it
        if prefer:
            for rec in candidates:
                if rec.name in prefer:
                    return self._instantiate(rec)

        # 2) Prefer builtins (they ship with the core) unless external is the only one
        chosen = None
        for rec in candidates:
            if rec.installed is False:      # builtin
                chosen = rec
                break
        if chosen is None:
            chosen = candidates[0]
        return self._instantiate(chosen)

    @staticmethod
    def _instantiate(rec: "CollectionRecord") -> Optional[PluginProvider]:
        try:
            return rec.provider()
        except Exception as e:
            print(f"[registry] instantiating '{rec.name}': {e}")
            return None

    # ── enable/disable ──

    def enable(self, name: str) -> bool:
        if name in self._records:
            self._disabled.discard(name)
            return True
        return False

    def disable(self, name: str) -> bool:
        if name in self._records:
            self._disabled.add(name)
            return True
        return False

    # ── auto-installation of requirements (behaves like ansible-galaxy) ──

    def ensure_required(self, requirements) -> list[str]:
        """Ensure the required collections are available.

        `requirements`: list of CollectionRequirement (from the manifest).
        For each one: if already present (builtin or external) OK; if it is a
        builtin but disabled, enable it; if external and missing, install it
        (pip or tar.gz) and rediscover. Returns a list of messages.
        """
        import subprocess
        import sys
        from infranix.models import CollectionSource

        messages: list[str] = []

        def _find(req_name: str):
            return self._records.get(req_name) or next(
                (r for r in self._records.values()
                 if r.name.replace("infra-collection-", "") == req_name
                 .replace("infra-collection-", "")), None)

        for req in requirements:
            rec = _find(req.name)

            # Already installed + enabled -> ok
            if rec is not None and self.is_enabled(rec.name):
                messages.append(f"collection '{rec.name}' ready.")
                continue

            # Builtin disabled -> reactivate (requirement says it is needed)
            if rec is not None and rec.installed is False:
                if rec.name not in self._disabled:
                    messages.append(f"collection '{rec.name}' ready (builtin).")
                    continue
                self._disabled.discard(rec.name)
                messages.append(f"collection '{rec.name}' enabled (builtin).")
                continue

            # External missing -> install per source
            if req.source == CollectionSource.ARCHIVE and req.path:
                try:
                    self._install_archive(req.path, req.name)
                    messages.append(f"collection '{req.name}' installed from "
                                    f"tarball {req.path}.")
                    self.discover()
                except Exception as e:
                    messages.append(f"ERROR installing '{req.name}' from {req.path}: {e}")
                continue

            # ansible-galaxy collection install
            if req.source == CollectionSource.GALAXY:
                try:
                    self._install_galaxy(req.name)
                    messages.append(f"collection '{req.name}' installed (galaxy).")
                    self.discover()
                except Exception as e:
                    messages.append(f"ERROR installing '{req.name}' (galaxy): {e}")
                continue

            # pip install
            pkg = self._pip_pkg_name(req)
            try:
                res = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg],
                    capture_output=True, text=True)
                if res.returncode != 0:
                    messages.append(
                        f"ERROR installing '{req.name}': {res.stderr[-400:].strip()}")
                else:
                    messages.append(f"collection '{req.name}' installed "
                                    f"(pip: {pkg}).")
                    self.discover()
            except Exception as e:
                messages.append(f"ERROR installing '{req.name}': {e}")
        return messages

    @staticmethod
    def _pip_pkg_name(req) -> str:
        # If the requirement gives a git/url/tar.gz path, pass it straight through
        if req.path and ("://" in req.path or req.path.endswith(".tar.gz")):
            return req.path
        base = req.name if req.name.startswith("infra-collection") \
            else f"infra-collection-{req.name}"
        if req.version:
            base += f"=={req.version}"
        return base

    @staticmethod
    def _install_archive(tarball: str, name: str) -> None:
        """Install a collection from a local tar.gz (offline).

        Unpacks to a temporary directory and `pip install`s the package inside
        (or directly from the tarball if it is itself the python package).
        """
        import subprocess
        import sys
        import tarfile
        import tempfile
        from pathlib import Path

        tb = Path(tarball)
        if not tb.exists():
            raise FileNotFoundError(f"tarball not found: {tarball}")
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(tb) as tar:
                tar.extractall(tmp)
            # find the python package with the entry point, or install the
            # tarball directly (pip accepts .tar.gz of an sdist)
            res = subprocess.run(
                [sys.executable, "-m", "pip", "install", str(tb)],
                capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(res.stderr[-500:].strip())

    @staticmethod
    def _install_galaxy(name: str) -> None:
        """Install an Ansible Galaxy collection with ansible-galaxy."""
        import subprocess
        import shutil
        if shutil.which("ansible-galaxy") is None:
            raise RuntimeError("'ansible-galaxy' not found in PATH.")
        res = subprocess.run(
            ["ansible-galaxy", "collection", "install", name],
            capture_output=True, text=True, timeout=900)
        if res.returncode != 0:
            raise RuntimeError(res.stderr[-500:].strip())


def _make_record(provider_cls: type[PluginProvider],
                 name: Optional[str] = None,
                 installed: bool = False) -> CollectionRecord:
    return CollectionRecord(
        name=name or getattr(provider_cls, "name", "") or
             provider_cls.__module__.split(".")[-1],
        version=getattr(provider_cls, "version", "0.1.0"),
        description=getattr(provider_cls, "description", ""),
        capabilities=getattr(provider_cls, "capabilities", frozenset()),
        provider=provider_cls,
        installed=installed,
    )


# shared global registry (cache)
_registry: Optional[CollectionRegistry] = None


def get_registry() -> CollectionRegistry:
    global _registry
    if _registry is None:
        _registry = CollectionRegistry().discover()
    return _registry