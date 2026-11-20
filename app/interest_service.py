"""
Mortgage Interest Rate Engine (Bauzinsen Live-Benchmark) for ImmoMetrica.
Scrapes live German mortgage interest rates (Baufinanzierungszinsen) from Dr. Klein
and Deutsche Bundesbank benchmark indicators, caches results in SQLite system_settings,
and feeds real-time interest rates into the Cashflow & Investment Calculator.
"""

import re
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import requests
from bs4 import BeautifulSoup

from app.database import get_system_setting, set_system_setting

logger = logging.getLogger("immometrica.interest_service")

# Fallback rates if external internet source is temporarily unavailable
FALLBACK_RATES: Dict[str, Any] = {
    "default_nominal_rate": 3.93,
    "default_effective_rate": 4.03,
    "default_term_years": 10,
    "as_of_date": datetime.now().strftime("%d.%m.%Y"),
    "source_name": "Dr. Klein / Deutsche Bundesbank (Benchmark)",
    "source_url": "https://www.drklein.de/bauzinsen.html",
    "last_fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "rates_by_term": [
        {"years": 5, "nominal_rate": 3.97, "effective_rate": 4.09, "label": "5 Jahre"},
        {"years": 10, "nominal_rate": 3.93, "effective_rate": 4.03, "label": "10 Jahre (Standard)", "is_default": True},
        {"years": 15, "nominal_rate": 4.09, "effective_rate": 4.19, "label": "15 Jahre"},
        {"years": 20, "nominal_rate": 4.18, "effective_rate": 4.28, "label": "20 Jahre"},
        {"years": 30, "nominal_rate": 4.44, "effective_rate": 4.55, "label": "30 Jahre"}
    ]
}


def _parse_rate_float(val: str) -> Optional[float]:
    """Helper to parse '3,93 %' or '3.93' into float."""
    clean = re.sub(r"[^\d,\.]", "", val).replace(",", ".")
    try:
        return float(clean)
    except (ValueError, TypeError):
        return None


def fetch_drklein_live_rates() -> Optional[Dict[str, Any]]:
    """
    Scrapes the daily mortgage rates table from drklein.de/bauzinsen.html.
    """
    url = "https://www.drklein.de/bauzinsen.html"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=5.0)
        if resp.status_code != 200:
            logger.warning(f"Dr. Klein returned HTTP {resp.status_code}")
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        
        # Extract Stand date
        date_match = re.search(r"Stand:?\s*([0-9]{1,2}\.[0-9]{1,2}\.[0-9]{4})", resp.text)
        as_of = date_match.group(1) if date_match else datetime.now().strftime("%d.%m.%Y")

        rates_by_term: List[Dict[str, Any]] = []

        # Find mortgage rates table
        tables = soup.find_all("table")
        for table in tables:
            rows = table.find_all("tr")
            for row in rows:
                cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
                if len(cells) >= 3 and "jahr" in cells[0].lower():
                    # e.g. ['10 Jahre', '3,93 %', '4,03 %']
                    term_match = re.search(r"(\d+)", cells[0])
                    if not term_match:
                        continue
                    years = int(term_match.group(1))
                    nominal = _parse_rate_float(cells[1])
                    effective = _parse_rate_float(cells[2])

                    if nominal is not None:
                        is_default = (years == 10)
                        label = f"{years} Jahre (Standard)" if is_default else f"{years} Jahre"
                        rates_by_term.append({
                            "years": years,
                            "nominal_rate": nominal,
                            "effective_rate": effective if effective is not None else nominal,
                            "label": label,
                            "is_default": is_default
                        })

        if not rates_by_term:
            return None

        # Sort by years
        rates_by_term.sort(key=lambda x: x["years"])

        # Find 10y rate (or first available)
        default_10y = next((r for r in rates_by_term if r["years"] == 10), rates_by_term[0])

        return {
            "default_nominal_rate": default_10y["nominal_rate"],
            "default_effective_rate": default_10y["effective_rate"],
            "default_term_years": default_10y["years"],
            "as_of_date": as_of,
            "source_name": "Dr. Klein Baufinanzierung (Tagesaktuell)",
            "source_url": url,
            "last_fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rates_by_term": rates_by_term
        }

    except Exception as e:
        logger.error(f"Error fetching live mortgage rates from Dr. Klein: {e}")
        return None


def get_live_mortgage_rates(force_refresh: bool = False) -> Dict[str, Any]:
    """
    Returns current German mortgage rates.
    Uses SQLite cached rates if younger than 6 hours; otherwise fetches fresh live data.
    Falls back gracefully if offline.
    """
    cache_key = "cached_mortgage_rates"

    if not force_refresh:
        raw_cache = get_system_setting(cache_key)
        if raw_cache:
            try:
                cached_data = json.loads(raw_cache)
                fetched_at_str = cached_data.get("last_fetched_at")
                if fetched_at_str:
                    fetched_at = datetime.strptime(fetched_at_str, "%Y-%m-%d %H:%M:%S")
                    if datetime.now() - fetched_at < timedelta(hours=6):
                        cached_data["cached"] = True
                        return cached_data
            except Exception as e:
                logger.warning(f"Error reading mortgage rate cache: {e}")

    # Fetch live from internet
    live_rates = fetch_drklein_live_rates()
    if live_rates:
        live_rates["cached"] = False
        try:
            set_system_setting(cache_key, json.dumps(live_rates, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"Error persisting mortgage rate cache: {e}")
        return live_rates

    # Fallback to cache if exists
    raw_cache = get_system_setting(cache_key)
    if raw_cache:
        try:
            cached_data = json.loads(raw_cache)
            cached_data["cached"] = True
            cached_data["warning"] = "Live-Abfrage fehlgeschlagen, gecachte Zinsdaten verwendet."
            return cached_data
        except Exception:
            pass

    # Hard fallback
    fallback = dict(FALLBACK_RATES)
    fallback["cached"] = False
    fallback["warning"] = "Live-Abfrage fehlgeschlagen, Referenz-Marktzins verwendet."
    return fallback


def get_active_mortgage_interest_rate() -> float:
    """
    Returns the nominal mortgage interest rate (10-year term) to use as default in calculations.
    """
    rates = get_live_mortgage_rates()
    return float(rates.get("default_nominal_rate", 3.93))
