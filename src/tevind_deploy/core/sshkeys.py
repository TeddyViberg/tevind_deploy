"""Manage git deploy keys on the host.

The description requires that missing SSH keys can be retrieved over SSH: the
user (or agent) provides the key content and ``add_key`` writes it to disk with
strict permissions and updates known_hosts. ``check_keys`` reports which
configured keys are still missing so a caller knows what to supply.
"""

from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import Config, SSHKeyConfig


@dataclass
class KeyStatus:
    name: str
    path: str
    exists: bool
    host_alias: Optional[str]
    hostname: str


def list_keys(cfg: Config) -> list[KeyStatus]:
    statuses: list[KeyStatus] = []
    for key in cfg.ssh_keys:
        p = key.resolved_path()
        statuses.append(
            KeyStatus(
                name=key.name,
                path=str(p),
                exists=p.exists(),
                host_alias=key.host_alias,
                hostname=key.hostname,
            )
        )
    return statuses


def check_keys(cfg: Config) -> list[KeyStatus]:
    """Return only the keys that are missing on disk."""
    return [s for s in list_keys(cfg) if not s.exists]


def _ssh_dir() -> Path:
    d = Path(os.path.expanduser("~/.ssh"))
    d.mkdir(mode=0o700, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d


def ensure_known_hosts(hostname: str, known_hosts_path: Optional[Path] = None) -> bool:
    """Append the host key for ``hostname`` to known_hosts if not present."""
    kh = known_hosts_path or (_ssh_dir() / "known_hosts")
    existing = kh.read_text() if kh.exists() else ""
    if hostname in existing:
        return False
    scan = subprocess.run(
        ["ssh-keyscan", hostname], capture_output=True, text=True, check=False
    )
    if scan.returncode != 0 or not scan.stdout.strip():
        return False
    with kh.open("a") as fh:
        fh.write(scan.stdout)
    kh.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return True


def add_key(cfg: Config, name: str, content: str) -> str:
    """Write a deploy key to disk (chmod 600) and refresh known_hosts.

    If ``name`` is not in the config, the key is written to ``~/.ssh/<name>``.
    """
    key = cfg.key_by_name(name)
    if key is None:
        key = SSHKeyConfig(name=name)
    path = key.resolved_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

    body = content if content.endswith("\n") else content + "\n"
    path.write_text(body)
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600

    ensure_known_hosts(key.hostname)
    return str(path)
