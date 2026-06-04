"""Full host provisioning for an Ubuntu 24.04 OVH VPS.

Brings a fresh server to a state where the Frappe stack can run, entirely over
SSH and idempotently:

* install base apt packages,
* install Docker Engine + compose plugin (get.docker.com) and add the user to
  the ``docker`` group,
* configure UFW (allow the configured ports, enable),
* create a swapfile and set swappiness.

Privileged commands are prefixed with ``sudo`` unless already root (and
``server.use_sudo`` is honoured). After the user is added to the ``docker``
group a new login is required before ``docker`` works without sudo; this is
reported and detectable via :func:`docker_accessible`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Sequence

from ..config import Config
from . import deps
from .runner import OutputCollector, run

SWAPFILE = "/swapfile"


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _sudo_prefix(cfg: Config) -> list[str]:
    if _is_root() or not cfg.server.use_sudo:
        return []
    sudo = shutil.which("sudo")
    if not sudo:
        return []
    # Non-interactive so we fail fast instead of hanging on a password prompt.
    return [sudo, "-n"]


def _priv(cfg: Config, args: Sequence[str], capture: bool, check: bool = True):
    return run(_sudo_prefix(cfg) + list(args), capture=capture, check=check)


def docker_accessible() -> bool:
    """True if the current user can talk to the Docker daemon without sudo."""
    if not shutil.which("docker"):
        return False
    proc = subprocess.run(
        ["docker", "info"], capture_output=True, text=True, check=False
    )
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
def install_base_packages(cfg: Config, out: OutputCollector) -> None:
    packages = cfg.server.base_packages
    if not packages:
        return
    out.status(f"Installing base packages: {', '.join(packages)}")
    out.add(_priv(cfg, ["apt-get", "update"], capture=out.capture, check=False))
    out.add(
        _priv(
            cfg,
            ["apt-get", "install", "-y", *packages],
            capture=out.capture,
            check=False,
        )
    )


def install_docker(cfg: Config, out: OutputCollector) -> None:
    if shutil.which("docker"):
        out.status("Docker already installed, skipping.")
    else:
        out.status("Installing Docker via get.docker.com ...")
        script = Path(cfg.root) / ".generated" / "get-docker.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        out.add(run(["curl", "-fsSL", "https://get.docker.com", "-o", str(script)],
                    capture=out.capture))
        out.add(_priv(cfg, ["sh", str(script)], capture=out.capture))

    # Ensure the deploy user can use docker without sudo (needs re-login).
    user = cfg.server.ssh_user or os.environ.get("USER", "")
    if user and not _is_root():
        out.status(f"Adding user '{user}' to the docker group ...")
        out.add(_priv(cfg, ["usermod", "-aG", "docker", user],
                      capture=out.capture, check=False))


def configure_ufw(cfg: Config, out: OutputCollector) -> None:
    if not shutil.which("ufw") and not Path("/usr/sbin/ufw").exists():
        out.status("ufw not installed, skipping firewall configuration.")
        return
    for port in cfg.server.ufw_allow_ports:
        out.status(f"ufw allow {port}")
        out.add(_priv(cfg, ["ufw", "allow", f"{port}"], capture=out.capture, check=False))
    out.status("Enabling ufw ...")
    out.add(_priv(cfg, ["ufw", "--force", "enable"], capture=out.capture, check=False))


def _swap_active(cfg: Config) -> bool:
    proc = subprocess.run(["swapon", "--show"], capture_output=True, text=True, check=False)
    return bool(proc.stdout.strip())


def configure_swap(cfg: Config, out: OutputCollector) -> None:
    size_gb = cfg.server.swap_size_gb
    if size_gb <= 0:
        out.status("swap_size_gb <= 0, skipping swap.")
        return
    if _swap_active(cfg) or Path(SWAPFILE).exists():
        out.status("Swap already present, skipping creation.")
    else:
        out.status(f"Creating {size_gb}G swapfile at {SWAPFILE} ...")
        # fallocate is fast; mkswap/swapon enable it.
        out.add(_priv(cfg, ["fallocate", "-l", f"{size_gb}G", SWAPFILE],
                      capture=out.capture, check=False))
        out.add(_priv(cfg, ["chmod", "600", SWAPFILE], capture=out.capture, check=False))
        out.add(_priv(cfg, ["mkswap", SWAPFILE], capture=out.capture, check=False))
        out.add(_priv(cfg, ["swapon", SWAPFILE], capture=out.capture, check=False))
        # Persist in /etc/fstab (idempotent: only if not already there).
        fstab_line = f"{SWAPFILE} none swap sw 0 0"
        out.add(
            _priv(
                cfg,
                ["bash", "-c", f"grep -qF '{SWAPFILE}' /etc/fstab || echo '{fstab_line}' >> /etc/fstab"],
                capture=out.capture,
                check=False,
            )
        )
    out.status(f"Setting vm.swappiness={cfg.server.swappiness}")
    out.add(_priv(cfg, ["sysctl", "-w", f"vm.swappiness={cfg.server.swappiness}"],
                  capture=out.capture, check=False))


def provision(cfg: Config, capture: bool = False) -> str:
    out = OutputCollector(capture=capture)
    out.status("=== Provisioning host ===")
    install_base_packages(cfg, out)
    install_docker(cfg, out)
    configure_ufw(cfg, out)
    configure_swap(cfg, out)

    out.status("=== Dependency check ===")
    for s in deps.check():
        out.status(f"  [{'ok' if s.present else 'MISSING'}] {s.name} {s.version}")

    if not docker_accessible():
        out.status(
            "NOTE: docker is not usable by the current user yet. "
            "Reconnect via SSH (new session) so the 'docker' group applies, "
            "then continue."
        )
    out.status("Provisioning complete.")
    return out.text()
