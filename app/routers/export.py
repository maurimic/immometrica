from fastapi import APIRouter, Response
from typing import Optional
from app.database import get_connection

router = APIRouter(prefix="/api/export", tags=["export"])

@router.get("/csv")
def export_csv(
    state: Optional[str] = None,
    city: Optional[str] = None,
    online_only: Optional[bool] = None
):
    conn = get_connection()
    cursor = conn.cursor()

    conditions = ["1=1"]
    params = []
    if state:
        conditions.append("state = ?")
        params.append(state)
    if city:
        conditions.append("city LIKE ?")
        params.append(f"%{city}%")
    if online_only:
        conditions.append("is_online = 1")

    query = f"""
    SELECT * FROM properties 
    WHERE {' AND '.join(conditions)}
    ORDER BY gross_yield DESC
    """
    cursor.execute(query, params)
    rows = cursor.fetchall()
    conn.close()

    # Exact ImmoMetrica CSV header specification
    headers = [
        "Adresse", "Ort", "PLZ", "Titel", "Quelle", "Haustyp", 
        "Preis", "Preis/m²", "Wfl.", "Zi", "Bj", "Zustand", 
        "Miete", "Miete/m²", "ROI (s)", "Marktwert", "Marktwert/m²", 
        "Marktwert %", "Cashflow/Monat", "Vermietet", "Zwangsversteigerung", "Link"
    ]

    csv_lines = [";".join(headers)]

    for r in rows:
        d = dict(r)
        sqm = d.get("living_space") or 1.0
        price = d.get("purchase_price") or 0.0
        rent = d.get("current_rent") or d.get("expected_rent") or 0.0
        price_sqm = round(price / sqm, 2)
        rent_sqm = round(rent / sqm, 2)

        line = [
            f'"{d.get("address", "")}"',
            f'"{d.get("city", "")}"',
            f'"{d.get("postal_code", "")}"',
            f'"{d.get("title", "").replace(";", " ")}"',
            f'"{d.get("source_portal", "")}"',
            f'"{d.get("property_type", "")}"',
            f'{price:.2f}'.replace('.', ','),
            f'{price_sqm:.2f}'.replace('.', ','),
            f'{sqm:.2f}'.replace('.', ','),
            f'{d.get("rooms", 0)}'.replace('.', ','),
            f'{d.get("year_built", 0)}',
            f'"{d.get("condition", "")}"',
            f'{rent:.2f}'.replace('.', ','),
            f'{rent_sqm:.2f}'.replace('.', ','),
            f'{d.get("gross_yield", 0):.2f}%'.replace('.', ','),
            f'{d.get("market_value", 0):.2f}'.replace('.', ','),
            f'{d.get("market_value_per_sqm", 0):.2f}'.replace('.', ','),
            f'{d.get("market_deviation_percent", 0):.1f}%'.replace('.', ','),
            f'{d.get("monthly_cashflow", 0):.2f} €'.replace('.', ','),
            "Ja" if d.get("is_rented") else "Nein",
            "Ja" if d.get("is_foreclosure") else "Nein",
            f'"{d.get("portal_url", "")}"'
        ]
        csv_lines.append(";".join(line))

    # Add UTF-8 BOM so Excel opens German Umlauts seamlessly
    bom = "\ufeff"
    csv_content = bom + "\n".join(csv_lines)

    return Response(
        content=csv_content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=immometrica_export.csv"
        }
    )
