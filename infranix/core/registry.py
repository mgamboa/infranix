"""Registry de colecciones — descubrimiento y carga de providers.

Descubre colecciones de dos formas:
  1. Entry points del grupo `infranix.collections` (paquetes externos que
     declaran en su pyproject.toml el punto de entrada).
  2. Colecciones *builtin*: subpaquetes bajo `infranix.collections.*` que
     implementan el protocolo (sirven como referencia y no requieren instalar).

El core pregunta al registry "quién tiene capability X" y NUNCA importa
internals de una colección: solo la clase Provider declarada.
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
    """Registro de una colección descubierta."""
    name: str
    version: str
    description: str
    capabilities: frozenset[Capability]
    provider: type[PluginProvider]
    installed: bool   # True si viene de un entry point externo
    enabled: bool = True


class CollectionRegistry:
    """Registro central de colecciones disponibles."""

    def __init__(self):
        self._records: dict[str, CollectionRecord] = {}
        self._disabled: set[str] = set()

    # ── descubrimiento ──

    @staticmethod
    def _discover_entrypoints() -> list[CollectionRecord]:
        """Carga providers declarados en [project.entry-points]."""
        records: list[CollectionRecord] = []
        try:
            eps: list[EntryPoint] = metadata.entry_points(
                group=ENTRY_GROUP)
        except Exception:
            eps = []
        for ep in eps:
            try:
                # value puede ser "modulo:ClassName" o solo "modulo"
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
                print(f"[registry] colección '{ep.name}' no cargó: {e}")
        return records

    @staticmethod
    def _discover_builtin() -> list[CollectionRecord]:
        """Descubre subpaquetes under `infranix.collections.*`."""
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
                print(f"[registry] builtin '{entry.name}' no cargó: {e}")
        return records

    def discover(self) -> "CollectionRegistry":
        """(Re)descubre entry points externos + builtins."""
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
        """Devuelve la instancia de la colección con la capability pedida.

        `prefer`: nombres de colección a priorizar (p.ej. las declaradas en el
        manifiesto). Si se pide explícitamente una, se usa esa. Si no, se
        prefieren las colecciones *builtin* (de confianza del core) sobre las
        externas; en igualdad, orden alfabético.
        """
        candidates = self.with_capability(cap)
        if not candidates:
            return None

        # 1) Si se pidió explícitamente una colección habilitada, priorizarla
        if prefer:
            for rec in candidates:
                if rec.name in prefer:
                    return self._instantiate(rec)

        # 2) Preferir builtins (vienen con el core) salvo que la externa sea la única
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
            print(f"[registry] instanciando '{rec.name}': {e}")
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

    # ── auto-instalación de requisitos (behaves like ansible-galaxy) ──

    def ensure_required(self, requirements) -> list[str]:
        """Asegura que las colecciones requeridas estén disponibles.

        `requirements`: lista de CollectionRequirement (del manifiesto).
        Para cada una: si ya está (builtin o externa) ok; si es una builtin
        pero está deshabilitada, la habilita; si es externa y no está, la
        instala (pip o tar.gz) y rediscover. Devuelve lista de mensajes.
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

            # Ya instalada + habilitada -> ok
            if rec is not None and self.is_enabled(rec.name):
                messages.append(f"colección '{rec.name}' lista.")
                continue

            # Builtin deshabilitada -> reactivar (requisito declara que se necesita)
            if rec is not None and rec.installed is False:
                if rec.name not in self._disabled:
                    messages.append(f"colección '{rec.name}' lista (builtin).")
                    continue
                self._disabled.discard(rec.name)
                messages.append(f"colección '{rec.name}' habilitada (builtin).")
                continue

            # Externa faltante -> instalar según source
            if req.source == CollectionSource.ARCHIVE and req.path:
                try:
                    self._install_archive(req.path, req.name)
                    messages.append(f"colección '{req.name}' instalada desde "
                                    f"tarball {req.path}.")
                    self.discover()
                except Exception as e:
                    messages.append(f"ERROR instalando '{req.name}' desde {req.path}: {e}")
                continue

            # pip install
            pkg = self._pip_pkg_name(req)
            try:
                res = subprocess.run(
                    [sys.executable, "-m", "pip", "install", pkg],
                    capture_output=True, text=True)
                if res.returncode != 0:
                    messages.append(
                        f"ERROR instalando '{req.name}': {res.stderr[-400:].strip()}")
                else:
                    messages.append(f"colección '{req.name}' instalada "
                                    f"(pip: {pkg}).")
                    self.discover()
            except Exception as e:
                messages.append(f"ERROR instalando '{req.name}': {e}")
        return messages

    @staticmethod
    def _pip_pkg_name(req) -> str:
        # Si el requirement da un path de git/url/tar.gz lo pasa directo
        if req.path and ("://" in req.path or req.path.endswith(".tar.gz")):
            return req.path
        base = req.name if req.name.startswith("infra-collection") \
            else f"infra-collection-{req.name}"
        if req.version:
            base += f"=={req.version}"
        return base

    @staticmethod
    def _install_archive(tarball: str, name: str) -> None:
        """Instala una colección desde un tar.gz local (offline).

        Descomprime a un directorio temporal y hace `pip install` del paquete
        dentro (o directamente del tarball si es el propio paquete python).
        """
        import subprocess
        import sys
        import tarfile
        import tempfile
        from pathlib import Path

        tb = Path(tarball)
        if not tb.exists():
            raise FileNotFoundError(f"tarball no encontrado: {tarball}")
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(tb) as tar:
                tar.extractall(tmp)
            # buscar el paquete python con entry point, o instalar el tarball
            # directamente (pip acepta .tar.gz de un sdist)
            res = subprocess.run(
                [sys.executable, "-m", "pip", "install", str(tb)],
                capture_output=True, text=True)
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


# registro global compartido (cache)
_registry: Optional[CollectionRegistry] = None


def get_registry() -> CollectionRegistry:
    global _registry
    if _registry is None:
        _registry = CollectionRegistry().discover()
    return _registry