from fastapi import APIRouter, Body, Query
from typing import Dict, Any, Optional
from app.calculator import calculate_cashflow, calculate_buy_costs, GRUNDERWERBSTEUER_DE
from app.geo_resolver import resolve_location_and_tax
from app.valuation import evaluate_property_valuation, CITY_BENCHMARKS
from app.interest_service import (
    get_live_mortgage_rates, 
    get_active_mortgage_interest_rate,
    get_property_mortgage_interest_quote,
    generate_drklein_calculator_url
)

router = APIRouter(prefix="/api/calculator", tags=["calculator"])

@router.get("/interest-rates")
def get_interest_rates(force_refresh: bool = False):
    """
    Returns current live German mortgage interest rates (Bauzinsen)
    across fixed terms (5, 10, 15, 20, 30 years) with effective and nominal rates.
    """
    return get_live_mortgage_rates(force_refresh=force_refresh)

@router.get("/object-rate")
def get_object_interest_rate(
    price: float = Query(..., description="Kaufpreis in EUR"),
    equity_value: float = Query(20.0, description="Eigenkapital in % oder EUR"),
    equity_type: str = Query("percent", description="'percent' oder 'amount'"),
    postal_code: Optional[str] = Query(None, description="Postleitzahl der Immobilie"),
    term_years: int = Query(10, description="Sollzinsbindung in Jahren"),
    repayment_rate: float = Query(2.0, description="Anfängliche Tilgung in %"),
    renovation_costs: float = Query(0.0, description="Sanierungskosten in EUR"),
    commission_percent: float = Query(3.57, description="Maklerprovision in %"),
    buy_costs_from_equity: bool = Query(True, description="Nebenkosten aus Eigenkapital zahlen")
):
    """
    Returns a customized Dr. Klein live mortgage quote tailored directly
    to the property's purchase price, postal code and financing ratio (LTV),
    including pre-filled direct URL to the official Dr. Klein calculator and tailored terms.
    """
    return get_property_mortgage_interest_quote(
        price=price,
        equity_value=equity_value,
        equity_type=equity_type,
        postal_code=postal_code,
        term_years=term_years,
        repayment_rate=repayment_rate,
        renovation_costs=renovation_costs,
        commission_percent=commission_percent,
        buy_costs_from_equity=buy_costs_from_equity
    )

@router.get("/states")
def get_states():
    return {
        "states": [
            {"state": s, "tax_rate": rate} 
            for s, rate in GRUNDERWERBSTEUER_DE.items()
        ]
    }

@router.get("/resolve-geo")
def resolve_geo(
    postal_code: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    state: Optional[str] = Query(None)
):
    """Auto-resolves Bundesland and exact Grunderwerbsteuer without prompting the user."""
    return resolve_location_and_tax(postal_code=postal_code, city=city, state=state)

@router.get("/benchmarks")
def get_benchmarks():
    return CITY_BENCHMARKS

@router.post("/calculate")
def compute_deal_kpis(data: Dict[str, Any] = Body(...)):
    price = float(data.get("price", 250000.0))
    rent = float(data.get("rent", 1200.0))
    equity_value = float(data.get("equity_value", 20.0))
    equity_type = str(data.get("equity_type", "percent"))
    
    # Use property-specific Dr. Klein live mortgage quote if not explicitly specified
    raw_interest = data.get("interest_rate")
    if raw_interest is not None and float(raw_interest) > 0:
        interest_rate = float(raw_interest)
    else:
        interest_rate = None
    repayment_rate = float(data.get("repayment_rate", 2.0))
    additional_costs = float(data.get("additional_costs", 50.0))
    renovation_costs = float(data.get("renovation_costs", 0.0))
    buy_costs_from_equity = bool(data.get("buy_costs_from_equity", True))
    state = data.get("state")
    postal_code = data.get("postal_code")
    city = data.get("city")
    commission_percent = float(data.get("commission_percent", 3.57))
    fixed_commission = float(data.get("fixed_commission", 0.0))
    living_space = float(data.get("living_space", 75.0))
    market_value = float(data.get("market_value", 0.0)) or None
    include_afa = bool(data.get("include_afa", False))
    building_share_percent = float(data.get("building_share_percent", 80.0))
    depreciation_years = float(data.get("depreciation_years", 50.0))
    marginal_tax_rate = float(data.get("marginal_tax_rate", 42.0))

    result = calculate_cashflow(
        price=price,
        rent=rent,
        equity_value=equity_value,
        equity_type=equity_type,
        interest_rate=interest_rate,
        repayment_rate=repayment_rate,
        additional_costs=additional_costs,
        renovation_costs=renovation_costs,
        buy_costs_from_equity=buy_costs_from_equity,
        state=state,
        postal_code=postal_code,
        city=city,
        commission_percent=commission_percent,
        fixed_commission=fixed_commission,
        living_space=living_space,
        market_value=market_value,
        include_afa=include_afa,
        building_share_percent=building_share_percent,
        depreciation_years=depreciation_years,
        marginal_tax_rate=marginal_tax_rate
    )
    return result

@router.post("/evaluate-market")
def evaluate_market(data: Dict[str, Any] = Body(...)):
    city = str(data.get("city", "Berlin"))
    living_space = float(data.get("living_space", 75.0))
    purchase_price = float(data.get("purchase_price", 300000.0))
    year_built = int(data.get("year_built", 1980))
    condition = str(data.get("condition", "Gepflegt"))
    is_rented = bool(data.get("is_rented", False))
    property_type = str(data.get("property_type", "Eigentumswohnung"))

    val = evaluate_property_valuation(
        city=city,
        living_space=living_space,
        purchase_price=purchase_price,
        year_built=year_built,
        condition=condition,
        is_rented=is_rented,
        property_type=property_type
    )
    return val
