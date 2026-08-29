"""Colección Discovery (VMware/govc) — capability: scan.

Escanea el estado actual del hypervisor (VMs, datastores, redes, imágenes).
Envuelve `infranix.adapters.discovery.make_scanner` (ESXiScanner o MockScanner).
El core no selecciona el scanner: esta colección lo hace y devuelve el
Inventory via PluginReport.data["inventory"].
"""

from __future__ import annotations

from infranix.pluginbase import (Capability, PluginContext, PluginProvider,
                                 PluginReport)
from infranix.adapters.discovery import make_scanner


class Provider(PluginProvider):
    name = "vmware"
    version = "0.1.0"
    description = "Escaneo/discovery del hypervisor VMware vía govc (o mock)"
    capabilities = frozenset({Capability.SCAN})

    def require(self, ctx: PluginContext) -> list[str]:
        name = self._scanner_name(ctx)
        if name == "esxi":
            import shutil
            if shutil.which("govc") is None:
                return ["Binario 'govc' no encontrado en PATH."]
        return []

    @staticmethod
    def _scanner_name(ctx: PluginContext) -> str:
        hv = (ctx.config.hypervisor or "").lower()
        return "mock" if hv in ("mock", "local", "") and hv != "esxi" else "esxi"

    def validate(self, ctx: PluginContext, manifest) -> list[str]:
        return []

    def plan(self, ctx: PluginContext) -> dict:
        return {"scan": True}

    def apply(self, ctx: PluginContext) -> PluginReport:
        errs = self.require(ctx)
        if errs:
            return PluginReport(ok=False, action="env-missing",
                                message="; ".join(errs), errors=errs)
        try:
            scanner = make_scanner(ctx.config)
            inventory = scanner.scan()
        except Exception as e:
            return PluginReport(ok=False, action="scan-failed",
                                message=f"Escaneando: {e}", errors=[str(e)])
        return PluginReport(
            ok=True, action="scanned",
            message=(f"{len(inventory.vms)} VMs, {len(inventory.datastores)} "
                     f"datastores, {len(inventory.images)} imágenes en "
                     f"{inventory.host or ctx.config.host}"),
            data={"inventory": inventory})

    def destroy(self, ctx: PluginContext) -> PluginReport:
        return PluginReport(ok=False, action="unsupported",
                            message="Discovery no destruye (solo lectura).")


provider = Provider