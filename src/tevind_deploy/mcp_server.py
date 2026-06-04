"""MCP server exposing tevind_deploy operations as tools.

Lets an agent on another app drive deployments over SSH. Each tool is a thin
wrapper around :mod:`tevind_deploy.core` (the same code the CLI uses) and runs
in capture mode so the combined command output is returned to the caller.

Start with: ``tevind-deploy mcp serve``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .config import Config
from .core import bench, compose, deploy, dns, image, npm_api, preflight, provision, sites, sshkeys
from .core.config_store import ConfigStore
from .core.npm_api import format_http_error
from .core.runner import CommandError

mcp = FastMCP("tevind-deploy")

# Set by run(); the CLI passes the resolved root/config/env paths.
_STATE: dict[str, object] = {"root": Path.cwd(), "config": None, "env": None}


def _cfg() -> Config:
    return Config.load(
        _STATE["root"],  # type: ignore[arg-type]
        _STATE.get("config"),  # type: ignore[arg-type]
        _STATE.get("env"),  # type: ignore[arg-type]
    )


def _store() -> ConfigStore:
    return ConfigStore.load(
        _STATE["root"],  # type: ignore[arg-type]
        _STATE.get("config"),  # type: ignore[arg-type]
        _STATE.get("env"),  # type: ignore[arg-type]
    )


def _j(data: object) -> str:
    return json.dumps(data, indent=2)


def _guard(fn):
    """Turn core exceptions into readable text instead of crashing the server."""
    try:
        return fn()
    except (CommandError, FileNotFoundError, ValueError) as exc:
        return f"ERROR: {exc}"
    except Exception as exc:  # noqa: BLE001 - httpx and other HTTP client errors
        try:
            import httpx  # type: ignore

            if isinstance(exc, httpx.HTTPStatusError):
                return f"ERROR: {format_http_error(exc)}"
        except ImportError:
            pass
        return f"ERROR: {exc}"


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------
@mcp.tool()
def get_config() -> str:
    """Return the effective deployment config (secrets excluded)."""
    def _do() -> str:
        cfg = _cfg()
        lines = [
            f"project: {cfg.project.compose_project_name}",
            f"image: {cfg.image.custom_image}:{cfg.image.custom_tag}",
            f"frappe_branch: {cfg.image.frappe_branch}",
            "apps:",
            *[f"  - {a.name} ({a.url} @ {a.branch})" for a in cfg.apps],
            "sites:",
            *[f"  - {s.domain}: {', '.join(s.apps) or '(none)'}" for s in cfg.sites],
        ]
        return "\n".join(lines)

    return _guard(_do)


@mcp.tool()
def list_sites() -> str:
    """List sites defined in config and (if the stack is up) on the bench."""
    def _do() -> str:
        cfg = _cfg()
        configured = "\n".join(
            f"  {s.domain}: {', '.join(s.apps) or '(none)'}" for s in cfg.sites
        )
        live = bench.list_sites(cfg, capture=True)
        return (
            "Configured sites:\n" + (configured or "  (none)")
            + "\n\nSites on bench:\n" + (live.output.strip() or "  (stack not running?)")
        )

    return _guard(_do)


@mcp.tool()
def list_apps(site: str = "") -> str:
    """List apps installed on a running site (defaults to the first configured site)."""
    def _do() -> str:
        cfg = _cfg()
        domain = site.strip() or (cfg.sites[0].domain if cfg.sites else "")
        if not domain:
            raise ValueError("No site given and no sites in config.")
        return bench.list_apps(cfg, domain, capture=True).output or "(none)"

    return _guard(_do)


@mcp.tool()
def doctor(check_dns: bool = True) -> str:
    """Run pre-flight checks: deps, config, secrets, NPM login, deploy keys, DNS."""
    return _guard(lambda: preflight.run_doctor(_cfg(), check_dns=check_dns))


@mcp.tool()
def check_keys() -> str:
    """Report which configured deploy keys are missing on disk."""
    def _do() -> str:
        missing = sshkeys.check_keys(_cfg())
        if not missing:
            return "All configured deploy keys are present."
        return "Missing keys:\n" + "\n".join(f"  {s.name} ({s.path})" for s in missing)

    return _guard(_do)


@mcp.tool()
def ps() -> str:
    """Show the Frappe stack service status."""
    return _guard(lambda: compose.ps(_cfg(), capture=True).output)


# ---------------------------------------------------------------------------
# Config CRUD (tevind_deploy.yaml via ConfigStore)
# ---------------------------------------------------------------------------
@mcp.tool()
def config_apps_list() -> str:
    """List apps in config (image bake list)."""
    return _guard(lambda: _j(_store().apps_list()))


@mcp.tool()
def config_apps_get(name: str) -> str:
    """Get one app entry by Frappe app name."""
    return _guard(lambda: _j(_store().apps_get(name)))


@mcp.tool()
def config_apps_add(
    name: str,
    url: str,
    branch: str = "version-16",
    deploy_key: str = "",
) -> str:
    """Add an app to config and save. deploy_key empty = public repo."""
    def _do() -> str:
        store = _store()
        dk = deploy_key or None
        result = store.apps_add(name, url, branch, dk)
        store.save()
        return _j(result)
    return _guard(_do)


@mcp.tool()
def config_apps_set(
    name: str,
    url: str = "",
    branch: str = "",
    deploy_key: str = "",
) -> str:
    """Update app fields (omit empty strings to leave unchanged). Saves config."""
    def _do() -> str:
        store = _store()
        result = store.apps_set(
            name,
            url=url or None,
            branch=branch or None,
            deploy_key=deploy_key or None,
        )
        store.save()
        return _j(result)
    return _guard(_do)


@mcp.tool()
def config_apps_remove(name: str) -> str:
    """Remove an app from config (and from all site app lists). Saves config."""
    def _do() -> str:
        store = _store()
        result = store.apps_remove(name)
        store.save()
        return _j(result)
    return _guard(_do)


@mcp.tool()
def config_sites_list() -> str:
    """List sites in config."""
    return _guard(lambda: _j(_store().sites_list()))


@mcp.tool()
def config_sites_get(domain: str) -> str:
    """Get one site entry by domain."""
    return _guard(lambda: _j(_store().sites_get(domain)))


@mcp.tool()
def config_sites_add(domain: str, apps: str = "") -> str:
    """Add a site. apps: comma-separated Frappe app names. Saves config."""
    def _do() -> str:
        store = _store()
        app_list = [a.strip() for a in apps.split(",") if a.strip()] if apps else []
        result = store.sites_add(domain, app_list)
        store.save()
        return _j(result)
    return _guard(_do)


@mcp.tool()
def config_sites_set(domain: str, apps: str = "", rename: str = "") -> str:
    """Update site (apps comma-separated, optional rename). Saves config."""
    def _do() -> str:
        store = _store()
        app_list = None
        if apps:
            app_list = [a.strip() for a in apps.split(",") if a.strip()]
        result = store.sites_set(domain, apps=app_list, new_domain=rename or None)
        store.save()
        return _j(result)
    return _guard(_do)


@mcp.tool()
def config_sites_remove(domain: str) -> str:
    """Remove a site from config. Saves config."""
    def _do() -> str:
        store = _store()
        result = store.sites_remove(domain)
        store.save()
        return _j(result)
    return _guard(_do)


@mcp.tool()
def config_site_apps_get(domain: str) -> str:
    """List apps configured for a site."""
    return _guard(lambda: _j(_store().site_apps_get(domain)))


@mcp.tool()
def config_site_apps_add(domain: str, app: str) -> str:
    """Add one app to a site's install list. Saves config."""
    def _do() -> str:
        store = _store()
        result = store.site_apps_add(domain, app)
        store.save()
        return _j(result)
    return _guard(_do)


