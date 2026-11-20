import sys
import json
from pathlib import Path

sys.path.insert(0, "C:/Users/User/.gemini/antigravity/scratch/immometrica-clone")
import mcp_stdio_server

print("=== SAARLOUIS SEARCH ===")
res_sls = mcp_stdio_server.search_deals(city="Saarlouis", online_only=True)
data_sls = json.loads(res_sls)
print(f"Treffer Saarlouis: {data_sls['count']}")
for d in data_sls['deals']:
    print(f"ID {d['id']}: {d['title']}")
    print(f"   Kaufpreis: {d['purchase_price']:,.0f} EUR | Kaltmiete: {d['current_rent']:.0f} EUR | Bruttorendite: {d['gross_yield']:.2f}% | Cashflow: {d['monthly_cashflow']:.2f} EUR/Mo")
    print(f"   Quelle: {d['source_portal']} | Online: {d['is_online']}")

print("\n=== NUERNBERG & UMGEBUNG SEARCH ===")
res_by = mcp_stdio_server.search_deals(state="Bayern", online_only=True)
data_by = json.loads(res_by)
print(f"Treffer Metropolregion Nürnberg / Bayern: {data_by['count']}")
for d in data_by['deals']:
    print(f"ID {d['id']} [{d['city']}]: {d['title']}")
    print(f"   Kaufpreis: {d['purchase_price']:,.0f} EUR | Kaltmiete: {d['current_rent']:.0f} EUR | Bruttorendite: {d['gross_yield']:.2f}% | Cashflow: {d['monthly_cashflow']:.2f} EUR/Mo")
    print(f"   Quelle: {d['source_portal']} | Online: {d['is_online']}")
