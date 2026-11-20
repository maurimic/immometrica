#!/usr/bin/env python3
"""
Model Context Protocol (MCP) Stdio Server for ImmoMetrica.
Allows AI assistants (Antigravity, Claude, Cursor) to search real estate deals,
calculate cashflows with automatic Grunderwerbsteuer resolution by PLZ/state,
verify listing availability online, and query German city benchmarks.
"""

import sys
import os
import json
from pathlib import Path
from typing import Optional, Dict, Any, List

# Ensure the clone root directory is on the python path
CLONE_ROOT = Path(__file__).resolve().parent
if str(CLONE_ROOT) not in sys.path:
    sys.path.insert(0, str(CLONE_ROOT))

from mcp.server.mcpserver import MCPServer
from app.database import get_connection
from app.calculator import calculate_cashflow as calc_engine
from app.valuation import evaluate_property_valuation, get_city_benchmark, CITY_BENCHMARKS
from app.availability import check_portal_url_live
from app.geo_resolver import resolve_location_and_tax, GRUNDERWERBSTEUER_DE

# Initialize MCP Server instance
server = MCPServer(
    name="immometrica",
    instructions="ImmoMetrica Real Estate Investment & Deal Finder MCP Server. "
                 "Provides live deal search, automated cashflow calculation with automatic "
                 "Grunderwerbsteuer derivation from postal codes, online availability checking, "
                 "and city market valuation benchmarks across Germany."
)

