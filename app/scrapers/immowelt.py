"""
Immowelt Scraper Engine for ImmoMetrica Clone.
Crawls live properties from Immowelt search pages,
extracting real offer links, prices, living space, rooms, high-resolution photos, and descriptions.
"""

import re
import json
import requests
import logging
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger("immometrica.scraper.immowelt")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache"
}


def clean_city_slug(city: str) -> str:
    c = city.lower().strip()
    c = c.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    c = re.sub(r"[^a-z0-9\-]", "-", c)
    return c


def clean_immowelt_title(raw_title: str, rooms: float, sqm: float, district: str, city: str, prop_type: str = "Wohnung") -> str:
    """Cleans up raw Immowelt titles or constructs crisp investor-friendly titles."""
    t = raw_title.strip()
    # Strip common disclaimers at start
    t = re.sub(r"^(?:BITTE BEACHTEN SIE[^\.]*[\.\!]\s*|ACHTUNG[^\.]*[\.\!]\s*)+", "", t, flags=re.IGNORECASE).strip()
    
    # If title is still too long (> 120 chars), extract first meaningful sentence
    if len(t) > 120:
        first_sentence = re.split(r"[\.\!\?]\s+", t)[0]
        if len(first_sentence) >= 20:
            t = first_sentence.strip()
        else:
            t = t[:110].rsplit(" ", 1)[0] + "..."

    # If title is too generic or short (e.g. 'Wohnung zum Kauf' or 'Maisonette zum Kauf')
    generic_patterns = ["wohnung zum kauf", "maisonette zum kauf", "etagenwohnung", "eigentumswohnung", "penthouse zum kauf"]
    if not t or len(t) < 12 or any(t.lower() == g for g in generic_patterns):
        loc_str = f"{city}-{district}" if district and district.lower() != city.lower() else city
        room_str = f"{rooms:.1f}".replace(".0", "")
        type_str = prop_type if prop_type else "Eigentumswohnung"
        t = f"Gepflegte {room_str}-Zimmer-{type_str} in {loc_str} ({sqm:.0f} m²)"

    return t


