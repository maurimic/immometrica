"""
API Router for ImmoMetrica Search Agent, Deal Profiles, and Telegram Alert Notifications.
Manages search profiles, manual and scheduled crawler execution, alert feed, and Telegram credentials.
"""

import json
import asyncio
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, HTTPException, BackgroundTasks, Query, Body

from app.database import (
    get_search_profiles,
    get_search_profile_by_id,
    save_search_profile,
    update_search_profile,
    delete_search_profile,
    get_deal_alerts,
    get_system_setting,
    set_system_setting
)
from app.telegram_notifier import test_telegram_connection
from app.search_agent import execute_search_profile, execute_all_active_profiles
from app.scheduler import is_crawler_busy

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def mask_token(t: Optional[str]) -> Optional[str]:
    if not t:
        return None
    t = t.strip()
    if len(t) <= 8:
        return "***"
    return f"{t[:4]}...{t[-4:]}"


# ==============================================================================
# 1. SEARCH PROFILES ENDPOINTS
# ==============================================================================

@router.get("/profiles")
def list_profiles():
    """Returns all saved search profiles."""
    profiles = get_search_profiles()
    return {
        "success": True,
        "total": len(profiles),
        "profiles": profiles
    }


@router.post("/profiles")
def create_profile(payload: Dict[str, Any] = Body(...)):
    """Creates a new investor search profile."""
    name = payload.get("name", "").strip()
    city = payload.get("city", "").strip()

    if not name:
        raise HTTPException(status_code=400, detail="Name des Suchprofils erforderlich.")
    if not city:
        raise HTTPException(status_code=400, detail="Zielstadt erforderlich.")

    new_id = save_search_profile(payload)
    created = get_search_profile_by_id(new_id)

    return {
        "success": True,
        "message": f"Suchprofil '{name}' erfolgreich angelegt.",
        "profile": created
    }


@router.get("/profiles/{profile_id}")
def get_profile(profile_id: int):
    """Retrieves a specific search profile by ID."""
    p = get_search_profile_by_id(profile_id)
    if not p:
        raise HTTPException(status_code=404, detail=f"Suchprofil #{profile_id} nicht gefunden.")
    return {"success": True, "profile": p}


