"""InfraNix Vault — encrypt/decrypt sensitive values in role defaults.

Similar to ansible-vault: passwords are derived via PBKDF2 and values are
encrypted with Fernet (AES-128-CBC + HMAC).  Encrypted values are marked
with the ``vault:`` prefix so ``role run`` can detect and decrypt them.

Password resolution order (highest → lowest):
  1. ``--vault-password`` CLI flag
  2. ``INFRA_VAULT_PASSWORD`` environment variable
  3. Interactive prompt (``getpass``)

Format::

    vault:<salt_hex>.<fernet_token>

The salt is 16 random bytes, encoded as hex.  The Fernet token is the
standard URL-safe-base64 output of ``cryptography.fernet.Fernet``.
"""

from __future__ import annotations

import base64
import os
import re
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

_VAULT_PREFIX = "vault:"
_SALT_BYTES = 16
_PBKDF2_ITERATIONS = 600_000  # OWASP 2023 recommendation


# ---------------------------------------------------------------------------
# Core encrypt / decrypt
# ---------------------------------------------------------------------------

def derive_key(password: str, salt: bytes) -> bytes:
    """Derive a 32-byte Fernet key from *password* + *salt* via PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF2_ITERATIONS,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def encrypt_value(plaintext: str, password: str) -> str:
    """Return ``vault:<salt_hex>.<fernet_token>`` for *plaintext*."""
    salt = os.urandom(_SALT_BYTES)
    key = derive_key(password, salt)
    token = Fernet(key).encrypt(plaintext.encode())
    return f"{_VAULT_PREFIX}{salt.hex()}.{token.decode()}"


def decrypt_value(vault_str: str, password: str) -> str:
    """Decrypt a ``vault:…`` string and return the plaintext.

    Raises ``ValueError`` if the format is wrong or the password is
    incorrect (wraps ``cryptography.fernet.InvalidToken``).
    """
    if not is_vault_encrypted(vault_str):
        raise ValueError("Value is not vault-encrypted (missing 'vault:' prefix)")
    payload = vault_str[len(_VAULT_PREFIX):]
    try:
        hex_salt, token = payload.split(".", 1)
        salt = bytes.fromhex(hex_salt)
    except (ValueError, AttributeError):
        raise ValueError("Malformed vault payload (expected vault:<hex>.<token>)")
    key = derive_key(password, salt)
    try:
        return Fernet(key).decrypt(token.encode()).decode()
    except InvalidToken:
        raise ValueError("Wrong vault password or corrupted ciphertext")


def is_vault_encrypted(value: str) -> bool:
    """Return True if *value* looks like a vault-encrypted string."""
    return isinstance(value, str) and value.startswith(_VAULT_PREFIX)


def can_decrypt(value: str, password: str) -> bool:
    """Return True if *value* is a vault string that can be decrypted with *password*."""
    if not is_vault_encrypted(value):
        return False
    try:
        decrypt_value(value, password)
        return True
    except (ValueError, InvalidToken):
        return False


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------

def decrypt_yaml_dict(data: dict, password: str) -> dict:
    """Return a copy of *data* where every ``vault:…`` value is decrypted."""
    out = {}
    for k, v in data.items():
        if is_vault_encrypted(v):
            out[k] = decrypt_value(v, password)
        else:
            out[k] = v
    return out


def encrypt_yaml_value(value: str, password: str) -> str:
    """Encrypt a single YAML value (convenience wrapper)."""
    return encrypt_value(value, password)


# ---------------------------------------------------------------------------
# Password resolution
# ---------------------------------------------------------------------------

def resolve_vault_password(
    explicit: Optional[str] = None,
    *,
    prompt: bool = True,
) -> Optional[str]:
    """Return the vault password, checking sources in priority order.

    Priority: *explicit* → ``INFRA_VAULT_PASSWORD`` env → interactive prompt.
    Returns ``None`` only when *prompt* is False and no source provides one.
    """
    if explicit:
        return explicit
    env_pw = os.environ.get("INFRA_VAULT_PASSWORD")
    if env_pw:
        return env_pw
    if prompt:
        import getpass
        return getpass.getpass("Vault password: ")
    return None
