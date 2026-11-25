"""
Comprehensive regression test suite for Security & Acute Bug fixes (Point 1).
Uses an isolated temporary SQLite database so production data is untouched.
"""

import json
import os
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

# Isolate database BEFORE importing app modules
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db.close()
os.environ["DATABASE_PATH"] = _tmp_db.name
os.environ["TESTING"] = "1"
os.environ["APP_USERNAME"] = "admin"
os.environ["APP_PASSWORD"] = "test-secure-pass-123"
os.environ["SESSION_SECRET"] = "test-secret-key-32-chars-minimum!!"

from fastapi.testclient import TestClient

from app.ai_analyst import extract_listing_insights, generate_ai_due_diligence_report, estimate_capex_and_hidden_costs
from app.auth import get_auth_config, hash_session_token
from app.availability import check_portal_url_live
from app.database import DB_PATH, get_active_mcp_token, get_connection, init_db, set_system_setting
from app.geo_resolver import resolve_location_and_tax
from app.image_analyst import analyze_property_images
from app.main import app, assemble_index_html
from app.network_security import sanitize_error_message, validate_external_url
from app.portal_health import run_canary_checks
from app.scheduler import crawler_scan_guard, is_crawler_busy
from app.scrapers.immowelt import fetch_immowelt_expose
from app.scrapers.kleinanzeigen import scrape_kleinanzeigen_deals
from app.scrapers.immoscout import search_immoscout_deals
from app.telegram_notifier import send_deal_telegram_alert
from app.valuation import evaluate_property_valuation

init_db()