@mcp.tool()
def config_site_apps_remove(domain: str, app: str) -> str:
    """Remove one app from a site's install list. Saves config."""
    def _do() -> str:
        store = _store()
        result = store.site_apps_remove(domain, app)
        store.save()
        return _j(result)
    return _guard(_do)


@mcp.tool()
def config_site_apps_set(domain: str, apps: str) -> str:
    """Replace a site's app list (comma-separated). Saves config."""
    def _do() -> str:
        store = _store()
        app_list = [a.strip() for a in apps.split(",") if a.strip()]
        result = store.site_apps_set(domain, app_list)
        store.save()
        return _j(result)
    return _guard(_do)


@mcp.tool()
def config_ssh_keys_list() -> str:
    """List ssh_keys entries in config (not key files on disk)."""
    return _guard(lambda: _j(_store().ssh_keys_list()))


@mcp.tool()
def config_ssh_keys_get(name: str) -> str:
    """Get one ssh_keys config entry."""
    return _guard(lambda: _j(_store().ssh_keys_get(name)))


@mcp.tool()
def config_ssh_keys_add(
    name: str,
    path: str = "",
    host_alias: str = "",
    hostname: str = "github.com",
) -> str:
    """Add deploy key metadata to config. Use add_key for the PEM file. Saves config."""
    def _do() -> str:
        store = _store()
        result = store.ssh_keys_add(
            name, path, host_alias or None, hostname
        )
        store.save()
        return _j(result)
    return _guard(_do)


