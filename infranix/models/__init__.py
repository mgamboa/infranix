"""Pydantic models that define the declarative manifest schema.

The schema covers: servers (VMs), networks, routers, load balancers, images
and safety policies. It is the contract that validates every YAML manifest.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


# ─────────────────────────── Enums ───────────────────────────
class Hypervisor(str, Enum):
    VSPHERE = "vsphere"
    VCENTER = "vcenter"
    ESXI = "esxi"
    PROXMOX = "proxmox"
    KVM = "kvm"
    MOCK = "mock"


class ImageSourceType(str, Enum):
    ISO = "iso"
    OVA = "ova"
    CLOUDIMAGE = "cloudimage"
    TEMPLATE = "template"


class ImageBuilder(str, Enum):
    PACKER = "packer"
    DIRECT = "direct"


class NetworkType(str, Enum):
    PORTGROUP = "portgroup"
    DVS = "dvswitch"
    VLAN = "vlan"


class RouterKind(str, Enum):
    VYOS = "vyos"
    OPNNSENSE = "opnsense"
    PFSENSE = "pfsense"


class LoadBalancerType(str, Enum):
    HAPROXY = "haproxy"
    NGINX = "nginx"
    NSX = "nsx"
    AWS_ELB = "aws-elb"
    F5 = "f5"


class ServerAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DESTROY = "destroy"


class Capability(str, Enum):
    """What a collection can do."""
    SCAN = "scan"            # discovery / current hypervisor state
    PROVISION = "provision"  # create/update resources (Terraform, cloud-init...)
    CONFIGURE = "configure"  # configure software inside the VMs (Ansible)
    IMAGE = "image"          # download/upload images (ISO/OVA/cloud-image)
    BUILD = "build"          # build templates (Packer)


# ─────────────────────────── Models ───────────────────────────

class SafetyPolicy(BaseModel):
    """Safety policies. Conservative by default."""
    destroy: bool = False
    allow_downtime: bool = False
    confirm_destructive: bool = True
    scan_before_apply: bool = True


class ImageSource(BaseModel):
    type: ImageSourceType = ImageSourceType.ISO
    url: Optional[str] = None


class ImageBuild(BaseModel):
    builder: ImageBuilder = ImageBuilder.DIRECT
    cloud_init: bool = False
    autounattend: bool = False


class Image(BaseModel):
    name: str
    distro: str
    version: str
    source: ImageSource = ImageSource()
    build: ImageBuild = ImageBuild()


class NetworkInterface(BaseModel):
    name: str
    ip: Optional[str] = None
    gateway: Optional[str] = None
    dns: list[str] = Field(default_factory=list)


class Server(BaseModel):
    name: str
    image: str
    cpu: int = Field(default=2, ge=1)
    mem: int = Field(default=2048, ge=512)   # MB
    disk: int = Field(default=20, ge=1)      # GB
    network: list[NetworkInterface] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    action: ServerAction = ServerAction.CREATE


class Network(BaseModel):
    name: str
    type: NetworkType = NetworkType.PORTGROUP
    vlan: Optional[int] = None
    subnet: Optional[str] = None
    gateway: Optional[str] = None
    dhcp: bool = False


class RouterInterface(BaseModel):
    network: str
    ip: Optional[str] = None


class Route(BaseModel):
    dest: str
    via: str


class Router(BaseModel):
    name: str
    image: str
    interfaces: list[RouterInterface] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)
    nat: bool = False


class LBListener(BaseModel):
    port: int
    protocol: str = "https"
    backend: list[str] = Field(default_factory=list)
    health: Optional[str] = None


class LoadBalancer(BaseModel):
    name: str
    type: LoadBalancerType = LoadBalancerType.HAPROXY
    listeners: list[LBListener] = Field(default_factory=list)


class CollectionSource(str, Enum):
    """Where the collection was installed from."""
    BUILTIN = "builtin"      # ships inside the infranix package
    PIP = "pip"              # pip install (PyPI, git, url)
    ARCHIVE = "archive"      # local tar.gz unzipped/installed by the user


class CollectionRequirement(BaseModel):
    """A collection the manifest declares as needed (ansible-galaxy style).

    The core verifies each requirement is available (builtin or installed)
    before running; if missing it installs it (pip or archive) as needed.
    """
    name: str            # 'terraform' | 'infra-collection-proxmox' | 'proxmox'
    version: Optional[str] = None      # version pkg / tag si aplica
    source: CollectionSource = CollectionSource.PIP   # cómo instalarla si falta
    path: Optional[str] = None         # para source=archive: ruta al tar.gz
    capabilities: list[Capability] = Field(default_factory=list)  # esperadas


class Manifest(BaseModel):
    """The root model of the declarative system."""
    version: int = 1
    project: str
    hypervisor: Hypervisor = Hypervisor.ESXI
    scan_before_apply: bool = True
    safety: SafetyPolicy = SafetyPolicy()
    collections: list[CollectionRequirement] = Field(default_factory=list)
    images: list[Image] = Field(default_factory=list)
    servers: list[Server] = Field(default_factory=list)
    networks: list[Network] = Field(default_factory=list)
    routers: list[Router] = Field(default_factory=list)
    load_balancers: list[LoadBalancer] = Field(default_factory=list)

    @model_validator(mode="after")
    def _names_unique(self) -> "Manifest":
        def _names(items, kind):
            names = [i.name for i in items]
            dups = {n for n in names if names.count(n) > 1}
            if dups:
                raise ValueError(f"duplicate {kind} names: {dups}")
        _names(self.servers, "servers")
        _names(self.networks, "networks")
        _names(self.routers, "routers")
        _names(self.load_balancers, "load_balancers")
        return self
