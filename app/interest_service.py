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


def format_bank_name(raw_name: Optional[str]) -> str:
    """Converts internal Dr. Klein bank producer identifiers to clean human-readable bank names."""
    if not raw_name:
        return "Dr. Klein Partnerbank"
    raw_upper = raw_name.strip().upper()
    known = {
        "ALLIANZ": "Allianz",
        "DKB": "DKB",
        "HYPOVEREINSBANK": "HypoVereinsbank",
        "VOLKSWOHL_BUND": "Volkswohl Bund",
        "ING_DIBA": "ING",
        "COMMERZBANK": "Commerzbank",
        "SPARKASSE": "Sparkasse",
        "DSL_BANK": "DSL Bank",
        "MAINZER_VOLKSBANK": "Mainzer Volksbank",
        "PSD_BANK": "PSD Bank",
        "SIGNAL_IDUNA": "Signal Iduna",
        "ERGO": "ERGO",
        "AXA": "AXA",
        "DEBEKA": "Debeka",
        "MUENCHENER_HYP": "MünchenerHyp",
        "BERLIN_HYP": "Berlin Hyp",
    }
    if raw_upper in known:
        return known[raw_upper]
    return raw_name.replace("VOBA_", "Volksbank ").replace("_", " ").title()


def generate_drklein_calculator_url(
    price: float,
    equity: float = 0.0,
    loan_amount: Optional[float] = None,
    postal_code: Optional[str] = None,
    term_years: int = 10,
    repayment_rate: float = 2.0,
    buy_costs_from_equity: bool = True,
    renovation_costs: float = 0.0,
    commission_percent: float = 3.57
) -> str:
    """
    Generates a direct pre-filled URL to the official Dr. Klein Bauzinsen-Rechner.
    Binds the exact user-entered Eigenkapital in EUR directly to Dr. Klein's 'eigenkapital' field.
    Reconciles the Darlehensbetrag so that it encompasses Kaufpreis + Grunderwerbsteuer + Notar/Grundbuch
    + Maklerprovision + Sanierungskosten (CaPEx) abzüglich des Eigenkapitals.
    """
    p = max(10000.0, float(price))
    tax_rate = 6.0
    try:
        from app.geo_resolver import resolve_location_and_tax
        geo = resolve_location_and_tax(postal_code=postal_code)
        tax_rate = float(geo.get("tax_rate") or 6.0)
    except Exception:
        pass

    eq_eur = max(0, int(round(equity)))
    capex = max(0, int(round(renovation_costs or 0)))
    commission = max(0.0, float(commission_percent or 0))

    # Total ancillary costs (GrESt + 2.0% Notar/Grundbuch + Maklerprovision)
    nebenkosten_pct = tax_rate + 2.0 + commission
    nebenkosten_eur = int(round(p * (nebenkosten_pct / 100.0)))
    total_financing_need = int(round(p + nebenkosten_eur + capex))

    if loan_amount is not None and loan_amount > 0:
        loan_target = int(round(loan_amount))
    else:
        loan_target = max(5000, total_financing_need - eq_eur)

    plz_clean = re.sub(r"\D", "", str(postal_code or ""))[:5]
    plz_param = f"&plz={plz_clean}" if plz_clean else ""
    tilg_int = max(1, min(10, int(round(repayment_rate or 2.0))))

    url = (
        f"https://www.drklein.de/bauzinsen-rechner.html?"
        f"kaufpreis={int(round(p))}&eigenkapital={eq_eur}&darlehensbetrag={loan_target}"
        f"{plz_param}&sollzinsbindung={term_years}&tilgungssatz={tilg_int}"
    )
    if capex > 0:
        url += f"&modernisierungskosten={capex}"
    if commission > 0:
        url += f"&maklerprovisionPct={commission}&maklerprovision={commission}"
    url += "#construction-interest-calculator"
    return url


