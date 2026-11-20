from fastapi import APIRouter, Query, HTTPException, Body, Request, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import json
import logging
import os
import re
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from app.database import get_connection, get_system_setting

logger = logging.getLogger("immometrica.properties")
from app.calculator import calculate_cashflow
from app.valuation import evaluate_property_valuation, get_city_benchmark
from app.availability import check_portal_url_live
from app.ai_analyst import generate_ai_due_diligence_report, reconcile_capex_with_images, build_missing_documents_request
from app.image_analyst import analyze_property_images, refine_photo_tag
from app.geo_resolver import resolve_city_coordinates, ensure_distinct_coord, _match_city_coord
from app.location_analyst import check_address_is_exact, analyze_property_location_and_pois, clean_real_estate_address

router = APIRouter(prefix="/api/properties", tags=["properties"])


def _resolve_updated_geo(
    cursor,
    prop_id: int,
    cur_lat: Optional[float],
    cur_lng: Optional[float],
    cur_address: Optional[str],
    cur_plz: Optional[str],
    cur_city: Optional[str],
    live_res: Optional[Dict[str, Any]] = None,
    occupied_set: Optional[set] = None,
):
    """
    Liefert aktualisierte (lat, lng, address, postal_code):
    Bevorzugt verifizierte Straßenkoordinaten (Nominatim/Photon) vor groben Stadtteil-Centroiden,
    übernimmt echte Portal-Kartenkoordinaten bei bekannten Straßen
    und korrigiert veraltete Grob-PLZ-Koordinaten.
    """
    if occupied_set is None:
        cursor.execute(
            "SELECT id, lat, lng FROM properties WHERE id != ? AND lat IS NOT NULL AND lng IS NOT NULL",
            (prop_id,),
        )
        occupied_set = {(round(float(r[1]), 4), round(float(r[2]), 4)) for r in cursor.fetchall()}

    live_res = live_res or {}
    plz_val = live_res.get("postal_code") or cur_plz or ""
    city_val = cur_city or ""

    new_addr = clean_real_estate_address(cur_address, city=city_val, postal_code=plz_val)
    if live_res.get("street_address"):
        st = clean_real_estate_address(str(live_res["street_address"]), city=city_val, postal_code=plz_val)
        st_exact, st_prec = check_address_is_exact(st, city=city_val)
        cur_exact, cur_prec = check_address_is_exact(new_addr, city=city_val)
        if st and st_prec in ["EXACT_STREET_AND_NUMBER", "PROBABLE_EXACT", "STREET_ONLY_NO_NUMBER"]:
            if not new_addr or (st_exact and not cur_exact) or cur_prec == "DISTRICT_OR_CITY_ONLY":
                new_addr = f"{st}, {plz_val} {city_val}".strip(" ,")

    # If new_addr contains a 5-digit German postal code, adopt it as authoritative
    m_plz = re.search(r"\b(\d{5})\b", new_addr)
    if m_plz:
        plz_val = m_plz.group(1)

    _, prec_type = check_address_is_exact(new_addr, city=city_val)
    is_street_known = prec_type in ["EXACT_STREET_AND_NUMBER", "PROBABLE_EXACT", "STREET_ONLY_NO_NUMBER"]

    # If the street is known (from exposé or portal):
    # ALWAYS resolve and prefer the exact street coordinates!
    if is_street_known and (plz_val or city_val):
        street_lat, street_lng = resolve_city_coordinates(
            city=city_val, postal_code=plz_val, street=new_addr, index=prop_id, allow_geocoder=True
        )
        if street_lat is not None and street_lng is not None:
            # If the portal provided an exact verified street address with coordinates, keep portal coords;
            # otherwise (e.g. Kleinanzeigen approximate district centroid), use the street coordinates!
            portal_has_street = bool(live_res.get("street_address") and check_address_is_exact(str(live_res["street_address"]), city=city_val)[0])
            if portal_has_street and live_res.get("lat") is not None and live_res.get("lng") is not None:
                lat_u, lng_u = ensure_distinct_coord(float(live_res["lat"]), float(live_res["lng"]), occupied_set, seed=prop_id)
                return lat_u, lng_u, new_addr, plz_val
            lat_u, lng_u = ensure_distinct_coord(street_lat, street_lng, occupied_set, seed=prop_id)
            return lat_u, lng_u, new_addr, plz_val

    # If no street is known (district / city only):
    # 1. Use portal coordinates if available
    if live_res.get("lat") is not None and live_res.get("lng") is not None:
        lat_u, lng_u = ensure_distinct_coord(float(live_res["lat"]), float(live_res["lng"]), occupied_set, seed=prop_id)
        return lat_u, lng_u, new_addr, plz_val

    # 2. Check if existing coordinates are reasonable
    if cur_lat is not None and cur_lng is not None and (plz_val or city_val):
        expected_lat, expected_lng = resolve_city_coordinates(
            city=city_val, postal_code=plz_val, street=new_addr, index=prop_id, allow_geocoder=True
        )
        # Check if cur_lat/cur_lng is just the generic city centroid while expected is a specific district or street
        city_coords = _match_city_coord(city_val.strip().lower())
        is_cur_generic_city = False
        if city_coords:
            c_lat, c_lng = city_coords
            if abs(float(cur_lat) - c_lat) < 0.005 and abs(float(cur_lng) - c_lng) < 0.005:
                is_cur_generic_city = True

        if not is_cur_generic_city and abs(float(cur_lat) - expected_lat) <= 0.05 and abs(float(cur_lng) - expected_lng) <= 0.07:
            return float(cur_lat), float(cur_lng), new_addr, plz_val
        lat_u, lng_u = ensure_distinct_coord(expected_lat, expected_lng, occupied_set, seed=prop_id)
        return lat_u, lng_u, new_addr, plz_val

    expected_lat, expected_lng = resolve_city_coordinates(
        city=city_val, postal_code=plz_val, street=new_addr, index=prop_id, allow_geocoder=True
    )
    lat_u, lng_u = ensure_distinct_coord(expected_lat, expected_lng, occupied_set, seed=prop_id)
    return lat_u, lng_u, new_addr, plz_val

def extract_clean_listing_title(
    url: str,
    raw_title: str = "",
    description: str = "",
    city: str = "",
    rooms: float = 2.0,
    sqm: float = 60.0
) -> str:
    """
    Ensures properties NEVER have generic placeholder titles like 'Importiertes Inserat' or 'Beschreibung'.
    Extracts slug from URL, parses description, or constructs a crisp German real estate headline.
    """
    clean_t = (raw_title or "").strip()
    generic_placeholders = [
        "importiertes inserat", "inserat", "beschreibung", "expose",
        "kleinanzeigen inserat", "immowelt angebot", "immoscout inserat"
    ]
    if clean_t and clean_t.lower() not in generic_placeholders and len(clean_t) >= 6:
        return clean_t

    # 1. Try extracting slug from URL
    m = re.search(r"/(?:s-anzeige|expose)/([a-zA-Z0-9äöüÄÖÜß\-]+)(?:/\d+)?", url or "")
    if m:
        slug = m.group(1).strip("-")
        # Check if slug is just a GUID or hex
        if len(slug) > 8 and not re.match(r"^[a-f0-9\-]+$", slug, re.IGNORECASE):
            slug = slug.replace("-fuer-", "-für-").replace("-koerne", "-körne").replace("-rendit", "-rendite")
            words = [
                w.capitalize() if w.lower() not in ['zu', 'in', 'im', 'am', 'für', 'und', 'mit', 'nahe', 'der', 'die', 'das', 'den', 'vom', 'von'] else w.lower() 
                for w in slug.split('-') if w
            ]
            slug_title = " ".join(words)
            slug_title = re.sub(r"\b1 Zimmer\b", "1-Zimmer", slug_title, flags=re.IGNORECASE)
            slug_title = re.sub(r"\b2 Zimmer\b", "2-Zimmer", slug_title, flags=re.IGNORECASE)
            slug_title = re.sub(r"\b3 Zimmer\b", "3-Zimmer", slug_title, flags=re.IGNORECASE)
            slug_title = re.sub(r"\bTop Rendite\b", "Top-Rendite", slug_title, flags=re.IGNORECASE)
            if len(slug_title) >= 10:
                return slug_title[:100]

    # 2. Try extracting first meaningful sentence or headline from description
    if description:
        lines = [line.strip() for line in description.split("\n") if len(line.strip()) >= 15]
        for line in lines:
            if line.lower() in ["**beschreibung**", "beschreibung:", "objektbeschreibung", "beschreibung"]:
                continue
            cleaned_line = re.sub(r"^[\*\#\-\s]+", "", line).strip()
            if len(cleaned_line) >= 15 and not cleaned_line.lower().startswith("hallo") and not cleaned_line.lower().startswith("bitte "):
                return cleaned_line[:100]

    # 3. Construct professional title from location and size
    loc_part = f" in {city.strip()}" if city and city.strip() else ""
    room_str = f"{rooms:.0f}" if float(rooms or 2.0).is_integer() else f"{rooms:.1f}"
    sqm_str = f" ({sqm:.0f} m²)" if sqm and float(sqm) > 0 else ""
    return f"Gepflegte {room_str}-Zimmer-Wohnung{loc_part}{sqm_str}"

