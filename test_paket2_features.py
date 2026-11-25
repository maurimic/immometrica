import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Isolate tests onto a temporary database
_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_TMP_DIR.name, "test_paket2.db")
os.environ["TESTING"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
from app.database import init_db, get_connection  # noqa: E402
from app.main import app  # noqa: E402
from app.auth import get_auth_config, hash_session_token, COOKIE_NAME  # noqa: E402


def _auth_cookies():
    cfg = get_auth_config()
    token = hash_session_token(cfg["username"], cfg["password"], cfg["secret"])
    return {COOKIE_NAME: token}


class TestPaket2FunctionalGaps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        try:
            _TMP_DIR.cleanup()
        except Exception:
            pass

    def test_01_url_normalization_and_same_url_dedup_keeps_cross_portal_separate(self):
        from app.scrapers.deal_fusion import are_deals_matching, fuse_deals, normalize_portal_url

        u1 = "https://www.immowelt.de/expose/abc-123/?utm_source=test#top"
        u2 = "https://www.immowelt.de/expose/abc-123"
        self.assertEqual(normalize_portal_url(u1), u2)

        d_iw_dup1 = {"portal_url": u1, "source_portal": "Immowelt", "purchase_price": 150000}
        d_iw_dup2 = {"portal_url": u2, "source_portal": "Immowelt", "purchase_price": 150000}
        d_ka = {
            "portal_url": "https://www.kleinanzeigen.de/s-anzeige/wohnung/999-196-1",
            "source_portal": "Kleinanzeigen",
            "purchase_price": 149000,
        }
        self.assertFalse(are_deals_matching(d_iw_dup1, d_ka))
        fused = fuse_deals([d_iw_dup1, d_iw_dup2, d_ka])
        self.assertEqual(len(fused), 2)
        self.assertEqual([f["source_portal"] for f in fused], ["Immowelt", "Kleinanzeigen"])

    def test_02_db_unique_portal_url_deduplicates_existing_and_blocks_new_duplicates(self):
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute("DROP INDEX IF EXISTS idx_properties_portal_url_unique")
            c.execute(
                "INSERT INTO properties (title, city, state, property_type, living_space, purchase_price, source_portal, portal_url) "
                "VALUES ('Dup 1', 'Essen', 'Nordrhein-Westfalen', 'Eigentumswohnung', 55, 100000, 'Kleinanzeigen', 'https://example.com/dup-1')"
            )
            c.execute(
                "INSERT INTO properties (title, city, state, property_type, living_space, purchase_price, source_portal, portal_url) "
                "VALUES ('Dup 2', 'Essen', 'Nordrhein-Westfalen', 'Eigentumswohnung', 55, 100000, 'Kleinanzeigen', 'https://example.com/dup-1')"
            )
            conn.commit()
        finally:
            conn.close()

        # init_db cleans existing duplicates and creates unique index
        init_db()
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM properties WHERE portal_url = 'https://example.com/dup-1'")
            self.assertEqual(c.fetchone()[0], 1)
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute(
                    "INSERT INTO properties (title, city, state, property_type, living_space, purchase_price, source_portal, portal_url) "
                    "VALUES ('Dup 3', 'Essen', 'Nordrhein-Westfalen', 'Eigentumswohnung', 55, 100000, 'Kleinanzeigen', 'https://example.com/dup-1')"
                )
        finally:
            conn.close()

    def test_03_scraper_type_mappings_and_yield_basis_actual_filter(self):
        from app.scrapers.kleinanzeigen import _build_kleinanzeigen_search_targets
        from app.scrapers.immowelt import _build_immowelt_search_paths
        from app.scrapers.immoscout import _map_immoscout_types
        from app.scraper import scrape_multi_portal_deals

        self.assertEqual(_build_kleinanzeigen_search_targets("Mehrfamilienhaus"), [("s-haus-kaufen", "c208")])
        self.assertEqual(_build_kleinanzeigen_search_targets("Grundstück"), [("s-grundstuecke-garten", "c207")])
        self.assertEqual(len(_build_kleinanzeigen_search_targets("all")), 2)

        self.assertEqual(_build_immowelt_search_paths("Mehrfamilienhaus")[0][0], "haeuser/kaufen")
        self.assertEqual(_build_immowelt_search_paths("Grundstück")[0][0], "grundstuecke/kaufen")
        self.assertEqual(len(_build_immowelt_search_paths("all")), 2)

        self.assertEqual(_map_immoscout_types("Mehrfamilienhaus")[0][1], "Mehrfamilienhaus")
        self.assertEqual(_map_immoscout_types("Grundstück")[0][0], "sitebuy")

        # Verify actual vs potential yield filtering and parameter forwarding in scrape_multi_portal_deals
        fake_deals = [
            {
                "title": "Unvermietete ETW",
                "city": "Dortmund",
                "property_type": "Eigentumswohnung",
                "purchase_price": 100000.0,
                "living_space": 60.0,
                "is_rented": 0,
                "current_rent": 0.0,
                "expected_rent": 600.0,
                "gross_yield": 7.2,
                "portal_url": "https://www.kleinanzeigen.de/s-anzeige/unvermietet/111",
                "source_portal": "Kleinanzeigen",
            },
            {
                "title": "Vermietete MFH",
                "city": "Dortmund",
                "property_type": "Mehrfamilienhaus",
                "purchase_price": 200000.0,
                "living_space": 140.0,
                "is_rented": 1,
                "current_rent": 1200.0,
                "expected_rent": 1300.0,
                "gross_yield": 7.2,
                "portal_url": "https://www.kleinanzeigen.de/s-anzeige/mfh-vermietet/222",
                "source_portal": "Kleinanzeigen",
            },
        ]

        captured_kwargs = {}

        def _fake_ka(**kwargs):
            captured_kwargs.update(kwargs)
            return list(fake_deals)

        with patch("app.scraper.scrape_kleinanzeigen_deals", side_effect=_fake_ka), \
             patch("app.scraper.search_immoscout_deals", return_value=[]), \
             patch("app.scraper.search_immowelt_deals", return_value=[]):
            res = scrape_multi_portal_deals(
                city="Dortmund",
                min_yield=6.5,
                yield_basis="actual",
                postal_code="44149",
                radius_km=10,
                property_type="Mehrfamilienhaus",
            )
            self.assertEqual(captured_kwargs.get("postal_code"), "44149")
            self.assertEqual(captured_kwargs.get("radius_km"), 10)
            self.assertEqual(captured_kwargs.get("property_type"), "Mehrfamilienhaus")
            self.assertEqual(len(res), 1)
            self.assertEqual(res[0]["property_type"], "Mehrfamilienhaus")

    def test_04_api_list_properties_yield_basis_actual_excludes_vacant_soll(self):
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute(
                "INSERT INTO properties (title, city, state, property_type, living_space, purchase_price, current_rent, expected_rent, gross_yield, is_rented, source_portal, portal_url) "
                "VALUES ('Vacant High Soll', 'Essen', 'Nordrhein-Westfalen', 'Eigentumswohnung', 50, 100000, 0, 650, 7.8, 0, 'Immowelt', 'https://example.com/vacant-yb')"
            )
            c.execute(
                "INSERT INTO properties (title, city, state, property_type, living_space, purchase_price, current_rent, expected_rent, gross_yield, is_rented, source_portal, portal_url) "
                "VALUES ('Real Rented 7pct', 'Essen', 'Nordrhein-Westfalen', 'Eigentumswohnung', 50, 100000, 585, 650, 7.02, 1, 'Immowelt', 'https://example.com/rented-yb')"
            )
            conn.commit()
        finally:
            conn.close()

        r = self.client.get(
            "/api/properties?city=Essen&min_yield=6.5&yield_basis=actual",
            cookies=_auth_cookies(),
        )
        self.assertEqual(r.status_code, 200)
        titles = [p["title"] for p in r.json()["properties"]]
        self.assertIn("Real Rented 7pct", titles)
        self.assertNotIn("Vacant High Soll", titles)

    def test_05_interest_berlin_time_cache_and_single_drklein_audit_call(self):
        from app import interest_service
        from app.ai_analyst import compute_investment_metrics

        interest_service._MEM_QUOTE_CACHE.clear()
        calls = {"count": 0}

        def _mock_quote(**kwargs):
            calls["count"] += 1
            return {
                "nominal_rate": 3.85,
                "effective_rate": 3.95,
                "bank_name": "MockBank",
                "is_live_quote": True,
                "rates_by_term": [{"years": 10, "nominal_rate": 3.85}],
            }

        insights = {
            "is_rented": True,
            "extracted_rent": 800.0,
            "rent_source": "EXPOSE_TEXT",
            "has_explicit_hausgeld": False,
        }
        prop = {
            "id": 999,
            "purchase_price": 150000.0,
            "living_space": 65.0,
            "city": "Dortmund",
            "state": "Nordrhein-Westfalen",
            "postal_code": "44149",
        }
        capex = {"total_capex": 0.0, "items": []}

        with patch("app.interest_service.get_property_mortgage_interest_quote", side_effect=_mock_quote):
            m = compute_investment_metrics(prop, insights, capex)
            # Only 1 quote request (for cf_data), reused for potential_cf_data!
            self.assertEqual(calls["count"], 1)
            # Consistent default 50 EUR/month (600 EUR/yr) for both cashflow & real_net_yield
            expected_net_yield = round(((800.0 * 12.0 - 600.0) / (150000.0 + m["ancillary_costs"])) * 100.0, 2)
            self.assertAlmostEqual(m["real_net_yield"], expected_net_yield, places=2)

    def test_06_valuation_expanded_cities_and_dynamic_year(self):
        from app.valuation import get_city_benchmark, evaluate_property_valuation

        bm_sl = get_city_benchmark("Saarlouis")
        self.assertEqual(bm_sl["city"], "Saarlouis")
        self.assertEqual(bm_sl["state"], "Saarland")
        self.assertEqual(bm_sl["avg_sqm_buy"], 2350.0)

        bm_fr = get_city_benchmark("Freiburg")
        self.assertEqual(bm_fr["state"], "Baden-Württemberg")
        self.assertGreater(bm_fr["avg_sqm_buy"], 4500.0)

        val = evaluate_property_valuation(
            city="Saarlouis",
            living_space=70.0,
            purchase_price=160000.0,
            year_built=None,
            condition=None,
            property_type=None,
        )
        self.assertGreater(val["estimated_market_value"], 0)

    def test_07_image_analyst_preserves_defect_capex_and_dynamic_blueprint_notice(self):
        import io
        from PIL import Image
        from app import image_analyst

        buf = io.BytesIO()
        Image.new("RGB", (600, 600), color="white").save(buf, format="JPEG")
        fake_jpeg_bytes = buf.getvalue()

        class _FakeImgResp:
            status_code = 200
            content = fake_jpeg_bytes

        prop_blueprint = {
            "id": 777,
            "living_space": 60.0,
            "year_built": 1975,
            "description": "Sanierungsbedürftige Eigentumswohnung, bitte renovieren.",
            "image_url": "https://example.com/plan.jpg",
        }
        cv_blueprint = {
            "width": 600,
            "height": 600,
            "visual_quality_score": 7.0,
            "is_blueprint": True,
            "tag": "📐 Grundriss",
            "description": "Grundrisszeichnung",
        }
        with patch("app.network_security.validate_external_url", side_effect=lambda u: u), \
             patch("app.image_analyst.requests.get", return_value=_FakeImgResp()), \
             patch.object(image_analyst, "should_run_vision_ai", return_value=(True, "Test")), \
             patch.object(image_analyst, "classify_photo", return_value=cv_blueprint), \
             patch.object(image_analyst, "get_resolved_gemini_key", return_value=None):
            res = image_analyst.analyze_property_images(
                prop_blueprint,
                image_urls=["https://example.com/plan.jpg"],
                full_description=prop_blueprint["description"],
                api_key="",
            )
            notice = res.get("notice") or ""
            self.assertNotIn("Renovieren!!!!", notice)
            self.assertIn("CaPEx-Kalkulation", notice)

        # Kitchen & bathroom defect preservation when Gemini Vision finds explicit defects
        fake_gem_dict = {
            "ai_provider": "Google Gemini Vision",
            "overall_visual_score": 5.2,
            "condition_rating": "MODERNISIERUNGSBEDARF",
            "condition_label": "🟠 Renovierungsbedarf",
            "has_interior_photos": True,
            "analyzed_photos": [
                {"index": 1, "tag": "🍳 Küche", "description": "Veraltete 70er-Jahre Küchenzeile", "findings": ["Abgenutzte Fronten"], "quality_score": 4.0, "is_blueprint": False},
                {"index": 2, "tag": "🛁 Badezimmer & Sanitär", "description": "Stark sanierungsbedürftiges Bad mit Feuchtigkeitsschäden", "findings": ["Fliesen gerissen, Schimmel"], "quality_score": 3.5, "is_blueprint": False},
            ],
            "categories": {
                "kitchen": {"score": 4.0, "status": "Erneuerungsbedürftig", "findings": "Veraltete Küche", "cost": 4500.0},
                "bathroom": {"score": 3.5, "status": "Sanierungsbedürftig", "findings": "Sichtbare Schäden", "cost": 6500.0},
            },
            "visual_capex_items": [
                {"category": "Küche", "title": "Küchenzeile erneuern", "urgency": "HOCH", "cost": 4500.0, "source_type": "FOTO_BELEG", "photo_indices": [1], "evidence_source": "Foto #1", "description": "Veraltete defekte Küche"},
                {"category": "Bad", "title": "Badsanierung komplett", "urgency": "KRITISCH", "cost": 6500.0, "source_type": "FOTO_BELEG", "photo_indices": [2], "evidence_source": "Foto #2", "description": "Sichtbare Fliesenrisse"},
            ],
            "detected_findings": [],
            "investor_summary": "Bad und Küche sanierungsbedürftig.",
            "token_usage": {"prompt_tokens": 10, "candidates_tokens": 10, "total_tokens": 20},
        }
        cv_interior = {
            "width": 800,
            "height": 600,
            "visual_quality_score": 6.5,
            "is_blueprint": False,
            "tag": "🏠 Wohnraum",
            "description": "Innenraum",
        }
        with patch("app.network_security.validate_external_url", side_effect=lambda u: u), \
             patch("app.image_analyst.requests.get", return_value=_FakeImgResp()), \
             patch.object(image_analyst, "should_run_vision_ai", return_value=(True, "Test")), \
             patch.object(image_analyst, "classify_photo", return_value=cv_interior), \
             patch.object(image_analyst, "get_resolved_gemini_key", return_value="dummy-key"), \
             patch.object(image_analyst, "call_gemini_vision", return_value=fake_gem_dict):
            res_vis = image_analyst.analyze_property_images(
                {"id": 778, "living_space": 60.0, "year_built": 1975, "image_url": "https://example.com/1.jpg"},
                image_urls=["https://example.com/1.jpg", "https://example.com/2.jpg"],
                api_key="dummy-key",
            )
            items = {it["title"]: it["cost"] for it in res_vis["visual_capex_items"]}
            self.assertEqual(items.get("Küchenzeile erneuern"), 4500.0)
            self.assertEqual(items.get("Badsanierung komplett"), 6500.0)

    def test_08_location_include_pois_flag_and_dead_code_cleanup(self):
        from app.location_analyst import analyze_property_location_and_pois

        prop = {"address": "Düsterstraße 15, 44143 Dortmund", "postal_code": "44143", "city": "Dortmund", "lat": 51.515, "lng": 7.495}
        no_pois = analyze_property_location_and_pois(prop, include_pois=False)
        self.assertEqual(no_pois["pois"], [])
        self.assertTrue(no_pois["is_exact_address"])
        self.assertIn("direct_url", no_pois["maps"])

        with_pois = analyze_property_location_and_pois(prop, include_pois=True)
        self.assertGreaterEqual(len(with_pois["pois"]), 4)

        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='saved_searches'")
            self.assertIsNone(c.fetchone())
        finally:
            conn.close()

        req_txt = Path("requirements.txt").read_text(encoding="utf-8")
        for pkg in ("mcp", "lxml", "httpx", "pytest"):
            self.assertIn(pkg, req_txt)

    def test_09_deal_notes_api_and_assembled_ui_completeness(self):
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute(
                "INSERT INTO properties (title, city, state, property_type, living_space, purchase_price, source_portal, portal_url) "
                "VALUES ('Notes Test Prop', 'Berlin', 'Berlin', 'Eigentumswohnung', 50, 200000, 'Kleinanzeigen', 'https://example.com/notes-test')"
            )
            pid = c.lastrowid
            conn.commit()
        finally:
            conn.close()

        r_post = self.client.post(
            f"/api/properties/{pid}/notes",
            json={"note": "Makler wegen Wirtschaftsplan angerufen."},
            cookies=_auth_cookies(),
        )
        self.assertEqual(r_post.status_code, 200)
        notes = r_post.json()["notes"]
        self.assertEqual(len(notes), 1)
        self.assertIn("Wirtschaftsplan", notes[0]["note"])

        r_get = self.client.get(f"/api/properties/{pid}", cookies=_auth_cookies())
        self.assertEqual(r_get.status_code, 200)
        self.assertEqual(len(r_get.json()["notes"]), 1)

        html = Path("static/index.html").read_text(encoding="utf-8")
        # Notes UI + Mobile Kanban stage selector + Responsive Map height
        self.assertIn("addPropertyNote()", html)
        self.assertIn("moveDeal(deal.id, $event.target.value)", html)
        self.assertIn("h-[400px] sm:h-[520px] lg:h-[700px]", html)
        # All 10 MCP tools listed in tab_info
        from app.mcp_server import MCP_TOOLS
        for tool in MCP_TOOLS:
            self.assertIn(tool["name"], html, f"MCP tool '{tool['name']}' missing in static/index.html")


if __name__ == "__main__":
    unittest.main()
