"""CLI commands for programmatic config CRUD (``config apps|sites|...``)."""

from __future__ import annotations

import json
from typing import Optional

import typer
import yaml

from .core.config_store import ConfigStore, SCALAR_SECTIONS

cfg_apps = typer.Typer(no_args_is_help=True, help="Apps baked into the image (apps:).")
cfg_sites = typer.Typer(no_args_is_help=True, help="Frappe sites (sites:).")
cfg_site_apps = typer.Typer(no_args_is_help=True, help="Per-site install-app list.")
cfg_ssh_keys = typer.Typer(
    no_args_is_help=True,
    help="Deploy key metadata in YAML (use top-level ``keys`` for key files on disk).",
)
cfg_proxy_hosts = typer.Typer(no_args_is_help=True, help="NPM proxy host overrides (proxy.hosts).")
cfg_section = typer.Typer(no_args_is_help=True, help="Scalar sections (server, image, dns, …).")


def _store(ctx: typer.Context) -> ConfigStore:
    o = ctx.obj or {}
    return ConfigStore.load(o["root"], o.get("config"), o.get("env"))


def _emit(data: object, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(data, indent=2))
    else:
        typer.echo(yaml.safe_dump(data, sort_keys=False))


def _save(store: ConfigStore, as_json: bool, result: object, label: str) -> None:
    store.save()
    typer.secho(f"{label} (saved to {store.config_path})", fg=typer.colors.GREEN)
    _emit(result, as_json)


