"""Discovery / infrastructure scan.

Encapsulates querying the current hypervisor state. For ESXi it uses
govc (VMware CLI). Provides a `mock` adapter for development without
real access.

Each method returns flat data structures (dicts) that the planner uses
to compute the diff against the declared manifest.
"""

from __future__ import annotations

import json
import os
import subprocess
import re
from dataclasses import dataclass, field
from typing import Any, Optional


def _num(value: str) -> int:
    """Extract the first integer from a string like '6 vCPU(s)' or '24576MB'."""
    m = re.search(r"\d+", str(value))
    return int(m.group(0)) if m else 0


def _size_bytes(value: str) -> int:
    """Convert 'X GB', 'Y TB', 'Z MB' to bytes."""
    m = re.search(r"([\d.]+)\s*(B|KB|MB|GB|TB|PB)", str(value), re.IGNORECASE)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2).upper()
    mult = {"B": 1, "KB": 10**3, "MB": 10**6, "GB": 10**9, "TB": 10**12,
            "PB": 10**15}[unit]
    return int(num * mult)

from infranix.config import InfraConfig


# ─────────────────────────── Structures ───────────────────────────

@dataclass
class VMState:
    name: str
    power_state: str
    cpu: int
    mem_mb: int
    guest_os: str
    ip: Optional[str]
    template: bool = False


@dataclass
class DatastoreState:
    name: str
    type: str
    capacity_bytes: int
    free_bytes: int


@dataclass
class Inventory:
    """Complete snapshot of the current hypervisor state."""
    host: str
    os_version: str
    vms: list[VMState] = field(default_factory=list)
    datastores: list[DatastoreState] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    hypervisor: str = "esxi"
    compute_cluster_host: str = ""

    def to_dict(self) -> dict:
        return {
            "host": self.host,
            "os_version": self.os_version,
            "hypervisor": self.hypervisor,
            "compute_cluster_host": self.compute_cluster_host,
            "vms": [vars(v) for v in self.vms],
            "datastores": [vars(d) for d in self.datastores],
            "networks": self.networks,
            "images": self.images,
        }


# ─────────────────────────── Base ───────────────────────────

class Scanner:
    """Common interface for all discovery adapters."""

    def scan(self) -> Inventory:  # pragma: no cover
        raise NotImplementedError


# ─────────────────────────── ESXi via govc ───────────────────────────

class ESXiScanner(Scanner):
    """Scan of a standalone ESXi using the `govc` CLI."""

    def __init__(self, config: InfraConfig):
        self.config = config
        self.url = self._build_url()

    def _build_url(self) -> str:
        env = os.environ.get("GOVC_URL")
        if env:
            return env
        # /sdk for HostAgent (standalone ESXi)
        return f"https://root:{self.config.password}@{self.config.host}/sdk"

    def _govc(self, args: list[str]) -> str:
        env = dict(os.environ)
        env["GOVC_URL"] = self.url
        env["GOVC_INSECURE"] = "true"
        res = subprocess.run(
            ["govc", *args],
            capture_output=True, text=True, env=env, timeout=60,
        )
        if res.returncode != 0:
            raise RuntimeError(f"govc {' '.join(args)} failed: {res.stderr.strip()}")
        return res.stdout

    def _about(self) -> dict:
        out = self._govc(["about"])
        info = {}
        for line in out.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()
        return info

    def _list_vms(self) -> list[str]:
        out = self._govc(["ls", "/ha-datacenter/vm"])
        names = []
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("/ha-datacenter/"):
                line = line.replace("/ha-datacenter/vm/", "", 1)
            names.append(line)
        return names

    def _vm_info(self, name: str) -> VMState:
        out = self._govc(["vm.info", name])
        info = {}
        for line in out.splitlines():
            if ":" in line:
                k, _, v = line.partition(":")
                info[k.strip()] = v.strip()
        power = info.get("Power state", "unknown")
        return VMState(
            name=name,
            power_state=power,
            cpu=_num(info.get("CPU") or "0"),
            mem_mb=_num(info.get("Memory") or "0"),
            guest_os=info.get("Guest name", ""),
            ip=info.get("IP address") or None,
        )

    def _datastores(self) -> list[DatastoreState]:
        out = self._govc(["datastore.info"])
        blocks = [b for b in out.split("\n\n") if b.strip()]
        stores = []
        for block in blocks:
            info = {}
            for line in block.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    info[k.strip()] = v.strip()
            if "Name" in info:
                stores.append(DatastoreState(
                    name=info["Name"],
                    type=info.get("Type", ""),
                    capacity_bytes=_size_bytes(info.get("Capacity") or "0"),
                    free_bytes=_size_bytes(info.get("Free") or "0"),
                ))
        return stores

    def _networks(self) -> list[str]:
        data = self._govc(["ls", "/ha-datacenter/network"])
        names = []
        for line in data.splitlines():
            line = line.strip()
            if not line:
                continue
            # take the base name (last part of the path)
            name = line.split("/")[-1]
            names.append(name)
        return names

    def _images(self) -> list[str]:
        """List of ISOs/images available on the datastore (ISO folder)."""
        try:
            out = self._govc(["datastore.ls", "ISO"])
            return [l.strip() for l in out.splitlines() if l.strip()]
        except RuntimeError:
            return []

    def _host_name(self) -> str:
        try:
            out = self._govc(["host.info"])
            for line in out.splitlines():
                if line.strip().startswith("Name:"):
                    return line.split(":", 1)[1].strip()
        except RuntimeError:
            pass
        return ""

    def scan(self) -> Inventory:
        about = self._about()
        vms = [self._vm_info(name) for name in self._list_vms()]
        return Inventory(
            host=self.config.host or "",
            os_version=about.get("Version", ""),
            vms=vms,
            datastores=self._datastores(),
            networks=self._networks(),
            images=self._images(),
            hypervisor=self.config.hypervisor,
            compute_cluster_host=self._host_name(),
        )


# ─────────────────────────── Mock ───────────────────────────

class MockScanner(Scanner):
    """Simulated scan for development without a real ESXi."""

    def __init__(self, config: InfraConfig):
        self.config = config
        self._fixture = {
            "host": "mock-host",
            "os_version": "8.0.3",
            "vms": [
                {"name": "web-prod-01", "power_state": "poweredOn", "cpu": 4,
                 "mem_mb": 8192, "guest_os": "RHEL 9", "ip": "10.0.0.10"},
            ],
            "datastores": [
                {"name": "datastore1", "type": "VMFS", "capacity_bytes": 10**12,
                 "free_bytes": 5 * 10**11},
            ],
            "networks": ["VM Network"],
            "images": ["rhel-9.4-x86_64-dvd.iso"],
        }

    def scan(self) -> Inventory:
        f = self._fixture
        inv = Inventory(
            host=f["host"], os_version=f["os_version"],
            hypervisor=self.config.hypervisor,
        )
        inv.vms = [VMState(**v) for v in f["vms"]]
        inv.datastores = [DatastoreState(**d) for d in f["datastores"]]
        inv.networks = f["networks"]
        inv.images = f["images"]
        return inv


# ─────────────────────────── Factory ───────────────────────────

def make_scanner(config: InfraConfig) -> Scanner:
    """Return the right scanner according to the configured hypervisor."""
    hv = config.hypervisor.lower()
    if hv in ("mock",):
        return MockScanner(config)
    if hv in ("esxi", "vsphere", "vcenter", "proxmox", "kvm"):
        # For now ESXi via govc as the primary backend
        return ESXiScanner(config)
    raise ValueError(f"Unsupported hypervisor: {hv}")
