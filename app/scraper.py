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


def scrape_multi_portal_deals(
    city: str,
    query: str = "",
    portals: Optional[List[str]] = None,
    max_results: int = 25,
    min_yield: Optional[float] = None,
    yield_basis: str = "any",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    exclude_urls: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    Coordinates multi-portal meta-search across Kleinanzeigen, ImmoScout24, and Immowelt.
    Fuses cross-portal duplicates into canonical deals with best price and multi-portal badges.
    Strictly filters out deals below min_yield (evaluating both actual and Mietspiegel potential)
    or outside price boundaries BEFORE saving to DB.
    """
    target_portals = [p.lower() for p in (portals or ["kleinanzeigen", "immoscout24", "immowelt"])]
    all_raw_deals: List[Dict[str, Any]] = []
    exclude_set = set(exclude_urls) if exclude_urls else set()

    # If filters like min_yield are requested, fetch more raw candidates so we still reach max_results after filtering
    raw_fetch_count = min(max_results * 3, 50) if (min_yield is not None or max_price is not None or min_price is not None) else max_results

    def _matches_criteria(d: Dict[str, Any]) -> bool:
        price = float(d.get("purchase_price") or 0.0)
        if price <= 0:
            return False

        portal_url = d.get("portal_url", "")
        if exclude_set and portal_url in exclude_set:
            return False

        if max_price is not None and max_price > 0 and price > max_price:
            return False
        if min_price is not None and min_price > 0 and price < min_price:
            return False

        if min_yield is not None and min_yield > 0:
            from app.valuation import get_mietspiegel_size_benchmark, evaluate_property_valuation
            sqm = float(d.get("living_space") or 0.0)
            deal_city = d.get("city") or city
            act_yield = float(d.get("gross_yield") or 0.0)

            # 1. Erwartete Benchmark-Rendite
            exp_rent = float(d.get("expected_rent") or 0.0)
            if exp_rent <= 0 and price > 0 and sqm > 0:
                val = evaluate_property_valuation(city=deal_city, living_space=sqm, purchase_price=price)
                exp_rent = float(val.get("expected_rent_monthly") or 0.0)
            expected_yield = round(((exp_rent * 12.0) / price) * 100.0, 2) if price > 0 else 0.0

            # 2. Größenadjustierte Mietspiegel-Rendite (Mietspiegel-Schutz)
            mietspiegel_size_yield = 0.0
            if price > 0 and sqm > 0:
                ms = get_mietspiegel_size_benchmark(city=deal_city, living_space=sqm)
                ms_monthly = float(ms.get("expected_sqm_rent") or 0.0) * sqm
                mietspiegel_size_yield = round(((ms_monthly * 12.0) / price) * 100.0, 2)

            yb = (yield_basis or "any").lower()
            if yb == "actual":
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
                raw_list = scrape_kleinanzeigen_deals(city=city, query=query, max_results=raw_fetch_count, exclude_urls=exclude_set)
            elif "scout" in portal_name:
                raw_list = search_immoscout_deals(city=city, query=query, max_results=raw_fetch_count, exclude_urls=exclude_set)
            elif "immowelt" in portal_name:
                raw_list = search_immowelt_deals(city=city, query=query, max_results=raw_fetch_count, exclude_urls=exclude_set)
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

    # Fallback to Kleinanzeigen alone if other portals were empty
    if not all_raw_deals:
        raw_k = scrape_kleinanzeigen_deals(city=city, query=query, max_results=raw_fetch_count, exclude_urls=exclude_set)
        all_raw_deals = [d for d in raw_k if _matches_criteria(d)]

    # Cross-Portal Deal Fusion
    fused_deals = fuse_deals(all_raw_deals)
    logger.info(f"Cross-Portal Deal Fusion: {len(all_raw_deals)} raw deals fused into {len(fused_deals)} canonical deals.")

    return fused_deals[:max_results]


def import_scraped_deals_returning_ids(deals: List[Dict[str, Any]]) -> List[int]:
    """
    Saves scraped properties into SQLite DB, avoiding duplicate portal_urls,
    stores multi-source portal badges in source_portals column, and returns new property IDs.
    """
    if not deals:
        return []

    from app.geo_resolver import ensure_distinct_coord
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT lat, lng FROM properties WHERE lat IS NOT NULL AND lng IS NOT NULL")
    occupied_coords = {(round(float(r[0]), 4), round(float(r[1]), 4)) for r in cursor.fetchall()}
    inserted_ids: List[int] = []

    for idx_d, d in enumerate(deals):
        portal_url = d.get("portal_url", "")
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
            d.get("days_online", 1), d.get("first_seen_date", datetime.now().strftime("%Y-%m-%d")),
            d.get("is_private", 0), d.get("is_rented", 0), d.get("is_foreclosure", 0),
            d.get("is_favorite", 0), d.get("pipeline_stage", "neu"),
            str(d.get("tags", "[]")).replace("'", '"'),
            clean_lat, clean_lng, d.get("parcel_id"), d.get("parcel_area", 0.0),
            d.get("is_online", 1), d.get("availability_status", "online"),
            d.get("status_message", "Live erreichbar & aktiv"),
            d.get("last_checked_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            d.get("live_search_url", ""),
            sp_json,
            d.get("description", ""),
            json.dumps(d.get("gallery_images") or []) if isinstance(d.get("gallery_images"), list) else d.get("gallery_images")
        ))
        inserted_ids.append(cursor.lastrowid)

    conn.commit()
    conn.close()
    return inserted_ids


def import_scraped_deals_to_db(deals: List[Dict[str, Any]]) -> int:
    """Saves scraped properties into SQLite DB, avoiding duplicate portal_urls"""
    return len(import_scraped_deals_returning_ids(deals))
