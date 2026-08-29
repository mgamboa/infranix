"""Ansible collection — capability: configure.

Generates inventory + roles from the manifest and (optionally) runs the
playbooks against the VMs. Wraps `infranix.ansible_gen.AnsibleGenerator`.
Ansible errors stay confined to this collection.

PluginContext extras:
  - extras["apply"]: bool — if True, actually runs `ansible-playbook`.
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
    description = "VM configuration via Ansible (inventory + roles)"
    capabilities = frozenset({Capability.CONFIGURE})

    def require(self, ctx: PluginContext) -> list[str]:
        import shutil
        errors = []
        if shutil.which("ansible-playbook") is None:
            errors.append("'ansible-playbook' binary not found in PATH.")
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
                                message=f"Generating Ansible: {e}",
                                errors=[str(e)])

        if not ctx.extras.get("apply"):
            return PluginReport(ok=True, action="generated",
                                message=f"Ansible inventory+roles at "
                                        f"{Path(ctx.out_dir)/'ansible'}")

        # Run playbooks
        base = Path(ctx.out_dir) / "ansible"
        inventory = base / "inventory" / "hosts.yml"
        if not inventory.exists():
            return PluginReport(ok=False, action="no-inventory",
                                message="Ansible inventory not found.")

        # Install Ansible Galaxy collections required by the roles.
        # Always present: the file starts with the baseline collections
        # (community.general, ansible.posix) plus any role-derived ones.
        galaxy_req = base / "galaxy" / "requirements.yml"
        if galaxy_req.exists():
            # Offline fallback: collect local collection tarballs placed under
            # this collection's own `collections/` directory, so a tar.gz such
            # as redhat-satellite-5.11.0.tar.gz can be used without downloading.
            # Local tarballs are ADDITIONAL to requirements.yml (baselines and
            # role collections are always installed from the file too).
            local_collections = Path(__file__).resolve().parent / "collections"
            local_tarballs = sorted(local_collections.glob("*.tar.gz")) \
                if local_collections.is_dir() else []
            for tb in local_tarballs:
                try:
                    g = subprocess.run(
                        ["ansible-galaxy", "collection", "install", str(tb)],
                        capture_output=True, text=True, timeout=900)
                    if g.returncode != 0:
                        return PluginReport(
                            ok=False, action="galaxy-failed",
                            message=g.stderr[-800:] or
                                    f"ansible-galaxy install failed for {tb.name}.")
                except Exception as e:
                    return PluginReport(ok=False, action="galaxy-failed",
                                        message=str(e))
            try:
                g = subprocess.run(
                    ["ansible-galaxy", "collection", "install", "-r",
                     str(galaxy_req)],
                    capture_output=True, text=True, timeout=900)
                if g.returncode != 0:
                    return PluginReport(
                        ok=False, action="galaxy-failed",
                        message=g.stderr[-800:] or
                                "ansible-galaxy install failed.")
            except Exception as e:
                return PluginReport(ok=False, action="galaxy-failed",
                                    message=str(e))

        try:
            import os
            env = dict(os.environ)
            ansible_cfg = base / "ansible.cfg"
            if ansible_cfg.exists():
                env["ANSIBLE_CONFIG"] = str(ansible_cfg)
            r = subprocess.run(
                ["ansible-playbook", "-i", str(inventory),
                 str(base / "playbooks" / "site.yml")],
                capture_output=True, text=True, timeout=900, env=env)
            if r.returncode != 0:
                return PluginReport(ok=False, action="run-failed",
                                    message=r.stderr[-800:])
        except Exception as e:
            return PluginReport(ok=False, action="run-failed", message=str(e))
        return PluginReport(ok=True, action="configured",
                            message="Ansible playbooks executed.")

    def destroy(self, ctx: PluginContext) -> PluginReport:
        return PluginReport(ok=False, action="unsupported",
                            message="Ansible does not destroy (nothing "
                                    "persistent to remove; resources are "
                                    "removed by Terraform).")


provider = Provider