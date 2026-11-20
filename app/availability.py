"""
Live Listing Availability Checker, Scoped Portal Verifier, and Listing Content Fetcher.
Accurately checks whether property listings on external portals are still live and reachable,
without false positives from footer/i18n templates (e.g. data-soldlabel or recommendations).
Also extracts full property descriptions and features for AI Due Diligence.
"""

import requests
from typing import Dict, Any, Optional
from datetime import datetime
import re
from bs4 import BeautifulSoup

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache"
}

def generate_live_search_url(source_portal: str, city: str, property_type: str = "") -> str:
    """Generates guaranteed live active search URLs on portals for that city/type"""
    city_clean = city.lower().replace(" ", "-").replace("ü", "ue").replace("ä", "ae").replace("ö", "oe")
    if "scout" in source_portal.lower():
        return f"https://www.immobilienscout24.de/Suche/de/{city_clean}/wohnung-kaufen"
    elif "kleinanzeigen" in source_portal.lower():
        return f"https://www.kleinanzeigen.de/s-immobilien/{city_clean}/c195"
    elif "immowelt" in source_portal.lower():
        return f"https://www.immowelt.de/suche/{city_clean}/wohnungen/kaufen"
    elif "zvg" in source_portal.lower():
        return "https://www.zvg-portal.de/"
    else:
        return f"https://www.google.com/search?q={city_clean}+immobilie+kaufen+{source_portal}"

def _is_valid_german_coord(lat: float, lng: float) -> bool:
    return 47.2 <= lat <= 55.2 and 5.5 <= lng <= 15.6


def _extract_portal_location(soup: BeautifulSoup, raw_html: str) -> Dict[str, Any]:
    """
    Extrahiert die vom Portal eingebetteten ungefähren / exakten Kartenkoordinaten
    (z. B. Kleinanzeigen og:latitude / og:longitude sowie Straße & PLZ/Ort).
    """
    lat_val: Optional[float] = None
    lng_val: Optional[float] = None
    street_addr: Optional[str] = None
    postal_code: Optional[str] = None
    locality: Optional[str] = None

    # 1. Meta tags (og:latitude / og:longitude, itemprop latitude / longitude)
    lat_meta = soup.find("meta", attrs={"property": re.compile(r"latitude", re.I)}) or soup.find("meta", attrs={"itemprop": "latitude"})
    lng_meta = soup.find("meta", attrs={"property": re.compile(r"longitude", re.I)}) or soup.find("meta", attrs={"itemprop": "longitude"})
    if lat_meta and lng_meta and lat_meta.get("content") and lng_meta.get("content"):
        try:
            la = float(str(lat_meta["content"]).replace(",", ".").strip())
            ln = float(str(lng_meta["content"]).replace(",", ".").strip())
            if _is_valid_german_coord(la, ln):
                lat_val, lng_val = round(la, 6), round(ln, 6)
        except (ValueError, TypeError):
            pass

    # 2. JSON / inline fallback if meta absent
    if (lat_val is None or lng_val is None) and raw_html:
        m_lat = re.search(r'"latitude"\s*:\s*([+-]?\d{2}\.\d{3,})', raw_html)
        m_lng = re.search(r'"longitude"\s*:\s*([+-]?\d{1,2}\.\d{3,})', raw_html)
        if m_lat and m_lng:
            try:
                la, ln = float(m_lat.group(1)), float(m_lng.group(1))
                if _is_valid_german_coord(la, ln):
                    lat_val, lng_val = round(la, 6), round(ln, 6)
            except ValueError:
                pass

    # 3. Street & Locality on Kleinanzeigen DOM
    st_el = soup.find(id="street-address") or soup.find(attrs={"itemprop": "streetAddress"})
    if st_el:
        st_txt = re.sub(r"\s+", " ", st_el.get_text(" ", strip=True)).strip(" ,")
        placeholder_check = st_txt.lower()
        if st_txt and not any(k in placeholder_check for k in [
            "nicht angegeben", "keine angabe", "auf anfrage", "genaue adresse",
            "straße und hausnummer", "strasse und hausnummer", "ohne angabe"
        ]):
            street_addr = st_txt

    loc_el = soup.find(id="viewad-locality") or soup.find(attrs={"itemprop": "addressLocality"})
    if loc_el:
        loc_txt = re.sub(r"\s+", " ", loc_el.get_text(" ", strip=True))
        m_loc = re.search(r"(\d{5})\s*(?:Bayern|Nordrhein-Westfalen|Baden-Württemberg|Saarland|Hessen|Berlin|Hamburg|Bremen|Sachsen|Sachsen-Anhalt|Thüringen|Brandenburg|Mecklenburg-Vorpommern|Niedersachsen|Rheinland-Pfalz|Schleswig-Holstein)?\s*-?\s*(.+)", loc_txt)
        if m_loc:
            postal_code = m_loc.group(1).strip()
            locality = m_loc.group(2).strip()
        elif loc_txt:
            locality = loc_txt

    return {
        "lat": lat_val,
        "lng": lng_val,
        "street_address": street_addr,
        "postal_code": postal_code,
        "locality": locality,
    }


