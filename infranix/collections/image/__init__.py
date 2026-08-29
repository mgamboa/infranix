"""Colección Image — capability: image.

Gestiona la disponibilidad de imágenes (ISO/OVA/cloud-image) en el hypervisor:
descarga desde mirror oficial si falta, sube al datastore y registra en el
catálogo local. Envuelve `infranix.image_manager.ImageManager` y lo expone a
través del protocolo de colecciones (PluginProvider).

El core nunca importa ImageManager directamente: delega aquí.
"""

from __future__ import annotations

from infranix.pluginbase import (Capability, PluginContext, PluginProvider,
                                 PluginReport)
from infranix.image_manager import ImageManager


class Provider(PluginProvider):
    name = "image"
    version = "0.1.0"
    description = "Descarga/subir imágenes (ISO) al datastore y registra catálogo"
    capabilities = frozenset({Capability.IMAGE})

    # ── protocolo ──

    def require(self, ctx: PluginContext) -> list[str]:
        import shutil
        errors = []
        if shutil.which("govc") is None:
            errors.append("Binario 'govc' no encontrado en PATH.")
        if not ctx.config.host:
            errors.append("INFRA_HOST vacío: define la IP del hypervisor.")
        return errors

    def validate(self, ctx: PluginContext, manifest) -> list[str]:
        errors = []
        seen: set[str] = set()
        for img in manifest.images:
            if img.name in seen:
                errors.append(f"Imagen duplicada: '{img.name}'")
                continue
            seen.add(img.name)
            if not img.version:
                errors.append(f"Imagen '{img.name}' sin versión.")
        return errors

    def plan(self, ctx: PluginContext) -> dict:
        im = ImageManager(ctx.config)
        work = []
        for img in ctx.manifest.images:
            remote = [x for x in (ctx.inventory.images if ctx.inventory else [])]
            match = im._match_remote(remote, img.distro, img.version)
            work.append({"image": img.name, "status": "present" if match else "needed"})
        return {"ensure": work}

    def apply(self, ctx: PluginContext) -> PluginReport:
        """Asegura todas las imágenes del manifiesto (descarga+sube si falta)."""
        errs = self.require(ctx)
        if errs:
            return PluginReport(ok=False, action="env-missing",
                                message="; ".join(errs), errors=errs)

        im = ImageManager(ctx.config)
        reports: list[str] = []
        ok_all = True
        for img in ctx.manifest.images:
            available = []
            if ctx.inventory is not None:
                available = ctx.inventory.images
            res = im.ensure(img.name, img.distro, img.version,
                            available_remotes=available)
            reports.append(f"{img.name}: {res.action} — {res.message}")
            if res.action != "available" and res.action != "none" and not res.message.startswith("ya disponible"):
                if res.action not in ("none",):
                    ok_all = False
        return PluginReport(ok=ok_all, action="ensured",
                            message="\n".join(reports))


provider = Provider