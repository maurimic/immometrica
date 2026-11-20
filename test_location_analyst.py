"""
Test script for Google Maps integration, address precision detection, and POI micro-location analysis.
"""
from app.location_analyst import check_address_is_exact, build_google_maps_links, analyze_property_location_and_pois

def test_address_precision():
    # Exact addresses with street + house number (including spaces and hyphens)
    exact_samples = [
        "Düsterstraße 15, 44143 Dortmund",
        "Körner Hellweg 42, 44143 Dortmund",
        "Leopoldstraße 120a, 80802 München",
        "Hauptstraße 1-3, 90403 Nürnberg",
        "Am Markt 7, Essen",
        "Fürther Straße 12",
        "Fürther Str. 12",
        "Karl-Bröger-Straße 5a",
        "Karl-Bröger-Str. 5a",
        "Am Stadtpark 3",
        "Hans-Sachs-Gasse 4",
        "Willy-Brandt-Platz 1"
    ]
    for addr in exact_samples:
        is_exact, reason = check_address_is_exact(addr)
        assert is_exact is True, f"Expected exact for '{addr}', got is_exact={is_exact}, reason={reason}"
        assert reason in ["EXACT_STREET_AND_NUMBER", "PROBABLE_EXACT"]
        print(f"[OK] Exact address detected: '{addr}' -> reason='{reason}'")

    # Inexact addresses (district / city / no house number)
    inexact_samples = [
        "44329 Dortmund-Derne",
        "Dortmund Innenstadt-Ost",
        "Zentrum, 80331 München",
        "Düsterstraße, 44143 Dortmund",
        "Essen-Dellwig",
        "Nürnberg Südstadt",
        "Fürther Straße",
        "Karl-Bröger-Straße",
        "Am Stadtpark"
    ]
    for addr in inexact_samples:
        is_exact, reason = check_address_is_exact(addr)
        assert is_exact is False, f"Expected inexact for '{addr}', got is_exact={is_exact}, reason={reason}"
        print(f"[OK] Inexact address detected: '{addr}' -> reason='{reason}'")

def test_google_maps_links():
    exact_addr = "Düsterstraße 15"
    links_exact = build_google_maps_links(exact_addr, "44143", "Dortmund")
    assert "Düsterstraße" in links_exact["direct_url"] or "D%C3%BCsterstra%C3%9Fe" in links_exact["direct_url"]
    assert "satellite_url" in links_exact
    print("[OK] Exact Maps URL:", links_exact["direct_url"])

    inexact_addr = "Dortmund-Derne"
    links_inexact = build_google_maps_links(inexact_addr, "44329", "Dortmund")
    assert "Dortmund-Derne" in links_inexact["direct_url"] or "44329" in links_inexact["direct_url"]
    print("[OK] Inexact Maps URL:", links_inexact["direct_url"])

def test_poi_analysis():
    # Test Dortmund property with exact address
    prop_exact = {
        "address": "Düsterstraße 15",
        "postal_code": "44143",
        "city": "Dortmund",
        "lat": 51.515,
        "lng": 7.495
    }
    loc = analyze_property_location_and_pois(
        property_data=prop_exact,
        full_description="Die Wohnung liegt in ruhiger Lage nahe REWE Supermarkt und U-Bahn Station."
    )
    assert loc["is_exact_address"] is True
    assert loc["disclaimer"] is None
    assert "Exakte Adresse" in loc["precision_badge"]
    assert len(loc["pois"]) >= 4
    poi_titles = [p["title"] for p in loc["pois"]]
    assert any("Supermarkt" in t for t in poi_titles)
    assert any("ÖPNV" in t for t in poi_titles)
    assert any("Universität" in t or "Bildung" in t for t in poi_titles)
    assert any("Klinikum" in t or "Ärzte" in t or "Gesundheit" in t for t in poi_titles)
    print(f"[OK] POI count: {len(loc['pois'])}, Location Tier: {loc['location_tier']}, Score: {loc['location_score']}")

    # Test approximate address with disclaimer
    prop_approx = {
        "address": "44329 Dortmund-Derne",
        "postal_code": "44329",
        "city": "Dortmund",
        "lat": 51.56,
        "lng": 7.52
    }
    loc_approx = analyze_property_location_and_pois(
        property_data=prop_approx,
        full_description="Gepflegte Eigentumswohnung im Grünen."
    )
    assert loc_approx["is_exact_address"] is False
    assert loc_approx["disclaimer"] is not None
    assert "Ungefähre Lage" in loc_approx["disclaimer"] or "Stadtteil" in loc_approx["disclaimer"]
    assert "Ungefähre Lage" in loc_approx["precision_badge"]
    print("[OK] Approximate disclaimer verified:", loc_approx["disclaimer"])

    # Test Nürnberg Deal 170: PLZ 90408 with district outline query (Mitte, 90408 Nürnberg)
    # matching the requested user area boundary link format
    prop_nuernberg = {
        "address": "Stadtteil Mitte, 90408 Nürnberg",
        "postal_code": "90408",
        "city": "Nürnberg",
        "lat": 49.46749,
        "lng": 11.06656
    }
    loc_nbg = analyze_property_location_and_pois(
        property_data=prop_nuernberg,
        full_description="Gepflegte 2-Zimmer-Wohnung mit Balkon in Nürnberg nahe Nordklinikum."
    )
    assert loc_nbg["is_exact_address"] is False
    assert "Mitte" in loc_nbg["maps"]["query"] and "90408" in loc_nbg["maps"]["query"]
    assert "Mitte" in loc_nbg["maps"]["direct_url"] and "90408" in loc_nbg["maps"]["direct_url"]
    assert "90408" in loc_nbg["disclaimer"]
    print(f"[OK] Nürnberg Deal #170 district area search verified: {loc_nbg['maps']['direct_url']} (Query: {loc_nbg['maps']['query']})")

