"""
Immowelt Scraper Engine for ImmoMetrica Clone.
Crawls live properties from Immowelt search pages,
extracting real offer links, prices, living space, rooms, high-resolution photos, and descriptions.
"""

import re
import os
import json
import copy
import time
import threading
import urllib.parse
import requests
import logging
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger("immometrica.scraper.immowelt")

_GATEWAY_LOCK = threading.Lock()
_DIRECT_BLOCKED_UNTIL: float = 0.0
_STANDARD_BLOCKED_UNTIL: float = 0.0
_EXPOSE_CACHE: Dict[str, tuple] = {}
_SEARCH_HTML_CACHE: Dict[str, tuple] = {}
_EXPOSE_CACHE_TTL_SEC = 600.0
_SEARCH_HTML_CACHE_TTL_SEC = 180.0
_BLOCK_COOLDOWN_SEC = 900.0
_EXPOSE_CACHE_MAX = 200
_SEARCH_CACHE_MAX = 60


def reset_immowelt_gateway_state() -> None:
    """Clears in-memory Immowelt cooldowns and TTL caches (useful for tests/resets)."""
    global _DIRECT_BLOCKED_UNTIL, _STANDARD_BLOCKED_UNTIL
    with _GATEWAY_LOCK:
        _DIRECT_BLOCKED_UNTIL = 0.0
        _STANDARD_BLOCKED_UNTIL = 0.0
        _EXPOSE_CACHE.clear()
        _SEARCH_HTML_CACHE.clear()


def _is_unmocked_fetch() -> bool:
    """Returns True only when fetch_immowelt_html is the real module function (never bypass mocks in tests)."""
    fn = globals().get("fetch_immowelt_html")
    return bool(
        callable(fn)
        and getattr(fn, "__module__", None) == __name__
        and not hasattr(fn, "assert_called")
    )


def _is_blocked_immowelt_response(resp: Optional[requests.Response]) -> bool:
    """Detects DataDome/anti-bot challenges or transient blocks while letting genuine 200/404 responses pass."""
    if resp is None:
        return True
    st = int(getattr(resp, "status_code", 0) or 0)
    text = str(getattr(resp, "text", "") or "")
    headers_raw = getattr(resp, "headers", None) or {}
    headers_low = {str(k).lower(): str(v).lower() for k, v in headers_raw.items()} if hasattr(headers_raw, "items") else {}

    if st in (404, 410):
        # Treat genuine 404/410 as final unless an explicit DataDome block header/body is present
        if "x-datadome" in headers_low and "datadome" in text[:4000].lower():
            return True
        return False

    if st != 200:
        return True

    has_real_markers = (
        "__UFRN_LIFECYCLE_SERVERREQUEST__" in text
        or "__UFRN_STORE__" in text
        or "/expose/" in text
    )
    if has_real_markers:
        return False

    head_low = text[:16000].lower()
    challenge_sigs = (
        "captcha-delivery.com",
        "datadome",
        "cf-browser-verification",
        "access denied",
        "enable javascript",
    )
    if any(sig in head_low for sig in challenge_sigs):
        return True
    if len(text.strip()) < 2500:
        return True
    return False


BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    # Omit "br" because optional brotli decoder is not guaranteed in environment; requests handles gzip/deflate natively.
    "Accept-Encoding": "gzip, deflate",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24", "Google Chrome";v="131"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Cache-Control": "no-cache"
}


