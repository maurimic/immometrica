"""
Telegram Bot Notification Service for ImmoMetrica Clone.
Dispatches real-time push alerts to investors for deals matching search profile criteria.
Includes connection testing, Markdown/HTML formatting, and robust error handling.
"""

import os
import requests
import html
import logging
from typing import Dict, Any, Optional
from pathlib import Path
from datetime import datetime

logger = logging.getLogger("immometrica.telegram")

DEFAULT_LIVE_URL = "https://immofinder-poggel-micciche.duckdns.org"


def is_live_environment() -> bool:
    """
    Ermittelt, ob die Anwendung in der Live-/Produktionsumgebung läuft (z.B. Linux-Server, Docker),
    oder lokal auf dem Entwicklungsrechner (Windows / Localhost).
    """
    if os.getenv("ENV") == "production":
        return True
    if os.getenv("DOCKER_CONTAINER") == "1":
        return True
    if os.path.exists("/data/immometrica.db"):
        return True
    # Auf Linux-/Server-Systemen ohne explizites dev-Flag
    if os.name != "nt" and os.getenv("ENV") != "development":
        return True
    return False


def _is_ip_address(url_or_host: str) -> bool:
    """Prüft, ob eine URL oder ein Host eine rohe IP-Adresse statt eines Domainnamens ist."""
    try:
        raw = (url_or_host or "").strip()
        if "://" in raw:
            raw = raw.split("://", 1)[1]
        host = raw.split("/")[0].split(":")[0]
        parts = host.split(".")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            return True
        return False
    except Exception:
        return False


def get_base_url() -> str:
    """
    Ermittelt die öffentliche Basis-URL der ImmoMetrica-Instanz:
    - Auf dem Live-Server (Docker/Linux/Production):
      Nutzt standardmäßig DEFAULT_LIVE_URL (https://immofinder-poggel-micciche.duckdns.org)
      bzw. eine konfigurierte Domain (niemals eine rohe IP-Adresse).
    - Lokal auf dem Entwicklungsrechner (Windows/Localhost):
      Nutzt http://127.0.0.1:8000 oder die lokale Adresse.
    """
    live = is_live_environment()

    # 1. Environment variables from runtime / container
    for env_var in ["APP_BASE_URL", "BASE_URL", "PUBLIC_URL", "SERVER_URL"]:
        val = os.getenv(env_var)
        if val and val.strip():
            cleaned = val.strip().rstrip("/")
            # Auf dem Live-Server niemals eine reine IP-Adresse für Benachrichtigungen nutzen
            if live and _is_ip_address(cleaned):
                continue
            return cleaned

    # 2. Check database settings (manually saved in UI)
    try:
        from app.database import get_system_setting
        db_val = get_system_setting("app_base_url")
        if db_val and db_val.strip():
            cleaned = db_val.strip().rstrip("/")
            if not (live and _is_ip_address(cleaned)):
                return cleaned
    except Exception:
        pass

    # 3. Check local .env file (nur wenn lokal)
    if not live:
        try:
            from app.auth import _read_env_file, LOCAL_ENV_PATH
            env_vars = _read_env_file(LOCAL_ENV_PATH)
            for k in ["APP_BASE_URL", "BASE_URL", "PUBLIC_URL"]:
                if env_vars.get(k):
                    cleaned = env_vars[k].strip().rstrip("/")
                    return cleaned
        except Exception:
            pass

    # 4. Check auto-detected domains from incoming requests
    try:
        from app.database import get_system_setting
        if live:
            server_url = get_system_setting("last_server_base_url")
            if server_url and server_url.strip():
                cleaned = server_url.strip().rstrip("/")
                if not _is_ip_address(cleaned):
                    return cleaned
        else:
            local_url = get_system_setting("last_local_base_url")
            if local_url and local_url.strip():
                return local_url.strip().rstrip("/")
    except Exception:
        pass

    # 5. Fallback je nach Umgebung
    if live:
        return DEFAULT_LIVE_URL

    port = os.getenv("PORT", "8000")
    host = os.getenv("HOST", "127.0.0.1")
    if host in ["0.0.0.0", ""]:
        host = "127.0.0.1"
    return f"http://{host}:{port}"

