"""Configuration model for tevind_deploy.

The YAML file (``tevind_deploy.yaml``) is the source of truth for the server,
image build, stack wiring, apps and per-site app mapping. Secrets (DB / admin
passwords) live in ``.env`` so they are never committed.

This replaces the old frappe_deploy split of ``apps.json`` + hardcoded site
mapping in ``bootstrap-sites.sh`` / ``add-site.sh``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field

DEFAULT_CONFIG_FILENAME = "tevind_deploy.yaml"
DEFAULT_ENV_FILENAME = ".env"


class ProjectConfig(BaseModel):
    compose_project_name: str = "tevind"
    project_path_on_host: str = "/home/ubuntu/tevind_deploy"
    frontend_port: int = 8080


class ImageConfig(BaseModel):
    custom_image: str
    custom_tag: str
    mariadb_image: str = "mariadb:11.8"
    pull_policy: str = "if_not_present"
    restart_policy: str = "unless-stopped"
    frappe_docker_repo: str = "https://github.com/frappe/frappe_docker.git"
    frappe_docker_ref: str = "v2.2.0"
    frappe_path: str = "https://github.com/frappe/frappe.git"
    frappe_branch: str = "version-16"
    python_version: str = "3.14.2"
    node_version: str = "24.13.0"
    # Containerfile template, relative to the repo root.
    containerfile: str = "containerfiles/Containerfile.custom.ssh"
    force_no_cache: bool = True
    force_pull: bool = True


class StackConfig(BaseModel):
    db_host: str = "db"
    db_port: int = 3306
    redis_cache: str = "redis-cache:6379"
    redis_queue: str = "redis-queue:6379"
    frappe_site_name_header: str = "$host"
    upstream_real_ip_address: str = "127.0.0.1"
    upstream_real_ip_header: str = "X-Forwarded-For"
    upstream_real_ip_recursive: str = "off"
    proxy_read_timeout: int = 120
    client_max_body_size: str = "50m"
    tz: str = "Europe/Stockholm"
    npm_admin_port: int = 81


class DeployConfig(BaseModel):
    auto_up: bool = True
    auto_migrate: bool = True
    auto_sync_assets: bool = True
    auto_flush_redis_cache_after_sync: bool = True
    auto_restart_after_assets: bool = True
    restart_services: list[str] = Field(
        default_factory=lambda: ["backend", "frontend", "websocket"]
    )
    asset_snapshot_path: str = "/opt/frappe-asset-snapshot"
    sites_assets_path: str = "/home/frappe/frappe-bench/sites/assets"


class SSHKeyConfig(BaseModel):
    """A git deploy key used at image-build time to clone a private repo."""

    name: str
    path: str = ""
    host_alias: Optional[str] = None
    hostname: str = "github.com"

    def resolved_path(self) -> Path:
        raw = self.path or f"~/.ssh/{self.name}"
        return Path(os.path.expanduser(raw))


class AppConfig(BaseModel):
    """An app baked into the image.

    ``name`` is the Frappe app name used by ``bench install-app`` (which may
    differ from the repo name). ``url`` / ``branch`` are written to apps.json.
    """

    name: str
    url: str
    branch: str = "version-16"
    deploy_key: Optional[str] = None


class SiteConfig(BaseModel):
    domain: str
    apps: list[str] = Field(default_factory=list)


class ServerConfig(BaseModel):
    """The target VPS. Used for provisioning, DNS A records and NPM upstream."""

    ip_address: str = ""
    ipv6_address: str = ""
    ssh_user: str = "ubuntu"
    os_version: str = "Ubuntu 24.04"
    provider: str = "OVH"
    use_sudo: bool = True
    swap_size_gb: int = 2
    swappiness: int = 10
    ufw_allow_ports: list[int] = Field(default_factory=lambda: [22, 80, 443])
    base_packages: list[str] = Field(
        default_factory=lambda: [
            "ca-certificates",
            "curl",
            "git",
            "rsync",
            "openssh-client",
            "ufw",
            "jq",
        ]
    )


class DNSConfig(BaseModel):
    provider: str = "ovh"
    endpoint: str = "ovh-eu"
    ttl: int = 3600
    manage_ipv6: bool = False
    # Explicit zones; if empty they are auto-detected from OVH.
    zones: list[str] = Field(default_factory=list)


class ProxyHostConfig(BaseModel):
    """Optional per-domain override; otherwise hosts derive from sites."""

    domain: str
    forward_host: str = ""
    forward_port: int = 0


class ProxyConfig(BaseModel):
    enabled: bool = True
    letsencrypt_email: str = ""
    force_ssl: bool = True
    http2: bool = True
    hsts: bool = False
    websockets: bool = True
    block_exploits: bool = True
    forward_host: str = ""  # default: server.ip_address
    forward_port: int = 0  # default: project.frontend_port
    hosts: list[ProxyHostConfig] = Field(default_factory=list)


class Config(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    image: ImageConfig
    stack: StackConfig = Field(default_factory=StackConfig)
    deploy: DeployConfig = Field(default_factory=DeployConfig)
    dns: DNSConfig = Field(default_factory=DNSConfig)
    proxy: ProxyConfig = Field(default_factory=ProxyConfig)
    ssh_keys: list[SSHKeyConfig] = Field(default_factory=list)
    apps: list[AppConfig] = Field(default_factory=list)
    sites: list[SiteConfig] = Field(default_factory=list)

    # Runtime-only fields (not part of the YAML schema).
    root: Path = Field(default_factory=Path.cwd, exclude=True)
    env: dict[str, str] = Field(default_factory=dict, exclude=True)

    model_config = {"arbitrary_types_allowed": True}

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    @classmethod
    def load(
        cls,
        root: os.PathLike | str,
        config_path: Optional[os.PathLike | str] = None,
        env_path: Optional[os.PathLike | str] = None,
    ) -> "Config":
        root_path = Path(root).resolve()
        cfg_file = Path(config_path) if config_path else root_path / DEFAULT_CONFIG_FILENAME
        if not cfg_file.exists():
            raise FileNotFoundError(
                f"Config file not found: {cfg_file}. "
                f"Copy tevind_deploy.example.yaml to {DEFAULT_CONFIG_FILENAME} first."
            )
        data = yaml.safe_load(cfg_file.read_text()) or {}

        env_file = Path(env_path) if env_path else root_path / DEFAULT_ENV_FILENAME
        env = parse_env_file(env_file) if env_file.exists() else {}

        cfg = cls(**data)
        cfg.root = root_path
        cfg.env = env
        return cfg

    # ------------------------------------------------------------------
    # Derived values
    # ------------------------------------------------------------------
    @property
    def env_file(self) -> Path:
        return self.root / DEFAULT_ENV_FILENAME

    @property
    def apps_json_path(self) -> Path:
        return self.root / "apps.json"

    def compose_env(self) -> dict[str, str]:
        """Substitution variables for ``docker compose`` (replaces the old .env)."""
        e: dict[str, str] = {
            "CUSTOM_IMAGE": self.image.custom_image,
            "CUSTOM_TAG": self.image.custom_tag,
            "MARIADB_IMAGE": self.image.mariadb_image,
            "PULL_POLICY": self.image.pull_policy,
            "RESTART_POLICY": self.image.restart_policy,
            "DB_HOST": self.stack.db_host,
            "DB_PORT": str(self.stack.db_port),
            "REDIS_CACHE": self.stack.redis_cache,
            "REDIS_QUEUE": self.stack.redis_queue,
            "FRONTEND_PORT": str(self.project.frontend_port),
            "FRAPPE_SITE_NAME_HEADER": self.stack.frappe_site_name_header,
            "UPSTREAM_REAL_IP_ADDRESS": self.stack.upstream_real_ip_address,
            "UPSTREAM_REAL_IP_HEADER": self.stack.upstream_real_ip_header,
            "UPSTREAM_REAL_IP_RECURSIVE": self.stack.upstream_real_ip_recursive,
            "PROXY_READ_TIMEOUT": str(self.stack.proxy_read_timeout),
            "CLIENT_MAX_BODY_SIZE": self.stack.client_max_body_size,
            "TZ": self.stack.tz,
            "NPM_ADMIN_PORT": str(self.stack.npm_admin_port),
        }
        # Secrets come from .env and must be present for compose substitution.
        for key in ("DB_ROOT_PASSWORD", "SITE_ADMIN_PASSWORD"):
            if self.env.get(key):
                e[key] = self.env[key]
        return e

    def render_apps_json(self) -> str:
        items = [{"url": app.url, "branch": app.branch} for app in self.apps]
        return json.dumps(items, indent=2) + "\n"

    def site(self, domain: str) -> Optional[SiteConfig]:
        for s in self.sites:
            if s.domain == domain:
                return s
        return None

    def key_by_name(self, name: str) -> Optional[SSHKeyConfig]:
        for k in self.ssh_keys:
            if k.name == name:
                return k
        return None

    def require_secret(self, key: str) -> str:
        value = self.env.get(key)
        if not value:
            raise ValueError(f"{key} is not set in {self.env_file}")
        return value

    def domains(self) -> list[str]:
        """All public domains (one per site)."""
        return [s.domain for s in self.sites]

    def proxy_hosts(self) -> list[ProxyHostConfig]:
        """Resolved proxy hosts: explicit overrides, else one per site domain.

        ``forward_host``/``forward_port`` fall back to the server IP and the
        project frontend port.
        """
        default_host = self.proxy.forward_host or self.server.ip_address
        default_port = self.proxy.forward_port or self.project.frontend_port

        configured = {h.domain: h for h in self.proxy.hosts}
        result: list[ProxyHostConfig] = []
        domains = list(configured.keys())
        for d in self.domains():
            if d not in configured:
                domains.append(d)
        for domain in domains:
            override = configured.get(domain)
            result.append(
                ProxyHostConfig(
                    domain=domain,
                    forward_host=(override.forward_host if override and override.forward_host else default_host),
                    forward_port=(override.forward_port if override and override.forward_port else default_port),
                )
            )
        return result


def parse_env_file(path: os.PathLike | str) -> dict[str, str]:
    """Minimal ``KEY=VALUE`` .env parser (ignores comments/blank lines)."""
    result: dict[str, str] = {}
    for raw_line in Path(path).read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        result[key] = value
    return result
