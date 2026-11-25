import json
import os
import tempfile
import unittest

_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_TMP_DIR.name, "test_calc_afa.db")
os.environ["TESTING"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
from app.auth import COOKIE_NAME, get_auth_config, hash_session_token  # noqa: E402
from app.database import init_db  # noqa: E402
from app.main import app  # noqa: E402


class TestCalculatorAfaEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)
        cfg = get_auth_config()
        token = hash_session_token(cfg["username"], cfg["password"], cfg["secret"])
        cls.client.cookies.set(COOKIE_NAME, token)

    @classmethod
    def tearDownClass(cls):
        try:
            _TMP_DIR.cleanup()
        except Exception:
            pass

    def test_calculator_afa_endpoint(self):
        payload_standard = {
            "price": 150000,
            "rent": 750,
            "living_space": 65,
            "include_afa": True,
            "building_share_percent": 80,
            "depreciation_years": 50,
            "marginal_tax_rate": 42,
            "additional_costs": 45,
            "interest_rate": 3.6,
            "repayment_rate": 2.0,
            "equity_value": 20,
            "equity_type": "percent",
        }
        r1 = self.client.post("/api/calculator/calculate", json=payload_standard)
        self.assertEqual(r1.status_code, 200)
        res = r1.json()
        self.assertTrue(res["include_afa"])
        self.assertEqual(res["afa_rate_percent"], 2.0)
        self.assertEqual(res["depreciation_years"], 50.0)

        payload_gutachten = dict(payload_standard, depreciation_years=25)
        r2 = self.client.post("/api/calculator/calculate", json=payload_gutachten)
        self.assertEqual(r2.status_code, 200)
        res2 = r2.json()
        self.assertEqual(res2["afa_rate_percent"], 4.0)
        self.assertAlmostEqual(res2["annual_afa"], res["annual_afa"] * 2, places=2)
        self.assertGreater(res2["monthly_cashflow_after_tax"], res["monthly_cashflow_after_tax"])


if __name__ == "__main__":
    unittest.main()