def fetch_immowelt_html(url: str, timeout: float = 12.0, allow_gateway: bool = True) -> requests.Response:
    """
    Fetches an Immowelt URL with tiered cost protection:
    1. Custom HTTP/HTTPS proxy if SCRAPER_PROXY_URL is configured.
    2. Direct browser-header HTTP request first (0 ScraperAPI credits) when enabled,
       with a 15-minute cooldown if the current IP is DataDome-blocked.
    3. Managed Scraping Gateway fallback (ScraperAPI standard 10cr -> premium 20cr only on block).
    """
    global _DIRECT_BLOCKED_UNTIL, _STANDARD_BLOCKED_UNTIL

    scraper_key = (os.getenv("SCRAPER_API_KEY") or os.getenv("SCRAPING_API_KEY") or "").strip()
    service = os.getenv("SCRAPING_SERVICE", "scraperapi").strip().lower()
    proxy_url = os.getenv("SCRAPER_PROXY_URL", "").strip()
    gateway_enabled = os.getenv("SCRAPER_API_ENABLED", "1").strip().lower() not in ("0", "false", "no")
    can_use_gateway = bool(allow_gateway and scraper_key and gateway_enabled)
    direct_first = os.getenv("SCRAPER_DIRECT_FIRST", "1").strip().lower() not in ("0", "false", "no")
    premium_mode = os.getenv("SCRAPER_PREMIUM_MODE", "fallback").strip().lower()

    # 1. Custom HTTP/HTTPS Proxy (e.g. Bright Data, Smartproxy, Webshare)
    if proxy_url:
        proxies = {"http": proxy_url, "https": proxy_url}
        return requests.get(url, headers=BROWSER_HEADERS, proxies=proxies, timeout=timeout)

    # 2. Tier 1 (0 Credits): Try Direct Request First when enabled or when no gateway is available
    last_direct_resp: Optional[requests.Response] = None
    last_direct_err: Optional[Exception] = None
    if direct_first or not can_use_gateway:
        now_m = time.monotonic()
        with _GATEWAY_LOCK:
            in_direct_cooldown = can_use_gateway and (now_m < _DIRECT_BLOCKED_UNTIL)

        if not in_direct_cooldown:
            try:
                direct_timeout = min(float(timeout or 12.0), 8.0) if can_use_gateway else float(timeout or 12.0)
                direct_resp = requests.get(url, headers=BROWSER_HEADERS, timeout=direct_timeout)
                if not _is_blocked_immowelt_response(direct_resp) or not can_use_gateway:
                    with _GATEWAY_LOCK:
                        _DIRECT_BLOCKED_UNTIL = 0.0
                    return direct_resp
                last_direct_resp = direct_resp
            except requests.RequestException as req_err:
                last_direct_err = req_err
                if not can_use_gateway:
                    raise

            if can_use_gateway:
                with _GATEWAY_LOCK:
                    _DIRECT_BLOCKED_UNTIL = time.monotonic() + _BLOCK_COOLDOWN_SEC
                logger.info("Direct Immowelt request blocked/restricted; falling back to scraping gateway...")

    # 3. Managed Scraping Gateway API (ScraperAPI / ZenRows / ScrapingBee)
    if can_use_gateway:
        encoded_url = urllib.parse.quote(url, safe="")
        gateway_timeout = max(float(timeout or 12.0), 35.0)

        if service == "zenrows":
            gateway_url = f"https://api.zenrows.com/v1/?apikey={scraper_key}&url={encoded_url}&premium_proxy=true&proxy_country=de"
            logger.info("Routing Immowelt request through zenrows gateway...")
            return requests.get(gateway_url, timeout=gateway_timeout)
        if service == "scrapingbee":
            gateway_url = f"https://app.scrapingbee.com/api/v1/?api_key={scraper_key}&url={encoded_url}&country_code=de"
            logger.info("Routing Immowelt request through scrapingbee gateway...")
            return requests.get(gateway_url, timeout=gateway_timeout)

        # ScraperAPI Tier 2 (standard: 10 credits) -> Tier 3 (premium: 20 credits only as fallback)
        render_param = "&render=true" if os.getenv("SCRAPER_RENDER", "").lower() == "true" else ""
        std_resp: Optional[requests.Response] = None

        if premium_mode != "always":
            now_m = time.monotonic()
            with _GATEWAY_LOCK:
                in_std_cooldown = now_m < _STANDARD_BLOCKED_UNTIL

            if not in_std_cooldown:
                std_url = f"https://api.scraperapi.com?api_key={scraper_key}&url={encoded_url}&country_code=de{render_param}"
                logger.info("Routing Immowelt request through scraperapi standard gateway (no premium)...")
                try:
                    std_resp = requests.get(std_url, timeout=gateway_timeout)
                except requests.RequestException:
                    std_resp = None

                if std_resp is not None and (not _is_blocked_immowelt_response(std_resp) or premium_mode == "never"):
                    if not _is_blocked_immowelt_response(std_resp):
                        with _GATEWAY_LOCK:
                            _STANDARD_BLOCKED_UNTIL = 0.0
                    return std_resp

                with _GATEWAY_LOCK:
                    _STANDARD_BLOCKED_UNTIL = time.monotonic() + _BLOCK_COOLDOWN_SEC
                logger.info("ScraperAPI standard gateway blocked; escalating to premium fallback...")

        if premium_mode != "never":
            prem_url = f"https://api.scraperapi.com?api_key={scraper_key}&url={encoded_url}&country_code=de&premium=true{render_param}"
            logger.info("Routing Immowelt request through scraperapi premium gateway...")
            return requests.get(prem_url, timeout=gateway_timeout)

        if std_resp is not None:
            return std_resp

    if last_direct_resp is not None:
        return last_direct_resp
    if last_direct_err is not None:
        raise last_direct_err

    # Fallback direct request if gateway is disabled and direct_first was explicitly turned off
    return requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)


CITY_SLUG_ALIASES = {
    "frankfurt": "frankfurt-am-main",
    "halle": "halle-saale",
    "halle (saale)": "halle-saale",
    "bad homburg vor der hoehe": "bad-homburg-vor-der-hoehe",
    "bad homburg vor der höhe": "bad-homburg-vor-der-hoehe",
    "muelheim an der ruhr": "muelheim-a-d-ruhr",
    "mülheim an der ruhr": "muelheim-a-d-ruhr",
}


