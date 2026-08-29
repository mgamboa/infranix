"""Colección Ansible — capability: configure.

Genera inventario + roles a partir del manifiesto y (opcionalmente) ejecuta
los playbooks contra las VMs. Envuelve `infranix.ansible_gen.AnsibleGenerator`.
El error de Ansible queda confinado a esta colección.

Extras del PluginContext:
  - extras["apply"]: bool — si True, ejecuta `ansible-playbook` de verdad.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from infranix.pluginbase import (Capability, PluginContext, PluginProvider,
                                 PluginReport)
from infranix.ansible_gen import AnsibleGenerator


class Provider(PluginProvider):
    name = "ansible"
    version = "0.1.0"
    description = "Configuración de VMs vía Ansible (inventario + roles)"
    capabilities = frozenset({Capability.CONFIGURE})

    def require(self, ctx: PluginContext) -> list[str]:
        import shutil
        errors = []
        if shutil.which("ansible-playbook") is None:
            errors.append("Binario 'ansible-playbook' no encontrado en PATH.")
        return errors

    def validate(self, ctx: PluginContext, manifest) -> list[str]:
        return []

    def plan(self, ctx: PluginContext) -> dict:
        roles = sorted({r for s in ctx.manifest.servers for r in s.roles})
        return {"roles": roles,
                "servers": [s.name for s in ctx.manifest.servers]}

    def apply(self, ctx: PluginContext) -> PluginReport:
        errs = self.require(ctx)
        if errs:
            return PluginReport(ok=False, action="env-missing",
                                message="; ".join(errs), errors=errs)

        try:
            AnsibleGenerator(ctx.manifest, Path(ctx.out_dir)).generate()
        except Exception as e:
            return PluginReport(ok=False, action="generate-failed",
                                message=f"Generando Ansible: {e}",
                                errors=[str(e)])

        if not ctx.extras.get("apply"):
            return PluginReport(ok=True, action="generated",
                                message=f"Ansible inventario+roles en "
                                        f"{Path(ctx.out_dir)/'ansible'}")

        # Ejecutar playbooks
        base = Path(ctx.out_dir) / "ansible"
        inventory = base / "inventory" / "hosts.yml"
        if not inventory.exists():
            return PluginReport(ok=False, action="no-inventory",
                                message="Inventario Ansible no encontrado.")
        try:
            r = subprocess.run(
                ["ansible-playbook", "-i", str(inventory),
                 str(base / "playbooks" / "site.yml")],
                capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                return PluginReport(ok=False, action="run-failed",
                                    message=r.stderr[-800:])
        except Exception as e:
            return PluginReport(ok=False, action="run-failed", message=str(e))
        return PluginReport(ok=True, action="configured",
                            message="Playbooks Ansible ejecutados.")

    def destroy(self, ctx: PluginContext) -> PluginReport:
        return PluginReport(ok=False, action="unsupported",
                            message="Ansible no destruye (nada que quitar "
                                    "persistente; los recursos los borra Terraform).")


provider = Provider