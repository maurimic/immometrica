import sys
sys.path.insert(0, "C:/Users/User/.gemini/antigravity/scratch/immometrica-clone")
from app.availability import check_portal_url_live

urls = [
    ("https://www.kleinanzeigen.de/s-wohnung-kaufen/saarlouis/c196l385", "Kleinanzeigen", "Saarlouis"),
    ("https://www.kleinanzeigen.de/s-anzeige/kapitalanlage-saarlouis-roden-2-zimmer/2891234567-196-385", "Kleinanzeigen", "Saarlouis"),
    ("https://www.immowelt.de/expose/saarlouis-innenstadt-apartment-29831", "Immowelt", "Saarlouis"),
    ("https://www.immobilienscout24.de/expose/149827361", "ImmoScout24", "Saarlouis"),
    ("https://www.zvg-portal.de/", "ZVG", "Magdeburg")
]

for url, portal, city in urls:
    res = check_portal_url_live(url, portal, city)
    print(f"URL: {url} -> Online: {res['is_online']} | Code: {res['status_code']} | Msg: {res['status_message']}")
