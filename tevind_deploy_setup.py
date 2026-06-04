#!/usr/bin/env python3
"""Bootstrap tevind_deploy on a fresh server.

Run with the system Python right after pulling this folder:

    python3 tevind_deploy_setup.py

It:
  1. verifies the Python version,
  2. creates a local virtualenv (.venv),
  3. installs this package into it (pip install -e .),
  4. makes the ./tevind-deploy wrapper executable,
  5. runs a host-dependency check (docker, compose plugin, git, rsync).

It deliberately uses only the standard library so it can run before anything
is installed.
"""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
MIN_PYTHON = (3, 10)


def info(msg: str) -> None:
    print(f"[setup] {msg}", flush=True)


def fail(msg: str) -> "None":
    print(f"[setup] ERROR: {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        fail(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, "
            f"found {sys.version.split()[0]}."
        )
    info(f"Python {sys.version.split()[0]} OK")


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def create_venv() -> None:
    if venv_python().exists():
        info(f"Virtualenv already exists at {VENV_DIR}")
        return
    info(f"Creating virtualenv at {VENV_DIR}")
    venv.create(VENV_DIR, with_pip=True)


def pip_install() -> None:
    py = str(venv_python())
    info("Upgrading pip")
    subprocess.run([py, "-m", "pip", "install", "--upgrade", "pip"], check=True)
    info("Installing tevind-deploy (editable)")
    subprocess.run([py, "-m", "pip", "install", "-e", "."], cwd=str(ROOT), check=True)


def make_wrapper_executable() -> None:
    wrapper = ROOT / "tevind-deploy"
    if wrapper.exists():
        mode = wrapper.stat().st_mode
        wrapper.chmod(mode | 0o111)
        info("Made ./tevind-deploy executable")


def run_dependency_check() -> None:
    info("Checking host dependencies")
    cli = VENV_DIR / ("Scripts" if os.name == "nt" else "bin") / "tevind-deploy"
    rc = subprocess.run([str(cli), "setup"], cwd=str(ROOT)).returncode
    if rc != 0:
        info(
            "Some host dependencies are missing (see above). "
            "Re-run with: ./tevind-deploy setup --install"
        )


def main() -> None:
    check_python()
    create_venv()
    pip_install()
    make_wrapper_executable()
    run_dependency_check()
    info("Done. Next steps:")
    info("  1. cp tevind_deploy.example.yaml tevind_deploy.yaml  # then edit")
    info("  2. cp .env.example .env                              # set secrets")
    info("  3. ./tevind-deploy doctor")
    info("  4. ./tevind-deploy keys check                        # add missing keys")


if __name__ == "__main__":
    main()
