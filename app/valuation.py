"""
ImmoMetrica Valuation & Benchmark Engine (Sprengnetter & Market Data Model)
Computes estimated market value, regional benchmark rents, and market deviations.
"""

from typing import Dict, Any, List, Optional

# Regional baseline benchmarks for German metropolitan and secondary cities
CITY_BENCHMARKS = {
    "Berlin": {"avg_sqm_buy": 4850.0, "avg_sqm_rent": 16.50, "avg_yield": 4.08, "state": "Berlin"},
    "München": {"avg_sqm_buy": 8600.0, "avg_sqm_rent": 22.00, "avg_yield": 3.07, "state": "Bayern"},
    "Hamburg": {"avg_sqm_buy": 5400.0, "avg_sqm_rent": 15.80, "avg_yield": 3.51, "state": "Hamburg"},
    "Köln": {"avg_sqm_buy": 4200.0, "avg_sqm_rent": 14.20, "avg_yield": 4.05, "state": "Nordrhein-Westfalen"},
    "Frankfurt am Main": {"avg_sqm_buy": 5800.0, "avg_sqm_rent": 17.50, "avg_yield": 3.62, "state": "Hessen"},
    "Düsseldorf": {"avg_sqm_buy": 4350.0, "avg_sqm_rent": 14.50, "avg_yield": 4.00, "state": "Nordrhein-Westfalen"},
    "Leipzig": {"avg_sqm_buy": 2850.0, "avg_sqm_rent": 9.80, "avg_yield": 4.12, "state": "Sachsen"},
    "Dresden": {"avg_sqm_buy": 2950.0, "avg_sqm_rent": 9.90, "avg_yield": 4.02, "state": "Sachsen"},
    "Essen": {"avg_sqm_buy": 2350.0, "avg_sqm_rent": 9.20, "avg_yield": 4.70, "state": "Nordrhein-Westfalen"},
    "Dortmund": {"avg_sqm_buy": 2280.0, "avg_sqm_rent": 8.90, "avg_yield": 4.68, "state": "Nordrhein-Westfalen"},
    "Stuttgart": {"avg_sqm_buy": 5100.0, "avg_sqm_rent": 16.00, "avg_yield": 3.76, "state": "Baden-Württemberg"},
    "Nürnberg": {"avg_sqm_buy": 3750.0, "avg_sqm_rent": 12.50, "avg_yield": 4.00, "state": "Bayern"},
    "Bremen": {"avg_sqm_buy": 2550.0, "avg_sqm_rent": 9.50, "avg_yield": 4.47, "state": "Bremen"},
    "Hannover": {"avg_sqm_buy": 3200.0, "avg_sqm_rent": 11.20, "avg_yield": 4.20, "state": "Niedersachsen"},
    "Duisburg": {"avg_sqm_buy": 1850.0, "avg_sqm_rent": 8.10, "avg_yield": 5.25, "state": "Nordrhein-Westfalen"},
    "Magdeburg": {"avg_sqm_buy": 2100.0, "avg_sqm_rent": 7.90, "avg_yield": 4.51, "state": "Sachsen-Anhalt"},
    "Bochum": {"avg_sqm_buy": 2550.0, "avg_sqm_rent": 9.40, "avg_yield": 4.42, "state": "Nordrhein-Westfalen"},
    "Wuppertal": {"avg_sqm_buy": 2250.0, "avg_sqm_rent": 8.70, "avg_yield": 4.64, "state": "Nordrhein-Westfalen"},
    "Bielefeld": {"avg_sqm_buy": 2950.0, "avg_sqm_rent": 10.20, "avg_yield": 4.15, "state": "Nordrhein-Westfalen"},
    "Bonn": {"avg_sqm_buy": 4350.0, "avg_sqm_rent": 13.80, "avg_yield": 3.81, "state": "Nordrhein-Westfalen"},
    "Münster": {"avg_sqm_buy": 4450.0, "avg_sqm_rent": 13.60, "avg_yield": 3.67, "state": "Nordrhein-Westfalen"},
    "Aachen": {"avg_sqm_buy": 3450.0, "avg_sqm_rent": 11.40, "avg_yield": 3.97, "state": "Nordrhein-Westfalen"},
    "Gelsenkirchen": {"avg_sqm_buy": 1650.0, "avg_sqm_rent": 7.60, "avg_yield": 5.53, "state": "Nordrhein-Westfalen"},
    "Mönchengladbach": {"avg_sqm_buy": 2400.0, "avg_sqm_rent": 9.10, "avg_yield": 4.55, "state": "Nordrhein-Westfalen"},
    "Krefeld": {"avg_sqm_buy": 2350.0, "avg_sqm_rent": 8.90, "avg_yield": 4.54, "state": "Nordrhein-Westfalen"},
    "Oberhausen": {"avg_sqm_buy": 1950.0, "avg_sqm_rent": 8.30, "avg_yield": 5.11, "state": "Nordrhein-Westfalen"},
    "Hagen": {"avg_sqm_buy": 1850.0, "avg_sqm_rent": 8.00, "avg_yield": 5.19, "state": "Nordrhein-Westfalen"},
    "Hamm": {"avg_sqm_buy": 2150.0, "avg_sqm_rent": 8.50, "avg_yield": 4.74, "state": "Nordrhein-Westfalen"},
    "Herne": {"avg_sqm_buy": 1850.0, "avg_sqm_rent": 7.90, "avg_yield": 5.12, "state": "Nordrhein-Westfalen"},
    "Mülheim an der Ruhr": {"avg_sqm_buy": 2650.0, "avg_sqm_rent": 9.50, "avg_yield": 4.30, "state": "Nordrhein-Westfalen"},
    "Solingen": {"avg_sqm_buy": 2550.0, "avg_sqm_rent": 9.30, "avg_yield": 4.38, "state": "Nordrhein-Westfalen"},
    "Leverkusen": {"avg_sqm_buy": 3350.0, "avg_sqm_rent": 11.30, "avg_yield": 4.05, "state": "Nordrhein-Westfalen"},
    "Karlsruhe": {"avg_sqm_buy": 3950.0, "avg_sqm_rent": 13.00, "avg_yield": 3.95, "state": "Baden-Württemberg"},
    "Mannheim": {"avg_sqm_buy": 3600.0, "avg_sqm_rent": 12.20, "avg_yield": 4.07, "state": "Baden-Württemberg"},
    "Freiburg": {"avg_sqm_buy": 5250.0, "avg_sqm_rent": 15.20, "avg_yield": 3.47, "state": "Baden-Württemberg"},
    "Heidelberg": {"avg_sqm_buy": 5100.0, "avg_sqm_rent": 14.90, "avg_yield": 3.51, "state": "Baden-Württemberg"},
    "Ulm": {"avg_sqm_buy": 4100.0, "avg_sqm_rent": 12.60, "avg_yield": 3.69, "state": "Baden-Württemberg"},
    "Augsburg": {"avg_sqm_buy": 4250.0, "avg_sqm_rent": 13.10, "avg_yield": 3.70, "state": "Bayern"},
    "Regensburg": {"avg_sqm_buy": 4650.0, "avg_sqm_rent": 13.50, "avg_yield": 3.48, "state": "Bayern"},
    "Würzburg": {"avg_sqm_buy": 4100.0, "avg_sqm_rent": 12.40, "avg_yield": 3.63, "state": "Bayern"},
    "Erlangen": {"avg_sqm_buy": 4550.0, "avg_sqm_rent": 13.60, "avg_yield": 3.59, "state": "Bayern"},
    "Fürth": {"avg_sqm_buy": 3550.0, "avg_sqm_rent": 11.80, "avg_yield": 3.99, "state": "Bayern"},
    "Ingolstadt": {"avg_sqm_buy": 4600.0, "avg_sqm_rent": 13.40, "avg_yield": 3.50, "state": "Bayern"},
    "Chemnitz": {"avg_sqm_buy": 1550.0, "avg_sqm_rent": 6.60, "avg_yield": 5.11, "state": "Sachsen"},
    "Halle": {"avg_sqm_buy": 2050.0, "avg_sqm_rent": 7.80, "avg_yield": 4.57, "state": "Sachsen-Anhalt"},
    "Erfurt": {"avg_sqm_buy": 2850.0, "avg_sqm_rent": 9.40, "avg_yield": 3.96, "state": "Thüringen"},
    "Potsdam": {"avg_sqm_buy": 4700.0, "avg_sqm_rent": 13.80, "avg_yield": 3.52, "state": "Brandenburg"},
    "Rostock": {"avg_sqm_buy": 3300.0, "avg_sqm_rent": 10.20, "avg_yield": 3.71, "state": "Mecklenburg-Vorpommern"},
    "Lübeck": {"avg_sqm_buy": 3450.0, "avg_sqm_rent": 11.00, "avg_yield": 3.83, "state": "Schleswig-Holstein"},
    "Kiel": {"avg_sqm_buy": 3150.0, "avg_sqm_rent": 10.60, "avg_yield": 4.04, "state": "Schleswig-Holstein"},
    "Braunschweig": {"avg_sqm_buy": 3100.0, "avg_sqm_rent": 10.50, "avg_yield": 4.06, "state": "Niedersachsen"},
    "Osnabrück": {"avg_sqm_buy": 2950.0, "avg_sqm_rent": 10.10, "avg_yield": 4.11, "state": "Niedersachsen"},
    "Oldenburg": {"avg_sqm_buy": 3350.0, "avg_sqm_rent": 10.80, "avg_yield": 3.87, "state": "Niedersachsen"},
    "Wiesbaden": {"avg_sqm_buy": 4200.0, "avg_sqm_rent": 13.40, "avg_yield": 3.83, "state": "Hessen"},
    "Mainz": {"avg_sqm_buy": 4350.0, "avg_sqm_rent": 13.90, "avg_yield": 3.83, "state": "Rheinland-Pfalz"},
    "Darmstadt": {"avg_sqm_buy": 4400.0, "avg_sqm_rent": 13.70, "avg_yield": 3.74, "state": "Hessen"},
    "Kassel": {"avg_sqm_buy": 2550.0, "avg_sqm_rent": 9.10, "avg_yield": 4.28, "state": "Hessen"},
    "Offenbach": {"avg_sqm_buy": 3850.0, "avg_sqm_rent": 13.10, "avg_yield": 4.08, "state": "Hessen"},
    "Saarbrücken": {"avg_sqm_buy": 2450.0, "avg_sqm_rent": 9.20, "avg_yield": 4.51, "state": "Saarland"},
    "Saarlouis": {"avg_sqm_buy": 2350.0, "avg_sqm_rent": 8.90, "avg_yield": 4.54, "state": "Saarland"},
}

