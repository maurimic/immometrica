import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient
from app.database import init_db
from app.main import app

# Ensure DB initialized with new schema
init_db()

client = TestClient(app)

# Test 0: Verify Authentication Barrier (Login Page & 401 for unauthenticated calls)
unauth_client = TestClient(app)
r_unauth = unauth_client.get("/")
assert r_unauth.status_code == 200
assert "Anmelden" in r_unauth.text
print("[OK] Auth barrier verified: Unauthenticated users receive login page")

r_api_unauth = unauth_client.get("/api/properties")
assert r_api_unauth.status_code == 401
print("[OK] Protected API verified: Unauthenticated API calls blocked with 401")

# Authenticate main Test Client session
login_res = client.post("/api/auth/login", json={"username": "admin", "password": "localtest123"})
assert login_res.status_code == 200, f"Login failed: {login_res.text}"
print(f"[OK] Auth login: OK (Angemeldet als {login_res.json()['username']})")

# Test 1: Root HTML (Authenticated)
r = client.get("/")
assert r.status_code == 200
print("[OK] Root index.html: OK")

# Test 2: Properties Search & Online filter
r = client.get("/api/properties?online_only=true")
assert r.status_code == 200
data = r.json()
assert data["total"] >= 1, f"Expected at least 1 online deal, got {data['total']}"
print(f"[OK] Properties endpoint: {data['total']} active online deals found with online_only=true")

# Test 3: Geo Resolver (Automatic Bundesland & Grunderwerbsteuer from PLZ)
r = client.get("/api/calculator/resolve-geo?postal_code=45127&city=Essen")
assert r.status_code == 200
geo = r.json()
assert geo["state"] == "Nordrhein-Westfalen"
assert geo["tax_rate"] == 6.5
print(f"[OK] Geo Resolver: 45127 Essen -> {geo['state']} ({geo['tax_rate']}% GrESt) automatically resolved")

r2 = client.get("/api/calculator/resolve-geo?postal_code=80331&city=München")
assert r2.status_code == 200
geo2 = r2.json()
assert geo2["state"] == "Bayern"
assert geo2["tax_rate"] == 3.5
print(f"[OK] Geo Resolver: 80331 München -> {geo2['state']} ({geo2['tax_rate']}% GrESt) automatically resolved")

# Test 4: Cashflow Calculator with automatic location derivation
r = client.post("/api/calculator/calculate", json={
    "price": 250000,
    "rent": 1200,
    "postal_code": "04315",
    "city": "Leipzig",
    "equity_value": 20
})
assert r.status_code == 200
cf = r.json()
assert cf["state"] == "Sachsen"
assert cf["transfer_tax_rate"] == 5.5
print(f"[OK] Calculator Auto-Location: Leipzig -> {cf['state']} ({cf['transfer_tax_rate']}% GrESt), Cashflow: {cf['monthly_cashflow']} EUR")

# Test 5: Live Availability Check for an active Deal
all_active = client.get("/api/properties?online_only=true").json()["properties"]
active_with_desc = [p for p in all_active if len(p.get("description") or "") > 200]
test_prop = next((p for p in all_active if p.get("id") == 258), None) or (active_with_desc[0] if active_with_desc else all_active[0])
test_prop_id = test_prop["id"]
r = client.post(f"/api/properties/{test_prop_id}/check-availability")
assert r.status_code == 200
status = r.json()
assert status["is_online"] == True, f"Deal #{test_prop_id} should be online, got: {status}"
print(f"[OK] Live Availability Checker: Deal #{test_prop_id} correctly verified as ONLINE ({status['status_message']})")

# Test 6: AI Due Diligence Audit on active Deal (Reads full description, detects WEG risk & CaPEx)
r = client.post(f"/api/properties/{test_prop_id}/ai-audit?force_refresh=false")
assert r.status_code == 200
audit_res = r.json()
assert "ai_audit" in audit_res
audit = audit_res["ai_audit"]
assert "score" in audit
assert "risks" in audit
assert isinstance(audit["capex"]["total_capex"], (int, float)) and audit["capex"]["total_capex"] >= 0
print(f"[OK] AI Due Diligence Audit on Deal #{test_prop_id}: Score {audit['score']}/10, {len(audit['risks'])} risks detected, CaPEx: {audit['capex']['total_capex']} EUR, Counter-offer: {audit['negotiation_target_price']} EUR")

# Test 7: Kanban Pipeline
r = client.get("/api/pipeline/board")
assert r.status_code == 200
board = r.json()
assert len(board) > 0
print(f"[OK] Pipeline board endpoint: OK ({len(board)} stages)")

# Test 8: CSV Export
r = client.get("/api/export/csv")
assert r.status_code == 200
assert "Adresse;Ort;PLZ" in r.text
print("[OK] CSV Export endpoint: OK")

# Test 9: MCP Tools list & MCP Call
r = client.get("/mcp/tools")
assert r.status_code == 200
tools = r.json()["tools"]
assert any(t["name"] == "audit_property_ai" for t in tools), "audit_property_ai MCP tool should be registered"
print(f"[OK] MCP Tools endpoint: OK ({len(tools)} tools registered, including audit_property_ai)")

