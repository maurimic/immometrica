import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Isolate tests onto a temporary database BEFORE importing app modules
_TMP_DIR = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_TMP_DIR.name, "test_mfh_units.db")
os.environ["TESTING"] = "1"

from fastapi.testclient import TestClient  # noqa: E402
from app.auth import COOKIE_NAME, get_auth_config, hash_session_token  # noqa: E402
from app.database import get_connection, init_db  # noqa: E402
from app.main import app  # noqa: E402


def _auth_cookies():
    cfg = get_auth_config()
    token = hash_session_token(cfg["username"], cfg["password"], cfg["secret"])
    return {COOKIE_NAME: token}


def _mock_quote(**kwargs):
    return {
        "nominal_rate": 3.85,
        "effective_rate": 3.95,
        "bank_name": "MockBank",
        "is_live_quote": True,
        "rates_by_term": [{"years": 10, "nominal_rate": 3.85}],
    }


DEAL_49_DESCRIPTION = """### Objektbeschreibung
Bieterverfahren!!! Kapitalanlage mit Potenzial im Essener Ostviertel – 5 Wohneinheiten auf ca. 258 m² Mieteinnahmen per Anno 25.921,32 Euro
Zum Verkauf steht ein gepflegtes Mehrfamilienhaus mit 5 Wohneinheiten in zentraler Lage des Essener Ostviertels. Die Immobilie wurde über viele Jahre kontinuierlich modernisiert und bietet damit eine interessante Basis für Kapitalanleger.
Die Wohnungen verteilen sich auf ca. 258 m² Wohnfläche:
EG rechts ca. 24 m², EG links ca. 24 m², 1. OG ca. 61 m², 2. OG ca. 61 m² sowie DG ca. 88 m².
In den vergangenen Jahren wurden zahlreiche wichtige Maßnahmen bereits durchgeführt: Wärmeschutzfassade 2012, Dach inklusive Dämmung und Dachfenstern 2014, Gas-Brennwerttherme 2016 sowie sukzessive erneuerte Fenster zwischen 1995 und 2014. Auch die Wohnungen wurden laufend renoviert, zuletzt die Einheit im 1. OG im Jahr 2026. Die Wohnung im 2. OG wurde 2019 inklusive Badezimmer modernisiert, die Dachgeschosswohnung 2014 renoviert.

### Sonstiges
Wohnung EG rechts 1995 renoviert
Wohnung EG links  ca. 24m2,1998 renoviert 2909,40 Euro
Wohnung EG rechts ca. 24m2, 2.808,00 Euro
Wohnung I. OG 2026  ca. 61m2 renoviert in 2026 , 6.514,80 Euro
(derzeit leer/ Mieterwechsel)
Wohnung II. OG mit ca. 61m2 Nettokaltmiete renoviert in 2020 , 5.892,12 Euro
Wohnung 5. mit ca. 88m2 Nettokaltmiete 7.860,00 Euro DG
(Maisonette)
Alle Mietangaben verstehen sich per Anno. Gesamt 25.921,32 Euro"""