@router.get("")
def list_properties(
    q: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    property_type: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_yield: Optional[float] = None,
    yield_basis: str = Query("any", description="Yield filter basis: 'any' (Ist oder Mietspiegel-Potenzial), 'actual' (nur Ist-Rendite), 'potential' (nur Mietspiegel/Soll)"),
    min_cashflow: Optional[float] = None,
    positive_cashflow: Optional[bool] = None,
    undervalued_only: Optional[bool] = None,
    foreclosure_only: Optional[bool] = None,
    private_only: Optional[bool] = None,
    online_only: Optional[bool] = None,
    sort_by: str = "gross_yield",
    sort_order: str = "desc"
):
    conn = get_connection()
    cursor = conn.cursor()

    conditions = ["1=1"]
    params = []

    if q:
        conditions.append("(title LIKE ? OR address LIKE ? OR city LIKE ? OR postal_code LIKE ?)")
        wildcard = f"%{q}%"
        params.extend([wildcard, wildcard, wildcard, wildcard])

    if city:
        conditions.append("city LIKE ?")
        params.append(f"%{city}%")

    if state:
        conditions.append("state = ?")
        params.append(state)

    if property_type and property_type != "all":
        conditions.append("property_type = ?")
        params.append(property_type)

    if min_price is not None:
        conditions.append("purchase_price >= ?")
        params.append(min_price)

    if max_price is not None:
        conditions.append("purchase_price <= ?")
        params.append(max_price)

    if min_yield is not None:
        if yield_basis == "actual":
            conditions.append("gross_yield >= ?")
            params.append(min_yield)
        elif yield_basis == "potential":
            conditions.append("((expected_rent * 12.0 / NULLIF(purchase_price, 0)) * 100.0) >= ?")
            params.append(min_yield)
        else:  # "any" (Standard: Entweder aktuelle Ist-Rendite ODER Mietspiegel-Potenzial)
            conditions.append("(gross_yield >= ? OR ((expected_rent * 12.0 / NULLIF(purchase_price, 0)) * 100.0) >= ?)")
            params.extend([min_yield, min_yield])

    if min_cashflow is not None:
        conditions.append("monthly_cashflow >= ?")
        params.append(min_cashflow)

    if positive_cashflow:
        conditions.append("monthly_cashflow >= 0")

    if undervalued_only:
        conditions.append("market_deviation_percent < -5")

    if foreclosure_only:
        conditions.append("is_foreclosure = 1")

    if private_only:
        conditions.append("is_private = 1")

    if online_only:
        conditions.append("is_online = 1")

    allowed_sorts = {
        "gross_yield": "gross_yield",
        "potential_yield": "((expected_rent * 12.0 / NULLIF(purchase_price, 0)) * 100.0)",
        "monthly_cashflow": "monthly_cashflow",
        "purchase_price": "purchase_price",
        "living_space": "living_space",
        "price_per_sqm": "purchase_price / living_space",
        "days_online": "days_online",
        "market_deviation": "market_deviation_percent"
    }

    sort_column = allowed_sorts.get(sort_by, "gross_yield")
    direction = "ASC" if sort_order.lower() == "asc" else "DESC"

    query = f"""
    SELECT * FROM properties 
    WHERE {' AND '.join(conditions)}
    ORDER BY {sort_column} {direction}
    """

    cursor.execute(query, params)
    rows = cursor.fetchall()

    results = []
    for r in rows:
        d = dict(r)
        exp_r = float(d.get("expected_rent") or 0.0)
        p_price = float(d.get("purchase_price") or 0.0)
        d["potential_gross_yield"] = round((exp_r * 12.0 / p_price) * 100.0, 2) if p_price > 0 else 0.0
        d["tags"] = json.loads(d["tags"] or "[]")
        if d.get("ai_analysis"):
            try:
                d["ai_analysis"] = json.loads(d["ai_analysis"])
            except Exception:
                pass
        if d.get("image_analysis"):
            try:
                d["image_analysis"] = json.loads(d["image_analysis"])
            except Exception:
                pass
        if d.get("gallery_images"):
            try:
                d["gallery_images"] = json.loads(d["gallery_images"])
            except Exception:
                d["gallery_images"] = []
        results.append(d)

    conn.close()
    return {
        "total": len(results),
        "properties": results
    }

@router.get("/by-url")
def get_property_by_url(url: str = Query(...)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM properties WHERE portal_url = ?", (url,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Property not found for URL")
    return get_property(row[0])

@router.get("/{property_id}")
def get_property(property_id: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM properties WHERE id = ?", (property_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Property not found")

    prop = dict(row)
    prop["tags"] = json.loads(prop["tags"] or "[]")
    if prop.get("ai_analysis"):
        try:
            prop["ai_analysis"] = json.loads(prop["ai_analysis"])
            if isinstance(prop["ai_analysis"], dict) and (not prop["ai_analysis"].get("missing_documents") or not prop["ai_analysis"].get("doc_checklist")):
                doc_req = build_missing_documents_request(prop, prop["ai_analysis"])
                prop["ai_analysis"]["missing_documents"] = doc_req.get("missing_documents", [])
                prop["ai_analysis"]["doc_checklist"] = doc_req.get("missing_documents", [])
                prop["ai_analysis"]["document_request"] = doc_req
        except Exception:
            pass
    if prop.get("image_analysis"):
        try:
            prop["image_analysis"] = json.loads(prop["image_analysis"])
            if isinstance(prop["image_analysis"], dict):
                for p in (prop["image_analysis"].get("analyzed_photos") or []):
                    p["tag"] = refine_photo_tag(p.get("tag", ""), p.get("description", ""), p.get("findings", []))
        except Exception:
            pass
    if prop.get("gallery_images"):
        try:
            prop["gallery_images"] = json.loads(prop["gallery_images"])
        except Exception:
            prop["gallery_images"] = []

    cursor.execute("SELECT * FROM deal_notes WHERE property_id = ? ORDER BY created_at DESC", (property_id,))
    notes = [dict(n) for n in cursor.fetchall()]
    prop["notes"] = notes

    benchmark = get_city_benchmark(prop["city"])
    prop["city_benchmark"] = benchmark

    conn.close()
    return prop

@router.post("/{property_id}/check-availability")
def check_property_availability(property_id: int):
    """
    Actively checks whether the listing is still live and reachable on the portal.
    Updates the database with fresh verification status and extracts full description if available.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT id, portal_url, source_portal, city, postal_code, address, lat, lng, description FROM properties WHERE id = ?", (property_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Property not found")

    prop_id, portal_url, source_portal, city, plz, addr, cur_lat, cur_lng, existing_desc = row
    status_result = check_portal_url_live(portal_url, source_portal, city)

    is_online_val = 1 if status_result["is_online"] else 0
    new_desc = status_result.get("description") or existing_desc or ""
    new_lat, new_lng, new_addr, new_plz = _resolve_updated_geo(
        cursor, prop_id, cur_lat, cur_lng, addr, plz, city, live_res=status_result
    )

    cursor.execute("""
        UPDATE properties 
        SET is_online = ?, availability_status = ?, status_message = ?, last_checked_at = ?, live_search_url = ?, description = ?,
            lat = ?, lng = ?, address = ?, postal_code = COALESCE(NULLIF(?, ''), postal_code)
        WHERE id = ?
    """, (
        is_online_val,
        status_result["availability_status"],
        status_result["status_message"],
        status_result["last_checked_at"],
        status_result["live_search_url"],
        new_desc,
        new_lat,
        new_lng,
        new_addr,
        new_plz,
        prop_id
    ))
    conn.commit()
    conn.close()

    return {
        "property_id": prop_id,
        "is_online": status_result["is_online"],
        "availability_status": status_result["availability_status"],
        "status_message": status_result["status_message"],
        "last_checked_at": status_result["last_checked_at"],
        "live_search_url": status_result["live_search_url"],
        "has_description": bool(new_desc),
        "lat": new_lat,
        "lng": new_lng,
        "address": new_addr
    }

@router.post("/check-all-availability")
def check_all_properties_availability():
    """
    Batch-verifies all active property listings to guarantee that displayed deals are still live
    and synchronizes accurate map coordinates directly from the portals.
    Uses ThreadPoolExecutor for fast parallel portal verification, then merges
    geo-resolution and database updates cleanly in the main thread.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, portal_url, source_portal, city, postal_code, address, lat, lng, description FROM properties")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return {
            "total_checked": 0,
            "online_count": 0,
            "offline_count": 0,
            "details": []
        }

    def _check_task(item):
        prop_id, portal_url, source_portal, city, plz, addr, cur_lat, cur_lng, existing_desc = item
        try:
            status = check_portal_url_live(portal_url, source_portal, city)
        except Exception as e:
            logger.error(f"Fehler bei Verfügbarkeitsprüfung für Objekt {prop_id}: {e}")
            status = {
                "is_online": True,
                "availability_status": "online",
                "status_message": f"Prüfung fehlgeschlagen: {e}",
                "last_checked_at": datetime.now().isoformat(),
                "live_search_url": portal_url,
                "description": "",
            }
        return item, status

    # Parallel live network verification across portals (max 8 workers)
    max_workers = min(len(rows), 8) or 1
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        raw_results = list(pool.map(_check_task, rows))

    # Sequential coordinate resolution and atomic SQLite update on main thread
    conn = get_connection()
    cursor = conn.cursor()

    checked_results = []
    online_count = 0
    offline_count = 0
    occupied_coords = set()

    for item, status in raw_results:
        prop_id, portal_url, source_portal, city, plz, addr, cur_lat, cur_lng, existing_desc = item
        is_online_bool = bool(status.get("is_online"))
        is_online_val = 1 if is_online_bool else 0
        if is_online_bool:
            online_count += 1
        else:
            offline_count += 1

        new_desc = status.get("description") or existing_desc or ""
        new_lat, new_lng, new_addr, new_plz = _resolve_updated_geo(
            cursor, prop_id, cur_lat, cur_lng, addr, plz, city, live_res=status, occupied_set=occupied_coords
        )

        cursor.execute("""
            UPDATE properties 
            SET is_online = ?, availability_status = ?, status_message = ?, last_checked_at = ?, live_search_url = ?, description = ?,
                lat = ?, lng = ?, address = ?, postal_code = COALESCE(NULLIF(?, ''), postal_code)
            WHERE id = ?
        """, (
            is_online_val,
            status.get("availability_status", "unknown"),
            status.get("status_message", ""),
            status.get("last_checked_at"),
            status.get("live_search_url", ""),
            new_desc,
            new_lat,
            new_lng,
            new_addr,
            new_plz,
            prop_id
        ))

        checked_results.append({
            "id": prop_id,
            "is_online": is_online_bool,
            "status_message": status.get("status_message", ""),
            "lat": new_lat,
            "lng": new_lng
        })

    conn.commit()
    conn.close()

    return {
        "total_checked": len(checked_results),
        "online_count": online_count,
        "offline_count": offline_count,
        "details": checked_results
    }

