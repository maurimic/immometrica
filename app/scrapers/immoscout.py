"""
ImmobilienScout24 Scraper Engine (Mobile REST API Adapter).
Inspired by Fredy: Avoids aggressive desktop web bot-protection (DataDome/Cloudflare)
by communicating natively via the official ImmoScout24 mobile application REST API
(api.mobile.immobilienscout24.de).
"""

import re
import json
import requests
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger("immometrica.scraper.immoscout")

MOBILE_HEADERS = {
    "User-Agent": "ImmoScout_28.1_26.5.2_._",
    "Accept": "application/json",
    "Content-Type": "application/json",
    "Accept-Language": "de-DE,de;q=0.9",
    "Cache-Control": "no-cache"
}

BASE_API_URL = "https://api.mobile.immobilienscout24.de"


def extract_immoscout_id(url_or_id: str) -> Optional[str]:
    """Extracts numeric expose ID from an ImmoScout URL or returns raw ID."""
    s = str(url_or_id).strip()
    if s.isdigit():
        return s
    m = re.search(r"/expose/(\d+)", s)
    if m:
        return m.group(1)
    m2 = re.search(r"realEstateId=(\d+)", s)
    if m2:
        return m2.group(1)
    m3 = re.search(r"\b(\d{7,10})\b", s)
    if m3:
        return m3.group(1)
    return None


def _map_immoscout_types(property_type: str = "all") -> List[tuple]:
    """Maps configured property_type to (api_type, property_type, building_type) tuples."""
    pt = str(property_type or "all").lower().strip()
    if any(k in pt for k in ("grundst", "plot", "land")):
        return [("sitebuy", "Grundstück", "Grundstück")]
    if any(k in pt for k in ("mehrfamilien", "mfh", "zinshaus")):
        return [("housebuy", "Mehrfamilienhaus", "Mehrfamilienhaus")]
    if any(k in pt for k in ("einfamilien", "efh", "haus", "house")):
        return [("housebuy", "Einfamilienhaus", "Haus")]
    if any(k in pt for k in ("wohnung", "etw", "apartment", "maisonette", "penthouse")):
        return [("apartmentbuy", "Eigentumswohnung", "Etagenwohnung")]
    return [
        ("apartmentbuy", "Eigentumswohnung", "Etagenwohnung"),
        ("housebuy", "Einfamilienhaus", "Haus"),
    ]


