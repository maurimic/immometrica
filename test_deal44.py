import sqlite3
import json
import sys

sys.stdout.reconfigure(encoding="utf-8")
conn = sqlite3.connect("C:/Users/User/.gemini/antigravity/scratch/immometrica-clone/app/immometrica.db")
cursor = conn.cursor()
cursor.execute("SELECT id, title, purchase_price, current_rent, gross_yield, is_private, commission_percent, ai_analysis FROM properties WHERE id = 44")
row = cursor.fetchone()
print(f"Deal #{row[0]}: {row[1]}")
print(f"Price: {row[2]} € | Cold Rent: {row[3]} € | Yield: {row[4]}% | Private: {row[5]} | Commission: {row[6]}%")

if row[7]:
    analysis = json.loads(row[7])
    print("\n=== RISKS & ADVANTAGES ===")
    for r in analysis.get("risks", []):
        print(f"[{r.get('severity')}] {r.get('title')} ({r.get('badge')})")
        print(f"  Description: {r.get('description')}")
    print("\n=== EXECUTIVE SUMMARY ===")
    print(analysis.get("executive_summary"))
