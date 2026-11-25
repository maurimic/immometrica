import csv
import io
from fastapi import APIRouter, Response
from typing import Any, Optional
from app.database import get_connection

router = APIRouter(prefix="/api/export", tags=["export"])


def _sanitize_csv_text(val: Any) -> str:
    """Normalizes None/newlines and neutralizes spreadsheet formula prefixes (=, +, -, @, Tab, CR)."""
    if val is None:
        return ""
    s = str(val).replace("\r", " ").replace("\n", " ")
    if s and s[0] in ("=", "+", "-", "@", "\t"):
        return "'" + s
    return s


@router.get("/csv")
def export_csv(
    state: Optional[str] = None,
    city: Optional[str] = None,
    online_only: Optional[bool] = None
):
    conn = get_connection()
    try:
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
    finally:
        conn.close()

    # Exact ImmoMetrica CSV header specification
    headers = [
        "Adresse", "Ort", "PLZ", "Titel", "Quelle", "Haustyp",
        "Preis", "Preis/m²", "Wfl.", "Zi", "Bj", "Zustand",
        "Miete", "Miete/m²", "ROI (s)", "Marktwert", "Marktwert/m²",
        "Marktwert %", "Cashflow/Monat", "Vermietet", "Zwangsversteigerung", "Link"
    ]

    output = io.StringIO()
    writer = csv.writer(output, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    writer.writerow(headers)

    for r in rows:
        d = dict(r)
        sqm = float(d.get("living_space") or 0.0)
        price = float(d.get("purchase_price") or 0.0)
        rent = float(d.get("current_rent") or d.get("expected_rent") or 0.0)
        price_sqm = round(price / sqm, 2) if sqm > 0 else 0.0
        rent_sqm = round(rent / sqm, 2) if sqm > 0 else 0.0
        rooms = float(d.get("rooms") or 0.0)
        year_built = d.get("year_built") or 0
        gross_yield = float(d.get("gross_yield") or 0.0)
        market_val = float(d.get("market_value") or 0.0)
        market_sqm = float(d.get("market_value_per_sqm") or 0.0)
        market_dev = float(d.get("market_deviation_percent") or 0.0)
        cashflow = float(d.get("monthly_cashflow") or 0.0)

        writer.writerow([
            _sanitize_csv_text(d.get("address")),
            _sanitize_csv_text(d.get("city")),
            _sanitize_csv_text(d.get("postal_code")),
            _sanitize_csv_text(d.get("title")),
            _sanitize_csv_text(d.get("source_portal")),
            _sanitize_csv_text(d.get("property_type")),
            f"{price:.2f}".replace(".", ","),
            f"{price_sqm:.2f}".replace(".", ","),
            f"{sqm:.2f}".replace(".", ","),
            f"{rooms}".replace(".", ","),
            f"{year_built}",
            _sanitize_csv_text(d.get("condition")),
            f"{rent:.2f}".replace(".", ","),
            f"{rent_sqm:.2f}".replace(".", ","),
            f"{gross_yield:.2f}%".replace(".", ","),
            f"{market_val:.2f}".replace(".", ","),
            f"{market_sqm:.2f}".replace(".", ","),
            f"{market_dev:.1f}%".replace(".", ","),
            f"{cashflow:.2f} €".replace(".", ","),
            "Ja" if d.get("is_rented") else "Nein",
            "Ja" if d.get("is_foreclosure") else "Nein",
            _sanitize_csv_text(d.get("portal_url")),
        ])

    # Add UTF-8 BOM so Excel opens German Umlauts seamlessly
    csv_content = "\ufeff" + output.getvalue()

    return Response(
        content=csv_content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": "attachment; filename=immometrica_export.csv"
        }
    )
