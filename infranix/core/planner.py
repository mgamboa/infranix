"""Planner — Computes the diff between the desired state (manifest) and the current one.

Generates a readable, auditable Plan: what it creates, updates, destroys, which
images are missing (to trigger the Image Manager), and which products/configs it applies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from infranix.adapters.discovery import Inventory, VMState
from infranix.models import Manifest, ServerAction


class ChangeKind(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DESTROY = "destroy"
    NOOP = "noop"
    IMAGE_MISSING = "image-missing"


@dataclass
class PlanChange:
    kind: ChangeKind
    resource_type: str        # server | network | router | load_balancer | image
    name: str
    detail: str = ""
    counterpart: Optional[VMState] = None  # current state if it exists


@dataclass
class Plan:
    changes: list[PlanChange] = field(default_factory=list)
    images_missing: list[str] = field(default_factory=list)

    def server_exists(self, name: str) -> bool:
        return any(
            c.name == name and c.resource_type == "server"
            and c.kind in (ChangeKind.UPDATE, ChangeKind.DESTROY)
            for c in self.changes
        )

    def summary(self) -> str:
        counts = {}
        for c in self.changes:
            counts[c.kind.value] = counts.get(c.kind.value, 0) + 1
        parts = [f"{k}:{v}" for k, v in counts.items()] or ["noop"]
        return "Plan: " + ", ".join(parts)


class Planner:
    """Builds a change plan comparing the manifest against the inventory."""

    def __init__(self, manifest: Manifest, inventory: Inventory):
        self.manifest = manifest
        self.inventory = inventory

    def _existing_vm(self, name: str) -> VMState | None:
        for vm in self.inventory.vms:
            if vm.name == name:
                return vm
        return None

    def _existing_image(self, name: str) -> bool:
        """Recognize the requested image against those available on the hypervisor.

        Does flexible matching: if the manifest asks for 'rhel-9.5' and the
        datastore has 'rhel-9.5-x86_64-dvd.iso', it is considered available
        (normalizes distro+version within the ISO name).
        """
        if not name:
            return False
        target = name.lower().strip()

        def norm(s: str) -> str:
            # reduce spaces/dots/separators to nothing and strip ISO noise
            s = s.lower().strip()
            s = re.sub(r"[-_. ]+", "", s)
            s = s.replace("x86_64dvd", "").replace("x86_64", "")
            s = s.replace("dvd", "").replace("iso", "")
            s = s.replace("minimal", "")
            return s

        tn = norm(target)
        # exact match
        for img in self.inventory.images:
            if target == img.lower():
                return True
        # normalized-base match (rhel9.5 vs rhel9.5x86_64dvd)
        for img in self.inventory.images:
            if norm(img).startswith(tn) or tn.startswith(norm(img)):
                # guard: avoid empty match
                if tn and norm(img):
                    return True
        return False

    def plan(self) -> Plan:
        plan = Plan()

        # ── Servers ──
        for server in self.manifest.servers:
            existing = self._existing_vm(server.name)
            # Does the requested image exist?
            # Phase 0: we assume the image is the name of an available ISO/template;
            # if not, we mark it to trigger the Image Manager.
            image_key = str(server.image)
            if not self._existing_image(image_key) and not self._template_available(server):
                plan.images_missing.append(image_key)
                plan.changes.append(PlanChange(
                    ChangeKind.IMAGE_MISSING, "image", image_key,
                    detail=f"image '{image_key}' not present; the Image Manager will download/build it.",
                ))

            if server.action == ServerAction.DESTROY:
                if existing:
                    plan.changes.append(PlanChange(
                        ChangeKind.DESTROY, "server", server.name,
                        detail=f"destroy VM '{server.name}' "
                               f"({existing.cpu} vCPU / {existing.mem_mb}MB).",
                    ))
                    plan.server_exists_called = True
                else:
                    plan.changes.append(PlanChange(
                        ChangeKind.NOOP, "server", server.name,
                        detail="does not exist; nothing to destroy.",
                    ))
            elif existing:
                plan.changes.append(PlanChange(
                    ChangeKind.UPDATE, "server", server.name,
                    detail=f"update '{server.name}' to "
                           f"{server.cpu}vCPU/{server.mem}MB/{server.disk}GB "
                           f"roles={server.roles}.",
                    counterpart=existing,
                ))
            else:
                plan.changes.append(PlanChange(
                    ChangeKind.CREATE, "server", server.name,
                    detail=f"create '{server.name}' "
                           f"{server.cpu}vCPU/{server.mem}MB/{server.disk}GB "
                           f"image={server.image} roles={server.roles}.",
                ))

        # ── Networks ──
        existing_nets = set(self.inventory.networks)
        for net in self.manifest.networks:
            if net.name in existing_nets:
                plan.changes.append(PlanChange(
                    ChangeKind.UPDATE, "network", net.name,
                    detail=f"network '{net.name}' exists (type={net.type.value} vlan={net.vlan}).",
                ))
            else:
                plan.changes.append(PlanChange(
                    ChangeKind.CREATE, "network", net.name,
                    detail=f"create network '{net.name}' (type={net.type.value} "
                           f"subnet={net.subnet} vlan={net.vlan}).",
                ))

        # ── Routers ──
        for router in self.manifest.routers:
            existing = self._existing_vm(router.name)
            if existing:
                plan.changes.append(PlanChange(
                    ChangeKind.UPDATE, "router", router.name,
                    detail=f"update router '{router.name}' "
                           f"interfaces={[i.network for i in router.interfaces]}.",
                ))
            else:
                plan.changes.append(PlanChange(
                    ChangeKind.CREATE, "router", router.name,
                    detail=f"create router '{router.name}' image={router.image} "
                           f"nat={router.nat}.",
                ))

        # ── Load balancers ──
        for lb in self.manifest.load_balancers:
            existing = self._existing_vm(lb.name)
            if existing:
                plan.changes.append(PlanChange(
                    ChangeKind.UPDATE, "load_balancer", lb.name,
                    detail=f"update LB '{lb.name}' type={lb.type.value} "
                           f"listeners={len(lb.listeners)}.",
                ))
            else:
                plan.changes.append(PlanChange(
                    ChangeKind.CREATE, "load_balancer", lb.name,
                    detail=f"create LB '{lb.name}' type={lb.type.value} "
                           f"listeners={len(lb.listeners)}.",
                ))

        return plan

    def _template_available(self, server) -> bool:
        """Heuristic: a base template/clone may be available without being an ISO."""
        # Simplified: if the inventory does not list the image we assume there
        # is no template. (Refined in later phases.)
        return False
