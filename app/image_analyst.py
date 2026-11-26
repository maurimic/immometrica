"""
AI-Powered Real Estate Image & Visual Due Diligence Analyst for ImmoMetrica Clone.
Analyzes property photos using Computer Vision (PIL) and Multimodal LLMs (Gemini / OpenAI),
evaluating the actual condition (IST-Zustand) of bathrooms, kitchens, floors, windows,
heating, and walls, and calculating itemized renovation costs (CaPEx).
"""

import io
import re
import os
import json
import base64
import logging
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
from app.datetime_utils import get_berlin_now_str
import requests
import PIL.Image
import PIL.ImageStat

logger = logging.getLogger("immometrica.image_analyst")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache"
}

# Real estate room categories recognized in German due diligence
ROOM_CATEGORIES = [
    {"key": "bathroom", "label": "Bad & Sanitär", "icon": "bath"},
    {"key": "kitchen", "label": "Küche & EBK", "icon": "utensils"},
    {"key": "flooring", "label": "Böden & Beläge", "icon": "layers"},
    {"key": "windows", "label": "Fenster & Dämmung", "icon": "app-window"},
    {"key": "heating_electrics", "label": "Elektrik & Heizung", "icon": "zap"},
    {"key": "walls_substance", "label": "Wände & Bausubstanz", "icon": "paint-bucket"}
]

# Standard construction cost benchmarks for Germany (BKI / DIN 276 standard)
BENCHMARK_COSTS = {
    "bath_complete": 7500.0,      # Vollsanierung Bad (Fliesen, Dusche/Wanne, WC, Waschtisch)
    "bath_partial": 2500.0,       # Teilsanierung Bad (Armaturen, WC, Duschkabine)
    "kitchen_new": 4500.0,        # Neue Standard-Einbauküche inkl. Geräte
    "kitchen_refresh": 1200.0,    # Küchenfronten/Arbeitsplatte modernisieren
    "flooring_sqm": 42.0,         # Hochwertiges Klick-Vinyl/Laminat inkl. Trittschall & Leisten pro m²
    "painting_sqm": 22.0,         # Raufaser entfernen, spachteln, malern pro m² Wohnfläche
    "window_single": 850.0,       # Austausch altes Fenster gegen 3-fach Isolierglas Kunststoff
    "heating_radiator": 450.0,    # Austausch alter Rippenheizkörper gegen modernen Flachheizkörper
    "electric_update": 3800.0     # Nachrüstung FI-Schalter & Schalterprogramm
}


# ==============================================================================
# 1. BILDER-EXTRAKTION (GALLERY SCRAPING)
# ==============================================================================

def extract_gallery_images(portal_url: str, source_portal: str = "", fallback_image: str = "") -> List[str]:
    """
    Extrahiert alle hochauflösenden Galeriebilder aus einem Online-Inserat (z. B. Kleinanzeigen).
    Sucht STRENG innerhalb des Galerie-Containers, um verfälschende Fremdbilder
    (z. B. ähnliche Angebote, Empfehlungen am Seitenende) komplett auszuschließen.
    Gibt eine deduplizierte Liste validierter Bild-URLs zurück.
    """
    images = []

    # If portal is Immowelt, extract from live expose JSON
    if portal_url and ("immowelt.de" in portal_url.lower() or "immowelt" in source_portal.lower()):
        try:
            from app.scrapers.immowelt import fetch_immowelt_expose
            iw_data = fetch_immowelt_expose(portal_url)
            if iw_data.get("images"):
                images.extend(iw_data["images"])
        except Exception as e:
            logger.warning(f"Failed to extract Immowelt gallery from {portal_url}: {e}")

    # If portal is ImmoScout24, extract from live mobile expose API
    elif portal_url and ("immobilienscout24.de" in portal_url.lower() or "scout" in portal_url.lower() or "immoscout" in source_portal.lower()):
        try:
            from app.scrapers.immoscout import fetch_immoscout_expose
            is_data = fetch_immoscout_expose(portal_url)
            if is_data.get("pictures"):
                images.extend(is_data["pictures"])
        except Exception as e:
            logger.warning(f"Failed to extract ImmoScout24 gallery from {portal_url}: {e}")

    # If portal is Kleinanzeigen and URL is available, scrape live gallery
    elif portal_url and ("kleinanzeigen" in portal_url.lower() or "kleinanzeigen" in source_portal.lower()):
        try:
            resp = requests.get(portal_url, headers=BROWSER_HEADERS, timeout=7.0)
            if resp.status_code == 200:
                # Scoped search strictly inside the listing gallery container
                m = re.search(
                    r'<div[^>]*class="[^"]*(?:vip-image-gallery|galleryimage-large|ad-gallery)[^"]*"[^>]*>(.*?)(?:<aside|<div[^>]*class="[^"]*(?:similar-ads|vip-similar-ads|aditem|viewad-description)[^"]*"|<!-- VIP-Bottom -->)',
                    resp.text,
                    re.DOTALL
                )
                gallery_html = m.group(1) if m else ""
                
                if gallery_html:
                    found_urls = re.findall(r'https?://img\.kleinanzeigen\.de/api/v1/prod-ads/images/[^\s"\'\\,<>]+', gallery_html)
                    seen_bases = set()
                    for u in found_urls:
                        base = u.split("?")[0]
                        if base not in seen_bases:
                            seen_bases.add(base)
                            images.append(base + "?rule=$_59.AUTO")
        except Exception as e:
            logger.warning(f"Failed to scrape live gallery from {portal_url}: {e}")

    # Fallback to single property image if no gallery could be scraped
    if not images and fallback_image:
        images.append(fallback_image)

    # Clean and deduplicate while preserving order
    seen = set()
    cleaned_images = []
    for u in images:
        if u and u not in seen and not u.startswith("data:") and "placeholder" not in u:
            seen.add(u)
            cleaned_images.append(u)

    return cleaned_images


# ==============================================================================
# 2. COMPUTER VISION INSPEKTION & FOTO-KLASSIFIZIERUNG (PIL)
# ==============================================================================

