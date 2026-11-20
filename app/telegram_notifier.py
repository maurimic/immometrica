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

logger = logging.getLogger("immometrica.telegram")

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

def test_telegram_connection(bot_token: str, chat_id: str) -> Dict[str, Any]:
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
    test_text = (
        "✅ <b>ImmoMetrica Deal-Alert System verbunden!</b>\n\n"
        f"Hallo! Dieser Chat ist nun mit deinem ImmoMetrica-Suchagenten verknüpft (Bot: @{bot_user}).\n"
        "Ab sofort erhältst du hier blitzschnelle Push-Alerts bei neuen Untermarktwert-Angeboten und Rendite-Objekten inklusive KI-Vorabcheck."
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
    profile_name: str = "Suchprofil"
) -> Dict[str, Any]:
    """
    Sends a formatted real estate deal push alert to Telegram.
    """
    token = (bot_token or "").strip()
    cid = (chat_id or "").strip()

    if not token or not cid:
        return {"success": False, "error": "Keine Telegram-Zugangsdaten konfiguriert."}

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

    if portal_url:
        lines.append(f"🔗 <a href=\"{portal_url}\">Zum Original-Exposé aufrufen</a>")

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
