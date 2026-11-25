"""
Unit & Integration Tests for Portal Health & Canary Monitoring System.
Verifies canary checks, startpage alert state management, incident recording,
and Telegram alerting logic.
"""

import os
import tempfile
import unittest
from unittest.mock import patch, MagicMock

_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_TMP_DIR.name, "test_canary.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.auth import hash_session_token, get_auth_config  # noqa: E402
from app.portal_health import (  # noqa: E402
    run_canary_checks,
    get_portal_health_status,
    record_portal_incident,
    dismiss_portal_alert
)
from app.telegram_notifier import send_system_telegram_alert  # noqa: E402
from app.database import init_db, get_system_setting, set_system_setting  # noqa: E402


class TestPortalHealthCanary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)
        cfg = get_auth_config()
        token = hash_session_token(cfg["username"], cfg["password"], cfg["secret"])
        cls.client.cookies.set("immo_session", token)

    @classmethod
    def tearDownClass(cls):
        try:
            _TMP_DIR.cleanup()
        except Exception:
            pass

    def setUp(self):
        # Reset alert settings before each test
        set_system_setting("portal_alert_active", "0")
        set_system_setting("portal_alert_message", "")
        set_system_setting("portal_health_overall", "healthy")
        set_system_setting("telegram_last_canary_alert", "")
        set_system_setting("telegram_last_alert_immowelt", "")
        set_system_setting("telegram_last_alert_kleinanzeigen", "")
        set_system_setting("telegram_last_alert_immoscout24", "")

    def test_get_portal_health_status(self):
        status = get_portal_health_status()
        self.assertIn("overall", status)
        self.assertIn("portals", status)
        self.assertIn("alert", status)

    @patch("app.scrapers.kleinanzeigen.scrape_kleinanzeigen_deals")
    @patch("app.scrapers.immoscout.search_immoscout_deals")
    @patch("app.scrapers.immowelt.fetch_immowelt_expose")
    def test_run_canary_checks_all_healthy(self, mock_iw, mock_is, mock_ka):
        mock_ka.return_value = [{"title": "KA Wohnung", "purchase_price": 120000.0, "living_space": 60.0}]
        mock_is.return_value = [{"title": "IS24 Wohnung", "purchase_price": 150000.0, "living_space": 70.0}]
        mock_iw.return_value = {"success": True, "price": 75000.0, "living_space": 59.0, "city": "Hagen"}

        res = run_canary_checks()
        self.assertEqual(res["overall"], "healthy")
        self.assertFalse(res["alert_active"])
        self.assertEqual(res["portals"]["Kleinanzeigen"]["status"], "healthy")
        self.assertEqual(res["portals"]["ImmoScout24"]["status"], "healthy")
        self.assertEqual(res["portals"]["Immowelt"]["status"], "healthy")

    @patch("app.scrapers.kleinanzeigen.scrape_kleinanzeigen_deals")
    @patch("app.scrapers.immoscout.search_immoscout_deals")
    @patch("app.scrapers.immowelt.fetch_immowelt_expose")
    @patch("app.portal_health.send_system_telegram_alert")
    def test_run_canary_checks_immowelt_failing_triggers_alert_and_telegram(self, mock_tg, mock_iw, mock_is, mock_ka):
        mock_ka.return_value = [{"title": "KA Wohnung", "purchase_price": 120000.0, "living_space": 60.0}]
        mock_is.return_value = [{"title": "IS24 Wohnung", "purchase_price": 150000.0, "living_space": 70.0}]
        # Immowelt fails due to format change (e.g. price and sqm not found)
        mock_iw.return_value = {"success": False, "status_code": 422, "error": "Schema-Änderung: Kaufpreis nicht auffindbar"}

        res = run_canary_checks(force_notify=True)
        self.assertEqual(res["overall"], "critical")
        self.assertTrue(res["alert_active"])
        self.assertIn("Immowelt", res["alert_message"])
        mock_tg.assert_called_once()
        call_kwargs = mock_tg.call_args[1]
        # Clearly identify ONLY the failing portal
        self.assertEqual(call_kwargs["portal"], "Immowelt")
        # Clearly provide the tested failing URL
        self.assertIn("immowelt.de/expose", call_kwargs.get("target_url", ""))
        self.assertIn("Immowelt", call_kwargs.get("portal_statuses", {}))

    def test_record_portal_incident_and_dismiss(self):
        with patch("app.portal_health.send_system_telegram_alert") as mock_tg:
            record_portal_incident(
                portal="Immowelt",
                reason="Pflichtfelder Kaufpreis/Wohnfläche fehlen",
                details="HTTP 200 aber React-State geändert"
            )
            mock_tg.assert_called_once()

            status = get_portal_health_status()
            self.assertTrue(status["alert"]["active"])
            self.assertIn("Immowelt", status["alert"]["title"])

            dismiss_portal_alert()
            status_after = get_portal_health_status()
            self.assertFalse(status_after["alert"]["active"])

    def test_portal_health_api_endpoints(self):
        # 1. GET /api/portal-health
        resp_get = self.client.get("/api/portal-health")
        self.assertEqual(resp_get.status_code, 200)
        data = resp_get.json()
        self.assertIn("overall", data)
        self.assertIn("alert", data)

        # 2. POST /api/portal-health/dismiss
        resp_dismiss = self.client.post("/api/portal-health/dismiss")
        self.assertEqual(resp_dismiss.status_code, 200)
        self.assertTrue(resp_dismiss.json().get("success"))

    @patch("requests.post")
    def test_telegram_system_alert_payload_formatting(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_post.return_value = mock_resp

        res = send_system_telegram_alert(
            title="Immowelt Layout geändert",
            message="Kaufpreise können nicht ausgelesen werden",
            portal="Immowelt",
            target_url="https://www.immowelt.de/expose/test-123",
            error_details="Key 'hardFacts' missing",
            portal_statuses={
                "Kleinanzeigen": {"status": "healthy", "message": "3 Inserate ok"},
                "ImmoScout24": {"status": "healthy", "message": "3 Inserate ok"},
                "Immowelt": {"status": "degraded", "message": "HTTP 403 Bot-Schutz"}
            },
            bot_token="123456:ABC-DEF",
            chat_id="987654321",
            base_url="https://immometrica.local"
        )
        self.assertTrue(res["success"])
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        payload = call_kwargs["json"]
        self.assertEqual(payload["chat_id"], "987654321")
        self.assertIn("Immowelt", payload["text"])
        self.assertIn("https://www.immowelt.de/expose/test-123", payload["text"])
        self.assertIn("Status aller Portale im Check", payload["text"])
        self.assertIn("https://immometrica.local", payload["text"])


if __name__ == "__main__":
    unittest.main()
