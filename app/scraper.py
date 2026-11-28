"""
Live Real Estate Scraper Engine for ImmoMetrica Clone.
Crawls live properties from Kleinanzeigen, ImmoScout24, and Immowelt,
extracting real offer links, prices, living space, rooms, and images.
Applies Cross-Portal Deal Fusion to consolidate duplicates and provide multi-portal badges.
"""

import re
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.datetime_utils import get_berlin_now_str, get_berlin_today_str
from concurrent.futures import ThreadPoolExecutor

from app.database import get_connection
from app.calculator import calculate_cashflow
from app.valuation import evaluate_property_valuation
from app.geo_resolver import resolve_location_and_tax

from app.scrapers.kleinanzeigen import scrape_kleinanzeigen_deals
from app.scrapers.immoscout import search_immoscout_deals, fetch_immoscout_expose
from app.scrapers.immowelt import search_immowelt_deals, fetch_immowelt_expose
from app.scrapers.deal_fusion import fuse_deals

logger = logging.getLogger("immometrica.scraper")


def scrape_live_deals_kleinanzeigen(city: str, query: str = "", max_results: int = 15) -> List[Dict[str, Any]]:
    """Backward-compatible proxy to Kleinanzeigen scraper."""
    return scrape_kleinanzeigen_deals(city=city, query=query, max_results=max_results)


def matches_property_type(requested_type: str, candidate_type: str) -> bool:
    """Unified property type matcher across scrapers, Search Agent, and Stage 4 audit."""
    pt_req = str(requested_type or "all").lower().strip()
    if not pt_req or pt_req == "all":
        return True
    pt_cand = str(candidate_type or "").lower().strip()
    if not pt_cand:
        return True

    apt_terms = ("wohnung", "etw", "apartment", "maisonette", "penthouse", "loft", "dachgeschoss", "erdgeschoss", "souterrain")
    mfh_terms = ("mehrfamilienhaus", "mfh", "zinshaus", "renditehaus", "wohn- und geschäftshaus", "anlageobjekt")
    efh_terms = ("einfamilienhaus", "efh", "doppelhaushälfte", "doppelhaus", "reihenhaus", "bungalow", "stadthaus", "zweifamilienhaus")
    land_terms = ("grundstück", "baugrundstück", "bauplatz", "bauland")

    if any(t in pt_req for t in ("grundst", "plot", "land")):
        return any(t in pt_cand for t in land_terms)
    if any(t in pt_req for t in ("mehrfamilien", "mfh", "zinshaus", "renditehaus")):
        return any(t in pt_cand for t in mfh_terms)
    if any(t in pt_req for t in ("einfamilien", "efh", "doppelhaus", "reihenhaus")):
        return any(t in pt_cand for t in efh_terms) or pt_cand == "haus"
    if pt_req in ("haus", "house"):
        return any(t in pt_cand for t in efh_terms) or any(t in pt_cand for t in mfh_terms) or "haus" in pt_cand
    if any(t in pt_req for t in ("wohnung", "etw", "apartment", "maisonette", "penthouse")):
        if any(t in pt_cand for t in land_terms) or any(t in pt_cand for t in mfh_terms) or any(t in pt_cand for t in efh_terms):
            return False
        return any(t in pt_cand for t in apt_terms)

    return pt_req in pt_cand or pt_cand in pt_req