def check_portal_url_live(url: str, source_portal: str = "", city: str = "") -> Dict[str, Any]:
    """
    Performs real-time network and DOM-scoped verification of a listing URL.
    Does NOT use crude substring matching across full HTML which caused false positives.
    """
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    live_fallback = generate_live_search_url(source_portal, city)

    if not url or not url.startswith("http"):
        return {
            "is_online": False,
            "availability_status": "offline",
            "status_code": 400,
            "status_message": "Keine gültige Portal-URL hinterlegt",
            "last_checked_at": now_iso,
            "live_search_url": live_fallback,
            "description": "",
            "details": {}
        }

    # Detect category / search overview URLs (e.g. /Suche/de/..., /s-wohnung-kaufen/...)
    # A generic search results page cannot verify the online status of a specific individual property!
    is_search_overview = bool(re.search(r"/(?:suche|search|s-immobilien|s-wohnung-kaufen|s-haus-kaufen)\b", url, re.I))
    has_expose_identifier = bool(re.search(r"/(?:expose|s-anzeige)/\w+", url, re.I))
    if is_search_overview and not has_expose_identifier:
        return {
            "is_online": False,
            "availability_status": "unverified_search_url",
            "status_code": 200,
            "status_message": "⚠️ Kein Einzel-Exposé verlinkt (nur Suchergebnis-Übersicht /Suche/...)",
            "last_checked_at": now_iso,
            "live_search_url": url,
            "description": "",
            "details": {}
        }

    # 1. Specialized Live API Verification for ImmoScout24 (Mobile REST API bypasses bot-walls and verifies real status)
    if "immobilienscout24.de" in url.lower() or source_portal.lower() in ["immobilienscout24", "immoscout24", "immoscout"]:
        from app.scrapers.immoscout import extract_immoscout_id, fetch_immoscout_expose
        scout_id = extract_immoscout_id(url)
        if scout_id:
            scout_res = fetch_immoscout_expose(scout_id)
            if scout_res.get("success"):
                return {
                    "is_online": True,
                    "availability_status": "online",
                    "status_code": 200,
                    "status_message": "Live erreichbar & aktiv (ImmoScout24 verifiziert)",
                    "last_checked_at": now_iso,
                    "live_search_url": live_fallback,
                    "title": scout_res.get("title", ""),
                    "price": scout_res.get("price", 0.0),
                    "living_space": scout_res.get("living_space", 0.0),
                    "rooms": scout_res.get("rooms", 0.0),
                    "description": scout_res.get("description", ""),
                    "details": scout_res.get("details", {}),
                    "lat": scout_res.get("lat"),
                    "lng": scout_res.get("lng"),
                    "street_address": scout_res.get("address")
                }
            else:
                return {
                    "is_online": False,
                    "availability_status": "offline",
                    "status_code": 404,
                    "status_message": "Angebot nicht gefunden (bereits gelöscht oder ungültige Scout-ID)",
                    "last_checked_at": now_iso,
                    "live_search_url": live_fallback,
                    "description": "",
                    "details": {}
                }

    # 2. Specialized Live API Verification for Immowelt
    if "immowelt.de" in url.lower() or source_portal.lower() in ["immowelt"]:
        from app.scrapers.immowelt import fetch_immowelt_expose
        if "/expose/" in url.lower():
            iw_res = fetch_immowelt_expose(url)
            if iw_res.get("success"):
                return {
                    "is_online": True,
                    "availability_status": "online",
                    "status_code": 200,
                    "status_message": "Live erreichbar & aktiv (Immowelt verifiziert)",
                    "last_checked_at": now_iso,
                    "live_search_url": live_fallback,
                    "title": iw_res.get("title", ""),
                    "price": iw_res.get("price", 0.0),
                    "living_space": iw_res.get("living_space", 0.0),
                    "rooms": iw_res.get("rooms", 0.0),
                    "description": iw_res.get("description", ""),
                    "details": iw_res.get("details", {}),
                    "lat": iw_res.get("lat"),
                    "lng": iw_res.get("lng"),
                    "street_address": iw_res.get("address")
                }

    try:
        response = requests.get(
            url,
            headers=BROWSER_HEADERS,
            timeout=5.5,
            allow_redirects=True
        )

        status_code = response.status_code

        # HTTP error codes
        if status_code in [404, 410]:
            return {
                "is_online": False,
                "availability_status": "offline",
                "status_code": status_code,
                "status_message": "Inserat vom Portal gelöscht (404 Not Found)",
                "last_checked_at": now_iso,
                "live_search_url": live_fallback,
                "description": "",
                "details": {}
            }

        # Redirect checks
        final_url = response.url.lower()
        original_url = url.lower()

        # Kleinanzeigen redirects deleted ads to category search or search page
        if "/s-anzeige/" in original_url and "/s-anzeige/" not in final_url:
            return {
                "is_online": False,
                "availability_status": "offline",
                "status_code": status_code,
                "status_message": "Umgeleitet auf Kategorie-Übersicht (Anzeige gelöscht)",
                "last_checked_at": now_iso,
                "live_search_url": live_fallback,
                "description": "",
                "details": {}
            }

        # ImmoScout/Immowelt redirects to search
        if "/expose/" in original_url and "/expose/" not in final_url:
            return {
                "is_online": False,
                "availability_status": "offline",
                "status_code": status_code,
                "status_message": "Umgeleitet auf Übersicht (Exposé offline)",
                "last_checked_at": now_iso,
                "live_search_url": live_fallback,
                "description": "",
                "details": {}
            }

        if any(err in final_url for err in ["/error", "/404", "/geloescht", "/inactive"]):
            return {
                "is_online": False,
                "availability_status": "offline",
                "status_code": status_code,
                "status_message": "Umgeleitet auf Fehlerseite (Angebot offline)",
                "last_checked_at": now_iso,
                "live_search_url": live_fallback,
                "description": "",
                "details": {}
            }

        # If bot protection wall encountered (e.g. 403 or 429 Cloudflare/Datadome),
        # bot protection alone does NOT guarantee the expose actually exists!
        if status_code in [401, 403, 429]:
            return {
                "is_online": False,
                "availability_status": "unverified_bot_restricted",
                "status_code": status_code,
                "status_message": "Prüfung durch Bot-Schutz des Portals eingeschränkt (bitte im Browser verifizieren)",
                "last_checked_at": now_iso,
                "live_search_url": live_fallback,
                "description": "",
                "details": {}
            }

        # Parse DOM with lxml
        try:
            soup = BeautifulSoup(response.content, "lxml")
        except Exception:
            soup = BeautifulSoup(response.text, "html.parser")

        extracted_description = ""
        extracted_details = {}
        geo_meta = _extract_portal_location(soup, response.text)

        # 1. Kleinanzeigen specific DOM check
        if "kleinanzeigen" in url.lower() or "kleinanzeigen" in source_portal.lower():
            title_elem = soup.find(id="viewad-title")
            status_box = soup.find(id="viewad-status")
            badge_deactivated = soup.find(class_=re.compile(r"badge-status-deactivated|is-deleted"))
            msgbox = soup.find(class_=re.compile(r"contentbox-msg|messagebox-error"))

            # Check explicit deactivation containers
            if status_box:
                st_text = status_box.get_text().lower()
                if any(k in st_text for k in ["gelöscht", "deaktiviert", "nicht mehr verfügbar", "beendet"]):
                    return {
                        "is_online": False,
                        "availability_status": "offline",
                        "status_code": 200,
                        "status_message": "Anzeige auf Kleinanzeigen deaktiviert",
                        "last_checked_at": now_iso,
                        "live_search_url": live_fallback,
                        "description": "",
                        "details": {}
                    }

            if badge_deactivated:
                return {
                    "is_online": False,
                    "availability_status": "offline",
                    "status_code": 200,
                    "status_message": "Anzeige als deaktiviert markiert",
                    "last_checked_at": now_iso,
                    "live_search_url": live_fallback,
                    "description": "",
                    "details": {}
                }

            # If no title found and error message present
            if not title_elem and msgbox:
                m_text = msgbox.get_text().lower()
                if any(k in m_text for k in ["nicht mehr verfügbar", "gelöscht", "beendet"]):
                    return {
                        "is_online": False,
                        "availability_status": "offline",
                        "status_code": 200,
                        "status_message": "Anzeige nicht mehr verfügbar (gelöscht)",
                        "last_checked_at": now_iso,
                        "live_search_url": live_fallback,
                        "description": "",
                        "details": {}
                    }

            # If title_elem exists, it is ACTIVE!
            if title_elem:
                title_text = title_elem.get_text(strip=True)

                # Extract price
                price_elem = soup.find(id="viewad-price")
                price_text = price_elem.get_text(strip=True) if price_elem else ""
                extracted_price = 0.0
                if price_text:
                    m_pr = re.search(r"([\d\.]+)", price_text)
                    if m_pr:
                        try:
                            extracted_price = float(m_pr.group(1).replace(".", ""))
                        except ValueError:
                            pass

                # Extract description (supports standard div, p itemprop="description", and class containers)
                desc_elem = (
                    soup.find(id="viewad-description-text") or 
                    soup.find("p", attrs={"itemprop": "description"}) or 
                    soup.find(class_=re.compile(r"ad-description|viewad-description"))
                )
                if desc_elem:
                    extracted_description = desc_elem.get_text("\n", strip=True)

                # Extract details
                details_elem = soup.find(id="viewad-details")
                if details_elem:
                    for li in details_elem.find_all("li"):
                        text_val = li.get_text(" ", strip=True)
                        parts = text_val.split(" ", 1)
                        if len(parts) == 2:
                            extracted_details[parts[0]] = parts[1]

                return {
                    "is_online": True,
                    "availability_status": "online",
                    "status_code": 200,
                    "status_message": "Live erreichbar & aktiv (Kleinanzeigen verifiziert)",
                    "last_checked_at": now_iso,
                    "live_search_url": live_fallback,
                    "title": title_text,
                    "price": extracted_price,
                    "description": extracted_description,
                    "details": extracted_details,
                    "lat": geo_meta.get("lat"),
                    "lng": geo_meta.get("lng"),
                    "street_address": geo_meta.get("street_address"),
                    "postal_code": geo_meta.get("postal_code"),
                    "locality": geo_meta.get("locality"),
                }

        # 2. Immowelt & ImmoScout DOM check
        is24_inactive = soup.find(id=re.compile(r"is24-expose-inactive|error-box|offmarket-banner"))
        if is24_inactive:
            return {
                "is_online": False,
                "availability_status": "offline",
                "status_code": 200,
                "status_message": "Inserat wurde offline geschaltet",
                "last_checked_at": now_iso,
                "live_search_url": live_fallback,
                "description": "",
                "details": {}
            }

        # 3. General check: look ONLY inside designated alert/warning message containers
        alert_boxes = soup.find_all(class_=re.compile(r"alert-box|warning-box|status-message|deactivated-notice"))
        for ab in alert_boxes:
            ab_text = ab.get_text().lower()
            for phrase in ["nicht mehr online", "inserat gelöscht", "dieses angebot existiert nicht mehr", "angebot wurde beendet"]:
                if phrase in ab_text:
                    return {
                        "is_online": False,
                        "availability_status": "offline",
                        "status_code": 200,
                        "status_message": f"Deaktivierungshinweis erkannt: {phrase}",
                        "last_checked_at": now_iso,
                        "live_search_url": live_fallback,
                        "description": "",
                        "details": {}
                    }

        # Fallback description extraction for generic portals
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            extracted_description = meta_desc["content"]

        if status_code == 200:
            return {
                "is_online": True,
                "availability_status": "online",
                "status_code": 200,
                "status_message": "Live erreichbar & aktiv",
                "last_checked_at": now_iso,
                "live_search_url": live_fallback,
                "description": extracted_description,
                "details": extracted_details,
                "lat": geo_meta.get("lat"),
                "lng": geo_meta.get("lng"),
                "street_address": geo_meta.get("street_address"),
                "postal_code": geo_meta.get("postal_code"),
                "locality": geo_meta.get("locality"),
            }
        else:
            return {
                "is_online": False,
                "availability_status": "offline",
                "status_code": status_code,
                "status_message": f"Portal meldet HTTP Status {status_code}",
                "last_checked_at": now_iso,
                "live_search_url": live_fallback,
                "description": "",
                "details": {}
            }

    except requests.exceptions.Timeout:
        return {
            "is_online": False,
            "availability_status": "timeout",
            "status_code": 504,
            "status_message": "Portal antwortet nicht (Timeout)",
            "last_checked_at": now_iso,
            "live_search_url": live_fallback,
            "description": "",
            "details": {}
        }
    except requests.exceptions.RequestException as e:
        return {
            "is_online": False,
            "availability_status": "error",
            "status_code": 500,
            "status_message": f"Verbindungsfehler: {str(e)[:60]}",
            "last_checked_at": now_iso,
            "live_search_url": live_fallback,
            "description": "",
            "details": {}
        }

def fetch_portal_listing(url: str, source_portal: str = "", city: str = "") -> Dict[str, Any]:
    """
    Convenience wrapper that runs check_portal_url_live and returns the full listing payload
    including the extracted description and details.
    """
    return check_portal_url_live(url=url, source_portal=source_portal, city=city)
