"""InfraNix — Aplicación orquestadora de alto nivel.

Expone una única API que "corre el archivo YAML declarativo": carga el
manifiesto, lo valida, escanea el estado actual, calcula el plan de cambios,
aplica el Safety Gate, asegura imágenes, genera Terraform + Ansible y (en modo
aplicativo) los ejecuta. El resultado se devuelve como un reporte estructurado.

Este es el cerebro que automatiza el trabajo a partir de la declaración.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from infranix.config import InfraConfig, load_config, resolve_vars
from infranix.adapters.discovery import Inventory, make_scanner
from infranix.core.planner import Planner, Plan, ChangeKind
from infranix.core.safety import SafetyGate, SafetyReport
from infranix.terraform_gen import TerraformGenerator
from infranix.ansible_gen import AnsibleGenerator
from infranix.image_manager import ImageManager
from infranix.models import Manifest


@dataclass
class RunReport:
    """Reporte estructurado de una ejecución de la aplicación."""
    project: str = ""
    hypervisor: str = ""
    plan_summary: str = ""
    safety_approved: bool = False
    safety_summary: str = ""
    images_ensured: list[str] = field(default_factory=list)
    artifacts_generated: bool = False
    terraform_executed: bool = False
    terraform_log: str = ""
    errors: list[str] = field(default_factory=list)

    def to_markdown(self) -> str:
        lines = [f"# Reporte InfraNix — {self.project}",
                 f"**Hypervisor:** {self.hypervisor}",
                 f"**Plan:** {self.plan_summary}",
                 f"**Safety:** {'APROBADO' if self.safety_approved else 'BLOQUEADO'}",
                 f"  {self.safety_summary}"]
        if self.images_ensured:
            lines.append("**Imágenes:**")
            lines += [f"  - {i}" for i in self.images_ensured]
        lines.append(f"**Artefactos generados:** {'sí' if self.artifacts_generated else 'no'}")
        lines.append(f"**Terraform ejecutado:** {'sí' if self.terraform_executed else 'no'}")
        if self.terraform_log:
            lines.append("**Log Terraform (últimas líneas):**")
            lines.append("```")
            lines.append(self.terraform_log.strip())
            lines.append("```")
        if self.errors:
            lines.append("**Errores:**")
            lines += [f"  - {e}" for e in self.errors]
        return "\n".join(lines)


class InfraNix:
    """Orquestador declarativo: corre un manifiesto YAML de principio a fin."""

    def __init__(self, config: Optional[InfraConfig] = None):
        self.config = config or load_config()

    # ── Utilidades ──
    @staticmethod
    def load_manifest(path) -> Manifest:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        data = resolve_vars(data)
        return Manifest(**data)

    def _scan(self) -> Inventory:
        return make_scanner(self.config).scan()

    # ── Ejecución principal ──
    def run(self, manifest_path: str, out_dir: str = "out",
            apply: bool = False, yes: bool = False) -> RunReport:
        """Corre el archivo declarativo. Devuelve un RunReport.

        apply=False → solo planifica (dry-run, sin tocar nada).
        apply=True  → genera artefactos y, si está aprobado, ejecuta
                      Terraform/Ansible.
        """
        report = RunReport()
        try:
            manifest = self.load_manifest(manifest_path)
        except Exception as e:
            report.errors.append(f"Manifiesto inválido: {e}")
            return report

        report.project = manifest.project
        report.hypervisor = manifest.hypervisor.value

        # 1) Escanear estado actual
        try:
            inventory = self._scan()
        except Exception as e:
            report.errors.append(f"Error escaneando: {e}")
            return report

        # 2) Planear diff
        plan: Plan = Planner(manifest, inventory).plan()
        report.plan_summary = plan.summary()

        # 3) Safety Gate
        gate: SafetyReport = SafetyGate(manifest.safety).evaluate(manifest, plan)
        report.safety_approved = gate.allowed
        report.safety_summary = gate.summary()

        if not gate.allowed:
            report.errors.append("Plan bloqueado por el Safety Gate.")
            report.errors.append(report.safety_summary)
            return report

        # 4) Asegurar imágenes (solo en modo aplicativo; en dry-run se reporta)
        if manifest.images:
            if apply:
                im = ImageManager(self.config)
                for img in manifest.images:
                    result = im.ensure(img.name, img.distro, img.version,
                                       available_remotes=inventory.images)
                    report.images_ensured.append(
                        f"{img.name}: {result.message}")
            else:
                report.images_ensured = [
                    f"{img.name}: (dry-run) se asegurará durante el apply"
                    for img in manifest.images]

        # 5) Generar artefactos (siempre que el plan esté aprobado, para revisión)
        try:
            datastore = (inventory.datastores[0].name
                         if inventory.datastores else "delldatastore")
            cluster_host = (inventory.compute_cluster_host
                            or "esxi01.example.local")
            tf_dir = Path(out_dir) / "terraform"
            TerraformGenerator(
                manifest, tf_dir, datastore=datastore,
                compute_cluster="/ha-datacenter/host/" + cluster_host,
            ).generate()
            AnsibleGenerator(manifest, Path(out_dir)).generate()
            report.artifacts_generated = True
        except Exception as e:
            report.errors.append(f"Error generando artefactos: {e}")

        # Si no se aplica, terminamos (artefactos ya generados, nada ejecutado)
        if not apply:
            return report

        # 6) Ejecutar Terraform + (si `apply`)
        destructive = [c for c in plan.changes if c.kind == ChangeKind.DESTROY]
        if destructive and not yes:
            report.errors.append(
                "Operaciones destructivas requieren --yes y safety.destroy.")
            return report

        try:
            log = self._terraform_apply(tf_dir)
            report.terraform_executed = True
            report.terraform_log = log
        except Exception as e:
            report.errors.append(f"Terraform: {e}")
        return report

    # ── Ejecución de Terraform (helper) ──
    def _terraform_apply(self, tf_dir: Path) -> str:
        env = dict(self._tf_env())
        logs = []
        cmd_init = ["terraform", "init", "-input=false", "-upgrade"]
        r_init = subprocess.run(cmd_init, cwd=str(tf_dir), env=env,
                                capture_output=True, text=True, timeout=600)
        logs.append(r_init.stdout[-2000:])
        if r_init.returncode != 0:
            raise RuntimeError(r_init.stderr[-800:])

        cmd_apply = (["terraform", "apply", "-auto-approve",
                      f"-var=vsphere_user={env['TF_VAR_vsphere_user']}",
                      f"-var=vsphere_password={env['TF_VAR_vsphere_password']}",
                      f"-var=vsphere_server={env['TF_VAR_vsphere_server']}"])
        r_apply = subprocess.run(cmd_apply, cwd=str(tf_dir), env=env,
                                 capture_output=True, text=True, timeout=900)
        logs.append(r_apply.stdout[-3000:])
        if r_apply.returncode != 0:
            raise RuntimeError(r_apply.stderr[-1000:])
        return "\n".join(logs)

    def _tf_env(self) -> dict:
        return {
            "TF_VAR_vsphere_user": self.config.user or "",
            "TF_VAR_vsphere_password": (self.config.password or "").replace("%29", ")"),
            "TF_VAR_vsphere_server": self.config.host or "",
            "TF_VAR_vsphere_insecure": "true",
        }
