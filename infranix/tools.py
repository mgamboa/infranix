"""InfraNix Tool Installer — auto-installs CLI dependencies.

Each collection declares what binaries it needs; this module checks for
them and installs missing ones automatically before the pipeline runs.

Supported tools:
  - terraform   (HashiCorp binary)
  - packer      (HashiCorp binary)
  - govc        (VMware govc binary)
  - ansible     (pip install ansible)

Installation methods:
  1. Direct download to ~/.local/bin (preferred, no root needed)
  2. dnf/apt if available (fallback)
  3. pip for Python packages (ansible)
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

TOOLS_DIR = Path.home() / ".local" / "bin"


def _arch() -> str:
    """Return the architecture string for binary downloads."""
    machine = platform.machine()
    mapping = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }
    return mapping.get(machine, "amd64")


def _os_name() -> str:
    """Return 'linux', 'darwin', or 'windows'."""
    return platform.system().lower()


def _download(url: str, dest: Path) -> None:
    """Download a URL to a local file."""
    import urllib.request
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, str(dest))


def _extract_tarball(tarball: Path, dest: Path, binary_name: str) -> Path:
    """Extract a tar.gz and return the path to the binary inside."""
    import tarfile
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball) as tar:
        tar.extractall(dest)
    # find the binary
    for f in dest.rglob(binary_name):
        if f.is_file():
            return f
    raise FileNotFoundError(f"Binary '{binary_name}' not found in {dest}")


def _extract_zip(zipfile_path: Path, dest: Path, binary_name: str) -> Path:
    """Extract a zip and return the path to the binary inside."""
    import zipfile
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zipfile_path) as zf:
        zf.extractall(dest)
    for f in dest.rglob(binary_name):
        if f.is_file():
            return f
    raise FileNotFoundError(f"Binary '{binary_name}' not found in {dest}")


def _chmod_x(path: Path) -> None:
    """Make a file executable."""
    path.chmod(path.stat().st_mode | 0o755)


def _in_path(name: str) -> bool:
    """Check if a binary is in PATH."""
    return shutil.which(name) is not None


# ─── Individual tool installers ───────────────────────────────

def ensure_terraform() -> str:
    """Ensure terraform is available. Returns path or raises."""
    if _in_path("terraform"):
        return "terraform"

    version = "1.9.8"
    arch = _arch()
    os_name = _os_name()
    url = (f"https://releases.hashicorp.com/terraform/{version}/"
           f"terraform_{version}_{os_name}_{arch}.zip")
    dest = TOOLS_DIR / "terraform"

    print(f"  Installing terraform {version} to {dest}...")
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "terraform.zip"
        _download(url, zip_path)
        _extract_zip(zip_path, Path(tmp), "terraform")
        extracted = Path(tmp) / "terraform"
        if not extracted.exists():
            # might be in a subdirectory
            for f in Path(tmp).rglob("terraform"):
                if f.is_file() and not f.suffix:
                    extracted = f
                    break
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(extracted), str(dest))
        _chmod_x(dest)

    print(f"  ✓ terraform installed to {dest}")
    return str(dest)


def ensure_packer() -> str:
    """Ensure packer is available. Returns path or raises."""
    if _in_path("packer"):
        return "packer"

    version = "1.11.2"
    arch = _arch()
    os_name = _os_name()
    url = (f"https://releases.hashicorp.com/packer/{version}/"
           f"packer_{version}_{os_name}_{arch}.zip")
    dest = TOOLS_DIR / "packer"

    print(f"  Installing packer {version} to {dest}...")
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "packer.zip"
        _download(url, zip_path)
        _extract_zip(zip_path, Path(tmp), "packer")
        extracted = Path(tmp) / "packer"
        if not extracted.exists():
            for f in Path(tmp).rglob("packer"):
                if f.is_file() and not f.suffix:
                    extracted = f
                    break
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(extracted), str(dest))
        _chmod_x(dest)

    print(f"  ✓ packer installed to {dest}")
    return str(dest)


def ensure_govc() -> str:
    """Ensure govc is available. Returns path or raises."""
    if _in_path("govc"):
        return "govc"

    version = "0.47.1"
    arch = _arch()
    os_name = _os_name()
    url = (f"https://github.com/vmware/govmomi/releases/download/"
           f"govc/v{version}/govc_{os_name}_{arch}.tar.gz")
    dest = TOOLS_DIR / "govc"

    print(f"  Installing govc {version} to {dest}...")
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = Path(tmp) / "govc.tar.gz"
        _download(url, tar_path)
        _extract_tarball(tar_path, Path(tmp), "govc")
        extracted = Path(tmp) / "govc"
        if not extracted.exists():
            for f in Path(tmp).rglob("govc"):
                if f.is_file():
                    extracted = f
                    break
        TOOLS_DIR.mkdir(parents=True, exist_ok=True)
        shutil.move(str(extracted), str(dest))
        _chmod_x(dest)

    print(f"  ✓ govc installed to {dest}")
    return str(dest)


def ensure_ansible() -> str:
    """Ensure ansible-playbook is available. Returns path or raises."""
    if _in_path("ansible-playbook"):
        return "ansible-playbook"

    print("  Installing ansible via pip...")
    res = subprocess.run(
        [sys.executable, "-m", "pip", "install", "ansible"],
        capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to install ansible: {res.stderr[-500:]}")

    if not _in_path("ansible-playbook"):
        raise RuntimeError("ansible-playbook installed but not in PATH.")

    print("  ✓ ansible installed")
    return "ansible-playbook"


# ─── Tool registry ────────────────────────────────────────────

# Maps binary name → installer function
TOOL_INSTALLERS = {
    "terraform": ensure_terraform,
    "packer": ensure_packer,
    "govc": ensure_govc,
    "ansible-playbook": ensure_ansible,
}


def ensure_tools(binaries: list[str]) -> dict[str, str]:
    """Ensure all required binaries are available, installing missing ones.

    Args:
        binaries: list of binary names (e.g. ["terraform", "govc"])

    Returns:
        dict mapping binary name → resolved path

    Raises:
        RuntimeError if a tool cannot be installed.
    """
    # Ensure ~/.local/bin is in PATH (where we install tools)
    tools_dir_str = str(TOOLS_DIR)
    if tools_dir_str not in os.environ.get("PATH", ""):
        os.environ["PATH"] = tools_dir_str + ":" + os.environ.get("PATH", "")

    results: dict[str, str] = {}
    for name in binaries:
        if _in_path(name):
            results[name] = shutil.which(name) or name
            continue
        installer = TOOL_INSTALLERS.get(name)
        if installer is None:
            raise RuntimeError(
                f"Required tool '{name}' not found and no auto-installer "
                f"available. Install it manually.")
        results[name] = installer()
    return results
