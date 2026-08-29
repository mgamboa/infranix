"""infra collection init — generates a collection skeleton (ansible-galaxy style).

Created structure, analogous to `ansible-galaxy init`:

    <name>/
      pyproject.toml            # defines the 'infranix.collections' entry point
      requirements.yml          # collections it depends on (like requirements.yml)
      infra_declaration/        # ≡ tasks/: resource declarations it applies
        __init__.py
        main.yml                # example resource declaration
      infra_nix/                # the python package with the Provider
        __init__.py
        provider.py
        capabilities.py         # the re-exported Capability enum
      README.md
"""

from __future__ import annotations

from pathlib import Path

cap_help = """\
Capabilities supported by InfraNix (enum):
  scan      - discovery / hypervisor state
  provision - create/update resources
  configure - configure inside the VMs
  image     - download/upload images
  build     - build templates
Choose yours in provider.py.
"""

PYPROJECT = """\
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "{pkg}"
version = "0.1.0"
description = "InfraNix collection: {name}"
requires-python = ">=3.11"
dependencies = [
    "infranix>=0.1.0",
]

[project.entry-points."infranix.collections"]
{name} = "{pkg}.provider:Provider"

[tool.setuptools.packages.find]
include = ["{pkg}*"]
"""

REQUIREMENTS_YML = """\
# Collection requirements (equivalent to ansible-galaxy requirements.yml).
# Declares which collections/builtins this collection depends on.
# The core installs them automatically before using this collection.

collections:
  - name: vmware          # builtin (scan)
  - name: terraform       # builtin (provision)
"""

MAIN_YML = """\
# infra_declaration/  —  ≡ Ansible tasks/.
# In this directory you declare the resources the collection applies.
# The grain (Provider) interprets them; here they are just declarations.

- name: example-server
  type: server
  state: present
  spec:
    cpu: 2
    mem: 2048
    disk: 20
"""

PROVIDER_PY = """\
from __future__ import annotations

from infranix.pluginbase import Capability, PluginContext, PluginProvider, PluginReport


class Provider(PluginProvider):
    name = "{name}"
    version = "0.1.0"
    description = "{name} collection for InfraNix"
    # Choose your capabilities: scan, provision, configure, image, build
    capabilities = frozenset({{Capability.SCAN, Capability.PROVISION}})

    def require(self, ctx: PluginContext) -> list[str]:
        # e.g. check binaries/credentials before acting
        return []

    def validate(self, ctx: PluginContext, manifest) -> list[str]:
        return []

    def plan(self, ctx: PluginContext) -> dict:
        return {{}}

    def apply(self, ctx: PluginContext) -> PluginReport:
        # Here goes YOUR real collection logic.
        return PluginReport(ok=True, action="none",
                            message="{name}: apply executed (skeleton).")


provider = Provider
"""

PKG_INIT = '''"""{name} — InfraNix collection."""

from .provider import Provider

__all__ = ["Provider"]
'''

CAPABILITIES_PY = """\
from infranix.pluginbase import Capability  # re-exported from core
"""

README = """\
# {name} — InfraNix collection

Collection for InfraNix created with `infra collection init`.

## Structure (analogous to ansible-galaxy)

- `pyproject.toml` — declares the `infranix.collections` entry point.
- `requirements.yml` — collections it depends on (auto-installed).
- `infra_declaration/` — resource declarations (≡ `tasks/`).
- `{pkg}/` — the Python package with your `Provider`.

## Installation

Develop your `Provider` in `{pkg}/provider.py`, then:

    pip install -e .
    infra collection list      # your collection should appear

If there is no internet, package and distribute the tar.gz:

    python -m build
    infra collection install-from-archive dist/{pkg}-0.1.0.tar.gz {name}

## Declare it in a manifest

    project: demo
    collections:
      - name: {name}
        source: pip          # or archive with path: dist/....
"""


