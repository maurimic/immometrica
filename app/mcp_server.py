"""
Model Context Protocol (MCP) Server for ImmoMetrica Clone.
Allows AI assistants (Claude, Cursor, Antigravity) to query real estate deals,
calculate cashflows, evaluate Sprengnetter market benchmarks, and manage deal pipeline.
"""

from fastapi import APIRouter, HTTPException, Body, Depends, Request, status
from typing import Dict, Any, List
import hmac
import json
from app.database import (
    get_connection,
    get_active_mcp_token,
    regenerate_mcp_token,
    increment_mcp_quota
)
from app.auth import get_current_user, require_auth
from app.calculator import calculate_cashflow
from app.valuation import evaluate_property_valuation, get_city_benchmark, CITY_BENCHMARKS

router = APIRouter(prefix="/mcp", tags=["mcp"])


def _require_mcp_auth(request: Request) -> Dict[str, Any]:
    """
    Allows either an authenticated web session (require_auth / session cookie)
    or a valid MCP Bearer token from api_tokens.
    """
    user = get_current_user(request)
    if user:
        return user

    auth_header = request.headers.get("Authorization") or ""
    if auth_header.startswith("Bearer "):
        submitted = auth_header[7:].strip()
        active = get_active_mcp_token().get("token") or ""
        if submitted and active and hmac.compare_digest(submitted.encode("utf-8"), active.encode("utf-8")):
            return {"authenticated": True, "source": "mcp_token"}

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Nicht autorisiert. Gültiger MCP Bearer Token oder Session-Login erforderlich.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _mcp_snippet_for_token(token: str) -> Dict[str, Any]:
    from app.telegram_notifier import get_base_url
    mcp_url = f"{get_base_url().rstrip('/')}/mcp/call"
    return {
        "mcpServers": {
            "immometrica": {
                "url": mcp_url,
                "headers": {
                    "Authorization": f"Bearer {token}"
                }
            }
        }
    }