def test_preserve_portal_house_number():
    from app.ai_analyst import _merge_llm_into_insights

    # Case 1: Portal has exact street + house number (e.g. Fürther Straße 12), LLM returns street only without number
    prop_fuerth = {
        "address": "Fürther Straße 12",
        "city": "Nürnberg",
        "postal_code": "90429",
        "lat": 49.45,
        "lng": 11.05
    }
    insights_no_nr = {"corrected_address": "Fürther Straße", "corrected_city": "Nürnberg"}
    loc = analyze_property_location_and_pois(
        property_data=prop_fuerth,
        full_description="Schöne Altbauwohnung in der Fürther Straße.",
        insights=insights_no_nr
    )
    assert loc["is_exact_address"] is True, "Location engine must keep exact address when portal has house number!"
    assert loc["display_address"] == "Fürther Straße 12", f"Expected 'Fürther Straße 12', got '{loc['display_address']}'"
    assert loc["disclaimer"] is None, "Exact address must not produce an approximate location disclaimer!"
    assert "Exakte Adresse verifiziert" in loc["precision_badge"]
    print("[OK] Preserved portal house number in location analysis: 'Fürther Straße 12' kept over 'Fürther Straße'")

    # Case 2: _merge_llm_into_insights preserves exact portal address when LLM drops house number
    merged = _merge_llm_into_insights(
        {},
        {"corrected_address": "Karl-Bröger-Straße"},
        {"address": "Karl-Bröger-Straße 5a", "city": "Nürnberg"}
    )
    assert merged["corrected_address"] == "Karl-Bröger-Straße 5a", f"Expected 'Karl-Bröger-Straße 5a', got '{merged.get('corrected_address')}'"
    print("[OK] _merge_llm_into_insights preserved exact portal address 'Karl-Bröger-Straße 5a'")

    # Case 3: _merge_llm_into_insights adopts LLM address if portal had no house number but LLM found one
    merged_upgraded = _merge_llm_into_insights(
        {},
        {"corrected_address": "Am Stadtpark 3"},
        {"address": "Am Stadtpark", "city": "Nürnberg"}
    )
    assert merged_upgraded["corrected_address"] == "Am Stadtpark 3", f"Expected 'Am Stadtpark 3', got '{merged_upgraded.get('corrected_address')}'"
    print("[OK] _merge_llm_into_insights upgraded address to 'Am Stadtpark 3'")


def test_portal_placeholder_cleaning():
    from app.location_analyst import clean_real_estate_address

    # Case 1: Portal placeholder "Straße und Hausnummer nicht angegeben"
    p_jungferntal = "Straße und Hausnummer nicht angegeben, 44369 Dortmund"
    is_exact, prec = check_address_is_exact(p_jungferntal)
    assert is_exact is False
    assert prec == "DISTRICT_OR_CITY_ONLY", f"Expected DISTRICT_OR_CITY_ONLY, got {prec}"
    cleaned = clean_real_estate_address(p_jungferntal, city="Dortmund", postal_code="44369", district="Jungferntal")
    assert "nicht angegeben" not in cleaned
    assert "Jungferntal" in cleaned
    print(f"[OK] Cleaned placeholder address: '{cleaned}'")

    # Case 2: Portal placeholder "Die vollständige Adresse der Immobilie erhältst du vom Anbieter."
    p_immoscout = "Die vollständige Adresse der Immobilie erhältst du vom Anbieter., 45279 Horst, Essen, 45279 Essen"
    is_exact2, prec2 = check_address_is_exact(p_immoscout)
    assert is_exact2 is False
    assert prec2 == "DISTRICT_OR_CITY_ONLY"
    cleaned2 = clean_real_estate_address(p_immoscout, city="Essen", postal_code="45279", district="Horst")
    assert "vom anbieter" not in cleaned2.lower()
    assert cleaned2.count("45279") == 1
    assert cleaned2.count("Essen") == 1
    print(f"[OK] Cleaned ImmoScout placeholder: '{cleaned2}'")

    # Case 3: analyze_property_location_and_pois with Jungferntal listing
    prop = {
        "address": "Straße und Hausnummer nicht angegeben, 44369 Dortmund",
        "city": "Dortmund",
        "postal_code": "44369",
        "lat": 51.5346,
        "lng": 7.4598
    }
    loc = analyze_property_location_and_pois(prop, insights={"district": "Jungferntal"})
    assert loc["precision_type"] == "DISTRICT_OR_CITY_ONLY"
    assert "nicht angegeben" not in loc["display_address"].lower()
    assert "Jungferntal" in loc["display_address"]
    assert "nicht angegeben" not in loc["maps"]["query"].lower()
    assert "Jungferntal" in loc["maps"]["query"]
    print(f"[OK] Location analysis for Jungferntal: display='{loc['display_address']}', query='{loc['maps']['query']}'")


if __name__ == "__main__":
    test_address_precision()
    test_google_maps_links()
    test_poi_analysis()
    test_preserve_portal_house_number()
    test_portal_placeholder_cleaning()
    print("\n>>> ALL LOCATION & GOOGLE MAPS TESTS PASSED! <<<")