class TestMfhUnitsAndAudit(unittest.TestCase):
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

    def test_01_deal49_deterministic_per_anno_partial_vacancy(self):
        from app.ai_analyst import extract_listing_insights, generate_ai_due_diligence_report

        prop = {
            "id": 49,
            "title": "Spannende Kapitalanlage",
            "city": "Essen",
            "state": "Nordrhein-Westfalen",
            "postal_code": "45141",
            "property_type": "Mehrfamilienhaus",
            "purchase_price": 399000.0,
            "living_space": 256.0,
            "year_built": 1958,
            "portal_url": "https://www.immobilienscout24.de/expose/172431710",
            "description": DEAL_49_DESCRIPTION,
        }

        insights = extract_listing_insights(DEAL_49_DESCRIPTION, {}, prop)
        self.assertTrue(insights["is_mfh"])
        self.assertEqual(insights["unit_count_in_offer"], 5)
        self.assertEqual(len(insights["units"]), 5)
        self.assertTrue(insights["has_partial_vacancy"])
        self.assertTrue(insights["is_rented"])
        self.assertFalse(insights["is_vacant"])
        self.assertEqual(insights["rent_source"], "EXPOSE_STATED_UNITS")

        # Sum of 4 rented units (2909.40 + 2808.00 + 5892.12 + 7860.00) / 12 = 1622.46 EUR/M
        self.assertAlmostEqual(insights["extracted_rent"], 1622.46, places=2)
        # Stated annual total 25921.32 / 12 = 2160.11 EUR/M
        self.assertAlmostEqual(insights["target_expose_monthly_rent"], 2160.11, places=2)
        # Stated total (25921.32) vs unit sum (25984.32) discrepancy
        self.assertIsNotNone(insights["unit_sum_discrepancy"])
        self.assertAlmostEqual(insights["unit_sum_discrepancy"]["difference_annual"], 63.0, places=2)

        with patch("app.interest_service.get_property_mortgage_interest_quote", side_effect=_mock_quote):
            report = generate_ai_due_diligence_report(prop, full_description=DEAL_49_DESCRIPTION, api_key=None)

        m = report["metrics"]
        self.assertTrue(m["is_mfh"])
        self.assertTrue(m["rent_is_verified"])
        self.assertTrue(m["is_rented"])
        self.assertAlmostEqual(m["current_monthly_rent"], 1622.46, places=2)
        self.assertAlmostEqual(m["gross_yield"], 4.88, places=2)
        self.assertAlmostEqual(m["target_expose_monthly_rent"], 2160.11, places=2)
        self.assertAlmostEqual(m["target_expose_gross_yield"], 6.50, places=2)
        # Realistic MFH operating costs: 256 m² * 1.50 EUR/m² = 384.00 EUR/M (not 50 EUR ETW default!)
        self.assertAlmostEqual(m["additional_costs"], 384.0, places=2)
        self.assertIn("MFH", m["operating_costs_label"])
        self.assertIn("4/5 Einheiten vermietet", m["rent_badge"])
        self.assertNotIn("Großraum", m["mietspiegel_size_label"])

        risk_titles = " | ".join(r.get("title", "") for r in report["risks"])
        self.assertIn("Teilleerstand", risk_titles)
        self.assertIn("Abweichung Gesamtmiete", risk_titles)

        doc_ids = {d["id"] for d in report["missing_documents"]}
        self.assertIn("mfh_rent_roll", doc_ids)

    def test_02_monthly_mfh_listing_full_occupancy_no_false_annual_division(self):
        from app.ai_analyst import extract_listing_insights, generate_ai_due_diligence_report

        monthly_desc = (
            "Gepflegtes 3-Familienhaus (Mehrfamilienhaus) mit ca. 180 m² Wohnfläche aufgeteilt auf 3 Wohnungen.\n"
            "Alle 3 Wohnungen sind solide und langjährig vermietet:\n"
            "- Wohnung EG links ca. 60 m² Kaltmiete: 520,00 € monatlich\n"
            "- Wohnung 1. OG ca. 60 m² Kaltmiete: 580,00 € pro Monat\n"
            "- Wohnung DG ca. 60 m² Kaltmiete: 490,00 € mtl.\n"
            "Kein Reparaturstau, Gas-Brennwerttherme 2019."
        )
        prop = {
            "id": 5001,
            "title": "Solides 3-FH in Dortmund",
            "city": "Dortmund",
            "state": "Nordrhein-Westfalen",
            "property_type": "Mehrfamilienhaus",
            "purchase_price": 280000.0,
            "living_space": 180.0,
            "year_built": 1972,
            "description": monthly_desc,
        }

        insights = extract_listing_insights(monthly_desc, {}, prop)
        self.assertTrue(insights["is_mfh"])
        self.assertEqual(insights["unit_count_in_offer"], 3)
        self.assertFalse(insights["has_partial_vacancy"])
        self.assertAlmostEqual(insights["extracted_rent"], 1590.0, places=2)

        with patch("app.interest_service.get_property_mortgage_interest_quote", side_effect=_mock_quote):
            report = generate_ai_due_diligence_report(prop, full_description=monthly_desc, api_key=None)

        m = report["metrics"]
        self.assertAlmostEqual(m["current_monthly_rent"], 1590.0, places=2)
        self.assertAlmostEqual(m["gross_yield"], 6.81, places=2)
        self.assertAlmostEqual(m["additional_costs"], 270.0, places=2)
        self.assertIn("3 Einheiten", m["rent_badge"])

    def test_03_llm_merge_corrects_partial_vacancy_soll_as_ist_confusion(self):
        from app.ai_analyst import _merge_llm_into_insights, extract_listing_insights

        prop = {
            "id": 49,
            "property_type": "Mehrfamilienhaus",
            "purchase_price": 399000.0,
            "living_space": 256.0,
        }
        base = extract_listing_insights(DEAL_49_DESCRIPTION, {}, prop)

        # Simulate an LLM that extracted all 5 units accurately (with I. OG vacant),
        # but accidentally wrote the full-occupancy total (2160.11) into current_cold_rent_monthly.
        llm_raw = {
            "corrected_property_type": "Mehrfamilienhaus",
            "unit_count_in_offer": 5,
            "is_package_deal": False,
            "is_rented": True,
            "is_vacant": False,
            "has_partial_vacancy": True,
            "rent_is_verified_in_text": True,
            "rent_period_stated": "annual",
            "current_cold_rent_monthly": 2160.11,
            "target_cold_rent_monthly": None,
            "annual_cold_rent": 25921.32,
            "units": [
                {"label": "EG links", "sqm": 24, "is_rented": True, "cold_rent_monthly": 2909.40, "raw_period": "annual"},
                {"label": "EG rechts", "sqm": 24, "is_rented": True, "cold_rent_monthly": 2808.00, "raw_period": "annual"},
                {"label": "I. OG", "sqm": 61, "is_rented": False, "cold_rent_monthly": 0.0, "target_rent_monthly": 6514.80, "raw_period": "annual"},
                {"label": "II. OG", "sqm": 61, "is_rented": True, "cold_rent_monthly": 5892.12, "raw_period": "annual"},
                {"label": "5. / DG (Maisonette)", "sqm": 88, "is_rented": True, "cold_rent_monthly": 7860.00, "raw_period": "annual"},
            ],
        }
        merged = _merge_llm_into_insights(base, llm_raw, prop)
        self.assertTrue(merged["is_mfh"])
        self.assertTrue(merged["has_partial_vacancy"])
        self.assertTrue(merged["is_rented"])
        self.assertFalse(merged["is_vacant"])
        self.assertEqual(merged["rent_source"], "EXPOSE_STATED_UNITS")
        self.assertAlmostEqual(merged["extracted_rent"], 1622.46, places=2)
        self.assertAlmostEqual(merged["target_expose_monthly_rent"], 2160.11, places=2)

    def test_04_valuation_unit_size_benchmark_for_mfh(self):
        from app.valuation import get_mietspiegel_size_benchmark

        units = [
            {"label": "EG links", "sqm": 24.0},
            {"label": "EG rechts", "sqm": 24.0},
            {"label": "I. OG", "sqm": 61.0},
            {"label": "II. OG", "sqm": 61.0},
            {"label": "DG", "sqm": 88.0},
        ]
        bm_mfh = get_mietspiegel_size_benchmark("Essen", 256.0, is_package_deal=False, unit_count=5, units=units)
        bm_single = get_mietspiegel_size_benchmark("Essen", 256.0, is_package_deal=False, unit_count=1)

        self.assertNotIn("Großraum", bm_mfh["size_label"])
        self.assertIn("5 WE", bm_mfh["size_label"])
        self.assertGreater(bm_mfh["expected_sqm_rent"], bm_single["expected_sqm_rent"])

    def test_05_analyze_images_upgrades_stale_fallback_audit_and_ui_synced(self):
        from app import ai_analyst, image_analyst

        conn = get_connection()
        try:
            c = conn.cursor()
            stale_ai = json.dumps({
                "used_llm": False,
                "metrics": {"current_monthly_rent": 7860.0, "gross_yield": 23.64},
            })
            c.execute(
                "INSERT INTO properties (title, city, state, postal_code, property_type, living_space, purchase_price, "
                "source_portal, portal_url, description, ai_analysis, image_url, gallery_images) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "Spannende Kapitalanlage",
                    "Essen",
                    "Nordrhein-Westfalen",
                    "45141",
                    "Mehrfamilienhaus",
                    256.0,
                    399000.0,
                    "ImmoScout24",
                    "https://www.immobilienscout24.de/expose/172431710",
                    DEAL_49_DESCRIPTION,
                    stale_ai,
                    "https://example.com/1.jpg",
                    json.dumps(["https://example.com/1.jpg"]),
                ),
            )
            pid = c.lastrowid
            conn.commit()
        finally:
            conn.close()

        fake_img_report = {
            "ai_provider": "OneProvider claude-haiku-5-5 Vision",
            "overall_visual_score": 7.0,
            "condition_rating": "GEPFLEGT",
            "condition_label": "Gepflegt",
            "has_interior_photos": True,
            "analyzed_photos": [{"url": "https://example.com/1.jpg", "tag": "Hausansicht"}],
            "visual_capex_items": [],
            "total_visual_capex": 0.0,
            "detected_findings": [],
        }

        fake_llm_report = {
            "ai_provider": "OneProvider claude-haiku-5-5",
            "corrected_property_type": "Mehrfamilienhaus",
            "unit_count_in_offer": 5,
            "is_package_deal": False,
            "is_rented": True,
            "is_vacant": False,
            "has_partial_vacancy": True,
            "rent_is_verified_in_text": True,
            "rent_period_stated": "annual",
            "current_cold_rent_monthly": 1622.46,
            "target_cold_rent_monthly": 2160.11,
            "annual_cold_rent": 25921.32,
            "units": [
                {"label": "EG links", "sqm": 24, "is_rented": True, "cold_rent_monthly": 242.45},
                {"label": "EG rechts", "sqm": 24, "is_rented": True, "cold_rent_monthly": 234.00},
                {"label": "I. OG", "sqm": 61, "is_rented": False, "cold_rent_monthly": 0.0, "target_rent_monthly": 542.90},
                {"label": "II. OG", "sqm": 61, "is_rented": True, "cold_rent_monthly": 491.01},
                {"label": "5. / DG", "sqm": 88, "is_rented": True, "cold_rent_monthly": 655.00},
            ],
            "executive_summary": "Gepflegtes 5-FH in Essen mit Teilleerstand.",
        }

        with patch("app.routers.properties.analyze_property_images", return_value=fake_img_report), \
             patch("app.ai_analyst.call_llm_text_analysis", return_value=fake_llm_report), \
             patch("app.interest_service.get_property_mortgage_interest_quote", side_effect=_mock_quote):
            res = self.client.post(
                f"/api/properties/{pid}/analyze-images",
                json={"api_key": "sk-dummy-test-key-1234567890"},
                cookies=_auth_cookies(),
            )

        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["ai_audit"]["used_llm"])
        self.assertIn("property_updates", body)
        self.assertAlmostEqual(body["property_updates"]["current_rent"], 1622.46, places=2)
        self.assertAlmostEqual(body["property_updates"]["gross_yield"], 4.88, places=2)

        html = Path("static/index.html").read_text(encoding="utf-8")
        self.assertIn("Einzelaufstellung", html)
        self.assertIn("target_expose_monthly_rent", html)
        self.assertIn("operating_costs_label", html)


if __name__ == "__main__":
    unittest.main()
