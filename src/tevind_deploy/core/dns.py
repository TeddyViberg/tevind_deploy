"""OVH DNS automation.

Creates/updates the A (and optional AAAA) records that point each site domain
at the VPS, then refreshes the zone. ``verify_dns`` resolves the domains with
``dig`` and compares against the configured server IP.

OVH credentials come from .env: ``OVH_APPLICATION_KEY``,
``OVH_APPLICATION_SECRET``, ``OVH_CONSUMER_KEY`` (and optional ``OVH_ENDPOINT``).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

from ..config import Config
from .runner import OutputCollector


@dataclass
class DNSResult:
    domain: str
    record_type: str
    target: str
    action: str  # created | updated | unchanged | error
    detail: str = ""


def _client(cfg: Config):
    try:
        import ovh  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "The 'ovh' package is required for DNS. Install it (pip install ovh) "
            "or re-run tevind_deploy_setup.py."
        ) from exc

    endpoint = cfg.env.get("OVH_ENDPOINT") or cfg.dns.endpoint or "ovh-eu"
    return ovh.Client(
        endpoint=endpoint,
        application_key=cfg.require_secret("OVH_APPLICATION_KEY"),
        application_secret=cfg.require_secret("OVH_APPLICATION_SECRET"),
        consumer_key=cfg.require_secret("OVH_CONSUMER_KEY"),
    )


def _zones(cfg: Config, client) -> list[str]:
    if cfg.dns.zones:
        return list(cfg.dns.zones)
    return list(client.get("/domain/zone"))


def split_domain(domain: str, zones: list[str]) -> tuple[str, str]:
    """Return (zone, subdomain) for ``domain`` given the available zones.

    The longest matching zone suffix wins so ``staging.autospine.eu`` maps to
    zone ``autospine.eu`` with subdomain ``staging``.
    """
    candidates = sorted(
        [z for z in zones if domain == z or domain.endswith("." + z)],
        key=len,
        reverse=True,
    )
    if not candidates:
        raise ValueError(
            f"No OVH zone found for '{domain}'. Available zones: {', '.join(zones) or '(none)'}"
        )
    zone = candidates[0]
    if domain == zone:
        return zone, ""
    return zone, domain[: -(len(zone) + 1)]


def _upsert_record(
    client,
    zone: str,
    subdomain: str,
    field_type: str,
    target: str,
    ttl: int,
) -> str:
    existing = client.get(
        f"/domain/zone/{zone}/record",
        fieldType=field_type,
        subDomain=subdomain,
    )
    if existing:
        record_id = existing[0]
        current = client.get(f"/domain/zone/{zone}/record/{record_id}")
        if current.get("target") == target and current.get("ttl") == ttl:
            return "unchanged"
        client.put(
            f"/domain/zone/{zone}/record/{record_id}",
            subDomain=subdomain,
            target=target,
            ttl=ttl,
        )
        return "updated"
    client.post(
        f"/domain/zone/{zone}/record",
        fieldType=field_type,
        subDomain=subdomain,
        target=target,
        ttl=ttl,
    )
    return "created"


def apply_dns(cfg: Config, capture: bool = False) -> str:
    out = OutputCollector(capture=capture)
    if not cfg.server.ip_address:
        raise ValueError("server.ip_address is not set; required for DNS A records.")

    client = _client(cfg)
    zones = _zones(cfg, client)
    touched_zones: set[str] = set()
    results: list[DNSResult] = []

    for domain in cfg.domains():
        try:
            zone, subdomain = split_domain(domain, zones)
        except ValueError as exc:
            out.status(f"SKIP {domain}: {exc}")
            results.append(DNSResult(domain, "A", "", "error", str(exc)))
            continue

        action = _upsert_record(client, zone, subdomain, "A", cfg.server.ip_address, cfg.dns.ttl)
        out.status(f"A {domain} -> {cfg.server.ip_address} ({action})")
        results.append(DNSResult(domain, "A", cfg.server.ip_address, action))
        touched_zones.add(zone)

        if cfg.dns.manage_ipv6 and cfg.server.ipv6_address:
            action6 = _upsert_record(
                client, zone, subdomain, "AAAA", cfg.server.ipv6_address, cfg.dns.ttl
            )
            out.status(f"AAAA {domain} -> {cfg.server.ipv6_address} ({action6})")
            results.append(DNSResult(domain, "AAAA", cfg.server.ipv6_address, action6))

    for zone in sorted(touched_zones):
        client.post(f"/domain/zone/{zone}/refresh")
        out.status(f"Refreshed zone {zone}")

    out.status("DNS apply complete.")
    return out.text()


def _dig(domain: str) -> list[str]:
    proc = subprocess.run(
        ["dig", "+short", "A", domain], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def verify_dns(cfg: Config, capture: bool = False) -> str:
    out = OutputCollector(capture=capture)
    expected = cfg.server.ip_address
    if not expected:
        raise ValueError("server.ip_address is not set; cannot verify DNS.")
    all_ok = True
    for domain in cfg.domains():
        resolved = _dig(domain)
        ok = expected in resolved
        all_ok = all_ok and ok
        mark = "ok " if ok else "MISMATCH"
        out.status(f"[{mark}] {domain} -> {', '.join(resolved) or '(no record)'} (expected {expected})")
    out.status("DNS verification " + ("passed." if all_ok else "found mismatches."))
    return out.text()
