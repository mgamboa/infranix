"""Terraform collection — capability: provision.

Generates and (optionally) applies resources with Terraform (vmware/vsphere
provider). Wraps `infranix.terraform_gen.TerraformGenerator`. The core asks
this collection for the provision step; if Terraform fails, the error stays
confined.

PluginContext extras:
  - extras["apply"]: bool — if True, actually runs `terraform apply`.
  - extras["datastore"]: str — hypervisor datastore (if not, from the inventory).
  - extras["compute_cluster"]: str — cluster path (if not, derived).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from infranix.pluginbase import (Capability, PluginContext, PluginProvider,
                                 PluginReport)
from infranix.terraform_gen import TerraformGenerator


class Provider(PluginProvider):
    name = "terraform"
    version = "0.1.0"
    description = "VM provisioning via HashiCorp/Terraform (vmware/vsphere)"
    capabilities = frozenset({Capability.PROVISION})

    # ── protocol ──

    def require(self, ctx: PluginContext) -> list[str]:
        import shutil
        errors = []
        if shutil.which("terraform") is None:
            errors.append("'terraform' binary not found in PATH.")
        return errors

    def validate(self, ctx: PluginContext, manifest) -> list[str]:
        return []  # Terraform validates most things in plan/apply

    def plan(self, ctx: PluginContext) -> dict:
        servers = [s.name for s in ctx.manifest.servers
                   if s.action.value != "destroy"]
        return {"servers": servers,
                "destroy": [s.name for s in ctx.manifest.servers
                            if s.action.value == "destroy"]}

    def apply(self, ctx: PluginContext) -> PluginReport:
        errs = self.require(ctx)
        if errs:
            return PluginReport(ok=False, action="env-missing",
                                message="; ".join(errs), errors=errs)

        datastore = (ctx.extras.get("datastore") or
                     (ctx.inventory.datastores[0].name
                      if ctx.inventory and ctx.inventory.datastores else
                      "delldatastore"))
        cluster_host = (ctx.inventory.compute_cluster_host
                        if ctx.inventory and ctx.inventory.compute_cluster_host
                        else "esxi01.example.local")
        compute_cluster = (ctx.extras.get("compute_cluster") or
                           f"/ha-datacenter/host/{cluster_host}")

        tf_dir = Path(ctx.out_dir) / "terraform"
        try:
            TerraformGenerator(ctx.manifest, tf_dir,
                               datastore=datastore,
                               compute_cluster=compute_cluster).generate()
        except Exception as e:
            return PluginReport(ok=False, action="generate-failed",
                                message=f"Generating Terraform: {e}",
                                errors=[str(e)])

        if not ctx.extras.get("apply"):
            return PluginReport(ok=True, action="generated",
                                message=f"Terraform generated at {tf_dir}")

        # Run apply for real
        env = self._tf_env(ctx)
        try:
            self._init(tf_dir, env)
            self._apply(tf_dir, env)
        except Exception as e:
            return PluginReport(ok=False, action="apply-failed",
                                message=f"Terraform apply: {e}",
                                errors=[str(e)])
        return PluginReport(ok=True, action="applied",
                            message=f"Terraform apply OK at {tf_dir}")

    def destroy(self, ctx: PluginContext) -> PluginReport:
        errs = self.require(ctx)
        if errs:
            return PluginReport(ok=False, action="env-missing",
                                message="; ".join(errs), errors=errs)
        tf_dir = Path(ctx.out_dir) / "terraform"
        env = self._tf_env(ctx)
        try:
            self._init(tf_dir, env)
            r = subprocess.run(["terraform", "destroy", "-auto-approve"],
                               cwd=str(tf_dir), env=env, capture_output=True,
                               text=True, timeout=900)
            if r.returncode != 0:
                return PluginReport(ok=False, action="destroy-failed",
                                    message=r.stderr[-800:])
        except Exception as e:
            return PluginReport(ok=False, action="destroy-failed",
                                message=str(e))
        return PluginReport(ok=True, action="destroyed",
                            message="Terraform resources destroyed.")

    # ── helpers ──

    @staticmethod
    def _tf_env(ctx: PluginContext) -> dict:
        return {
            "TF_VAR_vsphere_user": ctx.config.user or "",
            "TF_VAR_vsphere_password":
                (ctx.config.password or "").replace("%29", ")"),
            "TF_VAR_vsphere_server": ctx.config.host or "",
            "TF_VAR_vsphere_insecure": "true",
        }

    @staticmethod
    def _init(tf_dir: Path, env: dict):
        r = subprocess.run(["terraform", "init", "-input=false", "-upgrade"],
                           cwd=str(tf_dir), env=env, capture_output=True,
                           text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-800:])

    @staticmethod
    def _apply(tf_dir: Path, env: dict):
        r = subprocess.run(
            ["terraform", "apply", "-auto-approve",
             f"-var=vsphere_user={env['TF_VAR_vsphere_user']}",
             f"-var=vsphere_password={env['TF_VAR_vsphere_password']}",
             f"-var=vsphere_server={env['TF_VAR_vsphere_server']}"],
            cwd=str(tf_dir), env=env, capture_output=True, text=True,
            timeout=900)
        if r.returncode != 0:
            raise RuntimeError(r.stderr[-1000:])


provider = Provider