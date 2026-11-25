import unittest
from fastapi.testclient import TestClient

from app.main import app
from app.database import (
    record_deal_alert,
    get_deal_alerts,
    delete_deal_alert,
    clear_deal_alerts
)
from app.auth import hash_session_token, get_auth_config

class TestDealAlertsDeletion(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cfg = get_auth_config()
        token = hash_session_token(cfg["username"], cfg["password"], cfg["secret"])
        cls.client.cookies.set("immo_session", token)

    def setUp(self):
        from app.database import get_connection
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute("INSERT OR IGNORE INTO search_profiles (id, name, city) VALUES (999, 'Test Profile 999', 'Dortmund')")
            c.execute("INSERT OR IGNORE INTO search_profiles (id, name, city) VALUES (888, 'Test Profile 888', 'Essen')")
            conn.commit()
        finally:
            conn.close()
        clear_deal_alerts(profile_id=999)
        clear_deal_alerts(profile_id=888)
        # Create sample test alerts
        self.alert_id_1 = record_deal_alert({
            "profile_id": 999,
            "property_id": None,
            "portal_url": "https://www.kleinanzeigen.de/s-anzeige/test-alert-1",
            "title": "Testwohnung 1",
            "city": "Dortmund",
            "price": 120000,
            "living_space": 60,
            "price_per_sqm": 2000,
            "gross_yield": 6.5,
            "market_discount_pct": -12.0,
            "monthly_cashflow": 150,
            "ai_verdict": "Solider Test-Deal",
            "has_critical_risks": False,
            "sent_to_telegram": True,
            "telegram_error": None
        })

        self.alert_id_2 = record_deal_alert({
            "profile_id": 999,
            "property_id": None,
            "portal_url": "https://www.kleinanzeigen.de/s-anzeige/test-alert-2",
            "title": "Testwohnung 2",
            "city": "Bochum",
            "price": 95000,
            "living_space": 45,
            "price_per_sqm": 2111,
            "gross_yield": 7.2,
            "market_discount_pct": -15.0,
            "monthly_cashflow": 210,
            "ai_verdict": "Renditestarker Test-Deal",
            "has_critical_risks": False,
            "sent_to_telegram": False,
            "telegram_error": "Chat not found"
        })

        self.alert_id_3 = record_deal_alert({
            "profile_id": 888,
            "property_id": None,
            "portal_url": "https://www.kleinanzeigen.de/s-anzeige/test-alert-3",
            "title": "Testwohnung 3 (Anderes Profil)",
            "city": "Essen",
            "price": 140000,
            "living_space": 70,
            "price_per_sqm": 2000,
            "gross_yield": 6.0,
            "market_discount_pct": -10.0,
            "monthly_cashflow": 80,
            "ai_verdict": "Guter Schnitt",
            "has_critical_risks": False,
            "sent_to_telegram": True,
            "telegram_error": None
        })

    def tearDown(self):
        # Clean up any leftover test alerts & temporary test profiles
        clear_deal_alerts(profile_id=999)
        clear_deal_alerts(profile_id=888)
        from app.database import get_connection
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute("DELETE FROM search_profiles WHERE id IN (999, 888)")
            conn.commit()
        finally:
            conn.close()

    def test_01_delete_single_alert_api(self):
        """Test DELETE /api/alerts/feed/{id}."""
        resp = self.client.delete(f"/api/alerts/feed/{self.alert_id_1}")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))

        # Verify it no longer exists
        alerts = get_deal_alerts(profile_id=999)
        alert_ids = [a["id"] for a in alerts]
        self.assertNotIn(self.alert_id_1, alert_ids)
        self.assertIn(self.alert_id_2, alert_ids)

    def test_02_delete_single_alert_not_found(self):
        """Test deleting non-existent alert returns 404."""
        resp = self.client.delete("/api/alerts/feed/999999999")
        self.assertEqual(resp.status_code, 404)

    def test_03_clear_alerts_by_profile_api(self):
        """Test DELETE /api/alerts/feed?profile_id=999 only clears profile 999."""
        resp = self.client.delete("/api/alerts/feed?profile_id=999")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertGreaterEqual(data.get("deleted_count"), 2)

        # Profile 999 should be empty
        self.assertEqual(len(get_deal_alerts(profile_id=999)), 0)

        # Profile 888 should still exist
        self.assertGreaterEqual(len(get_deal_alerts(profile_id=888)), 1)

    def test_04_clear_all_alerts_api(self):
        """Test DELETE /api/alerts/feed without query param clears everything."""
        resp = self.client.delete("/api/alerts/feed")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("success"))

        # All alerts should be empty
        self.assertEqual(len(get_deal_alerts(limit=100)), 0)

if __name__ == "__main__":
    unittest.main()
