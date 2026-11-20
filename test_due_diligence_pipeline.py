import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def test_deal_44_private_and_cold_rent():
    # 1. Trigger AI Audit on Deal #44
    r = client.post("/api/properties/44/ai-audit?force_refresh=false")
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"
    data = r.json()
    audit = data["ai_audit"]
    updates = data["property_updates"]

    print("\n--- Test Deal #44 AI Audit Verification ---")
    print("Title:", audit.get("metrics", {}).get("title", "Deal #44"))
    print("Cold Rent:", audit["metrics"]["current_monthly_rent"], "EUR")
    print("Gross Yield:", audit["metrics"]["gross_yield"], "%")
    print("Is Private:", audit["insights"]["is_private"])
    print("Commission Free:", audit["insights"]["commission_free"])
    print("Commission Percent:", audit["insights"]["commission_percent"], "%")
    print("Monthly Cashflow:", audit["metrics"]["monthly_cashflow"], "EUR")

    # Assertions
    assert audit["insights"]["is_private"] == True, "Deal #44 must be recognized as private sale!"
    assert audit["insights"]["commission_free"] == True, "Deal #44 must be recognized as commission free!"
    assert audit["insights"]["commission_percent"] == 0.0, "Deal #44 commission percent must be 0.0!"
    assert audit["metrics"]["current_monthly_rent"] == 372.0, f"Expected cold rent 372.0, got {audit['metrics']['current_monthly_rent']}"
    assert audit["metrics"]["gross_yield"] == 6.87, f"Expected gross yield 6.87%, got {audit['metrics']['gross_yield']}%"
    assert audit["metrics"]["monthly_cashflow"] > 0, "Deal #44 must have positive monthly cashflow!"

    # Verify that NO broker commission risk is listed
    broker_risks = [r for r in audit["risks"] if "maklerprovision" in r["title"].lower() and r["severity"] != "POSITIVE"]
    assert len(broker_risks) == 0, f"Deal #44 must NOT have any broker commission risk, but found: {broker_risks}"

    # Verify positive highlight for 0% commission
    positive_adv = [r for r in audit["risks"] if r["severity"] == "POSITIVE"]
    assert len(positive_adv) > 0, "Deal #44 must have positive highlight for 0% commission!"
    print("[OK] Deal #44 verified: Cold rent 372 EUR, 6.87% yield, 0% commission, and no false broker risk!")

def test_deal_42_stated_yield():
    r = client.post("/api/properties/42/ai-audit?force_refresh=false")
    assert r.status_code == 200
    data = r.json()
    audit = data["ai_audit"]

    print("\n--- Test Deal #42 Stated Yield Verification ---")
    print("Cold Rent:", audit["metrics"]["current_monthly_rent"], "EUR")
    print("Gross Yield:", audit["metrics"]["gross_yield"], "%")
    print("Commission Percent:", audit["insights"]["commission_percent"], "%")

    assert audit["metrics"]["gross_yield"] == 6.56, f"Expected 6.56% stated yield, got {audit['metrics']['gross_yield']}"
    assert audit["metrics"]["current_monthly_rent"] == 426.4, f"Expected derived cold rent 426.4, got {audit['metrics']['current_monthly_rent']}"
    assert audit["insights"]["commission_percent"] == 4.76, f"Expected 4.76% buyer commission, got {audit['insights']['commission_percent']}"
    print("[OK] Deal #42 verified: 6.56% stated yield, 426.40 EUR derived cold rent, 4.76% commission correctly extracted!")

def test_deal_46_stated_rent():
    r = client.post("/api/properties/46/ai-audit?force_refresh=false")
    assert r.status_code == 200
    data = r.json()
    audit = data["ai_audit"]

    print("\n--- Test Deal #46 Stated Cold Rent Verification ---")
    print("Cold Rent:", audit["metrics"]["current_monthly_rent"], "EUR")
    print("Gross Yield:", audit["metrics"]["gross_yield"], "%")

    assert audit["metrics"]["current_monthly_rent"] == 750.0, f"Expected 750.0 EUR, got {audit['metrics']['current_monthly_rent']}"
    assert audit["metrics"]["gross_yield"] == 4.15, f"Expected 4.15% yield, got {audit['metrics']['gross_yield']}"
    print("[OK] Deal #46 verified: 750 EUR cold rent and 4.15% yield correctly extracted!")

def test_deal_49_stated_rent():
    r = client.post("/api/properties/49/ai-audit?force_refresh=false")
    assert r.status_code == 200
    data = r.json()
    audit = data["ai_audit"]

    print("\n--- Test Deal #49 Stated Cold Rent Verification ---")
    print("Cold Rent:", audit["metrics"]["current_monthly_rent"], "EUR")
    print("Gross Yield:", audit["metrics"]["gross_yield"], "%")

    assert audit["metrics"]["current_monthly_rent"] == 493.0, f"Expected 493.0 EUR, got {audit['metrics']['current_monthly_rent']}"
    assert audit["metrics"]["gross_yield"] == 5.43, f"Expected 5.43% yield, got {audit['metrics']['gross_yield']}"
    print("[OK] Deal #49 verified: 493 EUR cold rent and 5.43% yield correctly extracted!")

