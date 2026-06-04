"""Site provisioning: bootstrap all sites, add a single site, migrate all.

Ports ``bootstrap-sites.sh`` / ``add-site.sh`` / ``migrate-all-sites.sh``, but
the site list and per-site app mapping come from the YAML config instead of
hardcoded bash arrays / ``apps_for_site`` case statements.
"""

from __future__ import annotations

from ..config import Config, SiteConfig
from . import bench
from .runner import OutputCollector


def _ensure_site(cfg: Config, site: SiteConfig, out: OutputCollector) -> None:
    if bench.site_exists(cfg, site.domain):
        out.status(f"Site already present, skipping create: {site.domain}")
        return
    out.status(f"Creating site: {site.domain}")
    out.add(bench.new_site(cfg, site.domain, capture=out.capture))


def _install_apps(cfg: Config, site: SiteConfig, out: OutputCollector) -> None:
    if not site.apps:
        out.status(f"No apps defined for {site.domain}, skipping install.")
    else:
        existing = bench.installed_apps(cfg, site.domain)
        for app in site.apps:
            if app in existing:
                out.status(f"{app} already installed on {site.domain}, skipping.")
                continue
            out.status(f"Installing {app} on {site.domain}")
            out.add(bench.install_app(cfg, site.domain, app, capture=out.capture))
    out.status(f"Running migrate on {site.domain}")
    out.add(bench.migrate(cfg, site.domain, capture=out.capture))


def bootstrap_sites(cfg: Config, capture: bool = False) -> str:
    out = OutputCollector(capture=capture)
    if not cfg.sites:
        out.status("No sites defined in config.")
        return out.text()
    for site in cfg.sites:
        _ensure_site(cfg, site, out)
    for site in cfg.sites:
        _install_apps(cfg, site, out)
    out.status("Site bootstrap complete.")
    return out.text()


def add_site(cfg: Config, domain: str, capture: bool = False) -> str:
    out = OutputCollector(capture=capture)
    site = cfg.site(domain)
    if site is None:
        raise ValueError(
            f"No site '{domain}' defined in config. "
            f"Add it under sites: (with its apps) before provisioning."
        )
    if bench.site_exists(cfg, domain):
        out.status(f"Site already exists: {domain}")
        return out.text()
    _ensure_site(cfg, site, out)
    _install_apps(cfg, site, out)
    out.status(f"Added site: {domain}")
    return out.text()


def migrate_all(cfg: Config, capture: bool = False) -> str:
    out = OutputCollector(capture=capture)
    for site in cfg.sites:
        out.status(f"Migrating {site.domain}...")
        out.add(bench.migrate(cfg, site.domain, capture=capture))
    out.status("All site migrations complete.")
    return out.text()
