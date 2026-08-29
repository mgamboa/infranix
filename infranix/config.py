"""Configuración segura de InfraNix.

Las credenciales viven en ~/.infranix/.env (nunca en el repo).
Este módulo las lee de forma opcional y devuelve una configuración
que puede ser suplida con un adaptador mock cuando no hay accesso real.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field


DOTENV_PATH = Path.home() / ".infranix" / ".env"
CONFIG_DIR = Path.home() / ".infranix"


@dataclass
class InfraConfig:
    """Configuración global de InfraNix leída desde el entorno."""

    hypervisor: str = "esxi"          # vcenter | esxi | proxmox | kvm | mock
    host: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = None
    datacenter: Optional[str] = None
    datastore: Optional[str] = None
    network: Optional[str] = None
    insecure: bool = True

    # Cache de imágenes
    image_cache: Path = CONFIG_DIR / "images"

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password)


def _load_dotenv(path: Path) -> dict[str, str]:
    """Carga variables KEY=VALUE desde un archivo .env simple."""
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_config() -> InfraConfig:
    """Carga la configuración desde ~/.infranix/.env (crea directorio si falta).

    Prioridad: .env > variables de entorno > defaults.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    file_env = _load_dotenv(DOTENV_PATH)

    def _get(key: str) -> Optional[str]:
        return os.environ.get(key) or file_env.get(key)

    host = _get("INFRA_HOST") or _get("INFRA_ESXI_HOST")
    user = _get("INFRA_USER") or _get("INFRA_ESXI_USER")
    password = _get("INFRA_PASSWORD") or _get("INFRA_ESXI_PASSWORD")

    return InfraConfig(
        hypervisor=_get("INFRA_HYPERVISOR") or "esxi",
        host=host,
        user=user,
        password=password,
        datacenter=_get("INFRA_DATACENTER"),
        datastore=_get("INFRA_DATASTORE"),
        network=_get("INFRA_NETWORK"),
        insecure=(_get("INFRA_INSECURE") or "1") != "0",
    )


def write_config_template() -> Path:
    """Escribe un .env de ejemplo (sin credenciales) si no existe."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not DOTENV_PATH.exists():
        example = (
            "# InfraNix credentials (local only, never commit)\n"
            "INFRA_HYPERVISOR=esxi\n"
            "INFRA_HOST=192.168.x.x\n"
            "INFRA_USER=root\n"
            "INFRA_PASSWORD=\n"
            "INFRA_DATACENTER=\n"
            "INFRA_DATASTORE=\n"
            "INFRA_NETWORK=\n"
            "INFRA_INSECURE=1\n"
        )
        DOTENV_PATH.write_text(example)
    return DOTENV_PATH
