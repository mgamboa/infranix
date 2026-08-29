"""Colección Packer — capability: build.

Wrappers del protocolo sobre la maquinaria de `infranix.packer_builder`.
El registry instancia `provider` (la clase PackerProvider) y el core llama
`provider.apply(ctx)` para construir un template desde ISO.
"""

from __future__ import annotations

from infranix.pluginbase import (Capability, PluginContext, PluginProvider,
                                 PluginReport)
from infranix.packer_builder import PackerBuilder


class Provider(PluginProvider):
    name = "packer"
    version = "0.1.0"
    description = "Construye templates clonables desde ISO (kickstart/preseed)"
    capabilities = frozenset({Capability.BUILD})

    # ── protocolo ──

    def require(self, ctx: PluginContext) -> list[str]:
        import shutil
        errors = []
        if shutil.which("packer") is None:
            errors.append("Binario 'packer' no encontrado en PATH.")
        return errors

    def validate(self, ctx: PluginContext, manifest) -> list[str]:
        errors = []
        for img in manifest.images:
            if img.build.builder.value != "packer":
                continue
            if not img.version:
                errors.append(f"Imagen '{img.name}' falta `version` (Packer).")
        return errors

    def plan(self, ctx: PluginContext) -> dict:
        work = []
        for img in ctx.manifest.images:
            if img.build.builder.value == "packer":
                work.append({"image": img.name, "distro": img.distro,
                             "version": img.version, "action": "build"})
        return {"build": work}

    def apply(self, ctx: PluginContext) -> PluginReport:
        errs = self.require(ctx)
        if errs:
            return PluginReport(ok=False, action="env-missing",
                                message="; ".join(errs), errors=errs)
        img = ctx.image
        if img is None:
            return PluginReport(ok=False, action="no-image",
                                message="ctx.image requerido para build.")
        if img.build.builder.value != "packer":
            return PluginReport(ok=False, action="skip",
                                message=f"{img.name}: builder no es packer.")

        iso = getattr(ctx, "iso_path", None) or ctx.extras.get("iso_path")
        if not iso:
            return PluginReport(ok=False, action="no-iso",
                                message=f"ISO de '{img.name}' no localizado.")

        builder = PackerBuilder(ctx.config, img, iso_path=iso)
        work = ctx.work_dir or ctx.extras.get("work_dir")
        from pathlib import Path
        work = Path(work) if work else None
        if work is None:
            return PluginReport(ok=False, action="no-workdir",
                                message="ctx.work_dir requerido.")

        try:
            builder.generate(work)
            ok = builder.build(work)
        except Exception as e:
            return PluginReport(ok=False, action="error",
                                message=f"Packer: {e}", errors=[str(e)])
        if not ok:
            return PluginReport(ok=False, action="build-failed",
                                message=f"Packer fallo al construir '{img.name}'.")
        return PluginReport(ok=True, action="template-ready",
                            message=f"Template '{img.name}' listo para clonar.",
                            data={"template": img.name})


provider = Provider