def init_collection(name: str, out: Path) -> Path:
    """Create the `name` collection skeleton under `out`."""
    safe = name.replace("-", "_").replace(".", "_").lower()
    root = (out / name).resolve()
    pkg = f"infra_collection_{safe}"

    files = {
        "pyproject.toml": PYPROJECT.format(name=name, pkg=pkg),
        "requirements.yml": REQUIREMENTS_YML,
        "README.md": README.format(name=name, pkg=pkg),
        f"{pkg}/__init__.py": PKG_INIT.format(name=name),
        f"{pkg}/provider.py": PROVIDER_PY.format(name=name),
        f"{pkg}/capabilities.py": CAPABILITIES_PY.format(name=name),
        "infra_declaration/__init__.py": "",
        "infra_declaration/main.yml": MAIN_YML,
    }
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(content)

    (root / "capabilities.txt").write_text(cap_help)
    return root


# ─────────────────────────────────────────────────────────────────────────
# `infra role init <name>` — InfraNix-native role scaffold
#
# Layout (same spirit as `ansible-galaxy init <role>` but adapted to InfraNix):
#
#   <name>/
#     collections/requirements.yml   # collections the app reads/installs (ansible-galaxy-style)
#     defaults/main.yml              # default variables for the role's infra.yaml
#     infra/infra.yaml               # the InfraNix manifest this role executes
#     README.md
#
# `infra` reads `collections/requirements.yml` to know which collections this
# role needs, merges `defaults/main.yml` as default variables, and runs the
# `infra/infra.yaml` manifest (declarations) as the actual work.
# ─────────────────────────────────────────────────────────────────────────

PROJECT_REQUIREMENTS_YML = """\
# Collections this project needs. `infra` reads this file and auto-installs any
# missing collection before executing infra/infra.yaml (like ansible-galaxy
# installing requirements.yml). Add entries here as needed.

collections:
  - name: vmware
    source: builtin
  - name: terraform
    source: builtin
  - name: ansible
    source: builtin
"""

PROJECT_DEFAULTS_MAIN = """\
# Your variables for this project live here (no ~/.infranix/.env needed).
# ${KEY} in infra/infra.yaml resolves to the value of KEY in this file.
# Real environment variables still take precedence if exported.

# ── Hypervisor / credentials ──
INFRA_HYPERVISOR: esxi          # vcenter | esxi | proxmox | kvm | mock
INFRA_HOST: 192.168.2.81
INFRA_USER: root
INFRA_PASSWORD: your_password
INFRA_INSECURE: 1
INFRA_DATACENTER: ""
INFRA_DATASTORE: datastore1
INFRA_NETWORK: VM Network

# ── VM root password (baked into template via kickstart, used by Ansible SSH) ──
ROOT_PASSWORD: your_root_password

# ── Network / demo values ──
SUBNET: 192.168.2.0/24
GATEWAY: 192.168.2.1
DNS: 192.168.2.1
WEB1_IP: 192.168.2.11/24
ROCKY_VERSION: "9.5"
PROJECT_NAME: demo
"""

PROJECT_INFRA_YAML = """\
# InfraNix manifest — the work this project performs.
# ${KEY} resolves from defaults/main.yml (or real env / ~/.infranix/.env).
version: 1
project: ${PROJECT_NAME}
hypervisor: ${INFRA_HYPERVISOR}

collections:
  - name: vmware
    source: builtin
  - name: terraform
    source: builtin
  - name: ansible
    source: builtin

images:
  - name: rocky-${ROCKY_VERSION}
    distro: rocky
    version: ${ROCKY_VERSION}

servers:
  - name: web-01
    image: rocky-${ROCKY_VERSION}
    cpu: 2
    mem: 2048
    disk: 20
    network:
      - name: ${INFRA_NETWORK}
        ip: ${WEB1_IP}
        gateway: ${GATEWAY}
        dns: ['${DNS}']
    roles: [webserver]
    vars:
      root_password: ${{ROOT_PASSWORD}}
    action: create
"""

