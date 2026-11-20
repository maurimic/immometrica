"""
ImmoMetrica Valuation & Benchmark Engine (Sprengnetter & Market Data Model)
Computes estimated market value, regional benchmark rents, and market deviations.
"""

from typing import Dict, Any, List

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
}

DEFAULT_BENCHMARK = {"avg_sqm_buy": 3100.0, "avg_sqm_rent": 10.50, "avg_yield": 4.06, "state": "Nordrhein-Westfalen"}

def get_city_benchmark(city: str) -> Dict[str, Any]:
    for known_city, data in CITY_BENCHMARKS.items():
        if known_city.lower() in city.lower() or city.lower() in known_city.lower():
            return {**data, "city": known_city}
    return {**DEFAULT_BENCHMARK, "city": city}

def get_mietspiegel_size_benchmark(
    city: str,
    living_space: float,
    is_package_deal: bool = False,
    unit_count: int = 1
) -> Dict[str, Any]:
    """
    Computes German Mietspiegel rent range in €/m² tailored to apartment size category:
    - Kompaktwohnungen (< 45 m²): Higher €/m² due to small surface overhead.
    - Standardwohnungen (45-80 m²): Core standard rental bracket.
    - Großwohnungen (80-120 m²): Slightly lower €/m².
    - Sehr große Wohnungen (> 120 m²): Volume discount in €/m².
    For package deals with multiple units, adjusts based on effective average unit size.
    """
    bm = get_city_benchmark(city)
    base_rent = bm["avg_sqm_rent"]

    # Effective unit size for package deals
    effective_sqm = (living_space / max(1, unit_count)) if (is_package_deal and unit_count > 1) else living_space
    effective_sqm = max(15.0, effective_sqm)

    if effective_sqm < 45.0:
        factor = 1.15
        size_label = "Kompaktwohnung (< 45 m²)"
        r_min = round(base_rent * 1.05, 2)
        r_max = round(base_rent * 1.25, 2)
    elif effective_sqm <= 80.0:
        factor = 1.03
        size_label = "Standardwohnung (45–80 m²)"
        r_min = round(base_rent * 0.95, 2)
        r_max = round(base_rent * 1.12, 2)
    elif effective_sqm <= 120.0:
        factor = 0.97
        size_label = "Großwohnung (80–120 m²)"
        r_min = round(base_rent * 0.90, 2)
        r_max = round(base_rent * 1.05, 2)
    else:
        factor = 0.92
        size_label = "Großraumwohnung (> 120 m²)"
        r_min = round(base_rent * 0.85, 2)
        r_max = round(base_rent * 1.00, 2)

    expected_sqm_rent = round(base_rent * factor, 2)
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
    property_type: str = "Eigentumswohnung"
) -> Dict[str, Any]:
    """
    Sprengnetter AVM proxy model:
    Evaluates fair market value per m² based on local benchmark adjusted for:
    - Age / Construction year
    - Condition
    - Tenancy status (occupied rented units typically trade at 10-20% discount)
    - Property type (MFH vs ETW vs EFH)
    """
    benchmark = get_city_benchmark(city)
    base_sqm_price = benchmark["avg_sqm_buy"]

    # Construction year multiplier
    current_year = 2026
    age = max(0, current_year - year_built)
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
        age_factor = 1.08 if "altbau" in condition.lower() or year_built < 1930 else 0.88

    # Condition multiplier
    cond_factor = 1.0
    cond_lower = condition.lower()
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
    if "mehrfamilienhaus" in property_type.lower():
        type_factor = 0.92 # MFH trade at slightly lower €/sqm
    elif "einfamilienhaus" in property_type.lower():
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
