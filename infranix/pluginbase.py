"""Plugin API — the contract for InfraNix collections.

The core is thin: it knows the manifest schema, the planner, the safety gate
and the orchestrator. All additional functionality (Terraform provisioning,
Ansible configuration, scanning, images, Packer builders...) is delivered as
*collections* that implement this protocol.

A collection is a Python package that declares an entry point:

    [project.entry-points."infranix.collections"]
    packer = "infra_collection_packer:provider"

And exposes a `Provider(PluginProvider)` class with `name` and `capabilities`.

If a collection fails (e.g. Packer), the failure stays confined inside it: the
core never imports a collection's internals, it only calls the protocol.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from infranix.config import InfraConfig
from infranix.models import Capability, Manifest


# re-export for compatibility (Capability used to live in pluginbase)
__all__ = [
    "Capability", "PluginContext", "PluginReport", "PluginProvider",
]


@dataclass
class PluginContext:
    """Context that the core hands to each collection on every call."""
    config: InfraConfig
    manifest: Optional[Any] = None
    inventory: Any = None            # Inventory (from the scanner) if available
    out_dir: str = "out"             # artifacts root
    image: Optional[Any] = None      # Image model, for image/build collections
    work_dir: Optional[Any] = None   # collection's own working directory
    extras: dict = field(default_factory=dict)


@dataclass
class PluginReport:
    """Typed result returned by a collection."""
    ok: bool
    action: str = ""        # "none" | "uploaded" | "template-ready" | ...
    message: str = ""
    data: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class PluginProvider(ABC):
    """Base for all collections.

    A collection declares its `capabilities` and the core invokes ONLY the
    methods of the capabilities that match. The `label` method describes the
    collection for `infra collection list`.
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

    # ── optional hooks (by default they do nothing except `require`) ──

    def require(self, ctx: PluginContext) -> list[str]:
        """Return a list of errors if the environment is not ready (binaries,
        credentials, network...). Checked before using the collection."""
        return []

    def validate(self, ctx: PluginContext, manifest: Manifest) -> list[str]:
        """Validate the manifest against what this collection can do."""
        return []

    def plan(self, ctx: PluginContext) -> dict:
        """Compute the planned work. Free structure (it is informational)."""
        return {}

    @abstractmethod
    def apply(self, ctx: PluginContext) -> PluginReport:
        """Execute the work. Each collection implements its real logic."""

    def destroy(self, ctx: PluginContext) -> PluginReport:
        """Destroy what was created. Default: not implemented (non-destructive)."""
        return PluginReport(ok=False, action="unsupported",
                            message=f"{self.name} does not implement destroy.")