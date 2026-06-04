"""Asset sync + redis-cache flush.

Ports ``sync-assets-from-image.sh`` and ``flush-redis-cache.sh``. The asset
snapshot baked into the image (``/opt/frappe-asset-snapshot``) is rsynced into
the live ``sites/assets`` directory; afterwards only redis-cache is flushed
(never redis-queue) so login/desk HTML stops referencing stale bundle hashes.
"""

from __future__ import annotations

from ..config import Config
from . import compose
from .runner import Result


def sync_assets(cfg: Config, capture: bool = False) -> Result:
    snap = cfg.deploy.asset_snapshot_path
    dest = cfg.deploy.sites_assets_path
    script = (
        "set -euo pipefail\n"
        f"mkdir -p '{dest}'\n"
        f"rsync -a --delete '{snap}/' '{dest}/'\n"
    )
    return compose.exec_backend(cfg, ["bash", "-c", script], capture=capture)


def flush_redis_cache(cfg: Config, capture: bool = False) -> Result:
    # FLUSHDB on redis-cache only (not redis-queue).
    return compose.exec_service(
        cfg, "redis-cache", ["redis-cli", "FLUSHDB"], capture=capture
    )
