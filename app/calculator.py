"""
ImmoMetrica Clean-Room Investment & Cashflow Calculation Engine
Extracted and reverse-engineered from ImmoMetrica core JavaScript bundle.
Includes AfA (Absetzung für Abnutzung), Building/Land Split, Gutachten-Restnutzungsdauer,
and German V+V (Vermietung & Verpachtung) Tax Effect calculations.
"""

from typing import Dict, Any, Optional
from app.geo_resolver import resolve_location_and_tax, GRUNDERWERBSTEUER_DE

def calculate_buy_costs(
    price: float,
    country: str = "DE",
    transfer_tax_percent: float = 6.0,
    commission_percent: float = 3.57,
    fixed_commission: float = 0.0,
    notary_percent: float = 2.0
) -> Dict[str, float]:
    """
    Computes ancillary acquisition costs (Kaufnebenkosten):
    - Notary & Land Registry (Notar & Grundbuch): ~2.0%
    - Property Transfer Tax (Grunderwerbsteuer): based on state / country
    - Broker Commission (Maklerprovision): 0% to 3.57% or fixed
    """
    notary_costs = round(price * (notary_percent / 100.0), 2)

    if country == "CH":
        # Swiss average stamp & notary ~0.8%
        transfer_tax = round(price * 0.008, 2)
    elif country == "AT":
        # Austria: 3.5% GrESt + 1.1% Grundbuch + 3.0% Makler + 1.2% Notar
        transfer_tax = round(price * (transfer_tax_percent / 100.0), 2)
    else:
        transfer_tax = round(price * (transfer_tax_percent / 100.0), 2)

    if fixed_commission > 0:
        broker_costs = round(fixed_commission, 2)
    elif commission_percent > 0:
        broker_costs = round(price * (commission_percent / 100.0), 2)
    else:
        broker_costs = 0.0

    total_costs = round(notary_costs + transfer_tax + broker_costs, 2)

    return {
        "notary_costs": notary_costs,
        "transfer_tax": transfer_tax,
        "broker_costs": broker_costs,
        "total_buy_costs": total_costs,
        "total_buy_costs_percent": round((total_costs / price) * 100.0, 2) if price > 0 else 0.0
    }