def _fmt_currency(val: Any) -> str:
    try:
        f = float(val)
        return f"{f:,.0f} €".replace(",", ".")
    except Exception:
        return str(val)

def _fmt_de(val: Any, decimals: int = 1) -> str:
    try:
        f = float(val)
        return f"{f:.{decimals}f}".replace(".", ",")
    except Exception:
        return str(val)

def test_telegram_connection(bot_token: str, chat_id: str, base_url: Optional[str] = None) -> Dict[str, Any]:
    """
    Verifies the Telegram Bot token via getMe and dispatches a test message to the specified chat_id.
    """
    token = (bot_token or "").strip()
    cid = (chat_id or "").strip()

    if not token or not cid:
        return {
            "success": False,
            "error": "Bot-Token und Chat-ID müssen angegeben werden."
        }

    if not base_url:
        base_url = get_base_url()
    base_url = (base_url or "").rstrip("/")

    # 1. Verify Bot Token
    try:
        me_url = f"https://api.telegram.org/bot{token}/getMe"
        resp = requests.get(me_url, timeout=7.0)
        data = resp.json()
        if not data.get("ok"):
            desc = data.get("description", "Ungültiger Token")
            return {"success": False, "error": f"Telegram API Fehler: {desc}"}
        bot_user = data.get("result", {}).get("username", "Bot")
    except Exception as e:
        return {"success": False, "error": f"Verbindungsfehler zu Telegram: {str(e)}"}

    # 2. Dispatch Test Message
    url_hint = f"\n\n🔗 <b>Ziel-Adresse für KI-Audits:</b> <code>{base_url}</code>" if base_url else ""
    test_text = (
        "✅ <b>ImmoMetrica Deal-Alert System verbunden!</b>\n\n"
        f"Hallo! Dieser Chat ist nun mit deinem ImmoMetrica-Suchagenten verknüpft (Bot: @{bot_user}).\n"
        "Ab sofort erhältst du hier blitzschnelle Push-Alerts bei neuen Untermarktwert-Angeboten und Rendite-Objekten inklusive direktem Link zum KI-Audit und zum Original-Exposé."
        f"{url_hint}"
    )

    try:
        send_url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": cid,
            "text": test_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        resp = requests.post(send_url, json=payload, timeout=8.0)
        send_data = resp.json()
        if send_data.get("ok"):
            return {
                "success": True,
                "bot_username": bot_user,
                "message": f"Test-Nachricht erfolgreich an Chat-ID {cid} über @{bot_user} gesendet!"
            }
        else:
            desc = send_data.get("description", "Senden fehlgeschlagen")
            return {
                "success": False,
                "bot_username": bot_user,
                "error": f"Konnte keine Nachricht an Chat {cid} senden ({desc}). Hast du den Bot im Chat bereits gestartet (/start)?"
            }
    except Exception as e:
        return {"success": False, "error": f"Sende-Fehler: {str(e)}"}


