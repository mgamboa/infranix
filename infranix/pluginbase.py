"""Plugin API — contrato de colecciones para InfraNix.

El núcleo (core) es delgado: conoce el esquema del manifiesto, el planner, el
safety gate y el orquestador. Toda funcionalidad adicional (provisión con
Terraform, configuración con Ansible, escaneo, imágenes, builders con Packer...)
se entrega como *colecciones* que implementan este protocolo.

Una colección es un paquete Python que declara un punto de entrada:

    [project.entry-points."infranix.collections"]
    packer = "infra_collection_packer:provider"

Y expone una clase `Provider(PluginProvider)` con `name` y `capabilities`.

Si una colección falla (p.ej. Packer), el fallo queda confinado ahí dentro: el
core nunca importa internals de una colección, solo llama al protocolo.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from infranix.config import InfraConfig
from infranix.models import Capability, Manifest


# re-export para compatibilidad (antes Capability vivía en pluginbase)
__all__ = [
    "Capability", "PluginContext", "PluginReport", "PluginProvider",
]


@dataclass
class PluginContext:
    """Contexto que el core entrega a cada colección en cada llamada."""
    config: InfraConfig
    manifest: Optional[Any] = None
    inventory: Any = None            # Inventory (del scanner) si disponible
    out_dir: str = "out"             # raíz de artefactos
    image: Optional[Any] = None      # Image model, en colecciones de imagen/build
    work_dir: Optional[Any] = None   # dir de trabajo propio de la colección
    extras: dict = field(default_factory=dict)


@dataclass
class PluginReport:
    """Resultado tipado que devuelve una colección."""
    ok: bool
    action: str = ""        # "none" | "uploaded" | "template-ready" | ...
    message: str = ""
    data: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class PluginProvider(ABC):
    """Base de todas las colecciones.

    Una colección declara sus `capabilities` y el core invoca SOLO los métodos
    de las capabilities que coinciden. El método `label` describe la colección
    para `infra collection list`.
    """

    name: str = ""
    version: str = "0.1.0"
    description: str = ""
    capabilities: frozenset[Capability] = frozenset()

    @property
    def label(self) -> str:
        caps = ",".join(c.value for c in sorted(self.capabilities,
                                                key=lambda c: c.value))
        desc = f" — {self.description}" if self.description else ""
        return f"{self.name} v{self.version} [{caps}]{desc}"

    # ── hooks opcionales (por defecto no hacen nada salvo `require`) ──

    def require(self, ctx: PluginContext) -> list[str]:
        """Retorna lista de errores si el entorno no está listo (binarios,
        credenciales, red...). Chequeado antes de usar la colección."""
        return []

    def validate(self, ctx: PluginContext, manifest: Manifest) -> list[str]:
        """Valida el manifiesto contra lo que esta colección puede hacer."""
        return []

    def plan(self, ctx: PluginContext) -> dict:
        """Calcula el trabajo planificado. Estructura libre (es info)."""
        return {}

    @abstractmethod
    def apply(self, ctx: PluginContext) -> PluginReport:
        """Ejecuta el trabajo. Cada colección implementa su logica real."""

    def destroy(self, ctx: PluginContext) -> PluginReport:
        """Destruye lo creado. Default: sin implementar (no-destructive)."""
        return PluginReport(ok=False, action="unsupported",
                            message=f"{self.name} no implementa destroy.")