def calculate_cashflow(
    price: float,
    rent: float,
    equity_value: float = 20.0,
    equity_type: str = "percent", # "percent" or "amount"
    interest_rate: Optional[float] = None, # Zinssatz in % (None = Dr. Klein Tagesaktueller 10J-Benchmark)
    repayment_rate: float = 2.0,    # Tilgungssatz in %
    additional_costs: float = 50.0, # Nicht umlegbare Bewirtschaftungskosten (Hausgeld/Instandhaltung)
    renovation_costs: float = 0.0,  # Sanierungskosten
    buy_costs_from_equity: bool = True,
    country: str = "DE",
    state: Optional[str] = None,
    postal_code: Optional[str] = None,
    city: Optional[str] = None,
    commission_percent: float = 3.57,
    fixed_commission: float = 0.0,
    living_space: float = 0.0,
    market_value: Optional[float] = None,
    # AfA & Tax effect parameters (V+V)
    include_afa: bool = False,
    building_share_percent: float = 80.0,
    depreciation_years: float = 50.0,
    marginal_tax_rate: float = 42.0
) -> Dict[str, Any]:
    """
    Exact implementation of ImmoMetrica's cashflowCalculator component:
    Credit = Price - Equity + [BuyCosts if financed] + RenovationCosts
    Rate = Credit * (Interest% + Repayment%) / 12
    Cashflow = MonthlyRent - MonthlyRate - AdditionalCosts
    Automatically infers federal state and Grunderwerbsteuer from postal_code/city/state.
    Optionally computes German V+V depreciation (AfA) and tax refunds/liability.
    """
    drklein_quote = None
    if interest_rate is None or interest_rate <= 0:
        try:
            from app.interest_service import get_property_mortgage_interest_quote
            drklein_quote = get_property_mortgage_interest_quote(
                price=price,
                equity_value=equity_value,
                equity_type=equity_type,
                postal_code=postal_code,
                repayment_rate=repayment_rate,
                renovation_costs=renovation_costs,
                commission_percent=commission_percent,
                buy_costs_from_equity=buy_costs_from_equity
            )
            interest_rate = float(drklein_quote.get("nominal_rate", 3.93))
        except Exception:
            try:
                from app.interest_service import get_active_mortgage_interest_rate
                interest_rate = get_active_mortgage_interest_rate()
            except Exception:
                interest_rate = 3.93

    geo_info = resolve_location_and_tax(postal_code=postal_code, city=city, state=state)
    resolved_state = geo_info["state"]
    transfer_tax_rate = geo_info["tax_rate"]

    buy_costs_data = calculate_buy_costs(
        price=price,
        country=country,
        transfer_tax_percent=transfer_tax_rate,
        commission_percent=commission_percent,
        fixed_commission=fixed_commission
    )
    total_buy_costs = buy_costs_data["total_buy_costs"]

    if equity_type == "percent":
        equity_amount = round((equity_value / 100.0) * price, 2)
    else:
        equity_amount = round(equity_value, 2)

    # Loan principal calculation
    loan_amount = price - equity_amount
    if not buy_costs_from_equity:
        loan_amount += total_buy_costs
    if renovation_costs > 0:
        loan_amount += renovation_costs
    loan_amount = max(0.0, round(loan_amount, 2))

    # Total investment amount
    total_investment = price + total_buy_costs + renovation_costs
    actual_equity_invested = equity_amount + (total_buy_costs if buy_costs_from_equity else 0.0)

    # Monthly bank rate (Annuity)
    annual_rate_percent = interest_rate + repayment_rate
    monthly_rate = round(loan_amount * (annual_rate_percent / 100.0) / 12.0, 2)

    # Cashflow before tax
    monthly_cashflow = round(rent - monthly_rate - additional_costs, 2)
    annual_cashflow = round(monthly_cashflow * 12.0, 2)

    # Return on Equity (Eigenkapitalrendite / Cash-on-Cash Return)
    cash_on_cash = round((annual_cashflow / actual_equity_invested) * 100.0, 2) if actual_equity_invested > 0 else 0.0

    # Gross Rental Yield (Bruttomietrendite)
    gross_yield = round(((rent * 12.0) / price) * 100.0, 2) if price > 0 else 0.0

    # Net Rental Yield (Nettomietrendite)
    net_rent_annual = max(0.0, (rent - additional_costs) * 12.0)
    net_yield = round((net_rent_annual / total_investment) * 100.0, 2) if total_investment > 0 else 0.0

    # Purchase Price Multiple (Faktor / Vervielfältiger)
    multiplier = round(price / (rent * 12.0), 1) if rent > 0 else 0.0

    # Market Deviation (Abweichung zum Marktwert)
    market_deviation_pct = 0.0
    if market_value and market_value > 0:
        market_deviation_pct = round(((price - market_value) / market_value) * 100.0, 1)

    # AfA & German Real Estate Tax Calculations (Vermietung & Verpachtung - V+V)
    depreciation_years = float(depreciation_years) if depreciation_years and float(depreciation_years) > 0 else 50.0
    afa_rate_percent = round((100.0 / depreciation_years), 2)
    building_share_clean = max(0.0, min(100.0, float(building_share_percent)))
    land_share_clean = round(100.0 - building_share_clean, 2)

    # Depreciable base = building portion of acquisition cost (Kaufpreis + Nebenkosten) + 100% Sanierung
    depreciable_base = round((price + total_buy_costs) * (building_share_clean / 100.0) + renovation_costs, 2)
    land_value = round((price + total_buy_costs) * (land_share_clean / 100.0), 2)

    annual_afa = round(depreciable_base * (afa_rate_percent / 100.0), 2)
    monthly_afa = round(annual_afa / 12.0, 2)

    # Annual interest expense (Werbungskosten Schuldzinsen im 1. Jahr)
    annual_interest = round(loan_amount * (interest_rate / 100.0), 2)
    monthly_interest = round(annual_interest / 12.0, 2)

    # Annual additional operating costs (non-allocatable)
    annual_additional_costs = round(additional_costs * 12.0, 2)

    # Annual gross rental revenue
    annual_gross_rent = round(rent * 12.0, 2)

    # Total deductible expenses for V+V (Werbungskosten)
    annual_tax_deductions = round(annual_interest + annual_additional_costs + annual_afa, 2)

    # Taxable income from renting (Einkünfte aus V+V)
    taxable_income_vv = round(annual_gross_rent - annual_tax_deductions, 2)

    # Tax effect (+ = refund / tax savings from loss setoff; - = tax liability from positive income)
    marginal_tax_rate_clean = max(0.0, min(60.0, float(marginal_tax_rate)))
    annual_tax_effect = round(-taxable_income_vv * (marginal_tax_rate_clean / 100.0), 2)
    monthly_tax_effect = round(annual_tax_effect / 12.0, 2)

    # Cashflow after tax
    monthly_cashflow_after_tax = round(monthly_cashflow + (monthly_tax_effect if include_afa else 0.0), 2)
    annual_cashflow_after_tax = round(monthly_cashflow_after_tax * 12.0, 2)

    try:
        from app.interest_service import generate_drklein_calculator_url
        drklein_calculator_url = generate_drklein_calculator_url(
            price=price,
            equity=equity_amount,
            loan_amount=loan_amount,
            postal_code=geo_info.get("postal_code") or postal_code,
            repayment_rate=repayment_rate,
            buy_costs_from_equity=buy_costs_from_equity,
            renovation_costs=renovation_costs,
            commission_percent=commission_percent
        )
    except Exception:
        drklein_calculator_url = f"https://www.drklein.de/bauzinsen-rechner.html?kaufpreis={int(round(price))}#construction-interest-calculator"

    bank_name = drklein_quote.get("bank_name", "Dr. Klein Partnerbank") if drklein_quote else "Dr. Klein Benchmark"
    is_live_quote = drklein_quote.get("is_live_quote", False) if drklein_quote else False

    return {
        "price": price,
        "rent": rent,
        "living_space": living_space,
        "rent_per_sqm": round(rent / living_space, 2) if living_space > 0 else 0.0,
        "price_per_sqm": round(price / living_space, 2) if living_space > 0 else 0.0,
        "equity_amount": equity_amount,
        "equity_type": equity_type,
        "equity_value": equity_value,
        "actual_equity_invested": actual_equity_invested,
        "total_buy_costs": total_buy_costs,
        "buy_costs_details": buy_costs_data,
        "renovation_costs": round(renovation_costs, 2),
        "total_investment": round(total_investment, 2),
        "loan_amount": loan_amount,
        "interest_rate": interest_rate,
        "repayment_rate": repayment_rate,
        "monthly_rate": monthly_rate,
        "additional_costs": additional_costs,
        "monthly_cashflow": monthly_cashflow,
        "annual_cashflow": annual_cashflow,
        "cashflow_tone": "cashflow-positive" if monthly_cashflow >= 0 else "cashflow-negative",
        "gross_yield": gross_yield,
        "net_yield": net_yield,
        "cash_on_cash": cash_on_cash,
        "multiplier": multiplier,
        "state": resolved_state,
        "transfer_tax_rate": transfer_tax_rate,
        "postal_code": geo_info["postal_code"],
        "city": geo_info["city"],
        "market_value": market_value,
        "market_deviation_percent": market_deviation_pct,
        "is_undervalued": market_deviation_pct < -5.0,
        # AfA & Tax details
        "include_afa": include_afa,
        "building_share_percent": building_share_clean,
        "land_share_percent": land_share_clean,
        "depreciation_years": depreciation_years,
        "afa_rate_percent": afa_rate_percent,
        "marginal_tax_rate": marginal_tax_rate_clean,
        "depreciable_base": depreciable_base,
        "land_value": land_value,
        "annual_afa": annual_afa,
        "monthly_afa": monthly_afa,
        "annual_interest": annual_interest,
        "monthly_interest": monthly_interest,
        "annual_tax_deductions": annual_tax_deductions,
        "taxable_income_vv": taxable_income_vv,
        "annual_tax_effect": annual_tax_effect,
        "monthly_tax_effect": monthly_tax_effect,
        "monthly_cashflow_after_tax": monthly_cashflow_after_tax,
        "annual_cashflow_after_tax": annual_cashflow_after_tax,
        "drklein_calculator_url": drklein_calculator_url,
        "mortgage_bank_name": bank_name,
        "is_drklein_live_quote": is_live_quote
    }
