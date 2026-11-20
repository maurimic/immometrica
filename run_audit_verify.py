import urllib.request
import json

for pid in [173, 178]:
    url = f"http://127.0.0.1:8000/api/properties/{pid}/ai-audit?force_refresh=true"
    req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            loc = data.get("ai_audit", {}).get("location_analysis", {})
            print(f"\n================ Deal #{pid} ================")
            print("Display Address:", loc.get("display_address"))
            print("Is Exact:", loc.get("is_exact_address"))
            print("Badge:", loc.get("precision_badge"))
            print("Disclaimer:", loc.get("disclaimer"))
            print("Google Maps:", loc.get("maps", {}).get("direct_url"))
            print("Satellite:", loc.get("maps", {}).get("satellite_url"))
            print(f"Tier: {loc.get('location_tier')} | Score: {loc.get('location_score')}/10")
            print("Summary:", loc.get("summary"))
            print("POIs:")
            for p in loc.get("pois", []):
                print(f"  {p.get('icon')} {p.get('title')}: {p.get('distance_label')} ({p.get('time_label')}) -> {p.get('target')}")
    except Exception as e:
        print(f"Error auditing {pid}:", e)
