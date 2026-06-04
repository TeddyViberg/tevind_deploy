"""Release orchestration.

* ``post_deploy_refresh`` ports ``post-deploy-refresh.sh`` (up -> migrate ->
  sync assets -> clear caches -> flush redis-cache -> restart web).
* ``deploy_refresh`` builds the image first, then runs the post-deploy refresh
  (equivalent to ``make deploy-refresh``).
* ``sync_assets_release`` re-syncs assets from the current image without a
  rebuild (equivalent to ``make sync-assets``).
"""

from __future__ import annotations

from ..config import Config
from . import assets, bench, compose, image, sites
from .runner import OutputCollector


def post_deploy_refresh(cfg: Config, capture: bool = False) -> str:
    out = OutputCollector(capture=capture)
    d = cfg.deploy

    if d.auto_up:
        out.status("Starting services...")
        out.add(compose.up(cfg, capture=capture))

    if d.auto_migrate:
        out.status("Running migrations...")
        migrate_output = sites.migrate_all(cfg, capture=capture)
        if capture and migrate_output:
            out.status(migrate_output)

    if d.auto_sync_assets:
        out.status("Syncing assets from image snapshot into sites volume...")
        out.add(assets.sync_assets(cfg, capture=capture))
        out.status("Clearing caches per site...")
        for site in cfg.sites:
            out.add(bench.clear_cache(cfg, site.domain, capture=capture))
            out.add(bench.clear_website_cache(cfg, site.domain, capture=capture))
        if d.auto_flush_redis_cache_after_sync:
            out.status("Flushing redis-cache (FLUSHDB)...")
            out.add(assets.flush_redis_cache(cfg, capture=capture))

    if d.auto_sync_assets and d.auto_restart_after_assets:
        out.status(f"Restarting web services: {', '.join(d.restart_services)}")
        out.add(compose.restart(cfg, d.restart_services, capture=capture))

    out.status("Post-deploy refresh complete.")
    return out.text()


def deploy_refresh(cfg: Config, capture: bool = False) -> str:
    out = OutputCollector(capture=capture)
    out.status("=== Building image ===")
    build_output = image.build_image(cfg, capture=capture)
    if capture and build_output:
        out.status(build_output)
    out.status("=== Applying release ===")
    refresh_output = post_deploy_refresh(cfg, capture=capture)
    if capture and refresh_output:
        out.status(refresh_output)
    return out.text()


def sync_assets_release(cfg: Config, capture: bool = False) -> str:
    out = OutputCollector(capture=capture)
    out.status("Syncing assets from current image...")
    out.add(assets.sync_assets(cfg, capture=capture))
    if cfg.deploy.auto_flush_redis_cache_after_sync:
        out.status("Flushing redis-cache (FLUSHDB)...")
        out.add(assets.flush_redis_cache(cfg, capture=capture))
    out.status(f"Restarting web services: {', '.join(cfg.deploy.restart_services)}")
    out.add(compose.restart(cfg, cfg.deploy.restart_services, capture=capture))
    out.status("Asset sync complete.")
    return out.text()
