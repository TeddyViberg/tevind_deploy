"""Frappe ``bench`` wrappers executed inside the backend container.

Ports the ``docker compose exec -T backend bench ...`` calls scattered across
the old bash scripts into reusable functions.
"""

from __future__ import annotations

from typing import Sequence

from ..config import Config
from . import compose
from .runner import Result


def bench(
    cfg: Config,
    site: str,
    args: Sequence[str],
    capture: bool = False,
    check: bool = True,
) -> Result:
    return compose.exec_backend(
        cfg, ["bench", "--site", site, *args], capture=capture, check=check
    )


def site_exists(cfg: Config, site: str) -> bool:
    result = compose.exec_backend(
        cfg, ["test", "-d", f"sites/{site}"], capture=True, check=False
    )
    return result.returncode == 0


def list_sites(cfg: Config, capture: bool = False) -> Result:
    # A site directory is one that contains a site_config.json.
    script = (
        'for d in sites/*/; do '
        '[ -f "$d/site_config.json" ] && basename "$d"; '
        'done'
    )
    return compose.exec_backend(cfg, ["bash", "-lc", script], capture=capture, check=False)


def list_apps(cfg: Config, site: str, capture: bool = False) -> Result:
    return bench(cfg, site, ["list-apps"], capture=capture, check=False)


def installed_apps(cfg: Config, site: str) -> list[str]:
    result = bench(cfg, site, ["list-apps"], capture=True, check=False)
    apps: list[str] = []
    for line in result.output.splitlines():
        token = line.strip().split()
        if token:
            apps.append(token[0])
    return apps


def new_site(cfg: Config, site: str, capture: bool = False) -> Result:
    db_root = cfg.require_secret("DB_ROOT_PASSWORD")
    admin = cfg.require_secret("SITE_ADMIN_PASSWORD")
    return compose.exec_backend(
        cfg,
        [
            "bench",
            "new-site",
            site,
            "--mariadb-root-password",
            db_root,
            "--admin-password",
            admin,
            "--no-mariadb-socket",
        ],
        capture=capture,
    )


def install_app(cfg: Config, site: str, app: str, capture: bool = False) -> Result:
    return bench(cfg, site, ["install-app", app], capture=capture)


def migrate(cfg: Config, site: str, capture: bool = False) -> Result:
    return bench(cfg, site, ["migrate"], capture=capture)


def clear_cache(cfg: Config, site: str, capture: bool = False) -> Result:
    return bench(cfg, site, ["clear-cache"], capture=capture, check=False)


def clear_website_cache(cfg: Config, site: str, capture: bool = False) -> Result:
    return bench(cfg, site, ["clear-website-cache"], capture=capture, check=False)
