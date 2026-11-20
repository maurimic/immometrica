import os
import unittest
from unittest.mock import patch, MagicMock

from app.telegram_notifier import (
    get_base_url,
    send_deal_telegram_alert,
    test_telegram_connection
)
from app.database import set_system_setting

class TestTelegramAuditLink(unittest.TestCase):
    def test_01_get_base_url_default(self):
        """Test default base url returns 127.0.0.1:8000 locally."""
        with patch.dict(os.environ, {"APP_BASE_URL": "", "BASE_URL": "", "PUBLIC_URL": ""}):
            set_system_setting("app_base_url", "")
            base_url = get_base_url()
            self.assertTrue(base_url.startswith("http://127.0.0.1") or "localhost" in base_url or ":8000" in base_url)

    def test_02_get_base_url_env_override(self):
        """Test APP_BASE_URL environment variable override."""
        with patch.dict(os.environ, {"APP_BASE_URL": "https://immofinder-poggel-micciche.dedyn.io"}):
            base_url = get_base_url()
            self.assertEqual(base_url, "https://immofinder-poggel-micciche.dedyn.io")

    def test_03_get_base_url_db_setting_override(self):
        """Test database app_base_url setting override when no env var is set."""
        with patch.dict(os.environ, {"APP_BASE_URL": "", "BASE_URL": "", "PUBLIC_URL": ""}):
            set_system_setting("app_base_url", "https://mein-server.de")
            base_url = get_base_url()
            self.assertEqual(base_url, "https://mein-server.de")
            # cleanup
            set_system_setting("app_base_url", "")

    @patch("requests.post")
    def test_04_telegram_alert_message_contains_audit_link_local(self, mock_post):
        """Test that send_deal_telegram_alert formats both direct audit link and portal link for local environment."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_post.return_value = mock_resp

        deal = {
            "property_id": 42,
            "title": "Schöne 3-Zimmer Eigentumswohnung",
            "city": "Dortmund",
            "address": "Kaiserstraße 12, Dortmund",
            "purchase_price": 145000,
            "living_space": 75,
            "rooms": 3,
            "gross_yield": 6.8,
            "monthly_cashflow": 185,
            "market_deviation_percent": -14.5,
            "portal_url": "https://www.kleinanzeigen.de/s-anzeige/schoene-3-zimmer-wohnung/12345"
        }

        res = send_deal_telegram_alert(
            bot_token="dummy_token_123",
            chat_id="99887766",
            deal=deal,
            ai_verdict="Top-Zustand, kein Sanierungsstau.",
            profile_name="Dortmund Rendite > 6%",
            base_url="http://127.0.0.1:8000"
        )

        self.assertTrue(res.get("success"))
        mock_post.assert_called_once()
        payload = mock_post.call_args[1]["json"]
        msg_text = payload["text"]

        # 1. Must contain direct link to ImmoMetrica AI Audit with proper deal ID and view
        expected_audit_link = 'href="http://127.0.0.1:8000/deal/42?view=audit"'
        self.assertIn(expected_audit_link, msg_text)
        self.assertIn("Direkt zum KI-Audit in ImmoMetrica", msg_text)

        # 2. Must contain link to original exposé
        expected_portal_link = 'href="https://www.kleinanzeigen.de/s-anzeige/schoene-3-zimmer-wohnung/12345"'
        self.assertIn(expected_portal_link, msg_text)
        self.assertIn("Zum Original-Exposé aufrufen", msg_text)

    @patch("requests.post")
    def test_05_telegram_alert_message_contains_audit_link_server(self, mock_post):
        """Test that send_deal_telegram_alert formats audit link pointing to server domain when on server."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_post.return_value = mock_resp

        deal = {
            "property_id": 99,
            "title": "Gepflegtes MFH",
            "city": "Bochum",
            "address": "Herner Str. 45, Bochum",
            "purchase_price": 420000,
            "living_space": 240,
            "rooms": 10,
            "gross_yield": 7.4,
            "monthly_cashflow": 650,
            "market_deviation_percent": -18.0,
            "portal_url": "https://www.immobilienscout24.de/expose/987654"
        }

        server_domain = "https://immofinder-poggel-micciche.dedyn.io"
        res = send_deal_telegram_alert(
            bot_token="dummy_token_123",
            chat_id="99887766",
            deal=deal,
            ai_verdict="Hervorragender Cashflow, voll vermietet.",
            profile_name="Bochum MFH",
            base_url=server_domain
        )

        self.assertTrue(res.get("success"))
        payload = mock_post.call_args[1]["json"]
        msg_text = payload["text"]

        # Server audit link
        expected_audit_link = f'href="{server_domain}/deal/99?view=audit"'
        self.assertIn(expected_audit_link, msg_text)
        self.assertIn("Direkt zum KI-Audit in ImmoMetrica", msg_text)

        # Original portal link
        self.assertIn('href="https://www.immobilienscout24.de/expose/987654"', msg_text)

if __name__ == "__main__":
    unittest.main()
