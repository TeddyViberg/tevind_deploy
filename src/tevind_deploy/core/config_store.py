"""Programmatic CRUD for ``tevind_deploy.yaml``.

Agents and the CLI use these functions instead of hand-editing YAML. Each
resource group exposes ``get``, ``set``, ``add``, and ``remove`` (where
applicable). Changes are validated with pydantic and persisted on ``save()``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

import yaml

from ..config import (
    DEFAULT_CONFIG_FILENAME,
    AppConfig,
    Config,
    ProxyHostConfig,
    SSHKeyConfig,
    SiteConfig,
)

# Sections that support scalar field get/set via dotted keys (e.g. server.ip_address).
SCALAR_SECTIONS = ("project", "server", "image", "stack", "deploy", "dns", "proxy")


class ConfigStore:
    """Mutable view over a loaded :class:`Config` with YAML persistence."""

    def __init__(self, cfg: Config, config_path: Path):
        self.cfg = cfg
        self.config_path = config_path

    @classmethod
    def load(
        cls,
        root: Union[str, Path],
        config_path: Optional[Union[str, Path]] = None,
        env_path: Optional[Union[str, Path]] = None,
    ) -> "ConfigStore":
        cfg = Config.load(root, config_path, env_path)
        path = Path(config_path) if config_path else Path(root).resolve() / DEFAULT_CONFIG_FILENAME
        return cls(cfg, path)

    def save(self) -> None:
        """Write the in-memory config back to the YAML file."""
        Config.model_validate(self.cfg.model_dump(mode="json", exclude={"root", "env"}))
        data = self.cfg.model_dump(mode="json", exclude={"root", "env"})
        self.config_path.write_text(yaml.safe_dump(data, sort_keys=False))

    def _validate_app_names(self, app_names: list[str], context: str) -> None:
        known = {a.name for a in self.cfg.apps}
        unknown = [n for n in app_names if n not in known]
        if unknown:
            raise ValueError(
                f"{context}: unknown app(s) {unknown}. Add them under apps: first "
                f"(known: {sorted(known) or '(none)'})."
            )

    def _validate_deploy_key(self, deploy_key: Optional[str]) -> None:
        if deploy_key and not self.cfg.key_by_name(deploy_key):
            raise ValueError(
                f"Unknown deploy_key '{deploy_key}'. Add it under ssh_keys: first."
            )

    # ------------------------------------------------------------------
    # Apps (image bake list)
    # ------------------------------------------------------------------
    def apps_list(self) -> list[dict[str, Any]]:
        return [a.model_dump(mode="json") for a in self.cfg.apps]

    def apps_get(self, name: str) -> dict[str, Any]:
        app = self._app(name)
        return app.model_dump(mode="json")

    def apps_add(
        self,
        name: str,
        url: str,
        branch: str = "version-16",
        deploy_key: Optional[str] = None,
    ) -> dict[str, Any]:
        if self._find_app_index(name) is not None:
            raise ValueError(f"App '{name}' already exists. Use apps_set to update.")
        self._validate_deploy_key(deploy_key)
        app = AppConfig(name=name, url=url, branch=branch, deploy_key=deploy_key)
        self.cfg.apps.append(app)
        return app.model_dump(mode="json")

    def apps_set(
        self,
        name: str,
        *,
        url: Optional[str] = None,
        branch: Optional[str] = None,
        deploy_key: Optional[str] = None,
        clear_deploy_key: bool = False,
    ) -> dict[str, Any]:
        app = self._app(name)
        if url is not None:
            app.url = url
        if branch is not None:
            app.branch = branch
        if clear_deploy_key:
            app.deploy_key = None
        elif deploy_key is not None:
            self._validate_deploy_key(deploy_key)
            app.deploy_key = deploy_key
        return app.model_dump(mode="json")

    def apps_remove(self, name: str) -> dict[str, Any]:
        idx = self._app_index(name)
        removed = self.cfg.apps.pop(idx)
        for site in self.cfg.sites:
            site.apps = [a for a in site.apps if a != name]
        return removed.model_dump(mode="json")

    def _find_app_index(self, name: str) -> Optional[int]:
        for i, a in enumerate(self.cfg.apps):
            if a.name == name:
                return i
        return None

    def _app_index(self, name: str) -> int:
        idx = self._find_app_index(name)
        if idx is None:
            raise KeyError(f"App not found: {name}")
        return idx

    def _app(self, name: str) -> AppConfig:
        return self.cfg.apps[self._app_index(name)]

    # ------------------------------------------------------------------
    # Sites
    # ------------------------------------------------------------------
    def sites_list(self) -> list[dict[str, Any]]:
        return [s.model_dump(mode="json") for s in self.cfg.sites]

    def sites_get(self, domain: str) -> dict[str, Any]:
        return self._site(domain).model_dump(mode="json")

    def sites_add(self, domain: str, apps: Optional[list[str]] = None) -> dict[str, Any]:
        if self.cfg.site(domain):
            raise ValueError(f"Site '{domain}' already exists. Use sites_set to update.")
        app_list = list(apps or [])
        self._validate_app_names(app_list, f"site '{domain}'")
        site = SiteConfig(domain=domain, apps=app_list)
        self.cfg.sites.append(site)
        return site.model_dump(mode="json")

    def sites_set(
        self,
        domain: str,
        *,
        apps: Optional[list[str]] = None,
        new_domain: Optional[str] = None,
    ) -> dict[str, Any]:
        site = self._site(domain)
        if apps is not None:
            self._validate_app_names(apps, f"site '{domain}'")
            site.apps = list(apps)
        if new_domain is not None and new_domain != domain:
            if self.cfg.site(new_domain):
                raise ValueError(f"Site '{new_domain}' already exists.")
            site.domain = new_domain
            for host in self.cfg.proxy.hosts:
                if host.domain == domain:
                    host.domain = new_domain
        return site.model_dump(mode="json")

    def sites_remove(self, domain: str) -> dict[str, Any]:
        idx = self._site_index(domain)
        removed = self.cfg.sites.pop(idx)
        self.cfg.proxy.hosts = [h for h in self.cfg.proxy.hosts if h.domain != domain]
        return removed.model_dump(mode="json")

    def _find_site_index(self, domain: str) -> Optional[int]:
        for i, s in enumerate(self.cfg.sites):
            if s.domain == domain:
                return i
        return None

    def _site_index(self, domain: str) -> int:
        idx = self._find_site_index(domain)
        if idx is None:
            raise KeyError(f"Site not found: {domain}")
        return idx

    def _site(self, domain: str) -> SiteConfig:
        site = self.cfg.site(domain)
        if site is None:
            raise KeyError(f"Site not found: {domain}")
        return site

    # ------------------------------------------------------------------
    # Site apps (per-site install list)
    # ------------------------------------------------------------------
    def site_apps_get(self, domain: str) -> list[str]:
        return list(self._site(domain).apps)

    def site_apps_add(self, domain: str, app_name: str) -> list[str]:
        self._validate_app_names([app_name], f"site '{domain}'")
        site = self._site(domain)
        if app_name in site.apps:
            raise ValueError(f"App '{app_name}' already on site '{domain}'.")
        site.apps.append(app_name)
        return list(site.apps)

    def site_apps_remove(self, domain: str, app_name: str) -> list[str]:
        site = self._site(domain)
        if app_name not in site.apps:
            raise KeyError(f"App '{app_name}' not installed on site '{domain}' in config.")
        site.apps.remove(app_name)
        return list(site.apps)

    def site_apps_set(self, domain: str, apps: list[str]) -> list[str]:
        self._validate_app_names(apps, f"site '{domain}'")
        self._site(domain).apps = list(apps)
        return list(self._site(domain).apps)

    # ------------------------------------------------------------------
    # SSH keys (YAML metadata; key file on disk is separate ``keys add``)
    # ------------------------------------------------------------------
    def ssh_keys_list(self) -> list[dict[str, Any]]:
        return [k.model_dump(mode="json") for k in self.cfg.ssh_keys]

    def ssh_keys_get(self, name: str) -> dict[str, Any]:
        return self._ssh_key(name).model_dump(mode="json")

    def ssh_keys_add(
        self,
        name: str,
        path: str = "",
        host_alias: Optional[str] = None,
        hostname: str = "github.com",
    ) -> dict[str, Any]:
        if self.cfg.key_by_name(name):
            raise ValueError(f"SSH key '{name}' already exists. Use ssh_keys_set to update.")
        key = SSHKeyConfig(name=name, path=path, host_alias=host_alias, hostname=hostname)
        self.cfg.ssh_keys.append(key)
        return key.model_dump(mode="json")

    def ssh_keys_set(
        self,
        name: str,
        *,
        path: Optional[str] = None,
        host_alias: Optional[str] = None,
        hostname: Optional[str] = None,
        clear_host_alias: bool = False,
    ) -> dict[str, Any]:
        key = self._ssh_key(name)
        if path is not None:
            key.path = path
        if clear_host_alias:
            key.host_alias = None
        elif host_alias is not None:
            key.host_alias = host_alias
        if hostname is not None:
            key.hostname = hostname
        return key.model_dump(mode="json")

    def ssh_keys_remove(self, name: str) -> dict[str, Any]:
        idx = self._ssh_key_index(name)
        removed = self.cfg.ssh_keys.pop(idx)
        for app in self.cfg.apps:
            if app.deploy_key == name:
                app.deploy_key = None
        return removed.model_dump(mode="json")

    def _ssh_key_index(self, name: str) -> int:
        for i, k in enumerate(self.cfg.ssh_keys):
            if k.name == name:
                return i
        raise KeyError(f"SSH key not found: {name}")

    def _ssh_key(self, name: str) -> SSHKeyConfig:
        key = self.cfg.key_by_name(name)
        if key is None:
            raise KeyError(f"SSH key not found: {name}")
        return key

    # ------------------------------------------------------------------
    # Proxy host overrides (proxy.hosts)
    # ------------------------------------------------------------------
    def proxy_hosts_list(self) -> list[dict[str, Any]]:
        return [h.model_dump(mode="json") for h in self.cfg.proxy.hosts]

    def proxy_hosts_get(self, domain: str) -> dict[str, Any]:
        return self._proxy_host(domain).model_dump(mode="json")

    def proxy_hosts_add(
        self,
        domain: str,
        forward_host: str = "",
        forward_port: int = 0,
    ) -> dict[str, Any]:
        if self._find_proxy_host_index(domain) is not None:
            raise ValueError(f"Proxy host '{domain}' already exists. Use proxy_hosts_set.")
        host = ProxyHostConfig(
            domain=domain, forward_host=forward_host, forward_port=forward_port
        )
        self.cfg.proxy.hosts.append(host)
        return host.model_dump(mode="json")

    def proxy_hosts_set(
        self,
        domain: str,
        *,
        forward_host: Optional[str] = None,
        forward_port: Optional[int] = None,
        new_domain: Optional[str] = None,
    ) -> dict[str, Any]:
        host = self._proxy_host(domain)
        if forward_host is not None:
            host.forward_host = forward_host
        if forward_port is not None:
            host.forward_port = forward_port
        if new_domain is not None and new_domain != domain:
            if self._find_proxy_host_index(new_domain) is not None:
                raise ValueError(f"Proxy host '{new_domain}' already exists.")
            host.domain = new_domain
        return host.model_dump(mode="json")

    def proxy_hosts_remove(self, domain: str) -> dict[str, Any]:
        idx = self._proxy_host_index(domain)
        return self.cfg.proxy.hosts.pop(idx).model_dump(mode="json")

    def _find_proxy_host_index(self, domain: str) -> Optional[int]:
        for i, h in enumerate(self.cfg.proxy.hosts):
            if h.domain == domain:
                return i
        return None

    def _proxy_host_index(self, domain: str) -> int:
        idx = self._find_proxy_host_index(domain)
        if idx is None:
            raise KeyError(f"Proxy host not found: {domain}")
        return idx

    def _proxy_host(self, domain: str) -> ProxyHostConfig:
        return self.cfg.proxy.hosts[self._proxy_host_index(domain)]

    # ------------------------------------------------------------------
    # Scalar sections (project, server, image, …)
    # ------------------------------------------------------------------
    def section_get(self, section: str, field: Optional[str] = None) -> Any:
        if section not in SCALAR_SECTIONS:
            raise ValueError(f"Unknown section '{section}'. Use one of: {SCALAR_SECTIONS}")
        obj = getattr(self.cfg, section)
        if field is None:
            return obj.model_dump(mode="json")
        if not hasattr(obj, field):
            raise KeyError(f"No field '{field}' on section '{section}'")
        return getattr(obj, field)

    def section_set(self, section: str, field: str, value: Any) -> Any:
        if section not in SCALAR_SECTIONS:
            raise ValueError(f"Unknown section '{section}'. Use one of: {SCALAR_SECTIONS}")
        obj = getattr(self.cfg, section)
        if field not in type(obj).model_fields:
            raise KeyError(f"No field '{field}' on section '{section}'")
        setattr(obj, field, value)
        return getattr(obj, field)

    def section_set_dotted(self, key: str, value: Any) -> Any:
        """Set ``section.field`` (e.g. ``image.custom_tag``)."""
        parts = key.split(".")
        if len(parts) != 2 or parts[0] not in SCALAR_SECTIONS:
            raise ValueError(
                f"Expected section.field (one of {SCALAR_SECTIONS}), got '{key}'"
            )
        return self.section_set(parts[0], parts[1], value)
