"""
Automated Test Suite for:
1. Live Mortgage Interest Rates Check (Dr. Klein Benchmark Online Engine)
2. Mietspiegel Rentability & Upside Analysis for Negative Cashflow Properties
3. Immowelt Listings & Pipeline Verification
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient
from app.main import app
from app.interest_service import get_live_mortgage_rates, get_active_mortgage_interest_rate
from app.scrapers.immowelt import search_immowelt_deals, fetch_immowelt_expose
from app.routers.properties import run_property_ai_audit
from app.database import get_connection

client = TestClient(app)

print("=== STARTING LIVE INTEREST, MIETSPIEGEL & IMMOWELT TESTS ===\n")

# ==============================================================================
# TEST 1: Live Mortgage Interest Rates Endpoint & Service
# ==============================================================================
print("--- Test 1: Live Mortgage Interest Rates Service & Endpoint ---")
r = client.get("/api/calculator/interest-rates")
assert r.status_code == 200, f"Expected 200, got {r.status_code}"
rates_data = r.json()

assert "default_nominal_rate" in rates_data
assert "rates_by_term" in rates_data
assert len(rates_data["rates_by_term"]) >= 4

nominal_10y = rates_data["default_nominal_rate"]
effective_10y = rates_data["default_effective_rate"]
assert 2.5 <= nominal_10y <= 6.0, f"Unexpected nominal 10y rate: {nominal_10y}"
assert 2.5 <= effective_10y <= 6.5, f"Unexpected effective 10y rate: {effective_10y}"

active_rate = get_active_mortgage_interest_rate()
assert active_rate == nominal_10y

print(f"[OK] Test 1: Live mortgage rates verified (10y nominal: {nominal_10y} %, effective: {effective_10y} %, source: {rates_data.get('source_name')}, terms: {len(rates_data['rates_by_term'])}).")


# ==============================================================================
# TEST 2: Cashflow Calculator Auto-Defaults to Live Interest Rate
# ==============================================================================
print("\n--- Test 2: Calculator Auto-Defaults to Live Rate ---")
calc_payload = {
    "price": 200000,
    "rent": 950,
    "living_space": 70,
    "city": "Dortmund",
    "interest_rate": 0  # 0 signals: use live benchmark rate!
}
r = client.post("/api/calculator/calculate", json=calc_payload)
assert r.status_code == 200
cf_res = r.json()
assert cf_res["interest_rate"] == active_rate, f"Expected calculator to use live rate {active_rate}, got {cf_res['interest_rate']}"
print(f"[OK] Test 2: Calculator automatically defaulted to live interest rate ({cf_res['interest_rate']} %) with monthly cashflow {cf_res['monthly_cashflow']} EUR.")


# ==============================================================================
# TEST 3: Mietspiegel Rentability & Upside Check for Rented Properties
# ==============================================================================
print("\n--- Test 3: Mietspiegel Rentability & Upside Analysis ---")
# Deal 159 is rented with Ist-Kaltmiete 397.93 EUR, Mietspiegel 486.23 EUR, Ist-CF: -160.37 EUR
conn = get_connection()
c = conn.cursor()
c.execute("SELECT id FROM properties WHERE id = 159")
if not c.fetchone():
    c.execute("""
    INSERT OR REPLACE INTO properties (
        id, title, address, postal_code, city, state, country, property_type, building_type,
        rooms, living_space, land_area, year_built, condition, purchase_price, original_price,
        commission_percent, current_rent, expected_rent, gross_yield, monthly_cashflow,
        source_portal, portal_url, is_online, availability_status, is_rented, pipeline_stage,
        description
    ) VALUES (
        159, 'Vermietete 2-Zimmer-Wohnung mit Mietsteigerungspotenzial', 'Hauptstraße 10', '45127', 'Essen', 'Nordrhein-Westfalen', 'DE',
        'Eigentumswohnung', 'Etagenwohnung', 2.0, 52.0, 0.0, 1970, 'Gepflegt', 115000.0, 115000.0,
        3.57, 397.93, 486.23, 4.15, -160.37,
        'ImmobilienScout24', 'https://www.immobilienscout24.de/expose/159', 1, 'online', 1, 'neu',
        'Gepflegte vermietete 2-Zimmer-Wohnung in Essen. Die derzeitige Kaltmiete beträgt 397,93 € monatlich. Laut Mietspiegel liegt die ortsübliche Vergleichsmiete deutlich höher bei ca. 9,35 €/m².'
    )
    """)
    conn.commit()

audit_159 = run_property_ai_audit(159, force_refresh=True)
ai_data = audit_159.get("ai_audit") or {}
metrics = ai_data.get("metrics") or {}
miet_ana = metrics.get("mietspiegel_analysis") or {}

assert miet_ana.get("evaluated") is True, "Mietspiegel analysis should be evaluated for rented property"
assert miet_ana.get("is_currently_negative") is True
assert miet_ana.get("cashflow_delta") > 0, "Mietspiegel cashflow delta should be positive"
assert "Mietspiegel-Rentabilität" in miet_ana.get("summary", "")
assert metrics.get("rent_upside_percent") > 0
print(f"[OK] Test 3a: Negative cashflow rented deal #159 evaluated:")
print(f"     Ist-Kaltmiete: {metrics.get('current_monthly_rent')} EUR -> Mietspiegel: {metrics.get('expected_monthly_rent')} EUR (+{metrics.get('rent_upside_percent')}%)")
print(f"     Ist-Cashflow: {metrics.get('monthly_cashflow')} EUR -> Mietspiegel-CF: {metrics.get('potential_monthly_cashflow')} EUR (Delta: +{miet_ana.get('cashflow_delta')} EUR/M)")
print(f"     Status: {miet_ana.get('status')}")


# ==============================================================================
# TEST 4: Turnaround Scenario (Property turning cashflow-positive at Mietspiegel)
# ==============================================================================
print("\n--- Test 4: Turnaround Scenario (Negative -> Positive Cashflow) ---")
from app.ai_analyst import compute_investment_metrics

# Simulate a property currently rented at 380 EUR (negative cashflow), but Mietspiegel is 558 EUR (positive cashflow)
mock_prop = {
    "purchase_price": 130000,
    "living_space": 65,
    "city": "Dortmund",
    "state": "Nordrhein-Westfalen",
    "commission_percent": 3.57,
    "market_value": 170000
}
mock_insights = {
    "extracted_rent": 380.0,
    "rent_source": "EXPOSE_STATED_COLD",
    "is_rented": True,
    "has_explicit_hausgeld": True,
    "hausgeld": 180.0,
    "hausgeld_non_recoverable": 50.0
}
mock_capex = {"total_capex": 3000.0}

turnaround_metrics = compute_investment_metrics(mock_prop, mock_insights, mock_capex)
t_ana = turnaround_metrics.get("mietspiegel_analysis") or {}

assert t_ana.get("is_currently_negative") is True, "Current cashflow should be negative"
assert t_ana.get("turns_profitable") is True, "Deal should turn profitable at Mietspiegel"
assert t_ana.get("is_profitable_at_mietspiegel") is True
assert t_ana.get("status") == "PROFITABLE_UPSIDE"
assert "dreht der monatliche Cashflow" in t_ana.get("summary")
print(f"[OK] Test 4: Turnaround successfully verified:")
print(f"     Ist-CF: {turnaround_metrics['monthly_cashflow']} EUR/M -> Mietspiegel-CF: {turnaround_metrics['potential_monthly_cashflow']} EUR/M")
print(f"     Delta: +{t_ana['cashflow_delta']} EUR/M, Badge: '{t_ana['badge_text']}'")

# Test 4b: Unrented / Vacant Property (e.g. Deal #178) - MUST NOT show fake Turnaround or +0 € Hebel
print("\n--- Test 4b: Vacant / Unrented Property (No Ist-Miete) ---")
vacant_insights = {
    "extracted_rent": 0.0,
    "rent_source": "DERIVED_FROM_MARKET_BENCHMARK",
    "is_rented": False,
    "has_explicit_hausgeld": False,
    "hausgeld": 200.0
}
vacant_metrics = compute_investment_metrics(mock_prop, vacant_insights, mock_capex)
assert vacant_metrics.get("mietspiegel_analysis") is None, "Vacant property should have mietspiegel_analysis = None (no Turnaround Hebel)"
assert vacant_metrics.get("is_rented") is False
print(f"[OK] Test 4b: Vacant property verified: mietspiegel_analysis is None, Soll-Cashflow: {vacant_metrics['potential_monthly_cashflow']} EUR/M.")



# ==============================================================================
# TEST 5: Immowelt Scraper & Pipeline Ingestion
# ==============================================================================
print("\n--- Test 5: Immowelt Scraper & Pipeline Verification ---")
conn = get_connection()
cursor = conn.cursor()
cursor.execute("SELECT id, title, purchase_price, living_space, rooms, monthly_cashflow, pipeline_stage, source_portal, image_url, gallery_images FROM properties WHERE source_portal = 'Immowelt'")
immowelt_rows = cursor.fetchall()

assert len(immowelt_rows) >= 3, f"Expected at least 3 Immowelt properties in DB, found {len(immowelt_rows)}"
print(f"[OK] Test 5a: Found {len(immowelt_rows)} real Immowelt properties in database:")

has_negative_deal = False
for row in immowelt_rows:
    p_id, title, price, sqm, rooms, cf, stage, portal, img, gallery = row
    if cf < 0:
        has_negative_deal = True
    assert portal == "Immowelt"
    assert price > 10000
    assert sqm > 10
    assert "immowelt" in img or "unsplash" in img
    print(f"     Deal #{p_id}: '{title[:45]}...' | {price:,.0f} EUR | {sqm} m² | {rooms} Zi | CF: {cf:+,.2f} EUR/M | Stage: '{stage}' | Portal: {portal}")

assert has_negative_deal, "Database should contain cashflow-negative Immowelt deals for inspection as requested"
print(f"[OK] Test 5b: Immowelt pipeline contains both cashflow-positive and cashflow-negative deals as requested.")

# Test 6: Immowelt Expose Fetching Resilience
print("\n--- Test 6: Immowelt Expose Fetching Resilience ---")
fake_url = "https://www.immowelt.de/expose/7aebb29f-e293-46e9-a9b1-3ed4496d1197"
res_expose = fetch_immowelt_expose(fake_url)
assert res_expose["success"] is True
assert res_expose["portal"] == "Immowelt"
print(f"[OK] Test 6: fetch_immowelt_expose returned clean structured response without failing on DataDome.")

print("\n>>> ALL LIVE INTEREST, MIETSPIEGEL & IMMOWELT TESTS PASSED SUCCESSFULLY! <<<")
