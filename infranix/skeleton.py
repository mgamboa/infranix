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
