import sys
from pathlib import Path
sys.path.insert(0, "C:/Users/User/.gemini/antigravity/scratch/immometrica-clone")
from app.database import get_connection

conn = get_connection()
cursor = conn.cursor()
cursor.execute("SELECT id, title, city, purchase_price, gross_yield, is_online, availability_status, portal_url FROM properties")
rows = cursor.fetchall()
print(f"Total properties: {len(rows)}")
online_count = sum(1 for r in rows if r["is_online"] == 1)
print(f"Online properties (is_online=1): {online_count}")
for r in rows:
    print(f"ID {r['id']} [{r['city']}]: is_online={r['is_online']} | {r['title'][:40]} | URL: {r['portal_url'][:50]}")
conn.close()