DEFAULT_BENCHMARK = {"avg_sqm_buy": 3100.0, "avg_sqm_rent": 10.50, "avg_yield": 4.06, "state": "Nordrhein-Westfalen"}

def get_city_benchmark(
    city: str,
    postal_code: Optional[str] = None,
    state: Optional[str] = None
) -> Dict[str, Any]:
    if not city and not postal_code:
        return {**DEFAULT_BENCHMARK, "city": "Unbekannt", "state": "Deutschland"}

    for known_city, data in CITY_BENCHMARKS.items():
        if city and (known_city.lower() in city.lower() or city.lower() in known_city.lower()):
            return {**data, "city": known_city}

    # Dynamically resolve state via PLZ + city instead of hardcoding NRW
    resolved_state = state if state and state != "Deutschland" else "Deutschland"
    try:
        from app.geo_resolver import resolve_location_and_tax
        geo = resolve_location_and_tax(postal_code=postal_code, city=city, state=state)
        if geo and geo.get("state"):
            resolved_state = geo["state"]
    except Exception:
        pass

    return {**DEFAULT_BENCHMARK, "city": city or "Unbekannt", "state": resolved_state}

def _classify_unit_size_bracket(base_rent: float, unit_sqm: float) -> Dict[str, Any]:
    """Classifies a single apartment surface into its Mietspiegel bracket."""
    eff = max(15.0, float(unit_sqm or 50.0))
    if eff < 45.0:
        factor = 1.15
        size_label = "Kompaktwohnung (< 45 m²)"
        r_min = round(base_rent * 1.05, 2)
        r_max = round(base_rent * 1.25, 2)
    elif eff <= 80.0:
        factor = 1.03
        size_label = "Standardwohnung (45–80 m²)"
        r_min = round(base_rent * 0.95, 2)
        r_max = round(base_rent * 1.12, 2)
    elif eff <= 120.0:
        factor = 0.97
        size_label = "Großwohnung (80–120 m²)"
        r_min = round(base_rent * 0.90, 2)
        r_max = round(base_rent * 1.05, 2)
    else:
        factor = 0.92
        size_label = "Großraumwohnung (> 120 m²)"
        r_min = round(base_rent * 0.85, 2)
        r_max = round(base_rent * 1.00, 2)
    return {
        "factor": factor,
        "size_label": size_label,
        "r_min": r_min,
        "r_max": r_max,
        "expected_sqm_rent": round(base_rent * factor, 2),
    }