def register(parent: typer.Typer) -> None:
    parent.add_typer(cfg_apps, name="apps")
    parent.add_typer(cfg_sites, name="sites")
    parent.add_typer(cfg_site_apps, name="site-apps")
    parent.add_typer(cfg_ssh_keys, name="ssh-keys")
    parent.add_typer(cfg_proxy_hosts, name="proxy-hosts")
    parent.add_typer(cfg_section, name="section")

    # --- apps ---
    @cfg_apps.command("list")
    def apps_list(ctx: typer.Context, json_out: bool = typer.Option(False, "--json")) -> None:
        _emit(_store(ctx).apps_list(), json_out)

    @cfg_apps.command("get")
    def apps_get(
        ctx: typer.Context,
        name: str = typer.Argument(...),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        _emit(_store(ctx).apps_get(name), json_out)

    @cfg_apps.command("add")
    def apps_add(
        ctx: typer.Context,
        name: str = typer.Argument(..., help="Frappe app name (install-app)."),
        url: str = typer.Option(..., "--url"),
        branch: str = typer.Option("version-16", "--branch"),
        deploy_key: Optional[str] = typer.Option(None, "--deploy-key"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        result = store.apps_add(name, url, branch, deploy_key)
        _save(store, json_out, result, f"Added app '{name}'")

    @cfg_apps.command("set")
    def apps_set(
        ctx: typer.Context,
        name: str = typer.Argument(...),
        url: Optional[str] = typer.Option(None, "--url"),
        branch: Optional[str] = typer.Option(None, "--branch"),
        deploy_key: Optional[str] = typer.Option(None, "--deploy-key"),
        clear_deploy_key: bool = typer.Option(False, "--clear-deploy-key"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        result = store.apps_set(
            name, url=url, branch=branch, deploy_key=deploy_key, clear_deploy_key=clear_deploy_key
        )
        _save(store, json_out, result, f"Updated app '{name}'")

    @cfg_apps.command("remove")
    def apps_remove(
        ctx: typer.Context,
        name: str = typer.Argument(...),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        result = store.apps_remove(name)
        _save(store, json_out, result, f"Removed app '{name}'")

    # --- sites ---
    @cfg_sites.command("list")
    def sites_list(ctx: typer.Context, json_out: bool = typer.Option(False, "--json")) -> None:
        _emit(_store(ctx).sites_list(), json_out)

    @cfg_sites.command("get")
    def sites_get(
        ctx: typer.Context,
        domain: str = typer.Argument(...),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        _emit(_store(ctx).sites_get(domain), json_out)

    @cfg_sites.command("add")
    def sites_add(
        ctx: typer.Context,
        domain: str = typer.Argument(...),
        apps: Optional[str] = typer.Option(
            None, "--apps", help="Comma-separated Frappe app names."
        ),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        app_list = [a.strip() for a in apps.split(",") if a.strip()] if apps else []
        result = store.sites_add(domain, app_list)
        _save(store, json_out, result, f"Added site '{domain}'")

    @cfg_sites.command("set")
    def sites_set(
        ctx: typer.Context,
        domain: str = typer.Argument(...),
        apps: Optional[str] = typer.Option(None, "--apps", help="Replace app list (comma-separated)."),
        new_domain: Optional[str] = typer.Option(None, "--rename"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        app_list = None
        if apps is not None:
            app_list = [a.strip() for a in apps.split(",") if a.strip()]
        result = store.sites_set(domain, apps=app_list, new_domain=new_domain)
        _save(store, json_out, result, f"Updated site '{domain}'")

    @cfg_sites.command("remove")
    def sites_remove(
        ctx: typer.Context,
        domain: str = typer.Argument(...),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        result = store.sites_remove(domain)
        _save(store, json_out, result, f"Removed site '{domain}'")

    # --- site-apps ---
    @cfg_site_apps.command("list")
    def site_apps_list(
        ctx: typer.Context,
        domain: str = typer.Argument(...),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        _emit(_store(ctx).site_apps_get(domain), json_out)

    @cfg_site_apps.command("get")
    def site_apps_get(
        ctx: typer.Context,
        domain: str = typer.Argument(...),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        _emit(_store(ctx).site_apps_get(domain), json_out)

    @cfg_site_apps.command("add")
    def site_apps_add(
        ctx: typer.Context,
        domain: str = typer.Argument(...),
        app: str = typer.Option(..., "--app"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        result = store.site_apps_add(domain, app)
        _save(store, json_out, result, f"Added app '{app}' to site '{domain}'")

    @cfg_site_apps.command("remove")
    def site_apps_remove(
        ctx: typer.Context,
        domain: str = typer.Argument(...),
        app: str = typer.Option(..., "--app"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        result = store.site_apps_remove(domain, app)
        _save(store, json_out, result, f"Removed app '{app}' from site '{domain}'")

    @cfg_site_apps.command("set")
    def site_apps_set(
        ctx: typer.Context,
        domain: str = typer.Argument(...),
        apps: str = typer.Option(..., "--apps", help="Comma-separated app names (replaces list)."),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        app_list = [a.strip() for a in apps.split(",") if a.strip()]
        result = store.site_apps_set(domain, app_list)
        _save(store, json_out, result, f"Set apps on site '{domain}'")

    # --- ssh-keys (yaml) ---
    @cfg_ssh_keys.command("list")
    def ssh_keys_list(ctx: typer.Context, json_out: bool = typer.Option(False, "--json")) -> None:
        _emit(_store(ctx).ssh_keys_list(), json_out)

    @cfg_ssh_keys.command("get")
    def ssh_keys_get(
        ctx: typer.Context,
        name: str = typer.Argument(...),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        _emit(_store(ctx).ssh_keys_get(name), json_out)

    @cfg_ssh_keys.command("add")
    def ssh_keys_add(
        ctx: typer.Context,
        name: str = typer.Argument(...),
        path: str = typer.Option("", "--path"),
        host_alias: Optional[str] = typer.Option(None, "--host-alias"),
        hostname: str = typer.Option("github.com", "--hostname"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        result = store.ssh_keys_add(name, path, host_alias, hostname)
        _save(store, json_out, result, f"Added ssh_keys entry '{name}'")

    @cfg_ssh_keys.command("set")
    def ssh_keys_set(
        ctx: typer.Context,
        name: str = typer.Argument(...),
        path: Optional[str] = typer.Option(None, "--path"),
        host_alias: Optional[str] = typer.Option(None, "--host-alias"),
        hostname: Optional[str] = typer.Option(None, "--hostname"),
        clear_host_alias: bool = typer.Option(False, "--clear-host-alias"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        result = store.ssh_keys_set(
            name,
            path=path,
            host_alias=host_alias,
            hostname=hostname,
            clear_host_alias=clear_host_alias,
        )
        _save(store, json_out, result, f"Updated ssh_keys entry '{name}'")

    @cfg_ssh_keys.command("remove")
    def ssh_keys_remove(
        ctx: typer.Context,
        name: str = typer.Argument(...),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        result = store.ssh_keys_remove(name)
        _save(store, json_out, result, f"Removed ssh_keys entry '{name}'")

    # --- proxy-hosts ---
    @cfg_proxy_hosts.command("list")
    def proxy_hosts_list(ctx: typer.Context, json_out: bool = typer.Option(False, "--json")) -> None:
        _emit(_store(ctx).proxy_hosts_list(), json_out)

    @cfg_proxy_hosts.command("get")
    def proxy_hosts_get(
        ctx: typer.Context,
        domain: str = typer.Argument(...),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        _emit(_store(ctx).proxy_hosts_get(domain), json_out)

    @cfg_proxy_hosts.command("add")
    def proxy_hosts_add(
        ctx: typer.Context,
        domain: str = typer.Argument(...),
        forward_host: str = typer.Option("", "--forward-host"),
        forward_port: int = typer.Option(0, "--forward-port"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        result = store.proxy_hosts_add(domain, forward_host, forward_port)
        _save(store, json_out, result, f"Added proxy host '{domain}'")

    @cfg_proxy_hosts.command("set")
    def proxy_hosts_set(
        ctx: typer.Context,
        domain: str = typer.Argument(...),
        forward_host: Optional[str] = typer.Option(None, "--forward-host"),
        forward_port: Optional[int] = typer.Option(None, "--forward-port"),
        rename: Optional[str] = typer.Option(None, "--rename"),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        result = store.proxy_hosts_set(
            domain, forward_host=forward_host, forward_port=forward_port, new_domain=rename
        )
        _save(store, json_out, result, f"Updated proxy host '{domain}'")

    @cfg_proxy_hosts.command("remove")
    def proxy_hosts_remove(
        ctx: typer.Context,
        domain: str = typer.Argument(...),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        result = store.proxy_hosts_remove(domain)
        _save(store, json_out, result, f"Removed proxy host '{domain}'")

    # --- section (scalar fields) ---
    @cfg_section.command("list")
    def section_list() -> None:
        typer.echo("Sections: " + ", ".join(SCALAR_SECTIONS))

    @cfg_section.command("get")
    def section_get(
        ctx: typer.Context,
        section: str = typer.Argument(..., help=f"One of: {', '.join(SCALAR_SECTIONS)}"),
        field: Optional[str] = typer.Argument(None),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        _emit(_store(ctx).section_get(section, field), json_out)

    @cfg_section.command("set")
    def section_set(
        ctx: typer.Context,
        section: str = typer.Argument(...),
        field: str = typer.Argument(...),
        value: str = typer.Argument(..., help="YAML value (quoted strings, numbers, booleans)."),
        json_out: bool = typer.Option(False, "--json"),
    ) -> None:
        store = _store(ctx)
        parsed = yaml.safe_load(value)
        result = store.section_set(section, field, parsed)
        _save(store, json_out, {field: result}, f"Set {section}.{field}")
