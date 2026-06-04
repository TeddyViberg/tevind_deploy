"""Unified Typer CLI: the SSH-callable endpoints for tevind_deploy.

Every command is a thin wrapper around :mod:`tevind_deploy.core`; the MCP server
wraps the same core functions so the two never diverge.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Optional

import typer
import yaml

from .config import Config
from .core import bench, compose, deploy, deps, image, npm_api, sites, sshkeys
from .core import dns as dns_mod
from .core import provision as provision_mod
from .core.runner import CommandError

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Manage Frappe Docker deployments on an OVH VPS over SSH.",
)

config_app = typer.Typer(no_args_is_help=True, help="Inspect/modify the YAML config.")
keys_app = typer.Typer(no_args_is_help=True, help="Manage git deploy keys.")
sites_app = typer.Typer(no_args_is_help=True, help="Site provisioning.")
apps_app = typer.Typer(no_args_is_help=True, help="Per-site Frappe app operations.")
npm_app = typer.Typer(no_args_is_help=True, help="Nginx Proxy Manager stack.")
dns_app = typer.Typer(no_args_is_help=True, help="OVH DNS records.")
proxy_app = typer.Typer(no_args_is_help=True, help="NPM proxy hosts (REST API).")
mcp_app = typer.Typer(no_args_is_help=True, help="MCP server.")

app.add_typer(config_app, name="config")
app.add_typer(keys_app, name="keys")
app.add_typer(sites_app, name="sites")
app.add_typer(apps_app, name="apps")
app.add_typer(npm_app, name="npm")
app.add_typer(dns_app, name="dns")
app.add_typer(proxy_app, name="proxy")
app.add_typer(mcp_app, name="mcp")


# ---------------------------------------------------------------------------
# Shared state + helpers
# ---------------------------------------------------------------------------
@app.callback()
def _main(
    ctx: typer.Context,
    root: Optional[Path] = typer.Option(
        None, "--root", help="Repo root (default: current directory)."
    ),
    config: Optional[Path] = typer.Option(
        None, "--config", help="Path to tevind_deploy.yaml."
    ),
    env: Optional[Path] = typer.Option(None, "--env", help="Path to .env."),
) -> None:
    ctx.obj = {"root": root or Path.cwd(), "config": config, "env": env}


def _load(ctx: typer.Context) -> Config:
    o = ctx.obj or {}
    return Config.load(o["root"], o.get("config"), o.get("env"))


@contextlib.contextmanager
def _handle_errors():
    try:
        yield
    except (CommandError, FileNotFoundError, ValueError) as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Setup / diagnostics
# ---------------------------------------------------------------------------
@app.command()
def setup(
    install: bool = typer.Option(
        False, "--install", help="Attempt to install missing host dependencies."
    ),
) -> None:
    """Check (and optionally install) host dependencies."""
    statuses = deps.check()
    for s in statuses:
        mark = "ok " if s.present else "MISSING"
        line = f"[{mark}] {s.name}"
        if s.version:
            line += f" ({s.version})"
        if not s.present and s.hint:
            line += f"  -> {s.hint}"
        typer.echo(line)
    if install:
        for msg in deps.install_missing(statuses):
            typer.echo(msg)
    if not deps.all_present():
        raise typer.Exit(1)


@app.command()
def provision(ctx: typer.Context) -> None:
    """Provision the host from 0: base packages, Docker, UFW, swap (uses sudo)."""
    with _handle_errors():
        provision_mod.provision(_load(ctx))


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Validate config + dependencies + deploy keys and report findings."""
    typer.secho("Dependencies:", bold=True)
    for s in deps.check():
        typer.echo(f"  [{'ok' if s.present else 'MISSING'}] {s.name} {s.version}")

    typer.secho("Config:", bold=True)
    with _handle_errors():
        cfg = _load(ctx)
    typer.echo(f"  project: {cfg.project.compose_project_name}")
    typer.echo(f"  image:   {cfg.image.custom_image}:{cfg.image.custom_tag}")
    typer.echo(f"  apps:    {', '.join(a.name for a in cfg.apps) or '(none)'}")
    typer.echo(f"  sites:   {', '.join(s.domain for s in cfg.sites) or '(none)'}")

    # Validate site app references exist in the image apps.
    app_names = {a.name for a in cfg.apps}
    for site in cfg.sites:
        unknown = [a for a in site.apps if a not in app_names]
        if unknown:
            typer.secho(
                f"  WARNING: site {site.domain} references apps not in image: "
                f"{', '.join(unknown)}",
                fg=typer.colors.YELLOW,
            )

    typer.secho("Deploy keys:", bold=True)
    missing = sshkeys.check_keys(cfg)
    if not missing:
        typer.echo("  all configured keys present")
    for s in missing:
        typer.secho(f"  MISSING: {s.name} ({s.path})", fg=typer.colors.YELLOW)

    for key in ("DB_ROOT_PASSWORD", "SITE_ADMIN_PASSWORD"):
        if not cfg.env.get(key):
            typer.secho(f"  WARNING: {key} not set in .env", fg=typer.colors.YELLOW)


