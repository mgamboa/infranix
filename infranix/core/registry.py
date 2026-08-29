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
                mod = import_module(ep.value)
                provider = getattr(mod, "provider", None) or getattr(
                    mod, "Provider", None)
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

    def resolve(self, cap: Capability) -> Optional[PluginProvider]:
        """Devuelve la instancia de la colección con la capability pedida.

        Si hay varias, se usa la primera habilitada (orden alfabético).
        """
        for rec in self.with_capability(cap):
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