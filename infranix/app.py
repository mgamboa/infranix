"""InfraNix — Orquestador de alto nivel (core delgado).

Este módulo es EL CORE: casi no contiene lógica de provisión, solo orquesta.
Cada capacidad (scan, provision, configure, image, build) la resuelve en una
colección vía el registry. Si una colección falla, el error queda confinado
ahí — el core sigue operativo y reporta qué colección falló.

Funciones del core únicamente:
  - cargar/validar el manifiesto
  - planear el diff (Planner)
  - aplicar el Safety Gate
  - resolver y llamar colecciones por capability
  - ensamblar el RunReport
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
from infranix.models import Manifest


@dataclass
class RunReport:
    """Reporte estructurado de una ejecución de la aplicación."""
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

    def to_markdown(self) -> str:
        lines = [f"# Reporte InfraNix — {self.project}",
                 f"**Hypervisor:** {self.hypervisor}",
                 f"**Plan:** {self.plan_summary}",
                 f"**Safety:** {'APROBADO' if self.safety_approved else 'BLOQUEADO'}",
                 f"  {self.safety_summary}"]
        if self.scans:
            lines.append("**Discovery:**")
            lines += [f"  - {s}" for s in self.scans]
        if self.images_ensured:
            lines.append("**Imágenes:**")
            lines += [f"  - {i}" for i in self.images_ensured]
        lines.append(f"**Artefactos generados:** {'sí' if self.artifacts_generated else 'no'}")
        if self.provision_log:
            lines.append("**Provisión (Terraform):**")
            lines.append(f"  {self.provision_log}")
        if self.configure_log:
            lines.append("**Configuración (Ansible):**")
            lines.append(f"  {self.configure_log}")
        if self.errors:
            lines.append("**Errores:**")
            lines += [f"  - {e}" for e in self.errors]
        return "\n".join(lines)


class InfraNix:
    """Orquestador declarativo: corre un manifiesto YAML de principio a fin."""

    def __init__(self, config: Optional[InfraConfig] = None):
        self.config = config or load_config()
        self.registry = get_registry()

    # ── Utilería ──
    @staticmethod
    def load_manifest(path) -> Manifest:
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        data = resolve_vars(data)
        return Manifest(**data)

    def _cap(self, cap: Capability, what: str):
        p = self.registry.resolve(cap)
        if p is None:
            raise RuntimeError(
                f"Ninguna colección con capability {cap.value} habilitada "
                f"para {what}. Ver 'infra collection list'.")
        return p

    # ── Pasos (cada uno delega en una colección) ──

    def _do_scan(self, _manifest) -> tuple[list, Optional[object]]:
        """Escanea via colección SCAN. Devuelve (scans, inventory|None)."""
        provider = self._cap(Capability.SCAN, "scan")
        ctx = PluginContext(config=self.config)
        report = provider.apply(ctx)
        messages = [report.message]
        inventory = report.data.get("inventory") if report.ok else None
        if not report.ok:
            messages = report.errors or [report.message]
        return messages, inventory

    def _do_images(self, manifest, inventory, out_dir, apply) -> list[str]:
        provider = self._cap(Capability.IMAGE, "imágenes")
        ctx = PluginContext(config=self.config, manifest=manifest,
                            inventory=inventory, out_dir=out_dir)
        if not apply:
            return [f"{p['image']}: (dry-run) se asegurará al aplicar"
                    for p in provider.plan(ctx).get("ensure", [])
                    if p["status"] == "needed"] or \
                   ["(dry-run) imágenes requeridas se asegurarán en apply"]
        report = provider.apply(ctx)
        return report.message.splitlines()

    def _do_provision(self, manifest, inventory, out_dir, apply) -> tuple[bool, str]:
        provider = self._cap(Capability.PROVISION, "provisión (Terraform)")
        ctx = PluginContext(config=self.config, manifest=manifest,
                            inventory=inventory, out_dir=out_dir,
                            extras={"apply": apply})
        report = provider.apply(ctx)
        return report.ok, report.message

    def _do_configure(self, manifest, inventory, out_dir, apply) -> tuple[bool, str]:
        provider = self._cap(Capability.CONFIGURE, "configuración (Ansible)")
        ctx = PluginContext(config=self.config, manifest=manifest,
                            inventory=inventory, out_dir=out_dir,
                            extras={"apply": apply})
        report = provider.apply(ctx)
        return report.ok, report.message

    # ── Ejecución principal ──
    def run(self, manifest_path: str, out_dir: str = "out",
            apply: bool = False) -> RunReport:
        """Corre el archivo declarativo. Devuelve un RunReport.

        apply=False → solo planifica (dry-run, sin tocar nada).
        apply=True  → ejecuta las colecciones provisión/configuración.
        """
        report = RunReport()
        try:
            manifest = self.load_manifest(manifest_path)
        except Exception as e:
            report.errors.append(f"Manifiesto inválido: {e}")
            return report

        report.project = manifest.project
        report.hypervisor = manifest.hypervisor.value

        # 1) Scan (colección SCAN)
        try:
            scans, inventory = self._do_scan(manifest)
            report.scans = scans
        except Exception as e:
            report.errors.append(str(e))
            return report

        # 2) Planear diff
        if inventory is None:
            report.errors.append("Sin inventario: no se puede planear.")
            return report
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

        # 4) Imágenes (solo si las pide el manifiesto)
        if manifest.images:
            report.images_ensured = self._do_images(
                manifest, inventory, out_dir, apply)

        # 5) Provición (Terraform) — genera siempre; aplica si `apply`
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

        # 6) Configuración (Ansible) — solo en modo aplicativo
        if apply:
            try:
                ok, msg = self._do_configure(manifest, inventory, out_dir, True)
                report.configure_log = msg
                if not ok:
                    report.errors.append(msg)
            except Exception as e:
                report.errors.append(str(e))

        return report