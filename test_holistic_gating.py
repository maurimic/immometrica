import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.ai_analyst import generate_ai_due_diligence_report
from app.image_analyst import should_run_vision_ai, select_key_investment_photos, analyze_property_images


def test_stage1_offline_knockout():
    """Verify that offline listings are immediately knocked out with Score 1.0 and 0 tokens."""
    prop_offline = {
        "id": 9991,
        "title": "Offline Deal Test",
        "purchase_price": 120000.0,
        "living_space": 60.0,
        "is_online": 0,
        "availability_status": "offline",
        "portal_url": "https://www.immobilienscout24.de/expose/123456",
        "description": "Schöne 2-Zimmer-Wohnung in toller Lage mit Balkon und guter Rendite."
    }
    res = generate_ai_due_diligence_report(prop_offline, full_description=prop_offline["description"])
    assert res["score"] == 1.0
    assert res["status"] == "RED"
    assert "offline" in res["status_label"].lower()
    assert res["used_llm"] is False
    assert res["knockout_reason"] == "OFFLINE"
    print("✓ test_stage1_offline_knockout passed")


def test_stage1_missing_description_knockout():
    """Verify that listings with 0 or <50 characters description are knocked out with Score 2.0."""
    prop_no_desc = {
        "id": 9992,
        "title": "Deal without description",
        "purchase_price": 150000.0,
        "living_space": 70.0,
        "is_online": 1,
        "availability_status": "online",
        "portal_url": "https://www.immobilienscout24.de/expose/987654",
        "description": ""
    }
    res = generate_ai_due_diligence_report(prop_no_desc, full_description="")
    assert res["score"] == 2.0
    assert res["status"] == "RED"
    assert "nicht prüfbar" in res["status_label"].lower()
    assert res["used_llm"] is False
    assert res["knockout_reason"] == "MISSING_DESCRIPTION"
    print("✓ test_stage1_missing_description_knockout passed")


def test_stage1_search_url_knockout():
    """Verify that generic search overview URLs without an expose are rejected."""
    prop_search = {
        "id": 9993,
        "title": "Search URL Deal",
        "purchase_price": 200000.0,
        "living_space": 80.0,
        "is_online": 1,
        "availability_status": "online",
        "portal_url": "https://www.immobilienscout24.de/Suche/de/nordrhein-westfalen/essen/wohnung-kaufen",
        "description": "Kurzer Text"
    }
    res = generate_ai_due_diligence_report(prop_search, full_description=prop_search["description"])
    assert res["score"] == 2.0
    assert res["status"] == "RED"
    assert res["knockout_reason"] == "MISSING_DESCRIPTION"
    print("✓ test_stage1_search_url_knockout passed")


def test_stage3_vision_gating_unprofitable():
    """Verify that Vision AI is skipped for economically toxic listings to save tokens."""
    prop_unprofitable = {
        "id": 9994,
        "title": "Toxischer Deal",
        "purchase_price": 950000.0,
        "living_space": 50.0,
        "is_online": 1,
        "availability_status": "online",
        "description": "Eine sehr teure Luxus-Wohnung mit geringer Miete und sehr hohem Hausgeld." * 2
    }
    metrics = {
        "gross_yield": 1.9,
        "monthly_cashflow": -450.0,
        "potential_monthly_cashflow": -450.0,
        "mietspiegel_analysis": {"turns_profitable": False}
    }
    should_run, reason = should_run_vision_ai(prop_unprofitable, metrics=metrics, full_description=prop_unprofitable["description"])
    assert should_run is False
    assert "unrentabel" in reason.lower()

    # Test analyze_property_images with this property
    img_res = analyze_property_images(prop_unprofitable, metrics=metrics, full_description=prop_unprofitable["description"])
    assert img_res["vision_skipped"] is True
    assert img_res["vision_mode"] == "SKIPPED_UNPROFITABLE"
    assert img_res["photos_analyzed_count"] == 0
    print("✓ test_stage3_vision_gating_unprofitable passed")