@mcp.tool()
def config_ssh_keys_set(
    name: str,
    path: str = "",
    host_alias: str = "",
    hostname: str = "",
) -> str:
    """Update ssh_keys entry (empty strings = leave unchanged). Saves config."""
    def _do() -> str:
        store = _store()
        result = store.ssh_keys_set(
            name,
            path=path or None,
            host_alias=host_alias or None,
            hostname=hostname or None,
        )
        store.save()
        return _j(result)
    return _guard(_do)


@mcp.tool()
def config_ssh_keys_remove(name: str) -> str:
    """Remove ssh_keys entry from config. Saves config."""
    def _do() -> str:
        store = _store()
        result = store.ssh_keys_remove(name)
        store.save()
        return _j(result)
    return _guard(_do)


@mcp.tool()
def config_section_set(section: str, field: str, value: str) -> str:
    """Set a scalar field (server.ip_address, image.custom_tag, proxy.enabled, …). value is YAML."""
    def _do() -> str:
        import yaml

        store = _store()
        parsed = yaml.safe_load(value)
        result = store.section_set(section, field, parsed)
        store.save()
        return _j({f"{section}.{field}": result})
    return _guard(_do)


@mcp.tool()
def config_section_get(section: str, field: str = "") -> str:
    """Get a config section or one field (project, server, image, stack, deploy, dns, proxy)."""
    return _guard(
        lambda: _j(_store().section_get(section, field or None))
    )


# ---------------------------------------------------------------------------
# Mutating tools
# ---------------------------------------------------------------------------
@mcp.tool()
def add_key(name: str, content: str) -> str:
    """Write a git deploy key to disk (chmod 600) and refresh known_hosts.

    Use this to supply a key the server is missing (see check_keys).
    """
    return _guard(lambda: f"Wrote key '{name}' to {sshkeys.add_key(_cfg(), name, content)}")


@mcp.tool()
def build_image() -> str:
    """Build the custom Frappe image from the config."""
    return _guard(lambda: image.build_image(_cfg(), capture=True))


@mcp.tool()
def deploy_refresh() -> str:
    """Build image, then up + migrate + sync assets (full release)."""
    return _guard(lambda: deploy.deploy_refresh(_cfg(), capture=True))


@mcp.tool()
def sync_assets() -> str:
    """Re-sync assets from the current image + flush redis-cache + restart web."""
    return _guard(lambda: deploy.sync_assets_release(_cfg(), capture=True))


@mcp.tool()
def bootstrap_sites() -> str:
    """Create all configured sites + install their apps."""
    return _guard(lambda: sites.bootstrap_sites(_cfg(), capture=True))


@mcp.tool()
def add_site(domain: str) -> str:
    """Provision a single configured site (create + install apps + migrate)."""
    return _guard(lambda: sites.add_site(_cfg(), domain, capture=True))


@mcp.tool()
def remove_site(domain: str, no_backup: bool = True) -> str:
    """Drop a site from the bench (non-interactive). Does not remove config YAML."""
    return _guard(
        lambda: sites.remove_site(_cfg(), domain, no_backup=no_backup, capture=True)
    )