# ---------------------------------------------------------------------------
# Image build
# ---------------------------------------------------------------------------
@app.command()
def build(ctx: typer.Context) -> None:
    """Build the custom Frappe image (= old `make build-image`)."""
    with _handle_errors():
        image.build_image(_load(ctx))


# ---------------------------------------------------------------------------
# Stack lifecycle
# ---------------------------------------------------------------------------
@app.command()
def up(ctx: typer.Context) -> None:
    """Start the Frappe stack."""
    with _handle_errors():
        compose.up(_load(ctx))


@app.command()
def down(
    ctx: typer.Context,
    volumes: bool = typer.Option(
        False, "--volumes", help="DANGER: also remove volumes (wipes DB/sites/redis)."
    ),
) -> None:
    """Stop the Frappe stack."""
    with _handle_errors():
        compose.down(_load(ctx), remove_volumes=volumes)


@app.command()
def ps(ctx: typer.Context) -> None:
    """Show stack service status."""
    with _handle_errors():
        compose.ps(_load(ctx))


@app.command()
def logs(
    ctx: typer.Context,
    follow: bool = typer.Option(True, "--follow/--no-follow"),
    tail: int = typer.Option(200, "--tail"),
) -> None:
    """Follow stack logs."""
    with _handle_errors():
        compose.logs(_load(ctx), follow=follow, tail=tail)


@app.command()
def restart(
    ctx: typer.Context,
    services: Optional[list[str]] = typer.Argument(None),
) -> None:
    """Restart services (default: configured web services)."""
    with _handle_errors():
        cfg = _load(ctx)
        compose.restart(cfg, services or cfg.deploy.restart_services)


# ---------------------------------------------------------------------------
# Release flows
# ---------------------------------------------------------------------------
@app.command()
def migrate(ctx: typer.Context) -> None:
    """Migrate all configured sites (= old `make migrate`)."""
    with _handle_errors():
        sites.migrate_all(_load(ctx))


@app.command(name="deploy-refresh")
def deploy_refresh(ctx: typer.Context) -> None:
    """Build image, then up + migrate + sync-assets (= old `make deploy-refresh`)."""
    with _handle_errors():
        deploy.deploy_refresh(_load(ctx))


@app.command(name="sync-assets")
def sync_assets(ctx: typer.Context) -> None:
    """Re-sync assets from the current image + flush cache + restart web."""
    with _handle_errors():
        deploy.sync_assets_release(_load(ctx))


# ---------------------------------------------------------------------------
# config sub-app
# ---------------------------------------------------------------------------
@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    """Print the effective config (secrets excluded)."""
    with _handle_errors():
        cfg = _load(ctx)
    typer.echo(yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False))


@config_app.command("validate")
def config_validate(ctx: typer.Context) -> None:
    """Validate the config file."""
    with _handle_errors():
        _load(ctx)
    typer.secho("Config is valid.", fg=typer.colors.GREEN)


@config_app.command("set")
def config_set(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="Dotted path, e.g. image.custom_tag"),
    value: str = typer.Argument(...),
) -> None:
    """Set a dotted config key (rewrites YAML; comments are not preserved)."""
    o = ctx.obj or {}
    root = o["root"]
    cfg_file = Path(o["config"]) if o.get("config") else Path(root) / "tevind_deploy.yaml"
    with _handle_errors():
        if not cfg_file.exists():
            raise FileNotFoundError(f"Config file not found: {cfg_file}")
        data = yaml.safe_load(cfg_file.read_text()) or {}
        parsed = yaml.safe_load(value)
        node = data
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
            if not isinstance(node, dict):
                raise ValueError(f"Cannot set '{key}': '{p}' is not a mapping.")
        node[parts[-1]] = parsed
        # Validate before persisting.
        Config(**data)
        cfg_file.write_text(yaml.safe_dump(data, sort_keys=False))
    typer.secho(f"Set {key} = {parsed!r}", fg=typer.colors.GREEN)


