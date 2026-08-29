"""Planner — Calcula el diff entre el estado deseado (manifest) y el actual.

Genera un Plan legible y auditable: qué crea, actualiza, destruye, qué imágenes
faltan (para disparar el Image Manager), y qué productos/configuraciones aplica.
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
    counterpart: Optional[VMState] = None  # estado actual si existe


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
    """Construye un plan de cambios comparando manifest vs inventory."""

    def __init__(self, manifest: Manifest, inventory: Inventory):
        self.manifest = manifest
        self.inventory = inventory

    def _existing_vm(self, name: str) -> VMState | None:
        for vm in self.inventory.vms:
            if vm.name == name:
                return vm
        return None

    def _existing_image(self, name: str) -> bool:
        """Reconoce la imagen pedida contra las disponibles en el hypervisor.

        Hace match flexible: si el manifiesto pide 'rhel-9.5' y el datastore
        tiene 'rhel-9.5-x86_64-dvd.iso', se considera disponible (normaliza
        distro+version en el nombre del ISO).
        """
        if not name:
            return False
        target = name.lower().strip()

        def norm(s: str) -> str:
            # reduce espacios/puntos separadores a nada y quita engobe de ISO
            s = s.lower().strip()
            s = re.sub(r"[-_. ]+", "", s)
            s = s.replace("x86_64dvd", "").replace("x86_64", "")
            s = s.replace("dvd", "").replace("iso", "")
            s = s.replace("minimal", "")
            return s

        tn = norm(target)
        # match exacto
        for img in self.inventory.images:
            if target == img.lower():
                return True
        # match por base normalizada (rhel9.5 vs rhel9.5x86_64dvd)
        for img in self.inventory.images:
            if norm(img).startswith(tn) or tn.startswith(norm(img)):
                # guard: evitar match vacio
                if tn and norm(img):
                    return True
        return False

    def plan(self) -> Plan:
        plan = Plan()

        # ── Servidores ──
        for server in self.manifest.servers:
            existing = self._existing_vm(server.name)
            # ¿Existe la imagen que pide?
            # Fase 0: asumimos que la imagen es el nombre de un ISO/template
            # disponible; si no, lo marcamos para disparar Image Manager.
            image_key = str(server.image)
            if not self._existing_image(image_key) and not self._template_available(server):
                plan.images_missing.append(image_key)
                plan.changes.append(PlanChange(
                    ChangeKind.IMAGE_MISSING, "image", image_key,
                    detail=f"imagen '{image_key}' no local; Image Manager la descargará/construirá.",
                ))

            if server.action == ServerAction.DESTROY:
                if existing:
                    plan.changes.append(PlanChange(
                        ChangeKind.DESTROY, "server", server.name,
                        detail=f"destruir VM '{server.name}' "
                               f"({existing.cpu} vCPU / {existing.mem_mb}MB).",
                    ))
                    plan.server_exists_called = True
                else:
                    plan.changes.append(PlanChange(
                        ChangeKind.NOOP, "server", server.name,
                        detail="no existe; nada que destruir.",
                    ))
            elif existing:
                plan.changes.append(PlanChange(
                    ChangeKind.UPDATE, "server", server.name,
                    detail=f"actualizar '{server.name}' a "
                           f"{server.cpu}vCPU/{server.mem}MB/{server.disk}GB "
                           f"roles={server.roles}.",
                    counterpart=existing,
                ))
            else:
                plan.changes.append(PlanChange(
                    ChangeKind.CREATE, "server", server.name,
                    detail=f"crear '{server.name}' "
                           f"{server.cpu}vCPU/{server.mem}MB/{server.disk}GB "
                           f"imagen={server.image} roles={server.roles}.",
                ))

        # ── Redes ──
        existing_nets = set(self.inventory.networks)
        for net in self.manifest.networks:
            if net.name in existing_nets:
                plan.changes.append(PlanChange(
                    ChangeKind.UPDATE, "network", net.name,
                    detail=f"red '{net.name}' existe (tipo={net.type.value} vlan={net.vlan}).",
                ))
            else:
                plan.changes.append(PlanChange(
                    ChangeKind.CREATE, "network", net.name,
                    detail=f"crear red '{net.name}' (tipo={net.type.value} "
                           f"subnet={net.subnet} vlan={net.vlan}).",
                ))

        # ── Routers ──
        for router in self.manifest.routers:
            existing = self._existing_vm(router.name)
            if existing:
                plan.changes.append(PlanChange(
                    ChangeKind.UPDATE, "router", router.name,
                    detail=f"actualizar router '{router.name}' "
                           f"interfaces={[i.network for i in router.interfaces]}.",
                ))
            else:
                plan.changes.append(PlanChange(
                    ChangeKind.CREATE, "router", router.name,
                    detail=f"crear router '{router.name}' imagen={router.image} "
                           f"nat={router.nat}.",
                ))

        # ── Load balancers ──
        for lb in self.manifest.load_balancers:
            existing = self._existing_vm(lb.name)
            if existing:
                plan.changes.append(PlanChange(
                    ChangeKind.UPDATE, "load_balancer", lb.name,
                    detail=f"actualizar LB '{lb.name}' type={lb.type.value} "
                           f"listeners={len(lb.listeners)}.",
                ))
            else:
                plan.changes.append(PlanChange(
                    ChangeKind.CREATE, "load_balancer", lb.name,
                    detail=f"crear LB '{lb.name}' type={lb.type.value} "
                           f"listeners={len(lb.listeners)}.",
                ))

        return plan

    def _template_available(self, server) -> bool:
        """Heurística: un template/clone base puede estar disponible sin ser ISO."""
        # Simplificado: si el inventario no lista la imagen, asumimos que no
        # hay template. (Se refina en fases posteriores.)
        return False