@server.tool(
    name="search_deals",
    description="Search real estate investment deals in Germany across portals (ImmoScout24, Immowelt, Kleinanzeigen, ZVG). "
                "Filters by city, state, min gross rental yield, max purchase price, positive cashflow, "
                "undervalued properties (below market value), and verified active online status."
)
def search_deals(
    city: str = "",
    state: str = "",
    property_type: str = "",
    min_yield: float = 0.0,
    max_price: float = 0.0,
    min_price: float = 0.0,
    positive_cashflow_only: bool = False,
    undervalued_only: bool = False,
    online_only: bool = True,
    limit: int = 15
) -> str:
    """
    Searches properties matching criteria.
    Args:
        city: City name (e.g. 'Leipzig', 'Essen', 'Duisburg', 'Berlin')
        state: Federal state (e.g. 'Nordrhein-Westfalen', 'Sachsen')
        property_type: 'Eigentumswohnung', 'Mehrfamilienhaus', or 'Einfamilienhaus'
        min_yield: Minimum gross yield in percent (e.g. 5.5 for >= 5.5%)
        max_price: Maximum purchase price in EUR
        min_price: Minimum purchase price in EUR
        positive_cashflow_only: True to return only properties with net positive monthly cashflow
        undervalued_only: True to return only properties priced at least 5% below market valuation
        online_only: True to return only listings verified as currently active online (not 404/deleted)
        limit: Maximum number of properties to return (default 15)
    """
    conn = get_connection()
    cursor = conn.cursor()

    conditions = ["1=1"]
    params = []

    if city.strip():
        conditions.append("city LIKE ?")
        params.append(f"%{city.strip()}%")

    if state.strip():
        conditions.append("state LIKE ?")
        params.append(f"%{state.strip()}%")

    if property_type.strip():
        conditions.append("property_type LIKE ?")
        params.append(f"%{property_type.strip()}%")

    if min_yield > 0:
        conditions.append("gross_yield >= ?")
        params.append(min_yield)

    if max_price > 0:
        conditions.append("purchase_price <= ?")
        params.append(max_price)

    if min_price > 0:
        conditions.append("purchase_price >= ?")
        params.append(min_price)

    if positive_cashflow_only:
        conditions.append("monthly_cashflow > 0")

    if undervalued_only:
        conditions.append("market_deviation_percent < -5.0")

    if online_only:
        conditions.append("is_online = 1")

    sql = f"""
        SELECT id, title, city, postal_code, state, property_type, living_space, rooms,
               purchase_price, current_rent, gross_yield, monthly_cashflow, market_value,
               market_deviation_percent, source_portal, portal_url, is_online, availability_status
        FROM properties
        WHERE {' AND '.join(conditions)}
        ORDER BY gross_yield DESC
        LIMIT ?
    """
    params.append(max(1, min(limit, 50)))

    cursor.execute(sql, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not rows:
        return json.dumps({
            "status": "success",
            "count": 0,
            "message": "No properties found matching your filter criteria. Try relaxing yield or online filters.",
            "deals": []
        }, indent=2, ensure_ascii=False)

    return json.dumps({
        "status": "success",
        "count": len(rows),
        "deals": rows
    }, indent=2, ensure_ascii=False)


@server.tool(
    name="calculate_cashflow",
    description="Calculate complete real estate cashflow and investment metrics. "
                "Automatically resolves the German federal state (Bundesland) and Grunderwerbsteuer "
                "from the postal code (PLZ) or city without prompting the user. "
                "Calculates notary fees, broker fees, bank annuity, net cashflow, net yield, and ROI."
)
def calculate_cashflow(
    price: float,
    rent: float,
    postal_code: str = "",
    city: str = "",
    state: str = "",
    equity_value: float = 20.0,
    equity_type: str = "percent",
    interest_rate: float = 3.6,
    repayment_rate: float = 2.0,
    additional_costs: float = 50.0,
    renovation_costs: float = 0.0,
    commission_percent: float = 3.57,
    living_space: float = 0.0
) -> str:
    """
    Calculates detailed investment financials with automatic Grunderwerbsteuer.
    Args:
        price: Purchase price in EUR (Kaufpreis)
        rent: Monthly cold rent in EUR (Kaltmiete)
        postal_code: 5-digit German postal code (PLZ) - used to auto-determine Grunderwerbsteuer
        city: City name (e.g. 'Essen', 'Leipzig') - fallback to determine state
        state: Federal state (Bundesland) - optional override
        equity_value: Equity contribution (default 20.0%)
        equity_type: 'percent' or 'amount' (EUR)
        interest_rate: Annual mortgage interest rate in % (Zinssatz, default 3.6%)
        repayment_rate: Annual mortgage repayment in % (Tilgungssatz, default 2.0%)
        additional_costs: Non-recoverable monthly management & reserve costs in EUR (Hausgeld/Instandhaltung)
        renovation_costs: Initial renovation / refurbishment budget in EUR
        commission_percent: Broker commission in % (Maklercourtage, default 3.57%)
        living_space: Living space in m²
    """
    # Auto-resolve state and tax rate
    geo_data = resolve_location_and_tax(postal_code=postal_code, city=city, state=state)
    resolved_state = geo_data["state"]
    tax_rate = geo_data["tax_rate"]

    res = calc_engine(
        price=price,
        rent=rent,
        equity_value=equity_value,
        equity_type=equity_type,
        interest_rate=interest_rate,
        repayment_rate=repayment_rate,
        additional_costs=additional_costs,
        renovation_costs=renovation_costs,
        country="DE",
        state=resolved_state,
        postal_code=postal_code,
        city=city,
        commission_percent=commission_percent,
        living_space=living_space
    )

    res["tax_resolution_notes"] = {
        "postal_code": postal_code,
        "resolved_state": resolved_state,
        "grunderwerbsteuer_rate_percent": tax_rate,
        "auto_detected": True
    }

    return json.dumps(res, indent=2, ensure_ascii=False)


@server.tool(
    name="get_deal_details",
    description="Retrieve comprehensive dossier for a specific property by ID. "
                "Includes property characteristics, cadastral parcel data (Flurstück), "
                "price history, Sprengnetter market valuation, cashflow analysis, "
                "and live portal online availability."
)
def get_deal_details(property_id: int) -> str:
    """
    Args:
        property_id: Database ID of the property
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM properties WHERE id = ?", (property_id,))
    row = cursor.fetchone()
    conn.close()

    if not row:
        return json.dumps({"error": f"Property with ID {property_id} not found"}, indent=2)

    data = dict(row)

    # Calculate live cashflow with auto-detected Grunderwerbsteuer
    cf = calc_engine(
        price=data.get("purchase_price", 0),
        rent=data.get("current_rent", 0),
        postal_code=data.get("postal_code", ""),
        city=data.get("city", ""),
        state=data.get("state", ""),
        living_space=data.get("living_space", 0)
    )
    data["cashflow_analysis"] = cf

    return json.dumps(data, indent=2, ensure_ascii=False)


@server.tool(
    name="check_listing_availability",
    description="Actively verify if a property listing is still live and active on the external portal, "
                "or if it has expired / 404'd / been deactivated. Updates the database record."
)
def check_listing_availability(property_id: int = 0, url: str = "") -> str:
    """
    Args:
        property_id: ID of the property to check and update in DB
        url: External portal listing URL to test directly
    """
    if property_id > 0:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, portal_url, source_portal, city FROM properties WHERE id = ?", (property_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return json.dumps({"error": f"Property with ID {property_id} not found"}, indent=2)

        prop_id, portal_url, source_portal, city = row
        status_result = check_portal_url_live(portal_url, source_portal, city)

        cursor.execute("""
            UPDATE properties
            SET is_online = ?, availability_status = ?, status_message = ?, last_checked_at = ?, live_search_url = ?
            WHERE id = ?
        """, (
            1 if status_result["is_online"] else 0,
            status_result["availability_status"],
            status_result["status_message"],
            status_result["last_checked_at"],
            status_result["live_search_url"],
            prop_id
        ))
        conn.commit()
        conn.close()

        return json.dumps({
            "property_id": prop_id,
            "portal_url": portal_url,
            "source_portal": source_portal,
            "city": city,
            **status_result
        }, indent=2, ensure_ascii=False)
    elif url:
        result = check_portal_url_live(url)
        return json.dumps(result, indent=2, ensure_ascii=False)
    else:
        return json.dumps({"error": "Please provide either property_id or url"}, indent=2)


@server.tool(
    name="get_city_benchmarks",
    description="Returns regional real estate market benchmarks across German cities. "
                "Includes average purchase price/m², average rent/m², and typical gross yield."
)
def get_city_benchmarks(city: str = "") -> str:
    """
    Args:
        city: City name (e.g. 'Leipzig', 'Berlin', 'München', 'Essen'). If empty, returns all cities.
    """
    if city.strip():
        data = get_city_benchmark(city.strip())
        return json.dumps(data, indent=2, ensure_ascii=False)
    else:
        return json.dumps(CITY_BENCHMARKS, indent=2, ensure_ascii=False)


@server.tool(
    name="resolve_location_tax",
    description="Resolve German federal state (Bundesland) and Grunderwerbsteuer tax rate (%) "
                "automatically from a 5-digit postal code (PLZ) or city."
)
def resolve_location_tax(postal_code: str = "", city: str = "") -> str:
    """
    Args:
        postal_code: 5-digit German postal code (PLZ)
        city: City name
    """
    res = resolve_location_and_tax(postal_code=postal_code, city=city)
    return json.dumps(res, indent=2, ensure_ascii=False)


@server.tool(
    name="delete_deal",
    description="Delete a property deal from the database by its ID."
)
def delete_deal(property_id: int) -> str:
    """
    Args:
        property_id: ID of the deal to delete
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM properties WHERE id = ?", (property_id,))
    if not cursor.fetchone():
        conn.close()
        return json.dumps({"status": "error", "message": f"Deal #{property_id} not found"}, indent=2)

    cursor.execute("DELETE FROM properties WHERE id = ?", (property_id,))
    cursor.execute("DELETE FROM deal_notes WHERE property_id = ?", (property_id,))
    conn.commit()
    conn.close()
    return json.dumps({"status": "success", "deleted_id": property_id, "message": f"Deal #{property_id} gelöscht."}, indent=2)


@server.tool(
    name="cleanup_offline_deals",
    description="Bulk delete all deactivated and offline properties from the database in one operation."
)
def cleanup_offline_deals() -> str:
    """Removes all deals where is_online = 0."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM properties WHERE is_online = 0")
    offline_ids = [r[0] for r in cursor.fetchall()]

    if offline_ids:
        placeholders = ",".join("?" * len(offline_ids))
        cursor.execute(f"DELETE FROM properties WHERE id IN ({placeholders})", offline_ids)
        cursor.execute(f"DELETE FROM deal_notes WHERE property_id IN ({placeholders})", offline_ids)
        conn.commit()

    conn.close()
    return json.dumps({
        "status": "success",
        "deleted_count": len(offline_ids),
        "deleted_ids": offline_ids,
        "message": f"{len(offline_ids)} deaktivierte Deals wurden gelöscht."
    }, indent=2)


def main():
    """Start the MCP stdio server"""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