@router.post("/{property_id}/holistic-audit")
@router.post("/{property_id}/ai-audit")
def run_property_ai_audit(
    property_id: int,
    force_refresh: bool = False,
    skip_images: bool = False,
    request: Request = None,
    payload: Optional[Dict[str, Any]] = Body(default=None)
):
    """
    Ganzheitliche Exposé-Prüfung (Zahlen, Text & Bilder Fusion) nach 3-Stufen-Gating:
    1. STUFE 1 (Hard Knockout): Inserat offline oder 0 Zeichen Text -> Sofortiger Abbruch (Score 1.0/2.0, 0 Token).
    2. STUFE 2 (Quantitativ & Semantisch): Zahlen- und Text-Analyse (WEG, Hausgeld, GEG, Rendite).
    3. STUFE 3 (Smart Vision Gate): Bilder-KI nur bei kaufenswerten/Value-Add Deals (Vollständige Galerie-Analyse aller Bilder).
    4. STUFE 4 (Holistic Verdict): Ganzheitliche Entscheidung mit All-In Rentabilität und Value-Add Bonus.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM properties WHERE id = ?", (property_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Property not found")

    prop = dict(row)
    description = prop.get("description") or ""
    fetched_details: Dict[str, Any] = {}

    # Resolve optional API key (body -> header -> system setting -> env)
    api_key = None
    if payload and isinstance(payload, dict):
        api_key = payload.get("api_key")
        if "skip_images" in payload:
            skip_images = bool(payload.get("skip_images"))
    if not api_key and request:
        api_key = request.headers.get("X-Gemini-API-Key")
    if api_key and ("..." in api_key or "***" in api_key or len(api_key.strip()) < 15):
        api_key = None
    if not api_key:
        active_prov = get_system_setting("active_ai_provider")
        if active_prov == "oneprovider":
            api_key = get_system_setting("oneprovider_api_key") or get_system_setting("gemini_api_key")
        elif active_prov == "gemini":
            api_key = get_system_setting("google_gemini_api_key") or get_system_setting("gemini_api_key")
        else:
            api_key = get_system_setting("oneprovider_api_key") or get_system_setting("google_gemini_api_key") or get_system_setting("gemini_api_key")
    if not api_key:
        api_key = os.getenv("ONEPROVIDER_API_KEY") or os.getenv("GEMINI_API_KEY")

    existing_img_json = prop.get("image_analysis")
    img_report = None
    if existing_img_json:
        try:
            img_report = json.loads(existing_img_json)
        except Exception:
            img_report = None

    # Reuse up-to-date LLM-v2 audit when force_refresh is False and LLM capability hasn't upgraded
    existing_ai = None
    if prop.get("ai_analysis"):
        try:
            existing_ai = json.loads(prop["ai_analysis"])
        except Exception:
            existing_ai = None

    # Invalidate stale audit that erroneously gave high/green score to an offline or empty property
    is_offline_now = (prop.get("is_online", 1) == 0 or str(prop.get("availability_status", "")).lower() == "offline")
    desc_len_now = len((prop.get("description") or "").strip())
    if (is_offline_now or desc_len_now < 50) and existing_ai and existing_ai.get("score", 0.0) > 2.0:
        existing_ai = None

    has_llm_key = bool(api_key and str(api_key).strip())
    if (
        not force_refresh
        and existing_ai
        and existing_ai.get("engine_version") == "llm-v2"
        and (existing_ai.get("used_llm") or not has_llm_key)
    ):
        conn.close()
        if not existing_ai.get("missing_documents") or not existing_ai.get("doc_checklist"):
            doc_req = build_missing_documents_request(prop, existing_ai)
            existing_ai["missing_documents"] = doc_req.get("missing_documents", [])
            existing_ai["doc_checklist"] = doc_req.get("missing_documents", [])
            existing_ai["document_request"] = doc_req
        if img_report and isinstance(img_report, dict):
            for p in (img_report.get("analyzed_photos") or []):
                p["tag"] = refine_photo_tag(p.get("tag", ""), p.get("description", ""), p.get("findings", []))
        ins = existing_ai.get("insights", {})
        met = existing_ai.get("metrics", {})
        return {
            "property_id": property_id,
            "ai_audit": existing_ai,
            "image_analysis": img_report,
            "missing_documents": existing_ai.get("missing_documents", []),
            "doc_checklist": existing_ai.get("doc_checklist", []),
            "document_request": existing_ai.get("document_request"),
            "holistic_verdict": existing_ai.get("status_label"),
            "property_updates": {
                "current_rent": prop.get("current_rent", met.get("current_monthly_rent", 0.0)),
                "expected_rent": prop.get("expected_rent", met.get("expected_monthly_rent", 0.0)),
                "gross_yield": prop.get("gross_yield", met.get("gross_yield", 0.0)),
                "monthly_cashflow": prop.get("monthly_cashflow", met.get("monthly_cashflow", 0.0)),
                "is_rented": prop.get("is_rented", 0),
                "is_private": prop.get("is_private", 0),
                "commission_percent": prop.get("commission_percent", 3.57),
                "city": prop.get("city"),
                "address": prop.get("address"),
                "property_type": prop.get("property_type"),
                "year_built": prop.get("year_built"),
                "land_area": prop.get("land_area", 0.0),
                "parcel_area": prop.get("parcel_area", 0.0),
                "parcel_id": prop.get("parcel_id"),
                "market_value": prop.get("market_value"),
                "market_value_per_sqm": prop.get("market_value_per_sqm"),
                "market_deviation_percent": prop.get("market_deviation_percent")
            }
        }

    fetch_res: Optional[Dict[str, Any]] = None
    fetched_details: Dict[str, Any] = {}
    # Beim ersten KI-Audit (wenn noch kein ai_analysis in der Datenbank vorliegt),
    # bei fehlender description oder bei explizitem force_refresh:
    # immer die vollständigen Exposé-Daten und alle Galeriebilder live von der Portal-URL laden.
    has_existing_ai = bool(prop.get("ai_analysis"))
    if not description or force_refresh or not has_existing_ai:
        portal_url = prop.get("portal_url", "")
        source_portal = prop.get("source_portal", "")
        city = prop.get("city", "")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if "datetime" in globals() else ""
        from datetime import datetime as dt_cls
        if not now_str:
            now_str = dt_cls.now().strftime("%Y-%m-%d %H:%M:%S")

        if portal_url:
            if "immobilienscout24.de" in portal_url.lower() or source_portal == "ImmoScout24":
                from app.scrapers.immoscout import fetch_immoscout_expose
                scout_res = fetch_immoscout_expose(portal_url)
                if scout_res.get("success"):
                    fetched_desc = scout_res.get("description", "")
                    fetched_details = scout_res.get("details") or {}
                    if fetched_desc:
                        description = fetched_desc
                        prop["description"] = description

                    update_fields = [
                        "description = ?", "is_online = 1", "availability_status = 'online'",
                        "status_message = 'Live erreichbar & aktiv (ImmoScout24 API verifiziert)'",
                        "last_checked_at = ?"
                    ]
                    update_vals = [description, now_str]

                    # Gallery pictures from live expose
                    scout_pics = scout_res.get("pictures") or []
                    if scout_pics:
                        cur_gallery = []
                        if prop.get("gallery_images"):
                            try:
                                cur_gallery = json.loads(prop["gallery_images"]) if isinstance(prop["gallery_images"], str) else prop["gallery_images"]
                            except Exception:
                                cur_gallery = []
                        if len(scout_pics) > len(cur_gallery or []):
                            scout_gallery_json = json.dumps(scout_pics, ensure_ascii=False)
                            update_fields.append("gallery_images = ?")
                            update_vals.append(scout_gallery_json)
                            prop["gallery_images"] = scout_gallery_json
                            cur_img = prop.get("image_url") or ""
                            if not cur_img or "unsplash" in cur_img or len(cur_img) < 20:
                                update_fields.append("image_url = ?")
                                update_vals.append(scout_pics[0])
                                prop["image_url"] = scout_pics[0]

                    # Auto-fix title if it was "Beschreibung"
                    cur_title = (prop.get("title") or "").strip()
                    if (not cur_title or cur_title.lower() == "beschreibung") and scout_res.get("title"):
                        update_fields.append("title = ?")
                        update_vals.append(scout_res["title"])
                        prop["title"] = scout_res["title"]

                    # Update price / space / rooms if they were missing or default
                    if scout_res.get("price", 0) > 0 and (prop.get("purchase_price", 0) <= 0 or force_refresh or abs(scout_res["price"] - prop.get("purchase_price", 0)) > 1.0):
                        update_fields.append("purchase_price = ?")
                        update_vals.append(scout_res["price"])
                        prop["purchase_price"] = scout_res["price"]
                    if scout_res.get("living_space", 0) > 0 and prop.get("living_space", 0) <= 0:
                        update_fields.append("living_space = ?")
                        update_vals.append(scout_res["living_space"])
                        prop["living_space"] = scout_res["living_space"]
                    if scout_res.get("rooms", 0) > 0 and prop.get("rooms", 0) <= 0:
                        update_fields.append("rooms = ?")
                        update_vals.append(scout_res["rooms"])
                        prop["rooms"] = scout_res["rooms"]
                    scout_addr = (scout_res.get("address") or "").strip()
                    if scout_addr:
                        scout_is_exact, _ = check_address_is_exact(scout_addr, city=prop.get("city"))
                        cur_addr = (prop.get("address") or "").strip()
                        cur_is_exact, _ = check_address_is_exact(cur_addr, city=prop.get("city"))
                        if not cur_addr or cur_addr.startswith("Zentrum") or (scout_is_exact and not cur_is_exact):
                            update_fields.append("address = ?")
                            update_vals.append(scout_addr)
                            prop["address"] = scout_addr

                    update_vals.append(property_id)
                    cursor.execute(f"UPDATE properties SET {', '.join(update_fields)} WHERE id = ?", tuple(update_vals))
                    conn.commit()

                    fetch_res = {
                        "is_online": True,
                        "availability_status": "online",
                        "status_message": "Live erreichbar & aktiv (ImmoScout24 API verifiziert)",
                        "last_checked_at": now_str,
                        "description": description,
                        "details": fetched_details,
                        "lat": scout_res.get("lat"),
                        "lng": scout_res.get("lng"),
                        "street_address": scout_res.get("address")
                    }

            elif "immowelt.de" in portal_url.lower() or source_portal == "Immowelt":
                from app.scrapers.immowelt import fetch_immowelt_expose
                iw_res = fetch_immowelt_expose(portal_url)
                if iw_res.get("success"):
                    fetched_desc = iw_res.get("description", "")
                    fetched_details = iw_res.get("details") or {}
                    if iw_res.get("energy_certificate"):
                        fetched_details["energy_certificate"] = iw_res["energy_certificate"]
                    if iw_res.get("year_built"):
                        fetched_details["year_built"] = iw_res["year_built"]

                    if fetched_desc:
                        description = fetched_desc
                        prop["description"] = description

                    iw_price = float(iw_res.get("price") or 0.0)
                    upd_fields = ["description = ?", "is_online = 1", "availability_status = 'online'", "status_message = 'Live erreichbar & aktiv (Immowelt verifiziert)'", "last_checked_at = ?"]
                    upd_vals = [description, now_str]
                    if iw_price > 0 and (prop.get("purchase_price", 0) <= 0 or force_refresh or abs(iw_price - prop.get("purchase_price", 0)) > 1.0):
                        upd_fields.append("purchase_price = ?")
                        upd_vals.append(iw_price)
                        prop["purchase_price"] = iw_price

                    if iw_res.get("images") and len(iw_res["images"]) > 0:
                        iw_gallery_json = json.dumps(iw_res["images"], ensure_ascii=False)
                        upd_fields.append("gallery_images = ?")
                        upd_vals.append(iw_gallery_json)
                        prop["gallery_images"] = iw_gallery_json
                        cur_img = prop.get("image_url") or ""
                        if not cur_img or "unsplash" in cur_img or "623472ee" in cur_img or len(cur_img) < 20:
                            upd_fields.append("image_url = ?")
                            upd_vals.append(iw_res["images"][0])
                            prop["image_url"] = iw_res["images"][0]

                    if iw_res.get("year_built") and (not prop.get("year_built") or prop.get("year_built") == 1980):
                        upd_fields.append("year_built = ?")
                        upd_vals.append(iw_res["year_built"])
                        prop["year_built"] = iw_res["year_built"]

                    if iw_res.get("living_space") and prop.get("living_space", 0) <= 0:
                        upd_fields.append("living_space = ?")
                        upd_vals.append(iw_res["living_space"])
                        prop["living_space"] = iw_res["living_space"]

                    if iw_res.get("rooms") and prop.get("rooms", 0) <= 0:
                        upd_fields.append("rooms = ?")
                        upd_vals.append(iw_res["rooms"])
                        prop["rooms"] = iw_res["rooms"]

                    cur_title = (prop.get("title") or "").strip()
                    if (not cur_title or cur_title.lower() in ["beschreibung", "importiertes inserat"]) and iw_res.get("title"):
                        upd_fields.append("title = ?")
                        upd_vals.append(iw_res["title"])
                        prop["title"] = iw_res["title"]

                    upd_vals.append(property_id)
                    cursor.execute(f"UPDATE properties SET {', '.join(upd_fields)} WHERE id = ?", tuple(upd_vals))
                    conn.commit()

                    fetch_res = {
                        "is_online": True,
                        "availability_status": "online",
                        "status_message": "Live erreichbar & aktiv (Immowelt verifiziert)",
                        "last_checked_at": now_str,
                        "description": description,
                        "details": fetched_details
                    }

            if not fetch_res:
                fetch_res = check_portal_url_live(portal_url, source_portal, city)
                fetched_desc = fetch_res.get("description", "")
                fetched_details = fetch_res.get("details") or {}
                if fetched_desc:
                    description = fetched_desc
                    prop["description"] = description
                    is_online_val = 1 if fetch_res["is_online"] else 0
                    
                    # Synchronize live portal price if available
                    live_price = float(fetch_res.get("price") or 0.0)
                    upd_fields = [
                        "description = ?", "is_online = ?", "availability_status = ?", 
                        "status_message = ?", "last_checked_at = ?"
                    ]
                    upd_vals = [
                        description,
                        is_online_val,
                        fetch_res["availability_status"],
                        fetch_res["status_message"],
                        fetch_res["last_checked_at"]
                    ]
                    if live_price > 0 and (prop.get("purchase_price", 0) <= 0 or force_refresh or abs(live_price - prop.get("purchase_price", 0)) > 1.0):
                        upd_fields.append("purchase_price = ?")
                        upd_vals.append(live_price)
                        prop["purchase_price"] = live_price

                    # Extract full gallery for Kleinanzeigen / portal fallback
                    from app.image_analyst import extract_gallery_images
                    cur_gallery = []
                    if prop.get("gallery_images"):
                        try:
                            cur_gallery = json.loads(prop["gallery_images"]) if isinstance(prop["gallery_images"], str) else prop["gallery_images"]
                        except Exception:
                            cur_gallery = []
                    if len(cur_gallery or []) < 10:
                        live_gallery = extract_gallery_images(portal_url, source_portal, prop.get("image_url", ""))
                        if len(live_gallery) > len(cur_gallery or []):
                            upd_fields.append("gallery_images = ?")
                            upd_vals.append(json.dumps(live_gallery, ensure_ascii=False))
                            prop["gallery_images"] = json.dumps(live_gallery, ensure_ascii=False)
                            cur_img = prop.get("image_url") or ""
                            if not cur_img or "unsplash" in cur_img or len(cur_img) < 20:
                                upd_fields.append("image_url = ?")
                                upd_vals.append(live_gallery[0])
                                prop["image_url"] = live_gallery[0]

                    upd_vals.append(property_id)
                    cursor.execute(f"UPDATE properties SET {', '.join(upd_fields)} WHERE id = ?", tuple(upd_vals))
                    conn.commit()

    # Auto-fix title if it is a generic placeholder like "Importiertes Inserat" or "Beschreibung"
    cur_t = (prop.get("title") or "").strip()
    generic_placeholders = ["importiertes inserat", "beschreibung", "inserat", "expose", "immowelt angebot", "kleinanzeigen inserat"]
    if not cur_t or cur_t.lower() in generic_placeholders:
        fixed_t = extract_clean_listing_title(
            portal_url,
            raw_title=cur_t,
            description=description,
            city=prop.get("city", ""),
            rooms=prop.get("rooms", 2.0),
            sqm=prop.get("living_space", 50.0)
        )
        if fixed_t and fixed_t.lower() not in generic_placeholders:
            prop["title"] = fixed_t
            cursor.execute("UPDATE properties SET title = ? WHERE id = ?", (fixed_t, property_id))
            conn.commit()

    desc_clean = (description or "").strip()
    is_offline = (prop.get("is_online", 1) == 0 or str(prop.get("availability_status", "")).lower() == "offline")
    portal_url_check = str(prop.get("portal_url") or "").lower()
    is_search_url = bool(re.search(r"/(?:suche|search|s-immobilien|s-wohnung-kaufen|s-haus-kaufen)\b", portal_url_check))
    is_missing_desc = (len(desc_clean) < 50 or is_search_url)

    # Stage 1 Knockout: If listing is offline or lacks description, abort immediately with 0 tokens / 0 Vision calls
    if is_offline or is_missing_desc:
        report = generate_ai_due_diligence_report(prop, desc_clean, details=fetched_details, api_key=api_key, image_analysis=None)
        doc_request = build_missing_documents_request(prop, report)
        report["missing_documents"] = doc_request.get("missing_documents", [])
        report["doc_checklist"] = doc_request.get("missing_documents", [])
        report["document_request"] = doc_request
        cursor.execute("UPDATE properties SET ai_analysis = ? WHERE id = ?", (json.dumps(report, ensure_ascii=False), property_id))
        conn.commit()
        conn.close()
        return {
            "property_id": property_id,
            "ai_audit": report,
            "image_analysis": None,
            "missing_documents": report.get("missing_documents", []),
            "doc_checklist": report.get("doc_checklist", []),
            "document_request": doc_request,
            "holistic_verdict": report.get("status_label"),
            "property_updates": {}
        }

    # Stufe 1: Reine Text- & Zahlen-Due-Diligence (0 Vision-Tokens, nutzt vorhandene Bilddaten falls bereits vorliegend)
    report = generate_ai_due_diligence_report(prop, desc_clean, details=fetched_details, api_key=api_key, image_analysis=img_report)

    # Stufe 2: Smart Vision Gating (Prüfung: Ist die Immobilie laut Textanalyse kaufenswert / interessant?)
    text_score = float(report.get("score") or 0.0)
    text_status = str(report.get("status") or "").upper()
    has_critical_trap = any(
        r.get("severity") == "CRITICAL"
        or any(k in str(r.get("title", "")).lower() or k in str(r.get("description", "")).lower() for k in ["erbbaurecht", "erbpacht"])
        for r in (report.get("risks") or [])
    )
    is_interesting = (text_score >= 5.0 or text_status in ["GREEN", "YELLOW"]) and not has_critical_trap

    # Stufe 3: Vollständige KI-Bildanalyse aller Fotos (nur für qualifizierte Deals)
    from app.image_analyst import analyze_property_images
    if not skip_images and is_interesting and (not img_report or force_refresh):
        try:
            img_report = analyze_property_images(prop, full_description=desc_clean, api_key=api_key)
            if img_report and not img_report.get("error"):
                prop["image_analysis"] = json.dumps(img_report, ensure_ascii=False)
                cursor.execute("UPDATE properties SET image_analysis = ? WHERE id = ?", (prop["image_analysis"], property_id))
                analyzed_urls = [p["url"] for p in img_report.get("analyzed_photos", [])]
                if analyzed_urls:
                    prop["gallery_images"] = json.dumps(analyzed_urls, ensure_ascii=False)
                    cursor.execute("UPDATE properties SET gallery_images = ? WHERE id = ?", (prop["gallery_images"], property_id))
                conn.commit()
        except Exception as e:
            logger.warning(f"Error during auto image analysis for property {property_id}: {e}")

    # Stufe 4: Abschließende ganzheitliche Bewertung (Zahlen, Text & Bilder Fusion)
    # Erst nachdem alle Fotos analysiert vorliegen, werden CaPEx, All-In Rentabilität & Gesamturteil finalisiert!
    if img_report:
        report = reconcile_capex_with_images(report, prop, img_report)
        report["image_analysis"] = img_report
        if img_report.get("total_visual_capex", 0) > 0:
            report["capex"]["visual_capex"] = img_report["total_visual_capex"]
            report["capex"]["visual_items"] = img_report["visual_capex_items"]
        if isinstance(img_report, dict):
            for p in (img_report.get("analyzed_photos") or []):
                p["tag"] = refine_photo_tag(p.get("tag", ""), p.get("description", ""), p.get("findings", []))
    else:
        report["image_analysis"] = None

    # Re-run document request to reflect full image analysis (e.g. blueprint presence)
    doc_req = build_missing_documents_request(prop, report)
    report["missing_documents"] = doc_req.get("missing_documents", [])
    report["doc_checklist"] = doc_req.get("missing_documents", [])
    report["document_request"] = doc_req

    insights = report["insights"]
    metrics = report["metrics"]

    is_rented_val = 1 if metrics.get("is_rented", insights.get("is_rented", False)) else 0
    is_private_val = 1 if insights.get("is_private") else 0
    comm_pct_val = 0.0 if insights.get("commission_free") else float(insights.get("commission_percent", prop.get("commission_percent", 3.57)))
    cur_rent_val = float(metrics["current_monthly_rent"])
    exp_rent_val = float(metrics["expected_monthly_rent"])
    gross_yield_val = float(metrics["gross_yield"])
    cashflow_val = float(metrics["monthly_cashflow"])

    # Sync refined metadata extracted from exposé text & live portal geo coordinates
    new_city = insights.get("corrected_city") or prop.get("city")
    district = insights.get("district")
    cur_addr = clean_real_estate_address(prop.get("address"), city=new_city, postal_code=prop.get("postal_code"), district=district)
    cand_addr = clean_real_estate_address(insights.get("corrected_address"), city=new_city, postal_code=prop.get("postal_code"), district=district)
    cur_exact, cur_prec = check_address_is_exact(cur_addr, city=new_city)
    cand_exact, cand_prec = check_address_is_exact(cand_addr, city=new_city)

    if cur_exact and not cand_exact:
        new_address = cur_addr
    elif cand_prec in ["EXACT_STREET_AND_NUMBER", "PROBABLE_EXACT", "STREET_ONLY_NO_NUMBER"]:
        new_address = cand_addr
    elif cur_prec in ["EXACT_STREET_AND_NUMBER", "PROBABLE_EXACT", "STREET_ONLY_NO_NUMBER"]:
        new_address = cur_addr
    else:
        new_address = cand_addr or cur_addr

    cand_plz = None
    m_plz = re.search(r"\b(\d{5})\b", new_address)
    if m_plz:
        cand_plz = m_plz.group(1)

    new_lat, new_lng, new_address, new_plz = _resolve_updated_geo(
        cursor,
        property_id,
        prop.get("lat"),
        prop.get("lng"),
        new_address,
        cand_plz or prop.get("postal_code"),
        new_city,
        live_res=fetch_res,
    )

    new_address = clean_real_estate_address(new_address, city=new_city, postal_code=new_plz, district=district)
    prop["lat"] = new_lat
    prop["lng"] = new_lng
    prop["address"] = new_address
    prop["postal_code"] = new_plz
    prop["city"] = new_city
    insights["corrected_address"] = new_address
    insights["postal_code"] = new_plz
    insights["corrected_city"] = new_city

    # Recalculate micro-location analysis and Google Maps links with verified address & coordinates
    report["location_analysis"] = analyze_property_location_and_pois(
        prop,
        full_description=desc_clean,
        insights=insights
    )
    new_prop_type = insights.get("corrected_property_type") or prop.get("property_type")
    new_year_built = int(insights["year_built_extracted"]) if insights.get("year_built_extracted") else prop.get("year_built")

    # Property plot/land area cleanup: only real land_area for houses/plots, clear synthetic placeholders for ETWs
    is_apt_type = any(k in (new_prop_type or "").lower() for k in ["wohnung", "paket", "etw", "apartment", "maisonette", "penthouse", "loft"])
    if insights.get("land_area_extracted") and not is_apt_type:
        new_land_area = float(insights["land_area_extracted"])
        new_parcel_area = new_land_area
        new_parcel_id = prop.get("parcel_id")
    elif is_apt_type:
        new_land_area = 0.0
        new_parcel_area = 0.0
        cur_pid = prop.get("parcel_id")
        new_parcel_id = None if (not cur_pid or str(cur_pid).startswith("KAT-")) else cur_pid
    else:
        new_land_area = float(prop.get("land_area") or 0.0)
        new_parcel_area = float(prop.get("parcel_area") or 0.0)
        cur_pid = prop.get("parcel_id")
        new_parcel_id = None if (not cur_pid or str(cur_pid).startswith("KAT-")) else cur_pid

    new_market_value = float(metrics.get("market_value") or prop.get("market_value") or 0.0)
    new_market_value_sqm = float(metrics.get("market_value_per_sqm") or prop.get("market_value_per_sqm") or 0.0)
    new_market_dev = float(metrics.get("market_deviation_percent") if metrics.get("market_deviation_percent") is not None else (prop.get("market_deviation_percent") or 0.0))

    img_json = json.dumps(img_report, ensure_ascii=False) if img_report else existing_img_json
    gallery_json = prop.get("gallery_images")

    # Synchronize all verified insights, metadata, geo coordinates and metrics back into DB
    prop_price_val = float(prop.get("purchase_price", 0.0))
    cursor.execute("""
        UPDATE properties 
        SET ai_analysis = ?,
            image_analysis = ?,
            gallery_images = ?,
            purchase_price = ?,
            current_rent = ?,
            expected_rent = ?,
            gross_yield = ?,
            monthly_cashflow = ?,
            is_rented = ?,
            is_private = ?,
            commission_percent = ?,
            city = ?,
            address = ?,
            postal_code = COALESCE(NULLIF(?, ''), postal_code),
            lat = ?,
            lng = ?,
            property_type = ?,
            year_built = ?,
            land_area = ?,
            parcel_area = ?,
            parcel_id = ?,
            market_value = ?,
            market_value_per_sqm = ?,
            market_deviation_percent = ?
        WHERE id = ?
    """, (
        json.dumps(report, ensure_ascii=False),
        img_json,
        gallery_json,
        prop_price_val,
        cur_rent_val,
        exp_rent_val,
        gross_yield_val,
        cashflow_val,
        is_rented_val,
        is_private_val,
        comm_pct_val,
        new_city,
        new_address,
        new_plz,
        new_lat,
        new_lng,
        new_prop_type,
        new_year_built,
        new_land_area,
        new_parcel_area,
        new_parcel_id,
        new_market_value,
        new_market_value_sqm,
        new_market_dev,
        property_id
    ))

    # Keep deal_alerts table synchronized with verified audited metrics
    cursor.execute("""
        UPDATE deal_alerts
        SET monthly_cashflow = ?,
            gross_yield = ?,
            market_discount_pct = ?
        WHERE property_id = ?
    """, (cashflow_val, gross_yield_val, new_market_dev, property_id))

    conn.commit()
    conn.close()

    return {
        "property_id": property_id,
        "ai_audit": report,
        "image_analysis": img_report,
        "missing_documents": report.get("missing_documents", []),
        "doc_checklist": report.get("doc_checklist", []),
        "document_request": report.get("document_request"),
        "property_updates": {
            "purchase_price": prop_price_val,
            "current_rent": cur_rent_val,
            "expected_rent": exp_rent_val,
            "gross_yield": gross_yield_val,
            "monthly_cashflow": cashflow_val,
            "is_rented": is_rented_val,
            "is_private": is_private_val,
            "commission_percent": comm_pct_val,
            "city": new_city,
            "address": new_address,
            "lat": new_lat,
            "lng": new_lng,
            "property_type": new_prop_type,
            "year_built": new_year_built,
            "land_area": new_land_area,
            "parcel_area": new_parcel_area,
            "parcel_id": new_parcel_id,
            "market_value": new_market_value,
            "market_value_per_sqm": new_market_value_sqm,
            "market_deviation_percent": new_market_dev
        }
    }


class UpdatePriceRequest(BaseModel):
    purchase_price: float


@router.post("/{property_id}/update-price")
def update_property_purchase_price(property_id: int, payload: UpdatePriceRequest):
    """
    Manually or semi-automatically adjusts the property purchase price
    (e.g. to resolve an exposé text discrepancy) and re-runs the full AI audit & financial metrics.
    """
    if payload.purchase_price <= 0:
        raise HTTPException(status_code=400, detail="Ungültiger Kaufpreis")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE properties SET purchase_price = ? WHERE id = ?", (payload.purchase_price, property_id))
    conn.commit()
    conn.close()

    # Immediately re-run audit with fresh price to recalculate all yields & cashflows
    return run_property_ai_audit(property_id, force_refresh=True)

@router.post("/{property_id}/analyze-images")
def run_property_image_analysis(
    property_id: int, 
    request: Request = None, 
    payload: Optional[Dict[str, Any]] = Body(default=None)
):
    """
    Performs AI Computer Vision & Multimodal Image Analysis on the property photos on demand.
    Requires/uses a Gemini API-Key (from request body, header X-Gemini-API-Key, system settings, or env).
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM properties WHERE id = ?", (property_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Property not found")

    prop = dict(row)
    description = prop.get("description") or ""

    # Extract API key
    api_key = None
    if payload and isinstance(payload, dict):
        api_key = payload.get("api_key")
    if not api_key and request:
        api_key = request.headers.get("X-Gemini-API-Key")
    if api_key and ("..." in api_key or "***" in api_key or len(api_key.strip()) < 15):
        api_key = None
    if not api_key:
        active_prov = get_system_setting("active_ai_provider")
        if active_prov == "oneprovider":
            api_key = get_system_setting("oneprovider_api_key") or get_system_setting("gemini_api_key")
        elif active_prov == "gemini":
            api_key = get_system_setting("google_gemini_api_key") or get_system_setting("gemini_api_key")
        else:
            api_key = get_system_setting("oneprovider_api_key") or get_system_setting("google_gemini_api_key") or get_system_setting("gemini_api_key")
    if not api_key:
        api_key = os.getenv("ONEPROVIDER_API_KEY") or os.getenv("GEMINI_API_KEY")

    img_report = analyze_property_images(prop, full_description=description, api_key=api_key)

    if isinstance(img_report, dict) and (img_report.get("error") or img_report.get("vision_failed")):
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=img_report.get("error_message") or img_report.get("vision_error") or "Fehler bei der KI-Bildanalyse"
        )

    if isinstance(img_report, dict) and "analyzed_photos" in img_report:
        for p in img_report["analyzed_photos"]:
            p["tag"] = refine_photo_tag(p.get("tag", ""), p.get("description", ""), p.get("findings", []))

    img_json = json.dumps(img_report, ensure_ascii=False)
    gallery_json = json.dumps([p["url"] for p in img_report.get("analyzed_photos", [])], ensure_ascii=False)

    # Also update ai_analysis if present (inkl. CaPEx-Abgleich mit neu geprüften Fotos & Unterlagen-Checkliste!)
    ai_analysis_val = prop.get("ai_analysis")
    updated_ai_obj = None
    if ai_analysis_val:
        try:
            ai_data = json.loads(ai_analysis_val)
            ai_data = reconcile_capex_with_images(ai_data, prop, img_report)
            doc_req = build_missing_documents_request(prop, ai_data)
            ai_data["missing_documents"] = doc_req.get("missing_documents", [])
            ai_data["doc_checklist"] = doc_req.get("missing_documents", [])
            ai_data["document_request"] = doc_req
            updated_ai_obj = ai_data
            ai_analysis_val = json.dumps(ai_data, ensure_ascii=False)
        except Exception:
            pass

    cursor.execute("""
        UPDATE properties 
        SET image_analysis = ?,
            gallery_images = ?,
            ai_analysis = COALESCE(?, ai_analysis)
        WHERE id = ?
    """, (img_json, gallery_json, ai_analysis_val, property_id))
    conn.commit()
    conn.close()

    resp_payload = {
        "property_id": property_id,
        "image_analysis": img_report
    }
    if updated_ai_obj is not None:
        resp_payload["ai_audit"] = updated_ai_obj
        resp_payload["missing_documents"] = updated_ai_obj.get("missing_documents", [])
        resp_payload["doc_checklist"] = updated_ai_obj.get("doc_checklist", [])
        resp_payload["document_request"] = updated_ai_obj.get("document_request")
    return resp_payload