PROJECT_README = """\
# {name} — InfraNix project

A self-contained InfraNix project: a folder that declares what to orchestrate,
following the same layout spirit as an Ansible role.

## Structure

- `collections/requirements.yml` — collections `infra` reads and installs.
- `defaults/main.yml` — default variables (injected as `${{{{VAR}}}}` in the
  manifest).
- `infra/infra.yaml` — the InfraNix manifest this project executes.
- `kickstart/` — unattended OS installation templates (RHEL kickstart, Ubuntu
  preseed). Customize them per project. Packer uses these during image build.

## Run it

    infra project run {name}

Pattern a project to deploy any stack (e.g. a `redhat-satellite` project that
provisions a RHEL VM and subscribes/installs Satellite).
"""

# ── Kickstart templates (OS unattended installation) ──

KICKSTART_RHEL = """\
# Kickstart generated by InfraNix — RHEL / Rocky / CentOS
# Customize this file per project. Packer serves it via HTTP during build.
# The root password is baked into the template; Ansible uses it for SSH.

lang en_US.UTF-8
keyboard us
timezone UTC

# Network: DHCP during install; Terraform customizes with static IP on clone
network --bootproto=dhcp --device=link --activate

# Root password — comes from defaults/main.yml ROOT_PASSWORD
rootpw --iscrypted {{ root_password_hash }}

# Install from mirror (or local ISO)
url --url="{{ mirror }}"
repo --name="baseos" --baseurl="{{ mirror }}/BaseOS"
repo --name="appstream" --baseurl="{{ mirror }}/AppStream"

%packages
@core
open-vm-tools
%end

%addon com_redhat_kdump --disable
%end

clearpart --all --initlabel
part /boot --fstype="xfs" --size=1024 --asprimary
part pv.01 --size=1 --grow
volgroup vg0 pv.01
logvol / --fstype="xfs" --name=root --vgname=vg0 --size=20480 --grow

services --enabled="NetworkManager,sshd,cloud-init,open-vm-tools"

%post
# Allow root SSH (Ansible needs it)
sed -i 's/^#PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
systemctl enable sshd
systemctl enable open-vm-tools
systemctl enable cloud-init
%end

reboot
"""

KICKSTART_UBUNTU = """\
# Preseed generated by InfraNix — Ubuntu / Debian
# Customize this file per project.

d-i debian-installer/locale string en_US
d-i keyboard-configuration/xkb-keymap select us
d-i time/zone string UTC
d-i netcfg/choose_interface select auto
d-i netcfg/get_hostname string {{ hostname }}
d-i netcfg/get_domain string local
d-i mirror/country string manual
d-i mirror/http/hostname string {{ mirror }}
d-i mirror/http/directory string /ubuntu

d-i passwd/root-login boolean true
d-i passwd/root-password string {{ root_password }}
d-i passwd/root-password-again string {{ root_password }}

d-i partman/mount_style select uuid
d-i partman-auto/method string regular
d-i partman-auto/choose_recipe select atomic
d-i partman/confirm_write_new_label boolean true
d-i partman/choose_partition select finish
d-i partman/confirm boolean true
d-i partman/confirm_nooverwrite boolean true

d-i pkgsel/include string openssh-server cloud-init open-vm-tools
d-i pkgsel/upgrade select none
d-i grub-installer/only_debian boolean true
d-i grub-installer/bootdev string default

d-i finish-install/reboot_in_progress note
"""


def init_project(name: str, out: Path) -> Path:
    """Create the `name` InfraNix project scaffold under `out`."""
    root = (out / name).resolve()
    files = {
        "collections/requirements.yml": PROJECT_REQUIREMENTS_YML,
        "defaults/main.yml": PROJECT_DEFAULTS_MAIN,
        "infra/infra.yaml": PROJECT_INFRA_YAML,
        "kickstart/rhel.ks.cfg": KICKSTART_RHEL,
        "kickstart/ubuntu.preseed.cfg": KICKSTART_UBUNTU,
        "README.md": PROJECT_README.format(name=name),
    }
    for rel, content in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_text(content)
    return root