# Test 10: Call MCP audit_property_ai tool
r = client.post("/mcp/call", json={
    "name": "audit_property_ai",
    "arguments": {"property_id": test_prop_id}
})
assert r.status_code == 200
mcp_audit = r.json()["result"]["ai_audit"]
assert mcp_audit["score"] == audit["score"]
print(f"[OK] MCP Tool execution 'audit_property_ai': Verified result for Deal #{test_prop_id}")

# Test 11: AI Image Analysis endpoint
from unittest.mock import patch
from app.database import get_connection

# Backup original property state to prevent mock contamination in production database
_conn = get_connection()
_cur = _conn.cursor()
_orig_row = _cur.execute("SELECT image_analysis, ai_analysis FROM properties WHERE id = ?", (test_prop_id,)).fetchone()
_orig_img_analysis = _orig_row[0] if _orig_row else None
_orig_ai_analysis = _orig_row[1] if _orig_row else None
_conn.close()

mock_gemini_vision = {
    'ai_provider': 'OneProvider claude-haiku-5-5 Vision (Test Mock)',
    'error': False,
    'overall_visual_score': 7.5,
    'condition_rating': 'GEPFLEGT',
    'condition_label': '🟢 Gepflegter Zustand',
    'has_interior_photos': True,
    'categories': {
        'bathroom': {'score': 7.0, 'status': 'gut', 'findings': [], 'cost': 0.0},
        'kitchen': {'score': 8.0, 'status': 'gut', 'findings': [], 'cost': 0.0},
        'flooring': {'score': 7.5, 'status': 'gut', 'findings': [], 'cost': 0.0},
        'windows': {'score': 7.5, 'status': 'gut', 'findings': [], 'cost': 0.0},
        'heating_electrics': {'score': 7.0, 'status': 'gut', 'findings': [], 'cost': 0.0},
        'walls_substance': {'score': 7.5, 'status': 'gut', 'findings': [], 'cost': 0.0}
    },
    'photo_classifications': [
        {'index': 1, 'tag': '🛋️ Wohnbereich / Wohnzimmer', 'description': 'Helles Wohnzimmer mit Holzdecke.', 'quality_score': 8.0}
    ],
    'detected_findings': [],
    'visual_capex_items': [],
    'total_visual_capex': 0.0,
    'token_usage': {'prompt_tokens': 100, 'candidates_tokens': 50, 'total_tokens': 150}
}

try:
    with patch('app.image_analyst.call_gemini_vision', return_value=mock_gemini_vision):
        r = client.post(f"/api/properties/{test_prop_id}/analyze-images")
    assert r.status_code == 200
    img_data = r.json()
    assert "image_analysis" in img_data
    img_analysis = img_data["image_analysis"]
    assert "overall_visual_score" in img_analysis
    assert "categories" in img_analysis
    assert "bathroom" in img_analysis["categories"]
    assert "total_visual_capex" in img_analysis
    assert img_analysis["overall_visual_score"] > 0
    print(f"[OK] AI Image Analysis on Deal #{test_prop_id}: Score {img_analysis['overall_visual_score']}/10, Condition: {img_analysis['condition_rating']}, CaPEx: {img_analysis['total_visual_capex']} EUR")

    # Test 12: Call MCP analyze_property_images tool
    r = client.post("/mcp/call", json={
        "name": "analyze_property_images",
        "arguments": {"property_id": test_prop_id}
    })
    assert r.status_code == 200
    mcp_img = r.json()["result"]
    assert mcp_img["overall_visual_score"] == img_analysis["overall_visual_score"]
    print(f"[OK] MCP Tool execution 'analyze_property_images': Verified result for Deal #{test_prop_id}")
finally:
    # Always restore original database records after testing
    if _orig_row:
        _conn = get_connection()
        _cur = _conn.cursor()
        _cur.execute("UPDATE properties SET image_analysis = ?, ai_analysis = ? WHERE id = ?", (_orig_img_analysis, _orig_ai_analysis, test_prop_id))
        _conn.commit()
        _conn.close()

# Test 13: MCP generate_purchase_offer pre-LOI Document Request E-Mail on active deal
r = client.post("/mcp/call", json={
    "name": "generate_purchase_offer",
    "arguments": {"property_id": test_prop_id}
})
assert r.status_code == 200
offer_res = r.json()["result"]
assert "document_request_subject" in offer_res and "Objektunterlagen" in offer_res["document_request_subject"]
assert "document_request_email" in offer_res
assert "letter_of_intent_text" in offer_res and len(offer_res["letter_of_intent_text"]) > 100
print(f"[OK] MCP 'generate_purchase_offer' (Deal #{test_prop_id}): Auto-generated pre-LOI document request ({len(offer_res['missing_documents'])} docs) + LOI verified")

print("\n=== ALL 13 VERIFICATION TESTS PASSED SUCCESSFULLY ===")