def classify_photo(img_bytes: bytes) -> Dict[str, Any]:
    """
    Computer Vision Klassifizierung anhand von Farbmetriken, Luminanz und
    Pixelverteilung. Erkennt präzise Bauzeichnungen/Grundrisse vs. echte Raumfotos.
    """
    try:
        img = PIL.Image.open(io.BytesIO(img_bytes)).convert("RGB")
        width, height = img.size
        aspect_ratio = round(width / max(1, height), 2)

        stat = PIL.ImageStat.Stat(img)
        mr, mg, mb = stat.mean
        sr, sg, sb = stat.stddev

        luminance = 0.299 * mr + 0.587 * mg + 0.114 * mb
        warmth = (mr - mb)
        color_diff = (abs(mr - mg) + abs(mg - mb) + abs(mr - mb)) / 3.0
        color_var = (sr + sg + sb) / 3.0

        stat_top = PIL.ImageStat.Stat(img.crop((0, 0, width, height // 2)))
        stat_bot = PIL.ImageStat.Stat(img.crop((0, height // 2, width, height)))
        t_mr, t_mg, t_mb = stat_top.mean
        b_mr, b_mg, b_mb = stat_bot.mean
        t_lum = 0.299 * t_mr + 0.587 * t_mg + 0.114 * t_mb
        b_lum = 0.299 * b_mr + 0.587 * b_mg + 0.114 * b_mb

        # Sample thumbnail for background luminance distribution
        thumb = img.resize((100, 100))
        pixels = [thumb.getpixel((x, y)) for x in range(100) for y in range(100)]
        light_pixels = sum(1 for r, g, b in pixels if (0.299 * r + 0.587 * g + 0.114 * b) > 160)
        light_ratio = light_pixels / len(pixels)

        # Blueprints and architectural drawings: near-monochrome paper background, high brightness, high light pixel ratio
        is_blueprint = (color_diff < 6.0 and luminance > 190 and light_ratio > 0.5)

        if is_blueprint:
            tag = "📐 Bauzeichnung / Grundriss"
            desc = "Architektonische Bauzeichnung / Schnitt- bzw. Grundrissplan"
            quality_score = 6.0
        else:
            # Neutral tag: Computer Vision / PIL alone cannot reliably distinguish specific rooms (e.g. bathroom vs street).
            # Detailed room detection is handled exclusively by multimodal AI (Gemini Vision).
            tag = "📷 Exposé-Foto"
            desc = "Foto aus Exposé-Galerie"
            quality_score = 7.0

        return {
            "width": width,
            "height": height,
            "aspect_ratio": aspect_ratio,
            "luminance": round(luminance, 1),
            "warmth": round(warmth, 1),
            "color_diff": round(color_diff, 1),
            "color_variance": round(color_var, 1),
            "light_ratio": round(light_ratio, 2),
            "is_blueprint": is_blueprint,
            "tag": tag,
            "description": desc,
            "visual_quality_score": quality_score
        }
    except Exception as e:
        logger.warning(f"PIL photo classification failed: {e}")
        return {
            "width": 800,
            "height": 600,
            "aspect_ratio": 1.33,
            "luminance": 130.0,
            "warmth": 10.0,
            "color_diff": 15.0,
            "color_variance": 40.0,
            "light_ratio": 0.3,
            "is_blueprint": False,
            "tag": "Objektansicht",
            "description": "Foto aus Exposé-Galerie",
            "visual_quality_score": 7.0
        }


def inspect_image_with_pil(img_bytes: bytes) -> Dict[str, Any]:
    """
    Analysiert Bilddaten mit PIL auf Helligkeit, Kontrast, Farbverteilung und visuelle Komplexität.
    """
    return classify_photo(img_bytes)


_AI_STAGING_REGEX = re.compile(
    r"\b(?:"
    r"ki[-\s]?generiert\w*|"
    r"ai[-\s]?generated|"
    r"ai[-\s]?generiert\w*|"
    r"virtual\s*staging|"
    r"virtuell\w*\s*(?:staging|m[öo]blier\w*|eingerichtet\w*|eingef[üu]gt\w*|visualisier\w*|k[üu]che\w*|einrichtung\w*)|"
    r"digital\s*(?:m[öo]blier\w*|eingerichtet\w*|eingef[üu]gt\w*|bearbeitet\w*|staging)|"
    r"home[\s-]?staging|"
    r"einrichtungsbeispiel\w*|"
    r"einrichtungsvorschlag|"
    r"musterfoto\w*|"
    r"symbolfoto\w*|"
    r"symbolbild\w*|"
    r"3d[-\s]?visualisierung\w*|"
    r"ki[-\s]?visualisierung\w*|"
    r"visualisierung\w*|"
    r"fotomontage\w*|"
    r"render(?:ing|s)?|"
    r"retuschiert\w*|"
    r"k[üu]nstlich\w*\s+(?:eingef[üu]gt|generiert|erstellt)|"
    r"fiktiv\w*"
    r")\b",
    re.IGNORECASE,
)

_EMPTY_KITCHEN_REGEX = re.compile(
    r"\b(?:"
    r"keine\s+(?:einbauk[üu]che|ebk|k[üu]chenzeile|k[üu]chenm[öo]bel)|"
    r"ohne\s+(?:einbauk[üu]che|ebk|k[üu]chenzeile|m[öo]bel)|"
    r"leerstehend\w*\s+k[üu]che|"
    r"leere\s+k[üu]che|"
    r"k[üu]che\s+(?:ist\s+|wirkt\s+|komplett\s+|v[öo]llig\s+)?leer|"
    r"unm[öo]bliert\w*\s+k[üu]che|"
    r"nur\s+(?:k[üu]chen)?anschl[üu]sse|"
    r"k[üu]chenanschl[üu]sse\s+(?:vorhanden|sichtbar)|"
    r"kein\s+(?:herd|backofen|kochfeld|sp[üu]le)"
    r")\b",
    re.IGNORECASE,
)

_REAL_EBK_REGEX = re.compile(
    r"\b(?:vorhandene\s+einbauk[üu]che|einbauk[üu]che|k[üu]chenzeile|schrankzeile|kochfeld|backofen|herd)\b",
    re.IGNORECASE,
)


def is_ai_generated_photo(
    tag: str = "",
    description: str = "",
    findings: Optional[List[str]] = None,
    explicit_flag: bool = False,
) -> bool:
    """Detects whether a listing image is an AI visualization / virtual staging."""
    if explicit_flag:
        return True
    t = str(tag or "").strip().lower()
    if any(k in t for k in ("ki-visualisierung", "virtual staging", "ki-generiert", "ai-generated")):
        return True
    combined = f"{description or ''} {' '.join(findings or [])}"
    return bool(_AI_STAGING_REGEX.search(combined))


def refine_photo_tag(
    tag: str,
    description: str = "",
    findings: Optional[List[str]] = None,
    is_blueprint: bool = False,
) -> str:
    """
    Präzise, semantische Klassifizierung der Raum-Überschrift (Tag) anhand des echten Motiv-Subjekts.
    Verhindert zuverlässig Fehlzuordnungen wie:
    - Grundrisse/Bauzeichnungen als 'Badezimmer' oder 'Schlafzimmer'
    - Außenansichten/Fassaden als 'Badezimmer' oder 'Innenraum / Wohnbereich'
    - Flure mit Blick in offene Türen als 'Badezimmer'
    - Wohnzimmer mit Balkontür als 'Balkon'
    - Straßenansichten mit Balkonen an der Fassade als 'Balkon'
    - Reale Balkone als 'Wohnbereich / Zimmer'
    Hinweis: KI-generierte Bilder behalten ihren Raum-Tag zur Orientierung in der Galerie,
    werden aber über is_ai_generated=True strikt von Bewertung & CaPEx ausgeschlossen.
    """
    d = (str(description or "")).strip().lower()
    t = (str(tag or "")).strip()
    f_str = " ".join(findings or []).lower() if findings else ""

    # 1. Grundrisse und Baupläne (STRENGE Priorität vor allen Raum-Kategorien)
    plan_keywords = [
        "grundriss", "bauzeichnung", "schnittzeichnung", "architekturplan",
        "aufteilungsplan", "flächenberechnung", "etagenplan", "raumaufteilung",
        "2d-grundriss", "3d-grundriss", "bauplan", "wohnungsplan", "geschossplan",
        "grundrissplan", "möblierter grundriss", "schematischer grundriss"
    ]
    is_real_plan = (
        is_blueprint or 
        any(k in d or k in f_str for k in plan_keywords) or
        (t and any(k in t.lower() for k in ["grundriss", "bauzeichnung", "aufteilungsplan"]))
    )
    if is_real_plan:
        return "📐 Bauzeichnung / Grundriss"

    # 2. Maklerwerbung, Logos, Dokumente & Energieausweise
    if any(k in d for k in ["unternehmenslogo", "makler-einblend", "werbefolie", "marketinggrafik", "kontaktdatenblatt", "beratungsservice", "partnerlogos", "energieausweis", "werbe- und kontakt", "kontaktfolie", "vonovia verkauf", "fiktives, ki-generiertes musterfoto"]):
        return "📄 Dokument / Info"

    # 3. Fassade / Außenansicht / Straßenansicht des Gebäudes
    facade_starters = (
        "straßenseitig", "straßenansicht", "fassade", "außenansicht", "gebäudeansicht", 
        "mehrfamilienhaus", "einfamilienhaus", "reihenhaus", "punkthochhaus", "altbau",
        "rückwärtige gebäudeansicht", "rückwärtige ansicht des", "rückseitige außenansicht",
        "gesamtansicht der straßenecke", "halbtotale der straßenansicht", "frontalansicht der rückwärtigen fassade",
        "rückwärtige fassade", "ansicht des gebäudes", "gebäude von außen", "komplexes", "wohnanlage von außen",
        "straßen-/zugangsansicht", "vollständige straßenansicht", "straßenseite des gebäudes", "halbtotale der straßenansicht",
        "gebäudefront", "außenaufnahme des", "rückwärtige hofansicht", "hofansicht des gebäudes"
    )
    is_facade = d.startswith(facade_starters) or (
        any(k in d for k in ["mehrfamilienhaus", "gebäudeansicht", "außenansicht des mehrfamilienhauses", "putzfassade", "rückwärtige gebäudeansicht", "gebäudeansicht mit", "punkthochhauses"]) and
        not any(k in d for k in ["im wohnzimmer", "im schlafzimmer", "im badezimmer", "in der küche", "balkonaustritt mit", "wohnzimmer", "schlafzimmer", "badezimmer", "küche", "couch", "sofa", "bett", "kamin", "essbereich"])
    )
    if is_facade and not any(k in d for k in ["wohnungseingang", "treppenhaus", "flurbereich", "innenliegend", "wohnzimmer", "schlafzimmer", "badezimmer", "küche", "couch", "sofa", "bett", "kamin", "essbereich", "parkett", "laminat"]):
        return "🏢 Fassade / Außenansicht"

    # 4. Garten / Innenhof / Außenanlagen
    yard_starters = ("innenhof", "hinterhof", "hofbereich", "hofansicht", "rasenfläche", "grünfläche", "garten", "außenanlagen", "vorgarten", "sonnige garten- und fassadenansicht")
    if d.startswith(yard_starters) or any(k in d for k in ["ruhiger, begrünter innenhof", "weitläufiger rasenfläche", "heckenbepflanzung im vorgarten", "gepflasterten gehwegen"]):
        if not any(k in d for k in ["wohnzimmer", "schlafzimmer", "badezimmer", "küchenzeile"]):
            return "🌳 Garten / Hof / Außenanlagen"

    # 5. Garage / Stellplatz / Tiefgarage
    if any(k in d for k in ["tiefgarage", "tiefgaragenzufahrt", "garagentor", "duplexparker", "carport", "einzelgarage", "garagenstellplatz"]):
        if not any(k in d for k in ["im wohnzimmer", "im schlafzimmer"]):
            return "🚗 Garage / Stellplatz"

    # 6. Ausblick / Panorama / Fernsicht
    view_starters = ("fernsicht", "weitblick", "ausblick", "aussicht", "panorama", "blick über die dächer", "blick ins grüne", "blick auf die stadt")
    if d.startswith(view_starters):
        return "🌳 Weitblick / Ausblick"

    # 7. Reale Balkone / Loggien / Dachterrassen
    indoor_prefixes = (
        "wohn", "helles wohn", "großzügiges wohn", "lichtdurchfluteter wohn", "offener wohn", "blickwinkel im wohn",
        "blick vom wohn", "blick in den wohn", "blickrichtung vom fenster", "perspektive auf den ess", "essbereich",
        "schlaf", "ruhiges schlaf", "kinderzimmer", "arbeitszimmer", "gästezimmer", "das längliche zimmer", "weiteres zimmer",
        "zimmerausschnitt", "dieses helle zimmer", "raum ", "leerstehender raum", "küche", "flur", "diele",
        "länglicher eingangsbereich", "zentraler flurbereich", "blick durch den flur", "blick durch den offenen flur"
    )
    is_indoor_subject = d.startswith(indoor_prefixes)

    balcony_starters = (
        "balkon", "loggia", "terrasse", "dachterrasse", "freisitz", "balkonaustritt", 
        "großzügige dachterrasse", "großzügiger balkon", "überdachter balkon", "überdachte loggia",
        "sonniger balkon", "südbalkon", "die großzügige dachterrasse", "der balkon ", "die loggia "
    )
    if not is_indoor_subject:
        if d.startswith(balcony_starters):
            return "🌿 Balkon / Loggia"
        if any(s in d for s in ["balkonaustritt mit", "balkongeländer", "dachterrasse mit plattenbelag", "waschbetonplatten", "klickfliesen in holz-/wpc-optik"]):
            return "🌿 Balkon / Loggia"
        if "balkon" in d and any(s in d for s in ["brüstung", "geländer", "rattanmöbel", "fliesenbelag des balkons"]):
            return "🌿 Balkon / Loggia"

    # 8. Flur / Eingangsbereich / Treppenhaus (Vor Badezimmer prüfen, falls Blick durch Flur auf offene Türen geht)
    hallway_starters = (
        "flur", "diele", "eingangsbereich", "wohnungseingang", "hauseingang", "treppenhaus",
        "treppenaufgang", "treppenlauf", "windfang", "flurbereich", "eingangsdiele",
        "länglicher eingangsbereich", "zentraler flurbereich", "blick durch den flur",
        "blick durch den offenen flur", "blick in den flur", "offener flur", "die aufnahme zeigt den zentralen flur"
    )
    if d.startswith(hallway_starters) or any(k in d for k in [
        "wohnungsflur", "eingangsbereich", "flurbereich", "dielenbereich", "flur bzw. eine diele", "flur bzw.", "flur oder diele", "flur- bzw.", "treppenaufgang mit", "hauseingangsbereich des", "klingel- und briefkastenanlage", "überdachter hauseingangsbereich"
    ]):
        if not any(k in d for k in ["küchenzeile", "spüle in der küche", "duschkabine", "badewanne", "wand-wc", "wohn- und essbereich"]):
            return "🚪 Flur / Eingangsbereich"

    # 9. Badezimmer & Sanitär / Gäste-WC
    bath_starters = ("badezimmer", "duschbad", "wannenbad", "tageslichtbad", "gäste-wc", "wc", "sanitär", "duschbereich", "innenliegendes bad", "modernes bad", "modernisiertes tageslichtbad", "luxuriöses hauptbadezimmer", "separates gäste-wc", "funktionales tageslicht-badezimmer")
    if d.startswith(bath_starters) or (
        any(k in d for k in ["badezimmer", "duschbad", "gäste-wc", "stand-wc", "wand-wc", "wc-bereich", "toilette", "handwaschbecken", "tiefspüler", "flachspüler", "unterputzspülkasten", "spülkasten", "ebenerdige dusche", "badewanne", "waschtisch", "sanitärkeramik", "duschkabine", "waschbecken"]) and
        not any(k in d for k in ["küchenzeile", "spüle in der küche", "fassade", "außenansicht", "mehrfamilienhaus", "blick durch den flur", "blick durch den offenen flur"])
    ):
        return "🛁 Badezimmer & Sanitär"

    # 10. Küche & EBK
    kitchen_starters = ("küche", "einbauküche", "ebk", "küchenzeile", "kochküche", "offene küche", "u-förmig angelegte einbauküche", "funktional ausgestattete einbauküche", "längliche küche")
    if d.startswith(kitchen_starters) or (
        any(k in d for k in ["einbauküche", "küchenzeile", "cerankochfeld", "backofen", "dunstabzugshaube", "spüle"]) and
        not any(k in d for k in ["badezimmer", "duschbad", "wand-wc", "badewanne", "tiefspüler", "flachspüler", "wc-bereich", "gäste-wc", "toilette", "spülkasten"]) and
        not d.startswith(("wohn", "helles wohn", "großzügiger wohn", "großzügiges wohn", "großzügigen wohn", "wohnraum", "wohnzimmer", "gemütlicher nebenwohnraum", "nebenwohnraum", "sitzbereich", "schlaf", "kinderzimmer", "arbeitszimmer", "gästezimmer", "flur", "diele", "eingangsbereich"))
    ):
        return "🍳 Küche / EBK"

    # 11. Ergänzender Check Flur / Eingangsbereich
    if any(k in d for k in ["flurbereich", "dielenbereich", "wohnungsflur", "durchgänge der teilsanierten"]) and not any(k in d for k in ["badezimmer", "küchenzeile", "wohn- und essbereich"]):
        return "🚪 Flur / Eingangsbereich"

    # 12. Schlafzimmer / Kinderzimmer / Arbeitszimmer
    room_starters = ("schlafzimmer", "kinderzimmer", "arbeitszimmer", "gästezimmer", "elternschlafzimmer", "jugendzimmer", "das längliche zimmer", "weiteres schlaf", "dieses helle zimmer", "ergänzende perspektive des schlafzimmers", "ruhiges schlafzimmer")
    if d.startswith(room_starters) or any(k in d for k in ["schlafzimmer", "kinderzimmer", "arbeitszimmer", "gästezimmer", "elternschlafzimmer", "als schlaf-", "als arbeitszimmer", "arbeits- und medienraum", "schiebetüren-kleiderschrank"]):
        return "🛏️ Schlafzimmer / Zimmer"

    # 13. Wohnzimmer / Wohnbereich
    living_starters = (
        "wohnzimmer", "wohn- und ess", "wohn-/ess", "wohnraum", "essdiele", "wohnstudio",
        "dachstudio", "wohn-koch", "offener wohn", "heller wohn", "großzügiger wohn",
        "lichtdurchfluteter wohn", "blickwinkel im wohn", "blick vom wohn", "blick in den offenen wohn",
        "gesamtüberblick über den großzügigen wohnraum", "blick in den großzügigen, offenen wohn",
        "großzügiges, lichtdurchflutetes wohnzimmer", "sehr weitläufiger wohn"
    )
    if d.startswith(living_starters) or any(k in d for k in ["wohn- und essbereich", "couchtisch", "tv-bereich", "wohnzimmers"]):
        return "🛋️ Wohnbereich / Wohnzimmer"

    # 14. Keller / Abstellraum
    if any(k in d for k in ["keller", "kellerabteil", "abstellraum", "speisekammer", "dachboden", "hauswirtschaftsraum"]):
        return "📦 Keller / Abstellraum"

    # 15. Weitere Innenräume / Zimmer
    if any(k in d for k in ["zimmer", "innenaufnahme", "innenraum", "leerstehender raum", "dachgeschosszimmer", "estrich"]):
        return "🛋️ Wohnbereich / Zimmer"

    # Wenn der bestehende Tag bereits eine spezifische Kategorie hat und nicht generisch ist
    if t and t not in ["Objektansicht", "Objektfoto", "Foto aus Exposé-Galerie", "🛋️ Wohnbereich / Zimmer", "📷 Exposé-Foto"]:
        return t

    return t or "📷 Exposé-Foto"


# ==============================================================================
# 3. OPTIONALER GEMINI MULTIMODAL LLM CALL
# ==============================================================================

from app.llm_utils import (
    resolve_primary_llm_key as get_resolved_gemini_key,
    clean_and_parse_llm_json as _clean_and_parse_llm_json,
)


def sanitize_image_report_tags(img_report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Normalizes analyzed_photos tags & AI-staging flags in place for a parsed image_analysis dict."""
    if isinstance(img_report, dict):
        for p in (img_report.get("analyzed_photos") or []):
            if isinstance(p, dict):
                is_ai = is_ai_generated_photo(
                    p.get("tag", ""),
                    p.get("description", ""),
                    p.get("findings", []),
                    explicit_flag=bool(p.get("is_ai_generated", False)),
                )
                p["is_ai_generated"] = is_ai
                p["tag"] = refine_photo_tag(
                    p.get("tag", ""),
                    p.get("description", ""),
                    p.get("findings", []),
                    is_blueprint=bool(p.get("is_blueprint", False)),
                )
    return img_report


def call_gemini_vision(
    images_base64: List[Tuple[str, str]], 
    property_data: Dict[str, Any], 
    full_description: str = "",
    api_key: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Sendet bis zu 8 Primärfotos an Google Gemini Flash Vision für eine professionelle
    multimodale Bausachverständigen-Prüfung, Trade-Kosten-Kalkulation und Einzelfotobeschreibung.
    """
    gemini_key = get_resolved_gemini_key(api_key)
    if not gemini_key or not images_base64:
        return None

    # Limit primary call to at most 8 images to prevent generation timeouts (>90s)
    active_images = images_base64[:8]

    prompt = f"""Du bist ein DEKRA-zertifizierter Bausachverständiger und Immobilien-Due-Diligence-Auditor in Deutschland.
Analysiere die beigefügten Fotos der Immobilie '{property_data.get('title')}' in {property_data.get('city')} (Baujahr {property_data.get('year_built', 'k.A.')}, {property_data.get('living_space', 50)} m² Wohnfläche).

Objektbeschreibung des Verkäufers:
\"\"\"{full_description[:2000]}\"\"\"

WICHTIGE PRÜFKRITERIEN:
0. KI-GENERIERTE BILDER / VIRTUAL STAGING STRIKT AUSSCHLIESSEN (EXTREM WICHTIG!):
   - Prüfe bei jedem Foto kritisch, ob es sich um eine KI-generierte Visualisierung, Virtual Staging, digitale Möblierung, ein fiktives Einrichtungsbeispiel oder ein Vorher-/Nachher-Bild handelt (oft daran erkennbar, dass derselbe leere Raum auf einem anderen Foto leer ist und hier plötzlich virtuell mit Küche/Möbeln dargestellt wird, oder an Hinweisen wie "Virtual Staging", "Visualisierung", "Einrichtungsbeispiel", "KI-generiert").
   - Setze bei solchen Bildern zwingend `"is_ai_generated": true` (der Raum-`tag` wie z. B. `"🍳 Küche / EBK"` oder `"🛋️ Wohnbereich / Wohnzimmer"` darf zur besseren Orientierung beibehalten werden, das Bild gilt aber durch `is_ai_generated: true` nicht als realer Zustand).
   - Solche KI-/Staging-Bilder zeigen NICHT den realen Ist-Zustand! Sie dürfen NIEMALS für Zustandsnoten (`categories`, `overall_visual_score`) oder als Nachweis für vorhandene Einbauten (wie eine Einbauküche) herangezogen werden. Wenn das echte Foto der Küche leer ist und nur das KI-Bild eine Einbauküche zeigt, gilt die EBK als NICHT vorhanden (volle Kosten für neue EBK ansetzen!).
1. Prüfe zuerst, ob es sich um echte Innenraumfotos oder um Bauzeichnungen / Grundrisse / Pläne handelt.
2. Wenn nur Bauzeichnungen/Pläne vorliegen: Gib in 'notice' an, dass keine Innenraumfotos vorliegen, und leite den Zustand/CaPEx aus der Objektbeschreibung ab (z.B. wenn Verkäufer 'Renovieren!!!!' oder Sanierungsstau angibt).
3. Untersuche den visuellen IST-Zustand folgender 6 Bauteile auf den Fotos:
   - Badezimmer & Sanitär (Fliesenalter, Zustand Dusche/Wanne/WC, Fugen, Armaturen)
   - Küche & EBK (Vorhandensein EBK, Zustand Fronten/Arbeitsplatte, Gerätealter)
   - Böden (Material z.B. Parkett/Laminat/Fliesen, Trittspuren, Kratzer)
   - Fenster & Dämmung (Rahmen z.B. Kunststoff/Holz, Isolierverglasung, Rollläden)
   - Elektrik & Heizung (Heizkörpertyp z.B. modern Flachheizkörper vs. alt Gussrippen, Schalterprogramm)
   - Wände & Decken (Malerbedarf, Risse, Feuchtigkeits-/Schimmelhinweise, Tapeten)
4. Kalkuliere realistische deutsche Handwerkerkosten (CaPEx) nach BKI-Standard für notwendige Modernisierungen vor Neuvermietung.
5. QUELLENANGABE & BILDBELEGE FÜR JEDES CaPEx-GEWERK (SEHR WICHTIG!):
   - Prüfe kritisch: Welche Modernisierungskosten sind TATSÄCHLICH auf den Fotos sichtbar, und welche wären reine Baujahrs-/Sicherheitsmutmaßung?
   - Wenn Fenster auf den Fotos augenscheinlich intakte weiße/moderne Kunststoff-Isolierfenster sind -> setze `categories.windows.cost = 0.0` und füge KEINE Fenstermodernisierung zu `visual_capex_items` hinzu!
   - Gib bei JEDEM Eintrag in `visual_capex_items` zwingend an:
     * `source_type`: `"FOTO_BELEG"` (nur wenn Mangel/Altbestand konkret auf beigefügtem Foto erkennbar ist, z. B. altes Bad, alte Fliesen, alter Sicherungskasten, Gussrippen-Heizkörper, Holzdecke, abgewohnter Boden) ODER `"BAUJAHR_MUTMASSUNG"` (wenn das Bauteil — z. B. Sicherungskasten, verdeckte Steigleitungen, nicht fotografierte Räume — auf KEINEM Foto sichtbar ist und nur wegen des Baujahrs vorsorglich angenommen wird).
     * `photo_indices`: Array der 1-basierten Fotonummern, auf denen der Zustand wirklich zu sehen ist (z. B. `[4]` oder `[2, 5]`). Setze `[]`, wenn es nicht auf Fotos sichtbar ist (`BAUJAHR_MUTMASSUNG`).
     * `evidence_source`: Prägnante deutsche Begründung mit konkretem Bildbezug, z. B. `"📸 Beobachtet auf Foto #4: Sichtbar älteres Badezimmer mit beigen Fliesen und Stand-WC."` oder `"⚠️ Baujahrs-Mutmaßung (nicht auf Fotos sichtbar): Kein Sicherungskasten/Unterverteilung im Bildmaterial erkennbar."`

Klassifiziere und beschreibe JEDES der beigefügten Fotos 1 bis {len(active_images)} einzeln der Reihe nach.
WICHTIG ZUR LÄNGE: Halte das gesamte JSON kompakt und prägnant. Beschreibungen maximal 1-2 Sätze pro Foto. investor_summary maximal 2 prägnante Sätze. visual_capex_items maximal die wichtigsten 5 Gewerke. Vermeide ausschweifende Texte, damit die Antwort vollständig bleibt.
WICHTIG: Erkenne genau, was auf jedem Foto zu sehen ist, und wähle die präzise Kategorie als `tag`:
- 🌿 Balkon / Loggia (Balkon, Loggia, Dachterrasse, Freisitz, Balkonaustritt)
- 🏢 Fassade / Außenansicht (Mehrfamilienhaus von außen, Straßenansicht, Gebäudeansicht)
- 🌳 Weitblick / Ausblick (Aussicht, Panorama, Fernsicht, Blick ins Grüne)
- 🌳 Garten / Hof / Außenanlagen (Garten, Innenhof, Stellplatz, Außenbereich, Grünfläche)
- 🛋️ Wohnbereich / Wohnzimmer (Wohnzimmer, Wohn-/Essbereich)
- 🛏️ Schlafzimmer / Zimmer (Schlafzimmer, Kinderzimmer, Gästezimmer, Arbeitszimmer)
- 🛁 Badezimmer & Sanitär (Wanne, Dusche, Fliesen, WC, Gäste-WC)
- 🍳 Küche / EBK (Küchenzeile, Einbauküche, Geräte)
- 🚪 Flur / Eingangsbereich (Wohnungseingang, Flur, Diele, Treppenhaus, Aufzugstür)
- 📦 Keller / Abstellraum (Kellerabteil, Dachboden, Hauswirtschaftsraum)
- 🚗 Garage / Tiefgarage (Garagenstellplatz, Duplex, Tiefgarage)
- 📐 Bauzeichnung / Grundriss (Architekturplan, schematische Raumaufteilung)
- 🗺️ Lageplan / Umgebungskarte (Stadtplanausschnitt)
- 🤖 KI-Visualisierung (Virtual Staging) (KI-generierte Möbel/Küchen, fiktive Einrichtungsbeispiele, digitale Visualisierungen)

Antworte AUSSCHLIESSLICH mit gültigem JSON nach diesem Schema:
{{
  "has_interior_photos": true/false,
  "notice": "Hinweistext falls nur Baupläne, KI-Visualisierungen oder Besonderheiten, sonst null",
  "overall_visual_score": Zahl von 1.0 bis 10.0,
  "condition_rating": "SEHR_GEPFLEGT" / "GEPFLEGT" / "MODERNISIERUNGSBEDARF" / "RENOVIERUNGSBEDUERFTIG",
  "condition_label": "Prägnante 1-Zeilen Zustandsbewertung mit Ampel-Emoji",
  "photo_classifications": [
    {{
      "index": 1,
      "tag": "🏢 Fassade / Außenansicht",
      "description": "Prägnante, genaue Beschreibung von Foto 1...",
      "quality_score": 8.0,
      "is_blueprint": false,
      "is_ai_generated": false,
      "findings": ["Merkmal 1", "Merkmal 2"]
    }}
  ],
  "categories": {{
    "bathroom": {{"score": 7.5, "status": "Solide", "findings": "...", "cost": 0.0}},
    "kitchen": {{"score": 8.0, "status": "Gepflegt", "findings": "...", "cost": 0.0}},
    "flooring": {{"score": 8.5, "status": "Neuwertig", "findings": "...", "cost": 0.0}},
    "windows": {{"score": 9.0, "status": "Moderne Kunststofffenster", "findings": "...", "cost": 0.0}},
    "heating_electrics": {{"score": 8.0, "status": "Fernwärme", "findings": "...", "cost": 0.0}},
    "walls_substance": {{"score": 8.0, "status": "Guter Zustand", "findings": "...", "cost": 0.0}}
  }},
  "detected_findings": [
    "Konkreter visueller Befund 1...",
    "Konkreter visueller Befund 2..."
  ],
  "visual_capex_items": [
    {{
      "title": "Gewerk-Name",
      "category": "Kategorie",
      "urgency": "Sofort/Mittelfristig/Optional",
      "cost": 2500.0,
      "source_type": "FOTO_BELEG",
      "photo_indices": [4],
      "evidence_source": "📸 Sichtbar auf Foto #4: Konkreter visueller Befund am Bauteil.",
      "description": "Warum nötig..."
    }}
  ],
  "total_visual_capex": 2500.0,
  "investor_summary": "2-3 Sätze Zusammenfassung der Bildanalyse für die Verhandlung"
}}
"""

    if gemini_key.startswith("sk-"):
        # OneProvider Dev AI Vision-Modelle (OpenAI-kompatibles Format mit Base64 Image URLs)
        # Primärmodell: claude-haiku-5-5, Fallback-Modelle: qwen3.8-flash, gpt-6-luna, gemini-3.5-flash
        oneprovider_models = ["claude-haiku-5-5", "qwen3.8-flash", "gpt-6-luna", "gemini-3.5-flash"]
        user_content = [{"type": "text", "text": prompt}]
        for idx, (mime_type, b64_data) in enumerate(active_images, start=1):
            user_content.append({"type": "text", "text": f"--- Foto #{idx} ---"})
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{b64_data}"
                }
            })

        last_err = "Timeout / Verbindungsfehler"
        for model in oneprovider_models:
            try:
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": user_content
                        }
                    ],
                    "max_tokens": 8192
                }
                if model not in ["gpt-6-luna"] and not ("claude" in model.lower()):
                    payload["response_format"] = {"type": "json_object"}

                resp = requests.post(
                    "https://api.oneprovider.dev/v1/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {gemini_key}",
                        "Content-Type": "application/json"
                    },
                    timeout=90.0
                )
                if resp.status_code == 200:
                    res_data = resp.json()
                    choices = res_data.get("choices", [])
                    if choices:
                        text_content = choices[0].get("message", {}).get("content", "")
                        parsed = _clean_and_parse_llm_json(text_content)
                        if not isinstance(parsed, dict):
                            raise ValueError("Unerwartetes JSON-Format von OneProvider Vision")
                        parsed["ai_provider"] = f"OneProvider {model} Vision (Live API-Key)"
                        parsed["error"] = False
                        usage = res_data.get("usage") or {}
                        p_tok = int(usage.get("prompt_tokens") or 0)
                        c_tok = int(usage.get("completion_tokens") or 0)
                        t_tok = int(usage.get("total_tokens") or (p_tok + c_tok))
                        parsed["token_usage"] = {
                            "prompt_tokens": p_tok,
                            "candidates_tokens": c_tok,
                            "total_tokens": t_tok
                        }
                        return parsed
                else:
                    from app.network_security import sanitize_error_message
                    logger.warning(f"OneProvider Vision API status {resp.status_code} ({model}): {sanitize_error_message(resp.text[:200])}")
                    if resp.status_code in [400, 401, 403]:
                        try:
                            err_data = resp.json()
                            err_msg = sanitize_error_message(err_data.get("error", {}).get("message", f"HTTP {resp.status_code}"))
                            return {"error": True, "error_message": f"OneProvider API-Fehler ({resp.status_code}): {err_msg}"}
                        except Exception:
                            return {"error": True, "error_message": f"OneProvider API-Fehler ({resp.status_code}): Ungültiger API-Key oder Berechtigungsfehler"}
                    last_err = f"HTTP {resp.status_code} ({model})"
            except requests.exceptions.Timeout:
                logger.warning(f"Timeout calling OneProvider Vision with {model} (90s limit reached)")
                last_err = f"API Read-Timeout bei {model} (> 90s)"
            except Exception as e:
                from app.network_security import sanitize_error_message
                safe_err = sanitize_error_message(e)
                logger.warning(f"Error calling OneProvider Vision with {model}: {safe_err}")
                last_err = safe_err

        return {"error": True, "error_message": f"KI-Bildanalyse fehlgeschlagen: {last_err}"}

    # Google Gemini Vision API
    parts = [{"text": prompt}]
    for idx, (mime_type, b64_data) in enumerate(active_images, start=1):
        parts.append({"text": f"--- Foto #{idx} ---"})
        parts.append({
            "inline_data": {
                "mime_type": mime_type,
                "data": b64_data
            }
        })

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1}
    }

    # Use supported and cost-efficient Gemini models (Lite models primary, 3.8-flash fallback)
    from app.network_security import sanitize_error_message
    models = ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.8-flash"]
    last_err = "Timeout / Verbindungsfehler"
    for model in models:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            resp = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json", "x-goog-api-key": gemini_key},
                timeout=90.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                text_content = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = _clean_and_parse_llm_json(text_content)
                if not isinstance(parsed, dict):
                    raise ValueError("Unerwartetes JSON-Format von Gemini Vision")
                parsed["ai_provider"] = f"Google {model} Vision (Live API-Key)"
                parsed["error"] = False
                usage = data.get("usageMetadata") or {}
                p_tok = int(usage.get("promptTokenCount") or 0)
                c_tok = int(usage.get("candidatesTokenCount") or 0)
                t_tok = int(usage.get("totalTokenCount") or (p_tok + c_tok))
                parsed["token_usage"] = {
                    "prompt_tokens": p_tok,
                    "candidates_tokens": c_tok,
                    "total_tokens": t_tok
                }
                return parsed
            else:
                logger.warning(f"Gemini Vision API status {resp.status_code} ({model}): {sanitize_error_message(resp.text[:200])}")
                if resp.status_code in [400, 401, 403]:
                    try:
                        err_data = resp.json()
                        err_msg = sanitize_error_message(err_data.get("error", {}).get("message", f"HTTP {resp.status_code}"))
                        return {"error": True, "error_message": f"Gemini API-Fehler ({resp.status_code}): {err_msg}"}
                    except Exception:
                        return {"error": True, "error_message": f"Gemini API-Fehler ({resp.status_code}): Ungültiger API-Key oder fehlende Berechtigung"}
                last_err = f"HTTP {resp.status_code} ({model})"
        except requests.exceptions.Timeout:
            logger.warning(f"Timeout calling Gemini Vision with {model} (90s limit reached)")
            last_err = f"API Read-Timeout bei {model} (> 90s)"
        except Exception as e:
            safe_err = sanitize_error_message(e)
            logger.warning(f"Error calling Gemini Vision with {model}: {safe_err}")
            last_err = safe_err

    return {"error": True, "error_message": f"KI-Bildanalyse fehlgeschlagen: {last_err}"}


