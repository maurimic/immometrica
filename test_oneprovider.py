import os
import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from app.main import app
from app.ai_analyst import call_llm_text_analysis
from app.image_analyst import call_gemini_vision, classify_extra_photos_with_gemini
from app.search_agent import execute_search_profile
from app.database import get_system_setting, set_system_setting

client = TestClient(app)

class TestOneProviderIntegration(unittest.TestCase):
    def setUp(self):
        os.environ["TESTING"] = "1"
        self.orig_key = get_system_setting("gemini_api_key")
        self.orig_one_key = get_system_setting("oneprovider_api_key")
        self.orig_gem_key = get_system_setting("google_gemini_api_key")
        self.orig_prov = get_system_setting("active_ai_provider")
        self.orig_stats = get_system_setting("crawler_last_stats")

    def tearDown(self):
        os.environ.pop("TESTING", None)
        if self.orig_key is not None:
            set_system_setting("gemini_api_key", self.orig_key)
            os.environ["GEMINI_API_KEY"] = self.orig_key
        if self.orig_one_key is not None:
            set_system_setting("oneprovider_api_key", self.orig_one_key)
            os.environ["ONEPROVIDER_API_KEY"] = self.orig_one_key
        if self.orig_gem_key is not None:
            set_system_setting("google_gemini_api_key", self.orig_gem_key)
        if self.orig_prov is not None:
            set_system_setting("active_ai_provider", self.orig_prov)
        if self.orig_stats is not None:
            set_system_setting("crawler_last_stats", self.orig_stats)
        else:
            from app.database import delete_system_setting
            delete_system_setting("crawler_last_stats")

        # Clean up any test deals, alerts, or seen records created during tests
        from app.database import get_connection
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM properties WHERE portal_url LIKE '%test-oneprovider%' OR title LIKE '%OneProvider Test%'")
        c.execute("DELETE FROM deal_alerts WHERE portal_url LIKE '%test-oneprovider%' OR title LIKE '%OneProvider Test%'")
        c.execute("DELETE FROM seen_listings WHERE portal_url LIKE '%test-oneprovider%'")
        conn.commit()
        conn.close()

    def test_01_save_and_verify_oneprovider_key(self):
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"data": [{"id": "claude-haiku-5-5"}]}
            mock_get.return_value = mock_resp

            res = client.post("/api/settings/gemini-key", json={"api_key": "sk-live1234567890abcdef"})
            data = res.json()
            self.assertTrue(data.get("success"))
            self.assertTrue(data.get("is_oneprovider"))
            self.assertEqual(data.get("provider"), "OneProvider Dev AI")
            self.assertIn("sk-li", data.get("masked_key"))

            # Verify strictly sent Authorization header
            called_headers = mock_get.call_args[1].get("headers", {})
            self.assertEqual(called_headers.get("Authorization"), "Bearer sk-live1234567890abcdef")
            self.assertNotIn("x-api-key", called_headers)

    def test_02_balance_endpoint(self):
        # Set sk- key
        set_system_setting("oneprovider_api_key", "sk-live1234567890abcdef")
        set_system_setting("gemini_api_key", "sk-live1234567890abcdef")
        with patch("requests.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"balance": 18.75, "currency": "USD"}
            mock_get.return_value = mock_resp

            res = client.get("/api/settings/oneprovider-balance")
            data = res.json()
            self.assertTrue(data.get("success"))
            self.assertEqual(data.get("balance"), 18.75)
            self.assertEqual(data.get("formatted"), "18.75 $")

            # Check header
            called_headers = mock_get.call_args[1].get("headers", {})
            self.assertEqual(called_headers.get("Authorization"), "Bearer sk-live1234567890abcdef")
            self.assertNotIn("x-api-key", called_headers)

    def test_03_text_analysis_oneprovider(self):
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{
                    "message": {
                        "content": '{"score": 9.0, "summary": "Top Immobilie", "is_package_deal": false}'
                    }
                }],
                "usage": {
                    "prompt_tokens": 1200,
                    "completion_tokens": 350,
                    "total_tokens": 1550
                }
            }
            mock_post.return_value = mock_resp

            res = call_llm_text_analysis(
                "Schöne 3-Zimmer Eigentumswohnung in Köln mit Balkon und Einbauküche." * 4,
                {"title": "Wohnung", "city": "Köln"},
                api_key="sk-live1234567890abcdef"
            )
            self.assertIsNotNone(res)
            self.assertIn("OneProvider claude-haiku-5-5", res.get("ai_provider"))
            self.assertEqual(res.get("token_usage", {}).get("prompt_tokens"), 1200)
            self.assertEqual(res.get("token_usage", {}).get("candidates_tokens"), 350)
            self.assertEqual(res.get("token_usage", {}).get("total_tokens"), 1550)

            # Check post call
            call_kwargs = mock_post.call_args[1]
            self.assertEqual(mock_post.call_args[0][0], "https://api.oneprovider.dev/v1/chat/completions")
            self.assertEqual(call_kwargs["headers"].get("Authorization"), "Bearer sk-live1234567890abcdef")
            self.assertNotIn("x-api-key", call_kwargs["headers"])
            self.assertEqual(call_kwargs["json"]["model"], "claude-haiku-5-5")

    def test_04_vision_analysis_oneprovider(self):
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{
                    "message": {
                        "content": '{"overall_visual_score": 8.0, "condition_rating": "GEPFLEGT", "condition_label": "🟢 Gepflegt", "categories": {}, "detected_findings": [], "visual_capex_items": []}'
                    }
                }],
                "usage": {
                    "prompt_tokens": 2500,
                    "completion_tokens": 500,
                    "total_tokens": 3000
                }
            }
            mock_post.return_value = mock_resp

            res = call_gemini_vision(
                [("image/jpeg", "fake_base64_data")],
                {"title": "Wohnung", "city": "Köln"},
                "Beschreibung",
                api_key="sk-live1234567890abcdef"
            )
            self.assertIsNotNone(res)
            self.assertIn("OneProvider", res.get("ai_provider"))
            self.assertIn("Vision", res.get("ai_provider"))
            self.assertEqual(res.get("token_usage", {}).get("prompt_tokens"), 2500)
            self.assertEqual(res.get("token_usage", {}).get("candidates_tokens"), 500)
            self.assertEqual(res.get("token_usage", {}).get("total_tokens"), 3000)

            # Check post call
            call_kwargs = mock_post.call_args[1]
            self.assertEqual(mock_post.call_args[0][0], "https://api.oneprovider.dev/v1/chat/completions")
            self.assertEqual(call_kwargs["headers"].get("Authorization"), "Bearer sk-live1234567890abcdef")
            self.assertNotIn("x-api-key", call_kwargs["headers"])
            self.assertEqual(call_kwargs["json"]["model"], "claude-haiku-5-5")
            self.assertNotIn("response_format", call_kwargs["json"])
            # Verify base64 formatted inside message content
            user_content = call_kwargs["json"]["messages"][0]["content"]
            img_item = next(item for item in user_content if item.get("type") == "image_url")
            self.assertEqual(img_item["image_url"]["url"], "data:image/jpeg;base64,fake_base64_data")

    def test_05_search_agent_oneprovider_cost_calculation(self):
        # When sk- key is active, cost must calculate based on model rates:
        # - claude-haiku-5-5, gpt-6-luna: 0.09 / 0.46
        # - qwen3.8-flash: 0.14 / 0.43
        # - gemini-3.5-flash: 1.38 / 8.28
        set_system_setting("gemini_api_key", "sk-live1234567890abcdef")
        from app.search_agent import get_search_profiles
        profiles = get_search_profiles()
        self.assertTrue(len(profiles) > 0)
        p_id = profiles[0]["id"]

        with patch("app.search_agent.scrape_multi_portal_deals") as mock_scrape, \
             patch("app.routers.properties.run_property_ai_audit") as mock_audit, \
             patch("app.routers.properties.run_property_image_analysis") as mock_img, \
             patch("app.search_agent.send_deal_telegram_alert") as mock_tg:
            mock_tg.return_value = {"success": True, "mocked": True}

            import uuid
            unique_url = f"https://www.kleinanzeigen.de/s-anzeige/test-oneprovider-cost/{uuid.uuid4().hex}"
            mock_scrape.return_value = [{
                "title": "OneProvider Test Deal",
                "portal_url": unique_url,
                "purchase_price": 85000.0,
                "living_space": 50.0,
                "rooms": 2.5,
                "gross_yield": 7.5,
                "market_deviation_percent": -15.0,
                "city": "Dortmund",
                "postal_code": "44137",
                "property_type": "wohnung",
                "description": "Sehr gepflegte Eigentumswohnung mit Top Rendite und Balkon."
            }]

            # 1. Primary model claude-haiku-5-5 (0.09 in / 0.46 out)
            mock_audit.return_value = {
                "ai_audit": {
                    "ai_provider": "OneProvider claude-haiku-5-5 Text-KI (Live API-Key)",
                    "score": 8.5,
                    "metrics": {"purchase_price": 85000.0, "living_space": 50.0, "gross_yield": 7.5, "net_cashflow": 120.0},
                    "token_usage": {
                        "prompt_tokens": 100000,      # 100k text in -> 100000 * 0.09 / 1e6 = 0.009 €
                        "candidates_tokens": 10000,   # 10k text out -> 10000 * 0.46 / 1e6 = 0.0046 €
                        "total_tokens": 110000
                    }
                }
            }

            mock_img.return_value = {
                "image_analysis": {
                    "ai_provider": "OneProvider claude-haiku-5-5 Vision (Live API-Key)",
                    "overall_visual_score": 8.0,
                    "total_visual_capex": 0.0,
                    "token_usage": {
                        "prompt_tokens": 100000,      # 100k vision in -> 100000 * 0.09 / 1e6 = 0.009 €
                        "candidates_tokens": 10000,   # 10k vision out -> 10000 * 0.46 / 1e6 = 0.0046 €
                        "total_tokens": 110000
                    }
                }
            }

            res = execute_search_profile(p_id)
            self.assertTrue(res.get("success"))
            # Expected total cost = 0.009 + 0.0046 + 0.009 + 0.0046 = 0.0272 €
            expected_cost = round(0.009 + 0.0046 + 0.009 + 0.0046, 4)
            self.assertEqual(res.get("estimated_cost_eur"), expected_cost)
            self.assertTrue(res.get("is_oneprovider"))

            # 2. Test Qwen vision model pricing (0.14 in / 0.43 out)
            mock_scrape.return_value[0]["portal_url"] = f"https://www.kleinanzeigen.de/s-anzeige/test-oneprovider-cost/{uuid.uuid4().hex}"
            mock_img.return_value["image_analysis"]["ai_provider"] = "OneProvider qwen3.8-flash Vision"
            res_qwen = execute_search_profile(p_id)
            # Text: 0.0136 €, Vision: 0.014 + 0.0043 = 0.0183 € -> Total: 0.0319 €
            self.assertEqual(res_qwen.get("estimated_cost_eur"), round(0.0136 + 0.014 + 0.0043, 4))

            # 3. Test Gemini 3.5 vision model pricing (1.38 in / 8.28 out)
            mock_scrape.return_value[0]["portal_url"] = f"https://www.kleinanzeigen.de/s-anzeige/test-oneprovider-cost/{uuid.uuid4().hex}"
            mock_img.return_value["image_analysis"]["ai_provider"] = "OneProvider gemini-3.5-flash Vision"
            res_gem = execute_search_profile(p_id)
            # Text: 0.0136 €, Vision: 0.138 + 0.0828 = 0.2208 € -> Total: 0.2344 €
            self.assertEqual(res_gem.get("estimated_cost_eur"), round(0.0136 + 0.138 + 0.0828, 4))

        # Explicit immediate cleanup of created test deals
        from app.database import get_connection
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM properties WHERE portal_url LIKE '%test-oneprovider%' OR title LIKE '%OneProvider Test%'")
        c.execute("DELETE FROM deal_alerts WHERE portal_url LIKE '%test-oneprovider%' OR title LIKE '%OneProvider Test%'")
        c.execute("DELETE FROM seen_listings WHERE portal_url LIKE '%test-oneprovider%'")
        c.execute("UPDATE search_profiles SET deals_found_count = 0 WHERE id = ?", (p_id,))
        conn.commit()
        conn.close()

if __name__ == "__main__":
    unittest.main()