def get_mietspiegel_size_benchmark(
    city: str,
    living_space: float,
    is_package_deal: bool = False,
    unit_count: int = 1,
    units: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Computes German Mietspiegel rent range in €/m² tailored to apartment size category:
    - Kompaktwohnungen (< 45 m²): Higher €/m² due to small surface overhead.
    - Standardwohnungen (45-80 m²): Core standard rental bracket.
    - Großwohnungen (80-120 m²): Slightly lower €/m².
    - Sehr große Wohnungen (> 120 m²): Volume discount in €/m².
    For multi-unit properties (both Mehrfamilienhaus and package deals), evaluates each
    individual unit surface if available, or falls back to average unit size (living_space / unit_count).
    """
    bm = get_city_benchmark(city)
    base_rent = bm["avg_sqm_rent"]
    safe_unit_count = max(1, int(unit_count or 1))
    valid_units = [u for u in (units or []) if isinstance(u, dict) and float(u.get("sqm") or 0) > 0]

    if len(valid_units) > 1 or (valid_units and safe_unit_count > 1):
        unit_list = units if (units and len(units) >= len(valid_units)) else valid_units
        avg_fallback_sqm = max(15.0, float(living_space or 60.0) / max(safe_unit_count, len(unit_list) or 1))
        total_weighted_sqm = 0.0
        total_expected_rent = 0.0
        total_r_min = 0.0
        total_r_max = 0.0
        for u in unit_list:
            u_sqm = float(u.get("sqm") or 0.0)
            if u_sqm <= 0:
                u_sqm = avg_fallback_sqm
            br = _classify_unit_size_bracket(base_rent, u_sqm)
            total_weighted_sqm += u_sqm
            total_expected_rent += u_sqm * br["expected_sqm_rent"]
            total_r_min += u_sqm * br["r_min"]
            total_r_max += u_sqm * br["r_max"]

        effective_sqm = max(15.0, total_weighted_sqm / max(1, len(unit_list)))
        expected_sqm_rent = round(total_expected_rent / max(1.0, total_weighted_sqm), 2)
        r_min = round(total_r_min / max(1.0, total_weighted_sqm), 2)
        r_max = round(total_r_max / max(1.0, total_weighted_sqm), 2)
        avg_bracket = _classify_unit_size_bracket(base_rent, effective_sqm)
        size_label = f"{avg_bracket['size_label']} ({len(unit_list)} WE à Ø {round(effective_sqm)} m²)"
    else:
        # Effective unit size for both Mehrfamilienhaus and package deals
        multi_unit = safe_unit_count > 1 or is_package_deal
        effective_sqm = (float(living_space or 0.0) / safe_unit_count) if (multi_unit and safe_unit_count > 1) else float(living_space or 0.0)
        effective_sqm = max(15.0, effective_sqm)
        br = _classify_unit_size_bracket(base_rent, effective_sqm)
        expected_sqm_rent = br["expected_sqm_rent"]
        r_min = br["r_min"]
        r_max = br["r_max"]
        size_label = (
            f"{br['size_label']} ({safe_unit_count} WE à Ø {round(effective_sqm)} m²)"
            if safe_unit_count > 1
            else br["size_label"]
        )

    min_str = f"{r_min:.2f}".replace(".", ",")
    max_str = f"{r_max:.2f}".replace(".", ",")

    return {
        "city": bm["city"],
        "base_avg_sqm_rent": base_rent,
        "effective_sqm": round(effective_sqm, 1),
        "size_label": size_label,
        "expected_sqm_rent": expected_sqm_rent,
        "range_min": r_min,
        "range_max": r_max,
        "range_label": f"{min_str} € – {max_str} €/m²"
    }

def evaluate_property_valuation(
    city: str,
    living_space: float,
    purchase_price: float,
    year_built: int = 1980,
    condition: str = "Gepflegt",
    is_rented: bool = False,
    property_type: str = "Eigentumswohnung",
    postal_code: Optional[str] = None,
    state: Optional[str] = None
) -> Dict[str, Any]:
    """
    Sprengnetter AVM proxy model:
    Evaluates fair market value per m² based on local benchmark adjusted for:
    - Age / Construction year
    - Condition
    - Tenancy status (occupied rented units typically trade at 10-20% discount)
    - Property type (MFH vs ETW vs EFH)
    """
    benchmark = get_city_benchmark(city, postal_code=postal_code, state=state)
    base_sqm_price = benchmark["avg_sqm_buy"]

    safe_year_built = int(year_built) if year_built else 1980
    safe_condition = str(condition or "Gepflegt")
    safe_property_type = str(property_type or "Eigentumswohnung")

    # Construction year multiplier
    from app.datetime_utils import get_berlin_now
    current_year = get_berlin_now().year
    age = max(0, current_year - safe_year_built)
    if age < 5:
        age_factor = 1.25  # Neubau
    elif age < 15:
        age_factor = 1.15
    elif age < 30:
        age_factor = 1.05
    elif age < 50:
        age_factor = 0.95
    else:
        # Altbau / älterer Bestand
        age_factor = 1.08 if "altbau" in safe_condition.lower() or safe_year_built < 1930 else 0.88

    # Condition multiplier
    cond_factor = 1.0
    cond_lower = safe_condition.lower()
    if "erstbezug" in cond_lower or "neuwertig" in cond_lower:
        cond_factor = 1.20
    elif "saniert" in cond_lower or "renoviert" in cond_lower:
        cond_factor = 1.10
    elif "renovierungsbedürftig" in cond_lower or "sanierungsbedürftig" in cond_lower:
        cond_factor = 0.75
    elif "gepflegt" in cond_lower:
        cond_factor = 1.00

    # Rented status discount
    rented_factor = 0.88 if is_rented else 1.00

    # Property type adjustment
    type_factor = 1.0
    if "mehrfamilienhaus" in safe_property_type.lower():
        type_factor = 0.92 # MFH trade at slightly lower €/sqm
    elif "einfamilienhaus" in safe_property_type.lower():
        type_factor = 1.12

    estimated_sqm_value = round(base_sqm_price * age_factor * cond_factor * rented_factor * type_factor, 2)
    estimated_total_value = round(estimated_sqm_value * living_space, 2)

    actual_sqm_price = round(purchase_price / living_space, 2) if living_space > 0 else 0.0

    deviation_percent = 0.0
    if estimated_total_value > 0:
        deviation_percent = round(((purchase_price - estimated_total_value) / estimated_total_value) * 100.0, 1)

    expected_rent_sqm = round(benchmark["avg_sqm_rent"] * (age_factor * 0.7 + cond_factor * 0.3), 2)
    expected_rent_monthly = round(expected_rent_sqm * living_space, 2)

    return {
        "city": benchmark["city"],
        "state": benchmark["state"],
        "estimated_market_value": estimated_total_value,
        "estimated_market_value_per_sqm": estimated_sqm_value,
        "actual_price_per_sqm": actual_sqm_price,
        "market_deviation_percent": deviation_percent,
        "is_undervalued": deviation_percent < -5.0,
        "bargain_score": max(0, min(100, int(abs(deviation_percent) * 3))) if deviation_percent < 0 else 0,
        "expected_rent_per_sqm": expected_rent_sqm,
        "expected_rent_monthly": expected_rent_monthly,
        "regional_avg_yield": benchmark["avg_yield"],
        "regional_avg_sqm_buy": benchmark["avg_sqm_buy"],
        "regional_avg_sqm_rent": benchmark["avg_sqm_rent"]
    }
