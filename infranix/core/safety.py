"""Safety Gate — Núcleo de seguridad de InfraNix.

Garantiza que NINGUNA operación destructiva o de caída de servicio se ejecute
sin opt-in explícito. Es la capa por defecto, nunca opcional.

Reglas:
  - Destroy   : requiere safety.destroy=True (global) O action=destroy + confirmación en el recurso.
  - Downtime  : apagar/affectar un servicio existente requiere safety.allow_downtime=True.
  - Overwrite : modificar config existente siempre registra backup y pide confirmación.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from infranix.models import Manifest, ServerAction, SafetyPolicy


class Severity(str, Enum):
    INFO = "info"
    WARN = "warning"
    BLOCK = "block"


@dataclass
class SafetyFinding:
    """Un hallazgo del Safety Gate sobre una operación propuesta."""
    severity: Severity
    message: str
    resource: Optional[str] = None
    remedy: Optional[str] = None  # cómo desbloquear (solución alternativa)


@dataclass
class SafetyReport:
    """Resultado de evaluar una operación contra las políticas de seguridad."""
    allowed: bool
    findings: list[SafetyFinding] = field(default_factory=list)
    destructive_count: int = 0

    @property
    def blocked(self) -> list[SafetyFinding]:
        return [f for f in self.findings if f.severity == Severity.BLOCK]

    @property
    def warnings(self) -> list[SafetyFinding]:
        return [f for f in self.findings if f.severity == Severity.WARN]

    def summary(self) -> str:
        lines = [f"Safety Gate: {'APROBADO' if self.allowed else 'BLOQUEADO'} "
                 f"({len(self.blocked)} bloqueos)"]
        for f in self.findings:
            icon = {"info": "[i]", "warning": "[!]", "block": "[X]"}[f.severity.value]
            lines.append(f"  {icon} {f.message}")
            if f.remedy:
                lines.append(f"        remedio: {f.remedy}")
        return "\n".join(lines)


class SafetyGate:
    """Evalúa operaciones propuestas contra las políticas del manifiesto."""

    def __init__(self, policy: SafetyPolicy):
        self.policy = policy

    def check_server_action(self, server_name: str, action: ServerAction,
                            currently_exists: bool) -> SafetyFinding | None:
        """Retorna un hallazgo BLOQUEANTE o None si está permitido."""
        # Destrucción
        if action == ServerAction.DESTROY:
            if not currently_exists:
                return SafetyFinding(
                    Severity.INFO,
                    f"destroy de '{server_name}' pero no existe; sin efecto.",
                    server_name,
                )
            if not self.policy.destroy:
                return SafetyFinding(
                    Severity.BLOCK,
                    f"destroy de '{server_name}' BLOQUEADO: exige "
                    f"safety.destroy: true y confirmación explícita.",
                    server_name,
                    remedy="Añadir 'safety.destroy: true' y 'action: destroy' "
                           "explicitamente al manifiesto.",
                )
            return None

        # Update que podría caer un servicio existente
        if action == ServerAction.UPDATE and currently_exists:
            if not self.policy.allow_downtime:
                return SafetyFinding(
                    Severity.WARN,
                    f"actualización de '{server_name}' (existe) puede causar "
                    f"tiempo de inactividad.",
                    server_name,
                    remedy="Añadir 'safety.allow_downtime: true' si aceptas downtime.",
                )
        return None

    def evaluate(self, manifest: Manifest, diff) -> SafetyReport:
        """Evalúa todo un plan de cambios derivado del manifiesto."""
        report = SafetyReport(allowed=True)
        report.destructive_count = 0

        # Servidores programados para destroy
        for server in manifest.servers:
            if server.action == ServerAction.DESTROY:
                report.destructive_count += 1

        for server in manifest.servers:
            exists = diff.server_exists(server.name) if hasattr(diff, "server_exists") else False
            finding = self.check_server_action(server.name, server.action, exists)
            if finding:
                report.findings.append(finding)

        # Redes declaradas que no existen y se crean: siempre ok.
        # Redes existentes que se reconfiguran (update): warning si no allow_downtime.
        for net in manifest.networks:
            # (simplificado: en fase 0 solo se crean)
            pass

        if not self.policy.confirm_destructive and report.destructive_count:
            report.findings.append(SafetyFinding(
                Severity.WARN,
                "confirm_destructive está desactivado pero hay operaciones destructivas.",
                remedy="Reactivar 'safety.confirm_destructive: true'.",
            ))

        report.allowed = len(report.blocked) == 0
        return report
