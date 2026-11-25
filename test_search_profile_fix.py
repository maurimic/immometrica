import sys
import os
import tempfile
import unittest

_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_TMP_DIR.name, "test_search_profile_fix.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.database import (  # noqa: E402
    init_db,
    save_search_profile,
    get_search_profile_by_id,
    update_search_profile,
    get_search_profiles,
    delete_search_profile
)
from app.auth import hash_session_token, get_auth_config  # noqa: E402

class TestSearchProfiles(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)
        # Authenticate test client with session cookie
        cfg = get_auth_config()
        token = hash_session_token(cfg["username"], cfg["password"], cfg["secret"])
        cls.client.cookies.set("immo_session", token)

    @classmethod
    def tearDownClass(cls):
        try:
            _TMP_DIR.cleanup()
        except Exception:
            pass

    def test_01_user_screenshot_payload_direct_db(self):
        """Test exact payload from user screenshot with empty string numeric fields."""
        payload = {
            "name": "fa",
            "city": "Dortmund",
            "postal_code": "",
            "radius_km": 25,
            "max_price": "",
            "max_price_per_sqm": "",
            "min_yield": 6,
            "min_discount_pct": 10,
            "min_rooms": 1,
            "min_living_space": 40,
            "property_type": "all",
            "search_query": "",
            "is_active": True
        }
        profile_id = save_search_profile(payload)
        self.assertIsInstance(profile_id, int)
        self.assertGreater(profile_id, 0)

        profile = get_search_profile_by_id(profile_id)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["name"], "fa")
        self.assertEqual(profile["city"], "Dortmund")
        self.assertEqual(profile["radius_km"], 25)
        self.assertEqual(profile["max_price"], 0.0)
        self.assertEqual(profile["max_price_per_sqm"], 0.0)
        self.assertEqual(profile["min_yield"], 6.0)
        self.assertEqual(profile["min_discount_pct"], 10.0)
        self.assertEqual(profile["min_living_space"], 40.0)
        self.assertEqual(profile["is_active"], 1)

        # Cleanup
        delete_search_profile(profile_id)

    def test_02_api_post_profiles_user_payload(self):
        """Test POST /api/alerts/profiles with exact frontend payload."""
        payload = {
            "name": "fa Dortmund Test",
            "city": "Dortmund",
            "postal_code": "",
            "radius_km": "25",
            "max_price": "",
            "max_price_per_sqm": "",
            "min_yield": "6",
            "min_discount_pct": "10",
            "min_rooms": "1",
            "min_living_space": "40",
            "property_type": "all",
            "search_query": "",
            "is_active": True
        }
        resp = self.client.post("/api/alerts/profiles", json=payload)
        self.assertEqual(resp.status_code, 200, f"Expected 200, got {resp.status_code}: {resp.text}")
        data = resp.json()
        self.assertTrue(data.get("success"))
        created = data.get("profile")
        self.assertIsNotNone(created)
        self.assertEqual(created["name"], "fa Dortmund Test")
        self.assertEqual(created["city"], "Dortmund")
        self.assertEqual(created["radius_km"], 25)
        self.assertEqual(created["min_yield"], 6.0)

        profile_id = created["id"]

        # Test PUT /api/alerts/profiles/{id} with empty fields
        update_payload = {
            "max_price": "",
            "min_yield": "7.5",
            "is_active": False
        }
        resp_update = self.client.put(f"/api/alerts/profiles/{profile_id}", json=update_payload)
        self.assertEqual(resp_update.status_code, 200)
        updated_data = resp_update.json()["profile"]
        self.assertEqual(updated_data["min_yield"], 7.5)
        self.assertEqual(updated_data["max_price"], 0.0)
        self.assertEqual(updated_data["is_active"], 0)

        # Test GET /api/alerts/profiles
        resp_list = self.client.get("/api/alerts/profiles")
        self.assertEqual(resp_list.status_code, 200)
        profiles = resp_list.json().get("profiles", [])
        self.assertTrue(any(p["id"] == profile_id for p in profiles))

        # Test DELETE /api/alerts/profiles/{id}
        resp_del = self.client.delete(f"/api/alerts/profiles/{profile_id}")
        self.assertEqual(resp_del.status_code, 200)

        # Verify deletion
        self.assertIsNone(get_search_profile_by_id(profile_id))

    def test_03_validation_errors(self):
        """Test proper 400 validation when required fields are missing."""
        # Missing name
        resp = self.client.post("/api/alerts/profiles", json={"name": "", "city": "Dortmund"})
        self.assertEqual(resp.status_code, 400)

        # Missing city
        resp = self.client.post("/api/alerts/profiles", json={"name": "Test", "city": ""})
        self.assertEqual(resp.status_code, 400)

if __name__ == "__main__":
    unittest.main()