def clean_city_slug(city: str) -> str:
    c = city.lower().strip()
    if c in CITY_SLUG_ALIASES:
        return CITY_SLUG_ALIASES[c]
    c = c.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    c = re.sub(r"[^a-z0-9\-]", "-", c)
    c = re.sub(r"-{2,}", "-", c).strip("-")
    return CITY_SLUG_ALIASES.get(c, c)


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


def _build_immowelt_search_paths(property_type: str = "all") -> List[tuple]:
    """Maps configured property_type to (url_path_segment, default_prop_type, default_bldg_type)."""
    pt = str(property_type or "all").lower().strip()
    if any(k in pt for k in ("grundst", "plot", "land")):
        return [("grundstuecke/kaufen", "Grundstück", "Grundstück")]
    if any(k in pt for k in ("mehrfamilien", "mfh", "zinshaus", "einfamilien", "efh", "haus", "house")):
        return [("haeuser/kaufen", "Mehrfamilienhaus" if ("mehrfamilien" in pt or "mfh" in pt or "zinshaus" in pt) else "Einfamilienhaus", "Haus")]
    if any(k in pt for k in ("wohnung", "etw", "apartment", "maisonette", "penthouse")):
        return [("wohnungen/kaufen", "Eigentumswohnung", "Etagenwohnung")]
    return [
        ("wohnungen/kaufen", "Eigentumswohnung", "Etagenwohnung"),
        ("haeuser/kaufen", "Einfamilienhaus", "Haus"),
    ]