class ClassificationList(list):
    def __init__(self, iterable=(), token_usage=None):
        super().__init__(iterable)
        self.token_usage = token_usage or {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0}


def classify_extra_photos_with_gemini(
    extra_images_b64: List[Tuple[str, str]],
    start_index: int,
    property_data: Dict[str, Any],
    api_key: Optional[str] = None,
    photo_indices: Optional[List[int]] = None
) -> List[Dict[str, Any]]:
    """
    Klassifiziert und beschreibt ergänzende Galerie-Fotos (ab Bild 9 bis zum Galerieende) in kompakten Batches,
    damit auch größere Inserate auf jedem einzelnen Bild eine verständliche KI-Erklärung erhalten.
    Verhindert Index-Drift durch explizite Nummerierung und strenge Zuordnung jedes Fotos.
    """
    gemini_key = get_resolved_gemini_key(api_key)
    if not gemini_key or not extra_images_b64:
        return ClassificationList([])

    expected_indices = photo_indices if (photo_indices and len(photo_indices) == len(extra_images_b64)) else list(range(start_index, start_index + len(extra_images_b64)))
    idx_str = ", ".join(f"#{i}" for i in expected_indices)
    prompt = f"""Du analysierst die weiteren Fotos der Immobilie '{property_data.get('title')}'.
Die {len(extra_images_b64)} beigefügten Fotos haben die Nummern: {idx_str}.
Klassifiziere und beschreibe JEDES dieser Bilder einzeln der Reihe nach mit genauer Raumzuordnung, gut lesbarer 2-3-Sätze-Erklärung und konkreten Detail-Merkmalen.
WICHTIG: Setze im JSON für jedes Bild exakt den entsprechenden "index" aus dieser Liste ({', '.join(str(i) for i in expected_indices)}).

Erkenne präzise:
- 🌿 Balkon / Loggia (Balkon, Loggia, Dachterrasse, Freisitz, Balkonaustritt)
- 🏢 Fassade / Außenansicht (Mehrfamilienhaus von außen, Straßenansicht, Gebäudeansicht)
- 🌳 Weitblick / Ausblick (Aussicht, Panorama, Fernsicht, Blick ins Grüne)
- 🌳 Garten / Hof / Außenanlagen (Garten, Innenhof, Außenstellplatz, Grünfläche)
- 🛋️ Wohnbereich / Wohnzimmer (Wohnzimmer, Essbereich)
- 🛏️ Schlafzimmer / Zimmer (Schlafzimmer, Kinderzimmer, Gästezimmer)
- 🛁 Badezimmer & Sanitär (nur wenn Wanne/Dusche/Fliesen/WC vorhanden)
- 🍳 Küche & EBK (nur wenn Herd/Spüle/Küchenzeile vorhanden)
- 🚪 Flur / Eingangsbereich (Wohnungseingang, Flur, Diele, Treppenhaus)
- 📦 Keller / Abstellraum
- 🚗 Garage / Tiefgarage
- 📐 Bauzeichnung / Grundriss (Grundrissplan, Bauzeichnung, schematische Raumaufteilung)
- 🤖 KI-Visualisierung (Virtual Staging) (KI-generierte Möbel, virtuelle Küche/Visualisierung, Einrichtungsbeispiel – zwingend is_ai_generated: true setzen!)

Antworte AUSSCHLIESSLICH mit gültigem JSON:
{{
  "photo_classifications": [
    {{
      "index": {expected_indices[0]},
      "tag": "Raum-Kategorie mit Emoji",
      "description": "Gut lesbare 2-3 Sätze Erklärung...",
      "quality_score": 7.5,
      "is_blueprint": false,
      "is_ai_generated": false,
      "findings": ["Merkmal 1", "Merkmal 2"]
    }}
  ]
}}"""

    if gemini_key.startswith("sk-"):
        # OneProvider Dev AI Vision Batches (claude-haiku-5-5 primär)
        oneprovider_models = ["claude-haiku-5-5", "qwen3.8-flash", "gpt-6-luna", "gemini-3.5-flash"]
        user_content = [{"type": "text", "text": prompt}]
        for p_idx, (mime_type, b64_data) in zip(expected_indices, extra_images_b64):
            user_content.append({"type": "text", "text": f"--- Foto #{p_idx} ---"})
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{b64_data}"
                }
            })
        for model in oneprovider_models:
            try:
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": user_content}],
                    "max_tokens": 4000
                }
                if model not in ["gpt-6-luna"] and not ("claude" in model.lower()):
                    payload["response_format"] = {"type": "json_object"}

                resp = requests.post(
                    "https://api.oneprovider.dev/v1/chat/completions",
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {gemini_key}",
                        "Content-Type": "application/json"
                    },
                    timeout=60.0
                )
                if resp.status_code == 200:
                    res_data = resp.json()
                    choices = res_data.get("choices", [])
                    if choices:
                        txt = choices[0].get("message", {}).get("content", "")
                        parsed = _clean_and_parse_llm_json(txt)
                        usage = res_data.get("usage") or {}
                        p_tok = int(usage.get("prompt_tokens") or 0)
                        c_tok = int(usage.get("completion_tokens") or 0)
                        t_tok = int(usage.get("total_tokens") or (p_tok + c_tok))
                        tok_dict = {
                            "prompt_tokens": p_tok,
                            "candidates_tokens": c_tok,
                            "total_tokens": t_tok
                        }
                        pcs = parsed.get("photo_classifications", [])
                        aligned_pcs = []
                        for k, item in enumerate(pcs):
                            if not isinstance(item, dict):
                                continue
                            c_item = dict(item)
                            cur_idx = c_item.get("index")
                            try:
                                cur_idx = int(cur_idx)
                            except (TypeError, ValueError):
                                cur_idx = None
                            if cur_idx not in expected_indices:
                                if cur_idx is not None and 1 <= cur_idx <= len(expected_indices):
                                    c_item["index"] = expected_indices[cur_idx - 1]
                                elif k < len(expected_indices):
                                    c_item["index"] = expected_indices[k]
                                else:
                                    c_item["index"] = expected_indices[-1]
                            aligned_pcs.append(c_item)
                        return ClassificationList(aligned_pcs, token_usage=tok_dict)
            except Exception as e:
                logger.warning(f"Error classifying extra photos with OneProvider {model}: {e}")
        return ClassificationList([])

    # Google Gemini Vision Batches
    parts = [{"text": prompt}]
    for p_idx, (mime_type, b64_data) in zip(expected_indices, extra_images_b64):
        parts.append({"text": f"--- Foto #{p_idx} ---"})
        parts.append({"inline_data": {"mime_type": mime_type, "data": b64_data}})

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1}
    }

    from app.network_security import sanitize_error_message
    models = ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-3.8-flash"]
    for model in models:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
            resp = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json", "x-goog-api-key": gemini_key},
                timeout=45.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                txt = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed = _clean_and_parse_llm_json(txt)
                if not isinstance(parsed, dict):
                    continue
                usage = data.get("usageMetadata") or {}
                p_tok = int(usage.get("promptTokenCount") or 0)
                c_tok = int(usage.get("candidatesTokenCount") or 0)
                t_tok = int(usage.get("totalTokenCount") or (p_tok + c_tok))
                tok_dict = {
                    "prompt_tokens": p_tok,
                    "candidates_tokens": c_tok,
                    "total_tokens": t_tok
                }
                pcs = parsed.get("photo_classifications", [])
                aligned_pcs = []
                for k, item in enumerate(pcs):
                    if not isinstance(item, dict):
                        continue
                    c_item = dict(item)
                    cur_idx = c_item.get("index")
                    try:
                        cur_idx = int(cur_idx)
                    except (TypeError, ValueError):
                        cur_idx = None
                    if cur_idx not in expected_indices:
                        if cur_idx is not None and 1 <= cur_idx <= len(expected_indices):
                            c_item["index"] = expected_indices[cur_idx - 1]
                        elif k < len(expected_indices):
                            c_item["index"] = expected_indices[k]
                        else:
                            c_item["index"] = expected_indices[-1]
                    aligned_pcs.append(c_item)
                return ClassificationList(aligned_pcs, token_usage=tok_dict)
        except Exception as e:
            logger.warning(f"Error classifying extra photos with {model}: {sanitize_error_message(e)}")
    return ClassificationList([])

