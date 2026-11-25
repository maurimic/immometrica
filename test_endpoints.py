import os
import tempfile
import unittest
from unittest.mock import patch

_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_TMP_DIR.name, "test_endpoints.db")
os.environ["TESTING"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
from app.auth import get_auth_config  # noqa: E402
from app.database import get_connection, init_db  # noqa: E402
from app.main import app  # noqa: E402


class TestCoreApiEndpoints(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cfg = get_auth_config()
        cls.unauth_client = TestClient(app)
        cls.client = TestClient(app)
        login_res = cls.client.post(
            "/api/auth/login",
            json={"username": cfg["username"], "password": cfg["password"]},
        )
        assert login_res.status_code == 200, f"Login failed: {login_res.text}"

        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO properties (
                    title, address, postal_code, city, state, property_type,
                    living_space, purchase_price, current_rent, expected_rent,
                    gross_yield, monthly_cashflow, is_online, source_portal, portal_url,
                    description, image_url
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Isolierte Test-ETW Dortmund",
                    "Musterstraße 10, 44149 Dortmund",
                    "44149",
                    "Dortmund",
                    "Nordrhein-Westfalen",
                    "Eigentumswohnung",
                    62.0,
                    140000.0,
                    680.0,
                    680.0,
                    5.83,
                    85.0,
                    1,
                    "Kleinanzeigen",
                    "https://www.kleinanzeigen.de/s-anzeige/test-etw-isolated/123456789-196-1",
                    "Vermietete Eigentumswohnung in Dortmund, Kaltmiete 680 EUR monatlich, kein Hausgeld-Rückstand, keine Sonderumlagen.",
                    "https://example.com/test.jpg",
                ),
            )
            cls.test_prop_id = c.lastrowid
            conn.commit()
        finally:
            conn.close()

    @classmethod
    def tearDownClass(cls):
        try:
            _TMP_DIR.cleanup()
        except Exception:
            pass

    def test_01_auth_barrier_and_root(self):
        r_unauth = self.unauth_client.get("/")
        self.assertEqual(r_unauth.status_code, 200)
        self.assertIn("Anmelden", r_unauth.text)

        r_api = self.unauth_client.get("/api/properties")
        self.assertEqual(r_api.status_code, 401)

        r_auth = self.client.get("/")
        self.assertEqual(r_auth.status_code, 200)

    def test_02_properties_geo_and_calculator(self):
        r_props = self.client.get("/api/properties?online_only=true")
        self.assertEqual(r_props.status_code, 200)
        self.assertGreaterEqual(r_props.json()["total"], 1)

        r_geo = self.client.get("/api/calculator/resolve-geo?postal_code=80331&city=M%C3%BCnchen")
        self.assertEqual(r_geo.status_code, 200)
        self.assertEqual(r_geo.json()["state"], "Bayern")
        self.assertEqual(r_geo.json()["tax_rate"], 3.5)

        r_cf = self.client.post(
            "/api/calculator/calculate",
            json={"price": 250000, "rent": 1200, "postal_code": "04315", "city": "Leipzig", "equity_value": 20},
        )
        self.assertEqual(r_cf.status_code, 200)
        self.assertEqual(r_cf.json()["state"], "Sachsen")
        self.assertEqual(r_cf.json()["transfer_tax_rate"], 5.5)

    def test_03_pipeline_export_and_mcp_endpoints(self):
        r_board = self.client.get("/api/pipeline/board")
        self.assertEqual(r_board.status_code, 200)
        self.assertGreater(len(r_board.json()), 0)

        r_csv = self.client.get("/api/export/csv")
        self.assertEqual(r_csv.status_code, 200)
        self.assertIn("Adresse;Ort;PLZ", r_csv.text)

        r_tools = self.client.get("/mcp/tools")
        self.assertEqual(r_tools.status_code, 200)
        tools = r_tools.json()["tools"]
        self.assertEqual(len(tools), 10)
        self.assertTrue(any(t["name"] == "audit_property_ai" for t in tools))

    def test_04_audit_offer_and_mocked_availability(self):
        with patch(
            "app.routers.properties.check_portal_url_live",
            return_value={
                "is_online": True,
                "availability_status": "online",
                "status_code": 200,
                "status_message": "Mock Online OK",
                "last_checked_at": "2026-11-25 12:00:00",
                "live_search_url": "",
            },
        ):
            r_avail = self.client.post(f"/api/properties/{self.test_prop_id}/check-availability")
            self.assertEqual(r_avail.status_code, 200)
            self.assertTrue(r_avail.json()["is_online"])

        r_audit = self.client.post(f"/api/properties/{self.test_prop_id}/ai-audit?force_refresh=false")
        self.assertEqual(r_audit.status_code, 200)
        audit = r_audit.json()["ai_audit"]
        self.assertIn("score", audit)

        r_offer = self.client.post("/mcp/call", json={"name": "generate_purchase_offer", "arguments": {"property_id": self.test_prop_id}})
        self.assertEqual(r_offer.status_code, 200)
        offer = r_offer.json()["result"]
        self.assertIn("document_request_subject", offer)
        self.assertGreater(len(offer["letter_of_intent_text"]), 100)


if __name__ == "__main__":
    unittest.main()
