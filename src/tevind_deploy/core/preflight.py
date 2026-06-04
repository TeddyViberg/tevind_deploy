"""Pre-flight checks for ``doctor`` (CLI + MCP).

Aggregates dependency, config, secret, deploy-key, NPM, OVH, and DNS checks
into a single human-readable report for agents and operators.
"""

from __future__ import annotations

from ..config import Config
from . import bench, deps, dns, npm_api, sshkeys


def _line(status: str, message: str) -> str:
    return f"[{status}] {message}"


def run_doctor(cfg: Config, *, check_dns: bool = True) -> str:
    lines: list[str] = []

    lines.append("Dependencies:")
    for s in deps.check():
        mark = "ok" if s.present else "MISSING"
        detail = f"{s.name} {s.version}".strip()
        if not s.present and s.hint:
            detail += f" -> {s.hint}"
        lines.append(f"  {_line(mark, detail)}")

    lines.append("Config:")
    lines.append(f"  project: {cfg.project.compose_project_name}")
    lines.append(f"  image:   {cfg.image.custom_image}:{cfg.image.custom_tag}")
    lines.append(f"  apps:    {', '.join(a.name for a in cfg.apps) or '(none)'}")
    lines.append(f"  sites:   {', '.join(s.domain for s in cfg.sites) or '(none)'}")

    app_names = {a.name for a in cfg.apps}
    for site in cfg.sites:
        unknown = [a for a in site.apps if a not in app_names]
        if unknown:
            lines.append(
                f"  {_line('WARN', f"site {site.domain} references apps not in image: {', '.join(unknown)}")}"
            )

    lines.append("Secrets (.env):")
    for key in ("DB_ROOT_PASSWORD", "SITE_ADMIN_PASSWORD"):
        if cfg.env.get(key):
            lines.append(f"  {_line('ok', key)}")
        else:
            lines.append(f"  {_line('WARN', f'{key} not set')}")

    if cfg.proxy.enabled:
        for key in ("NPM_ADMIN_EMAIL", "NPM_ADMIN_PASSWORD"):
            if cfg.env.get(key):
                lines.append(f"  {_line('ok', key)}")
            else:
                lines.append(
                    f"  {_line('WARN', f'{key} not set (required for proxy apply)')}"
                )
        lines.append("NPM API:")
        lines.append(f"  {npm_api.test_login(cfg)}")
        if cfg.proxy.letsencrypt_email:
            lines.append(
                f"  {_line('ok', f'proxy.letsencrypt_email={cfg.proxy.letsencrypt_email} (enables cert step)')}"
            )
            lines.append(
                "  note: NPM 2.x uses the NPM admin user's profile email for Let's Encrypt, "
                "not this YAML field. Ensure they match."
            )
        else:
            lines.append(
                f"  {_line('WARN', 'proxy.letsencrypt_email empty; proxy apply will skip TLS')}"
            )
    else:
        lines.append(f"  {_line('ok', 'proxy.enabled=false (NPM checks skipped)')}")

    ovh_keys = ("OVH_APPLICATION_KEY", "OVH_APPLICATION_SECRET", "OVH_CONSUMER_KEY")
    if cfg.env.get("OVH_ENDPOINT") or any(cfg.env.get(k) for k in ovh_keys):
        for key in ("OVH_ENDPOINT", *ovh_keys):
            if cfg.env.get(key):
                lines.append(f"  {_line('ok', key)}")
            else:
                lines.append(f"  {_line('WARN', f'{key} not set (dns apply may fail)')}")
    elif cfg.sites and cfg.server.ip_address:
        lines.append(
            f"  {_line('ok', 'OVH credentials absent (use manual DNS or dns apply later)')}"
        )

    lines.append("Deploy keys:")
    missing = sshkeys.check_keys(cfg)
    if not missing:
        lines.append(f"  {_line('ok', 'all configured keys present on disk')}")
    else:
        for s in missing:
            lines.append(f"  {_line('MISSING', f'{s.name} ({s.path})')}")

    if check_dns and cfg.sites and cfg.server.ip_address:
        lines.append("DNS:")
        try:
            lines.append(dns.verify_dns(cfg, capture=True))
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  {_line('WARN', f'dns verify failed: {exc}')}")
    elif cfg.sites and not cfg.server.ip_address:
        lines.append(f"  {_line('WARN', 'server.ip_address unset; skipping dns verify')}")

    if cfg.sites:
        try:
            live = bench.list_sites(cfg, capture=True).output.strip()
            if live:
                lines.append("Bench sites:")
                for name in live.splitlines():
                    lines.append(f"  {name}")
        except Exception:  # noqa: BLE001
            lines.append("  (stack not running or bench unreachable)")

    return "\n".join(lines)
