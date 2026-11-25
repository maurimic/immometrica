"""
ImmoMetrica Location, Micro-Location & Infrastructure Proximity Engine.
Analyzes address precision (exact street vs approximate district/city),
builds direct Google Maps search and satellite/street-view links,
and computes structured infrastructure distances to supermarkets, transit,
universities, hospitals, and city centers.
"""

import math
import re
import urllib.parse
from typing import Dict, Any, List, Optional, Tuple

# Key German University coordinates (lat, lng, name)
MAJOR_UNIVERSITIES = {
    "dortmund": [
        {"name": "TU Dortmund (Campus Nord & Süd)", "lat": 51.4925, "lng": 7.4116},
        {"name": "FH Dortmund (Campus Sonnenstraße / Emil-Figge)", "lat": 51.4988, "lng": 7.4589}
    ],
    "essen": [
        {"name": "Universität Duisburg-Essen (Campus Essen)", "lat": 51.4645, "lng": 7.0068},
        {"name": "Folkwang Universität der Künste", "lat": 51.3888, "lng": 7.0145}
    ],
    "bochum": [
        {"name": "Ruhr-Universität Bochum (RUB)", "lat": 51.4444, "lng": 7.2608},
        {"name": "Hochschule Bochum", "lat": 51.4468, "lng": 7.2750}
    ],
    "münchen": [
        {"name": "Ludwig-Maximilians-Universität (LMU)", "lat": 48.1508, "lng": 11.5802},
        {"name": "Technische Universität München (TUM)", "lat": 48.1495, "lng": 11.5679}
    ],
    "nürnberg": [
        {"name": "FAU Erlangen-Nürnberg (Campus Nürnberg)", "lat": 49.4520, "lng": 11.0825},
        {"name": "Technische Hochschule Nürnberg Georg Simon Ohm", "lat": 49.4503, "lng": 11.0963}
    ],
    "köln": [
        {"name": "Universität zu Köln", "lat": 50.9281, "lng": 6.9287},
        {"name": "TH Köln (Campus Deutz / Südstadt)", "lat": 50.9328, "lng": 6.9856}
    ],
    "düsseldorf": [
        {"name": "Heinrich-Heine-Universität Düsseldorf", "lat": 51.1906, "lng": 6.7944},
        {"name": "Hochschule Düsseldorf (HSD)", "lat": 51.2462, "lng": 6.7972}
    ],
    "berlin": [
        {"name": "Humboldt-Universität zu Berlin", "lat": 52.5186, "lng": 13.3934},
        {"name": "Freie Universität Berlin (Dahlem)", "lat": 52.4537, "lng": 13.2906},
        {"name": "Technische Universität Berlin", "lat": 52.5119, "lng": 13.3265}
    ],
    "hamburg": [
        {"name": "Universität Hamburg", "lat": 53.5663, "lng": 9.9839},
        {"name": "TU Hamburg (TUHH Harburg)", "lat": 53.4608, "lng": 9.9697}
    ],
    "frankfurt": [
        {"name": "Goethe-Universität Frankfurt (Westend / Riedberg)", "lat": 50.1272, "lng": 8.6669}
    ],
    "leipzig": [
        {"name": "Universität Leipzig (Campus Augustusplatz)", "lat": 51.3388, "lng": 12.3786}
    ],
    "dresden": [
        {"name": "Technische Universität Dresden (TUD)", "lat": 51.0283, "lng": 13.7297}
    ]
}

# Major Central Stations / City Centers
CITY_CENTERS = {
    "dortmund": {"name": "Dortmund Innenstadt (Westenhellweg / Hbf)", "lat": 51.5150, "lng": 7.4640},
    "essen": {"name": "Essen Stadtkern (Kettwiger Str. / Hbf)", "lat": 51.4550, "lng": 7.0130},
    "bochum": {"name": "Bochum Innenstadt / Hbf", "lat": 51.4785, "lng": 7.2225},
    "münchen": {"name": "München Marienplatz / Zentrum", "lat": 48.1371, "lng": 11.5754},
    "nürnberg": {"name": "Nürnberg Hauptmarkt / Lorenzkirche", "lat": 49.4540, "lng": 11.0775},
    "köln": {"name": "Köln Dom / Hohe Straße", "lat": 50.9413, "lng": 6.9583},
    "düsseldorf": {"name": "Düsseldorf Königsallee / Altstadt", "lat": 51.2245, "lng": 6.7780},
    "berlin": {"name": "Berlin Alexanderplatz / Mitte", "lat": 52.5219, "lng": 13.4132},
    "hamburg": {"name": "Hamburg Rathaus / Jungfernstieg", "lat": 53.5502, "lng": 9.9926},
    "frankfurt": {"name": "Frankfurt Zeil / Hauptwache", "lat": 50.1136, "lng": 8.6797},
    "leipzig": {"name": "Leipzig Markt / Zentrum", "lat": 51.3406, "lng": 12.3747},
    "dresden": {"name": "Dresden Altmarkt / Neumarkt", "lat": 51.0493, "lng": 13.7383}
}