def search_immowelt_deals(
    city: str,
    query: str = "",
    max_results: int = 15,
    exclude_urls: Optional[Any] = None,
    postal_code: Optional[str] = None,
    radius_km: int = 0,
    property_type: str = "all",
    max_pages: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Scrapes live listings from Immowelt for a given German city or postal code.
    Supports property types (Wohnung, Haus/MFH, Grundstück) and search radius.
    Extracts real prices, living spaces, rooms, high-resolution property photos,
    and investor metrics (yield, cashflow, benchmark comparisons).
    """
    from app.calculator import calculate_cashflow
    from app.valuation import evaluate_property_valuation
    from app.geo_resolver import resolve_location_and_tax, resolve_city_coordinates
    from app.scrapers.deal_fusion import normalize_portal_url

    plz_clean = re.sub(r"\D", "", str(postal_code or ""))[:5]
    loc_input = (city or plz_clean or "").strip()
    if not loc_input:
        return []
    city_slug = clean_city_slug(loc_input)
    exclude_set = {normalize_portal_url(u) for u in exclude_urls} if exclude_urls else set()
    search_targets = _build_immowelt_search_paths(property_type)
    radius_param = f"&r={int(radius_km)}" if int(radius_km or 0) > 0 else ""

    if max_pages is not None:
        effective_max_pages = max(1, min(3, int(max_pages)))
    else:
        try:
            effective_max_pages = max(1, min(3, int(os.getenv("IMMOWELT_MAX_PAGES", "3"))))
        except (ValueError, TypeError):
            effective_max_pages = 3

    results: List[Dict[str, Any]] = []
    seen_urls = set()

    for path_seg, default_p_type, default_b_type in search_targets:
        if len(results) >= max_results:
            break
        prev_page_urls = set()
        for page in range(1, effective_max_pages + 1):
            if len(results) >= max_results:
                break

            url = f"https://www.immowelt.de/suche/{city_slug}/{path_seg}?sp={page}&sort=createdate_desc{radius_param}"

            html_text: Optional[str] = None
            can_cache_search = _is_unmocked_fetch()
            if can_cache_search:
                now_m = time.monotonic()
                with _GATEWAY_LOCK:
                    cached = _SEARCH_HTML_CACHE.get(url)
                    if cached and now_m < cached[0]:
                        html_text = cached[1]

            if html_text is None:
                try:
                    resp = fetch_immowelt_html(url, timeout=10.0)
                    if resp.status_code != 200:
                        logger.warning(f"Immowelt search returned HTTP {resp.status_code} ({path_seg}, page {page})")
                        break
                    html_text = resp.text
                    if can_cache_search and html_text and not _is_blocked_immowelt_response(resp):
                        with _GATEWAY_LOCK:
                            if len(_SEARCH_HTML_CACHE) >= _SEARCH_CACHE_MAX:
                                oldest_k = next(iter(_SEARCH_HTML_CACHE))
                                _SEARCH_HTML_CACHE.pop(oldest_k, None)
                            _SEARCH_HTML_CACHE[url] = (time.monotonic() + _SEARCH_HTML_CACHE_TTL_SEC, html_text)
                except Exception as e:
                    logger.warning(f"Immowelt request error for {loc_input} ({path_seg}, page {page}): {e}")
                    break

            soup = BeautifulSoup(html_text, "html.parser")
            expose_links = soup.find_all("a", href=re.compile(r"/expose/[a-f0-9\-]+", re.IGNORECASE))
            if not expose_links:
                break
            cur_page_urls = {
                normalize_portal_url(("https://www.immowelt.de" + lk["href"] if not lk["href"].startswith("http") else lk["href"]).split("?")[0])
                for lk in expose_links
            }
            if page > 1 and cur_page_urls and cur_page_urls == prev_page_urls:
                break
            prev_page_urls = cur_page_urls

            unseen_on_page = sum(
                1 for u in cur_page_urls
                if u not in seen_urls and (not exclude_set or u not in exclude_set)
            )
            # Results are sorted newest-first: if a full page has zero unseen URLs, deeper pages are also already seen.
            stop_after_this_page = bool(exclude_set and len(cur_page_urls) >= 3 and unseen_on_page == 0)

            for link in expose_links:
                raw_href = link["href"]
                if not raw_href.startswith("http"):
                    full_url = "https://www.immowelt.de" + raw_href
                else:
                    full_url = raw_href

                clean_url = normalize_portal_url(full_url.split("?")[0])
                if clean_url in seen_urls:
                    continue
                if exclude_set and clean_url in exclude_set:
                    continue
                seen_urls.add(clean_url)

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
                # Extract Living space & Rooms (exclude plot/land m² when residential)
                sqm = 0.0
                for s in stripped_strings:
                    if default_p_type != "Grundstück" and re.search(r"grundst|bauland|fläche des grund", s, re.IGNORECASE):
                        continue
                    sqm_match = re.search(r"([\d,\.]+)\s*m²", s)
                    if sqm_match:
                        raw_sq = sqm_match.group(1)
                        try:
                            if "," in raw_sq:
                                norm_sqm = raw_sq.replace(".", "").replace(",", ".")
                            elif re.match(r"^\d{1,3}(?:\.\d{3})+$", raw_sq):
                                norm_sqm = raw_sq.replace(".", "")
                            else:
                                norm_sqm = raw_sq
                            cand = float(norm_sqm)
                            if 8.0 <= cand <= 50000.0:
                                sqm = cand
                                break
                        except ValueError:
                            pass

                rooms = 2.0
                room_match = re.search(r"([\d,\.]+)\s*Zi", card_text)
                if room_match:
                    try:
                        rooms = float(room_match.group(1).replace(",", "."))
                    except ValueError:
                        pass

                # Extract postal code & location (supports both "District, City (44287)" and "44287 City")
                extracted_plz = plz_clean
                extracted_city = ""
                district = ""
                city_paren_match = re.search(
                    r"(?:^|\|\s*)(?:([^|,]{2,35}?),\s*)?([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-\.\s]{1,35}?)\s*\((\d{5})\)",
                    card_text,
                )
                if city_paren_match:
                    district = (city_paren_match.group(1) or "").strip()
                    extracted_city = city_paren_match.group(2).strip()
                    extracted_plz = city_paren_match.group(3).strip()
                else:
                    plz_city_match = re.search(r"\b(\d{5})\s+([A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß\-\s]{1,35}?)(?:\s*\(|,|\||$)", card_text)
                    if plz_city_match:
                        extracted_plz = plz_city_match.group(1)
                        extracted_city = plz_city_match.group(2).strip()
                    else:
                        plz_match = re.search(r"\b(\d{5})\b", card_text)
                        if plz_match:
                            extracted_plz = plz_match.group(1)

                if not district and city:
                    dist_match = re.search(r"([A-Za-zÄÖÜäöüß\s\-]+),\s*" + re.escape(city), card_text, re.IGNORECASE)
                    if dist_match:
                        district = dist_match.group(1).strip()

                teasers = [
                    s for s in stripped_strings
                    if len(s) > 35
                    and not any(k in s for k in ["€/m²", "provisionsfrei", "Herr ", "Frau ", "GmbH", "Immobilien"])
                ]
                desc_teaser = teasers[-1] if teasers else ""
                non_teaser_header = " | ".join(
                    s for s in stripped_strings
                    if s != desc_teaser and len(s) <= 80
                ).lower()
                type_source = non_teaser_header or card_text.lower()

                # Detect property type primarily from structured card badges/title, avoiding prose teaser contamination
                ct_lower = card_text.lower()
                p_type = default_p_type
                b_type = default_b_type
                if default_p_type == "Grundstück" or (
                    any(k in type_source for k in ["grundstück zum kauf", "baugrundstück", "bauplatz"])
                    and not any(k in type_source for k in ["wohnung", "maisonette", "penthouse", "einfamilienhaus", "doppelhaushälfte", "reihenhaus", "mehrfamilienhaus"])
                ):
                    p_type = "Grundstück"
                    b_type = "Grundstück"
                elif any(k in type_source for k in ["mehrfamilienhaus", "zinshaus", "renditehaus", "mfh"]):
                    p_type = "Mehrfamilienhaus"
                    b_type = "Mehrfamilienhaus"
                elif any(k in type_source for k in ["einfamilienhaus", "doppelhaushälfte", "reihenhaus", "bungalow", "stadthaus", "zweifamilienhaus", "haus zum kauf"]):
                    p_type = "Einfamilienhaus"
                    b_type = "Haus"
                elif "maisonette" in type_source:
                    p_type = "Maisonette"
                    b_type = "Maisonette"
                elif "penthouse" in type_source:
                    p_type = "Penthouse"
                    b_type = "Penthouse"
                elif "dachgeschoss" in type_source:
                    b_type = "Dachgeschosswohnung"
                elif "erdgeschoss" in type_source or " eg " in type_source:
                    b_type = "Erdgeschosswohnung"

                energy_class = ""
                energy_match = re.search(r"\b([A-H]\+?)\b", card_text)
                if energy_match and any(k in card_text for k in ["kWh", "Energie", "frei ab", " · "]):
                    energy_class = energy_match.group(1)

                title_h = card.find(["h2", "h3", "h1"])
                badge_titles = [
                    s for s in stripped_strings
                    if 8 <= len(s) <= 110 and any(k in s.lower() for k in ["zum kauf", "wohnung", "haus", "maisonette", "penthouse", "grundstück"]) and s != desc_teaser
                ]
                raw_title = (
                    title_h.text.strip()
                    if (title_h and title_h.text.strip())
                    else (desc_teaser if len(desc_teaser) >= 20 else (badge_titles[0] if badge_titles else ""))
                )
                resolved_city = (extracted_city or city or extracted_plz or "Deutschland").strip()
                if city:
                    from app.geo_resolver import normalize_district_city, is_within_search_radius
                    norm_city, norm_dist = normalize_district_city(city, resolved_city, extracted_plz)
                    if norm_dist and not district:
                        district = norm_dist
                    resolved_city = norm_city
                    in_radius, _ = is_within_search_radius(
                        target_city=city,
                        target_postal_code=plz_clean or None,
                        radius_km=radius_km,
                        deal_city=resolved_city,
                        deal_postal_code=extracted_plz,
                        allow_geocoder=False,
                    )
                    if not in_radius:
                        continue
                elif resolved_city and resolved_city.islower():
                    resolved_city = resolved_city.capitalize()
                title = clean_immowelt_title(raw_title, rooms, sqm if sqm > 0 else 50.0, district, resolved_city, p_type)

                if query and query.lower() not in title.lower() and query.lower() not in ct_lower:
                    continue

                geo_info = resolve_location_and_tax(postal_code=extracted_plz, city=resolved_city)
                resolved_state = geo_info["state"]

                import zlib
                url_seed = zlib.crc32(clean_url.encode("utf-8"))
                prop_lat, prop_lng = resolve_city_coordinates(
                    city=resolved_city,
                    postal_code=extracted_plz,
                    index=(len(results) + (url_seed % 17))
                )

                card_imgs = card.find_all("img")
                gallery_images: List[str] = []
                main_img = None
                for im in card_imgs:
                    src = im.get("src") or im.get("data-src")
                    if not src or "immowelt" not in src or src.startswith("data:"):
                        continue
                    # Skip small broker portraits / logos (e.g. ?h=50 or avatar/logo URLs)
                    if re.search(r"[?&](?:h|height)=(?:[1-9]\d?|1[01]\d)\b", src, re.IGNORECASE):
                        continue
                    if any(skip_kw in src.lower() for skip_kw in ["logo", "avatar", "portrait", "makler", "agent"]):
                        continue
                    high_res = re.sub(r"\?.*$", "?w=626&h=470", src)
                    if high_res not in gallery_images:
                        gallery_images.append(high_res)
                    if not main_img:
                        main_img = high_res

                if not main_img:
                    main_img = "https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?w=800"

                eff_sqm = sqm if sqm > 0 else 50.0
                benchmark = evaluate_property_valuation(resolved_city, eff_sqm, price, property_type=p_type, postal_code=extracted_plz)
                expected_rent_per_sqm = benchmark.get("expected_rent_per_sqm", 11.50)
                expected_rent = round(eff_sqm * expected_rent_per_sqm, 2) if (p_type != "Grundstück" and sqm > 0) else 0.0
                is_rented = 1 if ("vermietet" in ct_lower and "unvermietet" not in ct_lower and "nicht vermietet" not in ct_lower) else 0
                current_rent = 0.0
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

                is_private = 1 if ("provisionsfrei" in ct_lower or "privat" in ct_lower) else 0
                comm_pct = 0.0 if is_private else 3.57

                tags = ["Live Scraped", "Immowelt", resolved_state]
                if gross_yield >= 5.5:
                    tags.append("Top-Rendite")
                if monthly_cashflow > 0:
                    tags.append("Positiver Cashflow")
                if is_private:
                    tags.append("Provisionsfrei")

                from app.datetime_utils import get_berlin_now_str, get_berlin_today_str
                now_str = get_berlin_now_str()

                loc_display = f"{extracted_plz} {resolved_city}" + (f"-{district}" if district else "")
                desc_body = desc_teaser if desc_teaser else f"Attraktive {p_type} in begehrter Lage von {loc_display}."
                full_desc = (
                    f"{title}\n\n"
                    f"{desc_body}\n\n"
                    f"Eckdaten des Objekts:\n"
                    f"- Zimmer: {rooms:.1f}\n"
                    f"- Wohnfläche: {sqm:.1f} m²\n"
                    f"- Lage: {loc_display}\n"
                    f"- Kaufpreis: {price:,.0f} € (ca. {round(price/eff_sqm):,.0f} €/m²)\n"
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
                    "status_message": "Live erreichbar & aktiv (Immowelt verifiziert)",
                    "last_checked_at": now_str,
                    "live_search_url": url
                })

                if len(results) >= max_results:
                    break

            if stop_after_this_page:
                break

    return results




def fetch_immowelt_expose(url: str, use_cache: bool = True, allow_gateway: bool = True) -> Dict[str, Any]:
    """
    Fetches comprehensive expose details for an Immowelt URL (with 10-minute in-memory TTL cache).
    Parses embedded JSON state (__UFRN_LIFECYCLE_SERVERREQUEST__ / __UFRN_STORE__)
    to extract:
    - Full text description (main text + Lage + Weitere Infos / Garagen)
    - All 15+ high-resolution gallery images
    - Energy certificate (efficiency class, consumption in kWh, carrier, validity, type)
    - Hardfacts: purchase price, living space, rooms, year built, heating system.
    """
    clean_url = url.split("?")[0].rstrip("/")
    cache_key = clean_url.lower()
    can_cache = bool(use_cache and _is_unmocked_fetch())

    if can_cache:
        now_m = time.monotonic()
        with _GATEWAY_LOCK:
            cached = _EXPOSE_CACHE.get(cache_key)
            if cached and now_m < cached[0]:
                return copy.deepcopy(cached[1])

    result = _fetch_immowelt_expose_uncached(clean_url, allow_gateway=allow_gateway)
    if can_cache and isinstance(result, dict) and result.get("success"):
        with _GATEWAY_LOCK:
            if len(_EXPOSE_CACHE) >= _EXPOSE_CACHE_MAX:
                oldest_k = next(iter(_EXPOSE_CACHE))
                _EXPOSE_CACHE.pop(oldest_k, None)
            _EXPOSE_CACHE[cache_key] = (time.monotonic() + _EXPOSE_CACHE_TTL_SEC, copy.deepcopy(result))
    return result


def _fetch_immowelt_expose_uncached(clean_url: str, allow_gateway: bool = True) -> Dict[str, Any]:
    expose_id = clean_url.split("/")[-1]

    try:
        try:
            resp = fetch_immowelt_html(clean_url, timeout=12.0, allow_gateway=allow_gateway)
        except TypeError:
            # Backward compatibility for 1- or 2-arg test mocks of fetch_immowelt_html
            resp = fetch_immowelt_html(clean_url, timeout=12.0)
        if resp.status_code != 200:
            return {
                "success": False,
                "status_code": resp.status_code,
                "error": f"Immowelt blockiert den Zugriff oder Inserat nicht erreichbar (HTTP {resp.status_code} Bot-Schutz).",
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
                    import codecs
                    unescaped = codecs.decode(m.group(1), "unicode_escape")
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
            raw_d = classified.get("rawData") or {}

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

            # B. Title (prefer concise hardFacts heading over multi-paragraph description classified.title)
            hardfacts = sections.get("hardFacts") or sections.get("hardfacts") or {}
            hf_title = str(hardfacts.get("title") or "").strip() if isinstance(hardfacts, dict) else ""
            raw_title_cand = str(classified.get("title") or "").strip()
            if hf_title and (not raw_title_cand or len(raw_title_cand) > 120):
                raw_title = hf_title
            else:
                raw_title = raw_title_cand or hf_title
            clean_t = re.sub(r'<[^>]+>', ' ', raw_title).strip()
            if len(clean_t) > 110:
                first_s = re.split(r'[\.\!\?]\s+', clean_t)[0]
                title = first_s if len(first_s) >= 20 else clean_t[:100].rsplit(' ', 1)[0] + '...'
            else:
                title = clean_t or f"Immowelt Exposé #{expose_id[:8]}"

            # C. Gallery Images (skip small broker portraits/logos)
            images_raw = classified.get("domains", {}).get("medias", {}).get("images", [])
            gallery_images = []
            for im in images_raw:
                u = im.get("url")
                if not u or "immowelt" not in u:
                    continue
                if re.search(r"[?&](?:h|height)=(?:[1-9]\d?|1[01]\d)\b", u, re.IGNORECASE):
                    continue
                if any(skip_kw in u.lower() for skip_kw in ["logo", "avatar", "portrait", "makler", "agent"]):
                    continue
                clean_u = re.sub(r'\?.*$', '', u)
                if clean_u not in gallery_images:
                    gallery_images.append(clean_u)
            main_image = gallery_images[0] if gallery_images else ""

            # D. Hardfacts (Price, Sqm, Rooms) - Supports both camelCase 'hardFacts' and lowercase 'hardfacts'
            facts = hardfacts.get("facts") or []
            price_val = 0.0
            sqm_val = 0.0
            land_area_val = 0.0
            rooms_val = 0.0
            year_built_val = None

            # D1. Purchase Price from rawData, sections.price, or hardfacts
            if raw_d.get("Preis"):
                try:
                    price_val = float(raw_d["Preis"])
                except Exception:
                    pass

            if price_val <= 0:
                price_obj = sections.get("price") or hardfacts.get("price") or {}
                if isinstance(price_obj, dict):
                    if price_obj.get("value"):
                        try:
                            price_val = float(price_obj["value"])
                        except Exception:
                            pass
                    if price_val <= 0:
                        base_node = price_obj.get("base") if isinstance(price_obj, dict) else None
                        main1 = base_node.get("main") if isinstance(base_node, dict) else None
                        val1 = main1.get("value") if isinstance(main1, dict) else None
                        main2 = val1.get("main") if isinstance(val1, dict) else None
                        price_str = str(main2.get("value", "")) if isinstance(main2, dict) else ""
                        m_pr = re.search(r'([\d\.]+)', price_str)
                        if m_pr:
                            try:
                                price_val = float(m_pr.group(1).replace(".", ""))
                            except Exception:
                                pass

            # D2. Living Space vs Plot Space (never let plotSpace overwrite livingSpace!)
            for f in facts:
                f_type = str(f.get("type", "")).lower()
                f_label = str(f.get("label", "")).lower()
                f_val = str(f.get("value", ""))
                f_split = str(f.get("splitValue", ""))
                cand_text = f_split if f_split else f_val
                cand_low = f"{f_type} {f_label} {f_val} {f_split}".lower()

                is_plot = any(k in cand_low for k in ("plot", "grundst", "land", "bauland"))
                is_aux = any(k in cand_low for k in ("nutzfl", "garten", "balkon", "terrasse", "stellplatz", "keller"))
                if is_plot:
                    m_land = re.search(r'([\d,\.]+)', cand_text)
                    if m_land and land_area_val <= 0:
                        try:
                            land_area_val = float(m_land.group(1).replace(".", "").replace(",", "."))
                        except Exception:
                            pass
                    continue
                if is_aux:
                    continue

                if sqm_val <= 0 and ("livingspace" in f_type or "living" in f_type or "wohnfl" in cand_low or ("m²" in f_val and "nutz" not in cand_low)):
                    m_sqm = re.search(r'([\d,\.]+)', cand_text)
                    if m_sqm:
                        try:
                            sqm_val = float(m_sqm.group(1).replace(".", "").replace(",", "."))
                        except Exception:
                            pass

            if sqm_val <= 0:
                sqm_range = raw_d.get("Wohnflaeche_Range", "")
                if sqm_range:
                    m_s = re.search(r'(\d+)', sqm_range)
                    if m_s:
                        try:
                            sqm_val = float(m_s.group(1))
                        except Exception:
                            pass

            # D3. Rooms
            if raw_d.get("Raeume"):
                try:
                    rooms_val = float(raw_d["Raeume"])
                except Exception:
                    pass

            if rooms_val <= 0:
                for f in facts:
                    f_type = str(f.get("type", "")).lower()
                    f_val = str(f.get("value", ""))
                    f_split = str(f.get("splitValue", ""))
                    if "room" in f_type or "zimmer" in str(f.get("label", "")).lower():
                        cand_r = f_split if f_split else f_val
                        m_r = re.search(r'([\d,\.]+)', cand_r)
                        if m_r:
                            try:
                                rooms_val = float(m_r.group(1).replace(",", "."))
                            except Exception:
                                pass

            if rooms_val <= 0:
                rooms_val = 1.0

            # E. Location
            loc_sec = sections.get("location") or {}
            addr_obj = loc_sec.get("address") or {}
            city = addr_obj.get("city") or raw_d.get("city") or ""
            plz = addr_obj.get("zipCode") or raw_d.get("PLZ") or ""
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

            street_str = str(addr_obj.get("street") or "").strip()
            addr_display = ", ".join(p for p in [street_str, f"{plz} {city}".strip(), district] if p)
            details_dict = {
                "Kaufpreis": f"{price_val:,.0f} €".replace(",", ".") if price_val else "",
                "Wohnfläche": f"{sqm_val:.1f} m²" if sqm_val else "",
                "Grundstücksfläche": f"{land_area_val:.1f} m²" if land_area_val else "",
                "Zimmer": str(rooms_val) if rooms_val else "",
                "Baujahr": str(year_built_val) if year_built_val else "",
                "Heizungsart": heating_type or "",
                "energy_certificate": energy_data,
            }
            details_dict = {k: v for k, v in details_dict.items() if v}

            return {
                "success": True,
                "expose_id": expose_id,
                "title": title,
                "description": full_desc,
                "price": price_val or 0.0,
                "living_space": sqm_val or 0.0,
                "land_area": land_area_val or 0.0,
                "rooms": rooms_val or 1.0,
                "city": city,
                "postal_code": plz,
                "district": district,
                "address": addr_display,
                "year_built": year_built_val,
                "heating_type": heating_type,
                "images": gallery_images,
                "image_url": main_image,
                "energy_certificate": energy_data,
                "details": details_dict,
                "portal": "Immowelt"
            }

        # Fallback to HTML BeautifulSoup & Regex extraction if JSON state was not present (e.g. hydrated client DOM)
        soup = BeautifulSoup(html, "html.parser")
        html_lower = html.lower()
        if any(sig in html_lower for sig in ["datadome", "captcha", "access denied", "enable javascript", "cf-browser-verification"]):
            return {
                "success": False,
                "status_code": 403,
                "error": "Immowelt Bot-Schutz / JS-Challenge erkannt.",
                "portal": "Immowelt"
            }

        h1 = soup.find("h1")
        title_tag = soup.find("title")
        raw_title_text = title_tag.text.strip() if title_tag else (h1.text.strip() if h1 else f"Immowelt Exposé #{expose_id[:8]}")
        desc_elem = soup.find(class_=re.compile(r"description|section-content|text-content"))
        desc = desc_elem.get_text("\n", strip=True) if desc_elem else ""

        # Extract Price from Title, Meta tags, and Text
        fb_price = 0.0
        meta_desc = soup.find("meta", attrs={"name": "description"})
        meta_desc_str = meta_desc["content"] if (meta_desc and "content" in meta_desc.attrs) else ""
        text_corpus = f"{raw_title_text} {meta_desc_str} {soup.get_text(' ', strip=True)[:5000]}"

        m_pr = re.search(r'(?:kaufpreis|preis|kauf)?[:\s]*([\d\.]+)\s*€', text_corpus, re.IGNORECASE)
        if m_pr:
            try:
                p_cand = float(m_pr.group(1).replace(".", ""))
                if p_cand > 10000:
                    fb_price = p_cand
            except Exception:
                pass
        
        if fb_price <= 0:
            m_pr2 = re.search(r'(\d{4,9})\s*€', raw_title_text)
            if m_pr2:
                try:
                    fb_price = float(m_pr2.group(1))
                except Exception:
                    pass

        # Extract Living Space
        fb_sqm = 0.0
        m_sq = re.search(r'([\d,\.]+)\s*m²', text_corpus, re.IGNORECASE)
        if m_sq:
            try:
                s_cand = float(m_sq.group(1).replace(".", "").replace(",", "."))
                if 10 <= s_cand <= 2000:
                    fb_sqm = s_cand
            except Exception:
                pass

        # Extract Rooms
        fb_rooms = 1.0
        m_rm = re.search(r'([\d,\.]+)\s*(?:zimmer|zi\b)', text_corpus, re.IGNORECASE)
        if m_rm:
            try:
                r_cand = float(m_rm.group(1).replace(",", "."))
                if 1 <= r_cand <= 20:
                    fb_rooms = r_cand
            except Exception:
                pass

        # Extract Location
        fb_city = ""
        fb_plz = ""
        fb_district = ""
        m_loc = re.search(r'(?:in|ort|stadt)?\s*([A-Za-zäöüÄÖÜß\- ]+)\s*\((\d{5})\)', raw_title_text)
        if m_loc:
            fb_city = m_loc.group(1).strip()
            fb_plz = m_loc.group(2).strip()
        else:
            m_plz = re.search(r'\b(\d{5})\s+([A-Za-zäöüÄÖÜß\-]+)', text_corpus)
            if m_plz:
                fb_plz = m_plz.group(1)
                fb_city = m_plz.group(2)

        # Extract Images
        fb_images = []
        found_imgs = re.findall(r'https://mms\.immowelt\.de/[a-zA-Z0-9/\-_]+\.jpg', html)
        for img_url in found_imgs:
            if img_url not in fb_images:
                fb_images.append(img_url)

        if fb_price <= 0 and fb_sqm <= 0:
            return {
                "success": False,
                "status_code": 404,
                "error": "Immowelt Exposé enthält weder Kaufpreis noch Wohnfläche (offline oder geschützt).",
                "portal": "Immowelt"
            }

        return {
            "success": True,
            "expose_id": expose_id,
            "title": raw_title_text,
            "description": desc or raw_title_text,
            "price": fb_price,
            "living_space": fb_sqm,
            "rooms": fb_rooms,
            "city": fb_city,
            "postal_code": fb_plz,
            "district": fb_district,
            "images": fb_images,
            "image_url": fb_images[0] if fb_images else "",
            "portal": "Immowelt"
        }
    except Exception as e:
        logger.warning(f"Error fetching Immowelt expose {clean_url}: {e}")
        return {
            "success": False,
            "status_code": 500,
            "error": f"Fehler beim Laden des Immowelt-Exposés: {e}",
            "portal": "Immowelt"
        }
