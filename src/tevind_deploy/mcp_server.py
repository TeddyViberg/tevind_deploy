"""MCP server exposing tevind_deploy operations as tools.

Lets an agent on another app drive deployments over SSH. Each tool is a thin
wrapper around :mod:`tevind_deploy.core` (the same code the CLI uses) and runs
in capture mode so the combined command output is returned to the caller.

Start with: ``tevind-deploy mcp serve``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

from .config import Config
from .core import bench, compose, deploy, dns, image, npm_api, provision, sites, sshkeys
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


def _guard(fn):
    """Turn core exceptions into readable text instead of crashing the server."""
    try:
        return fn()
    except (CommandError, FileNotFoundError, ValueError) as exc:
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
def list_apps(site: str) -> str:
    """List apps installed on a running site."""
    return _guard(lambda: bench.list_apps(_cfg(), site, capture=True).output or "(none)")


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
