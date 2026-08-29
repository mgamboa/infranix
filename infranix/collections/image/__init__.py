"""Image collection — capability: image.

Manages the availability of images (ISO/OVA/cloud-image) on the hypervisor:
downloads from the official mirror if missing, uploads to the datastore and
registers it in the local catalog. Wraps `infranix.image_manager.ImageManager`
and exposes it through the collection protocol (PluginProvider).

The core never imports ImageManager directly: it delegates here.
"""

from __future__ import annotations

from infranix.pluginbase import (Capability, PluginContext, PluginProvider,
                                 PluginReport)
from infranix.image_manager import ImageManager


class Provider(PluginProvider):
    name = "image"
    version = "0.1.0"
    description = "Download/upload images (ISO) to the datastore and registers the catalog"
    capabilities = frozenset({Capability.IMAGE})

    # ── protocol ──

    def require(self, ctx: PluginContext) -> list[str]:
        import shutil
        errors = []
        if shutil.which("govc") is None:
            errors.append("'govc' binary not found in PATH.")
        if not ctx.config.host:
            errors.append("INFRA_HOST is empty: set the hypervisor IP.")
        return errors

    def validate(self, ctx: PluginContext, manifest) -> list[str]:
        errors = []
        seen: set[str] = set()
        for img in manifest.images:
            if img.name in seen:
                errors.append(f"Duplicate image: '{img.name}'")
                continue
            seen.add(img.name)
            if not img.version:
                errors.append(f"Image '{img.name}' has no version.")
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
        """Ensure all manifest images (download+upload if missing)."""
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
            if res.action != "available" and res.action != "none" and not res.message.startswith("already available"):
                if res.action not in ("none",):
                    ok_all = False
        return PluginReport(ok=ok_all, action="ensured",
                            message="\n".join(reports))


provider = Provider