@router.get("/{property_id}/image-analysis")
def get_property_image_analysis(property_id: int):
    """
    Returns the visual AI image analysis for a property.
    If not yet analyzed on-demand, returns status 'pending'.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT image_analysis FROM properties WHERE id = ?", (property_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Property not found")

    if row[0]:
        try:
            img_data = json.loads(row[0])
            if isinstance(img_data, dict):
                for p in (img_data.get("analyzed_photos") or []):
                    p["tag"] = refine_photo_tag(p.get("tag", ""), p.get("description", ""), p.get("findings", []))
            return {"property_id": property_id, "image_analysis": img_data, "status": "completed"}
        except Exception:
            pass

    return {"property_id": property_id, "image_analysis": None, "status": "pending"}

@router.get("/{property_id}/ai-audit")
def get_property_ai_audit(property_id: int):
    """
    Returns the AI audit report for a property. If not yet generated, generates it automatically.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT ai_analysis FROM properties WHERE id = ?", (property_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Property not found")

    if row[0]:
        try:
            ai_data = json.loads(row[0])
            if isinstance(ai_data, dict) and (not ai_data.get("missing_documents") or not ai_data.get("doc_checklist")):
                cursor.execute("SELECT * FROM properties WHERE id = ?", (property_id,))
                p_row = cursor.fetchone()
                if p_row:
                    doc_req = build_missing_documents_request(dict(p_row), ai_data)
                    ai_data["missing_documents"] = doc_req.get("missing_documents", [])
                    ai_data["doc_checklist"] = doc_req.get("missing_documents", [])
                    ai_data["document_request"] = doc_req
            conn.close()
            return {
                "property_id": property_id,
                "ai_audit": ai_data,
                "missing_documents": ai_data.get("missing_documents", []),
                "doc_checklist": ai_data.get("doc_checklist", []),
                "document_request": ai_data.get("document_request")
            }
        except Exception:
            pass

    conn.close()
    # If not present, run it
    return run_property_ai_audit(property_id=property_id, force_refresh=False)

