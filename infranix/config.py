"""Secure configuration for InfraNix.

Credentials live in ~/.infranix/.env (never in the repo).
This module reads them optionally and returns a configuration
that can be supplied via a mock adapter when there is no real access.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass, field


DOTENV_PATH = Path.home() / ".infranix" / ".env"
CONFIG_DIR = Path.home() / ".infranix"

VAR_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass
class InfraConfig:
    """Global InfraNix configuration read from the environment."""

    hypervisor: str = "esxi"          # vcenter | esxi | proxmox | kvm | mock
    host: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = None
    datacenter: Optional[str] = None
    datastore: Optional[str] = None
    network: Optional[str] = None
    insecure: bool = True

    # Image cache
    image_cache: Path = CONFIG_DIR / "images"

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password)


def _load_dotenv(path: Path) -> dict[str, str]:
    """Load KEY=VALUE variables from a simple .env file."""
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
    """Load configuration from ~/.infranix/.env (creates dir if missing).

    Priority: .env > environment variables > defaults.
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
    """Write an example .env (no credentials) if it does not exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not DOTENV_PATH.exists():
        example = (
            "# InfraNix credentials (local only, never commit)\n"
            "INFRA_HYPERVISOR=esxi\n"
            "INFRA_HOST=your-esxi-ip\n"
            "INFRA_USER=root\n"
            "INFRA_PASSWORD=\n"
            "INFRA_DATACENTER=\n"
            "INFRA_DATASTORE=\n"
            "INFRA_NETWORK=\n"
            "INFRA_INSECURE=1\n"
        )
        DOTENV_PATH.write_text(example)
    return DOTENV_PATH


def _env_for_vars() -> dict[str, str]:
    """Combined env (informative environment variables only) for ${VAR}."""
    merged = dict(os.environ)
    merged.update(_load_dotenv(DOTENV_PATH))
    return merged


def resolve_vars(value: Any, env: Optional[dict[str, str]] = None,
                 missing: str = "keep") -> Any:
    """Substitute ${VAR} in manifest strings/structures.

    `env` is a combined dict (os.environ + .env). If a variable does not exist
    and `missing='keep'` it is left as-is, if 'empty' it becomes '', if 'error'
    a ValueError is raised.
    """
    if env is None:
        env = _env_for_vars()

    if isinstance(value, str):
        def _sub(m: re.Match) -> str:
            key = m.group(1)
            if key in env:
                return env[key]
            if missing == "empty":
                return ""
            if missing == "error":
                raise ValueError(
                    f"Variable not defined: ${{{key}}}. "
                    f"Set it in ~/.infranix/.env or in the environment.")
            return m.group(0)
        return VAR_PATTERN.sub(_sub, value)

    if isinstance(value, dict):
        return {k: resolve_vars(v, env, missing) for k, v in value.items()}

    if isinstance(value, list):
        return [resolve_vars(v, env, missing) for v in value]

    return value