MCP_TOOLS = [
    {
        "name": "search_deals",
        "description": "Searches aggregated real estate properties across 40+ portals based on investment criteria (price, yield, positive cashflow, undervalued properties, city, state, property type).",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "City name, e.g. 'Leipzig', 'Berlin', 'Essen'"},
                "state": {"type": "string", "description": "Federal state, e.g. 'Nordrhein-Westfalen', 'Sachsen'"},
                "property_type": {"type": "string", "enum": ["Mehrfamilienhaus", "Eigentumswohnung", "Einfamilienhaus"], "description": "Type of property"},
                "min_yield": {"type": "number", "description": "Minimum gross rental yield in % (e.g. 5.5)"},
                "max_price": {"type": "number", "description": "Maximum purchase price in EUR"},
                "positive_cashflow": {"type": "boolean", "description": "Filter strictly for deals with positive monthly net cashflow"},
                "undervalued_only": {"type": "boolean", "description": "Filter strictly for deals priced below Sprengnetter market value"}
            }
        }
    },
    {
        "name": "calculate_cashflow",
        "description": "Calculates complete real estate investment financials: ancillary costs (Notary, Grunderwerbsteuer per state), loan annuity, cold rent, maintenance reserves, monthly net cashflow, and Return on Equity (Cash-on-Cash).",
        "parameters": {
            "type": "object",
            "required": ["price", "rent"],
            "properties": {
                "price": {"type": "number", "description": "Purchase price in EUR"},
                "rent": {"type": "number", "description": "Monthly cold rent in EUR"},
                "equity_value": {"type": "number", "default": 20.0, "description": "Equity percentage or fixed amount"},
                "interest_rate": {"type": "number", "default": 3.6, "description": "Mortgage interest rate in %"},
                "repayment_rate": {"type": "number", "default": 2.0, "description": "Initial repayment rate (Tilgung) in %"},
                "state": {"type": "string", "default": "Nordrhein-Westfalen", "description": "Federal state for Grunderwerbsteuer"}
            }
        }
    },
    {
        "name": "get_deal_details",
        "description": "Retrieves comprehensive information for a specific property including cadastral parcel data, price history, Sprengnetter valuation benchmark, and broker notes.",
        "parameters": {
            "type": "object",
            "required": ["property_id"],
            "properties": {
                "property_id": {"type": "integer", "description": "Property ID"}
            }
        }
    },
    {
        "name": "get_city_benchmarks",
        "description": "Returns current market benchmarks (average buy price/m², average rent/m², average rental yield) for German cities.",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "Optional city name to filter"}
            }
        }
    },
    {
        "name": "audit_property_ai",
        "description": "Performs institutional AI Due Diligence on a property listing: reads full exposé description, analyzes energy efficiency (GEG), identifies WEG risks (Instandhaltungsrücklage, missing protocols, special assessments), and estimates renovation CaPEx in EUR.",
        "parameters": {
            "type": "object",
            "required": ["property_id"],
            "properties": {
                "property_id": {"type": "integer", "description": "Property ID to audit"},
                "force_refresh": {"type": "boolean", "default": False, "description": "Whether to fetch fresh description from portal"}
            }
        }
    },
    {
        "name": "generate_purchase_offer",
        "description": "Generates a formal German real estate purchase offer (Letter of Intent / Kaufabsichtserklärung) with justified counter-offer price based on identified CaPEx, WEG reserve deficits, and energy status.",
        "parameters": {
            "type": "object",
            "required": ["property_id"],
            "properties": {
                "property_id": {"type": "integer", "description": "Property ID"},
                "buyer_name": {"type": "string", "default": "Kaufinteressent", "description": "Buyer's name"},
                "buyer_email": {"type": "string", "default": "investor@example.com", "description": "Buyer's email"},
                "buyer_phone": {"type": "string", "default": "+49 170 1234567", "description": "Buyer's phone"},
                "custom_price": {"type": "number", "description": "Optional custom offer price (overrides AI recommendation)"}
            }
        }
    },
    {
        "name": "analyze_property_images",
        "description": "Performs AI Computer Vision and Multimodal Due Diligence on property photos: evaluates visual condition (IST-Zustand) of bathroom, kitchen, flooring, windows, heating, and walls, detecting defects and calculating itemized renovation costs (CaPEx) in EUR according to German BKI standards.",
        "parameters": {
            "type": "object",
            "required": ["property_id"],
            "properties": {
                "property_id": {"type": "integer", "description": "Database ID of the property to analyze"},
                "image_urls": {"type": "array", "items": {"type": "string"}, "description": "Optional custom list of image URLs to inspect"}
            }
        }
    },
    {
        "name": "list_search_profiles",
        "description": "Lists all configured automated search profiles with their filter parameters, deal counts, and active status.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "run_search_profiles",
        "description": "Triggers immediate portal crawling for one or all active search profiles, applying AI risk pre-checks and Telegram deal alerts.",
        "parameters": {
            "type": "object",
            "properties": {
                "profile_id": {"type": "integer", "description": "Optional ID of a single profile to run. If omitted, runs all active profiles."}
            }
        }
    },
    {
        "name": "get_deal_alerts",
        "description": "Retrieves the latest deal alerts found by the automated search agent, including AI verdicts, yield, and discount metrics.",
        "parameters": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20, "description": "Maximum number of alerts to return"}
            }
        }
    }
]

@router.get("/tools", dependencies=[Depends(_require_mcp_auth)])
def list_tools():
    return {"tools": MCP_TOOLS}

@router.get("/snippet", dependencies=[Depends(require_auth)])
def get_mcp_config_snippet():
    """Generates the config snippet for Claude / Cursor / Antigravity settings"""
    token_info = get_active_mcp_token()
    return _mcp_snippet_for_token(token_info["token"])

@router.get("/status", dependencies=[Depends(require_auth)])
def get_mcp_status():
    """
    Returns current active token, quota usage, and configuration JSON snippet.
    """
    token_info = get_active_mcp_token()
    token = token_info["token"]
    snippet = _mcp_snippet_for_token(token)
    return {
        "token": token,
        "name": token_info.get("name", "Antigravity AI Agent & n8n Sync"),
        "quota_used": token_info.get("quota_used", 0),
        "quota_limit": token_info.get("quota_limit", 2000),
        "created_at": token_info.get("created_at", ""),
        "config_json": snippet,
        "snippet": snippet
    }

@router.post("/regenerate-token", dependencies=[Depends(require_auth)])
def regenerate_token():
    """
    Regenerates a new secure MCP token in the database and returns the new status and snippet.
    """
    token_info = regenerate_mcp_token()
    token = token_info["token"]
    snippet = _mcp_snippet_for_token(token)
    return {
        "success": True,
        "token": token,
        "name": token_info.get("name", "Antigravity AI Agent & n8n Sync"),
        "quota_used": token_info.get("quota_used", 0),
        "quota_limit": token_info.get("quota_limit", 2000),
        "created_at": token_info.get("created_at", ""),
        "config_json": snippet,
        "snippet": snippet
    }