def send_deal_telegram_alert(
    bot_token: str,
    chat_id: str,
    deal: Dict[str, Any],
    ai_verdict: str,
    profile_name: str = "Suchprofil",
    base_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Sends a formatted real estate deal push alert to Telegram.
    Includes direct link to the local or server ImmoMetrica AI Audit as well as the portal listing.
    """
    token = (bot_token or "").strip()
    cid = (chat_id or "").strip()

    if not token or not cid:
        return {"success": False, "error": "Keine Telegram-Zugangsdaten konfiguriert."}

    if not base_url:
        base_url = get_base_url()
    base_url = (base_url or "").rstrip("/")

    # Block test deals or unit test runs from ever sending live notifications to Telegram
    portal_url_lower = (deal.get("portal_url") or "").lower()
    title_lower = str(deal.get("title") or "").lower()
    is_test_deal = (
        "test-oneprovider" in portal_url_lower
        or "test-deal" in portal_url_lower
        or "/test" in portal_url_lower
        or "oneprovider test" in title_lower
        or "test deal" in title_lower
        or "test-deal" in title_lower
        or title_lower.startswith("test ")
        or title_lower.endswith(" test")
        or title_lower == "test"
        or os.getenv("TESTING") == "1"
    )
    if is_test_deal:
        logger.info(f"Skipping Telegram notification for test deal: '{title_lower}' ({portal_url_lower})")
        return {"success": True, "skipped_test_deal": True}

    city = html.escape(deal.get("city", "Deutschland"))
    title = html.escape(deal.get("title", "Immobilienangebot"))
    address = html.escape(deal.get("address", city))
    portal_url = deal.get("portal_url", "")
    safe_profile = html.escape(profile_name)
    property_id = deal.get("property_id") or deal.get("id")

    price = float(deal.get("purchase_price") or deal.get("price") or 0.0)
    sqm = float(deal.get("living_space") or 0.0)
    price_per_sqm = round(price / sqm) if sqm > 0 else 0
    gross_yield = float(deal.get("gross_yield") or 0.0)
    cashflow = float(deal.get("monthly_cashflow") or 0.0)
    rooms = deal.get("rooms", "k. A.")
    discount_pct = float(deal.get("market_deviation_percent") or deal.get("market_discount_pct") or 0.0)

    # Benchmark display
    benchmark_str = ""
    if discount_pct < -0.1:
        benchmark_str = f"<b>{discount_pct:+.1f}% unter Marktwert</b> 🔥"
    elif discount_pct > 0.1:
        benchmark_str = f"<b>{discount_pct:+.1f}% über Marktwert</b>"
    else:
        benchmark_str = "Im Marktwert-Schnitt"

    # Emoji alert header
    if discount_pct <= -15.0 or gross_yield >= 7.0:
        header = f"🚨 <b>TOP-DEAL ALERT: {city.upper()}</b>"
    else:
        header = f"🔔 <b>NEUER DEAL: {city}</b>"

    clean_ai_verdict = html.escape(ai_verdict or "Vorabprüfung: Keine gravierenden Risiken im ersten Textscan gefunden.")

    lines = [
        header,
        f"📁 <i>Suchauftrag: {safe_profile}</i>\n",
        f"🏢 <b>{title}</b>",
        f"📍 <i>{address}</i>\n",
        f"💰 <b>Kaufpreis:</b> {_fmt_currency(price)} ({_fmt_currency(price_per_sqm)}/m²)",
        f"📊 <b>Marktwert-Vergleich:</b> {benchmark_str}",
        f"📈 <b>Bruttorendite:</b> {_fmt_de(gross_yield, 2)}% | <b>Cashflow:</b> {_fmt_de(cashflow, 0)} €/Monat",
        f"📐 <b>Fläche:</b> {_fmt_de(sqm, 1)} m² | <b>Zimmer:</b> {rooms}\n",
        f"🧠 <b>KI-Vorabcheck & Risikobewertung:</b>",
        f"<i>{clean_ai_verdict}</i>\n",
    ]

    link_items = []
    if property_id and base_url:
        audit_url = f"{base_url}/deal/{property_id}?view=audit"
        link_items.append(f"🔬 <a href=\"{audit_url}\"><b>Direkt zum KI-Audit in ImmoMetrica</b></a>")

    if portal_url:
        link_items.append(f"🌐 <a href=\"{portal_url}\">Zum Original-Exposé aufrufen</a>")

    if link_items:
        lines.append("──────────────────────")
        lines.extend(link_items)

    message_text = "\n".join(lines)

    try:
        send_url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": cid,
            "text": message_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }
        resp = requests.post(send_url, json=payload, timeout=8.0)
        data = resp.json()
        if data.get("ok"):
            logger.info(f"Telegram deal alert successfully sent for '{title}' to chat {cid}")
            return {"success": True}
        else:
            err = data.get("description", "Send error")
            logger.warning(f"Telegram send error: {err}")
            return {"success": False, "error": err}
    except Exception as e:
        logger.error(f"Telegram exception: {e}")
        return {"success": False, "error": str(e)}


def send_system_telegram_alert(
    title: str,
    message: str,
    portal: Optional[str] = None,
    error_details: Optional[str] = None,
    target_url: Optional[str] = None,
    portal_statuses: Optional[Dict[str, Any]] = None,
    level: str = "warning",
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    base_url: Optional[str] = None
) -> Dict[str, Any]:
    """
    Sends an automated system notification to Telegram when a scraper failure,
    portal layout change, or bot protection restriction is detected.
    Clearly shows which portal failed, what exact page/URL was tested, and an overview of all portals.
    """
    if os.getenv("TESTING") == "1":
        return {"success": True, "skipped_testing": True}

    from app.database import get_system_setting

    token = (bot_token or get_system_setting("telegram_bot_token") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    cid = (chat_id or get_system_setting("telegram_chat_id") or os.getenv("TELEGRAM_CHAT_ID") or "").strip()

    if not token or not cid:
        logger.info("Telegram alert skipped: bot_token or chat_id not configured.")
        return {"success": False, "error": "Telegram Zugangsdaten nicht hinterlegt."}

    if not base_url:
        base_url = get_base_url()
    base_url = (base_url or "").rstrip("/")

    icon = "🚨" if level == "critical" else "⚠️"
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M")

    lines = [
        f"{icon} <b>System-Warnung: ImmoMetrica Scraper</b>",
        f"<b>{html.escape(title)}</b>",
        ""
    ]

    # Clearly show the failing portal(s)
    if portal:
        lines.append(f"❌ <b>Betroffenes Portal:</b> <code>{html.escape(portal)}</code>")

    # Clearly show the affected URL/page
    if target_url:
        safe_url = html.escape(target_url)
        if target_url.startswith("http://") or target_url.startswith("https://"):
            lines.append(f"🔗 <b>Betroffene Seite / URL:</b> <a href=\"{safe_url}\">{safe_url}</a>")
        else:
            lines.append(f"🔗 <b>Betroffene Seite / URL:</b> <code>{safe_url}</code>")

    # Detailed error cause
    lines.append(f"📋 <b>Fehlerursache:</b> {html.escape(message)}")

    if error_details and error_details != message:
        lines.append(f"🔍 <b>Details:</b> <code>{html.escape(str(error_details)[:300])}</code>")

    # Clean overview breakdown of all portals if provided
    if portal_statuses and isinstance(portal_statuses, dict):
        lines.append("")
        lines.append("📊 <b>Status aller Portale im Check:</b>")
        for p_name, p_data in portal_statuses.items():
            st = p_data.get("status", "unknown")
            msg_snippet = p_data.get("message", "")
            if len(msg_snippet) > 65:
                msg_snippet = msg_snippet[:62] + "..."
            if st == "healthy":
                badge = "✅ OK"
            elif st == "degraded":
                badge = "⚠️ Eingeschränkt"
            else:
                badge = "❌ Fehler"
            lines.append(f"  • <b>{p_name}:</b> {badge} (<i>{html.escape(msg_snippet)}</i>)")

    lines.append("")
    lines.append(f"⏰ <b>Zeitpunkt:</b> {now_str}")

    if base_url:
        lines.append("──────────────────────")
        lines.append(f"🔗 <a href=\"{base_url}\"><b>ImmoMetrica Dashboard öffnen</b></a>")

    payload_text = "\n".join(lines)

    try:
        send_url = f"https://api.telegram.org/bot{token}/sendMessage"
        resp = requests.post(send_url, json={
            "chat_id": cid,
            "text": payload_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }, timeout=8.0)
        res_data = resp.json()
        if res_data.get("ok"):
            logger.info(f"System alert successfully sent to Telegram chat {cid}: {title}")
            return {"success": True}
        else:
            err = res_data.get("description", "Unknown Telegram API error")
            logger.warning(f"Failed to send system alert to Telegram: {err}")
            return {"success": False, "error": err}
    except Exception as e:
        logger.error(f"Error dispatching system Telegram alert: {e}")
        return {"success": False, "error": str(e)}