def test_stage3_all_photos_analysis():
    """Verify that all images in a gallery are analyzed without limiting to key photos."""
    gallery_15 = [f"https://example.com/photo_{i}.jpg" for i in range(1, 16)]
    prop = {
        "id": 9996,
        "title": "Full Gallery Test",
        "purchase_price": 100000.0,
        "living_space": 50.0,
        "year_built": 1985,
        "condition": "Gepflegt",
        "gallery_images": gallery_15,
        "is_online": 1,
        "availability_status": "online",
        "description": "Schöne 2-Zimmer-Wohnung in toller Lage mit Balkon und guter Rendite." * 3
    }
    metrics = {"gross_yield": 6.0, "monthly_cashflow": 100.0}
    img_res = analyze_property_images(prop, metrics=metrics, full_description=prop["description"])
    assert img_res["photos_analyzed_count"] == 15
    assert len(img_res["analyzed_photos"]) == 15
    assert img_res["vision_mode"] == "ALL_PHOTOS"
    assert img_res["vision_skipped"] is False
    print("✓ test_stage3_all_photos_analysis passed")


def test_stage4_value_add_recognition():
    """
    CRITICAL USER REQUIREMENT:
    Verify that an unrenovated property with dated photos / renovation need is NOT downgraded to red,
    but recognized and awarded 🟢 Value-Add Empfehlung when all-in economics work out!
    """
    prop_renovate = {
        "id": 9995,
        "title": "Renovierungsbedürftige 3-Zimmer-Wohnung mit Entwicklungspotenzial in Dortmund",
        "city": "Dortmund",
        "postal_code": "44137",
        "purchase_price": 95000.0,
        "living_space": 75.0,
        "year_built": 1974,
        "is_online": 1,
        "availability_status": "online",
        "commission_percent": 0.0,
        "is_rented": 0,
        "portal_url": "https://www.immowelt.de/expose/test-renovation",
        "description": (
            "Attraktive 3-Zimmer-Eigentumswohnung in Dortmund. Die Wohnung ist bezugsfrei und bietet viel Potenzial. "
            "Die Ausstattung (Badezimmer, Bodenbeläge) stammt noch aus dem Baujahr und bedarf einer Modernisierung. "
            "Hausgeld: 180 € monatlich. Rücklagen der WEG betragen 120.000 €."
        )
    }
    # Simulated visual analysis with 10.000 € CaPEx (Bad 7.500 € + Böden 2.500 €)
    simulated_img_analysis = {
        "overall_visual_score": 5.5,
        "condition_rating": "RENOVIERUNGSBEDUERFTIG",
        "condition_label": "🟠 Modernisierungsbedarf",
        "total_visual_capex": 10000.0,
        "visual_capex_items": [
            {
                "title": "Badezimmer-Vollsanierung (Fliesen 70er Jahre)",
                "cost": 7500.0,
                "urgency": "Sofort vor Neuvermietung",
                "source_type": "FOTO_BELEG",
                "photo_indices": [2]
            },
            {
                "title": "Bodenbeläge erneuern (75 m² Klick-Vinyl)",
                "cost": 2500.0,
                "urgency": "Sofort vor Neuvermietung",
                "source_type": "FOTO_BELEG",
                "photo_indices": [3, 4]
            }
        ],
        "analyzed_photos": [{"index": 1}, {"index": 2}, {"index": 3}, {"index": 4}]
    }

    report = generate_ai_due_diligence_report(
        prop_renovate,
        full_description=prop_renovate["description"],
        image_analysis=simulated_img_analysis
    )

    print("Value-Add Report Result:")
    print("Score:", report["score"])
    print("Status:", report["status"])
    print("Status Label:", report["status_label"])
    print("All-In Investment:", report["metrics"].get("all_in_investment"))
    print("All-In Yield:", report["metrics"].get("all_in_gross_yield"))

    # The property must be GREEN, must have Value-Add label, and score >= 7.5!
    assert report["status"] == "GREEN", f"Expected GREEN but got {report['status']}"
    assert "Value-Add" in report["status_label"], f"Expected Value-Add in label: {report['status_label']}"
    assert report["score"] >= 7.5, f"Expected Score >= 7.5 but got {report['score']}"
    assert report["metrics"]["all_in_investment"] > prop_renovate["purchase_price"]
    print("✓ test_stage4_value_add_recognition passed successfully!")


if __name__ == "__main__":
    test_stage1_offline_knockout()
    test_stage1_missing_description_knockout()
    test_stage1_search_url_knockout()
    test_stage3_vision_gating_unprofitable()
    test_stage3_all_photos_analysis()
    test_stage4_value_add_recognition()
    print("\nALL 6 HOLISTIC GATING & VALUE-ADD TESTS PASSED!")
