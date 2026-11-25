"""
Cross-Portal Deal Fusion Engine for ImmoMetrica.
Note: Cross-portal deal merging is intentionally DEACTIVATED per user requirement:
Every scraped listing from every portal remains strictly independent and isolated.
No automated merging/fusing to prevent false merges or loss of portal-specific data.
However, exact-same normalized portal URLs are deduplicated so identical listings
from paginated or fallback passes are never imported twice.
"""

import logging
import urllib.parse
from typing import List, Dict, Any

logger = logging.getLogger("immometrica.scraper.deal_fusion")


def normalize_portal_url(url: str) -> str:
    """
    Normalizes a portal listing URL by stripping tracking query parameters,
    URL fragments, and trailing slashes while preserving significant query params.
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/") or "/"
        # For expose / Anzeige URLs on major German portals, query params & fragments are tracking-only
        if any(p in host for p in ("kleinanzeigen.de", "immowelt.de", "immobilienscout24.de")):
            if "/s-anzeige/" in path or "/expose/" in path:
                return urllib.parse.urlunsplit((parsed.scheme.lower(), host, path, "", ""))
        # Otherwise strip common tracking params and fragment
        qs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        clean_qs = [
            (k, v) for k, v in qs
            if not k.lower().startswith(("utm_", "fbclid", "gclid", "tracking", "ref"))
        ]
        return urllib.parse.urlunsplit((
            parsed.scheme.lower(),
            host,
            path,
            urllib.parse.urlencode(clean_qs),
            ""
        ))
    except Exception:
        return raw


def are_deals_matching(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """
    Cross-portal deal matching is completely deactivated.
    Always returns False so that listings from different portals are never merged.
    """
    return False


def fuse_deals(deals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keeps every scraped deal strictly separated across portals while deduplicating
    identical normalized portal URLs within the same batch.
    Initializes each deal's source_portals list with its own single portal entry.
    """
    if not deals:
        return []

    separated: List[Dict[str, Any]] = []
    seen_urls = set()
    for deal in deals:
        canonical = dict(deal)
        norm_url = normalize_portal_url(canonical.get("portal_url", ""))
        if norm_url:
            if norm_url in seen_urls:
                continue
            seen_urls.add(norm_url)
            canonical["portal_url"] = norm_url

        sp_list = canonical.get("source_portals")
        if not sp_list or not isinstance(sp_list, list):
            canonical["source_portals"] = [{
                "portal": canonical.get("source_portal", "Portal"),
                "url": norm_url or canonical.get("portal_url", ""),
                "price": float(canonical.get("purchase_price") or 0.0)
            }]
        separated.append(canonical)

    return separated
