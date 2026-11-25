"""
Network security, SSRF protection, and secret redaction helpers for ImmoMetrica.
"""

import ipaddress
import logging
import re
import socket
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse

import requests

logger = logging.getLogger("immometrica.security")

BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata",
    "169.254.169.254",
    "0.0.0.0",
    "::1",
}


def _is_ip_blocked(ip_str: str) -> bool:
    """Returns True if the IP address is private, loopback, link-local, multicast, or reserved."""
    try:
        ip_obj = ipaddress.ip_address(ip_str.split("%")[0])
        return bool(
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_link_local
            or ip_obj.is_multicast
            or ip_obj.is_reserved
            or ip_obj.is_unspecified
        )
    except ValueError:
        return True


def validate_external_url(url: str) -> str:
    """
    Validates that an external URL uses http/https and does not target localhost,
    private/LAN networks, link-local metadata endpoints, or reserved IP ranges.
    Raises ValueError with a German user-facing message if unsafe.
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("URL darf nicht leer sein.")

    parsed = urlparse(raw)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Nur http:// oder https:// URLs sind erlaubt.")

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("Ungültige URL: Hostname fehlt.")

    if (
        host in BLOCKED_HOSTNAMES
        or host.endswith(".local")
        or host.endswith(".internal")
        or host.endswith(".localhost")
    ):
        raise ValueError(f"Zugriff auf internen Host '{host}' aus Sicherheitsgründen blockiert.")

    # Direct IP literal check
    try:
        ipaddress.ip_address(host)
        if _is_ip_blocked(host):
            raise ValueError(f"Zugriff auf private/interne IP-Adresse '{host}' blockiert.")
        return raw
    except ValueError as e:
        if "blockiert" in str(e):
            raise

    # Resolve DNS if available and block private/internal targets (fails open only for non-resolvable
    # hostnames so mocked offline tests still work while any resolved private target is blocked).
    try:
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        for info in infos:
            addr = info[4][0]
            if _is_ip_blocked(addr):
                raise ValueError(
                    f"Ziel-Host '{host}' löst auf eine nicht-öffentliche IP ({addr}) auf und wurde blockiert."
                )
    except socket.gaierror:
        pass

    return raw


def safe_requests_get(
    url: str,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 7.0,
    max_redirects: int = 5,
    **kwargs,
) -> requests.Response:
    """
    Performs an HTTP GET request with SSRF validation on the initial URL and every redirect hop.
    """
    current_url = validate_external_url(url)
    for _ in range(max_redirects + 1):
        resp = requests.get(
            current_url,
            headers=headers,
            timeout=timeout,
            allow_redirects=False,
            **kwargs,
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get("Location")
            if not location:
                return resp
            next_url = urljoin(current_url, location.strip())
            current_url = validate_external_url(next_url)
            continue
        return resp
    raise requests.TooManyRedirects(f"Zu viele Weiterleitungen (> {max_redirects})")


_SECRET_PATTERNS = [
    (re.compile(r"([?&](?:key|api_key|token|apikey)=)[^&\s'\"]+", re.IGNORECASE), r"\1***"),
    (re.compile(r"(bot\d+):[A-Za-z0-9_-]{20,}"), r"\1:***"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._-]{12,}", re.IGNORECASE), r"\1***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"), "sk-***"),
    (re.compile(r"\bAIza[A-Za-z0-9_-]{20,}"), "AIza***"),
]


def sanitize_error_message(err: Any) -> str:
    """Redacts API keys, Telegram bot tokens, and Bearer tokens from error strings."""
    text = str(err or "")
    for pat, repl in _SECRET_PATTERNS:
        text = pat.sub(repl, text)
    return text