def call_gemini_vision_if_available(
    images_base64: List[Tuple[str, str]], 
    property_data: Dict[str, Any], 
    full_description: str = "",
    api_key: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    return call_gemini_vision(images_base64, property_data, full_description, api_key)


# ==============================================================================
# 4. SMART VISION GATING & KEY PHOTO SAMPLING (KOSTEN-MINIMIERUNG)
# ==============================================================================

def should_run_vision_ai(
    property_data: Dict[str, Any],
    metrics: Optional[Dict[str, Any]] = None,
    full_description: str = ""
) -> Tuple[bool, str]:
    """
    Intelligentes Vision-Gating (Kosten-Minimierung):
    Prüft VOR dem Start von Vision-LLM-Aufrufen, ob sich eine Multimodal-Analyse wirtschaftlich lohnt.
    Verhindert teure API-Kosten bei gelöschten Inseraten, 0-Zeichen-Rumpfdaten oder toxischen Deals.
    """
    # 1. Knockout: Inserat offline / gelöscht
    if property_data.get("is_online", 1) == 0 or str(property_data.get("availability_status", "")).lower() == "offline":
        return False, "Inserat ist offline / gelöscht (0 € Vision-Kosten)"

    # 2. Knockout: Keine Beschreibung vorhanden (< 50 Zeichen oder allgemeiner Suchlink)
    desc = (full_description or property_data.get("description") or "").strip()
    portal_url = str(property_data.get("portal_url") or "").lower()
    is_search_url = bool(re.search(r"/(?:suche|search|s-immobilien|s-wohnung-kaufen|s-haus-kaufen)\b", portal_url))
    if len(desc) < 50 or is_search_url:
        return False, "Keine Objektbeschreibung vorhanden (Rumpf-Inserat / Suchlink)"

    # 3. Knockout: Toxische Wirtschaftlichkeit (Bruttomietrendite < 2.5% und stark negativer Cashflow)
    if metrics and isinstance(metrics, dict):
        gross_yield = float(metrics.get("gross_yield") or 0.0)
        monthly_cf = float(metrics.get("monthly_cashflow") or 0.0)
        potential_cf = float(metrics.get("potential_monthly_cashflow") or monthly_cf)
        miet_ana = metrics.get("mietspiegel_analysis")
        turns_profitable = bool(miet_ana and miet_ana.get("turns_profitable"))

        if gross_yield < 2.5 and potential_cf < -300.0 and not turns_profitable:
            return False, f"Wirtschaftlich unrentabel (Rendite {gross_yield:.1f}%, CF {potential_cf:.0f} €/M) – Bilder-KI übersprungen"

    return True, "Deal ist prüfwürdig (Zahlen & Datenbasis solide)"


def select_key_investment_photos(image_urls: List[str], max_photos: int = 5) -> List[str]:
    """
    Smart Image Sampling:
    Wählt aus einer Galerie von bis zu 25 Bildern gezielt maximal 4–6 Schlüsselbilder aus,
    um redundante Fotos (gleicher Raum, Flure, Gartenwinkel) zu eliminieren und
    60–75 % der Vision-Tokenkosten einzusparen.
    Priorisiert:
      - 1x Titelbild / Fassade / Außenansicht (Index 0)
      - 1x Badezimmer (primärer CaPEx-Faktor)
      - 1x Küche / EBK
      - 1x Haustechnik / Heizung / Grundriss
      - 1-2x Haupt-Wohnräume / Böden
    """
    if not image_urls:
        return []
    if len(image_urls) <= max_photos:
        return list(image_urls)

    selected = [image_urls[0]]
    remaining = image_urls[1:]

    step = len(remaining) / float(max_photos - 1)
    for i in range(max_photos - 1):
        idx = int(round(i * step))
        idx = min(idx, len(remaining) - 1)
        url = remaining[idx]
        if url not in selected:
            selected.append(url)

    for url in remaining:
        if len(selected) >= max_photos:
            break
        if url not in selected:
            selected.append(url)

    return selected[:max_photos]


# ==============================================================================
# 5. DETERMINISTISCHE DEUTSCHE IMMOBILIEN-VISION & BKI-ENGINE
# ==============================================================================

def analyze_property_images(
    property_data: Dict[str, Any], 
    image_urls: Optional[List[str]] = None, 
    full_description: str = "",
    api_key: Optional[str] = None,
    metrics: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Vollständige KI-Bilder- und visuelle Zustandsanalyse aller Exposé-Bilder.
    Prüft vorab should_run_vision_ai (Kostensparmodus für unrentable Deals) und analysiert
    bei prüfwürdigen Deals ausnahmslos alle verfügbaren Galeriebilder vollständig.
    """
    # 0. Smart Vision Gate (Kosten-Minimierung)
    should_run, skip_reason = should_run_vision_ai(property_data, metrics=metrics, full_description=full_description)
    if not should_run:
        logger.info(f"Vision AI skipped for property {property_data.get('id')}: {skip_reason}")
        return {
            "timestamp": get_berlin_now_str(),
            "property_id": property_data.get("id"),
            "ai_provider": "Vision-Gate (Übersprungen zur Kosteneinsparung)",
            "token_usage": {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0},
            "photos_analyzed_count": 0,
            "has_interior_photos": False,
            "blueprints_count": 0,
            "notice": f"Bilder-KI übersprungen: {skip_reason}",
            "overall_visual_score": None,
            "condition_rating": "NICHT_GEPRUEFT",
            "condition_label": "⚪ Nicht geprüft (Kostensparmodus)",
            "categories": {},
            "analyzed_photos": [],
            "detected_findings": [f"Bilder-KI zur Kostensenkung nicht ausgeführt: {skip_reason}."],
            "visual_capex_items": [],
            "total_visual_capex": 0.0,
            "vision_mode": "SKIPPED_UNPROFITABLE",
            "vision_skipped": True,
            "skip_reason": skip_reason,
            "investor_summary": f"Die Bilder-KI wurde nicht gestartet ({skip_reason})."
        }

    portal_url = property_data.get("portal_url", "")
    source_portal = property_data.get("source_portal", "")
    fallback_img = property_data.get("image_url", "")

    # 1. Gather all photos
    if not image_urls:
        prop_gallery = property_data.get("gallery_images")
        if prop_gallery:
            if isinstance(prop_gallery, str):
                try:
                    parsed_g = json.loads(prop_gallery)
                    if isinstance(parsed_g, list) and len(parsed_g) > 0:
                        image_urls = list(parsed_g)
                except Exception:
                    pass
            elif isinstance(prop_gallery, list) and len(prop_gallery) > 0:
                image_urls = list(prop_gallery)

    # Wenn bisher nur wenige Vorschaubilder (z. B. aus der Suche) oder gar keine vorliegen,
    # dynamisch die vollständige Portal-Galerie live nachladen und übernehmen, sobald mehr Bilder gefunden werden.
    if portal_url and (not image_urls or len(image_urls) < 10):
        extracted = extract_gallery_images(portal_url, source_portal, fallback_img)
        if len(extracted) > len(image_urls or []):
            image_urls = extracted
            prop_id = property_data.get("id")
            if prop_id:
                try:
                    from app.database import update_property
                    upd_dict = {"gallery_images": json.dumps(extracted)}
                    cur_img = property_data.get("image_url") or ""
                    if not cur_img or "unsplash" in cur_img or len(cur_img) < 20:
                        upd_dict["image_url"] = extracted[0]
                        property_data["image_url"] = extracted[0]
                    update_property(prop_id, upd_dict)
                    property_data["gallery_images"] = json.dumps(extracted)
                except Exception as e:
                    logger.debug(f"Could not persist extracted gallery for #{prop_id}: {e}")
    elif not image_urls:
        extracted = extract_gallery_images(portal_url, source_portal, fallback_img)
        if extracted:
            image_urls = extracted

    if not image_urls and fallback_img:
        image_urls = [fallback_img]

    # If still empty, use fallback image
    if not image_urls:
        image_urls = ["https://images.unsplash.com/photo-1560448204-e02f11c3d0e2?w=800"]

    # Vollständige Galerie-Analyse: Alle vorhandenen Bilder analysieren (keine Begrenzung auf Schlüsselbilder)
    # image_urls bleibt vollständig erhalten

    # Extract numeric living space and year built safely
    try:
        living_space = float(property_data.get("living_space") or 60.0)
    except Exception:
        living_space = 60.0

    try:
        yb_val = str(property_data.get("year_built") or "1980")
        year_match = re.search(r"\b(18\d\d|19\d\d|20[0-2]\d)\b", yb_val)
        year_built = int(year_match.group(1)) if year_match else 1980
    except Exception:
        year_built = 1980
    cond_text = str(property_data.get("condition") or "Gepflegt").lower()
    desc_lower = full_description.lower() + " " + str(property_data.get("title", "")).lower()

    # 2. Inspect photos with parallel downloads and optimized thumbnail compression
    from concurrent.futures import ThreadPoolExecutor

    def _fetch_photo_task(item):
        idx, url = item
        photo_info = {
            "index": idx + 1,
            "url": url,
            "tag": "Objektansicht",
            "description": "Foto aus Exposé-Galerie",
            "quality_score": 7.5,
            "is_blueprint": False,
            "is_ai_generated": False,
            "findings": []
        }
        b64_tuple = None
        cv_score = 7.0
        try:
            from app.network_security import validate_external_url
            safe_url = validate_external_url(url)
            r = requests.get(safe_url, headers=BROWSER_HEADERS, timeout=6.0)
            if r.status_code == 200 and len(r.content) > 1000:
                cv_res = classify_photo(r.content)
                photo_info["width"] = cv_res["width"]
                photo_info["height"] = cv_res["height"]
                photo_info["quality_score"] = cv_res["visual_quality_score"]
                photo_info["is_blueprint"] = cv_res["is_blueprint"]
                photo_info["tag"] = cv_res["tag"]
                photo_info["description"] = cv_res["description"]
                cv_score = cv_res["visual_quality_score"]

                # Compress thumbnail to 384x384 JPEG quality 65 for ultra-fast, robust LLM transmission
                try:
                    t_img = PIL.Image.open(io.BytesIO(r.content)).convert("RGB")
                    t_img.thumbnail((384, 384), PIL.Image.Resampling.LANCZOS)
                    t_buf = io.BytesIO()
                    t_img.save(t_buf, format="JPEG", quality=65)
                    b64 = base64.b64encode(t_buf.getvalue()).decode("utf-8")
                    b64_tuple = ("image/jpeg", b64)
                except Exception:
                    b64 = base64.b64encode(r.content).decode("utf-8")
                    b64_tuple = ("image/jpeg", b64)
        except Exception as e:
            logger.debug(f"Failed to fetch image {url}: {e}")

        return idx, photo_info, cv_score, b64_tuple

    # Fast parallel photo download (max 8 workers)
    indexed_urls = list(enumerate(image_urls))
    with ThreadPoolExecutor(max_workers=min(len(indexed_urls), 8) or 1) as pool:
        raw_results = list(pool.map(_fetch_photo_task, indexed_urls))

    # Sort results back into exact gallery order
    raw_results.sort(key=lambda x: x[0])
    analyzed_photos = [r[1] for r in raw_results]
    pil_scores = [r[2] for r in raw_results]
    downloaded_b64_map = {r[0] + 1: r[3] for r in raw_results if r[3] is not None}
    primary_indices = [i for i in range(1, min(len(analyzed_photos), 8) + 1) if i in downloaded_b64_map]
    primary_b64 = [downloaded_b64_map[i] for i in primary_indices]

    # Check if ad contains real interior photos or solely architectural plans
    blueprints_count = sum(1 for p in analyzed_photos if p.get("is_blueprint", False))
    has_interior_photos = any(not p.get("is_blueprint", False) for p in analyzed_photos)

    # 3. If an API key is available, execute Multimodal Gemini Vision
    gemini_key = get_resolved_gemini_key(api_key)
    vision_error_msg = None
    gemini_res = None
    total_prompt_tokens = 0
    total_candidates_tokens = 0
    total_tokens = 0

    if gemini_key and primary_b64:
        gemini_res = call_gemini_vision(primary_b64, property_data, full_description=full_description, api_key=gemini_key)
        if isinstance(gemini_res, dict) and gemini_res.get("error"):
            vision_error_msg = gemini_res.get("error_message")
            gemini_res = None
        if gemini_res and "overall_visual_score" in gemini_res:
            tu = gemini_res.get("token_usage") or {}
            total_prompt_tokens += int(tu.get("prompt_tokens") or 0)
            total_candidates_tokens += int(tu.get("candidates_tokens") or 0)
            total_tokens += int(tu.get("total_tokens") or 0)
            primary_classifications = list(gemini_res.get("photo_classifications", []))
            all_classifications = []
            for pos, pc in enumerate(primary_classifications):
                if not isinstance(pc, dict):
                    continue
                pc_copy = dict(pc)
                try:
                    sent_idx = int(pc_copy.get("index", pos + 1))
                except (TypeError, ValueError):
                    sent_idx = pos + 1
                if 1 <= sent_idx <= len(primary_indices):
                    pc_copy["index"] = primary_indices[sent_idx - 1]
                all_classifications.append(pc_copy)

            # Falls einzelne Fotos noch nicht klassifiziert wurden, gezielt in 8er-Batches nachklassifizieren
            classified_indices = {int(pc.get("index", 0)) for pc in all_classifications if isinstance(pc, dict)}
            missing_photos = [
                (i, downloaded_b64_map[i])
                for i in range(1, len(analyzed_photos) + 1)
                if i in downloaded_b64_map and i not in classified_indices
            ]
            if missing_photos:
                chunk_size = 8
                chunks = [missing_photos[c_idx:c_idx + chunk_size] for c_idx in range(0, len(missing_photos), chunk_size)]
                
                def _classify_chunk(chk):
                    extra_b64 = [p[1] for p in chk]
                    chk_indices = [p[0] for p in chk]
                    start_i = chk[0][0]
                    return classify_extra_photos_with_gemini(
                        extra_b64,
                        start_index=start_i,
                        property_data=property_data,
                        api_key=gemini_key,
                        photo_indices=chk_indices
                    )

                with ThreadPoolExecutor(max_workers=min(len(chunks), 4) or 1) as c_pool:
                    chunk_results = list(c_pool.map(_classify_chunk, chunks))

                for extra_pcs in chunk_results:
                    tu_extra = getattr(extra_pcs, "token_usage", None)
                    if isinstance(tu_extra, dict):
                        total_prompt_tokens += int(tu_extra.get("prompt_tokens") or 0)
                        total_candidates_tokens += int(tu_extra.get("candidates_tokens") or 0)
                        total_tokens += int(tu_extra.get("total_tokens") or 0)
                    if extra_pcs:
                        all_classifications.extend(extra_pcs)

            # Synchronize Gemini's accurate per-photo classifications into analyzed_photos
            for pc in all_classifications:
                try:
                    idx = int(pc.get("index", 0)) - 1
                    if 0 <= idx < len(analyzed_photos):
                        raw_tag = pc.get("tag") or analyzed_photos[idx].get("tag") or "📷 Exposé-Foto"
                        raw_desc = pc.get("description") or analyzed_photos[idx].get("description") or ""
                        raw_findings = pc.get("findings") or analyzed_photos[idx].get("findings") or []
                        is_bp = bool(pc.get("is_blueprint", analyzed_photos[idx].get("is_blueprint", False)))
                        is_ai = is_ai_generated_photo(
                            raw_tag,
                            raw_desc,
                            raw_findings,
                            explicit_flag=bool(pc.get("is_ai_generated", analyzed_photos[idx].get("is_ai_generated", False))),
                        )

                        refined_tag = refine_photo_tag(raw_tag, raw_desc, raw_findings, is_blueprint=is_bp)
                        if refined_tag == "📐 Bauzeichnung / Grundriss":
                            is_bp = True
                        analyzed_photos[idx]["tag"] = refined_tag
                        analyzed_photos[idx]["description"] = raw_desc
                        if "quality_score" in pc and pc["quality_score"] is not None:
                            try:
                                analyzed_photos[idx]["quality_score"] = round(float(pc["quality_score"]), 1)
                            except (TypeError, ValueError):
                                pass
                        analyzed_photos[idx]["is_blueprint"] = is_bp
                        analyzed_photos[idx]["is_ai_generated"] = is_ai
                        if raw_findings:
                            analyzed_photos[idx]["findings"] = raw_findings
                except Exception:
                    pass

            # Final tag & flag refinement across all photos
            for p in analyzed_photos:
                is_ai = is_ai_generated_photo(
                    p.get("tag", ""),
                    p.get("description", ""),
                    p.get("findings", []),
                    explicit_flag=bool(p.get("is_ai_generated", False)),
                )
                p["is_ai_generated"] = is_ai
                p["tag"] = refine_photo_tag(
                    p.get("tag", ""),
                    p.get("description", ""),
                    p.get("findings", []),
                    is_blueprint=bool(p.get("is_blueprint", False)),
                )
                if p["tag"] == "📐 Bauzeichnung / Grundriss":
                    p["is_blueprint"] = True

            ai_photo_indices = {p["index"] for p in analyzed_photos if p.get("is_ai_generated")}
            real_photos = [p for p in analyzed_photos if not p.get("is_blueprint") and not p.get("is_ai_generated")]
            has_interior_photos = bool(real_photos)

            gem_categories = gemini_res.get("categories", {})
            raw_capex_items = gemini_res.get("visual_capex_items", [])
            has_interior_real = bool(gemini_res.get("has_interior_photos", has_interior_photos)) and has_interior_photos
            gem_capex_items = []
            max_p = len(analyzed_photos)

            # Check presence of real (non-blueprint, non-AI-staged) rooms across ALL analyzed photos
            kitchen_photos = [
                p["index"] for p in real_photos
                if (
                    p.get("tag") == "🍳 Küche / EBK"
                    or any(t in (str(p.get("tag", "")) + " " + str(p.get("description", ""))).lower() for t in ["küche", "ebk", "einbauküche", "küchenzeile"])
                )
            ]
            has_existing_ebk = False
            has_empty_real_kitchen = False
            for kp in kitchen_photos:
                matching_p = next((p for p in real_photos if p.get("index") == kp), None)
                if matching_p:
                    p_text = (str(matching_p.get("description", "")) + " " + " ".join(matching_p.get("findings") or [])).lower()
                    if _EMPTY_KITCHEN_REGEX.search(p_text):
                        has_empty_real_kitchen = True
                    elif _REAL_EBK_REGEX.search(p_text):
                        has_existing_ebk = True

            # Empty real kitchen photo beats ambiguous mentions if no real installed EBK was seen
            if has_empty_real_kitchen and not has_existing_ebk:
                has_existing_ebk = False

            ai_kitchen_photos = [
                p["index"] for p in analyzed_photos
                if p.get("is_ai_generated") and any(
                    w in (str(p.get("description", "")) + " " + " ".join(p.get("findings") or [])).lower()
                    for w in ["küche", "ebk", "einbauküche", "küchenzeile", "kochfeld", "herd"]
                )
            ]

            bath_photos = [
                p["index"] for p in real_photos
                if (
                    p.get("tag") == "🛁 Badezimmer & Sanitär"
                    or any(t in (str(p.get("tag", "")) + " " + str(p.get("description", ""))).lower() for t in ["bad", "badezimmer", "sanitär", "wanne", "dusche"])
                )
            ]

            for raw_it in raw_capex_items:
                if not isinstance(raw_it, dict):
                    continue
                it = dict(raw_it)
                title_l = str(it.get("title", "")).lower()
                cat_l = str(it.get("category", "")).lower()
                desc_l = str(it.get("description", "")).lower()
                combined_l = f"{title_l} {cat_l} {desc_l}"

                p_idxs: List[int] = []
                for p in (it.get("photo_indices") or []):
                    try:
                        pi = int(p)
                        if 1 <= pi <= max_p and pi not in ai_photo_indices and pi not in p_idxs:
                            p_idxs.append(pi)
                    except (ValueError, TypeError):
                        pass

                # Infer matching photo numbers strictly across REAL (non-blueprint, non-AI-generated) photos:
                if has_interior_real:
                    found_photos: List[int] = []
                    if any(k in combined_l for k in ["bad", "sanitär", "wc", "dusche", "wanne"]):
                        found_photos = [p["index"] for p in real_photos if any(t in (p.get("tag", "") + " " + p.get("description", "")).lower() for t in ["bad", "wc", "sanitär", "dusche", "wanne"])]
                    elif any(k in combined_l for k in ["küche", "ebk"]):
                        found_photos = [p["index"] for p in real_photos if any(t in (p.get("tag", "") + " " + p.get("description", "")).lower() for t in ["küche", "ebk"])]
                    elif any(k in combined_l for k in ["flurfliesen", "flur", "diele"]):
                        found_photos = [p["index"] for p in real_photos if any(t in (p.get("tag", "") + " " + p.get("description", "")).lower() for t in ["flur", "diele", "eingang"])]
                    elif any(k in combined_l for k in ["fenster", "verglasung"]):
                        found_photos = [p["index"] for p in real_photos if any(t in (p.get("description", "") + " " + " ".join(p.get("findings") or [])).lower() for t in ["fenster", "verglasung", "rollladen"])]
                    elif any(k in combined_l for k in ["boden", "laminat", "parkett", "maler", "wand", "tapete", "decke", "holzdecke"]):
                        found_photos = [p["index"] for p in real_photos if any(t in (p.get("tag", "")).lower() for t in ["wohn", "schlaf", "zimmer", "flur", "kinder"])][:3]
                    elif any(k in combined_l for k in ["sicherung", "elektrik", "unterverteilung", "schalter", "heizkörper", "rippenheizkörper"]):
                        found_photos = [p["index"] for p in real_photos if any(t in (p.get("description", "") + " " + " ".join(p.get("findings") or [])).lower() for t in ["sicherung", "elektrik", "schalter", "heizkörper", "rippen"])]

                    if found_photos:
                        p_idxs = [i for i in found_photos if i not in ai_photo_indices]

                # Specific Gewerk Reconciliation:
                # 1. KÜCHE: Only confirm EBK if a REAL (non-AI-staged) kitchen photo shows an installed EBK!
                if any(k in combined_l for k in ["küche", "ebk"]):
                    cur_k_cost = float(it.get("cost") or 0.0)
                    is_blind_k_guess = any(
                        m in combined_l
                        for m in ["nicht fotografiert", "keinem der", "nicht auf fotos", "keine einbauküche", "fehlt", "ohne besichtigungsbeleg"]
                    )
                    if not has_existing_ebk and (has_empty_real_kitchen or ai_kitchen_photos):
                        # Drop bogus 0-EUR "EBK vorhanden" item if LLM was fooled by AI-generated kitchen photo
                        if cur_k_cost <= 0 or any(w in combined_l for w in ["vorhanden", "funktionstüchtig", "gepflegte einbauküche"]):
                            continue
                        if kitchen_photos:
                            p_idxs = kitchen_photos
                    elif has_existing_ebk and (cur_k_cost <= 0 or is_blind_k_guess):
                        it["cost"] = 0.0
                        it["urgency"] = "Kein Handlungsbedarf"
                        it["title"] = "Einbauküche vorhanden & funktionstüchtig"
                        it["source_type"] = "FOTO_BELEG"
                        p_idxs = kitchen_photos
                        refs = ", ".join(f"#{i}" for i in p_idxs)
                        it["evidence_source"] = f"📸 Sichtbar auf Foto {refs}: Vorhandene Einbauküche mit Schränken und Elektrogeräten – keine Neuanschaffung nötig (0 €)."
                        it["description"] = "Die Einbauküche ist im Objekt vorhanden und funktionstüchtig für Vermietung."
                    elif kitchen_photos:
                        p_idxs = kitchen_photos
                        it["source_type"] = "FOTO_BELEG"

                # 2. BAD: Only override blind fallback guesses ("ohne Besichtigungsbeleg" / "nicht auf Fotos sichtbar");
                # preserve higher photo-evidenced CapEx estimates when bathroom defects are documented.
                if any(k in combined_l for k in ["bad", "sanitär", "wc", "dusche", "wanne"]):
                    if bath_photos:
                        p_idxs = bath_photos
                        is_blind_b_guess = any(
                            m in combined_l
                            for m in ["ohne besichtigungsbeleg", "nicht fotografiert", "keinem der", "nicht auf fotos", "kein badezimmerfoto"]
                        )
                        if is_blind_b_guess:
                            it["cost"] = 1500.0
                            it["title"] = "Badezimmer-Modernisierung / Sanitärauffrischung"
                            it["description"] = "Badezimmer ist verfliest und mit Sanitärobjekten ausgestattet; optische Auffrischung/Silikonfugen eingeplant."
                        it["source_type"] = "FOTO_BELEG"
                        refs = ", ".join(f"#{i}" for i in p_idxs)
                        it["evidence_source"] = f"📸 Sichtbar auf Foto {refs}: Badezimmer auf den Objektfotos dokumentiert."

                if not has_interior_real:
                    src_type = "BAUJAHR_MUTMASSUNG"
                    p_idxs = []
                elif p_idxs:
                    src_type = "FOTO_BELEG"
                else:
                    src_type = str(it.get("source_type") or "BAUJAHR_MUTMASSUNG").upper()
                    if src_type not in ["FOTO_BELEG", "BAUJAHR_MUTMASSUNG"]:
                        src_type = "BAUJAHR_MUTMASSUNG"

                ev_src = str(it.get("evidence_source") or "").strip()
                # If we now have verified photo evidence (e.g. photo #10 is a bathroom), replace any outdated "nicht auf Fotos sichtbar" text!
                if src_type == "FOTO_BELEG" and p_idxs:
                    if not ev_src or "keinem der" in ev_src.lower() or "nicht auf fotos" in ev_src.lower():
                        refs = ", ".join(f"#{i}" for i in p_idxs)
                        matching_findings = []
                        for pi in p_idxs:
                            matching_photo = next((p for p in analyzed_photos if p.get("index") == pi), None)
                            if matching_photo:
                                if matching_photo.get("findings"):
                                    matching_findings.extend(matching_photo["findings"][:2])
                                elif matching_photo.get("description"):
                                    matching_findings.append(matching_photo["description"][:100])
                        finding_summary = ("; ".join(matching_findings)) if matching_findings else "Aus den Fotos abgeleiteter Modernisierungsbefund."
                        ev_src = f"📸 Sichtbar auf Foto {refs}: {finding_summary}"
                elif not ev_src or "keinem der" in ev_src.lower() or "nicht auf fotos" in ev_src.lower():
                    if not has_interior_real:
                        ev_src = f"⚠️ Exposé-/Baujahrs-Mutmaßung (Bj. {year_built}): Im Inserat liegen keine Innenraumfotos vor."
                    else:
                        ev_src = f"⚠️ Baujahrs-/Pauschal-Mutmaßung (Bj. {year_built}): Auf den {max_p} Fotos nicht direkt sichtbar (Sicherheitspuffer)."

                it["source_type"] = src_type
                it["photo_indices"] = p_idxs
                it["evidence_source"] = ev_src
                gem_capex_items.append(it)

            # Reconcile categories for kitchen & bath based ONLY on REAL (non-AI-staged) photos
            k_cat = gem_categories.setdefault("kitchen", {})
            k_findings_l = str(k_cat.get("findings", "")).lower()
            k_status_l = str(k_cat.get("status", "")).lower()
            k_item_exists = any(
                any(w in (str(it.get("title", "")) + " " + str(it.get("category", ""))).lower() for w in ["küche", "ebk"])
                and float(it.get("cost") or 0.0) > 0
                for it in gem_capex_items
            )
            desc_mentions_ebk = any(
                k in desc_lower for k in ["einbauküche", "ebk", "küchenzeile", "inkl. küche"]
            ) and not any(neg in desc_lower for neg in ["keine einbauküche", "ohne einbauküche", "keine ebk", "ohne ebk"])

            if not has_existing_ebk and (has_empty_real_kitchen or ai_kitchen_photos) and not desc_mentions_ebk:
                # Kitchen is missing/empty in real photos (even if an AI-generated visualization showed an EBK!)
                ebk_cost = max(float(k_cat.get("cost") or 0.0), BENCHMARK_COSTS["kitchen_new"])
                k_cat["score"] = min(float(k_cat.get("score") or 5.0), 5.0)
                k_cat["cost"] = ebk_cost
                ai_refs = ", ".join(f"#{i}" for i in ai_kitchen_photos)
                real_k_refs = ", ".join(f"#{i}" for i in kitchen_photos)
                if ai_kitchen_photos and real_k_refs:
                    k_cat["status"] = "Keine EBK (KI-Visualisierung ignoriert)"
                    k_cat["findings"] = (
                        f"Echtes Foto ({real_k_refs}) zeigt eine leere Küche ohne Einbauküche; "
                        f"KI-Visualisierung ({ai_refs}) wurde von der Bewertung ausgeschlossen."
                    )
                    ev_k = f"📸 Reales Foto {real_k_refs}: Keine Einbauküche vorhanden (KI-Visualisierung auf Foto {ai_refs} ignoriert)."
                elif ai_kitchen_photos:
                    k_cat["status"] = "Keine EBK (nur KI-Visualisierung)"
                    k_cat["findings"] = f"Foto {ai_refs} ist eine KI-Visualisierung / Virtual Staging und zählt nicht als vorhandene Einbauküche."
                    ev_k = f"⚠️ KI-Visualisierung auf Foto {ai_refs} ausgeschlossen: Im realen Ist-Zustand keine Einbauküche belegt."
                else:
                    k_cat["status"] = "Keine Einbauküche vorhanden"
                    k_cat["findings"] = f"Auf Foto {real_k_refs} ist ein leerer Küchenraum ohne Einbauküche dokumentiert."
                    ev_k = f"📸 Sichtbar auf Foto {real_k_refs}: Küche ohne Einbauküche – Neuanschaffung einkalkuliert."

                if not k_item_exists:
                    gem_capex_items.append({
                        "title": "Einbauküche (EBK) Neuanschaffung",
                        "category": "Küche & EBK",
                        "urgency": "HOCH",
                        "cost": ebk_cost,
                        "source_type": "FOTO_BELEG" if kitchen_photos else "BAUJAHR_MUTMASSUNG",
                        "photo_indices": list(kitchen_photos),
                        "evidence_source": ev_k,
                        "description": "Im Ist-Zustand ist keine Einbauküche vorhanden (KI-Visualisierungen/Virtual Staging unberücksichtigt).",
                    })
            elif kitchen_photos and has_existing_ebk:
                if (
                    "nicht fotografiert" in k_findings_l
                    or "keinem" in k_findings_l
                    or "nicht auf fotos" in k_findings_l
                    or "keine" in k_status_l
                    or float(k_cat.get("cost") or 0.0) <= 0
                ):
                    k_cat["score"] = max(float(k_cat.get("score") or 0.0), 7.5)
                    k_cat["status"] = "Gepflegte Einbauküche vorhanden"
                    k_cat["cost"] = 0.0
                    refs = ", ".join(f"#{i}" for i in kitchen_photos)
                    k_cat["findings"] = f"Auf Foto {refs} ist eine funktionstüchtige Einbauküche mit Schrankzeile und Geräten belegt."

            if bath_photos:
                b_cat = gem_categories.setdefault("bathroom", {})
                b_findings = str(b_cat.get("findings", "")).lower()
                if "kein badezimmerfoto" in b_findings or "nicht fotografiert" in b_findings or "ohne besichtigungsbeleg" in b_findings:
                    b_cat["score"] = max(float(b_cat.get("score") or 0.0), 7.0)
                    b_cat["status"] = "Solider Zustand / Verfliest"
                    b_cat["cost"] = 1500.0
                    refs = ", ".join(f"#{i}" for i in bath_photos)
                    b_cat["findings"] = f"Auf Foto {refs} dokumentiert: Bad mit Wanne, Sanitärobjekten und Wandverfliesung."

            # Ensure ALL 6 standard categories exist in gem_categories (Flooring, Windows, Heating/Electrics, Walls):
            if "flooring" not in gem_categories or not isinstance(gem_categories["flooring"], dict) or not gem_categories["flooring"].get("status"):
                floor_mentions = [
                    p["description"] for p in real_photos
                    if any(w in (p.get("description", "") + " " + p.get("tag", "")).lower() for w in ["boden", "fliesen", "laminat", "parkett", "dielen", "teppich", "holzboden"])
                ]
                f_desc = floor_mentions[0][:140] if floor_mentions else "Solide Bodenbeläge in den Wohnbereichen sichtbar."
                gem_categories["flooring"] = {
                    "score": 7.5,
                    "status": "Gepflegt" if has_interior_photos else "Nicht beurteilt",
                    "findings": f_desc,
                    "cost": 0.0
                }

            if "windows" not in gem_categories or not isinstance(gem_categories["windows"], dict) or not gem_categories["windows"].get("status"):
                win_mentions = [
                    p["description"] for p in real_photos
                    if any(w in (p.get("description", "") + " " + p.get("tag", "")).lower() for w in ["fenster", "rollladen", "verglasung", "fensterfront", "dachfenster"])
                ]
                w_desc = win_mentions[0][:140] if win_mentions else "Fenster wirken dem Baujahr entsprechend intakt."
                gem_categories["windows"] = {
                    "score": 7.5,
                    "status": "Solider Zustand" if has_interior_photos else "Nicht beurteilt",
                    "findings": w_desc,
                    "cost": 0.0
                }

            if "heating_electrics" not in gem_categories or not isinstance(gem_categories["heating_electrics"], dict) or not gem_categories["heating_electrics"].get("status"):
                heat_mentions = [
                    p["description"] for p in real_photos
                    if any(w in (p.get("description", "") + " " + p.get("tag", "")).lower() for w in ["heizung", "heizkörper", "kamin", "kaminofen", "infrarot", "sicherung", "elektrik"])
                ]
                h_desc = heat_mentions[0][:140] if heat_mentions else "Heizungs- und Elektroinstallationen laut Exposé."
                gem_categories["heating_electrics"] = {
                    "score": 7.0,
                    "status": "Funktionsfähig",
                    "findings": h_desc,
                    "cost": 0.0
                }

            if "walls_substance" not in gem_categories or not isinstance(gem_categories["walls_substance"], dict) or not gem_categories["walls_substance"].get("status"):
                gem_categories["walls_substance"] = {
                    "score": 7.5,
                    "status": "Guter Zustand",
                    "findings": "Keine strukturellen Schäden, Risse oder Feuchtigkeitsspuren auf den Fotos erkennbar.",
                    "cost": 0.0
                }

            # Sanitize detected findings
            clean_findings = []
            for f in (gemini_res.get("detected_findings") or []):
                f_l = str(f).lower()
                if ("küche fehlen" in f_l or "bad und küche fehlen" in f_l or "bad und küche" in f_l) and (has_existing_ebk or bath_photos):
                    if has_existing_ebk and bath_photos:
                        clean_findings.append(f"Küche und Badezimmer sind im Bildmaterial dokumentiert (Küche Foto #{', #'.join(str(i) for i in kitchen_photos)}, Bad Foto #{', #'.join(str(i) for i in bath_photos)}).")
                    elif has_existing_ebk:
                        clean_findings.append(f"Einbauküche auf Foto #{', #'.join(str(i) for i in kitchen_photos)} belegt – keine Neuanschaffung nötig.")
                    elif bath_photos:
                        clean_findings.append(f"Badezimmer auf Foto #{', #'.join(str(i) for i in bath_photos)} dokumentiert.")
                elif "küche" in f_l and ("fehlt" in f_l or "nicht" in f_l or "kein" in f_l) and has_existing_ebk:
                    continue
                elif "bad" in f_l and ("fehlt" in f_l or "nicht" in f_l or "kein" in f_l) and bath_photos:
                    continue
                else:
                    clean_findings.append(f)

            if ai_photo_indices:
                ai_refs_str = ", ".join(f"#{i}" for i in sorted(ai_photo_indices))
                ai_warn_finding = f"🤖 KI-Filter: Foto {ai_refs_str} wurde als KI-Visualisierung / Virtual Staging erkannt und nicht in Bewertung/CaPEx einbezogen."
                if ai_warn_finding not in clean_findings:
                    clean_findings.insert(0, ai_warn_finding)

            # Sanitize notice
            gem_notice = gemini_res.get("notice")
            if not gem_notice and not has_interior_photos:
                gem_notice = f"Hinweis: Das Inserat enthält keine echten Innenraum-Fotos ({len(analyzed_photos)} Pläne/Visualisierungen)."
            elif gem_notice and (has_existing_ebk or bath_photos):
                notice_l = gem_notice.lower()
                if any(w in notice_l for w in ["nicht fotografiert", "fehlen", "keinem", "nicht abgebildet", "ungeprüft"]):
                    if ("küche" in notice_l and has_existing_ebk) or ("bad" in notice_l and bath_photos):
                        k_str = f"Küche (Foto #{', #'.join(str(i) for i in kitchen_photos)})" if has_existing_ebk else ""
                        b_str = f"Bad (Foto #{', #'.join(str(i) for i in bath_photos)})" if bath_photos else ""
                        rooms_str = " und ".join(s for s in [k_str, b_str] if s)
                        gem_notice = f"Es liegen echte Innenraumfotos ({len(real_photos)} reale Fotos) inklusive {rooms_str} vor."

            if ai_photo_indices:
                ai_refs_str = ", ".join(f"#{i}" for i in sorted(ai_photo_indices))
                ai_note = f"⚠️ Hinweis: Foto {ai_refs_str} ist eine KI-Visualisierung (Virtual Staging) und wurde von der Objektbewertung ausgeschlossen."
                if not gem_notice:
                    gem_notice = ai_note
                elif ai_refs_str not in gem_notice and "virtual staging" not in gem_notice.lower() and "ki-visualisierung" not in gem_notice.lower():
                    gem_notice = f"{gem_notice} {ai_note}".strip()

            total_capex = round(sum(float(item.get("cost") or 0.0) for item in gem_capex_items), 2)

            # Sanitize investor summary
            inv_sum = gemini_res.get("investor_summary", "")
            if inv_sum and (has_existing_ebk or bath_photos):
                inv_l = inv_sum.lower()
                if ("küche" in inv_l and has_existing_ebk) or ("bad" in inv_l and bath_photos):
                    if any(w in inv_l for w in ["fehlen", "fehlt", "nicht fotografiert", "nicht dokumentiert", "ungeprüft", "ebk-austausch", "keinem foto", "keinem der"]):
                        inv_sum = re.sub(
                            r"(?i)[^.!?]*(?:fehlen|fehlt|nicht fotografiert|nicht dokumentiert|ungeprüft|ebk-austausch)[^.!?]*(?:bad|küche|ebk)[^.!?]*[.!?]",
                            "",
                            inv_sum
                        ).strip()
                        inv_sum = re.sub(
                            r"(?i)[^.!?]*(?:bad|küche|ebk)[^.!?]*(?:fehlen|fehlt|nicht fotografiert|nicht dokumentiert|ungeprüft|ebk-austausch)[^.!?]*[.!?]",
                            "",
                            inv_sum
                        ).strip()
                        k_claim = f"Eine Einbauküche ist auf Foto #{', #'.join(str(i) for i in kitchen_photos)} belegt (keine EBK-Neuanschaffung nötig)." if has_existing_ebk else ""
                        b_claim = f"Das Badezimmer ist auf Foto #{', #'.join(str(i) for i in bath_photos)} dokumentiert." if bath_photos else ""
                        claims = " ".join(c for c in [k_claim, b_claim] if c)
                        inv_sum = f"{inv_sum} {claims}".strip()
                        def _fmt_de(v):
                            return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                        inv_sum = re.sub(r"(?i)\(bki-sicherheitspauschale gesamt ca\. [\d\.]+\s*€\)", f"(BKI-Kalkulation ca. {_fmt_de(total_capex)} €)", inv_sum)
            if not has_existing_ebk and (has_empty_real_kitchen or ai_kitchen_photos):
                inv_sum = re.sub(r"(?i)[^.!?]*(?:keine\s+ebk-?neuanschaffung|einbauküche\s+(?:ist\s+)?vorhanden|keine\s+investitionskosten\s+für\s+die\s+küche)[^.!?]*[.!?]", "", inv_sum).strip()
                if "küche" not in inv_sum.lower() and "ebk" not in inv_sum.lower():
                    inv_sum = f"{inv_sum} Hinweis: Im realen Ist-Zustand ist keine Einbauküche vorhanden (KI-Visualisierungen ausgeschlossen).".strip()
            if "keinem der 8 fotos" in inv_sum.lower() or "keinem der fotos" in inv_sum.lower():
                inv_sum = re.sub(r"(?i)auf keinem der \d+ fotos", f"auf den {len(analyzed_photos)} Fotos", inv_sum)

            return {
                "timestamp": get_berlin_now_str(),
                "property_id": property_data.get("id"),
                "ai_provider": gemini_res.get("ai_provider", "Google Gemini 1.5 Flash Vision (Live API-Key)"),
                "token_usage": {
                    "prompt_tokens": total_prompt_tokens,
                    "candidates_tokens": total_candidates_tokens,
                    "total_tokens": total_tokens
                },
                "photos_analyzed_count": len(analyzed_photos),
                "has_interior_photos": has_interior_real,
                "blueprints_count": blueprints_count,
                "ai_generated_photos_count": len(ai_photo_indices),
                "notice": gem_notice,
                "overall_visual_score": round(float(gemini_res.get("overall_visual_score", 7.0)), 1),
                "condition_rating": gemini_res.get("condition_rating", "GEPFLEGT"),
                "condition_label": gemini_res.get("condition_label", "🟢 Gepflegter Zustand"),
                "categories": gem_categories,
                "analyzed_photos": analyzed_photos,
                "detected_findings": clean_findings,
                "visual_capex_items": gem_capex_items,
                "total_visual_capex": total_capex,
                "vision_mode": "ALL_PHOTOS",
                "vision_skipped": False,
                "investor_summary": inv_sum
            }

    # Wenn ein API-Key vorhanden war, Gemini Vision aber fehlgeschlagen/getimed-outed ist:
    # Nicht heimlich falsche PIL-Halluzinationen ("Bad" bei Außenaufnahme) anzeigen, sondern ehrlich melden!
    if gemini_key and downloaded_b64_map and (not gemini_res or "overall_visual_score" not in gemini_res):
        logger.warning(f"Gemini Vision call failed for property {property_data.get('id')}: {vision_error_msg or 'Timeout / No Response'}")
        for p in analyzed_photos:
            if not p.get("is_blueprint"):
                p["tag"] = "📷 Objektansicht"
                p["description"] = "Exposé-Foto (Automatische KI-Erkennung aufgrund von API-Timeout nicht abgeschlossen)"
                p["quality_score"] = None
        
        return {
            "timestamp": get_berlin_now_str(),
            "property_id": property_data.get("id"),
            "ai_provider": "⚠️ KI-Bildanalyse fehlgeschlagen (API-Timeout / Fehler)",
            "token_usage": {
                "prompt_tokens": total_prompt_tokens,
                "candidates_tokens": total_candidates_tokens,
                "total_tokens": total_tokens
            },
            "photos_analyzed_count": len(analyzed_photos),
            "has_interior_photos": has_interior_photos,
            "blueprints_count": blueprints_count,
            "notice": f"⚠️ Achtung: Die multimodale KI-Bildanalyse konnte nicht ausgeführt werden ({vision_error_msg or 'API-Timeout'}).",
            "overall_visual_score": None,
            "condition_rating": "NICHT_GEPRUEFT",
            "condition_label": "⚠️ Bild-KI fehlgeschlagen (Timeout)",
            "categories": {},
            "analyzed_photos": analyzed_photos,
            "detected_findings": [
                f"⚠️ Multimodale KI-Bildanalyse fehlgeschlagen: {vision_error_msg or 'API-Timeout'}.",
                "Hinweis: Bitte starte die Bildanalyse erneut oder prüfe den API-Key."
            ],
            "visual_capex_items": [],
            "total_visual_capex": 0.0,
            "vision_mode": "ALL_PHOTOS",
            "vision_failed": True,
            "vision_error": vision_error_msg or "API Read-Timeout (> 90s)",
            "vision_skipped": False,
            "investor_summary": "Die multimodale KI-Bildanalyse ist aufgrund eines Timeouts fehlgeschlagen. Es wurden keine KI-Befunde generiert."
        }

    # 4. Semantic & Optical Clues from Description & Metadata (Deterministic BKI Engine)
    is_kernsaniert = any(k in desc_lower for k in ["kernsaniert", "vollsaniert", "erstbezug", "luxus", "komplett renoviert", "neuwertig"])
    is_renovierungsbeduerftig = any(k in desc_lower for k in ["renovieren", "renovierungsbedürftig", "sanierungsbedürftig", "bastler", "handwerker", "sanierungsstau"])
    has_ebk = any(k in desc_lower for k in ["einbauküche", "ebk", "küchenzeile", "inkl. küche"])
    has_vintage_bath = any(k in desc_lower for k in ["altes bad", "70er", "80er", "bahamabeige", "renovierungsbedürftiges bad"])
    has_modern_windows = any(k in desc_lower for k in ["kunststofffenster", "3-fach", "2-fach", "isolierverglasung", "fenster neu", "neue fenster"])
    has_parquet = any(k in desc_lower for k in ["parkett", "echtholzparkett", "dielen"])
    has_laminate = any(k in desc_lower for k in ["laminat", "vinyl", "designboden", "klick-vinyl"])
    has_tiles = any(k in desc_lower for k in ["fliesen", "gefliest"])

    # Base quality level derived from building year and verified condition
    avg_pil = (sum(pil_scores) / len(pil_scores)) if pil_scores else 7.0

    # Special handling: Listing has ONLY blueprints/drawings and NO interior photos
    notice = None
    if not has_interior_photos:
        if is_renovierungsbeduerftig:
            bad_score = 4.5
            bad_status = "Vollsanierung kalkuliert"
            bad_findings = "Keine Innenraum-Fotos vorhanden. Aufgrund des im Exposé genannten Sanierungsbedarfs ist eine Bad-Sanierung einzuplanen."
            bad_cost = 5800.0

            kitchen_score = 4.0
            kitchen_status = "Keine EBK vorhanden"
            kitchen_findings = "In den Bauzeichnungen ist keine Einbauküche dokumentiert. Neue Standard-EBK für Vermietung kalkuliert."
            kitchen_cost = 4500.0

            floor_score = 5.0
            floor_status = "Bodenbelagserneuerung nötig"
            floor_findings = "Böden müssen vor Neuvermietung saniert bzw. mit Klick-Vinyl belegt werden."
            floor_cost = 2600.0

            win_score = 6.5
            win_status = "Prüfung bei Besichtigung"
            win_findings = "Fensterzustand optisch nicht überprüfbar; mittelfristige Wartung einplanen."
            win_cost = 0.0

            heat_score = 5.0
            heat_status = "Elektrik-Überprüfung"
            heat_findings = "FI-Schutzschalter und Schalterprogramm bei Sanierungsstau prüfen."
            heat_cost = 1560.0

            wall_score = 4.5
            wall_status = "Maler- & Spachtelarbeiten"
            wall_findings = "Umfassender Maleraufwand vor Neuvermietung erforderlich."
            wall_cost = 1500.0

            est_total = bad_cost + kitchen_cost + floor_cost + win_cost + heat_cost + wall_cost
            est_fmt = f"{est_total:,.0f}".replace(",", ".")
            notice = (
                f"Hinweis: Das Inserat enthält ausschließlich {len(analyzed_photos)} Bauzeichnungen/Pläne und keine Innenraum-Fotos. "
                f"Da im Exposé Renovierungs- bzw. Sanierungsbedarf genannt wird, basiert die CaPEx-Kalkulation ({est_fmt} €) "
                f"auf einer fundierten Komplettrenovierung vor Neuvermietung."
            )

            overall_visual_score = 4.8
            condition_rating = "RENOVIERUNGSBEDUERFTIG"
            condition_label = "🟠 Renovierungsbedürftig / Hoher Sanierungsaufwand (Text-Befund)"
        else:
            notice = (
                f"Hinweis: Das Inserat enthält ausschließlich {len(analyzed_photos)} Bauzeichnungen/Grundrisse und keine Innenraum-Fotos. "
                f"Der kalkulierte Zustand basiert auf Baujahr und Exposé-Angaben."
            )
            bad_score = 7.0
            bad_status = "Zustand laut Baujahr"
            bad_findings = "Keine Innenraum-Fotos im Inserat vorhanden (nur Grundrisse/Pläne)."
            bad_cost = 0.0

            kitchen_score = 6.0
            kitchen_status = "Keine EBK ersichtlich"
            kitchen_findings = "EBK bei Besichtigung prüfen."
            kitchen_cost = 0.0

            floor_score = 7.0
            floor_status = "Böden laut Exposé"
            floor_findings = "Zustand der Beläge vor Ort verifizieren."
            floor_cost = 0.0

            win_score = 7.5
            win_status = "Isolierverglasung"
            win_findings = "Fenster bei Begehung sichten."
            win_cost = 0.0

            heat_score = 7.0
            heat_status = "Zentralheizung"
            heat_findings = "Standard-Installation."
            heat_cost = 0.0

            wall_score = 7.0
            wall_status = "Standard-Wandflächen"
            wall_findings = "Kosmetischer Anstrich einplanen."
            wall_cost = round(living_space * 15.0, -1)

            overall_visual_score = 6.9
            condition_rating = "MODERNISIERUNGSBEDARF"
            condition_label = "🟡 Solide (Keine Innenfotos - Pläne hinterlegt)"
    else:
        # Normal Photo-Based Due Diligence (Photos of rooms exist)
        # Category 1: Bathroom
        if is_kernsaniert:
            bad_score = 9.4
            bad_status = "Kernsaniert / Neuwertig"
            bad_findings = "Modernes Bad mit ebenerdiger Walk-In Dusche, zeitgemäßen Fliesen und neuen Sanitärobjekten."
            bad_cost = 0.0
        elif has_vintage_bath or is_renovierungsbeduerftig or (year_built < 1985 and "bad neu" not in desc_lower):
            bad_score = 5.2
            bad_status = "Modernisierungsbedarf"
            bad_findings = "Fliesendesign und Sanitärobjekte älteren Datums (80er/90er-Jahre Look). Armaturen und Fugen abgenutzt."
            bad_cost = BENCHMARK_COSTS["bath_complete"]
        else:
            bad_score = 7.6
            bad_status = "Gepflegter Zustand"
            bad_findings = "Gepflegtes Standard-Badezimmer, Wanne/Dusche intakt, kein akuter Sanierungsstau."
            bad_cost = 0.0

        # Category 2: Kitchen (Strictly ignore AI-generated visualizations and negated empty-kitchen descriptions!)
        has_photo_ebk = any(
            not p.get("is_blueprint")
            and not p.get("is_ai_generated")
            and not _EMPTY_KITCHEN_REGEX.search(str(p.get("description", "")) + " " + " ".join(p.get("findings") or []))
            and (
                p.get("tag") == "🍳 Küche / EBK"
                or bool(_REAL_EBK_REGEX.search(str(p.get("description", "")) + " " + " ".join(p.get("findings") or [])))
            )
            for p in analyzed_photos
        )
        if has_ebk or has_photo_ebk:
            if is_kernsaniert or "moderne einbauküche" in desc_lower or "hochwertige küche" in desc_lower:
                kitchen_score = 9.0
                kitchen_status = "Moderne EBK inklusive"
                kitchen_findings = "Hochwertige Einbauküche mit Markengeräten (Ceran/Induktion, Backofen, Spülmaschine) vorhanden."
                kitchen_cost = 0.0
            else:
                kitchen_score = 7.5
                kitchen_status = "Einbauküche vorhanden"
                kitchen_findings = "Solide Einbauküche vorhanden. Fronten und Arbeitsplatte gepflegt, funktionstüchtig."
                kitchen_cost = 0.0
        else:
            kitchen_score = 5.0
            kitchen_status = "Keine EBK / Erneuerung ratsam"
            kitchen_findings = "Aktuell keine Einbauküche im Inserat ersichtlich. Für Neuvermietung empfiehlt sich eine Standard-EBK."
            kitchen_cost = BENCHMARK_COSTS["kitchen_new"]

        # Category 3: Flooring
        if is_kernsaniert or has_parquet:
            floor_score = 9.1
            floor_status = "Hochwertiger Belag"
            floor_findings = "Edler Parkett- bzw. großformatiger Fliesenboden. Keine nennenswerten Gehspuren."
            floor_cost = 0.0
        elif has_laminate or has_tiles:
            floor_score = 7.8
            floor_status = "Gepflegtes Laminat / Fliesen"
            floor_findings = "Zeitgemäßer Bodenbelag (Laminat/Fliesen). Leichte normale Gebrauchsspuren, sofort bezugsfertig."
            floor_cost = 0.0
        elif is_renovierungsbeduerftig:
            floor_score = 4.8
            floor_status = "Erneuerungsbedarf"
            floor_findings = "Abgenutzte Bodenbeläge oder alter Teppichboden. Vollständige Erneuerung durch Klick-Vinyl angeraten."
            floor_cost = round(living_space * BENCHMARK_COSTS["flooring_sqm"], -1)
        else:
            floor_score = 7.2
            floor_status = "Solider Zustand"
            floor_findings = "Standard-Bodenbelag in bewohnbarem Zustand. Partieller Austausch optional."
            floor_cost = 0.0

        # Category 4: Windows & Insulation
        if has_modern_windows or is_kernsaniert or year_built >= 2005:
            win_score = 9.2
            win_status = "Moderne Isolierverglasung"
            win_findings = "Weiße Kunststofffenster mit Mehrscheiben-Isolierverglasung. Dichtungen und Beschläge in Ordnung."
            win_cost = 0.0
        elif year_built < 1980 and "fenster" not in desc_lower:
            win_score = 6.2
            win_status = "Ältere Isolierglasfenster"
            win_findings = "Ältere Fenstergeneration. Beschläge und Dichtungen sollten mittelfristig gewartet werden."
            win_cost = BENCHMARK_COSTS["window_single"] * 2
        else:
            win_score = 8.0
            win_status = "Gute Doppelverglasung"
            win_findings = "Intakte Isolierfenster mit Rollläden vorhanden. Kein akuter Handlungsbedarf."
            win_cost = 0.0

        # Category 5: Heating & Electrics
        if is_kernsaniert:
            heat_score = 9.5
            heat_status = "Moderne Installation"
            heat_findings = "Moderne Flachheizkörper, Unterverteilung mit FI-Schutzschalter und zeitgemäßes Schalterprogramm."
            heat_cost = 0.0
        elif is_renovierungsbeduerftig or year_built < 1975:
            heat_score = 5.5
            heat_status = "Teilmodernisierung ratsam"
            heat_findings = "Ältere Rippenheizkörper oder klassische 2-Ader-Elektrik. E-Check und FI-Nachrüstung empfohlen."
            heat_cost = BENCHMARK_COSTS["electric_update"]
        else:
            heat_score = 7.7
            heat_status = "Gepflegte Plattenheizkörper"
            heat_findings = "Plattenheizkörper mit Thermostatventilen. Schalterprogramme intakt."
            heat_cost = 0.0

        # Category 6: Walls & Building Substance
        if is_kernsaniert:
            wall_score = 9.6
            wall_status = "Glatt verputzt & weiß gestrichen"
            wall_findings = "Frisch gespachtelte, glatt verputzte Wände im Neuzustand. Keine Risse oder Feuchtigkeitsspuren."
            wall_cost = 0.0
        elif is_renovierungsbeduerftig:
            wall_score = 4.5
            wall_status = "Maler- & Spachtelbedarf"
            wall_findings = "Wandflächen abgewohnt, Tapeten teilweise abgelöst. Umfassender Maler- und Spachtelaufwand erforderlich."
            wall_cost = round(living_space * BENCHMARK_COSTS["painting_sqm"], -1)
        else:
            wall_score = 7.8
            wall_status = "Ordentlicher Zustand"
            wall_findings = "Saubere Raufasertapeten, helle Farbgebung. Kosmetisches Streichen bei Mieterwechsel ausreichend."
            wall_cost = round(living_space * 12.0, -1)

        overall_visual_score = round((bad_score * 0.25 + kitchen_score * 0.15 + floor_score * 0.20 + win_score * 0.15 + heat_score * 0.10 + wall_score * 0.15), 1)

        if overall_visual_score >= 8.8:
            condition_rating = "SEHR_GEPFLEGT"
            condition_label = "🟢 Erstklassig / Frisch modernisiert"
        elif overall_visual_score >= 7.3:
            condition_rating = "GEPFLEGT"
            condition_label = "🟢 Gepflegter Zustand / Sofort vermietbar"
        elif overall_visual_score >= 5.5:
            condition_rating = "MODERNISIERUNGSBEDARF"
            condition_label = "🟡 Solide mit partiellem Modernisierungsbedarf"
        else:
            condition_rating = "RENOVIERUNGSBEDUERFTIG"
            condition_label = "🟠 Renovierungsbedürftig / Hoher Sanierungsaufwand"

    # Assemble CaPEx items derived from visual findings
    visual_capex_items = []
    def _fallback_provenance(trade_kw: List[str], label_noun: str) -> Tuple[str, List[int], str]:
        if not has_interior_photos:
            return (
                "BAUJAHR_MUTMASSUNG",
                [],
                f"⚠️ Baujahrs-/Exposé-Mutmaßung (Bj. {year_built}): Nur Grundrisse/Pläne vorhanden, keine Innenraumfotos."
            )
        matched = [
            p["index"] for p in analyzed_photos
            if not p.get("is_blueprint")
            and not p.get("is_ai_generated")
            and any(k in (p.get("tag", "") + " " + p.get("description", "")).lower() for k in trade_kw)
        ]
        if matched:
            refs = ", ".join(f"#{i}" for i in matched[:3])
            return ("FOTO_BELEG", matched[:3], f"📸 Beobachtung Foto {refs} / Objektbefund ({label_noun}).")
        return (
            "BAUJAHR_MUTMASSUNG",
            [],
            f"⚠️ Baujahrs-Mutmaßung (Bj. {year_built}): {label_noun} auf den verfügbaren Fotos nicht eindeutig abgebildet."
        )

    if bad_cost > 0:
        st, p_idx, ev = _fallback_provenance(["bad", "wc", "sanitär", "dusche"], "Badezimmer")
        visual_capex_items.append({
            "title": "Badezimmer-Modernisierung",
            "category": "Bad & Sanitär",
            "urgency": "Mittelfristig",
            "cost": bad_cost,
            "source_type": st,
            "photo_indices": p_idx,
            "evidence_source": ev,
            "description": bad_findings
        })
    if kitchen_cost > 0:
        st, p_idx, ev = _fallback_provenance(["küche", "ebk"], "Küche / EBK")
        visual_capex_items.append({
            "title": "Einbauküche (EBK) Neuanschaffung",
            "category": "Küche & EBK",
            "urgency": "Optional / Für Neuvermietung",
            "cost": kitchen_cost,
            "source_type": st,
            "photo_indices": p_idx,
            "evidence_source": ev,
            "description": kitchen_findings
        })
    if floor_cost > 0:
        st, p_idx, ev = _fallback_provenance(["wohn", "schlaf", "zimmer", "flur"], "Bodenbeläge")
        visual_capex_items.append({
            "title": f"Bodenbelagserneuerung ({living_space:.0f} m²)",
            "category": "Böden & Beläge",
            "urgency": "Sofort vor Einzug",
            "cost": floor_cost,
            "source_type": st,
            "photo_indices": p_idx,
            "evidence_source": ev,
            "description": floor_findings
        })
    if win_cost > 0:
        st, p_idx, ev = _fallback_provenance(["fenster", "wohn", "zimmer"], "Fensteranlagen")
        visual_capex_items.append({
            "title": "Fensterwartung & Dichtungen",
            "category": "Fenster & Dämmung",
            "urgency": "Mittelfristig",
            "cost": win_cost,
            "source_type": st,
            "photo_indices": p_idx,
            "evidence_source": ev,
            "description": win_findings
        })
    if heat_cost > 0:
        visual_capex_items.append({
            "title": "Elektrik-Überprüfung & FI-Nachrüstung",
            "category": "Elektrik & Heizung",
            "urgency": "Sicherheitsrelevant",
            "cost": heat_cost,
            "source_type": "BAUJAHR_MUTMASSUNG",
            "photo_indices": [],
            "evidence_source": f"⚠️ Baujahrs-Mutmaßung (Bj. {year_built}): Kein Sicherungskasten/Unterverteilung auf Fotos sichtbar (Sicherheitspuffer).",
            "description": heat_findings
        })
    if wall_cost > 0:
        st, p_idx, ev = _fallback_provenance(["wohn", "schlaf", "zimmer", "flur"], "Wandflächen")
        visual_capex_items.append({
            "title": f"Maler- & Anstricharbeiten ({living_space:.0f} m²)",
            "category": "Wände & Decken",
            "urgency": "Sofort vor Neuvermietung",
            "cost": wall_cost,
            "source_type": st,
            "photo_indices": p_idx,
            "evidence_source": ev,
            "description": wall_findings
        })

    total_visual_capex = sum(item["cost"] for item in visual_capex_items)

    # Detected findings bullet points
    detected_findings = [
        f"Lichteinfall & Bildqualität: {('Gute Raumausleuchtung' if avg_pil >= 7.0 else 'Normale Ausleuchtung')}.",
        f"Sanitär-Befund: {bad_findings}",
        f"Küchen-Befund: {kitchen_findings}",
        f"Bodenbeläge: {floor_findings}",
        f"Fensteranlagen: {win_findings}",
        f"Heizkörper & Installation: {heat_findings}"
    ]

    if not has_interior_photos and is_renovierungsbeduerftig:
        investor_summary = (
            f"Die Bildanalyse hat {len(analyzed_photos)} Grundriss- und Bauzeichnungen identifiziert (keine Innenraum-Fotos im Inserat). "
            f"Aufgrund des im Exposé genannten Sanierungsbedarfs wird der Renovierungsaufwand auf ca. {total_visual_capex:,.2f} € geschätzt. "
            f"Dieser Betrag sollte vollumfänglich in die Kaufpreisverhandlung eingebracht werden."
        )
    elif not has_interior_photos:
        investor_summary = (
            f"Die Bildanalyse hat {len(analyzed_photos)} Grundriss- und Bauzeichnungen identifiziert (keine Innenraum-Fotos im Inserat). "
            f"Auf Basis der Exposé- und Baujahrsdaten wird ein CaPEx-Puffer von ca. {total_visual_capex:,.2f} € kalkuliert."
        )
    else:
        investor_summary = (
            f"Die KI-gestützte Bild- und Bauteilanalyse bewertet den visuellen IST-Zustand des Objekts mit {overall_visual_score:.1f} von 10 Punkten ({condition_label}). "
            f"Aus den analysierten Fotos ergibt sich ein realistischer Renovierungs- und Modernisierungsaufwand von insgesamt ca. {total_visual_capex:,.2f} €. "
            f"{'Das Objekt kann ohne größere Vorab-Investitionen sofort neu vermietet werden.' if total_visual_capex < 3000.0 else f'Der Sanierungsbedarf von {total_visual_capex:,.2f} € sollte in den Kaufpreisverhandlungen direkt als Preisnachlass geltend gemacht werden.'}"
        )

    sanitize_image_report_tags({"analyzed_photos": analyzed_photos})

    return {
        "timestamp": get_berlin_now_str(),
        "property_id": property_data.get("id"),
        "ai_provider": "BKI Bausachverständigen-Engine (Computer Vision)",
        "token_usage": {
            "prompt_tokens": total_prompt_tokens,
            "candidates_tokens": total_candidates_tokens,
            "total_tokens": total_tokens
        },
        "photos_analyzed_count": len(analyzed_photos),
        "has_interior_photos": has_interior_photos,
        "blueprints_count": blueprints_count,
        "notice": notice,
        "overall_visual_score": overall_visual_score,
        "condition_rating": condition_rating,
        "condition_label": condition_label,
        "categories": {
            "bathroom": {"score": bad_score, "status": bad_status, "findings": bad_findings, "cost": bad_cost},
            "kitchen": {"score": kitchen_score, "status": kitchen_status, "findings": kitchen_findings, "cost": kitchen_cost},
            "flooring": {"score": floor_score, "status": floor_status, "findings": floor_findings, "cost": floor_cost},
            "windows": {"score": win_score, "status": win_status, "findings": win_findings, "cost": win_cost},
            "heating_electrics": {"score": heat_score, "status": heat_status, "findings": heat_findings, "cost": heat_cost},
            "walls_substance": {"score": wall_score, "status": wall_status, "findings": wall_findings, "cost": wall_cost}
        },
        "analyzed_photos": analyzed_photos,
        "detected_findings": detected_findings,
        "visual_capex_items": visual_capex_items,
        "total_visual_capex": total_visual_capex,
        "vision_mode": "ALL_PHOTOS",
        "vision_skipped": False,
        "investor_summary": investor_summary
    }