def fetch_drklein_quote_for_property(
    price: float,
    loan_amount: float,
    postal_code: Optional[str] = None,
    term_years: int = 10,
    repayment_rate: float = 2.0,
    force_refresh: bool = False,
    renovation_costs: float = 0.0,
    commission_percent: float = 3.57,
    equity_amount: Optional[float] = None
) -> Dict[str, Any]:
    """
    Queries Dr. Klein's live REST API (/rest-api/tav/angebot) for a tailor-made mortgage quote
    tailored to the exact purchase price and loan volume (accounting for Kleindarlehensaufschläge).
    Caches quotes in SQLite system_settings for 12 hours.
    Falls back gracefully if offline or request times out.
    """
    p_clean = max(10000.0, float(price))
    loan_clean = max(5000.0, float(loan_amount))
    plz_clean = re.sub(r"\D", "", str(postal_code or ""))[:5] or None
    equity_clean = max(0.0, float(equity_amount)) if equity_amount is not None else max(0.0, p_clean - loan_clean)
    ltv = round((loan_clean / p_clean) * 100.0, 1)

    calc_url = generate_drklein_calculator_url(
        price=p_clean,
        equity=equity_clean,
        loan_amount=loan_clean,
        postal_code=plz_clean,
        term_years=term_years,
        repayment_rate=repayment_rate,
        renovation_costs=renovation_costs,
        commission_percent=commission_percent
    )

    # Cache key rounding brackets to reduce redundant external calls
    p_bracket = int(round(p_clean / 2500.0) * 2500)
    loan_bracket = int(round(loan_clean / 2500.0) * 2500)
    repay_bracket = int(round(repayment_rate * 10.0))
    cache_key = f"drklein_quote_{p_bracket}_{loan_bracket}_{plz_clean or 'none'}_{term_years}_{repay_bracket}"

    if not force_refresh:
        raw_cache = get_system_setting(cache_key)
        if raw_cache:
            try:
                cached_data = json.loads(raw_cache)
                fetched_at_str = cached_data.get("fetched_at")
                if fetched_at_str:
                    fetched_at = datetime.strptime(fetched_at_str, "%Y-%m-%d %H:%M:%S")
                    if datetime.now() - fetched_at < timedelta(hours=12):
                        cached_data["cached"] = True
                        cached_data["calculator_url"] = calc_url
                        return cached_data
            except Exception as e:
                logger.warning(f"Error reading quote cache for {cache_key}: {e}")

    # Query Dr. Klein live REST API
    api_url = "https://www.drklein.de/rest-api/tav/angebot"
    headers = {
        "Content-Type": "application/json;charset=utf-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Origin": "https://www.drklein.de",
        "Referer": "https://www.drklein.de/bauzinsen-rechner.html"
    }
    payload = {
        "immobilie": {
            "kaufPreis": int(round(p_clean)),
            "postleitzahl": plz_clean
        },
        "finanzierung": {
            "darlehensbetrag": int(round(loan_clean)),
            "monatlicheRate": None,
            "auszahlungstermin": None,
            "finanzierungszweck": "KAUF",
            "tilgungssatz": float(repayment_rate),
            "darlehensart": "ANNUITAET",
            "laufzeit": int(term_years)
        }
    }

    try:
        resp = requests.post(api_url, headers=headers, json=payload, timeout=4.0)
        if resp.status_code == 200:
            res_json = resp.json()
            soll_zins = float(res_json.get("sollZins") or 0.0)
            eff_zins = float(res_json.get("effektivZins") or soll_zins)
            monthly_rate = float(res_json.get("monatlicheRate") or 0.0)
            raw_producer = res_json.get("produzent")
            bank_name = format_bank_name(raw_producer)

            if soll_zins > 0:
                result = {
                    "price": p_clean,
                    "loan_amount": loan_clean,
                    "nominal_rate": round(soll_zins, 2),
                    "effective_rate": round(eff_zins, 2),
                    "monthly_rate": round(monthly_rate, 2),
                    "term_years": term_years,
                    "repayment_rate": repayment_rate,
                    "bank_name": bank_name,
                    "bank_code": raw_producer or "UNKNOWN",
                    "ltv": ltv,
                    "source": "Dr. Klein Live-Schnittstelle",
                    "calculator_url": calc_url,
                    "cached": False,
                    "is_live_quote": True,
                    "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                }
                try:
                    set_system_setting(cache_key, json.dumps(result, ensure_ascii=False))
                except Exception as e:
                    logger.warning(f"Error persisting quote cache: {e}")
                return result
    except Exception as e:
        logger.info(f"Dr. Klein live quote request timed out or failed ({e}), using tiered volume benchmark.")

    # Graceful Fallback: Volume-adjusted market rate based on Dr. Klein daily benchmark
    base_benchmark = get_active_mortgage_interest_rate()
    volume_markup = 0.0
    if loan_clean < 65000:
        volume_markup = 0.60
    elif loan_clean < 100000:
        volume_markup = 0.50
    elif loan_clean < 150000:
        volume_markup = 0.20
    elif loan_clean >= 400000:
        volume_markup = -0.12

    ltv_markup = 0.20 if ltv > 85.0 else 0.0
    adjusted_nominal = round(base_benchmark + volume_markup + ltv_markup, 2)
    adjusted_effective = round(adjusted_nominal + 0.12, 2)
    approx_rate = round(loan_clean * ((adjusted_nominal + repayment_rate) / 100.0) / 12.0, 2)

    return {
        "price": p_clean,
        "loan_amount": loan_clean,
        "nominal_rate": adjusted_nominal,
        "effective_rate": adjusted_effective,
        "monthly_rate": approx_rate,
        "term_years": term_years,
        "repayment_rate": repayment_rate,
        "bank_name": "Dr. Klein Konditionsvergleich",
        "bank_code": "BENCHMARK_VOLUME_ADJUSTED",
        "ltv": ltv,
        "source": "Dr. Klein Baufinanzierung (Volumen-Staffelung)",
        "calculator_url": calc_url,
        "cached": False,
        "is_live_quote": False,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


def get_property_mortgage_interest_quote(
    price: float,
    equity_value: float = 20.0,
    equity_type: str = "percent",
    postal_code: Optional[str] = None,
    term_years: int = 10,
    repayment_rate: float = 2.0,
    renovation_costs: float = 0.0,
    commission_percent: float = 3.57,
    buy_costs_from_equity: bool = True
) -> Dict[str, Any]:
    """
    Convenience method calculating loan amount and returning the tailored Dr. Klein quote.
    """
    p = max(10000.0, float(price))
    if equity_type == "percent":
        eq = round((float(equity_value) / 100.0) * p, 2)
    else:
        eq = round(float(equity_value), 2)

    capex = max(0.0, float(renovation_costs or 0))
    commission = max(0.0, float(commission_percent or 0))

    tax_rate = 6.0
    try:
        from app.geo_resolver import resolve_location_and_tax
        geo = resolve_location_and_tax(postal_code=postal_code)
        tax_rate = float(geo.get("tax_rate") or 6.0)
    except Exception:
        pass

    nebenkosten_pct = tax_rate + 2.0 + commission
    nebenkosten_eur = round(p * (nebenkosten_pct / 100.0), 2)
    total_financing_need = round(p + nebenkosten_eur + capex, 2)
    loan = max(5000.0, round(total_financing_need - eq, 2))

    return fetch_drklein_quote_for_property(
        price=p,
        loan_amount=loan,
        postal_code=postal_code,
        term_years=term_years,
        repayment_rate=repayment_rate,
        renovation_costs=capex,
        commission_percent=commission,
        equity_amount=eq
    )

