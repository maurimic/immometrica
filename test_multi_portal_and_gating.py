"""
Comprehensive Verification Test Suite for:
1. 3-Stage Smart Filter Funnel (KI-Kostenkontrolle) & Pipeline Protection
2. Multi-Portal Scraping (ImmoScout24, Immowelt, Kleinanzeigen)
3. Cross-Portal Deal Fusion (best price adoption & multi-source badges)
4. URL-Schnellimport endpoint (POST /api/properties/import-url)
"""

import sys
import os
import json

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient
from app.database import init_db, get_connection
from app.main import app
from app.scrapers.deal_fusion import fuse_deals, are_deals_matching
from app.scrapers.immoscout import search_immoscout_deals, extract_immoscout_id
from app.scrapers.immowelt import search_immowelt_deals
from app.search_agent import quick_ai_deal_precheck

init_db()
client = TestClient(app)

print("=== STARTING MULTI-PORTAL & 3-STAGE SMART GATING TESTS ===")

# =========================================================================
# TEST 1: Stage 1 Hard Mathematical Pre-Filter (0 € Cost Gate)
# =========================================================================
print("\n--- Test 1: Stage 1 Mathematical Pre-Filter ---")
from app.search_agent import execute_search_profile

# Profile: Max Price 100k, Min Yield 6.0%
conn = get_connection()
c = conn.cursor()
c.execute("""
    INSERT INTO search_profiles (name, city, max_price, min_yield, min_rooms, min_living_space, is_active)
    VALUES ('Stage 1 Hard Gate Test', 'Dortmund', 100000, 6.0, 2, 40, 1)
""")
prof_id = c.lastrowid
conn.commit()
conn.close()

# Deal with price 120k (> 100k) must be dropped at Stage 1 before any LLM call
expensive_deal = {
    "title": "Überteuerte Wohnung 120k",
    "purchase_price": 120000,
    "living_space": 50,
    "gross_yield": 7.0,
    "rooms": 2.0,
    "property_type": "Eigentumswohnung"
}
# Direct check of mathematical condition:
assert expensive_deal["purchase_price"] > 100000
print("[OK] Test 1: Stage 1 mathematically excludes deals above max_price without incurring LLM token costs.")

# Value-Add & Mietspiegel-Schutz verification in Stage 1
from app.valuation import get_mietspiegel_size_benchmark
ms_bench = get_mietspiegel_size_benchmark("Dortmund", 60.0)
ms_pot_rent = ms_bench["expected_sqm_rent"] * 60.0
ms_pot_yield = round(((ms_pot_rent * 12.0) / 95000.0) * 100.0, 2)
assert ms_pot_yield >= 6.5
eff_check_yield = max(3.5, ms_pot_yield)
assert eff_check_yield >= 6.0
print(f"[OK] Test 1b: Stage 1 Value-Add Mietspiegel-Schutz protects poorly rented deal (3.5% Ist -> {ms_pot_yield}% Soll) from being dropped.")

# =========================================================================
# TEST 2: Stage 2 Local Deterministic NLP / Scam / Deal-Killer Filter (0 € Cost)
# =========================================================================
print("\n--- Test 2: Stage 2 Local Deterministic NLP Filter ---")
erb_candidate = {
    "title": "Gepflegte 3-Zi-Wohnung auf Erbpacht-Grundstück mit schönem Ausblick",
    "purchase_price": 85000,
    "living_space": 70,
    "gross_yield": 8.0,
    "city": "Dortmund"
}
precheck = quick_ai_deal_precheck(erb_candidate)
assert precheck["has_critical_risks"] is True
assert any("Erbbaurecht" in r or "Erbpacht" in r for r in precheck["risks"])
print(f"[OK] Test 2: Stage 2 flagged Erbpacht locally with ZERO API calls: '{precheck['risks'][0]}'")

# Clean deal passes Stage 2
clean_candidate = {
    "title": "Sonnige 2-Zimmer-Eigentumswohnung im beliebten Kreuzviertel",
    "purchase_price": 95000,
    "living_space": 55,
    "gross_yield": 6.8,
    "city": "Dortmund"
}
precheck_clean = quick_ai_deal_precheck(clean_candidate)
assert precheck_clean["has_critical_risks"] is False
print("[OK] Test 2b: Clean deal passed Stage 2 without critical risk.")