@router.post("/call", dependencies=[Depends(_require_mcp_auth)])
def call_tool(payload: Dict[str, Any] = Body(...)):
    if not isinstance(payload, dict) or not payload.get("name"):
        raise HTTPException(status_code=400, detail="Tool 'name' fehlt.")
    increment_mcp_quota()
    tool_name = payload.get("name")
    args = payload.get("arguments") or {}
    if not isinstance(args, dict):
        raise HTTPException(status_code=400, detail="'arguments' muss ein JSON-Objekt sein.")

    if tool_name == "search_deals":
        conn = get_connection()
        try:
            cursor = conn.cursor()
            conditions = ["1=1"]
            params = []
            if args.get("city"):
                conditions.append("city LIKE ?")
                params.append(f"%{args['city']}%")
            if args.get("state"):
                conditions.append("state = ?")
                params.append(args["state"])
            if args.get("property_type"):
                conditions.append("property_type = ?")
                params.append(args["property_type"])
            if args.get("min_yield"):
                min_y = float(args["min_yield"])
                conditions.append("(gross_yield >= ? OR ((expected_rent * 12.0 / NULLIF(purchase_price, 0)) * 100.0) >= ?)")
                params.extend([min_y, min_y])
            if args.get("max_price"):
                conditions.append("purchase_price <= ?")
                params.append(float(args["max_price"]))
            if args.get("positive_cashflow"):
                conditions.append("monthly_cashflow >= 0")
            if args.get("undervalued_only"):
                conditions.append("market_deviation_percent < -5")

            query = f"SELECT * FROM properties WHERE {' AND '.join(conditions)} ORDER BY gross_yield DESC LIMIT 20"
            cursor.execute(query, params)
            rows = [dict(r) for r in cursor.fetchall()]
        finally:
            conn.close()
        return {"result": rows}

    elif tool_name == "calculate_cashflow":
        if "price" not in args or "rent" not in args:
            raise HTTPException(status_code=400, detail="'price' und 'rent' sind erforderlich.")
        price = float(args["price"])
        rent = float(args["rent"])
        equity = float(args.get("equity_value", 20.0))
        interest = float(args.get("interest_rate", 3.6))
        repayment = float(args.get("repayment_rate", 2.0))
        state = args.get("state", "Nordrhein-Westfalen")
        res = calculate_cashflow(
            price=price,
            rent=rent,
            equity_value=equity,
            interest_rate=interest,
            repayment_rate=repayment,
            state=state
        )
        return {"result": res}

    elif tool_name == "get_deal_details":
        if "property_id" not in args:
            raise HTTPException(status_code=400, detail="'property_id' ist erforderlich.")
        prop_id = int(args["property_id"])
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM properties WHERE id = ?", (prop_id,))
            row = cursor.fetchone()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Property not found")
        return {"result": dict(row)}

    elif tool_name == "get_city_benchmarks":
        city = args.get("city")
        if city:
            return {"result": get_city_benchmark(city)}
        return {"result": CITY_BENCHMARKS}

    elif tool_name == "audit_property_ai":
        from app.routers.properties import run_property_ai_audit
        if "property_id" not in args:
            raise HTTPException(status_code=400, detail="'property_id' ist erforderlich.")
        prop_id = int(args["property_id"])
        force_refresh = bool(args.get("force_refresh", False))
        res = run_property_ai_audit(property_id=prop_id, force_refresh=force_refresh)
        return {"result": res}

    elif tool_name == "generate_purchase_offer":
        from app.routers.properties import run_property_ai_audit
        if "property_id" not in args:
            raise HTTPException(status_code=400, detail="'property_id' ist erforderlich.")
        prop_id = int(args["property_id"])
        buyer_name = args.get("buyer_name", "Kaufinteressent")
        buyer_email = args.get("buyer_email", "investor@example.com")
        buyer_phone = args.get("buyer_phone", "+49 170 1234567")

        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM properties WHERE id = ?", (prop_id,))
            row = cursor.fetchone()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="Property not found")

        prop = dict(row)
        audit_res = run_property_ai_audit(property_id=prop_id, force_refresh=False)
        audit = audit_res["ai_audit"]

        orig_price = float(prop.get("purchase_price", 0.0))
        target_price = float(args.get("custom_price") or audit.get("negotiation_target_price", orig_price))
        discount = max(0.0, orig_price - target_price)

        capex = audit.get("capex", {})
        insights = audit.get("insights", {})

        reasons = []
        if capex.get("total_capex", 0) > 0:
            top_items = ", ".join(i["title"] for i in capex.get("items", [])[:3])
            reasons.append(f"- Notwendiger Investitions- & Sanierungsbedarf (CaPEx ca. {capex['total_capex']:,.2f} € für {top_items})")
        if insights.get("no_reserve_found"):
            reasons.append("- Fehlende Instandhaltungsrücklage der WEG (0 € Rücklage gebildet; akutes Sonderumlagenrisiko)")
        if insights.get("energy_class") in ["F", "G", "H"]:
            reasons.append(f"- Gebäudeenergiegesetz (GEG) & Energieklasse {insights['energy_class']} (mittelfristige energetische Dämmpflichten)")
        if not reasons:
            reasons.append("- Marktwertanalyse und Zinsstruktur im aktuellen Marktumfeld")

        loi_text = (
            f"Sehr geehrte Damen und Herren,\n"
            f"sehr geehrte Eigentümer,\n\n"
            f"hiermit bekunde ich mein verbindliches Kaufinteresse (Letter of Intent) für Ihre Immobilie:\n\n"
            f"Objekt: {prop.get('title')}\n"
            f"Standort: {prop.get('address')}, {prop.get('postal_code')} {prop.get('city')}\n"
            f"Wohnfläche: ca. {prop.get('living_space')} m²\n\n"
            f"Angebotener Kaufpreis: {target_price:,.2f} €\n"
            f"(Angebotspreis im Inserat: {orig_price:,.2f} €)\n\n"
            f"Begründung des Kaufpreisangebots:\n"
            f"Auf Basis unserer kaufmännischen und bautechnischen Vorprüfung setzt sich dieses Angebot zusammen:\n"
            + "\n".join(reasons) + "\n\n"
            f"Konditionen & Abwicklung:\n"
            f"1. Vorläufige Finanzierungsbestätigung bzw. Eigenkapitalnachweis liegt vor.\n"
            f"2. Beurkundung innerhalb von 14 bis 21 Werktagen nach Erhalt der vollständigen Objektunterlagen möglich.\n"
            f"3. Kaufpreiszahlung und Übergabe flexibel nach Absprache.\n\n"
            f"Mit freundlichen Grüßen\n"
            f"{buyer_name}\n"
            f"Telefon: {buyer_phone}\n"
            f"E-Mail: {buyer_email}"
        )

        from app.ai_analyst import build_missing_documents_request
        doc_req = build_missing_documents_request(
            prop, audit, buyer_name=buyer_name, buyer_email=buyer_email, buyer_phone=buyer_phone
        )

        return {
            "result": {
                "property_id": prop_id,
                "original_price": orig_price,
                "offered_price": target_price,
                "discount": discount,
                "document_request_subject": doc_req["subject"],
                "document_request_email": doc_req["email_text"],
                "missing_documents": doc_req["missing_documents"],
                "letter_of_intent_text": loi_text
            }
        }

    elif tool_name == "analyze_property_images":
        prop_id = int(args.get("property_id", 0))
        from app.routers.properties import get_property_image_analysis, run_property_image_analysis
        cached = get_property_image_analysis(prop_id)
        if cached and cached.get("image_analysis"):
            return {"result": cached["image_analysis"]}
        res = run_property_image_analysis(prop_id)
        return {"result": res["image_analysis"]}

    elif tool_name == "list_search_profiles":
        from app.database import get_search_profiles
        profiles = get_search_profiles()
        return {"result": {"profiles": profiles, "total": len(profiles)}}

    elif tool_name == "run_search_profiles":
        from app.search_agent import execute_search_profile, execute_all_active_profiles
        pid = args.get("profile_id")
        if pid:
            res = execute_search_profile(int(pid), force=True)
            return {"result": res}
        else:
            results = execute_all_active_profiles()
            return {"result": {"profiles_executed": len(results), "details": results}}

    elif tool_name == "get_deal_alerts":
        from app.database import get_deal_alerts
        limit = int(args.get("limit", 20))
        alerts = get_deal_alerts(limit=limit)
        return {"result": {"alerts": alerts, "total": len(alerts)}}

    else:
        raise HTTPException(status_code=400, detail=f"Unknown tool: {tool_name}")
