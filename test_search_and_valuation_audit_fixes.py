import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_TMP_DIR.name, "test_audit_fixes.db")
os.environ["TESTING"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
from app.auth import COOKIE_NAME, get_auth_config, hash_session_token  # noqa: E402
from app.database import get_connection, init_db  # noqa: E402
from app.main import app  # noqa: E402


def _auth_cookies():
    cfg = get_auth_config()
    return {COOKIE_NAME: hash_session_token(cfg["username"], cfg["password"], cfg["secret"])}


class TestSearchAndValuationAuditFixes(unittest.TestCase):
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

    def test_01_immowelt_headers_slugs_cards_and_expose_plot_guard(self):
        from app.scrapers.immowelt import (
            BROWSER_HEADERS,
            clean_city_slug,
            fetch_immowelt_expose,
            search_immowelt_deals,
        )

        # No unsupported brotli encoding
        self.assertNotIn("br", BROWSER_HEADERS.get("Accept-Encoding", ""))
        self.assertEqual(clean_city_slug("Halle (Saale)"), "halle-saale")
        self.assertEqual(clean_city_slug("Frankfurt"), "frankfurt-am-main")

        # Card parser must skip ?h=50 broker logo, parse "Aplerbeck, Dortmund (44287)",
        # keep Maisonette (not MFH from teaser), and stop paginating identical redirected pages.
        card_html = """
        <html><body>
          <div class="card">
            <img src="https://mms.immowelt.de/broker/avatar.jpg?h=50" />
            <img src="https://mms.immowelt.de/obj/real1.jpg?w=626&h=470" />
            <div>Herr Makler</div>
            <div>349.000 €</div>
            <div>Maisonette zum Kauf · provisionsfrei</div>
            <div>4,5 Zimmer · 107 m²</div>
            <div>Aplerbeck, Dortmund (44287)</div>
            <div>Diese Maisonette Wohnung in einem gepflegten Mehrfamilienhaus mit großem Grundstück bietet Licht.</div>
            <a href="/expose/11111111-1111-1111-1111-111111111111">Details</a>
          </div>
        </body></html>
        """
        resp = MagicMock(status_code=200, text=card_html)
        with patch("app.scrapers.immowelt.fetch_immowelt_html", return_value=resp) as mock_get:
            deals = search_immowelt_deals("Dortmund", property_type="Eigentumswohnung", radius_km=10, max_results=5)
        # Page 2 produces identical expose IDs -> breaks early after page 2 (<= 2 calls per path)
        self.assertLessEqual(mock_get.call_count, 2)
        self.assertEqual(len(deals), 1)
        d = deals[0]
        self.assertEqual(d["city"], "Dortmund")
        self.assertEqual(d["postal_code"], "44287")
        self.assertEqual(d["property_type"], "Maisonette")
        self.assertIn("obj/real1.jpg", d["image_url"])
        self.assertNotIn("avatar.jpg", d["image_url"])

        # Expose parser: plotSpace must not overwrite livingSpace, hardFacts.title used if classified.title is long prose
        expose_payload = {
            "app_demand_referral_cldp": {
                "data": {
                    "classified": {
                        "title": "In gewachsener Wohnlage erwartet Sie dieses schöne Haus. " * 4,
                        "sections": {
                            "hardFacts": {
                                "title": "Einfamilienhaus zum Kauf",
                                "facts": [
                                    {"type": "livingSpace", "value": "200 m²", "splitValue": "200 m²"},
                                    {"type": "plotSpace", "value": "871 m² Grundstück", "splitValue": "871 m²"},
                                    {"type": "numberOfRooms", "value": "6 Zimmer", "splitValue": "6"},
                                ],
                            },
                            "price": {"value": 495000},
                            "location": {"address": {"city": "Essen", "zipCode": "45219", "district": "Kettwig", "street": "Ruhrtalstr. 5"}},
                            "description": {"description": "Gepflegtes Haus in Kettwig mit großem Grundstück."},
                        },
                        "domains": {"medias": {"images": [{"url": "https://mms.immowelt.de/a/b/c.jpg"}]}},
                    }
                }
            }
        }
        embedded_js = f'<script>window["__UFRN_LIFECYCLE_SERVERREQUEST__"] = JSON.parse({json.dumps(json.dumps(expose_payload))});</script>'
        with patch("app.scrapers.immowelt.fetch_immowelt_html", return_value=MagicMock(status_code=200, text=embedded_js)):
            ex = fetch_immowelt_expose("https://www.immowelt.de/expose/f6f6e9be-d3d2-4c18-98cf-d5783586e1bc")
        self.assertTrue(ex["success"])
        self.assertEqual(ex["living_space"], 200.0)
        self.assertEqual(ex["land_area"], 871.0)
        self.assertEqual(ex["title"], "Einfamilienhaus zum Kauf")
        self.assertIn("Ruhrtalstr. 5", ex["address"])
        self.assertIn("Wohnfläche", ex["details"])

    def test_02_immoscout_sorting_filter_city_and_no_fake_sqm(self):
        from app.scrapers.immoscout import fetch_immoscout_expose, search_immoscout_deals

        mock_list_json = {
            "resultListItems": [
                {"type": "NEW_HOME_BUILDER_RESULT", "item": {"title": "Bauträger Ad"}},
                {
                    "type": "EXPOSE_RESULT",
                    "item": {
                        "id": "9001",
                        "title": "Doppelhaushälfte in ruhiger Lage",
                        "attributes": [
                            {"label": "Kaufpreis", "value": "390.000 €"},
                            {"label": "Wohnfläche", "value": "144 m²"},
                            {"label": "Zimmer", "value": "5 Zi."},
                        ],
                        "address": {"line": "60599 Frankfurt am Main, Oberrad", "lat": 50.098, "lon": 8.718},
                        "pictures": [],
                    },
                },
                {
                    "type": "EXPOSE_RESULT",
                    "item": {
                        "id": "9002",
                        "title": "Gepflegtes Mehrfamilienhaus mit 6 Einheiten",
                        "attributes": [
                            {"label": "Kaufpreis", "value": "750.000 €"},
                            {"label": "Mieteinnahmen pro Monat", "value": "4.200 €"},
                        ],
                        "address": {"line": "60311 Frankfurt am Main, Altstadt", "lat": 50.1109, "lon": 8.6821},
                        "pictures": [],
                    },
                },
            ]
        }
        captured_params = {}

        def _fake_post(url, params=None, **kwargs):
            captured_params.update(params or {})
            return MagicMock(status_code=200, json=lambda: mock_list_json)

        with patch("app.scrapers.immoscout.requests.post", side_effect=_fake_post):
            res = search_immoscout_deals("Frankfurt am Main", property_type="Mehrfamilienhaus", radius_km=10, max_results=5)

        self.assertEqual(captured_params.get("sorting"), "-firstactivation")
        self.assertEqual(len(res), 2)
        # First is clearly EFH/Doppelhaushälfte, not coerced into Mehrfamilienhaus; multi-word city kept intact
        self.assertEqual(res[0]["property_type"], "Einfamilienhaus")
        self.assertEqual(res[0]["city"], "Frankfurt am Main")
        # Second has no attribute sqm -> NOT fabricated via price/3000; stated rent extracted
        mfh = res[1]
        self.assertEqual(mfh["property_type"], "Mehrfamilienhaus")
        self.assertEqual(mfh["living_space"], 0.0)
        self.assertEqual(mfh["current_rent"], 4200.0)
        self.assertEqual(mfh["is_rented"], 1)

        # Expose ATTRIBUTE_LIST must skip Kaufpreis/m² and Grundstücksfläche
        ex_json = {
            "header": {"title": "Test"},
            "sections": [
                {"type": "TITLE", "title": "MFH Test"},
                {
                    "type": "ATTRIBUTE_LIST",
                    "attributes": [
                        {"label": "Kaufpreis €/m²", "text": "2.500 €/m²"},
                        {"label": "Stellplatz-Kaufpreis", "text": "20.000 €"},
                        {"label": "Kaufpreis", "text": "500.000 €"},
                        {"label": "Grundstücksfläche", "text": "800 m²"},
                        {"label": "Wohnfläche", "text": "220 m²"},
                    ],
                },
            ],
        }
        with patch("app.scrapers.immoscout.requests.get", return_value=MagicMock(status_code=200, json=lambda: ex_json)):
            ex = fetch_immoscout_expose("9002")
        self.assertEqual(ex["price"], 500000.0)
        self.assertEqual(ex["living_space"], 220.0)

    def test_03_kleinanzeigen_frankfurt_fact_tag_street_and_type(self):
        from app.scrapers.kleinanzeigen import (
            _LOCATION_ID_CACHE,
            resolve_kleinanzeigen_location_id,
            scrape_kleinanzeigen_deals,
        )

        _LOCATION_ID_CACHE.pop("city:frankfurt", None)
        ka_emp = {
            "_0": "Deutschland",
            "_7950": "Frankfurt (Oder) - Brandenburg",
            "_4292": "Frankfurt am Main - Hessen",
        }
        with patch("app.scrapers.kleinanzeigen.requests.get", return_value=MagicMock(status_code=200, json=lambda: ka_emp)):
            loc_id = resolve_kleinanzeigen_location_id("Frankfurt")
        self.assertEqual(loc_id, "4292")

        # Card with plot m² in teaser p[0] before structured fact p[1], "Stellplatz" word, district "44137 Innenstadt-West", EFH on plot
        ka_html = """
        <h1>Häuser zum Kauf in Dortmund</h1>
        <ul id="srchrslt-adtable">
          <article class="aditem">
            <a href="/s-anzeige/efh-dortmund/12345-208-1085">Einfamilienhaus auf 655 m² Grundstück</a>
            <p class="mb-xsmall text-bodyRegular text-onSurfaceSubdued">Auf rund 655 m² Grundstück inklusive Stellplatz erwartet Sie dieses Haus, vermietet für 1.400 €.</p>
            <p class="font-strong text-onSurfaceSubdued">220 m² · 6 Zi.</p>
            <p class="my-xsmall text-title3 font-strong text-secondary">460.000 €</p>
            <div>44137 Innenstadt-West</div>
          </article>
        </ul>
        """
        with patch("app.scrapers.kleinanzeigen.requests.get", return_value=MagicMock(status_code=200, text=ka_html)):
            deals = scrape_kleinanzeigen_deals("Dortmund", radius_km=10, property_type="all", max_results=5)
        self.assertEqual(len(deals), 1)
        d = deals[0]
        self.assertEqual(d["living_space"], 220.0)
        self.assertEqual(d["city"], "Dortmund")
        self.assertNotIn("Stellplatz", d["address"])
        self.assertEqual(d["property_type"], "Einfamilienhaus")
        self.assertEqual(d["is_rented"], 1)

    def test_04_geo_ulm_radius_zero_type_matcher_and_availability(self):
        from app.availability import _extract_portal_location
        from app.geo_resolver import _match_city_coord, is_within_search_radius, resolve_location_and_tax
        from app.scraper import matches_property_type, scrape_multi_portal_deals
        from bs4 import BeautifulSoup

        # Homonym & substring guards
        gi_c = _match_city_coord("giessen")
        self.assertIsNotNone(gi_c)
        self.assertAlmostEqual(gi_c[0], 50.5841, places=2)  # NOT Essen NRW (51.4556)!
        eb_c = _match_city_coord("essenbach")
        self.assertAlmostEqual(eb_c[0], 48.6136, places=2)  # NOT Essen NRW!
        hw_c = _match_city_coord("halle (westfalen)")
        self.assertAlmostEqual(hw_c[0], 52.0604, places=2)  # NOT Halle/Saale (51.4828)!

        # Ulm 890xx = BW (5.0%), Neu-Ulm 892xx = BY (3.5%)
        self.assertEqual(resolve_location_and_tax(postal_code="89073")["state"], "Baden-Württemberg")
        self.assertEqual(resolve_location_and_tax(postal_code="89073")["tax_rate"], 5.0)
        self.assertEqual(resolve_location_and_tax(postal_code="89231")["state"], "Bayern")
        self.assertEqual(resolve_location_and_tax(postal_code="89231")["tax_rate"], 3.5)

        # Unmapped unknown outside city must be rejected when radius filtering is active
        in_rad, _ = is_within_search_radius("Dortmund", None, 25, "UnbekanntesDorfAmEndeDerWelt", None, allow_geocoder=False)
        self.assertFalse(in_rad)

        # Property type matching
        self.assertTrue(matches_property_type("Eigentumswohnung", "Maisonette"))
        self.assertTrue(matches_property_type("Eigentumswohnung", "Penthouse"))
        self.assertFalse(matches_property_type("Mehrfamilienhaus", "Einfamilienhaus"))
        self.assertTrue(matches_property_type("Mehrfamilienhaus", "Zinshaus"))

        # Explicit portals parameter respected even if empty
        with patch("app.scraper.search_immoscout_deals", return_value=[]), \
             patch("app.scraper.scrape_kleinanzeigen_deals") as mock_ka:
            scrape_multi_portal_deals("Dortmund", portals=["immoscout24"])
        mock_ka.assert_not_called()

        # City-state locality preservation
        soup = BeautifulSoup('<span id="viewad-locality">10115 Berlin - Mitte</span>', "html.parser")
        loc = _extract_portal_location(soup, "")
        self.assertEqual(loc["postal_code"], "10115")
        self.assertIn("Berlin", loc["locality"])

        # Profile radius_km=0 roundtrip
        r_post = self.client.post(
            "/api/alerts/profiles",
            json={"name": "Null Radius", "city": "Dortmund", "radius_km": 0, "min_yield": 0},
            cookies=_auth_cookies(),
        )
        self.assertEqual(r_post.status_code, 200)
        prof = r_post.json()["profile"]
        self.assertEqual(prof["radius_km"], 0)
        self.client.delete(f"/api/alerts/profiles/{prof['id']}", cookies=_auth_cookies())

    def test_05_db_deal_alerts_per_profile_and_migration(self):
        from app.database import is_alert_already_logged, record_deal_alert

        # Create two distinct profiles and log same portal_url for both
        r1 = self.client.post("/api/alerts/profiles", json={"name": "P1", "city": "Essen", "radius_km": 10}, cookies=_auth_cookies()).json()["profile"]
        r2 = self.client.post("/api/alerts/profiles", json={"name": "P2", "city": "Essen", "radius_km": 20}, cookies=_auth_cookies()).json()["profile"]
        url = "https://www.immobilienscout24.de/expose/888777"
        record_deal_alert({
            "profile_id": r1["id"], "portal_url": url, "title": "T", "city": "Essen",
            "price": 150000, "living_space": 60, "price_per_sqm": 2500, "gross_yield": 6.0, "sent_to_telegram": 1,
        })
        self.assertTrue(is_alert_already_logged(url, profile_id=r1["id"]))
        self.assertFalse(is_alert_already_logged(url, profile_id=r2["id"]))
        record_deal_alert({
            "profile_id": r2["id"], "portal_url": url, "title": "T", "city": "Essen",
            "price": 150000, "living_space": 60, "price_per_sqm": 2500, "gross_yield": 6.0, "sent_to_telegram": 1,
        })
        self.assertTrue(is_alert_already_logged(url, profile_id=r2["id"]))

    def test_06_valuation_calculator_text_audit_vision_and_scoring(self):
        from app.ai_analyst import (
            _compute_holistic_deal_verdict,
            extract_listing_insights,
            generate_ai_due_diligence_report,
            reconcile_capex_with_images,
        )
        from app.calculator import calculate_cashflow
        from app.image_analyst import analyze_property_images, is_ai_generated_photo
        from app.valuation import evaluate_property_valuation, get_city_benchmark

        # "unsaniert" MUST trigger 0.75 discount, NOT 1.10 premium
        val_unsaniert = evaluate_property_valuation("Dortmund", 80.0, 120000.0, condition="unsaniert", property_type="Eigentumswohnung")
        val_gepflegt = evaluate_property_valuation("Dortmund", 80.0, 120000.0, condition="gepflegt", property_type="Eigentumswohnung")
        self.assertLess(val_unsaniert["estimated_market_value"], val_gepflegt["estimated_market_value"])

        # Word boundary benchmark lookup
        bm = get_city_benchmark("Essenbach", postal_code="84051")
        self.assertEqual(bm["state"], "Bayern")
        self.assertEqual(bm["city"], "Essenbach")

        # Calculator forwards state + fixed_commission into mortgage quote
        captured_q = {}
        def _mock_quote(**kwargs):
            captured_q.update(kwargs)
            return {"nominal_rate": 3.5, "effective_rate": 3.6, "bank_name": "Test", "is_live_quote": True}
        with patch("app.interest_service.get_property_mortgage_interest_quote", side_effect=_mock_quote):
            calculate_cashflow(price=200000, rent=900, city="München", state="Bayern", fixed_commission=5000.0)
        self.assertEqual(captured_q.get("state"), "Bayern")
        self.assertEqual(captured_q.get("city"), "München")
        self.assertEqual(captured_q.get("fixed_commission"), 5000.0)

        # Text audit fixes:
        # 1) Hausgeld inkl. NK not treated as Kaltmiete
        # 2) Hausgeld sentence breakdown not summed (280 + 195 + 85 != 560)
        # 3) Kaufpreis Tiefgarage doesn't overwrite/trigger price discrepancy
        # 4) ETW in MFH not flagged as is_mfh=True
        # 5) "kein Erbbaurecht", "kein Leerstand", "unrenoviert" handled accurately
        desc = (
            "Schöne unrenovierte 3-Zimmer-Eigentumswohnung in gepflegtem MFH mit 8 Einheiten. "
            "Kein Leerstand bei den Stellplätzen. Das monatliche Hausgeld beträgt 280 € (inkl. 120 € Nebenkosten und 60 € Rücklage). "
            "Kein Erbbaurecht. Der Kaufpreis für die Tiefgarage beträgt 22.000 €. Bezogen oder unvermietet zu 900 € möglich."
        )
        prop_etw = {
            "id": 9123,
            "title": "3-Zimmer-Eigentumswohnung in Dortmund",
            "city": "Dortmund",
            "postal_code": "44137",
            "property_type": "Eigentumswohnung",
            "purchase_price": 180000.0,
            "living_space": 75.0,
            "year_built": 1978,
            "commission_percent": 3.57,
            "is_rented": 0,
            "description": desc,
        }
        ins = extract_listing_insights(desc, {}, prop_etw)
        self.assertIsNone(ins["extracted_rent"])
        self.assertFalse(ins["is_rented"])
        self.assertEqual(ins["hausgeld"], 280.0)
        self.assertIsNone(ins["stated_purchase_price_in_text"])
        self.assertFalse(ins["is_erbbaurecht"])
        self.assertFalse(ins["is_mfh"])
        self.assertTrue(ins["needs_renovation"])
        self.assertFalse(ins["is_renoviert"])

        # Preserve llm_capex_adjustments across generate + reconcile_capex_with_images
        llm_mock = {
            "is_rented": False,
            "is_vacant": True,
            "rent_is_verified_in_text": False,
            "needs_renovation": True,
            "capex_adjustments": [
                {"title": "Asbest-Bodenplatten entfernen", "amount": 6000.0, "urgency": "HOCH", "source_type": "EXPOSE_TEXT", "description": "Spezialbefund"}
            ],
            "risk_highlights": [],
            "executive_summary": "Objekt benötigt Sanierung.",
            "token_usage": {"prompt_tokens": 10, "candidates_tokens": 10, "total_tokens": 20},
        }
        with patch("app.ai_analyst.call_llm_text_analysis", return_value=llm_mock), \
             patch("app.interest_service.get_property_mortgage_interest_quote", return_value={"nominal_rate": 3.8, "effective_rate": 3.9, "bank_name": "B", "is_live_quote": False}):
            rep = generate_ai_due_diligence_report(prop_etw, full_description=desc, api_key="dummy")
            reconciled = reconcile_capex_with_images(rep, prop_etw, {"photos_analyzed_count": 1, "has_interior_photos": True, "overall_visual_score": 6.5, "analyzed_photos": [{"index": 1, "tag": "🛋️ Wohnzimmer"}], "visual_capex_items": [], "total_visual_capex": 0.0})
        self.assertTrue(any("Asbest" in it["title"] for it in reconciled["capex"]["items"]))

        # Severe negative cashflow (-900 EUR/M) MUST NOT receive GREEN even if yield >= 2.5%
        verdict_bad_cf = _compute_holistic_deal_verdict(
            {"purchase_price": 400000, "living_space": 80, "gross_yield": 3.1, "market_deviation_percent": -16.0, "monthly_cashflow": -900.0, "potential_monthly_cashflow": -900.0, "ancillary_costs": 40000, "rent_is_verified": True},
            {"is_kernsaniert": False, "commission_free": True},
            {"total_capex": 0.0, "items": []},
            [],
        )
        self.assertNotEqual(verdict_bad_cf["status"], "GREEN")

        # Empty photo gallery -> NO Unsplash injection, NO_PHOTOS, has_interior_photos=False
        no_photo_res = analyze_property_images({"id": 55, "living_space": 60, "description": "Schöne Wohnung in guter Lage mit Balkon." * 2}, image_urls=[])
        self.assertEqual(no_photo_res["vision_mode"], "NO_PHOTOS")
        self.assertFalse(no_photo_res["has_interior_photos"])
        self.assertEqual(no_photo_res["photos_analyzed_count"], 0)

        # Negation check in AI staging detector
        self.assertFalse(is_ai_generated_photo("🛋️ Wohnzimmer", "Echtes Foto ohne Virtual Staging und keine Visualisierung."))
        self.assertTrue(is_ai_generated_photo("🛋️ Wohnzimmer", "Virtuell möbliert (Virtual Staging) für Beispielansicht."))

        # Audit cache invalidation when previously audited with skip_images=True and photos exist
        conn_c = get_connection()
        try:
            cur_c = conn_c.cursor()
            long_desc = (
                "Schöne gepflegte 2-Zimmer-Wohnung in guter zentraler Lage von Dortmund mit Balkon, "
                "hellen Räumen, gepflegter Substanz und solider Mietrendite für Kapitalanleger."
            )
            cur_c.execute(
                "INSERT INTO properties (title, address, postal_code, city, state, property_type, living_space, purchase_price, source_portal, portal_url, description, gallery_images, is_online, availability_status) "
                "VALUES ('Cache Test', 'Str 1, 44137 Dortmund', '44137', 'Dortmund', 'Nordrhein-Westfalen', 'Eigentumswohnung', 65.0, 140000.0, 'Immowelt', 'https://www.immowelt.de/expose/cache-inv-1', ?, ?, 1, 'online')",
                (long_desc, json.dumps(["https://mms.immowelt.de/test/img1.jpg"])),
            )
            cache_pid = cur_c.lastrowid
            conn_c.commit()
        finally:
            conn_c.close()

        with patch("app.image_analyst.analyze_property_images", return_value={
            "photos_analyzed_count": 1, "has_interior_photos": False, "overall_visual_score": 4.5,
            "analyzed_photos": [{"index": 1, "tag": "🏢 Fassade / Außenansicht", "url": "https://mms.immowelt.de/test/img1.jpg"}],
            "visual_capex_items": [], "total_visual_capex": 0.0, "vision_skipped": False,
        }) as mock_vis, patch("app.interest_service.get_property_mortgage_interest_quote", return_value={"nominal_rate": 3.8, "effective_rate": 3.9, "bank_name": "B", "is_live_quote": False}):
            r_skip = self.client.post(f"/api/properties/{cache_pid}/ai-audit?skip_images=true", cookies=_auth_cookies())
            self.assertEqual(r_skip.status_code, 200)
            self.assertEqual(mock_vis.call_count, 0)
            r_full = self.client.post(f"/api/properties/{cache_pid}/ai-audit", cookies=_auth_cookies())
            self.assertEqual(r_full.status_code, 200)
            self.assertEqual(mock_vis.call_count, 1)
            self.assertEqual(r_full.json()["ai_audit"]["status"], "YELLOW")

        # URL Schnellimport detects Mehrfamilienhaus instead of hardcoding Eigentumswohnung
        with patch("app.scrapers.immowelt.fetch_immowelt_expose", return_value={
            "success": True, "title": "Renditestarkes Mehrfamilienhaus mit 4 Parteien",
            "description": "Gepflegtes Mehrfamilienhaus mit 4 vermieteten Wohnungen in guter Lage.",
            "price": 450000.0, "living_space": 280.0, "rooms": 12.0, "city": "Dortmund", "postal_code": "44137",
            "images": ["https://mms.immowelt.de/mfh/1.jpg"], "details": {},
        }), patch("app.interest_service.get_property_mortgage_interest_quote", return_value={"nominal_rate": 3.8, "effective_rate": 3.9, "bank_name": "B", "is_live_quote": False}), \
             patch("app.routers.properties.run_property_ai_audit", return_value={"ai_audit": {"score": 7.8}}):
            r_imp = self.client.post(
                "/api/properties/import-url",
                json={"url": "https://www.immowelt.de/expose/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
                cookies=_auth_cookies(),
            )
            self.assertEqual(r_imp.status_code, 200)
            self.assertEqual(r_imp.json()["property"]["property_type"], "Mehrfamilienhaus")

    def test_07_search_agent_actual_yield_gatekeeper_and_error_safety(self):
        from app.database import delete_search_profile, save_search_profile
        from app.search_agent import execute_search_profile

        pid = save_search_profile({
            "name": "Ist Rendite Strict",
            "city": "Essen",
            "radius_km": 10,
            "min_yield": 5.5,
            "yield_basis": "actual",
            "is_active": True,
        })
        fake_deals = [{
            "title": "Vermietet laut Teaser ohne Miete",
            "city": "Essen",
            "postal_code": "45127",
            "property_type": "Eigentumswohnung",
            "purchase_price": 120000.0,
            "living_space": 60.0,
            "rooms": 2.0,
            "is_rented": 1,
            "current_rent": 0.0,
            "gross_yield": 6.5,
            "portal_url": "https://www.immobilienscout24.de/expose/555666",
            "source_portal": "ImmoScout24",
        }]
        # Audited actual yield is 0.0% while benchmark potential is 6.8%
        fake_audit = {
            "ai_audit": {
                "score": 8.0,
                "status": "GREEN",
                "status_label": "Solider Deal",
                "metrics": {
                    "purchase_price": 120000.0, "living_space": 60.0,
                    "gross_yield": 6.8, "actual_gross_yield": 0.0, "potential_gross_yield": 6.8,
                    "rent_is_verified": False, "monthly_cashflow": 50.0, "potential_monthly_cashflow": 50.0,
                    "market_deviation_percent": -10.0,
                },
                "insights": {"corrected_city": "Essen", "postal_code": "45127", "corrected_property_type": "Eigentumswohnung", "full_description": "Vermietet"},
                "capex": {"total_capex": 0.0},
                "risks": [],
            }
        }
        try:
            with patch("app.search_agent.scrape_multi_portal_deals", return_value=fake_deals), \
                 patch("app.routers.properties.run_property_ai_audit", return_value=fake_audit):
                res = execute_search_profile(pid, force=True)
            self.assertEqual(res["qualified_deals"], 0)
            self.assertEqual(res["new_alerts"], 0)

            # Audit raising exception MUST NOT default to 7.0 and trigger Telegram alert!
            with patch("app.search_agent.scrape_multi_portal_deals", return_value=fake_deals), \
                 patch("app.routers.properties.run_property_ai_audit", side_effect=RuntimeError("API down")):
                res_err = execute_search_profile(pid, force=True)
            self.assertEqual(res_err["qualified_deals"], 0)
            self.assertEqual(res_err["new_alerts"], 0)
        finally:
            delete_search_profile(pid)


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        try:
            _TMP_DIR.cleanup()
        except Exception:
            pass
