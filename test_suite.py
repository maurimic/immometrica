import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

def run():
    print("=== RUNNING SUITE TEST ===")
    r = client.get("/api/properties")
    data = r.json()
    props = data["properties"]
    print(f"Total properties: {len(props)}")
    
    # Verify all properties have valid distinct coordinates
    coords = set()
    for p in props:
        lat, lng = p.get("lat"), p.get("lng")
        assert lat is not None and lng is not None, f"Property {p['id']} missing coordinates!"
        coords.add((round(lat, 4), round(lng, 4)))
        safe_title = p['title'][:35].encode('ascii', 'replace').decode()
        print(f"  ID {p['id']}: {p['city']} ({lat}, {lng}) - {safe_title}")
    
    print(f"Distinct coordinates: {len(coords)} of {len(props)}")
    assert len(coords) == len(props), "All properties should have distinct coordinates!"

    # Verify Weiden deals (#109, #110) and geo_resolver are accurately placed in Weiden (never Amberg 49.44, 11.86)
    from app.geo_resolver import resolve_city_coordinates
    w_lat, w_lng = resolve_city_coordinates("Weiden in der Oberpfalz", "92637", allow_geocoder=False)
    assert w_lat >= 49.60 and w_lng >= 12.05, f"Weiden fallback must be near Weiden i.d.OPf, got {(w_lat, w_lng)}"
    weiden_deals = [p for p in props if p["id"] in (109, 110)]
    for wp in weiden_deals:
        assert wp["lat"] >= 49.60 and wp["lng"] >= 12.05, f"Weiden deal #{wp['id']} has wrong coords {(wp['lat'], wp['lng'])}"
    print("[OK] Weiden (#109, #110) & PLZ 92637 coordinates verified near Weiden (not Amberg)")

    # Test availability & AI audit on an active Deal
    test_pid = props[0]["id"]
    r_avail = client.post(f"/api/properties/{test_pid}/check-availability")
    assert r_avail.status_code == 200
    assert r_avail.json()["is_online"] == True
    print(f"[OK] Deal #{test_pid} availability check & live geo coords passed")

    r_audit = client.post(f"/api/properties/{test_pid}/ai-audit?force_refresh=false")
    assert r_audit.status_code == 200
    audit = r_audit.json()["ai_audit"]
    assert audit["score"] > 6.0
    print(f"[OK] Deal #{test_pid} AI audit passed: score {audit['score']}, rent {audit['metrics']['current_monthly_rent']}")

    # Test MCP tool execution on active deal
    r_mcp = client.post("/mcp/call", json={
        "name": "audit_property_ai",
        "arguments": {"property_id": test_pid}
    })
    assert r_mcp.status_code == 200
    assert r_mcp.json()["result"]["ai_audit"]["score"] == audit["score"]
    print("[OK] MCP audit_property_ai tool verified")

    print("\n>>> ALL TESTS IN SUITE PASSED SUCCESSFULLY! <<<")

if __name__ == "__main__":
    run()
