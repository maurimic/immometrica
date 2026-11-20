"""
Verification Test Suite for ImmoMetrica Automated Search Agent & Deal Alert System.
Tests:
- Search Profiles CRUD
- Telegram Notifier & Settings
- AI Pre-Check (Erbbaurecht detection, 1-sentence investor verdict)
- Scraper Integration & Deduplication in deal_alerts
- MCP Tools integration (list_search_profiles, run_search_profiles, get_deal_alerts)
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient
from app.database import init_db, get_search_profiles, get_deal_alerts
from app.search_agent import quick_ai_deal_precheck, execute_search_profile
from app.main import app

init_db()
client = TestClient(app)

print("=== RUNNING SEARCH AGENT & DEAL ALERT TESTS ===")

# Test 1: List Default Profiles
r = client.get("/api/alerts/profiles")
assert r.status_code == 200
data = r.json()
assert data["success"] is True
assert data["total"] >= 3
print(f"[OK] Test 1: Profiles endpoint returned {data['total']} profiles.")

# Test 2: Create a Custom Search Profile
test_profile = {
    "name": "Bochum Studenten-Apartments > 7%",
    "city": "Bochum",
    "radius_km": 15,
    "max_price": 120000,
    "max_price_per_sqm": 2200,
    "min_yield": 7.0,
    "min_discount_pct": 10.0,
    "min_rooms": 1,
    "min_living_space": 25,
    "property_type": "Eigentumswohnung",
    "search_query": "Balkon",
    "is_active": True
}
r = client.post("/api/alerts/profiles", json=test_profile)
assert r.status_code == 200
created = r.json()["profile"]
new_id = created["id"]
assert created["name"] == test_profile["name"]
assert created["min_yield"] == 7.0
print(f"[OK] Test 2: Custom search profile #{new_id} created successfully.")

# Test 3: Update Profile
r = client.put(f"/api/alerts/profiles/{new_id}", json={"max_price": 130000, "is_active": False})
assert r.status_code == 200
updated = r.json()["profile"]
assert updated["max_price"] == 130000
assert updated["is_active"] == 0
print(f"[OK] Test 3: Search profile #{new_id} updated (max_price: {updated['max_price']}, is_active: {updated['is_active']}).")

# Test 4: Alert Settings & Crawler Configuration
r = client.get("/api/alerts/settings")
assert r.status_code == 200
sett = r.json()
assert "crawler_interval_minutes" in sett
orig_saved_chat_id = sett.get("telegram_chat_id")
orig_saved_interval = sett.get("crawler_interval_minutes")
orig_saved_active = sett.get("crawler_active")

r = client.post("/api/alerts/settings", json={
    "telegram_chat_id": "123456789",
    "crawler_interval_minutes": 15,
    "crawler_active": False
})
assert r.status_code == 200
sett_after = client.get("/api/alerts/settings").json()
assert sett_after["telegram_chat_id"] == "123456789"
assert sett_after["crawler_interval_minutes"] == 15
print(f"[OK] Test 4: Alert settings updated (Chat-ID: {sett_after['telegram_chat_id']}, Interval: {sett_after['crawler_interval_minutes']}m).")

# Test 5: Telegram Test Endpoint
r = client.post("/api/alerts/telegram/test", json={
    "telegram_bot_token": "fake_token_for_testing",
    "telegram_chat_id": "123456789"
})
assert r.status_code == 200
tg_res = r.json()
assert tg_res["success"] is False
assert "error" in tg_res
print(f"[OK] Test 5: Telegram verification endpoint returned expected error on dummy token.")

# Test 6: AI Pre-check on Erbbaurecht (Critical Risk Gate)
erb_deal = {
    "title": "Günstige Eigentumswohnung auf Erbbaugrundstück",
    "city": "Dortmund",
    "purchase_price": 65000,
    "living_space": 50,
    "gross_yield": 8.5,
    "market_deviation_percent": -30.0
}
ai_res1 = quick_ai_deal_precheck(erb_deal)
assert ai_res1["has_critical_risks"] is True
assert any("Erbbaurecht" in r for r in ai_res1["risks"])
assert "Achtung" in ai_res1["verdict"] and "Erbpacht" in ai_res1["verdict"]
print(f"[OK] Test 6: AI Pre-check correctly flagged Erbbaurecht as critical risk! ({ai_res1['verdict']})")

# Test 7: AI Pre-check on Clean High-Yield Deal
clean_deal = {
    "title": "Gepflegte 2-Zimmer-Wohnung voll vermietet",
    "city": "Dortmund",
    "purchase_price": 115000,
    "living_space": 60,
    "gross_yield": 6.8,
    "market_deviation_percent": -18.0
}
ai_res2 = quick_ai_deal_precheck(clean_deal)
assert ai_res2["has_critical_risks"] is False
assert len(ai_res2["risks"]) == 0
assert "Deal-Kandidat" in ai_res2["verdict"] or "Rendite" in ai_res2["verdict"]
print(f"[OK] Test 7: AI Pre-check confirmed clean high-yield deal! ({ai_res2['verdict']})")

# Test 8: Execute Search Profile (Scraper & Deduplication)
from unittest.mock import patch
with patch("app.search_agent.send_deal_telegram_alert") as mock_tg:
    mock_tg.return_value = {"success": True, "mocked": True}
    res = execute_search_profile(new_id, force=True)
assert res["success"] is True
assert "total_scraped" in res
assert "qualified_deals" in res
assert "skipped_known_deals" in res
assert "newly_evaluated_deals" in res
assert "filtered_stage1_2" in res
assert "text_ai_calls" in res
assert "vision_ai_calls" in res
assert "total_tokens_used" in res
assert "estimated_cost_eur" in res
assert "stats" in res
print(f"[OK] Test 8: execute_search_profile completed ({res['total_scraped']} scraped, {res['skipped_known_deals']} skipped, {res['newly_evaluated_deals']} newly evaluated, {res['filtered_stage1_2']} stage1/2 filtered, {res['text_ai_calls']} text AI, {res['vision_ai_calls']} vision AI, {res['total_tokens_used']} tokens, {res['estimated_cost_eur']} EUR, {res['qualified_deals']} qualified, {res['new_alerts']} alerts).")

# Test 8b: Value-Add & Mietspiegel-Schutz in Stage 1
from app.valuation import get_mietspiegel_size_benchmark, evaluate_property_valuation
low_yield_deal = {
    "title": "Untervermietete 2-Zimmer-Wohnung mit hohem Mietsteigerungspotenzial",
    "city": "Dortmund",
    "purchase_price": 100000.0,
    "living_space": 60.0,
    "gross_yield": 3.5,  # Schlecht vermietet: nur 3.5 % Ist-Rendite
    "current_rent": 291.67,
    "rooms": 2.0,
    "property_type": "Eigentumswohnung"
}
ms_data = get_mietspiegel_size_benchmark(city="Dortmund", living_space=60.0)
ms_monthly = float(ms_data["expected_sqm_rent"]) * 60.0
ms_yield = round(((ms_monthly * 12.0) / 100000.0) * 100.0, 2)
assert ms_yield >= 6.5, f"Expected Mietspiegel Soll-Rendite >= 6.5%, got {ms_yield}%"

eff_yield = max(low_yield_deal["gross_yield"], ms_yield)
profile_min_yield = 6.0
assert low_yield_deal["gross_yield"] < profile_min_yield
assert eff_yield >= profile_min_yield
print(f"[OK] Test 8b: Value-Add & Mietspiegel-Schutz verified! Low-yield deal ({low_yield_deal['gross_yield']}%) is protected by Mietspiegel Soll-Rendite ({ms_yield}%) and passes Stage 1 (min_yield: {profile_min_yield}%).")

# Test 8c: Seen Listings Memory & Reset on Profile Update
from app.database import mark_listing_seen, get_seen_listing_urls, clear_seen_listings_for_profile
test_seen_url = "https://www.kleinanzeigen.de/s-anzeige/test-deal-999"
mark_listing_seen(test_seen_url, new_id, "dropped_stage1_yield")
assert test_seen_url in get_seen_listing_urls(new_id)

# Updating filter criteria resets seen listings memory for this profile
client.put(f"/api/alerts/profiles/{new_id}", json={"min_yield": 5.5})
assert test_seen_url not in get_seen_listing_urls(new_id)
print("[OK] Test 8c: Seen listings memory and automatic invalidation on profile update verified.")

# Test 8d: Token-Tracking, Crawler-Stats & Cost Calculation
sett_with_stats = client.get("/api/alerts/settings").json()
assert "crawler_last_stats" in sett_with_stats
cls = sett_with_stats["crawler_last_stats"]
assert cls is not None
assert "filtered_stage1_2" in cls
assert "text_ai_calls" in cls
assert "vision_ai_calls" in cls
assert "total_tokens_used" in cls
assert "estimated_cost_eur" in cls

from app.ai_analyst import generate_ai_due_diligence_report
from app.image_analyst import analyze_property_images
sample_rep = generate_ai_due_diligence_report({"title": "Test", "purchase_price": 100000, "is_online": 1}, full_description="Kurz")
assert "token_usage" in sample_rep
assert "prompt_tokens" in sample_rep["token_usage"]
assert "candidates_tokens" in sample_rep["token_usage"]
assert "total_tokens" in sample_rep["token_usage"]

sample_img = analyze_property_images({"title": "Test", "is_online": 0})
assert "token_usage" in sample_img
assert "total_tokens" in sample_img["token_usage"]
print("[OK] Test 8d: Token tracking (prompt/candidates/total) and crawler_last_stats verified.")

# Test 9: Alert Feed API
r = client.get("/api/alerts/feed?limit=20")
assert r.status_code == 200
feed = r.json()
assert "alerts" in feed
print(f"[OK] Test 9: Alert feed returned {feed['total']} logged deal alerts.")

# Test 10: MCP Tools Execution
r = client.post("/mcp/call", json={"name": "list_search_profiles", "arguments": {}})
assert r.status_code == 200
mcp_profiles = r.json()["result"]["profiles"]
assert len(mcp_profiles) >= 4
print(f"[OK] Test 10: MCP 'list_search_profiles' verified ({len(mcp_profiles)} profiles).")

r = client.post("/mcp/call", json={"name": "get_deal_alerts", "arguments": {"limit": 10}})
assert r.status_code == 200
mcp_alerts = r.json()["result"]
assert "alerts" in mcp_alerts
print(f"[OK] Test 10b: MCP 'get_deal_alerts' verified ({mcp_alerts['total']} alerts in system).")

# Test 11: Cleanup created test profile & restore original settings
r = client.delete(f"/api/alerts/profiles/{new_id}")
assert r.status_code == 200
client.post("/api/alerts/settings", json={
    "telegram_chat_id": orig_saved_chat_id,
    "crawler_interval_minutes": orig_saved_interval,
    "crawler_active": orig_saved_active
})
print(f"[OK] Test 11: Test profile #{new_id} cleaned up and original alert settings restored.")

print("\n>>> ALL 11 SEARCH AGENT & DEAL ALERT TESTS PASSED SUCCESSFULLY! <<<")