def scrape_multi_portal_deals(
    city: str,
    query: str = "",
    portals: Optional[List[str]] = None,
    max_results: int = 25,
    min_yield: Optional[float] = None,
    yield_basis: str = "any",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    exclude_urls: Optional[Any] = None,
    postal_code: Optional[str] = None,
    radius_km: int = 25,
    property_type: str = "all"
) -> List[Dict[str, Any]]:
    """
    Coordinates multi-portal meta-search across Kleinanzeigen, ImmoScout24, and Immowelt.
    Supports property_type, postal_code and radius_km parameters, deduplicates identical
    normalized URLs while keeping cross-portal listings strictly separated, and filters
    out deals below min_yield (respecting yield_basis) or outside price boundaries.
    """
    from app.scrapers.deal_fusion import normalize_portal_url
    target_portals = [p.lower() for p in (portals or ["kleinanzeigen", "immoscout24", "immowelt"])]
    all_raw_deals: List[Dict[str, Any]] = []
    exclude_set = {normalize_portal_url(u) for u in exclude_urls} if exclude_urls else set()

    # If filters like min_yield are requested, fetch more raw candidates so we still reach max_results after filtering
    raw_fetch_count = min(max_results * 3, 50) if (min_yield is not None or max_price is not None or min_price is not None) else max_results

    def _matches_criteria(d: Dict[str, Any]) -> bool:
        price = float(d.get("purchase_price") or 0.0)
        if price <= 0:
            return False

        portal_url = normalize_portal_url(d.get("portal_url", ""))
        if exclude_set and portal_url in exclude_set:
            return False

        if property_type and property_type.lower() != "all":
            if not matches_property_type(property_type, str(d.get("property_type") or "")):
                return False

        if max_price is not None and max_price > 0 and price > max_price:
            return False
        if min_price is not None and min_price > 0 and price < min_price:
            return False

        if city or postal_code:
            from app.geo_resolver import is_within_search_radius
            in_range, _ = is_within_search_radius(
                target_city=city,
                target_postal_code=postal_code,
                radius_km=radius_km,
                deal_city=d.get("city"),
                deal_postal_code=d.get("postal_code"),
                deal_lat=d.get("lat"),
                deal_lng=d.get("lng"),
            )
            if not in_range:
                return False

        if min_yield is not None and min_yield > 0:
            from app.valuation import get_mietspiegel_size_benchmark, evaluate_property_valuation
            sqm = float(d.get("living_space") or 0.0)
            deal_city = d.get("city") or city
            deal_plz = d.get("postal_code") or postal_code
            deal_state = d.get("state")
            cur_rent = float(d.get("current_rent") or 0.0)
            is_rented_flag = bool(d.get("is_rented"))
            act_yield = round(((cur_rent * 12.0) / price) * 100.0, 2) if (is_rented_flag and cur_rent > 0 and price > 0) else 0.0

            # 1. Erwartete Benchmark-Rendite
            exp_rent = float(d.get("expected_rent") or 0.0)
            if exp_rent <= 0 and price > 0 and sqm > 0:
                val = evaluate_property_valuation(city=deal_city, living_space=sqm, purchase_price=price, postal_code=deal_plz, state=deal_state)
                exp_rent = float(val.get("expected_rent_monthly") or 0.0)
            expected_yield = round(((exp_rent * 12.0) / price) * 100.0, 2) if price > 0 else 0.0

            # 2. Größenadjustierte Mietspiegel-Rendite (Mietspiegel-Schutz)
            mietspiegel_size_yield = 0.0
            if price > 0 and sqm > 0:
                ms = get_mietspiegel_size_benchmark(city=deal_city, living_space=sqm, postal_code=deal_plz, state=deal_state)
                ms_monthly = float(ms.get("expected_sqm_rent") or 0.0) * sqm
                mietspiegel_size_yield = round(((ms_monthly * 12.0) / price) * 100.0, 2)

            yb = (yield_basis or "any").lower()
            if yb == "actual":
                # Keep rented card-teaser listings whose exact EUR rent is only revealed inside the full exposé
                if is_rented_flag and cur_rent <= 0:
                    eff_yield = max(act_yield, float(d.get("gross_yield") or 0.0), expected_yield)
                else:
                    eff_yield = act_yield
            elif yb == "potential":
                eff_yield = max(expected_yield, mietspiegel_size_yield)
            else:  # "any" (Standard): Maximum aus Ist-Rendite, erwarteter Benchmark-Rendite und Mietspiegel-Rendite
                eff_yield = max(act_yield, expected_yield, mietspiegel_size_yield)

            if eff_yield < min_yield:
                return False

        return True

    def _fetch_portal(portal_name: str) -> List[Dict[str, Any]]:
        try:
            if "kleinanzeigen" in portal_name:
                raw_list = scrape_kleinanzeigen_deals(
                    city=city,
                    query=query,
                    max_results=raw_fetch_count,
                    exclude_urls=exclude_set,
                    postal_code=postal_code,
                    radius_km=radius_km,
                    property_type=property_type
                )
            elif "scout" in portal_name:
                raw_list = search_immoscout_deals(
                    city=city,
                    query=query,
                    radius_km=radius_km,
                    max_results=raw_fetch_count,
                    property_type=property_type,
                    exclude_urls=exclude_set,
                    postal_code=postal_code
                )
            elif "immowelt" in portal_name:
                raw_list = search_immowelt_deals(
                    city=city,
                    query=query,
                    max_results=raw_fetch_count,
                    exclude_urls=exclude_set,
                    postal_code=postal_code,
                    radius_km=radius_km,
                    property_type=property_type
                )
            else:
                raw_list = []
            
            # Pre-filter raw deals against user criteria before fusing and importing
            return [d for d in raw_list if _matches_criteria(d)]
        except Exception as e:
            logger.error(f"Error scraping portal {portal_name}: {e}")
        return []

    # Run portal scrapers in parallel threads for lightning-fast response (< 2 seconds total)
    portal_deals_dict: Dict[str, List[Dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {executor.submit(_fetch_portal, p): p for p in target_portals}
        for future in futures:
            p_name = futures[future]
            try:
                portal_results = future.result()
                logger.info(f"Portal '{p_name}' returned {len(portal_results)} matching deals for {city} (min_yield: {min_yield}).")
                portal_deals_dict[p_name] = portal_results
            except Exception as exc:
                logger.error(f"Portal '{p_name}' generated an exception: {exc}")

    # Interleave results fairly across all portals (Round-Robin) so Kleinanzeigen doesn't crowd out Immowelt & ImmoScout
    all_raw_deals: List[Dict[str, Any]] = []
    max_len = max([len(v) for v in portal_deals_dict.values()] or [0])
    for i in range(max_len):
        for p_name in target_portals:
            p_list = portal_deals_dict.get(p_name, [])
            if i < len(p_list):
                all_raw_deals.append(p_list[i])

    # Fallback only if Kleinanzeigen was requested AND failed/errored earlier (never override explicit portal filter)
    kleinanzeigen_requested = any("kleinanzeigen" in p for p in target_portals)
    if not all_raw_deals and kleinanzeigen_requested and "kleinanzeigen" not in portal_deals_dict:
        try:
            raw_k = scrape_kleinanzeigen_deals(
                city=city,
                query=query,
                max_results=raw_fetch_count,
                exclude_urls=exclude_set,
                postal_code=postal_code,
                radius_km=radius_km,
                property_type=property_type
            )
            all_raw_deals = [d for d in raw_k if _matches_criteria(d)]
        except Exception as exc:
            logger.error(f"Fallback portal 'kleinanzeigen' generated an exception: {exc}")

    # Cross-Portal Deal Fusion is deactivated: every portal listing remains strictly independent
    fused_deals = fuse_deals(all_raw_deals)
    logger.info(f"Portal listings separation: {len(all_raw_deals)} deals processed independently.")

    return fused_deals[:max_results]


def import_scraped_deals_returning_ids(deals: List[Dict[str, Any]]) -> List[int]:
    """
    Saves scraped properties into SQLite DB, avoiding duplicate portal_urls,
    stores multi-source portal badges in source_portals column, and returns new property IDs.
    """
    if not deals:
        return []

    from app.geo_resolver import ensure_distinct_coord
    from app.scrapers.deal_fusion import normalize_portal_url
    conn = get_connection()
    inserted_ids: List[int] = []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT lat, lng FROM properties WHERE lat IS NOT NULL AND lng IS NOT NULL")
        occupied_coords = {(round(float(r[0]), 4), round(float(r[1]), 4)) for r in cursor.fetchall()}

        for idx_d, d in enumerate(deals):
            portal_url = normalize_portal_url(d.get("portal_url", ""))
            # Check if portal_url already exists
            cursor.execute("SELECT id, source_portals FROM properties WHERE portal_url = ?", (portal_url,))
            existing_row = cursor.fetchone()
            if existing_row:
                # Update source_portals if newly detected portals exist
                if d.get("source_portals"):
                    try:
                        cur_sp = json.loads(existing_row["source_portals"] or "[]")
                        new_sp = d["source_portals"]
                        merged_sp = {p.get("portal"): p for p in cur_sp}
                        for p in new_sp:
                            merged_sp[p.get("portal")] = p
                        cursor.execute(
                            "UPDATE properties SET source_portals = ? WHERE id = ?",
                            (json.dumps(list(merged_sp.values())), existing_row["id"])
                        )
                    except Exception:
                        pass
                continue

            d_lat = float(d.get("lat") or 51.5)
            d_lng = float(d.get("lng") or 7.4)
            clean_lat, clean_lng = ensure_distinct_coord(d_lat, d_lng, occupied_coords, seed=idx_d + 100)

            sp_json = json.dumps(d.get("source_portals") or [{
                "portal": d.get("source_portal", "Kleinanzeigen"),
                "url": portal_url,
                "price": float(d.get("purchase_price") or 0.0)
            }])
            raw_tags = d.get("tags", [])
            tags_json = json.dumps(raw_tags, ensure_ascii=False) if isinstance(raw_tags, list) else (raw_tags or "[]")

            cursor.execute("""
                INSERT INTO properties (
                    title, address, postal_code, city, state, country, property_type, building_type,
                    rooms, living_space, land_area, year_built, condition, purchase_price, original_price,
                    price_drop_amount, price_drop_percent, commission_percent, current_rent, expected_rent,
                    gross_yield, monthly_cashflow, market_value, market_value_per_sqm, market_deviation_percent,
                    source_portal, portal_url, image_url, days_online, first_seen_date, is_private,
                    is_rented, is_foreclosure, is_favorite, pipeline_stage, tags, lat, lng, parcel_id,
                    parcel_area, is_online, availability_status, status_message, last_checked_at, live_search_url,
                    source_portals, description, gallery_images
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?
                )
            """, (
                d.get("title", ""), d.get("address", ""), d.get("postal_code", ""), d.get("city", ""), d.get("state", "DE"), d.get("country", "DE"),
                d.get("property_type", "Eigentumswohnung"), d.get("building_type", "Etagenwohnung"),
                d.get("rooms", 2.0), d.get("living_space", 50.0), d.get("land_area", 0.0), d.get("year_built", 1980), d.get("condition", "Gepflegt"),
                d.get("purchase_price", 0.0), d.get("original_price", d.get("purchase_price", 0.0)),
                d.get("price_drop_amount", 0.0), d.get("price_drop_percent", 0.0), d.get("commission_percent", 3.57),
                d.get("current_rent", 0.0), d.get("expected_rent", 0.0),
                d.get("gross_yield", 0.0), d.get("monthly_cashflow", 0.0),
                d.get("market_value", 0.0), d.get("market_value_per_sqm", 0.0), d.get("market_deviation_percent", 0.0),
                d.get("source_portal", "Kleinanzeigen"), portal_url, d.get("image_url", ""),
                d.get("days_online", 1), d.get("first_seen_date", get_berlin_today_str()),
                d.get("is_private", 0), d.get("is_rented", 0), d.get("is_foreclosure", 0),
                d.get("is_favorite", 0), d.get("pipeline_stage", "neu"),
                tags_json,
                clean_lat, clean_lng, d.get("parcel_id"), d.get("parcel_area", 0.0),
                d.get("is_online", 1), d.get("availability_status", "online"),
                d.get("status_message", "Live erreichbar & aktiv"),
                d.get("last_checked_at", get_berlin_now_str()),
                d.get("live_search_url", ""),
                sp_json,
                d.get("description", ""),
                json.dumps(d.get("gallery_images") or []) if isinstance(d.get("gallery_images"), list) else d.get("gallery_images")
            ))
            inserted_ids.append(cursor.lastrowid)

        conn.commit()
    finally:
        conn.close()
    return inserted_ids


def import_scraped_deals_to_db(deals: List[Dict[str, Any]]) -> int:
    """Saves scraped properties into SQLite DB, avoiding duplicate portal_urls"""
    return len(import_scraped_deals_returning_ids(deals))