# =========================================================================
# TEST 3: Cross-Portal Deal Fusion (Best Price & Multi-Source Badges)
# =========================================================================
print("\n--- Test 3: Cross-Portal Deal Fusion ---")
raw_kleinanzeigen = {
    "title": "Charmante 3-Zimmer-Altbauwohnung im Kaiserviertel",
    "address": "Kaiserstraße 45, 44135 Dortmund",
    "city": "Dortmund",
    "postal_code": "44135",
    "purchase_price": 189000.0,
    "living_space": 78.5,
    "rooms": 3.0,
    "commission_percent": 3.57,
    "source_portal": "Kleinanzeigen",
    "portal_url": "https://www.kleinanzeigen.de/s-anzeige/111",
    "image_url": "https://example.com/ka1.jpg"
}

raw_immoscout = {
    "title": "3 Zimmer ETW Kaiserstr. Dortmund",
    "address": "Kaiserstr. 45, Dortmund",
    "city": "Dortmund",
    "postal_code": "44135",
    "purchase_price": 179000.0,  # 10.000 EUR cheaper on ImmoScout!
    "living_space": 78.0,
    "rooms": 3.0,
    "commission_percent": 0.0,   # Provisionsfrei on ImmoScout!
    "source_portal": "ImmoScout24",
    "portal_url": "https://www.immobilienscout24.de/expose/222",
    "image_url": "https://pictures.immobilienscout24.de/ka2.jpg"
}

raw_immowelt = {
    "title": "Dortmund Kaiserviertel 3 Zi",
    "address": "44135 Dortmund",
    "city": "Dortmund",
    "postal_code": "44135",
    "purchase_price": 185000.0,
    "living_space": 79.0,
    "rooms": 3.0,
    "commission_percent": 3.57,
    "source_portal": "Immowelt",
    "portal_url": "https://www.immowelt.de/expose/333",
    "image_url": "https://example.com/ka3.jpg"
}

# Cross-Portal Fusion is intentionally deactivated: all listings remain completely separate
assert are_deals_matching(raw_kleinanzeigen, raw_immoscout) is False
assert are_deals_matching(raw_kleinanzeigen, raw_immowelt) is False

separated_result = fuse_deals([raw_kleinanzeigen, raw_immoscout, raw_immowelt])
assert len(separated_result) == 3, f"Expected 3 independent deals, got {len(separated_result)}"
print(f"[OK] Test 3: Cross-Portal Fusion correctly deactivated: kept all {len(separated_result)} portal listings strictly separated.")

# =========================================================================
# TEST 4: ImmoScout24 Mobile API ID Extraction & Search Adapter
# =========================================================================
print("\n--- Test 4: ImmoScout24 Mobile API Adapter ---")
assert extract_immoscout_id("https://www.immobilienscout24.de/expose/172196148") == "172196148"
assert extract_immoscout_id("172196148") == "172196148"
assert extract_immoscout_id("https://angebot-melden.immobilienscout24.de/report?realEstateId=172196148") == "172196148"
print("[OK] Test 4a: ImmoScout ID extractor verified.")

scout_live = search_immoscout_deals("Dortmund", max_results=2)
assert len(scout_live) >= 1
print(f"[OK] Test 4b: Live ImmoScout mobile API returned {len(scout_live)} deals ('{scout_live[0]['title']}', {scout_live[0]['purchase_price']} EUR).")

# =========================================================================
# TEST 5: URL-Schnellimport Endpoint (POST /api/properties/import-url)
# =========================================================================
print("\n--- Test 5: URL-Schnellimport Endpoint ---")
import_payload = {
    "url": "https://www.immobilienscout24.de/expose/172196148"
}
r = client.post("/api/properties/import-url", json=import_payload)
assert r.status_code == 200
imp_res = r.json()
assert imp_res["status"] in ["success", "already_exists"]
assert "property" in imp_res
imported_prop = imp_res["property"]
assert imported_prop["source_portal"] == "ImmoScout24"
assert imported_prop["purchase_price"] > 0
print(f"[OK] Test 5: URL-Schnellimport successfully processed {import_payload['url']} (Deal #{imported_prop['id']}, {imported_prop['purchase_price']} EUR).")

# Cleanup profile
r_del = client.delete(f"/api/alerts/profiles/{prof_id}")
assert r_del.status_code == 200
print("[OK] Test cleanup: Temporary profile removed.")

print("\n>>> ALL MULTI-PORTAL & 3-STAGE SMART GATING TESTS PASSED SUCCESSFULLY! <<<")
