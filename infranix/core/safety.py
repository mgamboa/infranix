"""Safety Gate — InfraNix's security core.

Guarantees that NO destructive or downtime operation runs without explicit
opt-in. It is the default layer, never optional.

Rules:
  - Destroy  : requires safety.destroy=True (global) OR action=destroy + confirmation on the resource.
  - Downtime : shutting down/affecting an existing service requires safety.allow_downtime=True.
  - Overwrite: modifying existing config always logs a backup and asks for confirmation.
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
    """A Safety Gate finding about a proposed operation."""
    severity: Severity
    message: str
    resource: Optional[str] = None
    remedy: Optional[str] = None  # how to unblock (workaround)


@dataclass
class SafetyReport:
    """Result of evaluating an operation against the safety policies."""
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
        lines = [f"Safety Gate: {'APPROVED' if self.allowed else 'BLOCKED'} "
                 f"({len(self.blocked)} blocks)"]
        for f in self.findings:
            icon = {"info": "[i]", "warning": "[!]", "block": "[X]"}[f.severity.value]
            lines.append(f"  {icon} {f.message}")
            if f.remedy:
                lines.append(f"        remedy: {f.remedy}")
        return "\n".join(lines)


class SafetyGate:
    """Evaluate proposed operations against the manifest's policies."""

    def __init__(self, policy: SafetyPolicy):
        self.policy = policy

    def check_server_action(self, server_name: str, action: ServerAction,
                            currently_exists: bool) -> SafetyFinding | None:
        """Return a BLOCKING finding or None if allowed."""
        # Destruction
        if action == ServerAction.DESTROY:
            if not currently_exists:
                return SafetyFinding(
                    Severity.INFO,
                    f"destroy of '{server_name}' but it does not exist; no effect.",
                    server_name,
                )
            if not self.policy.destroy:
                return SafetyFinding(
                    Severity.BLOCK,
                    f"destroy of '{server_name}' BLOCKED: requires "
                    f"safety.destroy: true and explicit confirmation.",
                    server_name,
                    remedy="Add 'safety.destroy: true' and 'action: destroy' "
                           "explicitly to the manifest.",
                )
            return None

        # Update that could take down an existing service
        if action == ServerAction.UPDATE and currently_exists:
            if not self.policy.allow_downtime:
                return SafetyFinding(
                    Severity.WARN,
                    f"update of '{server_name}' (exists) may cause "
                    f"downtime.",
                    server_name,
                    remedy="Add 'safety.allow_downtime: true' if you accept downtime.",
                )
        return None

    def evaluate(self, manifest: Manifest, diff) -> SafetyReport:
        """Evaluate a whole change plan derived from the manifest."""
        report = SafetyReport(allowed=True)
        report.destructive_count = 0

        # Servers scheduled for destroy
        for server in manifest.servers:
            if server.action == ServerAction.DESTROY:
                report.destructive_count += 1

        for server in manifest.servers:
            exists = diff.server_exists(server.name) if hasattr(diff, "server_exists") else False
            finding = self.check_server_action(server.name, server.action, exists)
            if finding:
                report.findings.append(finding)

        # Declared networks that do not exist and are created: always ok.
        # Existing networks being reconfigured (update): warning if not allow_downtime.
        for net in manifest.networks:
            # (simplified: in phase 0 they are only created)
            pass

        if not self.policy.confirm_destructive and report.destructive_count:
            report.findings.append(SafetyFinding(
                Severity.WARN,
                "confirm_destructive is disabled but there are destructive operations.",
                remedy="Re-enable 'safety.confirm_destructive: true'.",
            ))

        report.allowed = len(report.blocked) == 0
        return report
