"""
Cross-Portal Deal Fusion Engine for ImmoMetrica.
Note: Cross-portal deal merging is intentionally DEACTIVATED per user requirement:
Every scraped listing from every portal remains strictly independent and isolated.
No automated merging/fusing to prevent false merges or loss of portal-specific data.
"""

import logging
from typing import List, Dict, Any

logger = logging.getLogger("immometrica.scraper.deal_fusion")


def are_deals_matching(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """
    Cross-portal deal matching is completely deactivated.
    Always returns False so that listings from different portals are never merged.
    """
    return False


def fuse_deals(deals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Keeps every scraped deal strictly separated.
    Initializes each deal's source_portals list with its own single portal entry.
    No deals are merged, deduplicated, or overwritten across portals.
    """
    if not deals:
        return []

    separated: List[Dict[str, Any]] = []
    for deal in deals:
        canonical = dict(deal)
        sp_list = canonical.get("source_portals")
        if not sp_list or not isinstance(sp_list, list):
            canonical["source_portals"] = [{
                "portal": canonical.get("source_portal", "Portal"),
                "url": canonical.get("portal_url", ""),
                "price": float(canonical.get("purchase_price") or 0.0)
            }]
        separated.append(canonical)

    return separated