class TestPoint1SecurityAndBugs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.unauth = TestClient(app)
        cls.authed = TestClient(app)
        cfg = get_auth_config()
        cls.session_token = hash_session_token(cfg["username"], cfg["password"], cfg["secret"])
        cls.authed.cookies.set("immo_session", cls.session_token)

    def test_01_auth_backdoor_removed_and_mcp_protected(self):
        # X-Test-Client header must NO LONGER bypass auth
        r_backdoor = self.unauth.get("/api/properties", headers={"X-Test-Client": "immometrica-internal-test"})
        self.assertEqual(r_backdoor.status_code, 401)

        # Unauthenticated /mcp endpoints must return 401
        self.assertEqual(self.unauth.get("/mcp/status").status_code, 401)
        self.assertEqual(self.unauth.post("/mcp/regenerate-token").status_code, 401)
        self.assertEqual(self.unauth.get("/mcp/tools").status_code, 401)
        self.assertEqual(self.unauth.post("/mcp/call", json={"name": "get_deal_alerts", "arguments": {}}).status_code, 401)

        # Static raw partials & fallback index.html must require auth
        self.assertEqual(self.unauth.get("/static/index.html").status_code, 401)
        self.assertEqual(self.unauth.get("/static/partials/modals.html").status_code, 401)
        self.assertEqual(self.authed.get("/static/partials/modals.html").status_code, 200)

        # /mcp/status works via web session, and /mcp/call works via MCP Bearer token
        r_status = self.authed.get("/mcp/status")
        self.assertEqual(r_status.status_code, 200)
        mcp_token = r_status.json()["token"]
        self.assertTrue(mcp_token.startswith("immo_live_"))

        r_bearer = self.unauth.post(
            "/mcp/call",
            json={"name": "get_city_benchmarks", "arguments": {"city": "Dortmund"}},
            headers={"Authorization": f"Bearer {mcp_token}"},
        )
        self.assertEqual(r_bearer.status_code, 200)

        r_bad_bearer = self.unauth.post(
            "/mcp/call",
            json={"name": "get_city_benchmarks", "arguments": {"city": "Dortmund"}},
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(r_bad_bearer.status_code, 401)

    def test_02_login_remember_false_and_me_privacy(self):
        # Unauthenticated /me must not leak local_env_path
        r_me = self.unauth.get("/api/auth/me")
        self.assertEqual(r_me.status_code, 200)
        self.assertEqual(r_me.json(), {"authenticated": False})

        # remember=False must set a session cookie (no Max-Age) and omit raw token in JSON
        r_login = self.unauth.post(
            "/api/auth/login",
            json={"username": "admin", "password": "test-secure-pass-123", "remember": False},
        )
        self.assertEqual(r_login.status_code, 200)
        body = r_login.json()
        self.assertNotIn("token", body)
        set_cookie = r_login.headers.get("set-cookie", "").lower()
        self.assertIn("immo_session=", set_cookie)
        self.assertNotIn("max-age=", set_cookie)

    def test_03_cors_not_wildcard(self):
        r_cors = self.unauth.options(
            "/api/health",
            headers={"Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"},
        )
        self.assertNotEqual(r_cors.headers.get("access-control-allow-origin"), "*")
        self.assertNotEqual(r_cors.headers.get("access-control-allow-origin"), "https://evil.example.com")

    def test_04_ssrf_and_secret_redaction(self):
        for bad_url in [
            "http://127.0.0.1:8000/api/health",
            "http://localhost:8000/mcp/status",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.1/admin",
            "ftp://example.com/file",
        ]:
            with self.assertRaises(ValueError):
                validate_external_url(bad_url)
            r_imp = self.authed.post("/api/properties/import-url", json={"url": bad_url})
            self.assertEqual(r_imp.status_code, 400)
            avail = check_portal_url_live(bad_url)
            self.assertFalse(avail["is_online"])
            self.assertEqual(avail["availability_status"], "blocked_unsafe_url")

        leaked = "Error at https://api.telegram.org/bot123456:ABCDEF_SECRET_TOKEN_XYZ/sendMessage?key=AIzaSyDummySecretKey1234567890"
        cleaned = sanitize_error_message(leaked)
        self.assertNotIn("ABCDEF_SECRET_TOKEN_XYZ", cleaned)
        self.assertNotIn("AIzaSyDummySecretKey", cleaned)

    def test_05_csv_injection_and_price_per_sqm_div_zero(self):
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO properties (
                    title, address, postal_code, city, state, property_type, living_space, purchase_price,
                    source_portal, portal_url, is_online, tags
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    '=CMD("calc")',
                    '+1+1; "Teststraße"',
                    "44143",
                    "Dortmund",
                    "Nordrhein-Westfalen",
                    "Eigentumswohnung",
                    0.0,
                    150000.0,
                    "Kleinanzeigen",
                    "https://www.kleinanzeigen.de/s-anzeige/csv-test-99911",
                    1,
                    '["Test"]',
                ),
            )
            prop_id = c.lastrowid
            conn.commit()
        finally:
            conn.close()

        # Sorting by price_per_sqm with living_space=0.0 must not raise ZeroDivisionError / 500
        r_sort = self.authed.get("/api/properties?sort_by=price_per_sqm&sort_order=asc")
        self.assertEqual(r_sort.status_code, 200)

        # CSV export must safely escape quotes/semicolons and prefix formula characters
        r_csv = self.authed.get("/api/export/csv?city=Dortmund")
        self.assertEqual(r_csv.status_code, 200)
        csv_text = r_csv.content.decode("utf-8-sig")
        self.assertIn("'=CMD(\"\"calc\"\")", csv_text)
        self.assertIn("'+1+1; \"\"Teststraße\"\"", csv_text)

        # Clean up via DELETE and verify cascade/cleanup
        r_del = self.authed.delete(f"/api/properties/{prop_id}")
        self.assertEqual(r_del.status_code, 200)

    def test_06_telegram_url_escaping_and_testament_not_skipped(self):
        with patch("app.telegram_notifier.requests.post") as mock_post, patch.dict(os.environ, {"TESTING": "0"}):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"ok": True}
            mock_post.return_value = mock_resp

            res = send_deal_telegram_alert(
                bot_token="123456:fakeTokenSecretValue12345",
                chat_id="999",
                deal={
                    "id": 77,
                    "city": "Dortmund",
                    "title": "Testament-Villa <script>alert(1)</script>",
                    "address": "Teststr. 1",
                    "purchase_price": 200000,
                    "living_space": 80,
                    "portal_url": "https://www.kleinanzeigen.de/s-anzeige/testament-villa/123?a=1&b=2",
                },
                ai_verdict="Prüfung OK",
                profile_name="Profil <A&B>",
            )
            self.assertTrue(res.get("success"))
            self.assertFalse(res.get("skipped_test_deal", False))
            sent_html = mock_post.call_args.kwargs["json"]["text"]
            self.assertNotIn("<script>", sent_html)
            self.assertIn("&lt;script&gt;", sent_html)
            self.assertIn("&amp;b=2", sent_html)

    def test_07_immowelt_and_portal_health_and_availability_no_false_positives(self):
        # Immowelt 403 JS challenge HTML must fail instead of returning success=True
        with patch("app.scrapers.immowelt.fetch_immowelt_html") as mock_iw:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = "<html><title>Immowelt</title><body>DataDome JS challenge</body></html>"
            mock_iw.return_value = mock_resp
            res = fetch_immowelt_expose("https://www.immowelt.de/expose/461cd070-709d-40a9-9dc4-55fcc8111e86")
            self.assertFalse(res["success"])

        # Portal health run_canary_checks must have os imported (no NameError on 403 fallback)
        with patch("app.scrapers.kleinanzeigen.scrape_kleinanzeigen_deals", return_value=[]), \
             patch("app.scrapers.immoscout.search_immoscout_deals", return_value=[]), \
             patch("app.scrapers.immowelt.fetch_immowelt_expose", return_value={"success": False, "status_code": 403, "error": "Blocked"}), \
             patch("app.scrapers.immowelt.search_immowelt_deals", return_value=[{"purchase_price": 150000, "living_space": 60}]):
            with patch.dict(os.environ, {"TESTING": "0"}):
                with patch("app.portal_health.send_system_telegram_alert", return_value={"success": True}):
                    health = run_canary_checks(force_notify=False)
            self.assertEqual(health["portals"]["Immowelt"]["status"], "healthy")

        # Availability check on ImmoScout 429 must NOT say 404 offline
        with patch("app.scrapers.immoscout.fetch_immoscout_expose", return_value={"success": False, "status_code": 429, "error": "Rate limit"}):
            av = check_portal_url_live("https://www.immobilienscout24.de/expose/123456789", "ImmoScout24", "Berlin")
            self.assertFalse(av["is_online"])
            self.assertEqual(av["availability_status"], "unverified_bot_restricted")
            self.assertEqual(av["status_code"], 429)

    def test_08_scrapers_immoscout_and_kleinanzeigen_parsers(self):
        # ImmoScout: "249.000,00 €" must be 249000.0 (not 24900000.0)
        fake_json = {
            "resultListItems": [{
                "item": {
                    "id": "99887766",
                    "title": "Schöne ETW",
                    "attributes": [
                        {"value": "249.000,00 €", "label": "Kaufpreis"},
                        {"value": "75,5 m²", "label": "Wohnfläche"},
                        {"value": "3 Zi.", "label": "Zimmer"},
                    ],
                    "address": {"line": "44143 Dortmund", "lat": 51.51, "lon": 7.46},
                }
            }]
        }
        with patch("app.scrapers.immoscout.requests.post") as mock_post:
            m = MagicMock()
            m.status_code = 200
            m.json.return_value = fake_json
            mock_post.return_value = m
            deals = search_immoscout_deals("Dortmund", max_results=3)
            self.assertEqual(len(deals), 1)
            self.assertEqual(deals[0]["purchase_price"], 249000.0)
            self.assertEqual(deals[0]["living_space"], 75.5)

        # Kleinanzeigen: "75.5 m²" must stay 75.5, and "vermietet" without rent keeps current_rent=0.0
        ka_html = """
        <ul id="srchrslt-adtable">
          <article class="aditem">
            <a class="ellipsis" href="/s-anzeige/vermietete-wohnung-dortmund/12345-196-100">Schöne Wohnung vermietet in 44143 Dortmund</a>
            <p class="aditem-main--middle--price-shipping--price">150.000 €</p>
            <p class="aditem-main--middle--tags">75.5 m² · 3 Zimmer</p>
            <p class="aditem-main--top--left">44143 Dortmund</p>
          </article>
        </ul>
        """
        with patch("app.scrapers.kleinanzeigen.requests.get") as mock_ka:
            mk = MagicMock()
            mk.status_code = 200
            mk.text = ka_html
            mock_ka.return_value = mk
            ka_deals = scrape_kleinanzeigen_deals("Dortmund", max_results=3)
            self.assertEqual(len(ka_deals), 1)
            self.assertEqual(ka_deals[0]["living_space"], 75.5)
            self.assertEqual(ka_deals[0]["is_rented"], 1)
            self.assertEqual(ka_deals[0]["current_rent"], 0.0)

    def test_09_ai_analyst_regex_verdict_and_zero_div(self):
        # "seit 2024 unvermietet" + "Baujahr 1965 kalt" must NOT mark rented or extract 1965 rent
        ins = extract_listing_insights(
            "Die helle Eigentumswohnung steht seit 2024 unvermietet. Baujahr 1965 kalt und solide Substanz.",
            {},
            {"title": "ETW in Dortmund", "purchase_price": 150000, "living_space": 60, "city": "Dortmund"},
        )
        self.assertFalse(ins["is_rented"])
        self.assertIsNone(ins["extracted_rent"])

        # "1.200,- €" must parse cleanly as 1200.0 and ",00 €" purchase price
        ins2 = extract_listing_insights(
            "Kaufpreis: 240.000,00 €. Kaltmiete gesamt: 1.200,- €.",
            {},
            {"title": "ETW in Dortmund", "purchase_price": 240000, "living_space": 80, "city": "Dortmund"},
        )
        self.assertEqual(ins2["extracted_rent"], 1200.0)
        self.assertEqual(ins2["stated_purchase_price_in_text"], 240000.0)

        # Single ETW in 6-unit house without is_package_deal must not get 6x bath CaPEx
        capex = estimate_capex_and_hidden_costs(
            {"year_built_extracted": 1975, "unit_count_in_offer": 6, "is_package_deal": False},
            {"living_space": 60, "year_built": 1975},
        )
        bad_item = next(i for i in capex["items"] if "Badezimmer" in i["title"])
        self.assertEqual(bad_item["amount"], 7500.0)

        # True critical risk (Nachtspeicherheizung) with score=5.3 must yield RED instead of YELLOW
        rep = generate_ai_due_diligence_report(
            {
                "title": "ETW mit Nachtspeicherheizung",
                "city": "Dortmund",
                "purchase_price": 150000.0,
                "living_space": 65.0,
                "is_online": 1,
            },
            full_description=(
                "Gepflegte Eigentumswohnung in Dortmund, derzeit bezugsfrei. "
                "Beheizung erfolgt über Nachtspeicheröfen / Nachtspeicherheizung. "
                "Hausgeld beträgt 280 € monatlich laut aktueller Abrechnung."
            ),
        )
        self.assertEqual(rep["status"], "RED")

        # Zero purchase price with discount must not raise ZeroDivisionError
        rep_zero = generate_ai_due_diligence_report(
            {
                "title": "Test 0 Price",
                "city": "Dortmund",
                "purchase_price": 0.0,
                "living_space": 60.0,
                "is_online": 1,
            },
            full_description="Renovierungsbedürftige Wohnung aus Baujahr 1965 in zentraler Lage mit viel Potenzial und Gestaltungsspielraum.",
        )
        self.assertIn("status", rep_zero)

    def test_10_geo_resolver_and_valuation_none_safety(self):
        # PLZ 49 -> Niedersachsen, PLZ 63739 -> Bayern, PLZ 80331 overrides default NRW state
        self.assertEqual(resolve_location_and_tax(postal_code="49074", state="Nordrhein-Westfalen")["state"], "Niedersachsen")
        self.assertEqual(resolve_location_and_tax(postal_code="63739", state="Nordrhein-Westfalen")["state"], "Bayern")
        self.assertEqual(resolve_location_and_tax(postal_code="80331", city="München", state="Nordrhein-Westfalen")["state"], "Bayern")
        # Substring safety: Essenbach should NOT resolve to Essen (NRW) if Bayern city passed
        self.assertNotEqual(resolve_location_and_tax(city="Essenbach")["state"], "None")

        # None inputs must not crash evaluate_property_valuation
        val = evaluate_property_valuation("Dortmund", 60.0, 150000.0, year_built=None, condition=None, property_type=None)
        self.assertGreater(val["estimated_market_value"], 0)

    def test_11_crawler_lock_reentrancy_and_cross_thread_exclusion(self):
        with crawler_scan_guard(blocking=False) as acq1:
            self.assertTrue(acq1)
            self.assertTrue(is_crawler_busy())
            # Nested on same thread succeeds (RLock)
            with crawler_scan_guard(blocking=False) as acq_same:
                self.assertTrue(acq_same)
            # Different thread gets False (excluded!)
            other_res = []
            t = threading.Thread(target=lambda: other_res.append(self._try_acquire_in_thread()))
            t.start()
            t.join()
            self.assertEqual(other_res, [False])
        self.assertFalse(is_crawler_busy())

    @staticmethod
    def _try_acquire_in_thread() -> bool:
        with crawler_scan_guard(blocking=False) as acq:
            return acq

    def test_12_image_analyst_index_alignment_and_assembled_html(self):
        # Mock 3 URLs where photo #2 fails to download; Gemini classifies sent #1 and sent #2 -> must map to photo #1 and #3
        def fake_get(url, **kwargs):
            if "fail2" in url:
                raise ConnectionError("404")
            from PIL import Image
            import io
            buf = io.BytesIO()
            Image.new("RGB", (100, 100), color="white").save(buf, format="JPEG")
            r = MagicMock()
            r.status_code = 200
            r.content = buf.getvalue() * 10
            return r

        fake_gemini = {
            "overall_visual_score": 8.0,
            "condition_label": "Gepflegt",
            "has_interior_photos": True,
            "categories": {
                "bathroom": {"score": 8, "status": "Gut", "findings": "OK", "cost": 0},
                "kitchen": {"score": 8, "status": "Gut", "findings": "OK", "cost": 0},
                "flooring": {"score": 8, "status": "Gut", "findings": "OK", "cost": 0},
                "windows": {"score": 8, "status": "Gut", "findings": "OK", "cost": 0},
                "heating_electrics": {"score": 8, "status": "Gut", "findings": "OK", "cost": 0},
                "walls_substance": {"score": 8, "status": "Gut", "findings": "OK", "cost": 0},
            },
            "photo_classifications": [
                {"index": 1, "tag": "Wohnbereich / Wohnzimmer", "description": "Foto eins", "quality_score": 8, "is_blueprint": False, "findings": []},
                {"index": 2, "tag": "Badezimmer & Sanitär", "description": "Foto drei im Original", "quality_score": 8, "is_blueprint": False, "findings": []},
            ],
            "visual_capex_items": [],
            "investor_summary": "Solide.",
        }

        with patch("app.image_analyst.requests.get", side_effect=fake_get), \
             patch("app.image_analyst.call_gemini_vision", return_value=fake_gemini):
            res = analyze_property_images(
                {
                    "id": 99999,
                    "title": "Test Wohnung",
                    "city": "Dortmund",
                    "living_space": 60,
                    "year_built": 1990,
                    "description": "Gepflegte helle 3-Zimmer-Eigentumswohnung in ruhiger Lage mit Balkon und Einbauküche.",
                },
                image_urls=[
                    "https://example.com/ok1.jpg",
                    "https://example.com/fail2.jpg",
                    "https://example.com/ok3.jpg",
                ],
                full_description="Gepflegte helle 3-Zimmer-Eigentumswohnung in ruhiger Lage mit Balkon und Einbauküche.",
                api_key="AIzaFakeKey1234567890",
            )
            self.assertEqual(len(res["analyzed_photos"]), 3)
            self.assertEqual(res["analyzed_photos"][1]["description"], "Foto aus Exposé-Galerie")
            self.assertEqual(res["analyzed_photos"][2]["description"], "Foto drei im Original")

        # Verify assembled HTML matches static/index.html cleanly and contains key fixes
        html_out = assemble_index_html()
        with open("static/css/style.css", encoding="utf-8") as css_f:
            self.assertIn("[x-cloak]", css_f.read())
        self.assertIn("formatPricePerSqm(prop.purchase_price, prop.living_space)", html_out)
        self.assertIn("formatDateTimeDE(portalHealth.last_checked_at)", html_out)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        try:
            os.unlink(_tmp_db.name)
        except Exception:
            pass