@router.post("/run-all-ai-audits")
def run_all_ai_audits(force_refresh: bool = False):
    """
    Performs AI Due Diligence across all properties in the database:
    extracts cold rents, stated yields, WEG risks, CaPEx, and provisions status,
    and synchronizes all properties with their verified real-world metrics.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM properties")
    ids = [r[0] for r in cursor.fetchall()]
    conn.close()

    audited = 0
    results = []
    for prop_id in ids:
        try:
            res = run_property_ai_audit(property_id=prop_id, force_refresh=force_refresh)
            audited += 1
            results.append({
                "id": prop_id,
                "score": res["ai_audit"]["score"],
                "status": res["ai_audit"]["status"],
                "gross_yield": res["property_updates"]["gross_yield"],
                "current_rent": res["property_updates"]["current_rent"],
                "is_private": res["property_updates"]["is_private"],
                "commission_percent": res["property_updates"]["commission_percent"]
            })
        except Exception as e:
            logger.error(f"Error auditing property {prop_id}: {e}")

    return {
        "status": "success",
        "total_audited": audited,
        "results": results
    }

@router.post("/{property_id}/toggle-favorite")
def toggle_favorite(property_id: int):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT is_favorite FROM properties WHERE id = ?", (property_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Property not found")

    new_state = 0 if row[0] == 1 else 1
    cursor.execute("UPDATE properties SET is_favorite = ? WHERE id = ?", (new_state, property_id))
    conn.commit()
    conn.close()

    return {"id": property_id, "is_favorite": new_state}

@router.post("/{property_id}/notes")
def add_note(property_id: int, note_data: Dict[str, str] = Body(...)):
    note_text = note_data.get("note", "").strip()
    if not note_text:
        raise HTTPException(status_code=400, detail="Note cannot be empty")

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("INSERT INTO deal_notes (property_id, note) VALUES (?, ?)", (property_id, note_text))
    conn.commit()

    cursor.execute("SELECT * FROM deal_notes WHERE property_id = ? ORDER BY created_at DESC", (property_id,))
    notes = [dict(n) for n in cursor.fetchall()]
    conn.close()

    return {"success": True, "notes": notes}

@router.post("/live-scrape")
def trigger_live_scrape(payload: Dict[str, Any] = Body(...), request: Request = None, background_tasks: BackgroundTasks = None):
    """
    Triggers live multi-portal scraping (Kleinanzeigen, ImmoScout24, Immowelt) for any German city,
    applies Cross-Portal Deal Fusion, enriches listings with cashflow and valuations,
    strictly filters out deals below min_yield or outside price criteria,
    and returns immediately to the UI while running deep AI audits in the background.
    """
    from app.scraper import scrape_multi_portal_deals, import_scraped_deals_returning_ids

    city = payload.get("city", "Essen").strip()
    query = payload.get("query", "").strip()
    limit = int(payload.get("limit", 30))
    analyze_ai = bool(payload.get("analyze_with_ai", True))

    min_yield = None
    if payload.get("min_yield") is not None and str(payload.get("min_yield")).strip() != "":
        try:
            min_yield = float(payload.get("min_yield"))
        except (ValueError, TypeError):
            pass

    yield_basis = str(payload.get("yield_basis") or "any").strip().lower()

    min_price = None
    if payload.get("min_price") is not None and str(payload.get("min_price")).strip() != "":
        try:
            min_price = float(payload.get("min_price"))
        except (ValueError, TypeError):
            pass

    max_price = None
    if payload.get("max_price") is not None and str(payload.get("max_price")).strip() != "":
        try:
            max_price = float(payload.get("max_price"))
        except (ValueError, TypeError):
            pass

    if not city:
        raise HTTPException(status_code=400, detail="City name is required")

    deals = scrape_multi_portal_deals(
        city=city,
        query=query,
        max_results=limit,
        min_yield=min_yield,
        yield_basis=yield_basis,
        min_price=min_price,
        max_price=max_price
    )
    new_ids = import_scraped_deals_returning_ids(deals)
    saved_count = len(new_ids)

    # Run AI text & due diligence analysis in background so search responds in < 3 seconds
    if analyze_ai and new_ids:
        def _bg_audit_batch(pids: List[int]):
            for pid in pids:
                try:
                    run_property_ai_audit(property_id=pid, force_refresh=True, request=request)
                except Exception as exc:
                    print(f"Auto AI-Audit failed for newly scraped deal #{pid}: {exc}")

        if background_tasks:
            background_tasks.add_task(_bg_audit_batch, new_ids)
        else:
            threading.Thread(target=_bg_audit_batch, args=(new_ids,), daemon=True).start()

    criteria_str = []
    if min_yield is not None:
        criteria_str.append(f"≥ {min_yield:.1f}% Rendite")
    if max_price is not None:
        criteria_str.append(f"bis {max_price:,.0f} €")
    crit_info = f" ({', '.join(criteria_str)})" if criteria_str else ""

    return {
        "status": "success",
        "city": city,
        "found": len(deals),
        "imported": saved_count,
        "analyzed_ids": new_ids,
        "message": f"{saved_count} neue passende Live-Deals in {city}{crit_info} gefunden und importiert!"
    }


@router.post("/import-url")
def import_property_from_url(payload: Dict[str, Any] = Body(...), request: Request = None):
    """
    Imports a listing directly by its portal URL (Kleinanzeigen, ImmoScout24, Immowelt).
    Fetches the exposé live, extracts pricing, space, rooms, location and full description,
    runs the complete AI Due Diligence Audit immediately, saves the property into the pipeline,
    and returns the complete deal object.
    """
    from datetime import datetime
    from app.scrapers.immoscout import fetch_immoscout_expose
    from app.scrapers.immowelt import fetch_immowelt_expose
    from app.geo_resolver import resolve_location_and_tax, resolve_city_coordinates, ensure_distinct_coord

    raw_url = (payload.get("url") or "").strip()
    if not raw_url or not raw_url.startswith("http"):
        raise HTTPException(status_code=400, detail="Bitte eine gültige URL (http:// oder https://) angeben.")

    conn = get_connection()
    cursor = conn.cursor()

    # 1. Check if already in DB
    cursor.execute("SELECT * FROM properties WHERE portal_url = ?", (raw_url,))
    existing = cursor.fetchone()
    if existing:
        existing_id = existing["id"]
        cur_t = existing["title"] or ""
        force = True if (not cur_t or cur_t.lower() == "beschreibung" or existing["purchase_price"] <= 0) else False
        audit_res = run_property_ai_audit(existing_id, force_refresh=force, request=request)
        cursor.execute("SELECT * FROM properties WHERE id = ?", (existing_id,))
        updated_row = dict(cursor.fetchone())
        conn.close()
        return {
            "status": "already_exists",
            "message": f"Dieses Inserat existiert bereits in Ihrer Pipeline (Deal #{existing_id}).",
            "property_id": existing_id,
            "property": updated_row,
            "ai_audit": audit_res.get("ai_audit")
        }

    # 2. Identify portal and fetch listing data
    url_lower = raw_url.lower()
    source_portal = "Kleinanzeigen"
    title = "Importiertes Inserat"
    description = ""
    price = 0.0
    sqm = 0.0
    rooms = 2.0
    year_built = 1980
    address = ""
    postal_code = ""
    city = ""
    img_url = "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?w=800"
    lat = None
    lng = None
    gallery_images = []

    if "immobilienscout24.de" in url_lower:
        source_portal = "ImmoScout24"
        scout_res = fetch_immoscout_expose(raw_url)
        if scout_res.get("success"):
            title = scout_res.get("title") or title
            description = scout_res.get("description") or ""
            addr_str = scout_res.get("address") or ""
            address = addr_str
            lat = scout_res.get("lat")
            lng = scout_res.get("lng")
            if scout_res.get("price", 0) > 0:
                price = scout_res["price"]
            if scout_res.get("living_space", 0) > 0:
                sqm = scout_res["living_space"]
            if scout_res.get("rooms", 0) > 0:
                rooms = scout_res["rooms"]
            if scout_res.get("pictures"):
                gallery_images = scout_res["pictures"]
                img_url = gallery_images[0]
            details = scout_res.get("details") or {}
            for k, v in details.items():
                if price <= 0 and any(x in k.lower() for x in ["kaufpreis", "preis"]):
                    m = re.search(r"([\d\.]+)", str(v))
                    if m:
                        try:
                            price = float(m.group(1).replace(".", ""))
                        except ValueError:
                            pass
                elif sqm <= 0 and any(x in k.lower() for x in ["wohnfläche", "fläche"]):
                    m = re.search(r"([\d,\.]+)", str(v))
                    if m:
                        try:
                            sqm = float(m.group(1).replace(".", "").replace(",", "."))
                        except ValueError:
                            pass
                elif rooms <= 0 and "zimmer" in k.lower():
                    m = re.search(r"([\d,\.]+)", str(v))
                    if m:
                        try:
                            rooms = float(m.group(1).replace(",", "."))
                        except ValueError:
                            pass
            plz_m = re.search(r"\b(\d{5})\s+([A-Za-zäöüÄÖÜß\-]+)", addr_str)
            if plz_m:
                postal_code = plz_m.group(1)
                city = plz_m.group(2)

    elif "immowelt.de" in url_lower:
        source_portal = "Immowelt"
        iw_res = fetch_immowelt_expose(raw_url)
        title = iw_res.get("title") or "Immowelt Angebot"
        description = iw_res.get("description") or ""
        if iw_res.get("price", 0) > 0:
            price = float(iw_res["price"])
        if iw_res.get("living_space", 0) > 0:
            sqm = float(iw_res["living_space"])
        if iw_res.get("rooms", 0) > 0:
            rooms = float(iw_res["rooms"])
        if iw_res.get("year_built", 0) > 0:
            year_built = int(iw_res["year_built"])
        if iw_res.get("postal_code"):
            postal_code = iw_res["postal_code"]
        if iw_res.get("city"):
            city = iw_res["city"]
        if iw_res.get("district"):
            address = f"{postal_code} {city}-{iw_res['district']}"
        if iw_res.get("images"):
            gallery_images = iw_res["images"]
            if iw_res.get("image_url"):
                img_url = iw_res["image_url"]
            elif gallery_images:
                img_url = gallery_images[0]
        if iw_res.get("energy_certificate"):
            details["energy_certificate"] = iw_res["energy_certificate"]

    else:
        if "kleinanzeigen" in url_lower:
            source_portal = "Kleinanzeigen"
        live_check = check_portal_url_live(raw_url, source_portal)
        if live_check.get("title"):
            title = live_check["title"]
        if live_check.get("price", 0) > 0:
            price = live_check["price"]
        description = live_check.get("description") or ""
        lat = live_check.get("lat")
        lng = live_check.get("lng")
        if live_check.get("postal_code"):
            postal_code = live_check["postal_code"]
        if live_check.get("locality"):
            city = live_check["locality"]
        if live_check.get("street_address"):
            address = live_check["street_address"]
        details = live_check.get("details") or {}
        for k, v in details.items():
            if "preis" in k.lower():
                m = re.search(r"([\d\.]+)", str(v))
                if m:
                    try:
                        price = float(m.group(1).replace(".", ""))
                    except ValueError:
                        pass
            elif "wohnfläche" in k.lower():
                m = re.search(r"([\d,\.]+)", str(v))
                if m:
                    try:
                        sqm = float(m.group(1).replace(".", "").replace(",", "."))
                    except ValueError:
                        pass
            elif "zimmer" in k.lower():
                m = re.search(r"([\d,\.]+)", str(v))
                if m:
                    try:
                        rooms = float(m.group(1).replace(",", "."))
                    except ValueError:
                        pass

    # 3. Fallback extraction from description text if fields were missing
    combined_all = f"{title}\n{description}"
    if price <= 0:
        p_match = re.search(r"(?:kaufpreis|preis|vb)?[^\d\n\r]{0,10}(\d{2,3}(?:\.\d{3})+)\s*€", combined_all, re.IGNORECASE)
        if p_match:
            try:
                price = float(p_match.group(1).replace(".", ""))
            except ValueError:
                pass
    if price <= 0:
        price = 150000.0

    if sqm <= 0:
        sq_match = re.search(r"(\d+(?:[\.,]\d+)?)\s*m²", combined_all, re.IGNORECASE)
        if sq_match:
            try:
                sqm = float(sq_match.group(1).replace(".", "").replace(",", "."))
            except ValueError:
                pass
    if sqm <= 0:
        sqm = max(30.0, round(price / 2500.0, 1))

    if not city:
        plz_match = re.search(r"\b(\d{5})\s+([A-Za-zäöüÄÖÜß\-]{2,30})", combined_all)
        if plz_match:
            postal_code = plz_match.group(1)
            city = plz_match.group(2)
        else:
            city = "Dortmund"

    geo_info = resolve_location_and_tax(postal_code=postal_code, city=city)
    state = geo_info["state"]
    if not postal_code and geo_info.get("postal_code"):
        postal_code = geo_info["postal_code"]

    if not address:
        address = f"{postal_code} {city}".strip()

    if lat is None or lng is None:
        lat, lng = resolve_city_coordinates(city=city, postal_code=postal_code, street=address)

    # Ensure title is never a generic placeholder
    title = extract_clean_listing_title(raw_url, title, description, city, rooms, sqm)

    benchmark = evaluate_property_valuation(city, sqm, price)
    expected_rent_per_sqm = benchmark.get("expected_rent_per_sqm", 11.50)
    expected_rent = round(sqm * expected_rent_per_sqm, 2)
    gross_yield = round(((expected_rent * 12.0) / price) * 100.0, 2) if price > 0 else 0.0

    cf = calculate_cashflow(price=price, rent=expected_rent, city=city, state=state, living_space=sqm)
    monthly_cashflow = cf.get("monthly_cashflow", 0.0)

    is_private = 1 if ("provisionsfrei" in combined_all.lower() or "privat" in combined_all.lower()) else 0
    comm_pct = 0.0 if is_private else 3.57

    tags = ["URL Import", source_portal, state]
    if gross_yield >= 5.5:
        tags.append("Top-Rendite")

    cursor.execute("SELECT lat, lng FROM properties WHERE lat IS NOT NULL AND lng IS NOT NULL")
    occupied_coords = {(round(float(r[0]), 4), round(float(r[1]), 4)) for r in cursor.fetchall()}
    clean_lat, clean_lng = ensure_distinct_coord(lat, lng, occupied_coords, seed=999)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sp_json = json.dumps([{"portal": source_portal, "url": raw_url, "price": price}])

    cursor.execute("""
        INSERT INTO properties (
            title, address, postal_code, city, state, country, property_type, building_type,
            rooms, living_space, land_area, year_built, condition, purchase_price, original_price,
            price_drop_amount, price_drop_percent, commission_percent, current_rent, expected_rent,
            gross_yield, monthly_cashflow, market_value, market_value_per_sqm, market_deviation_percent,
            source_portal, portal_url, image_url, days_online, first_seen_date, is_private,
            is_rented, is_foreclosure, is_favorite, pipeline_stage, tags, lat, lng, parcel_id,
            parcel_area, is_online, availability_status, status_message, last_checked_at, live_search_url,
            description, gallery_images, source_portals
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
        title, address, postal_code, city, state, "DE",
        "Eigentumswohnung", "Etagenwohnung",
        rooms, sqm, 0.0, year_built, "Gepflegt",
        price, price, 0.0, 0.0, comm_pct,
        0.0, expected_rent, gross_yield, monthly_cashflow,
        benchmark.get("estimated_market_value", price),
        benchmark.get("estimated_market_value_per_sqm", 2800.0),
        benchmark.get("market_deviation_percent", 0.0),
        source_portal, raw_url, img_url,
        1, datetime.now().strftime("%Y-%m-%d"),
        is_private, 0, 0, 0, "neu",
        str(tags).replace("'", '"'),
        clean_lat, clean_lng, None, 0.0,
        1, "online", "Live importiert & verifiziert",
        now_str, raw_url,
        description, json.dumps(gallery_images), sp_json
    ))
    new_prop_id = cursor.lastrowid
    conn.commit()

    # 4. Trigger full AI Due Diligence Audit immediately
    audit_res = {}
    try:
        audit_res = run_property_ai_audit(new_prop_id, force_refresh=True, request=request)
    except Exception as e:
        print(f"Error running initial AI audit on imported deal #{new_prop_id}: {e}")

    cursor.execute("SELECT * FROM properties WHERE id = ?", (new_prop_id,))
    final_prop = dict(cursor.fetchone())
    conn.close()

    return {
        "status": "success",
        "message": f"Inserat von {source_portal} erfolgreich importiert und per KI analysiert!",
        "property_id": new_prop_id,
        "property": final_prop,
        "ai_audit": audit_res.get("ai_audit")
    }

