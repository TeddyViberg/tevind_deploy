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
from . import assets, bench, compose, dns, image, npm_api, sites
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


def reconfigure(
    cfg: Config,
    domain: str | None = None,
    *,
    skip_build: bool = False,
    skip_sync: bool = False,
    skip_dns_apply: bool = False,
    skip_dns_verify: bool = False,
    skip_proxy: bool = False,
    capture: bool = False,
) -> str:
    """Apply config changes end-to-end: build -> up -> site(s) -> assets -> DNS -> proxy.

    Use after editing ``apps:`` / ``sites:`` in YAML (and bumping ``image.custom_tag``
    when the image app list changed). If ``domain`` is set, only that configured site
    is provisioned; otherwise every configured site is passed through ``add_site``.
    """
    out = OutputCollector(capture=capture)
    targets = [cfg.site(domain)] if domain else list(cfg.sites)
    if domain and targets[0] is None:
        raise ValueError(
            f"No site '{domain}' in config. Add it under sites: before reconfigure."
        )
    if not targets:
        raise ValueError("No sites defined in config.")

    if not skip_build:
        out.status("=== Building image ===")
        out.add(image.build_image(cfg, capture=out.capture))

    out.status("=== Starting stack ===")
    out.add(compose.up(cfg, capture=out.capture))

    for site in targets:
        assert site is not None
        out.status(f"=== Provisioning site {site.domain} ===")
        site_out = sites.add_site(cfg, site.domain, capture=out.capture)
        if capture and site_out:
            out.status(site_out)

    if not skip_sync:
        out.status("=== Syncing assets ===")
        sync_out = sync_assets_release(cfg, capture=out.capture)
        if capture and sync_out:
            out.status(sync_out)

    if not skip_dns_apply and cfg.env.get("OVH_APPLICATION_KEY"):
        out.status("=== DNS apply ===")
        out.add(dns.apply_dns(cfg, capture=out.capture))

    if not skip_dns_verify and cfg.server.ip_address:
        out.status("=== DNS verify ===")
        verify_out = dns.verify_dns(cfg, capture=out.capture)
        if capture and verify_out:
            out.status(verify_out)

    if not skip_proxy and cfg.proxy.enabled:
        out.status("=== Proxy apply ===")
        proxy_out = npm_api.apply_proxy_hosts(cfg, capture=out.capture)
        if capture and proxy_out:
            out.status(proxy_out)

    out.status("Reconfigure complete.")
    return out.text()