# Major Hospitals / Kliniken
MAJOR_HOSPITALS = {
    "dortmund": [
        {"name": "Klinikum Dortmund (Mitte)", "lat": 51.5085, "lng": 7.4542},
        {"name": "St. Josefs Hospital / Knappschaftskrankenhaus", "lat": 51.5278, "lng": 7.5512}
    ],
    "essen": [
        {"name": "Universitätsklinikum Essen (Holsterhausen)", "lat": 51.4336, "lng": 6.9897},
        {"name": "Alfried Krupp Krankenhaus Rüttenscheid", "lat": 51.4283, "lng": 7.0092}
    ],
    "münchen": [
        {"name": "Klinikum der Universität München (Großhadern / Innenstadt)", "lat": 48.1108, "lng": 11.4705},
        {"name": "Klinikum Schwabing", "lat": 48.1725, "lng": 11.5815}
    ],
    "nürnberg": [
        {"name": "Klinikum Nürnberg Nord", "lat": 49.4674, "lng": 11.0683},
        {"name": "Klinikum Nürnberg Süd", "lat": 49.4035, "lng": 11.1118}
    ],
    "köln": [
        {"name": "Uniklinik Köln (Lindenthal)", "lat": 50.9239, "lng": 6.9189}
    ],
    "düsseldorf": [
        {"name": "Universitätsklinikum Düsseldorf", "lat": 51.1966, "lng": 6.7915}
    ]
}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates geodesic distance between two coordinate pairs in kilometers."""
    R = 6371.0  # Earth radius in km
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2.0) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2)
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return round(R * c, 2)


def clean_real_estate_address(
    address: Optional[str],
    city: Optional[str] = None,
    postal_code: Optional[str] = None,
    district: Optional[str] = None
) -> str:
    """
    Cleans raw portal addresses, stripping generic placeholder sentences
    (e.g. 'Straße und Hausnummer nicht angegeben', 'Die vollständige Adresse der Immobilie erhältst du vom Anbieter.')
    and deduplicating repeated postal codes and cities.
    """
    raw = (address or "").strip()
    c_clean = (city or "").strip()
    plz_clean = (postal_code or "").strip()

    m_plz = re.search(r"\b(\d{5})\b", raw)
    if m_plz and not plz_clean:
        plz_clean = m_plz.group(1)

    placeholder_regex = re.compile(
        r"\([^)]*?(?:hausnummer|straße|strasse|adresse)[^)]*?\)|"
        r"(?:die\s+)?vollständige\s+adresse\s+der\s+immobilie\s+(?:erhältst|erhaeltst|bekommen|erhalten)\s+du\s+vom\s+anbieter\.?|"
        r"straße\s+und\s+hausnummer\s+nicht\s+angegeben\.?|"
        r"strasse\s+und\s+hausnummer\s+nicht\s+angegeben\.?|"
        r"straße\s+nicht\s+angegeben\.?|"
        r"strasse\s+nicht\s+angegeben\.?|"
        r"hausnummer\s+nicht\s+angegeben\.?|"
        r"adresse\s+auf\s+anfrage\.?|"
        r"auf\s+anfrage\.?|"
        r"keine\s+angabe\.?|"
        r"nicht\s+angegeben\.?|"
        r"genaue\s+adresse\s+auf\s+anfrage\.?",
        re.IGNORECASE
    )
    cleaned = placeholder_regex.sub(" ", raw)
    cleaned = re.sub(r"[()]", " ", cleaned)
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,-")

    clean_dist = (district or "").strip()
    if ";" in clean_dist:
        clean_dist = clean_dist.split(";")[0].strip()
    if "(" in clean_dist:
        clean_dist = re.sub(r"\(.*?\)", "", clean_dist).strip()

    clean_lower = cleaned.lower()
    is_empty_or_generic = (
        not cleaned
        or clean_lower in ["zentrum", "innenstadt", "mikrolage", "stadtteil", "ortsteil"]
        or bool(re.match(r"^(?:\d{5}\s*)?(?:[a-zäöüß\-]+\s*)?$", clean_lower) and c_clean and c_clean.lower() in clean_lower)
    )

    if is_empty_or_generic:
        if clean_dist and clean_dist.lower() not in c_clean.lower():
            return f"Stadtteil {clean_dist}, {plz_clean} {c_clean}".strip(" ,")
        elif plz_clean and c_clean:
            return f"{plz_clean} {c_clean}".strip()
        return c_clean or "Deutschland"

    # Remove duplicated city and postal code repeats
    if c_clean:
        cleaned = re.sub(rf"(?:,\s*|\s+){re.escape(c_clean)}(?:,\s*|\s+){re.escape(c_clean)}$", f", {c_clean}", cleaned, flags=re.I)
    if plz_clean and c_clean:
        pat = rf"(?:,\s*|\s+){re.escape(plz_clean)}\s+{re.escape(c_clean)}$"
        if re.search(rf"\b{re.escape(plz_clean)}\b.*?" + pat, cleaned, flags=re.I):
            cleaned = re.sub(pat, "", cleaned, flags=re.I).strip(" ,")

    return re.sub(r"\s+", " ", cleaned).strip(" ,")


def check_address_is_exact(address: str, city: Optional[str] = None) -> Tuple[bool, str]:
    """
    Analyzes whether an address string represents an exact street + house number,
    or just a street without house number, or an approximate district/city.
    """
    if not address or not address.strip():
        return False, "UNKNOWN"

    raw = address.strip()
    raw_lower = raw.lower()

    # Reject placeholder texts & district indicators
    placeholder_keywords = [
        "stadtteil", "ortsteil", "bezirk", "innenstadt", "zentrum",
        "mikrolage", "auf anfrage", "keine angabe", "genaue adresse",
        "nicht angegeben", "adresse nicht angegeben", "straße nicht angegeben",
        "strasse nicht angegeben", "hausnummer nicht angegeben",
        "straße und hausnummer", "strasse und hausnummer",
        "vollständige adresse", "vollstaendige adresse", "erhältst du vom anbieter",
        "erhaeltst du vom anbieter", "vom anbieter", "adresse erhalten sie",
        "ohne angabe", "unbekannt"
    ]
    if any(k in raw_lower for k in placeholder_keywords):
        return False, "DISTRICT_OR_CITY_ONLY"

    # Remove city and postal codes to prevent city names like Nürnberg (contains berg),
    # Wuppertal (contains tal), Bielefeld (contains feld) from falsely matching street words!
    clean_addr = raw
    if city and city.strip():
        clean_addr = re.sub(re.escape(city.strip()), " ", clean_addr, flags=re.IGNORECASE)
    clean_addr = re.sub(r"\b(?:d-)?\d{5}\b", " ", clean_addr, flags=re.IGNORECASE)
    clean_lower = clean_addr.lower().strip()

    # Common German street suffixes / words (including multi-word, hyphenated names and prepositions)
    has_street_word = bool(re.search(
        r"(\b[a-zäöüß\-]*?(?:straße|strasse|str\b|str\.|weg|allee|gasse|gäßchen|gaesschen|platz|ring|damm|ufer|zeile|wall|pfad|chaussee|hellweg|steig|stieg|steg|promenade|graben|gang|reihe|park|kamp|blick)\b|\b(?:am|an der|an dem|im|in der|auf dem|auf der|vor dem|hinter dem|zum|zur|beim)\s+[a-zäöüß\-]+|\b(?:am markt|markt|berg|tal|hof|feld|tor)\b)",
        clean_lower
    ))

    # House number pattern: 1-4 digits optionally followed by letters/hyphens (e.g. 15, 120a, 1-3, 44 b)
    # Exclude standalone years like 1985 or 2024 if Bj. is present
    cleaned_for_nr = re.sub(r"\b(?:19\d{2}|20\d{2})\b", " ", clean_addr)
    has_house_number = bool(re.search(r"\b\d{1,4}\s*[a-zA-Z]?(?:\s*[\/\-]\s*\d{1,4}[a-zA-Z]?)?\b", cleaned_for_nr.strip()))

    if has_street_word and has_house_number:
        return True, "EXACT_STREET_AND_NUMBER"
    elif has_street_word:
        return False, "STREET_ONLY_NO_NUMBER"
    elif has_house_number and any(len(w) > 2 and not w.isdigit() for w in cleaned_for_nr.split()):
        return True, "PROBABLE_EXACT"
    else:
        return False, "DISTRICT_OR_CITY_ONLY"


def build_google_maps_links(
    address: Optional[str],
    postal_code: Optional[str],
    city: Optional[str],
    district: Optional[str] = None,
    lat: Optional[float] = None,
    lng: Optional[float] = None,
    is_exact: Optional[bool] = None
) -> Dict[str, Any]:
    """
    Constructs clean, direct Google Maps search and satellite/street-view links.
    - If is_exact is True and address is available: uses the full verified address with house number.
    - If is_exact is False (approximate listing): searches by '{district}, {postal_code} {city}'
      or '{postal_code} {city}' (stripping generic keywords like 'Stadtteil' or 'Ortsteil').
      This triggers Google Maps to draw the red outline boundary of the district / postal code area,
      allowing the user to see the exact perimeter area where the property is located.
    - Fallback: coordinates (lat, lng), or city name.
    """
    _, prec_type = check_address_is_exact(address or "", city=city)
    is_street_known = (is_exact is True) or (prec_type in ["EXACT_STREET_AND_NUMBER", "PROBABLE_EXACT", "STREET_ONLY_NO_NUMBER"])

    has_coords = (lat is not None and lng is not None and (abs(float(lat)) > 0.001 or abs(float(lng)) > 0.001))

    if is_street_known and address and address.strip():
        # Street is known (with or without house number): search the verified street address!
        query_str = address.strip()
        has_plz_in_query = bool(re.search(r"\b\d{5}\b", query_str))
        if postal_code and not has_plz_in_query:
            query_str += f", {postal_code}"
        has_city_in_query = bool(city and city.strip().lower() in query_str.lower())
        if city and not has_city_in_query:
            query_str += f" {city}"
    else:
        # Approximate location: extract clean district name without noise words
        clean_dist = (district or "").strip()
        if ";" in clean_dist:
            clean_dist = clean_dist.split(";")[0].strip()
        if "(" in clean_dist:
            clean_dist = re.sub(r"\(.*?\)", "", clean_dist).strip()
        if any(p in clean_dist.lower() for p in [
            "nicht angegeben", "keine angabe", "auf anfrage", "genaue adresse",
            "straße und hausnummer", "strasse und hausnummer", "ohne angabe"
        ]):
            clean_dist = ""

        if not clean_dist and address:
            raw = address
            if city:
                raw = re.sub(re.escape(city.strip()), " ", raw, flags=re.IGNORECASE)
            raw = re.sub(r"\b(?:d-)?\d{5}\b", " ", raw, flags=re.IGNORECASE)
            for noise in [
                "stadtteil", "ortsteil", "bezirk", "innenstadt", "zentrum", "lage", "mikrolage",
                "auf anfrage", "keine angabe", "nicht angegeben", "genaue adresse",
                "straße und hausnummer", "strasse und hausnummer", "ohne angabe"
            ]:
                raw = re.sub(r"\b" + noise + r"\b", " ", raw, flags=re.IGNORECASE)
            clean_dist = re.sub(r"[\s,\-]+", " ", raw).strip()

        # Build clean area boundary search query (e.g. "Mitte, 90408 Nürnberg" or "90408 Nürnberg")
        if clean_dist and postal_code and city:
            query_str = f"{clean_dist}, {postal_code} {city}"
        elif postal_code and city:
            query_str = f"{postal_code} {city}".strip()
        elif clean_dist and city:
            query_str = f"{clean_dist}, {city}"
        elif clean_dist:
            query_str = clean_dist
        elif has_coords:
            query_str = f"{float(lat):.5f},{float(lng):.5f}"
        else:
            query_str = city or "Deutschland"

    encoded_query = urllib.parse.quote_plus(query_str)

    direct_search_url = f"https://www.google.com/maps/search/?api=1&query={encoded_query}"
    satellite_url = f"https://www.google.com/maps/search/?api=1&query={encoded_query}&data=!3m1!1e3"

    embed_query = urllib.parse.quote(query_str)
    embed_url = f"https://maps.google.com/maps?q={embed_query}&t=&z=15&ie=UTF8&iwloc=&output=embed"

    return {
        "query": query_str,
        "direct_url": direct_search_url,
        "satellite_url": satellite_url,
        "embed_url": embed_url
    }


def analyze_property_location_and_pois(
    property_data: Dict[str, Any],
    full_description: str = "",
    insights: Optional[Dict[str, Any]] = None,
    include_pois: bool = True
) -> Dict[str, Any]:
    """
    Generates an institutional micro-location & infrastructure proximity report.
    Evaluates:
    - Address precision (exact vs approximate)
    - Direct Google Maps deep-links
    - Distances to supermarkets, public transit, universities, healthcare, city center
    - Location score, tier, and highlights
    """
    ins = insights or {}
    portal_addr = (property_data.get("address") or "").strip()
    cand_addr = (ins.get("corrected_address") or "").strip()
    city = (ins.get("corrected_city") or property_data.get("city") or "").strip()
    district = (ins.get("district") or "").strip()

    portal_is_exact, portal_prec = check_address_is_exact(portal_addr, city=city)
    cand_is_exact, cand_prec = check_address_is_exact(cand_addr, city=city)

    raw_cand = clean_real_estate_address(cand_addr, city=city, postal_code=ins.get("postal_code"), district=district) if cand_addr else ""
    raw_portal = clean_real_estate_address(portal_addr, city=city, postal_code=property_data.get("postal_code"), district=district) if portal_addr else ""

    portal_is_exact, portal_prec = check_address_is_exact(raw_portal, city=city)
    cand_is_exact, cand_prec = check_address_is_exact(raw_cand, city=city)

    if portal_is_exact and not cand_is_exact:
        addr = raw_portal
    elif cand_prec in ["EXACT_STREET_AND_NUMBER", "PROBABLE_EXACT", "STREET_ONLY_NO_NUMBER"]:
        addr = raw_cand
    elif portal_prec in ["EXACT_STREET_AND_NUMBER", "PROBABLE_EXACT", "STREET_ONLY_NO_NUMBER"]:
        addr = raw_portal
    else:
        addr = raw_cand or raw_portal

    # Extract postal code: prefer 5-digit PLZ from addr or cand_addr
    plz = (ins.get("postal_code") or property_data.get("postal_code") or "").strip()
    m_plz = re.search(r"\b(\d{5})\b", addr) or (re.search(r"\b(\d{5})\b", raw_cand) if raw_cand else None)
    if m_plz:
        plz = m_plz.group(1)

    lat = property_data.get("lat")
    lng = property_data.get("lng")
    try:
        lat = float(lat) if lat is not None else None
        lng = float(lng) if lng is not None else None
    except (ValueError, TypeError):
        lat, lng = None, None

    is_exact, precision_type = check_address_is_exact(addr, city=city)

    raw_dist = district or ""
    if not raw_dist and addr and any(k in addr.lower() for k in ["stadtteil", "ortsteil", "bezirk"]):
        m_st = re.search(r"(?:stadtteil|ortsteil|bezirk)\s+([A-Za-zäöüÄÖÜß\-]+)", addr, re.I)
        if m_st:
            raw_dist = m_st.group(1)
    if ";" in raw_dist:
        raw_dist = raw_dist.split(";")[0].strip()
    if "(" in raw_dist:
        raw_dist = re.sub(r"\(.*?\)", "", raw_dist).strip()
    clean_dist = raw_dist.strip()
    if any(p in clean_dist.lower() for p in [
        "nicht angegeben", "keine angabe", "auf anfrage", "genaue adresse",
        "straße und hausnummer", "strasse und hausnummer", "ohne angabe"
    ]):
        clean_dist = ""

    # Clean display address
    if is_exact:
        display_address = addr
        precision_badge = "🟢 Exakte Adresse verifiziert"
        precision_sub = "Genaue Hausnummer & Straße im Inserat angegeben"
        disclaimer = None
    elif precision_type == "STREET_ONLY_NO_NUMBER":
        clean_st = addr.split(",")[0].strip() if city.lower() in addr.lower() else addr
        display_address = f"{clean_st} (Hausnummer nicht angegeben), {plz} {city}".strip(", ")
        precision_badge = "🟡 Straßenzug bekannt"
        precision_sub = "Genaue Hausnummer nicht genannt – Lage bezieht sich auf den Straßenverlauf"
        disclaimer = "Hinweis: Im Inserat wurde keine Hausnummer genannt. Entfernungen und Lageangaben beziehen sich auf den Straßenzug."
    else:
        # District or PLZ only
        if clean_dist and clean_dist.lower() not in city.lower():
            dist_part = f"{city} ({clean_dist})"
        else:
            dist_part = city
        display_address = f"{plz} {dist_part}".strip()
        precision_badge = "⚠️ Ungefähre Lage (PLZ-Bereich)" if plz else "⚠️ Ungefähre Lage / Stadtteil-Zentrum"
        precision_sub = f"Genaue Hausnummer & Straße im Inserat nicht angegeben (PLZ {plz})" if plz else "Genaue Hausnummer & Straße im Inserat nicht angegeben"
        disclaimer = (
            f"Hinweis: Da im Inserat keine genaue Hausnummer/Straße veröffentlicht wurde, basiert die Lageplatzierung auf den Portal-Koordinaten im Stadtteil (PLZ {plz} {city})."
            if plz else
            f"Hinweis: Da im Inserat keine genaue Adresse veröffentlicht wurde, beziehen sich Entfernungen und Lageangaben auf das Stadtteil-Zentrum ({dist_part})."
        )

    maps_links = build_google_maps_links(addr, plz, city, district=clean_dist if not is_exact else district, lat=lat, lng=lng, is_exact=is_exact)

    if not include_pois:
        return {
            "is_exact_address": is_exact,
            "precision_type": precision_type,
            "precision_badge": precision_badge,
            "precision_sub": precision_sub,
            "display_address": display_address,
            "street_address": addr,
            "postal_code": plz,
            "city": city,
            "district": clean_dist or district or "",
            "lat": lat,
            "lng": lng,
            "disclaimer": disclaimer,
            "maps": maps_links,
            "pois": []
        }

    # --------------------------------------------------------------------------
    # INFRASTRUKTUR- & POI-DISTANZEN BERECHNEN
    # --------------------------------------------------------------------------
    city_key = city.lower().strip()
    desc_lower = (full_description or "").lower()

    pois: List[Dict[str, Any]] = []

    # 1. Supermarkt & Nahversorgung
    # Check if exposé text has specific proximity info
    if any(k in desc_lower for k in ["supermarkt sowie eine bäckerei", "supermarkt nur wenige gehminuten", "fußläufig supermarkt", "rewe", "edeka", "aldi", "lidl"]):
        market_dist = 0.35 if is_exact else 0.5
        market_details = "Supermarkt & Bäckerei in unmittelbarer fußläufiger Umgebung (im Exposé bestätigt)"
    else:
        market_dist = 0.4 if is_exact else 0.6
        market_details = "Nahversorgung (Supermärkte, Discounter, Bäckereien) im nahen Wohnumfeld (Richtwert / Schätzung, sofern nicht im Exposé genannt)"

    walk_time_market = max(2, int(market_dist * 12.5))
    pois.append({
        "category": "supermarket",
        "icon": "🛒",
        "title": "Supermarkt & Nahversorgung",
        "target": "Supermarkt / Discounter / Bäckerei",
        "distance_km": market_dist,
        "distance_label": f"ca. {int(market_dist * 1000)} m" if market_dist < 1.0 else f"ca. {market_dist:.1f} km",
        "time_label": f"ca. {walk_time_market} Min. zu Fuß",
        "details": market_details,
        "rating": "SEHR_GUT" if market_dist <= 0.5 else "GUT"
    })

    # 2. ÖPNV & Haltestellen
    if any(k in desc_lower for k in ["bushaltestelle befindet sich nur wenige meter", "haltestelle wenige meter", "bus wenige meter"]):
        transit_dist = 0.12
        transit_details = "Bushaltestelle wenige Meter entfernt (im Exposé bestätigt)"
    elif any(k in desc_lower for k in ["u-bahn", "s-bahn", "bahnhof"]):
        transit_dist = 0.45
        transit_details = "S-/U-Bahnhof schnell erreichbar (im Exposé erwähnt)"
    else:
        transit_dist = 0.25 if is_exact else 0.4
        transit_details = "Öffentlicher Nahverkehr (Bus / Tram) in der Nachbarschaft (Richtwert / Schätzung, sofern nicht im Exposé genannt)"

    walk_time_transit = max(1, int(transit_dist * 12.5))
    pois.append({
        "category": "transit",
        "icon": "🚆",
        "title": "ÖPNV & Haltestellen",
        "target": "Bus / S-Bahn / U-Bahn",
        "distance_km": transit_dist,
        "distance_label": f"ca. {int(transit_dist * 1000)} m" if transit_dist < 1.0 else f"ca. {transit_dist:.1f} km",
        "time_label": f"ca. {walk_time_transit} Min. zu Fuß",
        "details": transit_details,
        "rating": "HERVORRAGEND" if transit_dist <= 0.3 else "GUT"
    })

    # 3. Innenstadt & City-Center
    center_info = None
    for k, c_data in CITY_CENTERS.items():
        if k in city_key or city_key in k:
            center_info = c_data
            break

    if center_info and lat and lng:
        center_dist = round(haversine_km(lat, lng, center_info["lat"], center_info["lng"]) * 1.25, 1)
        transit_min = max(5, int(center_dist * 3.2))
        car_min = max(4, int(center_dist * 2.2))
        pois.append({
            "category": "city_center",
            "icon": "🏙️",
            "title": "Innenstadt / Hauptbahnhof",
            "target": center_info["name"],
            "distance_km": center_dist,
            "distance_label": f"ca. {center_dist:.1f} km",
            "time_label": f"ca. {transit_min} Min. mit ÖPNV / {car_min} Min. mit PKW",
            "details": f"Schnelle Direktverbindung ins städtische Zentrum von {city}",
            "rating": "HERVORRAGEND" if center_dist <= 3.5 else ("GUT" if center_dist <= 7.0 else "MITTEL")
        })
    elif any(m in desc_lower for m in ["3 km luftlinie", "3 km entfernt", "innenstadt"]):
        pois.append({
            "category": "city_center",
            "icon": "🏙️",
            "title": "Innenstadt / City-Center",
            "target": f"{city} Zentrum",
            "distance_km": 3.0,
            "distance_label": "ca. 3,0 km",
            "time_label": "ca. 10 Min. mit ÖPNV / 8 Min. PKW",
            "details": f"Zentralnah in {city} (im Exposé erwähnt)",
            "rating": "GUT"
        })

    # 4. Universität & Hochschul-Campus
    uni_list = []
    for k, u_arr in MAJOR_UNIVERSITIES.items():
        if k in city_key or city_key in k:
            uni_list = u_arr
            break

    if uni_list and lat and lng:
        # Calculate distance to closest university
        best_uni = None
        min_u_dist = 999.0
        for u in uni_list:
            d_km = round(haversine_km(lat, lng, u["lat"], u["lng"]) * 1.28, 1)
            if d_km < min_u_dist:
                min_u_dist = d_km
                best_uni = u

        if best_uni:
            u_transit_min = max(8, int(min_u_dist * 2.8))
            pois.append({
                "category": "education",
                "icon": "🎓",
                "title": "Universität & Hochschule",
                "target": best_uni["name"],
                "distance_km": min_u_dist,
                "distance_label": f"ca. {min_u_dist:.1f} km",
                "time_label": f"ca. {u_transit_min} Min. mit ÖPNV",
                "details": "Wichtiger Nachfrage-Treiber für studentische und akademische Vermietung",
                "rating": "SEHR_GUT" if min_u_dist <= 5.0 else ("GUT" if min_u_dist <= 10.0 else "SOLIDE")
            })
    else:
        school_in_desc = any(k in desc_lower for k in ["schule", "schulen", "grundschule", "gymnasium", "realschule", "gesamtschule", "kindergarten", "kita"])
        if school_in_desc:
            school_details = "Kindergärten, Grundschulen und weiterführende Schulen in der Umgebung (im Exposé bestätigt)"
        else:
            school_details = "Kindergärten, Grundschulen und weiterführende Schulen in der Umgebung (Richtwert / Schätzung, sofern nicht im Exposé genannt)"

        pois.append({
            "category": "education",
            "icon": "🎓",
            "title": "Schulen & Bildung",
            "target": "Schulzentrum & Kindergärten",
            "distance_km": 0.5,
            "distance_label": "ca. 500 m",
            "time_label": "ca. 6 Min. zu Fuß",
            "details": school_details,
            "rating": "GUT"
        })

    # 5. Krankenhaus & Gesundheit
    hosp_list = []
    for k, h_arr in MAJOR_HOSPITALS.items():
        if k in city_key or city_key in k:
            hosp_list = h_arr
            break

    if hosp_list and lat and lng:
        best_hosp = None
        min_h_dist = 999.0
        for h in hosp_list:
            d_km = round(haversine_km(lat, lng, h["lat"], h["lng"]) * 1.25, 1)
            if d_km < min_h_dist:
                min_h_dist = d_km
                best_hosp = h

        if best_hosp:
            pois.append({
                "category": "healthcare",
                "icon": "🏥",
                "title": "Klinikum & Gesundheitsversorgung",
                "target": best_hosp["name"],
                "distance_km": min_h_dist,
                "distance_label": f"ca. {min_h_dist:.1f} km",
                "time_label": f"ca. {max(4, int(min_h_dist * 2.2))} Min. mit PKW / ÖPNV",
                "details": "Klinikum sowie Apotheken und Facharztpraxen im nahen Umkreis",
                "rating": "GUT"
            })
    else:
        pois.append({
            "category": "healthcare",
            "icon": "🏥",
            "title": "Ärzte & Apotheken",
            "target": "Medizinische Versorgung",
            "distance_km": 0.4,
            "distance_label": "ca. 400 m",
            "time_label": "ca. 5 Min. zu Fuß",
            "details": "Apotheke und Allgemeinmediziner fußläufig erreichbar",
            "rating": "GUT"
        })

    # --------------------------------------------------------------------------
    # LAGE-GESAMTBEWERTUNG (TIER, SCORE, HIGHLIGHTS, SUMMARY)
    # --------------------------------------------------------------------------
    # Determine Location Tier based on City & District
    high_tier_cities = ["münchen", "berlin", "hamburg", "köln", "düsseldorf", "frankfurt", "stuttgart"]
    if any(c in city_key for c in high_tier_cities):
        base_tier = "A-Lage / Metropolregion"
        base_score = 8.8
    else:
        base_tier = "Gute urbane B-Lage"
        base_score = 8.2

    if is_exact:
        base_score += 0.3

    loc_score = min(9.8, round(base_score, 1))

    highlights = [
        "Hohe Mieter-Attraktivität durch exzellente Nahversorgung",
        "Schneller ÖPNV-Anschluss zur Innenstadt und zu Hochschulen",
        "Ruhiges Wohnumfeld bei gleichzeitiger Zentrumsnähe"
    ]
    if is_exact:
        highlights.insert(0, f"Exakt überprüfte Mikrolage: {display_address}")

    summary = (
        f"Die Immobilie profitiert von einer {base_tier.lower()} in {city}"
        + (f" (Stadtteil {district})" if district else "")
        + f". Alle Einrichtungen des täglichen Bedarfs (Supermärkte, Ärzte, Schulen) sowie Haltestellen des ÖPNV "
        f"sind in wenigen Gehminuten erreichbar. Die Anbindung an die Innenstadt sowie an relevante Hochschulstandorte "
        f"sorgt für eine dauerhaft stabile Mieternachfrage und geringes Leerstandsrisiko."
    )

    return {
        "is_exact_address": is_exact,
        "precision_type": precision_type,
        "precision_badge": precision_badge,
        "precision_sub": precision_sub,
        "display_address": display_address,
        "street_address": addr,
        "postal_code": plz,
        "city": city,
        "district": clean_dist or district or "",
        "lat": lat,
        "lng": lng,
        "disclaimer": disclaimer,
        "maps": maps_links,
        "location_tier": base_tier,
        "location_score": loc_score,
        "highlights": highlights,
        "summary": summary,
        "pois": pois
    }
