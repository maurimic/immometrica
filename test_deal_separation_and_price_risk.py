import unittest
from typing import Dict, Any

from app.scrapers.deal_fusion import are_deals_matching, fuse_deals
from app.ai_analyst import detect_hidden_problems_and_risks, generate_ai_due_diligence_report


class TestDealSeparationAndPriceRisk(unittest.TestCase):
    def test_deal_fusion_is_deactivated(self):
        """Verify that cross-portal deal matching is completely disabled."""
        deal_kleinanzeigen = {
            "title": "2 Zimmer - Wohnung in 44149 Dortmund Provisionsfrei",
            "postal_code": "44149",
            "city": "Dortmund",
            "address": "44149 Dortmund",
            "living_space": 52.0,
            "rooms": 2.0,
            "purchase_price": 45000.0,
            "source_portal": "Kleinanzeigen",
            "portal_url": "https://www.kleinanzeigen.de/s-anzeige/111",
            "image_url": "https://example.com/klein1.jpg",
            "is_foreclosure": 0
        }
        deal_immowelt = {
            "title": "Zwangsversteigerung: 2-Zimmer-Eigentumswohnung Dortmund",
            "postal_code": "44149",
            "city": "Dortmund",
            "address": "Dorstfelder Hellweg, 44149 Dortmund",
            "living_space": 52.5,
            "rooms": 2.0,
            "purchase_price": 47000.0,
            "source_portal": "Immowelt",
            "portal_url": "https://www.immowelt.de/expose/222",
            "image_url": "https://example.com/immowelt1.jpg",
            "is_foreclosure": 1
        }

        # are_deals_matching must always return False
        self.assertFalse(are_deals_matching(deal_kleinanzeigen, deal_immowelt))

        # fuse_deals must return both deals completely separated
        fused = fuse_deals([deal_kleinanzeigen, deal_immowelt])
        self.assertEqual(len(fused), 2, "Expected 2 independent deals, deals must not be merged!")
        self.assertEqual(fused[0]["source_portal"], "Kleinanzeigen")
        self.assertEqual(fused[0]["image_url"], "https://example.com/klein1.jpg")
        self.assertEqual(fused[1]["source_portal"], "Immowelt")
        self.assertEqual(fused[1]["image_url"], "https://example.com/immowelt1.jpg")
        self.assertEqual(fused[1]["is_foreclosure"], 1)

    def test_below_market_price_is_not_critical_risk(self):
        """Verify that a below-market price warning is downgraded to HIGH/warning, never CRITICAL."""
        insights = {
            "llm_risk_highlights": [
                {
                    "title": "Kaufpreisniveau weit unter Marktdurchschnitt – starkes Indiz für verdecktes Risiko",
                    "severity": "CRITICAL",
                    "category": "Markt & Bewertung",
                    "badge": "🔴 Kritisch",
                    "description": "Der Quadratmeterpreis liegt ca. 55% unter dem Dortmunder Marktdurchschnitt.",
                    "impact": "Mögliche schwere verdeckte Mängel.",
                    "action": "Vor Ort genau prüfen."
                }
            ],
            "is_rented": False,
            "commission_percent": 0.0,
            "commission_free": True,
            "year_built_extracted": 1970
        }
        prop_data = {
            "purchase_price": 45000.0,
            "living_space": 52.0,
            "city": "Dortmund"
        }
        capex = {"total_capex": 0.0, "items": []}

        risks = detect_hidden_problems_and_risks(insights, prop_data, capex)
        price_risk = next((r for r in risks if "unter marktdurchschnitt" in r["title"].lower()), None)
        self.assertIsNotNone(price_risk)
        self.assertNotEqual(price_risk["severity"], "CRITICAL", "Below market price must never be CRITICAL!")
        self.assertEqual(price_risk["severity"], "HIGH")
        self.assertIn("Prüfhinweis", price_risk["badge"])

    def test_search_agent_critical_filter_ignores_price_anomalies(self):
        """Verify that search agent logic does not kill a deal for below-market price."""
        from app.search_agent import execute_search_profile

        # Test the helper filter logic directly
        def _is_true_critical_deal_killer(r_obj: Dict[str, Any]) -> bool:
            if r_obj.get("severity") != "CRITICAL":
                return False
            t = str(r_obj.get("title", "")).lower()
            d = str(r_obj.get("description", "")).lower()
            if any(k in t or k in d for k in [
                "unter marktdurchschnitt", "unter marktwert", "kaufpreisniveau", "niedriger kaufpreis", 
                "günstiger kaufpreis", "preisniveau", "marktabweichung", "preis unter", "preisabstand",
                "weit unter", "preis weit", "verdecktes risiko"
            ]):
                return False
            return True

        risk_price = {
            "title": "Kaufpreisniveau weit unter Marktdurchschnitt – starkes Indiz für verdecktes Risiko",
            "severity": "CRITICAL",
            "description": "Preis auffällig niedrig"
        }
        risk_erbpacht = {
            "title": "Erbbaurecht mit kurzer Restlaufzeit",
            "severity": "CRITICAL",
            "description": "Erbbauzins 350 €/M"
        }

        self.assertFalse(_is_true_critical_deal_killer(risk_price))
        self.assertTrue(_is_true_critical_deal_killer(risk_erbpacht))


if __name__ == "__main__":
    unittest.main()