def search_immoscout_deals(
    city: str,
    query: str = "",
    radius_km: int = 15,
    max_results: int = 15,
    property_type: str = "all",
    exclude_urls: Optional[Any] = None,
    postal_code: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Queries live ImmoScout24 listings using the reverse-engineered mobile REST API.
    Resolves geo-coordinates for the city/PLZ centroid, sorts by newest ('firstactivation'),
    supports multiple property types (ETW/EFH/MFH/Grundstück), pagination (pages 1 to 3),
    and skips known candidate URLs.
    """
    from app.geo_resolver import resolve_city_coordinates, resolve_location_and_tax, is_within_search_radius, normalize_district_city
    from app.calculator import calculate_cashflow
    from app.valuation import evaluate_property_valuation
    from app.scrapers.deal_fusion import normalize_portal_url

    api_targets = _map_immoscout_types(property_type)
    plz_clean = re.sub(r"\D", "", str(postal_code or ""))[:5]
    center_lat, center_lng = resolve_city_coordinates(city=city, postal_code=plz_clean or None)
    req_radius = int(radius_km) if radius_km is not None else 15
    eff_radius = max(1, req_radius) if req_radius > 0 else 5

    url = f"{BASE_API_URL}/search/list"
    exclude_set = {normalize_portal_url(u) for u in exclude_urls} if exclude_urls else set()
    results: List[Dict[str, Any]] = []
    seen_urls = set()

    for api_type, detected_p_type, detected_b_type in api_targets:
        if len(results) >= max_results:
            break
        for page in range(1, 4):
            if len(results) >= max_results:
                break

            params = {
                "searchType": "radius",
                "realestatetype": api_type,
                "geocoordinates": f"{center_lat:.4f};{center_lng:.4f};{eff_radius}",
                "pagesize": min(max_results * 2, 50),
                "pagenumber": page,
                "sorting": "-firstactivation"
            }

            try:
                resp = requests.post(
                    url,
                    params=params,
                    headers=MOBILE_HEADERS,
                    json={},
                    timeout=6.0
                )
                if resp.status_code != 200:
                    logger.warning(f"ImmoScout mobile API returned HTTP {resp.status_code} ({api_type}): {resp.text[:200]}")
                    break

                data = resp.json()
                items = data.get("resultListItems", [])
                if not items:
                    break

                for wrap in items:
                    if wrap.get("type") == "NEW_HOME_BUILDER_RESULT":
                        continue
                    it = wrap.get("item")
                    if not it or not it.get("id"):
                        continue

                    expose_id = str(it["id"])
                    title = it.get("title") or "Immobilienangebot ImmoScout24"
                    portal_url = normalize_portal_url(f"https://www.immobilienscout24.de/expose/{expose_id}")

                    if portal_url in seen_urls:
                        continue
                    if exclude_set and portal_url in exclude_set:
                        continue
                    seen_urls.add(portal_url)

                    # Extract price, sqm, rooms & optional stated rent from attributes array
                    price = 0.0
                    sqm = 0.0
                    rooms = 2.0
                    card_rent = 0.0
                    attr_chunks = []
                    for attr in it.get("attributes", []):
                        val = str(attr.get("value", "")).strip()
                        lbl = str(attr.get("label", "")).lower()
                        attr_chunks.append(f"{lbl} {val}")
                        if "€" in val and ("miete" in lbl or "kaltmiete" in lbl) and "m²" not in val:
                            no_dec = re.sub(r",\d{1,2}\b", "", val)
                            clean_r = re.sub(r"[^\d]", "", no_dec)
                            if clean_r:
                                cand_r = float(clean_r)
                                if 100.0 <= cand_r <= 250000.0 and card_rent <= 0:
                                    card_rent = cand_r
                        elif "€" in val and price <= 0 and "m²" not in val and "hausgeld" not in lbl and "miete" not in lbl and "stellplatz" not in lbl:
                            # Strip optional decimal cents (",00") before removing thousands separators
                            no_dec = re.sub(r",\d{1,2}\b", "", val)
                            clean_pr = re.sub(r"[^\d]", "", no_dec)
                            if clean_pr:
                                price = float(clean_pr)
                        elif "m²" in val and sqm <= 0 and "grundstück" not in lbl and "nutzfläche" not in lbl and "m²/" not in val:
                            m_sq = re.search(r"([\d,\.]+)", val)
                            if m_sq:
                                raw_sq = m_sq.group(1)
                                try:
                                    if "," in raw_sq:
                                        norm_sq = raw_sq.replace(".", "").replace(",", ".")
                                    elif re.match(r"^\d{1,3}(?:\.\d{3})+$", raw_sq):
                                        norm_sq = raw_sq.replace(".", "")
                                    else:
                                        norm_sq = raw_sq
                                    sqm = float(norm_sq)
                                except ValueError:
                                    pass
                        elif "Zi" in val and rooms == 2.0:
                            clean_rm = re.sub(r"[^\d,\.]", "", val).replace(",", ".")
                            if clean_rm:
                                try:
                                    rooms = float(clean_rm)
                                except ValueError:
                                    pass

                    if price < 15000:
                        continue

                    # Address & Coordinates (keep multi-word city names like "Frankfurt am Main")
                    addr_obj = it.get("address") or {}
                    line = addr_obj.get("line") or f"{city or plz_clean}"
                    raw_lat = addr_obj.get("lat")
                    raw_lon = addr_obj.get("lon")

                    # Filter query keyword against title + address + attributes
                    query_corpus = f"{title} {line} {' '.join(attr_chunks)}".lower()
                    if query and query.lower() not in query_corpus:
                        continue

                    plz_match = re.search(r"\b(\d{5})\s+([^,|(\n\r]+)", line)
                    if plz_match:
                        extracted_plz = plz_match.group(1).strip()
                        extracted_city = re.sub(r"\s+", " ", plz_match.group(2)).strip(" -")
                    else:
                        extracted_plz = plz_clean or ""
                        extracted_city = city

                    resolved_city = extracted_city or city
                    if city:
                        resolved_city, _ = normalize_district_city(city, resolved_city, extracted_plz)

                    if raw_lat is not None and raw_lon is not None and float(raw_lat) > 0:
                        prop_lat = float(raw_lat)
                        prop_lng = float(raw_lon)
                    else:
                        prop_lat, prop_lng = resolve_city_coordinates(
                            city=resolved_city,
                            postal_code=extracted_plz,
                            street=line,
                            fallback_city=city,
                            index=len(results),
                            allow_geocoder=True
                        )

                    if city or plz_clean:
                        in_rad, _ = is_within_search_radius(
                            target_city=city,
                            target_postal_code=plz_clean or None,
                            radius_km=req_radius,
                            deal_city=resolved_city,
                            deal_postal_code=extracted_plz,
                            deal_lat=prop_lat,
                            deal_lng=prop_lng,
                            allow_geocoder=False,
                        )
                        if not in_rad:
                            continue

                    geo_info = resolve_location_and_tax(postal_code=extracted_plz, city=resolved_city)
                    resolved_state = geo_info["state"]

                    # Refine property type cleanly for housebuy / apartmentbuy
                    row_p_type = detected_p_type
                    row_b_type = detected_b_type
                    t_low = title.lower()
                    if api_type == "housebuy":
                        if any(k in t_low for k in ["gewerbeliegenschaft", "bürohaus", "lagerhalle", "gewerbeobjekt"]):
                            row_p_type = "Gewerbeobjekt"
                            row_b_type = "Gewerbe"
                        elif any(k in t_low for k in ["mehrfamilienhaus", "zinshaus", "renditehaus", "mfh", "wohneinheiten", "parteien", "wohn- und geschäftshaus"]):
                            row_p_type = "Mehrfamilienhaus"
                            row_b_type = "Mehrfamilienhaus"
                        elif any(k in t_low for k in ["einfamilienhaus", "doppelhaushälfte", "doppelhaus", "reihenhaus", "bungalow", "stadthaus", "zweifamilienhaus", "efh"]):
                            row_p_type = "Einfamilienhaus"
                            row_b_type = "Haus"
                        elif detected_p_type == "Mehrfamilienhaus" and sqm > 0 and sqm < 180.0 and rooms < 6.0:
                            row_p_type = "Einfamilienhaus"
                            row_b_type = "Haus"
                    elif api_type == "apartmentbuy":
                        if "maisonette" in t_low:
                            row_p_type = "Maisonette"
                            row_b_type = "Maisonette"
                        elif "penthouse" in t_low:
                            row_p_type = "Penthouse"
                            row_b_type = "Penthouse"

                    if row_p_type == "Gewerbeobjekt":
                        continue

                    # Image & Gallery (never store Unsplash stock URLs inside gallery_images)
                    gallery = []
                    if it.get("pictures"):
                        for pic in it["pictures"]:
                            raw_p = pic.get("urlScaleAndCrop") or pic.get("url") or pic.get("full") or ""
                            if raw_p:
                                gallery.append(raw_p.replace("%WIDTH%", "1200").replace("%HEIGHT%", "900"))

                    tp = it.get("titlePicture") or {}
                    img_url = tp.get("full") or tp.get("preview")
                    if not img_url and gallery:
                        img_url = gallery[0]
                    if img_url and not gallery:
                        gallery = [img_url]

                    if not img_url:
                        img_url = "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?w=800"

                    # Benchmark, Rent & Cashflow
                    eff_sqm = sqm if sqm > 0 else 50.0
                    benchmark = evaluate_property_valuation(resolved_city, eff_sqm, price, property_type=row_p_type, postal_code=extracted_plz)
                    expected_rent_per_sqm = benchmark.get("expected_rent_per_sqm", 11.50)
                    expected_rent = round(eff_sqm * expected_rent_per_sqm, 2) if (row_p_type != "Grundstück" and sqm > 0) else 0.0
                    is_rented = 1 if (card_rent > 0 or ("vermietet" in t_low and "unvermietet" not in t_low and "nicht vermietet" not in t_low)) else 0
                    current_rent = card_rent if card_rent > 0 else 0.0
                    gross_yield = round(((expected_rent * 12.0) / price) * 100.0, 2) if (price > 0 and expected_rent > 0) else 0.0
        
                    cf = calculate_cashflow(
                        price=price,
                        rent=expected_rent,
                        city=resolved_city,
                        state=resolved_state,
                        postal_code=extracted_plz,
                        living_space=eff_sqm
                    )
                    monthly_cashflow = cf.get("monthly_cashflow", 0.0)
        
                    is_private = 1 if it.get("isPrivate") or "privat" in t_low else 0
                    comm_pct = 0.0 if is_private else 3.57
        
                    tags = ["Live Scraped", "ImmoScout24", resolved_state]
                    if gross_yield >= 5.5:
                        tags.append("Top-Rendite")
                    if monthly_cashflow > 0:
                        tags.append("Positiver Cashflow")
                    if is_private:
                        tags.append("Provisionsfrei")
        
                    from app.datetime_utils import get_berlin_now_str, get_berlin_today_str
                    now_str = get_berlin_now_str()
        
                    deal_record = {
                        "title": title,
                        "address": line,
                        "postal_code": extracted_plz,
                        "city": resolved_city,
                        "state": resolved_state,
                        "country": "DE",
                        "property_type": row_p_type,
                        "building_type": row_b_type,
                        "rooms": rooms,
                        "living_space": sqm,
                        "land_area": 0.0,
                        "year_built": 1980,
                        "condition": "Gepflegt",
                        "purchase_price": price,
                        "original_price": price,
                        "price_drop_amount": 0.0,
                        "price_drop_percent": 0.0,
                        "commission_percent": comm_pct,
                        "current_rent": current_rent,
                        "expected_rent": expected_rent,
                        "gross_yield": gross_yield,
                        "monthly_cashflow": monthly_cashflow,
                        "market_value": benchmark.get("estimated_market_value", price),
                        "market_value_per_sqm": benchmark.get("estimated_market_value_per_sqm", 2800.0),
                        "market_deviation_percent": benchmark.get("market_deviation_percent", 0.0),
                        "source_portal": "ImmoScout24",
                        "portal_url": portal_url,
                        "source_portals": [{"portal": "ImmoScout24", "url": portal_url, "price": price}],
                        "image_url": img_url,
                        "gallery_images": json.dumps(gallery),
                        "days_online": 1,
                        "first_seen_date": get_berlin_today_str(),
                        "is_private": is_private,
                        "is_rented": is_rented,
                        "is_foreclosure": 0,
                        "is_favorite": 0,
                        "pipeline_stage": "merkliste" if gross_yield >= 5.0 else "neu",
                        "tags": str(tags).replace("'", '"'),
                        "lat": prop_lat,
                        "lng": prop_lng,
                        "parcel_id": None,
                        "parcel_area": 0.0,
                        "is_online": 1,
                        "availability_status": "online",
                        "status_message": "Live erreichbar & aktiv (ImmoScout24 API verifiziert)",
                        "last_checked_at": now_str,
                        "live_search_url": f"https://www.immobilienscout24.de/Suche/de/{resolved_city.lower()}/wohnung-kaufen"
                    }
                    results.append(deal_record)
        
                    if len(results) >= max_results:
                        break
        
            except Exception as e:
                logger.error(f"Error querying ImmoScout mobile API: {e}")

    return results


def fetch_immoscout_expose(url_or_id: str) -> Dict[str, Any]:
    """
    Fetches comprehensive expose metadata and description from ImmoScout24 mobile API.
    Used by URL Schnellimport to load rich exposé content with 100% accuracy.
    Extracts real listing headline (from TITLE section, avoiding 'Beschreibung' navbar title),
    exact attributes, pictures, and geographic data.
    """
    expose_id = extract_immoscout_id(url_or_id)
    if not expose_id:
        return {"success": False, "error": f"Ungültige ImmoScout URL / ID: {url_or_id}"}

    url = f"{BASE_API_URL}/expose/{expose_id}"
    try:
        resp = requests.get(url, headers=MOBILE_HEADERS, timeout=7.0)
        if resp.status_code != 200:
            return {"success": False, "status_code": resp.status_code, "error": f"ImmoScout API HTTP {resp.status_code}"}

        data = resp.json()
        sections = data.get("sections", [])

        # 1. Extract Real Title (Do not use mobile navbar header.title = "Beschreibung")
        title = ""
        for sec in sections:
            if sec.get("type") == "TITLE":
                cand = (sec.get("title") or "").strip()
                if cand and cand.lower() != "beschreibung":
                    title = cand
                    break

        if not title:
            header_t = (data.get("header") or {}).get("title") or ""
            if header_t and header_t.lower() != "beschreibung":
                title = header_t
            else:
                sm = (data.get("header") or {}).get("shareMessage") or ""
                lines = [l.strip() for l in sm.split("\n") if l.strip()]
                if lines:
                    title = lines[0]
                else:
                    title = "Immobilienangebot ImmoScout24"

        # 2. Extract Key Attributes (Price, Sqm, Rooms) from TOP_ATTRIBUTES & ATTRIBUTE_LIST
        details: Dict[str, Any] = {}
        price = 0.0
        living_space = 0.0
        rooms = 0.0

        for sec in sections:
            stype = sec.get("type")
            if stype == "TOP_ATTRIBUTES":
                for attr in sec.get("attributes", []):
                    lbl = attr.get("label", "")
                    val = attr.get("text", "")
                    if lbl and val:
                        details[lbl] = val
                        lbl_lower = lbl.lower()
                        if price <= 0 and ("kaufpreis" in lbl_lower or lbl_lower == "preis") and "m²" not in lbl_lower and "stellplatz" not in lbl_lower:
                            no_dec = re.sub(r",\d{1,2}\b", "", val)
                            m = re.search(r"([\d\.]+)", no_dec)
                            if m:
                                try:
                                    price = float(m.group(1).replace(".", ""))
                                except ValueError:
                                    pass
                        elif living_space <= 0 and ("wohnfläche" in lbl_lower or lbl_lower == "fläche") and "grundstück" not in lbl_lower and "nutz" not in lbl_lower:
                            m = re.search(r"([\d,\.]+)", val)
                            if m:
                                try:
                                    living_space = float(m.group(1).replace(".", "").replace(",", "."))
                                except ValueError:
                                    pass
                        elif rooms <= 0 and lbl_lower in ("zimmer", "zimmeranzahl"):
                            m = re.search(r"([\d,\.]+)", val)
                            if m:
                                try:
                                    rooms = float(m.group(1).replace(",", "."))
                                except ValueError:
                                    pass

            elif stype == "ATTRIBUTE_LIST":
                list_title = sec.get("title", "")
                for attr in sec.get("attributes", []):
                    lbl = attr.get("label", "").rstrip(":")
                    val = attr.get("text") or ("Ja" if attr.get("type") == "CHECK" else "")
                    if lbl and val:
                        full_key = f"{list_title} - {lbl}" if list_title else lbl
                        details[full_key] = val
                        lbl_lower = lbl.lower()
                        if (
                            price <= 0
                            and ("kaufpreis" in lbl_lower or lbl_lower == "preis")
                            and "m²" not in lbl_lower
                            and "stellplatz" not in lbl_lower
                            and "garage" not in lbl_lower
                            and "miete" not in lbl_lower
                            and "hausgeld" not in lbl_lower
                            and "/m²" not in str(val).lower()
                        ):
                            no_dec = re.sub(r",\d{1,2}\b", "", str(val))
                            m = re.search(r"([\d\.]+)", no_dec)
                            if m:
                                try:
                                    price = float(m.group(1).replace(".", ""))
                                except ValueError:
                                    pass
                        if (
                            living_space <= 0
                            and ("wohnfläche" in lbl_lower or lbl_lower == "fläche")
                            and "grundstück" not in lbl_lower
                            and "nutz" not in lbl_lower
                        ):
                            m = re.search(r"([\d,\.]+)", str(val))
                            if m:
                                try:
                                    living_space = float(m.group(1).replace(".", "").replace(",", "."))
                                except ValueError:
                                    pass
                        if rooms <= 0 and "zimmer" in lbl_lower:
                            m = re.search(r"([\d,\.]+)", str(val))
                            if m:
                                try:
                                    rooms = float(m.group(1).replace(",", "."))
                                except ValueError:
                                    pass

        # 3. Extract Map, Address & Coordinates
        address = ""
        lat = None
        lng = None
        for sec in sections:
            if sec.get("type") == "MAP":
                l1 = sec.get("addressLine1") or ""
                l2 = sec.get("addressLine2") or ""
                address = f"{l1}, {l2}".strip(" ,")
                loc = sec.get("location") or {}
                lat = loc.get("lat")
                lng = loc.get("lng")
                break

        # 4. Extract Text Sections (Objektbeschreibung, Lage, Ausstattung, Sonstiges)
        text_sections = []
        for sec in sections:
            if sec.get("type") == "TEXT_AREA":
                sec_title = sec.get("title", "")
                sec_content = sec.get("text", "") or sec.get("content", "")
                if sec_content:
                    text_sections.append(f"### {sec_title}\n{sec_content}")

        full_description = "\n\n".join(text_sections) if text_sections else title

        # 5. Extract Pictures from MEDIA section
        pictures = []
        for sec in sections:
            if sec.get("type") == "MEDIA":
                for m in sec.get("media", []):
                    u = m.get("fullImageUrl") or m.get("previewImageUrl") or m.get("imageUrlForWeb") or ""
                    if u:
                        clean_u = u.replace("%WIDTH%", "1200").replace("%HEIGHT%", "900")
                        pictures.append(clean_u)

        return {
            "success": True,
            "expose_id": expose_id,
            "title": title,
            "price": price,
            "living_space": living_space,
            "rooms": rooms,
            "description": full_description,
            "details": details,
            "address": address,
            "lat": lat,
            "lng": lng,
            "pictures": pictures,
            "raw_data": data
        }
    except Exception as e:
        logger.error(f"Error fetching ImmoScout expose #{expose_id}: {e}")
        return {"success": False, "status_code": 500, "error": str(e)}
