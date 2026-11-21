"""
Portal Health & Canary Monitoring Engine for ImmoMetrica.
Periodically verifies scraper and portal availability across Kleinanzeigen, ImmoScout24, and Immowelt.
Triggers immediate Telegram alerts and surfaces startpage notification banners upon portal layout changes or failures.
"""

import time
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

from app.database import get_system_setting, set_system_setting
from app.telegram_notifier import send_system_telegram_alert

logger = logging.getLogger("immometrica.portal_health")

# Standard test URL for Immowelt
IMMOWELT_TEST_URL = "https://www.immowelt.de/expose/461cd070-709d-40a9-9dc4-55fcc8111e86"


def run_canary_checks(force_notify: bool = False) -> Dict[str, Any]:
    """
    Executes live verification tests across all portals.
    Checks whether listings, prices, living spaces, and cities are accurately extracted.
    Saves results to SQLite system_settings and dispatches Telegram alerts if degraded.
    """
    results: Dict[str, Dict[str, Any]] = {}
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Kleinanzeigen Check
    t0 = time.time()
    try:
        from app.scrapers.kleinanzeigen import scrape_kleinanzeigen_deals
        ka_deals = scrape_kleinanzeigen_deals(city="Dortmund", max_results=3)
        elapsed = round(time.time() - t0, 2)
        valid_deals = [d for d in ka_deals if float(d.get("purchase_price") or 0) > 0 and float(d.get("living_space") or 0) > 0]
        if valid_deals:
            results["Kleinanzeigen"] = {
                "status": "healthy",
                "status_code": 200,
                "latency_s": elapsed,
                "message": f"{len(valid_deals)} Inserate erfolgreich mit Kaufpreis und Wohnfläche verifiziert.",
                "last_checked_at": now_iso
            }
        else:
            results["Kleinanzeigen"] = {
                "status": "degraded",
                "status_code": 200,
                "latency_s": elapsed,
                "message": "Suchlauf lieferte keine Inserate mit gültigem Preis und m² (Formatänderung vermutet).",
                "last_checked_at": now_iso
            }
    except Exception as e:
        results["Kleinanzeigen"] = {
            "status": "critical",
            "status_code": 500,
            "latency_s": round(time.time() - t0, 2),
            "message": f"Verbindungs- oder Parser-Fehler: {str(e)}",
            "last_checked_at": now_iso
        }

    # 2. ImmoScout24 Check
    t0 = time.time()
    try:
        from app.scrapers.immoscout import search_immoscout_deals
        is_deals = search_immoscout_deals(city="Dortmund", max_results=3)
        elapsed = round(time.time() - t0, 2)
        valid_is = [d for d in is_deals if float(d.get("purchase_price") or 0) > 0 and float(d.get("living_space") or 0) > 0]
        if valid_is:
            results["ImmoScout24"] = {
                "status": "healthy",
                "status_code": 200,
                "latency_s": elapsed,
                "message": f"{len(valid_is)} Inserate erfolgreich verifiziert.",
                "last_checked_at": now_iso
            }
        else:
            results["ImmoScout24"] = {
                "status": "degraded",
                "status_code": 200,
                "latency_s": elapsed,
                "message": "ImmoScout24 lieferte keine parsebaren Angebote (API oder Layout geändert).",
                "last_checked_at": now_iso
            }
    except Exception as e:
        results["ImmoScout24"] = {
            "status": "critical",
            "status_code": 500,
            "latency_s": round(time.time() - t0, 2),
            "message": f"ImmoScout-Fehler: {str(e)}",
            "last_checked_at": now_iso
        }

    # 3. Immowelt Check
    t0 = time.time()
    try:
        from app.scrapers.immowelt import fetch_immowelt_expose
        iw_res = fetch_immowelt_expose(IMMOWELT_TEST_URL)
        elapsed = round(time.time() - t0, 2)
        if iw_res.get("success") and float(iw_res.get("price") or 0) > 0 and float(iw_res.get("living_space") or 0) > 0:
            results["Immowelt"] = {
                "status": "healthy",
                "status_code": 200,
                "latency_s": elapsed,
                "message": f"Exposé erfolgreich ausgelesen ({iw_res.get('city')}, {iw_res.get('price'):,.0f} €, {iw_res.get('living_space')} m²).",
                "last_checked_at": now_iso
            }
        else:
            st = iw_res.get("status_code", 400)
            err = iw_res.get("error") or "Kaufpreis oder Wohnfläche konnten nicht extrahiert werden."
            level = "degraded" if st in [403, 429] else "critical"
            results["Immowelt"] = {
                "status": level,
                "status_code": st,
                "latency_s": elapsed,
                "message": err,
                "last_checked_at": now_iso
            }
    except Exception as e:
        results["Immowelt"] = {
            "status": "critical",
            "status_code": 500,
            "latency_s": round(time.time() - t0, 2),
            "message": f"Immowelt-Fehler: {str(e)}",
            "last_checked_at": now_iso
        }

    # Determine overall status
    overall = "healthy"
    failing_portals = []
    for portal, res in results.items():
        if res["status"] == "critical":
            overall = "critical"
            failing_portals.append(f"{portal} ({res['message']})")
        elif res["status"] == "degraded" and overall != "critical":
            overall = "degraded"
            failing_portals.append(f"{portal} ({res['message']})")

    # Update system settings in DB
    set_system_setting("portal_health_status", json.dumps(results))
    set_system_setting("portal_health_overall", overall)
    set_system_setting("portal_health_last_check", now_iso)

    if overall != "healthy":
        alert_msg = f"Einschränkung bei Immobilienportal(en) erkannt: {'; '.join(failing_portals)}"
        set_system_setting("portal_alert_active", "1")
        set_system_setting("portal_alert_message", alert_msg)
        set_system_setting("portal_alert_level", overall)
        set_system_setting("portal_alert_title", "⚠️ Portal-Warnung: Scraper eingeschränkt")

        # Check Telegram rate limit (send at most once every 2 hours unless forced)
        last_tg = get_system_setting("telegram_last_canary_alert")
        should_send_tg = force_notify
        if not should_send_tg:
            if not last_tg:
                should_send_tg = True
            else:
                try:
                    last_dt = datetime.strptime(last_tg, "%Y-%m-%d %H:%M:%S")
                    if (datetime.now() - last_dt).total_seconds() >= 7200: # 2 hours
                        should_send_tg = True
                except Exception:
                    should_send_tg = True

        if should_send_tg:
            send_system_telegram_alert(
                title="Scraper-Anomalie / Portal-Format geändert",
                message=alert_msg,
                portal=", ".join(results.keys()),
                level=overall
            )
            set_system_setting("telegram_last_canary_alert", now_iso)
    else:
        # Reset alert if previously active
        prev_active = get_system_setting("portal_alert_active")
        if prev_active == "1":
            set_system_setting("portal_alert_active", "0")
            set_system_setting("portal_alert_message", "")

    return {
        "overall": overall,
        "last_checked_at": now_iso,
        "portals": results,
        "alert_active": overall != "healthy",
        "alert_message": get_system_setting("portal_alert_message", ""),
        "alert_level": overall
    }