@router.put("/profiles/{profile_id}")
def update_profile(profile_id: int, payload: Dict[str, Any] = Body(...)):
    """Updates search profile filters or active state."""
    existing = get_search_profile_by_id(profile_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Suchprofil #{profile_id} nicht gefunden.")

    updated = update_search_profile(profile_id, payload)
    if not updated:
        raise HTTPException(status_code=400, detail="Keine gültigen Felder zur Aktualisierung übergeben.")

    p = get_search_profile_by_id(profile_id)
    return {
        "success": True,
        "message": f"Suchprofil #{profile_id} erfolgreich aktualisiert.",
        "profile": p
    }


@router.delete("/profiles/{profile_id}")
def remove_profile(profile_id: int):
    """Deletes a search profile."""
    existing = get_search_profile_by_id(profile_id)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Suchprofil #{profile_id} nicht gefunden.")

    delete_search_profile(profile_id)
    return {
        "success": True,
        "message": f"Suchprofil '{existing['name']}' gelöscht."
    }


# ==============================================================================
# 2. RUN / TRIGGER CRAWL
# ==============================================================================

@router.post("/profiles/run")
def trigger_profile_run(
    profile_id: Optional[int] = Query(None, description="Optional single profile ID to run"),
    background_tasks: BackgroundTasks = None
):
    """
    Triggers an immediate search run for a specific profile or all active profiles.
    Returns results, token usage, cost estimates and newly dispatched alerts.
    """
    if profile_id:
        p = get_search_profile_by_id(profile_id)
        if not p:
            raise HTTPException(status_code=404, detail=f"Suchprofil #{profile_id} nicht gefunden.")
        res = execute_search_profile(profile_id, force=True)
        st = res.get("stats") or {}
        scraped = res.get("total_scraped", 0)
        qualified = res.get("qualified_deals", 0)
        new_alerts = res.get("new_alerts", 0)
        filtered = st.get("filtered_stage1_2", 0)
        text_ai = st.get("text_ai_calls", 0)
        vision_ai = st.get("vision_ai_calls", 0)
        tokens = st.get("total_tokens_used", 0)
        cost = st.get("estimated_cost_eur", 0.0)
        cost_str = f"{cost:.4f}".replace(".", ",") + " €"
        tokens_str = f"{tokens:,}".replace(",", ".")
        msg = f"{scraped} Inserate geprüft: {filtered} lokal aussortiert, {text_ai} per Text-KI und {vision_ai} per Bilder-KI analysiert ({tokens_str} Token, ca. {cost_str})"
        return {
            "success": True,
            "message": msg,
            "results": [res],
            "stats": st,
            "total_scraped": scraped,
            "qualified_deals": qualified,
            "new_alerts": new_alerts
        }
    else:
        results = execute_all_active_profiles()
        total_scraped = sum(r.get("total_scraped", 0) for r in results if isinstance(r, dict))
        total_qualified = sum(r.get("qualified_deals", 0) for r in results if isinstance(r, dict))
        total_new_alerts = sum(r.get("new_alerts", 0) for r in results if isinstance(r, dict))
        last_stats_raw = get_system_setting("crawler_last_stats")
        agg_stats = {}
        if last_stats_raw:
            try:
                agg_stats = json.loads(last_stats_raw)
            except Exception:
                agg_stats = {}
        filtered = agg_stats.get("filtered_stage1_2", 0)
        text_ai = agg_stats.get("text_ai_calls", 0)
        vision_ai = agg_stats.get("vision_ai_calls", 0)
        tokens = agg_stats.get("total_tokens_used", 0)
        cost = agg_stats.get("estimated_cost_eur", 0.0)
        cost_str = f"{cost:.4f}".replace(".", ",") + " €"
        tokens_str = f"{tokens:,}".replace(",", ".")
        msg = f"{total_scraped} Inserate geprüft: {filtered} lokal aussortiert, {text_ai} per Text-KI und {vision_ai} per Bilder-KI analysiert ({tokens_str} Token, ca. {cost_str})"
        return {
            "success": True,
            "message": msg,
            "results": results,
            "stats": agg_stats,
            "total_scraped": total_scraped,
            "qualified_deals": total_qualified,
            "total_new_alerts": total_new_alerts
        }


# ==============================================================================
# 3. ALERT FEED
# ==============================================================================

@router.get("/feed")
def get_alert_feed(
    limit: int = Query(50, ge=1, le=200),
    profile_id: Optional[int] = Query(None)
):
    """Returns chronological list of triggered deal alerts with metrics and AI verdicts."""
    alerts = get_deal_alerts(limit=limit, profile_id=profile_id)
    return {
        "success": True,
        "total": len(alerts),
        "alerts": alerts
    }


# ==============================================================================
# 4. SETTINGS & TELEGRAM INTEGRATION
# ==============================================================================

@router.get("/settings")
def get_alert_settings():
    """Returns crawler and Telegram alert settings."""
    tg_token = get_system_setting("telegram_bot_token")
    tg_chat_id = get_system_setting("telegram_chat_id", "")
    interval = get_system_setting("crawler_interval_minutes", "30")
    active_str = get_system_setting("crawler_active", "0")
    last_run = get_system_setting("crawler_last_run")

    last_stats_raw = get_system_setting("crawler_last_stats")
    crawler_last_stats = None
    if last_stats_raw:
        try:
            crawler_last_stats = json.loads(last_stats_raw)
        except Exception:
            crawler_last_stats = None

    return {
        "telegram_configured": bool(tg_token and tg_chat_id),
        "telegram_masked_token": mask_token(tg_token),
        "telegram_chat_id": tg_chat_id,
        "crawler_interval_minutes": int(interval) if interval and interval.isdigit() else 30,
        "crawler_active": active_str in ("1", "true", "True"),
        "crawler_last_run": last_run,
        "crawler_is_busy": is_crawler_busy(),
        "crawler_last_stats": crawler_last_stats
    }


@router.post("/settings")
def save_alert_settings(payload: Dict[str, Any] = Body(...)):
    """Saves Telegram credentials and crawler schedule settings."""
    if "telegram_bot_token" in payload and payload["telegram_bot_token"] is not None:
        raw_token = str(payload["telegram_bot_token"]).strip()
        if raw_token:
            set_system_setting("telegram_bot_token", raw_token)

    if "telegram_chat_id" in payload and payload["telegram_chat_id"] is not None:
        raw_cid = str(payload["telegram_chat_id"]).strip()
        set_system_setting("telegram_chat_id", raw_cid)

    if "crawler_interval_minutes" in payload:
        try:
            val = max(5, int(payload["crawler_interval_minutes"]))
            set_system_setting("crawler_interval_minutes", str(val))
        except ValueError:
            pass

    if "crawler_active" in payload:
        is_act = "1" if payload["crawler_active"] in (True, 1, "1", "true") else "0"
        set_system_setting("crawler_active", is_act)

    tg_chat_id = get_system_setting("telegram_chat_id", "")
    tg_token = get_system_setting("telegram_bot_token", "")

    return {
        "success": True,
        "message": "Suchagent- & Alert-Einstellungen erfolgreich gespeichert.",
        "telegram_chat_id": tg_chat_id,
        "telegram_configured": bool(tg_token and tg_chat_id)
    }


@router.post("/telegram/test")
def test_telegram(payload: Dict[str, Any] = Body(...)):
    """Sends a live test notification to verify the Telegram bot credentials."""
    token = payload.get("telegram_bot_token")
    chat_id = payload.get("telegram_chat_id")

    # Fallback to stored settings if not provided in payload
    if not token:
        token = get_system_setting("telegram_bot_token")
    if not chat_id:
        chat_id = get_system_setting("telegram_chat_id")

    result = test_telegram_connection(bot_token=token, chat_id=chat_id)
    return result
