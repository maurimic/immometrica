"""
Cross-Portal Deal Fusion Engine for ImmoMetrica.
Detects identical listings published across multiple portals (Kleinanzeigen, ImmoScout24, Immowelt),
merges them into a single canonical deal adopting the lowest purchase price,
and aggregates multi-source direct links for investor transparency.
"""

import re
import json
import logging
from typing import List, Dict, Any

logger = logging.getLogger("immometrica.scraper.deal_fusion")


def are_deals_matching(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """
    Evaluates whether two scraped listings from different portals represent the same physical property.
    Criteria:
    1. Same city or postal code
    2. Living space within +/- 2.0 m² (or <= 3% variance)
    3. Room count matches (+/- 0.5)
    4. Price within 10% corridor OR street name match
    """
    # 1. City / PLZ check
    city_a = (a.get("city") or "").lower().strip()
    city_b = (b.get("city") or "").lower().strip()
    plz_a = (a.get("postal_code") or "").strip()
    plz_b = (b.get("postal_code") or "").strip()

    loc_match = False
    if plz_a and plz_b and plz_a == plz_b:
        loc_match = True
    elif city_a and city_b and (city_a in city_b or city_b in city_a):
        loc_match = True

    if not loc_match:
        return False

    # 2. Living space check
    sqm_a = float(a.get("living_space") or 0.0)
    sqm_b = float(b.get("living_space") or 0.0)
    if sqm_a <= 0 or sqm_b <= 0:
        return False
    diff_sqm = abs(sqm_a - sqm_b)
    if diff_sqm > 2.0 and (diff_sqm / max(sqm_a, sqm_b)) > 0.03:
        return False

    # 3. Room count check
    rooms_a = float(a.get("rooms") or 1.0)
    rooms_b = float(b.get("rooms") or 1.0)
    if abs(rooms_a - rooms_b) >= 0.5:
        return False

    # 4. Street name or price proximity check
    addr_a = (a.get("address") or "").lower()
    addr_b = (b.get("address") or "").lower()
    # Extract street word (>= 5 chars ending in str/straße/weg/platz/allee)
    st_words_a = set(re.findall(r"\b[a-zäöüß]{4,}(?:str|straße|weg|platz|allee|ring|gasse)\b", addr_a))
    st_words_b = set(re.findall(r"\b[a-zäöüß]{4,}(?:str|straße|weg|platz|allee|ring|gasse)\b", addr_b))
    street_matches = bool(st_words_a and st_words_b and (st_words_a & st_words_b))

    price_a = float(a.get("purchase_price") or 0.0)
    price_b = float(b.get("purchase_price") or 0.0)
    price_diff_pct = abs(price_a - price_b) / max(price_a, price_b) if max(price_a, price_b) > 0 else 1.0

    # Match if street identical OR price within 10%
    if street_matches or price_diff_pct <= 0.10:
        return True

    return False


def fuse_deals(deals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Fuses a combined list of listings across portals into canonical deduplicated records.
    - Adopts the lowest purchase price.
    - Sets primary source_portal to the best-price portal.
    - Aggregates all portal sources into `source_portals` JSON list.
    - Recomputes yield and cashflow with the optimized lowest price.
    """
    if not deals:
        return []

    from app.calculator import calculate_cashflow

    fused: List[Dict[str, Any]] = []

    for deal in deals:
        matched_idx = None
        for idx, existing in enumerate(fused):
            if are_deals_matching(existing, deal):
                matched_idx = idx
                break

        if matched_idx is None:
            # Initialize source_portals list if not present
            canonical = dict(deal)
            sp_list = canonical.get("source_portals")
            if not sp_list or not isinstance(sp_list, list):
                canonical["source_portals"] = [{
                    "portal": canonical.get("source_portal", "Kleinanzeigen"),
                    "url": canonical.get("portal_url", ""),
                    "price": float(canonical.get("purchase_price") or 0.0)
                }]
            fused.append(canonical)
        else:
            # Merge deal into existing canonical deal
            canonical = fused[matched_idx]
            existing_portals = canonical.setdefault("source_portals", [])

            # Check if this portal already recorded
            new_portal_name = deal.get("source_portal", "Anderes Portal")
            new_portal_url = deal.get("portal_url", "")
            new_price = float(deal.get("purchase_price") or 0.0)

            already_present = any(p.get("url") == new_portal_url or p.get("portal") == new_portal_name for p in existing_portals)
            if not already_present:
                existing_portals.append({
                    "portal": new_portal_name,
                    "url": new_portal_url,
                    "price": new_price
                })

            # Check if new deal has a lower purchase price!
            cur_canon_price = float(canonical.get("purchase_price") or 0.0)
            if 0 < new_price < cur_canon_price:
                logger.info(f"Cross-Portal Deal Fusion: Price drop detected for '{canonical['title']}' ({cur_canon_price} -> {new_price} EUR via {new_portal_name})")
                canonical["purchase_price"] = new_price
                canonical["source_portal"] = new_portal_name
                canonical["portal_url"] = new_portal_url
                canonical["commission_percent"] = min(
                    float(canonical.get("commission_percent", 3.57)),
                    float(deal.get("commission_percent", 3.57))
                )

                # Recompute yield & cashflow with lowest price
                sqm = float(canonical.get("living_space") or 50.0)
                expected_rent = float(canonical.get("expected_rent") or 0.0)
                if expected_rent > 0 and new_price > 0:
                    canonical["gross_yield"] = round(((expected_rent * 12.0) / new_price) * 100.0, 2)
                    cf = calculate_cashflow(
                        price=new_price,
                        rent=expected_rent,
                        city=canonical.get("city", ""),
                        state=canonical.get("state", ""),
                        living_space=sqm
                    )
                    canonical["monthly_cashflow"] = cf.get("monthly_cashflow", canonical.get("monthly_cashflow", 0.0))

            # Prefer higher resolution or non-placeholder images
            if ("unsplash" in (canonical.get("image_url") or "") or "placeholder" in (canonical.get("image_url") or "")) and deal.get("image_url"):
                canonical["image_url"] = deal["image_url"]

            # Add multi-portal tag
            tag_str = str(canonical.get("tags") or "[]")
            if "Multi-Portal" not in tag_str:
                canonical["tags"] = tag_str.replace("]", ', "Multi-Portal"]').replace("'", '"')

    return fused
