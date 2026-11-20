import sys
sys.path.insert(0, 'C:/Users/User/.gemini/antigravity/scratch/immometrica-clone')
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
r = client.get("/api/properties?sort_by=gross_yield&sort_order=desc")
data = r.json()
print("Total:", data["total"])
for p in data["properties"]:
    print(f"ID {p['id']}: lat={p['lat']} ({type(p['lat'])}), lng={p['lng']} ({type(p['lng'])}), yield={p['gross_yield']}")
