import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app.image_analyst import refine_photo_tag

def test_photo_classifications():
    cases = [
        # 1. Screenshot 1 issues: Facades / Courtyards mistakenly tagged as bathroom or living room
        ("Straßenseitige Mehrfamilienhausfassade mit rotem Mittelteil...", "🛋️ Innenraum / Wohnbereich", "🏢 Fassade / Außenansicht"),
        ("Rückwärtige Hofansicht des Gebäudes mit markanten rot abgesetzten Balkonen...", "🛁 Badezimmer & Sanitär", "🏢 Fassade / Außenansicht"),
        ("Außenansicht des mehrgeschossigen Punkthochhauses mit Pkw-Stellplatz...", "🛋️ Wohnbereich / Zimmer", "🏢 Fassade / Außenansicht"),
        ("Rückwärtige Gebäudeansicht des Mehrfamilienhauses mit Balkonanlagen...", "🌿 Balkon / Loggia", "🏢 Fassade / Außenansicht"),
        
        # 2. Screenshot 2 issue: Balcony with parapet and tiles mistakenly tagged as living room
        ("Balkonaustritt mit Betonbrüstung und rötlichem Spaltklinker-/Fliesenbelag im 7. Obergeschoss.", "🛋️ Wohnbereich / Zimmer", "🌿 Balkon / Loggia"),
        ("Großzügige Dachterrasse mit Plattenbelag aus Waschbeton und Geländer...", "🛋️ Wohnbereich / Zimmer", "🌿 Balkon / Loggia"),
        
        # 3. Living room with balcony access (MUST stay living room, NOT balcony)
        ("Lichtdurchfluteter Wohn- und Essbereich mit Balkonzugang...", "🌿 Balkon / Loggia", "🛋️ Wohnbereich / Wohnzimmer"),
        ("Blickwinkel im Wohnzimmer auf die Fensterfront und den Ausgang zum Balkon...", "🌿 Balkon / Loggia", "🛋️ Wohnbereich / Wohnzimmer"),
        ("Offener Wohn- und Essbereich mit Fenstern zur Dachterrasse...", "🌿 Balkon / Loggia", "🛋️ Wohnbereich / Wohnzimmer"),
        
        # 4. Standard interior rooms
        ("Badezimmer mit deckenhohen Fliesen, Waschtisch und Dusche...", "Objektansicht", "🛁 Badezimmer & Sanitär"),
        ("Funktional ausgestattete Einbauküche mit Herd und Spüle...", "Objektansicht", "🍳 Küche / EBK"),
        ("Länglicher Eingangsbereich und Wohnungsflur mit Fliesen...", "Objektansicht", "🚪 Flur / Eingangsbereich"),
        ("Ruhiges Schlafzimmer mit Doppelbett und Kleiderschrank...", "Objektansicht", "🛏️ Schlafzimmer / Zimmer"),
        ("Schematischer 2-Zimmer-Grundriss mit Raumaufteilung...", "Objektansicht", "📐 Bauzeichnung / Grundriss"),
        
        # 5. Hallway with view to open doors vs Bathroom
        ("Blick durch den offenen Flur und die Durchgänge der teilsanierten Wohnung. Man sieht den Rohbauzustand mit freiliegenden Wänden und Zugängen zu den Zimmern sowie dem Badezimmer.", "Objektansicht", "🚪 Flur / Eingangsbereich"),
        
        # 6. Floor plans (even if description mentions bad/wc/zimmer or old tag was bad/schlafzimmer)
        ("Der schematische Grundriss veranschaulicht die Raumaufteilung der 3-Zimmer-Wohnung mit Bad und WC.", "🛁 Badezimmer & Sanitär", "📐 Bauzeichnung / Grundriss"),
        ("Die Abbildung zeigt den Grundriss und Aufteilungsplan.", "🛏️ Schlafzimmer / Zimmer", "📐 Bauzeichnung / Grundriss")
    ]
    
    for desc, orig_tag, expected in cases:
        actual = refine_photo_tag(orig_tag, desc)
        assert actual == expected, f"Failed for '{desc[:40]}...': Expected '{expected}', got '{actual}'"

    # Test with is_blueprint=True
    bp_tag = refine_photo_tag("🛁 Badezimmer & Sanitär", "Wohnung mit Küche und Bad", is_blueprint=True)
    assert bp_tag == "📐 Bauzeichnung / Grundriss", f"is_blueprint=True failed: got '{bp_tag}'"

    print("[OK] All photo classification accuracy test cases passed!")

if __name__ == "__main__":
    test_photo_classifications()