def test_deal_68_unrented_benchmark_distinction():
    r = client.post("/api/properties/68/ai-audit?force_refresh=false")
    assert r.status_code == 200
    data = r.json()
    audit = data["ai_audit"]

    print("\n--- Test Deal #68 Unrented Benchmark Distinction ---")
    print("Is Rented:", audit["metrics"]["is_rented"])
    print("Rent Is Verified:", audit["metrics"]["rent_is_verified"])
    print("Actual Ist Rent:", audit["metrics"]["current_monthly_rent"], "EUR")
    print("Expected Soll Rent:", audit["metrics"]["expected_monthly_rent"], "EUR")
    print("Yield Type:", audit["metrics"]["yield_type"])

    assert audit["metrics"]["is_rented"] == False, "Deal #68 must be recognized as NOT rented (no tenant in exposé)!"
    assert audit["metrics"]["rent_is_verified"] == False, "Deal #68 must not claim a verified rent!"
    assert audit["metrics"]["current_monthly_rent"] == 0.0, "Deal #68 actual Ist-Miete must be 0.0 EUR!"
    assert audit["metrics"]["expected_monthly_rent"] > 600.0, "Deal #68 Soll-Miete benchmark must be populated!"
    assert audit["metrics"]["yield_type"] == "SOLL", "Deal #68 yield must be labeled as SOLL yield!"
    print("[OK] Deal #68 verified: Clearly separated unrented Ist-Miete (0 EUR) from Soll-Marktpotenzial!")

def test_deal_122_package_llm():
    r = client.post("/api/properties/122/ai-audit?force_refresh=false")
    assert r.status_code == 200
    data = r.json()
    audit = data["ai_audit"]
    updates = data["property_updates"]

    print("\n--- Test Deal #122 Package Deal LLM Verification ---")
    print("AI Provider:", audit.get("ai_provider"))
    print("Total Cold Rent:", audit["metrics"]["current_monthly_rent"], "EUR")
    print("Total Hausgeld:", audit["insights"]["hausgeld"], "EUR")
    print("City / Address:", updates.get("city"), "/", updates.get("address"))
    print("Parcel Area:", updates.get("parcel_area"))

    assert audit["insights"].get("is_package_deal") == True, "Deal #122 must be recognized as package deal!"
    assert audit["metrics"]["current_monthly_rent"] == 1030.0, f"Expected summed cold rent 1030.0 (350+680), got {audit['metrics']['current_monthly_rent']}"
    assert audit["insights"]["hausgeld"] == 1200.0, f"Expected summed hausgeld 1200.0 (500+700), got {audit['insights']['hausgeld']}"
    assert updates.get("city") == "Dortmund", f"Expected Dortmund city, got {updates.get('city')}"
    assert updates.get("parcel_area") == 0.0, f"Apartment package must not show fake 1000m² plot area, got {updates.get('parcel_area')}"
    print("[OK] Deal #122 verified: 1.030 EUR combined rent, 1.200 EUR combined Hausgeld, Dortmund city, and MEA plot cleanup!")

def test_deal_116_rented_without_rent_and_capex_provenance():
    r = client.post("/api/properties/116/ai-audit?force_refresh=false")
    assert r.status_code == 200
    data = r.json()
    audit = data["ai_audit"]
    updates = data["property_updates"]
    m = audit["metrics"]

    print("\n--- Test Deal #116 Rented Without Stated Rent & CaPEx Provenance ---")
    print("Is Rented:", m["is_rented"], "| Rent Verified:", m["rent_is_verified"])
    print("Actual Ist Rent:", m["current_monthly_rent"], "EUR | Yield Type:", m["yield_type"])
    print("CaPEx Items:", [(i["title"], i.get("source_type"), i.get("photo_indices")) for i in audit["capex"]["items"]])

    assert m["is_rented"] == True, "Deal #116 must remain flagged as rented!"
    assert m["rent_is_verified"] == False, "Deal #116 has no stated rent -> rent_is_verified must be False!"
    assert m["current_monthly_rent"] == 0.0, f"Expected current_monthly_rent=0.0 when cold rent missing, got {m['current_monthly_rent']}"
    assert updates["current_rent"] == 0.0, f"DB current_rent must be 0.0 (not benchmark), got {updates['current_rent']}"
    assert m["yield_type"] == "SOLL (Benchmark)", f"Expected 'SOLL (Benchmark)', got {m['yield_type']}"
    assert m["actual_gross_yield"] == 0.0, "Actual IST gross yield must be 0.0 when cold rent is missing!"
    assert any("kaltmiete" in r["title"].lower() or "ertragslage" in r["title"].lower() for r in audit["risks"]), "Missing-rent transparency risk must be flagged!"

    for item in audit["capex"]["items"]:
        assert item.get("source_type") in ("FOTO_BELEG", "EXPOSE_TEXT", "BAUJAHR_MUTMASSUNG"), f"Missing source_type on {item['title']}"
        assert item.get("evidence_source"), f"Missing evidence_source on {item['title']}"
    print("[OK] Deal #116 verified: SOLL yield only, DB current_rent=0, risk flagged, all CaPEx items have source provenance!")

if __name__ == "__main__":
    test_deal_44_private_and_cold_rent()
    test_deal_46_stated_rent()
    test_deal_49_stated_rent()
    test_deal_68_unrented_benchmark_distinction()
    test_deal_122_package_llm()
    test_deal_116_rented_without_rent_and_capex_provenance()
    print("\n=== ALL AI DUE DILIGENCE EXTRACTION & NEGATION TESTS PASSED ===")
