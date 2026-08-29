"""InfraNix — high-level orchestrator (thin core).

This module IS THE CORE: it contains almost no provisioning logic, only
orchestration. Each capability (scan, provision, configure, image, build) is
resolved to a collection via the registry. If a collection fails, the error
stays confined there — the core keeps running and reports which collection failed.

The core only does:
  - load/validate the manifest
  - plan the diff (Planner)
  - apply the Safety Gate
  - resolve and call collections by capability
  - assemble the RunReport
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import yaml

from infranix.config import InfraConfig, load_config, resolve_vars
from infranix.core.planner import Planner, Plan, ChangeKind
from infranix.core.safety import SafetyGate, SafetyReport
from infranix.core.registry import get_registry
from infranix.pluginbase import Capability, PluginContext
from infranix.models import CollectionRequirement, Manifest


@dataclass
class RunReport:
    """Structured report of an application run."""
    project: str = ""
    hypervisor: str = ""
    plan_summary: str = ""
    safety_approved: bool = False
    safety_summary: str = ""
    scans: list[str] = field(default_factory=list)
    images_ensured: list[str] = field(default_factory=list)
    artifacts_generated: bool = False
    provision_log: str = ""
    configure_log: str = ""
    errors: list[str] = field(default_factory=list)
    tool_messages: list[str] = field(default_factory=list)
    template_messages: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [f"# InfraNix Report — {self.project}",
                 f"**Hypervisor:** {self.hypervisor}",
                 f"**Plan:** {self.plan_summary}",
                 f"**Safety:** {'APPROVED' if self.safety_approved else 'BLOCKED'}",
                 f"  {self.safety_summary}"]
        if self.scans:
            lines.append("**Discovery:**")
            lines += [f"  - {s}" for s in self.scans]
        if self.images_ensured:
            lines.append("**Images:**")
            lines += [f"  - {i}" for i in self.images_ensured]
        lines.append(f"**Artifacts generated:** {'yes' if self.artifacts_generated else 'no'}")
        if self.provision_log:
            lines.append("**Provision (Terraform):**")
            lines.append(f"  {self.provision_log}")
        if self.configure_log:
            lines.append("**Configuration (Ansible):**")
            lines.append(f"  {self.configure_log}")
        if self.errors:
            lines.append("**Errors:**")
            lines += [f"  - {e}" for e in self.errors]
        return "\n".join(lines)


class InfraNix:
    """Declarative orchestrator: runs a YAML manifest end to end."""

    def __init__(self, config: Optional[InfraConfig] = None):
        self.config = config or load_config()
        self.registry = get_registry()

    # ── Utilities ──
    @staticmethod
    def load_manifest(path, env: Optional[dict] = None) -> Manifest:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        data = resolve_vars(data, env=env) if env else resolve_vars(data)
        return Manifest(**data)

    def _cap(self, cap: Capability, what: str,
             prefer: Optional[list] = None):
        p = self.registry.resolve(cap, prefer=prefer)
        if p is None:
            raise RuntimeError(
                f"No collection with capability {cap.value} enabled "
                f"for {what}. See 'infra collection list'.")
        return p

    # ── Steps (each one delegates to a collection) ──

    @staticmethod
    def _prefer_names(manifest) -> Optional[list]:
        """Collection names the manifest explicitly asks for."""
        return [c.name for c in (manifest.collections or [])] or None

    def _do_scan(self, _manifest) -> tuple[list, Optional[object]]:
        """Scan via a SCAN collection. Returns (scans, inventory|None)."""
        provider = self._cap(Capability.SCAN, "scan",
                             prefer=self._prefer_names(_manifest))
        ctx = PluginContext(config=self.config)
        report = provider.apply(ctx)
        messages = [report.message]
        inventory = report.data.get("inventory") if report.ok else None
        if not report.ok:
            messages = report.errors or [report.message]
        return messages, inventory

    def _do_images(self, manifest, inventory, out_dir, apply) -> list[str]:
        provider = self._cap(Capability.IMAGE, "images",
                             prefer=self._prefer_names(manifest))
        ctx = PluginContext(config=self.config, manifest=manifest,
                            inventory=inventory, out_dir=out_dir)
        if not apply:
            return [f"{p['image']}: (dry-run) will be ensured on apply"
                    for p in provider.plan(ctx).get("ensure", [])
                    if p["status"] == "needed"] or \
                   ["(dry-run) required images will be ensured on apply"]
        report = provider.apply(ctx)
        return report.message.splitlines()

    def _do_build_templates(self, manifest, inventory, out_dir) -> list[str]:
        """Build cloneable templates from ISOs using Packer."""
        from infranix.image_manager import ImageManager
        import subprocess, os
        messages: list[str] = []
        im = ImageManager(self.config)
        datastore_isos = inventory.images if inventory else []

        # Get datastore name from inventory
        ds_name = None
        if inventory and hasattr(inventory, 'datastores') and inventory.datastores:
            ds_name = inventory.datastores[0].name

        # Get root_password from manifest servers' vars (already decrypted)
        root_pw = None
        for s in manifest.servers:
            if s.vars and s.vars.get("root_password"):
                root_pw = s.vars["root_password"]
                break

        # Check if templates already exist on ESXi
        env = dict(os.environ)
        env["GOVC_URL"] = f"https://{self.config.user}:{self.config.password}@{self.config.host}/sdk"
        env["GOVC_INSECURE"] = "true"

        for img in manifest.images:
            # Check if VM/template with this name already exists
            res = subprocess.run(
                ["govc", "vm.info", img.name],
                capture_output=True, text=True, env=env, timeout=30)
            if res.returncode == 0 and img.name in res.stdout:
                messages.append(f"{img.name}: already exists on hypervisor -- skipping Packer build")
                continue

            matched_iso = im._match_remote(datastore_isos, img.distro, img.version)
            result = im.build_template(
                img.name, img.distro, img.version,
                datastore_iso=matched_iso,
                datastore_name=ds_name,
                root_password=root_pw)
            messages.append(f"{img.name}: {result.action} -- {result.message}")

        return messages

    def _do_provision(self, manifest, inventory, out_dir, apply) -> tuple[bool, str]:
        provider = self._cap(Capability.PROVISION, "provision (Terraform)",
                             prefer=self._prefer_names(manifest))
        ctx = PluginContext(config=self.config, manifest=manifest,
                            inventory=inventory, out_dir=out_dir,
                            extras={"apply": apply})
        report = provider.apply(ctx)
        return report.ok, report.message

    def _do_configure(self, manifest, inventory, out_dir, apply) -> tuple[bool, str]:
        provider = self._cap(Capability.CONFIGURE, "configuration (Ansible)",
                             prefer=self._prefer_names(manifest))
        ctx = PluginContext(config=self.config, manifest=manifest,
                            inventory=inventory, out_dir=out_dir,
                            extras={"apply": apply})
        report = provider.apply(ctx)
        return report.ok, report.message

    # ── Main execution ──
    def run(self, manifest_path: str, out_dir: str = "out",
            apply: bool = False,
            env: Optional[dict] = None,
            extra_collections: Optional[list] = None) -> RunReport:
        """Run the declarative file. Returns a RunReport.

        apply=False → only plans (dry-run, touches nothing).
        apply=True  → runs the provision/config collections.
        env → optional var environment for ${VAR} resolution (e.g. role defaults).
        extra_collections → optional CollectionRequirement dicts added for
            ensure_required (e.g. from a role's collections/requirements.yml).
        """
        report = RunReport()
        try:
            manifest = self.load_manifest(manifest_path, env=env)
        except Exception as e:
            report.errors.append(f"Invalid manifest: {e}")
            return report

        report.project = manifest.project
        report.hypervisor = manifest.hypervisor.value

        # 0) Ensure required CLI tools are installed (auto-install missing ones)
        from infranix.tools import ensure_tools
        _COLLECTION_TOOLS = {
            "vmware": ["govc"],
            "terraform": ["terraform"],
            "ansible": ["ansible-playbook"],
            "packer": ["packer"],
            "image": ["govc"],
        }
        needed_tools: set[str] = set()
        for coll in manifest.collections:
            needed_tools.update(_COLLECTION_TOOLS.get(coll.name, []))
        if needed_tools:
            try:
                installed = ensure_tools(sorted(needed_tools))
                for name, path in installed.items():
                    report.tool_messages.append(f"  ✓ {name}: {path}")
            except RuntimeError as e:
                report.errors.append(f"Tool installation failed: {e}")
                return report

        # 1) Ensure required collections (behaves like ansible-galaxy regarding
        #    requirements: installs what is missing before running)
        requirements = list(manifest.collections)
        if extra_collections:
            requirements = requirements + [
                c if isinstance(c, CollectionRequirement)
                else CollectionRequirement(**c)
                for c in extra_collections
            ]
        if requirements:
            try:
                req_msgs = self.registry.ensure_required(requirements)
                for m in req_msgs:
                    if m.startswith("ERROR"):
                        report.errors.append(m)
            except Exception as e:
                report.errors.append(f"Resolving collections: {e}")
                return report

        # 1) Scan (SCAN collection)
        try:
            scans, inventory = self._do_scan(manifest)
            report.scans = scans
        except Exception as e:
            report.errors.append(str(e))
            return report

        # 2) Plan the diff
        if inventory is None:
            report.errors.append("No inventory: cannot plan.")
            return report
        plan: Plan = Planner(manifest, inventory).plan()
        report.plan_summary = plan.summary()

        # 3) Safety Gate
        gate: SafetyReport = SafetyGate(manifest.safety).evaluate(manifest, plan)
        report.safety_approved = gate.allowed
        report.safety_summary = gate.summary()
        if not gate.allowed:
            report.errors.append("Plan blocked by the Safety Gate.")
            report.errors.append(report.safety_summary)
            return report

        # 4) Images (only if the manifest asks for them)
        if manifest.images:
            report.images_ensured = self._do_images(
                manifest, inventory, out_dir, apply)

        # 4b) Build templates from ISOs (Packer) — ensures a cloneable
        #     template exists for each image before Terraform runs.
        if manifest.images and apply:
            report.template_messages = self._do_build_templates(
                manifest, inventory, out_dir)

        # 5) Provision (Terraform) — always generates; applies if `apply`
        try:
            should_run = plan.changes and any(
                c.kind in (ChangeKind.CREATE, ChangeKind.UPDATE)
                for c in plan.changes)
            ok, msg = self._do_provision(manifest, inventory, out_dir,
                                         apply and should_run)
            report.provision_log = msg
            if ok:
                report.artifacts_generated = True
            else:
                report.errors.append(msg)
        except Exception as e:
            report.errors.append(str(e))

        # 6) Configuration (Ansible) — always generates; runs only in apply mode
        try:
            ok, msg = self._do_configure(manifest, inventory, out_dir, apply)
            report.configure_log = msg
            if not ok:
                report.errors.append(msg)
        except Exception as e:
            report.errors.append(str(e))

        return report