# ---------------------------------------------------------------------------
# keys sub-app
# ---------------------------------------------------------------------------
@keys_app.command("list")
def keys_list(ctx: typer.Context) -> None:
    """List configured deploy keys and whether they exist on disk."""
    with _handle_errors():
        cfg = _load(ctx)
    for s in sshkeys.list_keys(cfg):
        mark = "ok " if s.exists else "MISSING"
        alias = f" alias={s.host_alias}" if s.host_alias else ""
        typer.echo(f"[{mark}] {s.name} -> {s.path}{alias}")


@keys_app.command("check")
def keys_check(ctx: typer.Context) -> None:
    """List only the deploy keys that are missing (exit 1 if any)."""
    with _handle_errors():
        cfg = _load(ctx)
    missing = sshkeys.check_keys(cfg)
    if not missing:
        typer.secho("All configured deploy keys are present.", fg=typer.colors.GREEN)
        return
    for s in missing:
        typer.secho(f"MISSING: {s.name} ({s.path})", fg=typer.colors.YELLOW)
    raise typer.Exit(1)


@keys_app.command("add")
def keys_add(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Key name (matches ssh_keys[].name)."),
    from_file: Optional[Path] = typer.Option(
        None, "--from-file", help="Read key from file instead of stdin."
    ),
) -> None:
    """Add/replace a deploy key. Reads PEM content from stdin (or --from-file)."""
    with _handle_errors():
        cfg = _load(ctx)
        if from_file:
            content = Path(from_file).read_text()
        elif not sys.stdin.isatty():
            content = sys.stdin.read()
        else:
            typer.echo("Paste the private key, then press Ctrl-D:")
            content = sys.stdin.read()
        if not content.strip():
            raise ValueError("No key content provided.")
        path = sshkeys.add_key(cfg, name, content)
    typer.secho(f"Wrote key '{name}' to {path} (chmod 600).", fg=typer.colors.GREEN)


# ---------------------------------------------------------------------------
# sites sub-app
# ---------------------------------------------------------------------------
@sites_app.command("list")
def sites_list(ctx: typer.Context) -> None:
    """List sites defined in config (and on the running bench, if up)."""
    with _handle_errors():
        cfg = _load(ctx)
    typer.secho("Configured sites:", bold=True)
    for site in cfg.sites:
        typer.echo(f"  {site.domain}: {', '.join(site.apps) or '(no apps)'}")


@sites_app.command("bootstrap")
def sites_bootstrap(ctx: typer.Context) -> None:
    """Create all configured sites + install their apps (= old `make bootstrap-sites`)."""
    with _handle_errors():
        sites.bootstrap_sites(_load(ctx))


@sites_app.command("add")
def sites_add(
    ctx: typer.Context,
    domain: str = typer.Argument(..., help="Site domain (must exist in config)."),
) -> None:
    """Provision a single configured site (= old `make add-site`)."""
    with _handle_errors():
        sites.add_site(_load(ctx), domain)


@sites_app.command("migrate")
def sites_migrate(ctx: typer.Context) -> None:
    """Migrate all configured sites."""
    with _handle_errors():
        sites.migrate_all(_load(ctx))


# ---------------------------------------------------------------------------
# apps sub-app
# ---------------------------------------------------------------------------
@apps_app.command("list")
def apps_list(
    ctx: typer.Context,
    site: str = typer.Option(..., "--site", help="Site domain."),
) -> None:
    """List apps installed on a running site."""
    with _handle_errors():
        bench.list_apps(_load(ctx), site)


@apps_app.command("install")
def apps_install(
    ctx: typer.Context,
    site: str = typer.Option(..., "--site"),
    app_name: str = typer.Option(..., "--app", help="Frappe app name."),
) -> None:
    """Install an app on a site (app must be in the running image)."""
    with _handle_errors():
        bench.install_app(_load(ctx), site, app_name)


# ---------------------------------------------------------------------------
# npm sub-app
# ---------------------------------------------------------------------------
@npm_app.command("up")
def npm_up(ctx: typer.Context) -> None:
    """Start the Nginx Proxy Manager stack."""
    with _handle_errors():
        compose.npm_up(_load(ctx))


@npm_app.command("down")
def npm_down(ctx: typer.Context) -> None:
    with _handle_errors():
        compose.npm_down(_load(ctx))


@npm_app.command("ps")
def npm_ps(ctx: typer.Context) -> None:
    with _handle_errors():
        compose.npm_ps(_load(ctx))


