"""infra collection init — genera un esqueleto de colección (style ansible-galaxy).

Estructura creada, análoga a `ansible-galaxy init`:

    <name>/
      pyproject.toml            # defina el entry point 'infranix.collections'
      requirements.yml          # colecciones de las que depende (como requirements.yml)
      infra_declaration/        # ≡ tasks/: declaraciones de recursos que aplica
        __init__.py
        main.yml                # ejemplo de declaración de recurso
      infra_nix/                # el paquete python con el Provider
        __init__.py
        provider.py
        capabilities.py         # el enum Capability re-exportado
      README.md
"""

from __future__ import annotations

from pathlib import Path

cap_help = """\
Capabilities soportadas por InfraNix (enum):
  scan      - discovery/estado del hypervisor
  provision - crear/actualizar recursos
  configure - configurar dentro de las VMs
  image     - descargar/subir imágenes
  build     - construir templates
Elige las tuyas en provider.py.
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
# Collection requirements (equivalente a ansible-galaxy requirements.yml).
# Declara de qué colecciones/builtins depende esta colección.
# El core las instala automáticamente antes de usar esta colección.

collections:
  - name: vmware          # builtin (scan)
  - name: terraform       # builtin (provision)
"""

MAIN_YML = """\
# infra_declaration/  —  ≡ tasks/ de Ansible.
# En este directorio declaras los recursos que la colección aplica.
# El grano (Provider) los interpreta; aquí solo son declaraciones.

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
    # Elige tus capabilities: scan, provision, configure, image, build
    capabilities = frozenset({{Capability.SCAN, Capability.PROVISION}})

    def require(self, ctx: PluginContext) -> list[str]:
        # Ej: chequea binarios/credenciales antes de actuar
        return []

    def validate(self, ctx: PluginContext, manifest) -> list[str]:
        return []

    def plan(self, ctx: PluginContext) -> dict:
        return {{}}

    def apply(self, ctx: PluginContext) -> PluginReport:
        # Aquí va TU lógica de real de la colección.
        return PluginReport(ok=True, action="none",
                            message="{name}: apply ejecutado (esqueleto).")


provider = Provider
"""

PKG_INIT = '''"""{name} — InfraNix collection."""

from .provider import Provider

__all__ = ["Provider"]
'''

CAPABILITIES_PY = """\
from infranix.pluginbase import Capability  # re-exportado desde core
"""

README = """\
# {name} — InfraNix collection

Colección para InfraNix creada con `infra collection init`.

## Estructura (analoga a ansible-galaxy)

- `pyproject.toml` — declara el entry point `infranix.collections`.
- `requirements.yml` — colecciones de las que depende (se instalan solas).
- `infra_declaration/` — declaraciones de recursos (≡ `tasks/`).
- `{pkg}/` — el paquete Python con tu `Provider`.

## Instalacion

Desarrolla tu `Provider` en `{pkg}/provider.py`, luego:

    pip install -e .
    infra collection list      # tu coleccion deberia aparecer

Si no hay internet, empaqueta y distribuye el tar.gz:

    python -m build
    infra collection install-from-archive dist/{pkg}-0.1.0.tar.gz {name}

## Declararla en un manifold

    project: demo
    collections:
      - name: {name}
        source: pip          # o archive con path: dist/....
"""


def init_collection(name: str, out: Path) -> Path:
    """Crea el esqueleto de la colección `name` bajo `out`."""
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