@router.delete("/all")
def delete_all_properties_endpoint(clear_alerts: bool = True, clear_seen: bool = True):
    """Löscht alle Inserate / Deals aus der Datenbank"""
    from app.database import delete_all_properties
    deleted = delete_all_properties(clear_alerts=clear_alerts, clear_seen=clear_seen)
    return {
        "status": "success",
        "deleted_count": deleted,
        "message": f"Alle {deleted} Inserate wurden erfolgreich aus der Datenbank entfernt."
    }

@router.delete("/{property_id}")
def delete_property(property_id: int):
    """Delete a single property from the database"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, title FROM properties WHERE id = ?", (property_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(status_code=404, detail="Property not found")

    cursor.execute("DELETE FROM properties WHERE id = ?", (property_id,))
    cursor.execute("DELETE FROM deal_notes WHERE property_id = ?", (property_id,))
    conn.commit()
    conn.close()

    return {"status": "success", "deleted_id": property_id, "message": f"Deal #{property_id} erfolgreich gelöscht."}

@router.post("/delete-offline")
def delete_all_offline_properties():
    """Bulk delete all properties that are marked as deactivated / offline"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM properties WHERE is_online = 0")
    offline_ids = [r[0] for r in cursor.fetchall()]

    if offline_ids:
        placeholders = ",".join("?" * len(offline_ids))
        cursor.execute(f"DELETE FROM properties WHERE id IN ({placeholders})", offline_ids)
        cursor.execute(f"DELETE FROM deal_notes WHERE property_id IN ({placeholders})", offline_ids)
        conn.commit()

    conn.close()
    return {
        "status": "success",
        "deleted_count": len(offline_ids),
        "deleted_ids": offline_ids,
        "message": f"{len(offline_ids)} deaktivierte Deals wurden gelöscht."
    }