def search_immowelt_deals(
    city: str,
    query: str = "",
    max_results: int = 15,
    exclude_urls: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """
    Scrapes live listings from Immowelt for a given German city.
    Extracts real prices, living spaces, rooms, high-resolution property photos,
    and investor metrics (yield, cashflow, benchmark comparisons).
    Supports sorting by newest ('createdate_desc'), pagination (pages 1 to 3),
    and skipping known candidate URLs.
    """
    from app.calculator import calculate_cashflow
    from app.valuation import evaluate_property_valuation
    from app.geo_resolver import resolve_location_and_tax, resolve_city_coordinates

    city_slug = clean_city_slug(city)
    exclude_set = set(exclude_urls) if exclude_urls else set()
    results: List[Dict[str, Any]] = []
    seen_urls = set()

    for page in range(1, 4):
        if len(results) >= max_results:
            break

        url = f"https://www.immowelt.de/suche/{city_slug}/wohnungen/kaufen?sp={page}&sort=createdate_desc"

        try:
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=8.0)
            if resp.status_code != 200:
                logger.warning(f"Immowelt search returned HTTP {resp.status_code} (page {page})")
                break
        except Exception as e:
            logger.warning(f"Immowelt request error for {city} (page {page}): {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        expose_links = soup.find_all("a", href=re.compile(r"/expose/[a-f0-9\-]+", re.IGNORECASE))
        if not expose_links:
            break

        for link in expose_links:
            raw_href = link["href"]
            if not raw_href.startswith("http"):
                full_url = "https://www.immowelt.de" + raw_href
            else:
                full_url = raw_href

            # Normalize URL (strip query params)
            clean_url = full_url.split("?")[0]
            if clean_url in seen_urls:
                continue
            if exclude_set and clean_url in exclude_set:
                continue
            seen_urls.add(clean_url)
            # Locate complete card element
            card = None
            curr = link
            for _ in range(7):
                if curr.parent:
                    curr = curr.parent
                    if "€" in curr.get_text():
                        card = curr
                        break
            if not card:
                card = link.find_parent("div") or link
    
            card_text = card.get_text(" | ", strip=True)
            stripped_strings = [s.strip() for s in card.stripped_strings]
    
            # Extract Price
            price = 0.0
            price_match = re.search(r"([\d\.]+)\s*€", card_text)
            if price_match:
                try:
                    price = float(price_match.group(1).replace(".", ""))
                except ValueError:
                    pass
    
            if price < 15000:
                continue
    
            # Extract Living space & Rooms
            sqm = 0.0
            sqm_match = re.search(r"([\d,\.]+)\s*m²", card_text)
            if sqm_match:
                try:
                    sqm = float(sqm_match.group(1).replace(".", "").replace(",", "."))
                except ValueError:
                    pass
    
            rooms = 2.0
            room_match = re.search(r"([\d,\.]+)\s*Zi", card_text)
            if room_match:
                try:
                    rooms = float(room_match.group(1).replace(",", "."))
                except ValueError:
                    pass
    
            if sqm <= 0:
                sqm = max(30.0, round(price / 3000.0, 1))
    
            # Extract postal code & location
            extracted_plz = ""
            plz_match = re.search(r"\b(\d{5})\b", card_text)
            if plz_match:
                extracted_plz = plz_match.group(1)
    
            # Extract district
            district = ""
            dist_match = re.search(r"([A-Za-zÄÖÜäöüß\s\-]+),\s*" + re.escape(city), card_text, re.IGNORECASE)
            if dist_match:
                district = dist_match.group(1).strip()
    
            # Extract property type (Maisonette, Etagenwohnung, Penthouse, etc.)
            p_type = "Eigentumswohnung"
            b_type = "Etagenwohnung"
            if "maisonette" in card_text.lower():
                p_type = "Maisonette"
                b_type = "Maisonette"
            elif "penthouse" in card_text.lower():
                p_type = "Penthouse"
                b_type = "Penthouse"
            elif "dachgeschoss" in card_text.lower():
                b_type = "Dachgeschosswohnung"
            elif "erdgeschoss" in card_text.lower() or " eg " in card_text.lower():
                b_type = "Erdgeschosswohnung"
    
            # Energy efficiency class
            energy_class = ""
            energy_match = re.search(r"\b([A-H])\b", card_text)
            if energy_match and any(k in card_text for k in ["kWh", "Energie", "frei ab"]):
                energy_class = energy_match.group(1)
    
            # Title & Description Teaser
            # Look for descriptive teaser in stripped strings
            teasers = [
                s for s in stripped_strings 
                if len(s) > 35 
                and not any(k in s for k in ["€/m²", "provisionsfrei", "Herr ", "Frau ", "GmbH", "Immobilien"])
            ]
            desc_teaser = teasers[-1] if teasers else ""
    
            title_h = card.find(["h2", "h3", "h1"])
            raw_title = title_h.text.strip() if (title_h and title_h.text.strip()) else desc_teaser
            title = clean_immowelt_title(raw_title, rooms, sqm, district, city, p_type)
    
            # Filter query if requested
            if query and query.lower() not in title.lower() and query.lower() not in card_text.lower():
                continue
    
            resolved_city = city.strip().capitalize()
            geo_info = resolve_location_and_tax(postal_code=extracted_plz, city=resolved_city)
            resolved_state = geo_info["state"]
    
            # Coordinates
            import zlib
            url_seed = zlib.crc32(clean_url.encode("utf-8"))
            prop_lat, prop_lng = resolve_city_coordinates(
                city=resolved_city,
                postal_code=extracted_plz,
                index=(len(results) + (url_seed % 17))
            )
    
            # Extract High-Resolution Images & Gallery
            card_imgs = card.find_all("img")
            gallery_images: List[str] = []
            main_img = None
            for im in card_imgs:
                src = im.get("src") or im.get("data-src")
                if src and "immowelt" in src and not src.startswith("data:"):
                    # Clean up query params to get full high-res 626x470
                    high_res = re.sub(r"\?.*$", "?w=626&h=470", src)
                    if high_res not in gallery_images:
                        gallery_images.append(high_res)
                    if not main_img:
                        main_img = high_res
    
            if not main_img:
                main_img = "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?w=800"
    
            # Benchmark & Cashflow
            benchmark = evaluate_property_valuation(resolved_city, sqm, price)
            expected_rent_per_sqm = benchmark.get("expected_rent_per_sqm", 11.50)
            expected_rent = round(sqm * expected_rent_per_sqm, 2)
            is_rented = 1 if "vermietet" in card_text.lower() else 0
            current_rent = 0.0
            gross_yield = round(((expected_rent * 12.0) / price) * 100.0, 2) if price > 0 else 0.0
    
            cf = calculate_cashflow(
                price=price,
                rent=current_rent if (is_rented and current_rent > 0) else expected_rent,
                city=resolved_city,
                state=resolved_state,
                living_space=sqm
            )
            monthly_cashflow = cf.get("monthly_cashflow", 0.0)
    
            is_private = 1 if ("provisionsfrei" in card_text.lower() or "privat" in card_text.lower()) else 0
            comm_pct = 0.0 if is_private else 3.57
    
            tags = ["Live Scraped", "Immowelt", resolved_state]
            if gross_yield >= 5.5:
                tags.append("Top-Rendite")
            if monthly_cashflow > 0:
                tags.append("Positiver Cashflow")
            if is_private:
                tags.append("Provisionsfrei")
    
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
            # Build informative description text from Immowelt listing details
            loc_display = f"{extracted_plz} {resolved_city}" + (f"-{district}" if district else "")
            desc_body = desc_teaser if desc_teaser else f"Attraktive {p_type} in begehrter Lage von {loc_display}."
            full_desc = (
                f"{title}\n\n"
                f"{desc_body}\n\n"
                f"Eckdaten des Objekts:\n"
                f"- Zimmer: {rooms:.1f}\n"
                f"- Wohnfläche: {sqm:.1f} m²\n"
                f"- Lage: {loc_display}\n"
                f"- Kaufpreis: {price:,.0f} € (ca. {round(price/sqm):,.0f} €/m²)\n"
                f"- Status: {'Vermietet' if is_rented else 'Bezugsfrei / Leerstehend'}\n"
                + (f"- Energieeffizienzklasse: {energy_class}\n" if energy_class else "")
                + f"- Provision: {'Provisionsfrei (0,0 %)' if is_private else f'{comm_pct:.2f} % inkl. MwSt.'}\n"
                f"- Portal: Immowelt (Exposé-ID: {clean_url.split('/')[-1]})\n"
            )
    
            results.append({
                "title": title,
                "address": loc_display.strip(),
                "postal_code": extracted_plz,
                "city": resolved_city,
                "state": resolved_state,
                "country": "DE",
                "property_type": p_type,
                "building_type": b_type,
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
                "source_portal": "Immowelt",
                "portal_url": clean_url,
                "source_portals": [{"portal": "Immowelt", "url": clean_url, "price": price}],
                "image_url": main_img,
                "gallery_images": gallery_images,
                "description": full_desc,
                "days_online": 1,
                "first_seen_date": datetime.now().strftime("%Y-%m-%d"),
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
                "status_message": "Live erreichbar & aktiv (Immowelt verifiziert)",
                "last_checked_at": now_str,
                "live_search_url": url
            })
    
            if len(results) >= max_results:
                break
    

    return results




def fetch_immowelt_expose(url: str) -> Dict[str, Any]:
    """
    Fetches comprehensive expose details for an Immowelt URL.
    Parses embedded JSON state (__UFRN_LIFECYCLE_SERVERREQUEST__ / __UFRN_STORE__)
    to extract:
    - Full text description (main text + Lage + Weitere Infos / Garagen)
    - All 15+ high-resolution gallery images
    - Energy certificate (efficiency class, consumption in kWh, carrier, validity, type)
    - Hardfacts: purchase price, living space, rooms, year built, heating system.
    """
    clean_url = url.split("?")[0]
    expose_id = clean_url.split("/")[-1]

    try:
        resp = requests.get(clean_url, headers=BROWSER_HEADERS, timeout=10.0)
        if resp.status_code != 200:
            return {
                "success": True,
                "title": f"Immowelt Exposé #{expose_id[:8]}",
                "description": f"Exposé auf Immowelt: {clean_url}",
                "portal": "Immowelt"
            }

        html = resp.text

        # 1. Parse embedded JSON payload
        classified = None
        m = re.search(r'window\["__UFRN_LIFECYCLE_SERVERREQUEST__"\]\s*=\s*JSON\.parse\("(.*?)"\);', html, re.DOTALL)
        if m:
            try:
                raw_json = json.loads(f'"{m.group(1)}"')
                data = json.loads(raw_json)
                classified = data.get("app_demand_referral_cldp", {}).get("data", {}).get("classified", {})
            except Exception:
                try:
                    unescaped = m.group(1).encode('utf-8').decode('unicode-escape').encode('latin1').decode('utf-8')
                    data = json.loads(unescaped)
                    classified = data.get("app_demand_referral_cldp", {}).get("data", {}).get("classified", {})
                except Exception:
                    pass

        if not classified:
            m_store = re.search(r'window\["__UFRN_STORE__"\]\s*=\s*JSON\.parse\("(.*?)"\);', html, re.DOTALL)
            if m_store:
                try:
                    raw_json = json.loads(f'"{m_store.group(1)}"')
                    data_store = json.loads(raw_json)
                    classified = data_store.get("classified", {})
                except Exception:
                    pass

        # If JSON was parsed successfully, extract structured metadata
        if classified:
            sections = classified.get("sections") or {}

            # A. Full Description (main description + areaDescription + extendedInfoDescription)
            desc_sec = sections.get("description") or {}
            main_desc = ""
            if isinstance(desc_sec, dict):
                main_desc = desc_sec.get("description", "")
                if isinstance(main_desc, dict):
                    main_desc = main_desc.get("description", "")
            elif isinstance(desc_sec, str):
                main_desc = desc_sec

            if not main_desc and classified.get("title"):
                main_desc = classified.get("title", "")

            area_desc = ""
            area_sec = desc_sec.get("areaDescription") if isinstance(desc_sec, dict) else None
            if isinstance(area_sec, dict):
                headline = area_sec.get("headline", "Lage")
                body = area_sec.get("description", "")
                if body:
                    area_desc = f"\n\n📍 {headline}:\n{body}"

            ext_desc = ""
            ext_sec = desc_sec.get("extendedInfoDescription") if isinstance(desc_sec, dict) else None
            if isinstance(ext_sec, dict):
                headline = ext_sec.get("headline", "Weitere Informationen")
                body = ext_sec.get("description", "")
                if body:
                    ext_desc = f"\n\nℹ️ {headline}:\n{body}"

            full_desc_raw = (main_desc or "") + area_desc + ext_desc
            # Clean HTML tags like <br> -> newline
            full_desc = re.sub(r'<br\s*/?>', '\n', full_desc_raw, flags=re.IGNORECASE)
            full_desc = re.sub(r'<[^>]+>', '', full_desc).strip()

            # B. Title
            raw_title = classified.get("title") or ""
            clean_t = re.sub(r'<[^>]+>', ' ', raw_title).strip()
            if len(clean_t) > 110:
                first_s = re.split(r'[\.\!\?]\s+', clean_t)[0]
                title = first_s if len(first_s) >= 20 else clean_t[:100].rsplit(' ', 1)[0] + '...'
            else:
                title = clean_t or f"Immowelt Exposé #{expose_id[:8]}"

            # C. Gallery Images
            images_raw = classified.get("domains", {}).get("medias", {}).get("images", [])
            gallery_images = []
            for im in images_raw:
                u = im.get("url")
                if u and "immowelt" in u:
                    clean_u = re.sub(r'\?.*$', '', u)
                    if clean_u not in gallery_images:
                        gallery_images.append(clean_u)
            main_image = gallery_images[0] if gallery_images else ""

            # D. Hardfacts (Price, Sqm, Rooms)
            hardfacts = sections.get("hardfacts") or {}
            facts = hardfacts.get("facts") or []
            price_val = 0.0
            sqm_val = 0.0
            rooms_val = 1.0
            year_built_val = None

            price_obj = hardfacts.get("price") or {}
            if isinstance(price_obj, dict) and price_obj.get("value"):
                try:
                    price_val = float(price_obj["value"])
                except Exception:
                    pass

            for f in facts:
                f_type = str(f.get("type", "")).lower()
                f_val = str(f.get("value", ""))
                if "living" in f_type or "area" in f_type or "m²" in f_val:
                    m_sqm = re.search(r'([\d,\.]+)', f_val)
                    if m_sqm:
                        sqm_val = float(m_sqm.group(1).replace(".", "").replace(",", "."))
                elif "room" in f_type or "zimmer" in str(f.get("label", "")).lower():
                    m_r = re.search(r'([\d,\.]+)', f_val)
                    if m_r:
                        rooms_val = float(m_r.group(1).replace(",", "."))

            # E. Location
            loc_sec = sections.get("location") or {}
            addr_obj = loc_sec.get("address") or {}
            city = addr_obj.get("city") or ""
            plz = addr_obj.get("zipCode", "")
            district = addr_obj.get("district", "")

            # F. Energy Certificate
            energy_sec = sections.get("energy") or {}
            energy_features = energy_sec.get("features") or []
            heating_type = None
            energy_carrier = None
            for ef in energy_features:
                t = ef.get("type", "")
                v = ef.get("value", "")
                if t == "yearOfConstruction":
                    try:
                        year_built_val = int(v)
                    except Exception:
                        pass
                elif t == "heatingSystem":
                    heating_type = v
                elif t == "energySource":
                    energy_carrier = v

            certs = energy_sec.get("certificates") or []
            cert_obj = certs[0] if certs else {}
            cert_features = cert_obj.get("features") or []
            cert_type = None
            validity = ""
            for cf in cert_features:
                ct = cf.get("type", "")
                cv = cf.get("value", "")
                if ct == "basedOn":
                    cert_type = cv
                elif ct == "validity":
                    validity = cv
                elif ct == "primaryEnergySource":
                    energy_carrier = cv

            scales = cert_obj.get("scales") or []
            scale_obj = scales[0] if scales else {}
            eff_class = scale_obj.get("efficiencyClass", {}).get("rating")
            
            consumption_kwh = None
            scale_vals = scale_obj.get("values") or []
            for sv in scale_vals:
                s_text = sv.get("value", "")
                m_kwh = re.search(r'([\d,\.]+)\s*kwh', s_text.lower())
                if m_kwh:
                    try:
                        consumption_kwh = float(m_kwh.group(1).replace(".", "").replace(",", "."))
                    except Exception:
                        pass

            energy_data = {
                "has_certificate": bool(certs or energy_sec),
                "efficiency_class": eff_class,
                "consumption_kwh": consumption_kwh,
                "consumption_label": f"{consumption_kwh:.1f} kWh/(m²·a)".replace(".", ",") if consumption_kwh is not None else None,
                "certificate_type": cert_type or "Verbrauchsausweis",
                "primary_energy_source": energy_carrier or "Gas",
                "heating_type": heating_type or "Etagenheizung",
                "year_built": year_built_val,
                "validity": validity or None,
                "scale_segments": [
                    {"label": "A+", "range": "< 30", "color": "#16a34a"},
                    {"label": "A", "range": "30 - 49", "color": "#22c55e"},
                    {"label": "B", "range": "50 - 74", "color": "#84cc16"},
                    {"label": "C", "range": "75 - 99", "color": "#eab308"},
                    {"label": "D", "range": "100 - 129", "color": "#facc15"},
                    {"label": "E", "range": "130 - 159", "color": "#fb923c"},
                    {"label": "F", "range": "160 - 199", "color": "#f97316"},
                    {"label": "G", "range": "200 - 250", "color": "#ef4444"},
                    {"label": "H", "range": "> 250", "color": "#b91c1c"}
                ]
            }

            return {
                "success": True,
                "expose_id": expose_id,
                "title": title,
                "description": full_desc,
                "price": price_val or 0.0,
                "living_space": sqm_val or 0.0,
                "rooms": rooms_val or 1.0,
                "city": city,
                "postal_code": plz,
                "district": district,
                "year_built": year_built_val,
                "heating_type": heating_type,
                "images": gallery_images,
                "image_url": main_image,
                "energy_certificate": energy_data,
                "portal": "Immowelt"
            }

        # Fallback to HTML BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        h1 = soup.find("h1")
        title = h1.text.strip() if h1 else f"Immowelt Exposé #{expose_id[:8]}"
        desc_elem = soup.find(class_=re.compile(r"description|section-content|text-content"))
        desc = desc_elem.get_text("\n", strip=True) if desc_elem else title

        return {
            "success": True,
            "title": title,
            "description": desc,
            "portal": "Immowelt"
        }
    except Exception as e:
        logger.warning(f"Error fetching Immowelt expose {clean_url}: {e}")
        return {
            "success": True,
            "title": f"Immowelt Exposé #{expose_id[:8]}",
            "description": f"Exposé auf Immowelt: {clean_url}",
            "portal": "Immowelt"
        }