@npm_app.command("logs")
def npm_logs(
    ctx: typer.Context,
    follow: bool = typer.Option(True, "--follow/--no-follow"),
    tail: int = typer.Option(200, "--tail"),
) -> None:
    with _handle_errors():
        compose.npm_logs(_load(ctx), follow=follow, tail=tail)


# ---------------------------------------------------------------------------
# dns sub-app
# ---------------------------------------------------------------------------
@dns_app.command("apply")
def dns_apply(ctx: typer.Context) -> None:
    """Create/update OVH A (and optional AAAA) records for all site domains."""
    with _handle_errors():
        dns_mod.apply_dns(_load(ctx))


@dns_app.command("verify")
def dns_verify(ctx: typer.Context) -> None:
    """Resolve site domains with dig and compare against server.ip_address."""
    with _handle_errors():
        dns_mod.verify_dns(_load(ctx))


@dns_app.command("list")
def dns_list(ctx: typer.Context) -> None:
    """List the domains that DNS records will be managed for."""
    with _handle_errors():
        cfg = _load(ctx)
    typer.echo(f"server.ip_address: {cfg.server.ip_address or '(unset)'}")
    for domain in cfg.domains():
        typer.echo(f"  {domain}")


# ---------------------------------------------------------------------------
# proxy sub-app (NPM REST API)
# ---------------------------------------------------------------------------
@proxy_app.command("apply")
def proxy_apply(ctx: typer.Context) -> None:
    """Create/update NPM proxy hosts + Let's Encrypt certs for all domains."""
    with _handle_errors():
        npm_api.apply_proxy_hosts(_load(ctx))


@proxy_app.command("list")
def proxy_list(ctx: typer.Context) -> None:
    """List proxy hosts currently configured in NPM."""
    with _handle_errors():
        npm_api.list_hosts(_load(ctx))


# ---------------------------------------------------------------------------
# bootstrap (end-to-end, from 0)
# ---------------------------------------------------------------------------
@app.command()
def bootstrap(
    ctx: typer.Context,
    skip_provision: bool = typer.Option(False, "--skip-provision"),
    skip_build: bool = typer.Option(False, "--skip-build"),
    skip_sites: bool = typer.Option(False, "--skip-sites"),
    skip_npm: bool = typer.Option(False, "--skip-npm"),
    skip_dns: bool = typer.Option(False, "--skip-dns"),
    skip_proxy: bool = typer.Option(False, "--skip-proxy"),
) -> None:
    """Run the full server setup from 0 (provision -> stack -> DNS -> proxy)."""
    with _handle_errors():
        cfg = _load(ctx)

        if not skip_provision:
            typer.secho(">> provision", bold=True)
            provision_mod.provision(cfg)
            if not provision_mod.docker_accessible():
                typer.secho(
                    "Docker is not usable by this user yet. Reconnect via SSH so the "
                    "'docker' group applies, then run: tevind-deploy bootstrap "
                    "--skip-provision",
                    fg=typer.colors.YELLOW,
                )
                raise typer.Exit(2)

        typer.secho(">> keys check", bold=True)
        missing = sshkeys.check_keys(cfg)
        if missing:
            for s in missing:
                typer.secho(f"   MISSING key: {s.name} ({s.path})", fg=typer.colors.YELLOW)
            raise ValueError("Add missing deploy keys (tevind-deploy keys add NAME) and re-run.")

        if not skip_build:
            typer.secho(">> build image", bold=True)
            image.build_image(cfg)

        typer.secho(">> up", bold=True)
        compose.up(cfg)

        if not skip_sites:
            typer.secho(">> sites bootstrap", bold=True)
            sites.bootstrap_sites(cfg)

        if not skip_npm:
            typer.secho(">> npm up", bold=True)
            compose.npm_up(cfg)

        if not skip_dns:
            typer.secho(">> dns apply + verify", bold=True)
            dns_mod.apply_dns(cfg)
            dns_mod.verify_dns(cfg)

        if not skip_proxy:
            typer.secho(">> proxy apply", bold=True)
            npm_api.apply_proxy_hosts(cfg)

        typer.secho("Bootstrap complete.", fg=typer.colors.GREEN)


# ---------------------------------------------------------------------------
# mcp sub-app
# ---------------------------------------------------------------------------
@mcp_app.command("serve")
def mcp_serve(ctx: typer.Context) -> None:
    """Start the MCP server (stdio transport) for agent control over SSH."""
    from . import mcp_server

    o = ctx.obj or {}
    mcp_server.run(root=o["root"], config=o.get("config"), env=o.get("env"))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
