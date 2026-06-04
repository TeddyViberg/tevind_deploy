"""Host dependency checks/installs used by the setup flow.

Verifies the tools the deploy stack needs (docker engine, the compose plugin,
git, rsync). ``install_missing`` makes a best-effort attempt to install them on
Debian/Ubuntu; anything requiring elevated privileges is reported with guidance
rather than failing hard.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass
class DepStatus:
    name: str
    present: bool
    version: str = ""
    hint: str = ""


def _cmd_version(args: list[str]) -> Optional[str]:
    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or proc.stderr).strip().splitlines()[0] if (proc.stdout or proc.stderr) else ""


def check() -> list[DepStatus]:
    statuses: list[DepStatus] = []

    docker = shutil.which("docker")
    docker_version = _cmd_version(["docker", "--version"]) if docker else None
    statuses.append(
        DepStatus(
            name="docker",
            present=docker is not None and docker_version is not None,
            version=docker_version or "",
            hint="Install: curl -fsSL https://get.docker.com | sh",
        )
    )

    compose_version = _cmd_version(["docker", "compose", "version"]) if docker else None
    statuses.append(
        DepStatus(
            name="docker compose plugin",
            present=compose_version is not None,
            version=compose_version or "",
            hint="Install the docker-compose-plugin package.",
        )
    )

    for tool, install_hint in (
        ("git", "apt-get install -y git"),
        ("rsync", "apt-get install -y rsync"),
        ("ssh-keyscan", "apt-get install -y openssh-client"),
    ):
        path = shutil.which(tool)
        statuses.append(
            DepStatus(
                name=tool,
                present=path is not None,
                version=_cmd_version([tool, "--version"]) or "" if path else "",
                hint=install_hint,
            )
        )
    return statuses


def all_present(statuses: Optional[list[DepStatus]] = None) -> bool:
    statuses = statuses or check()
    return all(s.present for s in statuses)


def install_missing(statuses: Optional[list[DepStatus]] = None) -> list[str]:
    """Best-effort install of apt-available tools. Returns log messages."""
    statuses = statuses or check()
    messages: list[str] = []

    apt = shutil.which("apt-get")
    sudo = shutil.which("sudo")

    def apt_install(packages: list[str]) -> None:
        if not apt:
            messages.append(f"apt-get not found; install manually: {' '.join(packages)}")
            return
        prefix = [sudo] if sudo else []
        subprocess.run(prefix + [apt, "update"], check=False)
        rc = subprocess.run(
            prefix + [apt, "install", "-y", *packages], check=False
        ).returncode
        if rc == 0:
            messages.append(f"Installed: {' '.join(packages)}")
        else:
            messages.append(f"Failed to install (try manually): {' '.join(packages)}")

    apt_packages: list[str] = []
    for s in statuses:
        if s.present:
            continue
        if s.name == "git":
            apt_packages.append("git")
        elif s.name == "rsync":
            apt_packages.append("rsync")
        elif s.name == "ssh-keyscan":
            apt_packages.append("openssh-client")
        elif s.name == "docker":
            messages.append(
                "Docker engine missing. Install with: "
                "curl -fsSL https://get.docker.com | sh "
                "(then add your user to the 'docker' group and re-login)."
            )
        elif s.name == "docker compose plugin":
            messages.append(
                "Docker compose plugin missing. Install the docker-compose-plugin "
                "package for your distro."
            )

    if apt_packages:
        apt_install(apt_packages)

    return messages
