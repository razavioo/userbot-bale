"""Domain policy helpers for direct-vs-tunnel proxy routing."""

from __future__ import annotations

import base64
import os
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from userbot_bale.control.paths import config_dir

IRAN_HOSTED_DOMAINS_URL = (
    "https://github.com/bootmortis/iran-hosted-domains/releases/latest/download/domains.txt"
)
GFWLIST_URL = "https://raw.githubusercontent.com/gfwlist/gfwlist/master/gfwlist.txt"
POLICY_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class ProxyDomainPolicy:
    direct_only_domains: tuple[str, ...] = ()
    tunnel_only_domains: tuple[str, ...] = ()


def load_proxy_domain_policy(*, refresh: bool = False, timeout: float = 4.0) -> ProxyDomainPolicy:
    """Load direct/tunnel routing domains from local files and cached public lists."""

    direct_domains = parse_domain_lines(_read_bundled_policy_file("iran-hosted-domains.txt"))
    direct_domains.update(parse_domain_lines(_read_bundled_policy_file("iran-direct-overrides.txt")))
    tunnel_domains = parse_gfwlist_domains(_read_bundled_policy_file("gfwlist.txt"))

    direct_domains.update(_domains_from_env("USERBOT_BALE_PROXY_DIRECT_ONLY_DOMAIN"))
    tunnel_domains.update(_domains_from_env("USERBOT_BALE_PROXY_TUNNEL_ONLY_DOMAIN"))

    direct_domains.update(_read_domain_file_env("USERBOT_BALE_PROXY_DIRECT_ONLY_FILE"))
    tunnel_domains.update(_read_domain_file_env("USERBOT_BALE_PROXY_TUNNEL_ONLY_FILE"))

    if refresh:
        direct_domains.update(
            _load_cached_or_remote_domains(
                cache_name="iran-hosted-domains.txt",
                url=os.environ.get("USERBOT_BALE_IRAN_HOSTED_DOMAINS_URL", IRAN_HOSTED_DOMAINS_URL),
                parser=parse_domain_lines,
                refresh=refresh,
                timeout=timeout,
            )
        )
        tunnel_domains.update(
            _load_cached_or_remote_domains(
                cache_name="gfwlist.txt",
                url=os.environ.get("USERBOT_BALE_GFWLIST_URL", GFWLIST_URL),
                parser=parse_gfwlist_domains,
                refresh=refresh,
                timeout=timeout,
            )
        )

    tunnel_domains.difference_update(direct_domains)
    return ProxyDomainPolicy(
        direct_only_domains=tuple(sorted(direct_domains)),
        tunnel_only_domains=tuple(sorted(tunnel_domains)),
    )


def _read_bundled_policy_file(name: str) -> str:
    try:
        return resources.files("userbot_bale.runtime.policy_data").joinpath(name).read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return ""


def parse_domain_lines(text: str) -> set[str]:
    domains: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "!", "[")):
            continue
        if line.startswith("@@"):
            continue
        domains.update(_extract_domains_from_text(line))
    return domains


def parse_gfwlist_domains(text: str) -> set[str]:
    decoded = _maybe_base64_decode(text)
    domains: set[str] = set()
    for raw in decoded.splitlines():
        line = raw.strip()
        if not line or line.startswith(("!", "[", "@@")):
            continue
        if line.startswith("||"):
            domains.update(_extract_domains_from_text(line[2:].split("^", 1)[0]))
            continue
        if line.startswith("|"):
            domains.update(_extract_domains_from_text(line.lstrip("|")))
            continue
        if "://" in line or "." in line:
            domains.update(_extract_domains_from_text(line))
    return domains


def _load_cached_or_remote_domains(
    *,
    cache_name: str,
    url: str,
    parser,
    refresh: bool,
    timeout: float,
) -> set[str]:  # noqa: ANN001
    cache_path = _policy_cache_dir() / cache_name
    text = ""
    if not refresh and _cache_is_fresh(cache_path):
        text = _safe_read_text(cache_path)
    if not text:
        text = _fetch_text(url, timeout=timeout)
        if text:
            try:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(text, encoding="utf-8")
            except OSError:
                pass
    if not text:
        text = _safe_read_text(cache_path)
    return parser(text) if text else set()


def _policy_cache_dir() -> Path:
    return config_dir() / "proxy-policy"


def _cache_is_fresh(path: Path) -> bool:
    try:
        return (time.time() - path.stat().st_mtime) < POLICY_CACHE_TTL_SECONDS
    except OSError:
        return False


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _fetch_text(url: str, *, timeout: float) -> str:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except Exception:
        return ""


def _read_domain_file_env(name: str) -> set[str]:
    path = os.environ.get(name)
    if not path:
        return set()
    return parse_domain_lines(_safe_read_text(Path(path).expanduser()))


def _domains_from_env(name: str) -> set[str]:
    return {domain for domain in (_normalize_domain(item) for item in os.environ.get(name, "").split(",")) if domain}


def _maybe_base64_decode(text: str) -> str:
    compact = "".join(line.strip() for line in text.splitlines() if line.strip())
    try:
        decoded = base64.b64decode(compact + "=" * (-len(compact) % 4), validate=True)
        return decoded.decode("utf-8", errors="replace")
    except Exception:
        return text


def _extract_domains_from_text(value: str) -> set[str]:
    candidate = value.strip()
    if not candidate:
        return set()
    if "://" in candidate:
        host = urllib.parse.urlsplit(candidate).hostname or ""
        return {_normalize_domain(host)} if _normalize_domain(host) else set()
    candidate = candidate.split("/", 1)[0].split("^", 1)[0].split(":", 1)[0]
    candidate = candidate.replace("*.", "").lstrip(".")
    domain = _normalize_domain(candidate)
    return {domain} if domain else set()


def _normalize_domain(value: str) -> str:
    domain = value.strip().lower().strip(".")
    if not domain or "*" in domain or "/" in domain:
        return ""
    if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)*", domain):
        return ""
    return domain