def get_portal_health_status() -> Dict[str, Any]:
    """
    Returns the current cached health status and alert information for UI consumption.
    """
    raw_status = get_system_setting("portal_health_status")
    portals = {}
    if raw_status:
        try:
            portals = json.loads(raw_status)
        except Exception:
            portals = {}

    overall = get_system_setting("portal_health_overall", "healthy")
    last_check = get_system_setting("portal_health_last_check")
    alert_active = get_system_setting("portal_alert_active", "0") in ("1", "true", "True")
    alert_msg = get_system_setting("portal_alert_message", "")
    alert_level = get_system_setting("portal_alert_level", "warning")
    alert_title = get_system_setting("portal_alert_title", "⚠️ Portal-Warnung")

    # If never checked, return empty/unknown state
    if not portals:
        return {
            "overall": "unknown",
            "last_checked_at": None,
            "portals": {
                "Kleinanzeigen": {"status": "unknown", "message": "Noch nicht geprüft"},
                "ImmoScout24": {"status": "unknown", "message": "Noch nicht geprüft"},
                "Immowelt": {"status": "unknown", "message": "Noch nicht geprüft"}
            },
            "alert": {
                "active": False,
                "title": "",
                "message": "",
                "level": "info"
            }
        }

    return {
        "overall": overall,
        "last_checked_at": last_check,
        "portals": portals,
        "alert": {
            "active": alert_active,
            "title": alert_title,
            "message": alert_msg,
            "level": alert_level
        }
    }


def record_portal_incident(portal: str, reason: str, details: str = ""):
    """
    Instantly records an incident when a real scrape or single URL import fails
    due to missing fields or bot protection.
    Surfaces the notification banner on the startpage and notifies Telegram.
    """
    now_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.warning(f"Recording portal incident for {portal}: {reason} ({details})")

    set_system_setting("portal_alert_active", "1")
    set_system_setting("portal_alert_title", f"⚠️ Warnung: {portal} Scraper-Format geändert")
    set_system_setting("portal_alert_message", f"Beim Abruf von {portal} konnten Pflichtdaten nicht extrahiert werden: {reason}")
    set_system_setting("portal_alert_level", "critical" if "Schema" in reason else "warning")
    set_system_setting("portal_alert_updated_at", now_iso)

    # Update portals status dict
    raw_status = get_system_setting("portal_health_status")
    portals = {}
    if raw_status:
        try:
            portals = json.loads(raw_status)
        except Exception:
            portals = {}
    portals[portal] = {
        "status": "degraded",
        "status_code": 422,
        "message": reason,
        "details": details,
        "last_checked_at": now_iso
    }
    set_system_setting("portal_health_status", json.dumps(portals))
    set_system_setting("portal_health_overall", "degraded")

    # Send Telegram alert (cooldown: max 1 per hour per incident)
    last_tg = get_system_setting(f"telegram_last_alert_{portal.lower()}")
    should_send = True
    if last_tg:
        try:
            last_dt = datetime.strptime(last_tg, "%Y-%m-%d %H:%M:%S")
            if (datetime.now() - last_dt).total_seconds() < 3600:
                should_send = False
        except Exception:
            pass

    if should_send:
        send_system_telegram_alert(
            title=f"Format-Änderung / Scraper-Fehler bei {portal}",
            message=reason,
            portal=portal,
            error_details=details,
            level="warning"
        )
        set_system_setting(f"telegram_last_alert_{portal.lower()}", now_iso)


def dismiss_portal_alert() -> bool:
    """Dismisses the active alert banner on the frontend."""
    set_system_setting("portal_alert_active", "0")
    return True
