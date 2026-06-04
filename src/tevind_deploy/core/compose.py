"""docker compose wrappers, parameterised from the YAML config.

Replaces the hardcoded ``docker compose -f ...`` arrays in the old bash
scripts. The compose project name comes from config (``-p``) and all
``${VAR}`` substitutions are fed via the process environment from
``Config.compose_env()`` so the compose YAML files stay generic.
"""

from __future__ import annotations

import os
from typing import Optional, Sequence

from ..config import Config
from .runner import Result, run

STACK_FILES = [
    "compose/compose.yaml",
    "compose/compose.mariadb.yaml",
    "compose/compose.redis.yaml",
]
NPM_FILES = ["compose/compose.npm.yaml"]


def _base_cmd(cfg: Config, files: Sequence[str]) -> list[str]:
    cmd = ["docker", "compose", "--project-directory", str(cfg.root)]
    if cfg.env_file.exists():
        cmd += ["--env-file", str(cfg.env_file)]
    cmd += ["-p", cfg.project.compose_project_name]
    for f in files:
        cmd += ["-f", str(cfg.root / f)]
    return cmd


def _env(cfg: Config) -> dict[str, str]:
    merged = dict(os.environ)
    merged.update(cfg.compose_env())
    return merged


def compose(
    cfg: Config,
    args: Sequence[str],
    files: Optional[Sequence[str]] = None,
    capture: bool = False,
    check: bool = True,
) -> Result:
    cmd = _base_cmd(cfg, files or STACK_FILES) + list(args)
    return run(cmd, env=_env(cfg), capture=capture, check=check)


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------
def up(cfg: Config, capture: bool = False) -> Result:
    return compose(cfg, ["up", "-d"], capture=capture)


def down(cfg: Config, remove_volumes: bool = False, capture: bool = False) -> Result:
    args = ["down"]
    if remove_volumes:
        args.append("-v")
    return compose(cfg, args, capture=capture)


def ps(cfg: Config, capture: bool = False) -> Result:
    return compose(cfg, ["ps"], capture=capture)


def logs(cfg: Config, follow: bool = True, tail: int = 200, capture: bool = False) -> Result:
    args = ["logs", "--tail", str(tail)]
    if follow:
        args.append("-f")
    return compose(cfg, args, capture=capture)


def restart(cfg: Config, services: Sequence[str], capture: bool = False) -> Result:
    return compose(cfg, ["restart", *services], capture=capture)


# ---------------------------------------------------------------------------
# exec helpers
# ---------------------------------------------------------------------------
def exec_service(
    cfg: Config,
    service: str,
    command: Sequence[str],
    capture: bool = False,
    check: bool = True,
) -> Result:
    return compose(cfg, ["exec", "-T", service, *command], capture=capture, check=check)


def exec_backend(
    cfg: Config,
    command: Sequence[str],
    capture: bool = False,
    check: bool = True,
) -> Result:
    return exec_service(cfg, "backend", command, capture=capture, check=check)


# ---------------------------------------------------------------------------
# NPM stack
# ---------------------------------------------------------------------------
def npm_up(cfg: Config, capture: bool = False) -> Result:
    return compose(cfg, ["up", "-d"], files=NPM_FILES, capture=capture)


def npm_down(cfg: Config, capture: bool = False) -> Result:
    return compose(cfg, ["down"], files=NPM_FILES, capture=capture)


def npm_ps(cfg: Config, capture: bool = False) -> Result:
    return compose(cfg, ["ps"], files=NPM_FILES, capture=capture)


def npm_logs(cfg: Config, follow: bool = True, tail: int = 200, capture: bool = False) -> Result:
    args = ["logs", "--tail", str(tail)]
    if follow:
        args.append("-f")
    return compose(cfg, args, files=NPM_FILES, capture=capture)