@mcp.tool()
def reconfigure(
    domain: str = "",
    skip_build: bool = False,
    skip_sync: bool = False,
    skip_dns_apply: bool = False,
    skip_dns_verify: bool = False,
    skip_proxy: bool = False,
) -> str:
    """Apply config changes: build -> up -> site(s) -> sync assets -> DNS -> proxy."""
    return _guard(
        lambda: deploy.reconfigure(
            _cfg(),
            domain.strip() or None,
            skip_build=skip_build,
            skip_sync=skip_sync,
            skip_dns_apply=skip_dns_apply,
            skip_dns_verify=skip_dns_verify,
            skip_proxy=skip_proxy,
            capture=True,
        )
    )


@mcp.tool()
def migrate_all() -> str:
    """Migrate all configured sites."""
    return _guard(lambda: sites.migrate_all(_cfg(), capture=True))


@mcp.tool()
def stack_up() -> str:
    """Start the Frappe stack."""
    return _guard(lambda: (compose.up(_cfg(), capture=True).output or "Stack started."))


@mcp.tool()
def stack_down() -> str:
    """Stop the Frappe stack (volumes are preserved)."""
    return _guard(lambda: (compose.down(_cfg(), capture=True).output or "Stack stopped."))


@mcp.tool()
def provision_host() -> str:
    """Provision the host from 0: base packages, Docker, UFW, swap (uses sudo)."""
    return _guard(lambda: provision.provision(_cfg(), capture=True))


@mcp.tool()
def dns_apply() -> str:
    """Create/update OVH A (and optional AAAA) records for all site domains."""
    return _guard(lambda: dns.apply_dns(_cfg(), capture=True))


@mcp.tool()
def dns_verify() -> str:
    """Resolve site domains and compare against the configured server IP."""
    return _guard(lambda: dns.verify_dns(_cfg(), capture=True))


@mcp.tool()
def proxy_apply() -> str:
    """Create/update NPM proxy hosts + Let's Encrypt certs for all domains."""
    return _guard(lambda: npm_api.apply_proxy_hosts(_cfg(), capture=True))


@mcp.tool()
def list_proxy_hosts() -> str:
    """List proxy hosts currently configured in NPM."""
    return _guard(lambda: npm_api.list_hosts(_cfg(), capture=True))


@mcp.tool()
def bootstrap_all() -> str:
    """Full setup from 0: provision -> build -> up -> sites -> npm -> dns -> proxy.

    Note: if provisioning adds the user to the docker group, a new SSH session is
    required before docker works; in that case this returns early and you should
    reconnect and call the remaining steps.
    """
    def _do() -> str:
        cfg = _cfg()
        parts: list[str] = []
        parts.append(provision.provision(cfg, capture=True))
        if not provision.docker_accessible():
            parts.append(
                "Docker not usable yet (group change). Reconnect via SSH, then call "
                "build_image, stack_up, bootstrap_sites, dns_apply, proxy_apply."
            )
            return "\n".join(parts)
        missing = sshkeys.check_keys(cfg)
        if missing:
            names = ", ".join(s.name for s in missing)
            parts.append(f"Missing deploy keys: {names}. Use add_key, then retry.")
            return "\n".join(parts)
        parts.append(image.build_image(cfg, capture=True))
        parts.append(compose.up(cfg, capture=True).output or "stack up")
        parts.append(sites.bootstrap_sites(cfg, capture=True))
        parts.append(compose.npm_up(cfg, capture=True).output or "npm up")
        parts.append(dns.apply_dns(cfg, capture=True))
        parts.append(dns.verify_dns(cfg, capture=True))
        parts.append(npm_api.apply_proxy_hosts(cfg, capture=True))
        parts.append("Bootstrap complete.")
        return "\n".join(p for p in parts if p)

    return _guard(_do)


def run(
    root: Path | str,
    config: Optional[Path | str] = None,
    env: Optional[Path | str] = None,
) -> None:
    _STATE["root"] = Path(root)
    _STATE["config"] = config
    _STATE["env"] = env
    mcp.run()


if __name__ == "__main__":
    run(Path.cwd())
