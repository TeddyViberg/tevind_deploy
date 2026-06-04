"""Nginx Proxy Manager automation via its REST API.

Logs into the NPM admin API (running on the host, default port 81), then
creates/updates one proxy host per domain forwarding to the Frappe frontend,
with websocket upgrade enabled and a Let's Encrypt certificate (forcing SSL).

Admin credentials come from .env: ``NPM_ADMIN_EMAIL`` / ``NPM_ADMIN_PASSWORD``.
The NPM stack itself is started separately via ``npm up`` (compose).
"""

from __future__ import annotations

from typing import Any, Optional

from ..config import Config, ProxyHostConfig
from .runner import OutputCollector


def _httpx():
    try:
        import httpx  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The 'httpx' package is required for NPM automation. "
            "Install it (pip install httpx) or re-run tevind_deploy_setup.py."
        ) from exc
    return httpx


class NPMClient:
    def __init__(self, cfg: Config):
        httpx = _httpx()
        self._base = f"http://127.0.0.1:{cfg.stack.npm_admin_port}/api"
        self._email = cfg.require_secret("NPM_ADMIN_EMAIL")
        self._password = cfg.require_secret("NPM_ADMIN_PASSWORD")
        self._client = httpx.Client(base_url=self._base, timeout=120.0)
        self._token: Optional[str] = None

    def __enter__(self) -> "NPMClient":
        self.login()
        return self

    def __exit__(self, *exc) -> None:
        self._client.close()

    # -- low level -------------------------------------------------------
    def login(self) -> None:
        resp = self._client.post(
            "/tokens", json={"identity": self._email, "secret": self._password}
        )
        resp.raise_for_status()
        self._token = resp.json()["token"]
        self._client.headers["Authorization"] = f"Bearer {self._token}"

    def _get(self, path: str) -> Any:
        resp = self._client.get(path)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> Any:
        resp = self._client.post(path, json=payload)
        resp.raise_for_status()
        return resp.json()

    def _put(self, path: str, payload: dict) -> Any:
        resp = self._client.put(path, json=payload)
        resp.raise_for_status()
        return resp.json()

    # -- resources -------------------------------------------------------
    def proxy_hosts(self) -> list[dict]:
        return self._get("/nginx/proxy-hosts")

    def certificates(self) -> list[dict]:
        return self._get("/nginx/certificates")

    def find_host(self, domain: str) -> Optional[dict]:
        for host in self.proxy_hosts():
            if domain in host.get("domain_names", []):
                return host
        return None

    def find_certificate(self, domain: str) -> Optional[dict]:
        for cert in self.certificates():
            if domain in cert.get("domain_names", []):
                return cert
        return None

    def request_letsencrypt(self, domain: str, email: str) -> dict:
        return self._post(
            "/nginx/certificates",
            {
                "domain_names": [domain],
                "meta": {
                    "letsencrypt_email": email,
                    "letsencrypt_agree": True,
                    "dns_challenge": False,
                },
                "provider": "letsencrypt",
            },
        )


def _host_payload(cfg: Config, host: ProxyHostConfig) -> dict:
    return {
        "domain_names": [host.domain],
        "forward_scheme": "http",
        "forward_host": host.forward_host,
        "forward_port": host.forward_port,
        "caching_enabled": False,
        "block_exploits": cfg.proxy.block_exploits,
        "allow_websocket_upgrade": cfg.proxy.websockets,
        "access_list_id": 0,
        "certificate_id": 0,
        "ssl_forced": False,
        "http2_support": False,
        "hsts_enabled": False,
        "hsts_subdomains": False,
        "meta": {"letsencrypt_agree": False, "dns_challenge": False},
        "advanced_config": "",
        "locations": [],
    }


def apply_proxy_hosts(cfg: Config, capture: bool = False) -> str:
    out = OutputCollector(capture=capture)
    if not cfg.proxy.enabled:
        out.status("proxy.enabled is false; skipping NPM proxy host configuration.")
        return out.text()

    hosts = cfg.proxy_hosts()
    if not hosts:
        out.status("No proxy hosts to configure.")
        return out.text()
    for h in hosts:
        if not h.forward_host:
            raise ValueError(
                f"No forward_host for {h.domain}; set server.ip_address or proxy.forward_host."
            )

    with NPMClient(cfg) as npm:
        for host in hosts:
            existing = npm.find_host(host.domain)
            payload = _host_payload(cfg, host)
            if existing:
                host_id = existing["id"]
                # Preserve any certificate already attached.
                payload["certificate_id"] = existing.get("certificate_id", 0) or 0
                payload["ssl_forced"] = bool(existing.get("ssl_forced", False))
                payload["http2_support"] = bool(existing.get("http2_support", False))
                payload["hsts_enabled"] = bool(existing.get("hsts_enabled", False))
                npm._put(f"/nginx/proxy-hosts/{host_id}", payload)
                out.status(f"Updated proxy host {host.domain} -> {host.forward_host}:{host.forward_port}")
            else:
                created = npm._post("/nginx/proxy-hosts", payload)
                host_id = created["id"]
                out.status(f"Created proxy host {host.domain} -> {host.forward_host}:{host.forward_port}")

            # TLS: request/attach a Let's Encrypt certificate.
            if cfg.proxy.letsencrypt_email:
                cert = npm.find_certificate(host.domain)
                if cert is None:
                    try:
                        cert = npm.request_letsencrypt(host.domain, cfg.proxy.letsencrypt_email)
                        out.status(f"Requested Let's Encrypt certificate for {host.domain}")
                    except Exception as exc:  # noqa: BLE001 - report and continue
                        out.status(
                            f"WARNING: certificate request failed for {host.domain}: {exc}. "
                            f"Ensure DNS resolves and port 80 is reachable, then re-run."
                        )
                        cert = None
                if cert is not None:
                    ssl_payload = dict(_host_payload(cfg, host))
                    ssl_payload["certificate_id"] = cert["id"]
                    ssl_payload["ssl_forced"] = cfg.proxy.force_ssl
                    ssl_payload["http2_support"] = cfg.proxy.http2
                    ssl_payload["hsts_enabled"] = cfg.proxy.hsts
                    npm._put(f"/nginx/proxy-hosts/{host_id}", ssl_payload)
                    out.status(f"Attached certificate + SSL settings to {host.domain}")

    out.status("Proxy host configuration complete.")
    return out.text()


def list_hosts(cfg: Config, capture: bool = False) -> str:
    out = OutputCollector(capture=capture)
    with NPMClient(cfg) as npm:
        hosts = npm.proxy_hosts()
    if not hosts:
        out.status("No proxy hosts configured in NPM.")
        return out.text()
    for h in hosts:
        domains = ", ".join(h.get("domain_names", []))
        ssl = "ssl" if h.get("certificate_id") else "no-ssl"
        out.status(
            f"{domains} -> {h.get('forward_scheme')}://{h.get('forward_host')}:{h.get('forward_port')} "
            f"[{ssl}, ws={h.get('allow_websocket_upgrade')}]"
        )
    return out.text()
