"""
Portal Health API Router for ImmoMetrica.
Provides endpoints for monitoring portal scrapers, triggering live canary checks,
and dismissing startpage notification alerts.
"""

from fastapi import APIRouter, Query, BackgroundTasks
from typing import Dict, Any

from app.portal_health import (
    run_canary_checks,
    get_portal_health_status,
    dismiss_portal_alert
)

router = APIRouter(prefix="/api/portal-health", tags=["portal-health"])


@router.get("")
def get_health():
    """Returns the current cached health status and active alert for portals."""
    return get_portal_health_status()


@router.post("/check")
def trigger_canary_check(force_notify: bool = Query(False)):
    """Triggers an immediate live canary check across all portals."""
    return run_canary_checks(force_notify=force_notify)


@router.post("/dismiss")
def dismiss_alert():
    """Dismisses the active portal alert banner on the frontend."""
    dismiss_portal_alert()
    return {"success": True, "message": "Benachrichtigung ausgeblendet."}
