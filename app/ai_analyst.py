"""
KI-gestützter Real Estate Due-Diligence & Investment-Analyst für ImmoMetrica.
Nutzt primär Google Gemini / OpenAI LLM für echtes semantisches Textverständnis
von Objektbeschreibungen (inkl. Paket-Deals, Multi-Einheiten-Summierung, Hausgeld,
Mieten, WEG-Risiken, Negationen & Korrektur fehlerhafter Portalfelder), sowie eine
gehärtete deterministische deutsche Immobilien-NLP-Engine als Offline-Fallback.
"""

import re
import os
import json
import logging
import requests
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime
from app.calculator import calculate_cashflow
from app.valuation import evaluate_property_valuation, get_city_benchmark, get_mietspiegel_size_benchmark
from app.geo_resolver import resolve_location_and_tax
from app.location_analyst import analyze_property_location_and_pois, check_address_is_exact
from app.database import get_system_setting

logger = logging.getLogger("immometrica.ai_analyst")


def fmt_de(value: Any, decimals: int = 2) -> str:
    """Formatiert Zahlen strikt nach deutschem Standard (Tausenderpunkt, Dezimalkomma)."""
    try:
        num = float(value or 0.0)
        s = f"{num:,.{decimals}f}"
        return s.replace(",", "__TMP__").replace(".", ",").replace("__TMP__", ".")
    except Exception:
        return str(value)


def fmt_signed_de(value: Any, decimals: int = 2) -> str:
    """Formatiert Zahlen mit explizitem Vorzeichen (+ oder -) nach deutschem Standard (verhindert '+-')."""
    try:
        num = float(value or 0.0)
        formatted = fmt_de(num, decimals)
        return f"+{formatted}" if num > 0 else formatted
    except Exception:
        return str(value)


def resolve_llm_keys(explicit_api_key: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Ermittelt konfigurierte Google Gemini / OneProvider / OpenAI API Keys aus Request, SQLite Settings oder Environment."""
    if explicit_api_key and explicit_api_key.strip() and "..." not in explicit_api_key and "***" not in explicit_api_key:
        gemini_key = explicit_api_key.strip()
    else:
        active_prov = get_system_setting("active_ai_provider")
        if active_prov == "gemini":
            gemini_key = (
                get_system_setting("google_gemini_api_key")
                or get_system_setting("gemini_api_key")
                or os.getenv("GEMINI_API_KEY")
            )
        elif active_prov == "oneprovider":
            gemini_key = (
                get_system_setting("oneprovider_api_key")
                or get_system_setting("gemini_api_key")
                or os.getenv("ONEPROVIDER_API_KEY")
                or os.getenv("GEMINI_API_KEY")
            )
        else:
            gemini_key = (
                get_system_setting("gemini_api_key")
                or get_system_setting("oneprovider_api_key")
                or get_system_setting("google_gemini_api_key")
                or os.getenv("ONEPROVIDER_API_KEY")
                or os.getenv("GEMINI_API_KEY")
            )
    if gemini_key:
        gemini_key = gemini_key.strip() or None
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        openai_key = openai_key.strip() or None
    return gemini_key, openai_key


# ==============================================================================
# 1. DETERMINISTISCHE DEUTSCHE IMMOBILIEN-NLP (FALLBACK & VALIDIERUNG)
# ==============================================================================

def _clean_german_number(s: str) -> Optional[float]:
    if not s:
        return None
    cleaned = s.strip().rstrip(".-,")
    if "." in cleaned and "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    elif "." in cleaned:
        parts = cleaned.split(".")
        if len(parts) == 2 and len(parts[1]) == 3:
            cleaned = parts[0] + parts[1]
        elif len(parts) > 2:
            cleaned = "".join(parts)
    try:
        return float(cleaned)
    except ValueError:
        return None


def derive_energy_class_from_kwh(kwh: Union[float, int, str]) -> str:
    """
    Ermittelt die offizielle Energieeffizienzklasse gemäß Gebäudeenergiegesetz (GEG § 85 / Anlage 10)
    bzw. EnEV-Standard für Wohngebäude anhand des Endenergiebedarfs / -verbrauchs:
      A+:  < 30 kWh/(m²·a)
      A:   30 bis < 50 kWh/(m²·a)
      B:   50 bis < 75 kWh/(m²·a)
      C:   75 bis < 100 kWh/(m²·a)   (z. B. 81,8 kWh/m²a -> C)
      D:  100 bis < 130 kWh/(m²·a)
      E:  130 bis < 160 kWh/(m²·a)
      F:  160 bis < 200 kWh/(m²·a)
      G:  200 bis < 250 kWh/(m²·a)
      H:  >= 250 kWh/(m²·a)
    """
    try:
        val = float(str(kwh).replace(",", ".").strip())
    except (ValueError, TypeError):
        return "C"

    if val < 30.0:
        return "A+"
    elif val < 50.0:
        return "A"
    elif val < 75.0:
        return "B"
    elif val < 100.0:
        return "C"
    elif val < 130.0:
        return "D"
    elif val < 160.0:
        return "E"
    elif val < 200.0:
        return "F"
    elif val < 250.0:
        return "G"
    else:
        return "H"


def estimate_energy_class_from_year_built(year_built: Optional[int]) -> str:
    """Schätzt die Energieeffizienzklasse anhand des Gebäude-Baujahrs, falls kein Ausweis/kWh-Wert vorliegt."""
    if not year_built or year_built <= 0:
        return "Nicht angegeben"
    if year_built >= 2016:
        return "A (Geschätzt nach Baujahr)"
    elif year_built >= 2002:
        return "B (Geschätzt nach Baujahr)"
    elif year_built >= 1995:
        return "C (Geschätzt nach Baujahr)"
    elif year_built >= 1984:
        return "D (Geschätzt nach Baujahr)"
    elif year_built >= 1978:
        return "E (Geschätzt nach Baujahr)"
    elif year_built >= 1958:
        return "F (Geschätzt nach Baujahr)"
    elif year_built >= 1919:
        return "G (Geschätzt nach Baujahr)"
    else:
        return "H (Geschätzt nach Baujahr)"


def extract_listing_insights(text: str, details: Dict[str, Any], property_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Semantische Fallback-Analyse der vollständigen Beschreibung und Portal-Attribute
    (falls kein LLM API-Key aktiv ist oder als Validierungsbasis).
    Unterstützt Paket-Deals (Summe mehrerer Wohnungen), deutsches Zahlenformat inkl. Cent-Beträge,
    Negationen (z.B. 'keine Makleranfragen') und strikt Euro-geprüftes Hausgeld.
    """
    raw_text = text or ""
    text_lower = raw_text.lower()
    details_str = " ".join(f"{k}: {v}" for k, v in (details or {}).items()).lower()
    full_corpus = f"{text_lower} {details_str}".strip()
    title_lower = (property_data.get("title") or "").lower()
    combined_text = f"{title_lower} {full_corpus}".strip()

    # --------------------------------------------------------------------------
    # A. PRIVATVERKAUF & PROVISIONSFREIHEIT (Inkl. Makler-Negationsanalyse)
    # --------------------------------------------------------------------------
    private_patterns = [
        r'makler\s*anfragen\s*(?:nicht\s*erwünscht|unerwünscht|verboten|zwecklos|unterlassen)',
        r'keine\s*makler(?:anfragen)?',
        r'bitte\s*keine\s*makler',
        r'anfragen\s*von\s*maklern\s*(?:nicht\s*erwünscht|unerwünscht|zwecklos)',
        r'ohne\s*makler',
        r'ohne-makler',
        r'privatverkauf',
        r'von\s*privat',
        r'direkt\s*vom\s*eigentümer',
        r'direkt\s*von\s*privat',
        r'aus\s*privater\s*hand',
        r'verkauf\s*durch\s*eigentümer',
        r'provisionsfrei',
        r'provisions\s*frei',
        r'keine\s*(?:zusätzliche\s*)?(?:käufer)?provision',
        r'ohne\s*(?:käufer)?provision',
        r'es\s*fällt\s*keine\s*makler(?:-|/|\s*bzw\.\s*)?provision\s*an',
        r'keine\s*maklercourtage',
        r'ohne\s*courtage',
        r'käuferprovision[:\s]+0\b',
        r'nach\s*§\s*7\s*uwg\s*sind\s*unaufgeforderte\s*kontaktaufnahmen\s*durch\s*makler'
    ]

    is_private = False
    commission_free = False
    commission_percent = 0.0
    broker_negation_matched = False

    for pat in private_patterns:
        if re.search(pat, combined_text):
            is_private = True
            commission_free = True
            commission_percent = 0.0
            broker_negation_matched = True
            break

    source_portal = (property_data.get("source_portal") or "").lower()
    portal_url = (property_data.get("portal_url") or "").lower()
    if "ohne-makler" in source_portal or "ohne-makler.net" in portal_url:
        is_private = True
        commission_free = True
        commission_percent = 0.0

    if not commission_free:
        comm_match = (
            re.search(r'käuferprovision[:\s]+(\d+[\.,]?\d*)\s*%', combined_text) or
            re.search(r'maklerprovision[:\s]+(\d+[\.,]?\d*)\s*%', combined_text) or
            re.search(r'maklercourtage[:\s]+(\d+[\.,]?\d*)\s*%', combined_text) or
            re.search(r'courtage[:\s]+(\d+[\.,]?\d*)\s*%', combined_text) or
            re.search(r'provision[:\s]+(\d+[\.,]?\d*)\s*%', combined_text)
        )
        if comm_match:
            try:
                commission_percent = float(comm_match.group(1).replace(",", "."))
                is_private = False
                commission_free = (commission_percent == 0.0)
            except ValueError:
                commission_percent = float(property_data.get("commission_percent", 3.57))
        else:
            commission_percent = float(property_data.get("commission_percent", 3.57))
            commission_free = (commission_percent == 0.0)
            is_private = bool(property_data.get("is_private", 0))

    # --------------------------------------------------------------------------
    # B. REALE KALTMIEtE (Inkl. Gesamt-Summen bei Paket-Deals)
    # --------------------------------------------------------------------------
    extracted_rent = None
    rent_source = "BENCHMARK_ESTIMATE"
    stated_yield = None
    num_pat = r"(\d+(?:\.\d{3})*(?:,\d{1,2})?|\d+[\.,-]*)"

    is_package_deal = any(k in combined_text for k in [
        "wohnungspaket", "immobilienpaket", "zwei wohnungen", "2 wohnungen",
        "mehrere wohneinheiten", "zwei wohneinheiten", "2 vermietete wohneinheiten"
    ])

    # 0. Priority: Explicit package / total cold rent patterns
    total_rent_patterns = [
        (rf'(?:monatliche\s+)?kaltmiete\s+gesamt[^\d\n\r]{{0,15}}{num_pat}\s*(?:€|euro)', "EXPOSE_STATED_COLD"),
        (rf'gesamt(?:e|en)?\s+(?:aktuelle\s+)?(?:monatliche\s+)?(?:kalt)?miete(?:innahmen)?[^\d\n\r]{{0,20}}{num_pat}\s*(?:€|euro)', "EXPOSE_STATED_RENT"),
        (rf'insgesamt\s+belaufen\s+sich\s+die\s+aktuellen\s+mieteinnahmen\s+auf[^\d\n\r]{{0,15}}{num_pat}\s*(?:€|euro)', "EXPOSE_STATED_RENT"),
        (rf'(?:aktuelle\s+)?mieteinnahmen\s+(?:gesamt|insgesamt)[^\d\n\r]{{0,15}}{num_pat}\s*(?:€|euro)', "EXPOSE_STATED_RENT"),
    ]
    for pat, src_tag in total_rent_patterns:
        m = re.search(pat, combined_text)
        if m:
            val = _clean_german_number(m.group(1))
            if val and 120.0 <= val <= 50000.0:
                extracted_rent = val
                rent_source = src_tag
                break

    if not extracted_rent:
        total_annual_pats = [
            rf'jahresnettokaltmiete\s+gesamt[^\d\n\r]{{0,15}}{num_pat}\s*(?:€|euro)',
            rf'jahreskaltmiete\s+gesamt[^\d\n\r]{{0,15}}{num_pat}\s*(?:€|euro)',
            rf'gesamt(?:e|en)?\s+jahres(?:netto)?kaltmiete[^\d\n\r]{{0,15}}{num_pat}\s*(?:€|euro)',
        ]
        for pat in total_annual_pats:
            m = re.search(pat, combined_text)
            if m:
                val_a = _clean_german_number(m.group(1))
                if val_a and 1500.0 <= val_a <= 500000.0:
                    extracted_rent = round(val_a / 12.0, 2)
                    rent_source = "EXPOSE_STATED_ANNUAL"
                    break

    # 1. Warmmiete mit Nebenkosten-Aufschlüsselung
    if not extracted_rent:
        warm_nk_patterns = [
            rf'zu\s*{num_pat}\s*(?:€|euro)?\s*inkl\.?\s*{num_pat}\s*(?:€|euro)?\s*(?:nebenkosten|betriebskosten|nk)[^\n\r]*vermietet',
            rf'vermietet\s*(?:zu|für)\s*{num_pat}\s*(?:€|euro)?\s*inkl\.?\s*{num_pat}\s*(?:€|euro)?\s*(?:nebenkosten|betriebskosten|nk)',
            rf'{num_pat}\s*(?:€|euro)?\s*(?:warm|brutto)\s*(?:inkl\.?|davon)\s*{num_pat}\s*(?:€|euro)?\s*(?:nebenkosten|betriebskosten|nk)',
            rf'(?:warmmiete|bruttomiete|pauschalmiete)[^\d\n\r]*{num_pat}\s*(?:€|euro)?[^\n\r]*(?:nebenkosten|betriebskosten|nk)[^\d\n\r]*{num_pat}\s*(?:€|euro)?',
            rf'{num_pat}\s*(?:€|euro)?\s*inkl\.?\s*{num_pat}\s*(?:€|euro)?\s*(?:nebenkosten|betriebskosten|nk)'
        ]
        for w_pat in warm_nk_patterns:
            w_match = re.search(w_pat, combined_text)
            if w_match:
                warm_val = _clean_german_number(w_match.group(1))
                nk_val = _clean_german_number(w_match.group(2))
                if warm_val and nk_val and warm_val > nk_val:
                    derived_cold = round(warm_val - nk_val, 2)
                    if 120.0 <= derived_cold <= 20000.0:
                        extracted_rent = derived_cold
                        rent_source = "EXPOSE_STATED_COLD"
                        break

    # 2. Direkte Kaltmieten-Patterns (mit Fix für ",00 €" Cent-Nachkommastellen)
    if not extracted_rent:
        rent_patterns = [
            (rf'{num_pat}\s*(?:€|euro)?\s*kalt(?:\s*miet)?', "EXPOSE_STATED_COLD"),
            (rf'(?:aktuelle\s+|monatliche\s+)?kaltmiete[^\d\n\r]{{0,25}}{num_pat}\s*(?:€|euro)', "EXPOSE_STATED_RENT"),
            (rf'vermietet\s*für\s*(?:monatlich\s*)?{num_pat}\s*(?:€|euro)', "EXPOSE_STATED_RENT"),
            (rf'monatlich\s*{num_pat}\s*(?:€|euro)\s*vermietet', "EXPOSE_STATED_RENT"),
            (rf'aktuell\s*vermietet\s*für\s*{num_pat}\s*(?:€|euro)', "EXPOSE_STATED_RENT"),
            (rf'ist-kaltmiete[^\d\n\r]{{0,20}}{num_pat}\s*(?:€|euro)', "EXPOSE_STATED_RENT"),
            (rf'nettokaltmiete[^\d\n\r]{{0,20}}{num_pat}\s*(?:€|euro)', "EXPOSE_STATED_RENT"),
            (rf'mieteinnahmen\s*(?:monatlich|im\s*monat|pro\s*monat)[^\d\n\r]{{0,20}}{num_pat}\s*(?:€|euro)', "EXPOSE_STATED_RENT"),
            (rf'vermietet\s*(?:zu|an[^\n\r]+zu)\s*{num_pat}\s*(?:€|euro)?\s*(?:kalt|netto)?', "EXPOSE_STATED_RENT")
        ]

        for pat, src_tag in rent_patterns:
            match = re.search(pat, combined_text)
            if match:
                val = _clean_german_number(match.group(1))
                if val and 120.0 <= val <= 20000.0:
                    extracted_rent = val
                    rent_source = src_tag
                    break

    # 3. Check for annual cold rent (Jahreskaltmiete)
    if not extracted_rent:
        annual_patterns = [
            rf'jahreskaltmiete[^\d\n\r]{{0,20}}{num_pat}\s*(?:€|euro)',
            rf'jahresnettokaltmiete[^\d\n\r]{{0,20}}{num_pat}\s*(?:€|euro)',
            rf'mieteinnahmen\s*(?:p\.a\.|pro\s*jahr|jährlich)[^\d\n\r]{{0,20}}{num_pat}\s*(?:€|euro)'
        ]
        for pat in annual_patterns:
            a_match = re.search(pat, combined_text)
            if a_match:
                val_annual = _clean_german_number(a_match.group(1))
                if val_annual and 1500.0 <= val_annual <= 300000.0:
                    extracted_rent = round(val_annual / 12.0, 2)
                    rent_source = "EXPOSE_STATED_ANNUAL"
                    break

    # 4. Check for explicitly stated gross yield
    yield_patterns = [
        r'(?:bruttorendite|brutto-rendite)[^\d\n\r]{0,15}(\d+[\.,]?\d*)\s*%',
        r'rendite[^\d\n\r]{0,10}von[^\d\n\r]{0,10}(\d+[\.,]?\d*)\s*%',
        r'(\d+[\.,]?\d*)\s*%\s*(?:bruttorendite|rendite)'
    ]
    for y_pat in yield_patterns:
        ym = re.search(y_pat, combined_text)
        if ym:
            try:
                y_val = float(ym.group(1).replace(",", "."))
                if 1.5 <= y_val <= 25.0:
                    stated_yield = y_val
                    break
            except ValueError:
                pass

    purchase_price = float(property_data.get("purchase_price", 0.0))
    if not extracted_rent and stated_yield and purchase_price > 0:
        extracted_rent = round((purchase_price * (stated_yield / 100.0)) / 12.0, 2)
        rent_source = "DERIVED_FROM_STATED_YIELD"

    # Kaufpreis-Extraktion aus Fließtext (zur Diskrepanz-Erkennung)
    stated_purchase_price_in_text = None
    kp_matches = re.finditer(r"(?:kaufpreis|angebotspreis|gesamtpreis|festpreis)\s*[:=]?\s*([\d\.]+)\s*(?:€|euro)", combined_text)
    for m in kp_matches:
        raw_val = m.group(1).replace(".", "")
        try:
            val = float(raw_val)
            if 20000.0 <= val <= 30000000.0:
                stated_purchase_price_in_text = val
                break
        except ValueError:
            pass

    # Vermietungsstatus
    is_rented = False
    has_rented_phrase = any(w in combined_text for w in [
        "vermietete", "vermietet an", "ist vermietet", "seit 0", "seit 1", "seit 2",
        "ungekündigt vermietet", "langjährig vermietet", "solide vermietet", "fest vermietet"
    ]) or "vermietet" in title_lower

    if has_rented_phrase and "nicht vermietet" not in combined_text:
        is_rented = True
    elif "vermietet" in combined_text and "nicht vermietet" not in combined_text and not any(w in combined_text for w in ["unvermietet", "leerstand", "bezugsfrei"]):
        is_rented = True
    elif extracted_rent is not None or stated_yield is not None:
        is_rented = True
    elif property_data.get("is_rented", 0) == 1:
        is_rented = True

    is_vacant = any(w in combined_text for w in ["bezugsfrei", "leerstand", "sofort frei", "frei werdend", "unvermietet"])
    if (extracted_rent is not None or has_rented_phrase) and "vermietet" in combined_text:
        is_vacant = False
        is_rented = True
    elif is_vacant and extracted_rent is None:
        is_rented = False

    # --------------------------------------------------------------------------
    # C. ENERGIEAUSWEIS & GEG-CHECK
    # --------------------------------------------------------------------------
    energy_cert = details.get("energy_certificate") if isinstance(details, dict) else None

    energy_class = None
    if energy_cert and energy_cert.get("efficiency_class"):
        energy_class = str(energy_cert["efficiency_class"]).upper()
    else:
        ec_match = re.search(r"energie(?:effizienz)?klasse[:\s]+([a-h]\+?|[a-h])\b", combined_text)
        if ec_match:
            energy_class = ec_match.group(1).upper()
        else:
            for c in ["A+", "A", "B", "C", "D", "E", "F", "G", "H"]:
                if f"klasse {c.lower()}" in combined_text or f"effizienzklasse: {c.lower()}" in combined_text:
                    energy_class = c
                    break

    energy_kwh = None
    if energy_cert and energy_cert.get("consumption_kwh") is not None:
        try:
            energy_kwh = float(energy_cert["consumption_kwh"])
        except (ValueError, TypeError):
            pass
    else:
        kwh_match = re.search(r"(\d+[\.,]?\d*)\s*kwh", combined_text)
        if kwh_match:
            try:
                energy_kwh = float(kwh_match.group(1).replace(",", "."))
            except ValueError:
                pass

    # Automatische gesetzliche GEG-Klassifizierung aus Endenergieverbrauch / Endenergiebedarf:
    if energy_kwh is not None:
        derived_c = derive_energy_class_from_kwh(energy_kwh)
        if not energy_class or "geschätzt" in str(energy_class).lower() or energy_class not in ["A+", "A", "B", "C", "D", "E", "F", "G", "H"]:
            energy_class = derived_c
        elif energy_class != derived_c and not (energy_cert and energy_cert.get("efficiency_class")):
            energy_class = derived_c

    # Falls weder Klasse noch kWh angegeben sind: Schätzung nach Baujahr
    if not energy_class:
        yb_match = re.search(r"baujahr[:\s]+(\d{4})\b", combined_text)
        yb = int(yb_match.group(1)) if yb_match else ((details.get("year_built") if isinstance(details, dict) else None) or property_data.get("year_built"))
        if yb:
            try:
                energy_class = estimate_energy_class_from_year_built(int(yb))
            except (ValueError, TypeError):
                energy_class = "Nicht angegeben"
        else:
            energy_class = "Nicht angegeben"

    cert_type = "Unbekannt"
    if energy_cert and energy_cert.get("certificate_type"):
        cert_type = f"{energy_cert['certificate_type']}"
        if energy_cert.get("consumption_label"):
            cert_type += f" ({energy_cert['consumption_label']})"
        elif energy_kwh:
            cert_type += f" ({energy_kwh:.1f} kWh/(m²·a))".replace(".", ",")
    elif "bedarfsausweis" in combined_text or "energiebedarfsausweis" in combined_text:
        cert_type = f"Bedarfsausweis ({energy_kwh:.1f} kWh/(m²·a))".replace(".", ",") if energy_kwh else "Bedarfsausweis (Aussagekräftig)"
    elif "verbrauchsausweis" in combined_text or "energieverbrauchsausweis" in combined_text:
        cert_type = f"Verbrauchsausweis ({energy_kwh:.1f} kWh/(m²·a))".replace(".", ",") if energy_kwh else "Verbrauchsausweis (Nutzerabhängig)"
    elif energy_kwh:
        cert_type = f"Verbrauchsausweis ({energy_kwh:.1f} kWh/(m²·a))".replace(".", ",")
    elif "erstellung eines energieausweises wurde beauftragt" in combined_text or "energieausweis liegt noch nicht vor" in combined_text:
        cert_type = "In Erstellung / Noch nicht vorliegend"

    heating_type = "Zentralheizung"
    if energy_cert and energy_cert.get("heating_type"):
        heating_type = energy_cert["heating_type"]
        if energy_cert.get("primary_energy_source") and energy_cert["primary_energy_source"] not in heating_type:
            heating_type += f" ({energy_cert['primary_energy_source']})"
    elif "etagenheizung" in combined_text or "gasetagenheizung" in combined_text or "gastherme" in combined_text:
        heating_type = "Gas-Etagenheizung"
    elif "fernwärme" in combined_text or "fernwaerme" in combined_text:
        heating_type = "Fernwärme"
    elif "wärmepumpe" in combined_text or "waermepumpe" in combined_text:
        heating_type = "Wärmepumpe"
    elif "nachtspeicher" in combined_text or "stromheizung" in combined_text:
        heating_type = "Nachtspeicheröfen (Kritisch/Teuer)"
    elif "ölheizung" in combined_text or "oelheizung" in combined_text:
        heating_type = "Ölheizung"
    elif "gas" in combined_text:
        heating_type = "Gas-Zentralheizung"

    # --------------------------------------------------------------------------
    # D. WEG, HAUSGELD (Euro-geprüft & Paketsummierung!) & RÜCKLAGEN
    # --------------------------------------------------------------------------
    no_reserve_found = any(phrase in combined_text for phrase in [
        "keine instandhaltungsrücklage", "keine instandhaltungsruecklage",
        "keine rücklagen", "keine ruecklagen",
        "keine instandhaltungsrücklagen gebildet", "derzeit keine instandhaltungsrücklagen",
        "0 € rücklage", "0,- € rücklage", "keine rücklage vorhanden"
    ])

    reserve_amount = 0.0
    res_match = re.search(r"rücklage[^\d\n\r]{0,20}(\d+(?:\.\d{3})*(?:,\d{1,2})?)\s*(?:€|euro)", combined_text)
    if res_match and not no_reserve_found:
        rv = _clean_german_number(res_match.group(1))
        if rv:
            reserve_amount = rv

    missing_protocols = any(phrase in combined_text for phrase in [
        "keine vollständigen hausgeldabrechnungen", "keine vollstaendigen hausgeldabrechnungen",
        "keine protokolle vorliegen", "keine abrechnungen vorliegen",
        "verwaltung neu geordnet", "verwaltung der wohnanlage neu geordnet",
        "keine protokolle"
    ])

    special_assessment = any(phrase in combined_text for phrase in [
        "sonderumlage beschlossen", "sonderumlage geplant", "sonderumlage fällig",
        "künftige sonderumlage", "sonderumlage zu erwarten"
    ])

    units_count = None
    units_match = re.search(r"(\d+)\s*(?:einheiten|parteien|wohneinheiten)", combined_text)
    if units_match:
        try:
            units_count = int(units_match.group(1))
        except ValueError:
            pass

    # Hausgeld (STRIKT nur Beträge vor € / Euro akzeptieren, NIEMALS m²-Flächen wie "70 m²"!)
    hausgeld = None
    # 1. Check multi-unit lines like "Hausgeld Wohnung 1: 500,00 €" + "Hausgeld Wohnung 2: 700,00 €"
    unit_hg_matches = re.findall(r"hausgeld\s*(?:wohnung|einheit|whg|etw)?\s*\d*[:\s]+(\d+(?:\.\d{3})*(?:,\d{1,2})?)\s*(?:€|euro)", combined_text)
    if len(unit_hg_matches) >= 2:
        vals = [_clean_german_number(x) for x in unit_hg_matches]
        vals = [v for v in vals if v and 20.0 <= v <= 10000.0]
        if vals:
            hausgeld = round(sum(vals), 2)
    else:
        # 2. Check full sentence containing "hausgeld"
        hg_sentence = re.search(r"[^.\n\r]*hausgeld[^.\n\r]+", combined_text)
        if hg_sentence:
            sent = hg_sentence.group(0)
            euro_amounts = re.findall(r"(\d+(?:\.\d{3})*(?:,\d{1,2})?)\s*(?:€|euro)", sent)
            parsed_hg = [_clean_german_number(x) for x in euro_amounts]
            parsed_hg = [v for v in parsed_hg if v and 20.0 <= v <= 10000.0]
            if parsed_hg:
                hausgeld = round(sum(parsed_hg), 2)

    # --------------------------------------------------------------------------
    # E. BAUSUBSTANZ & SANIERUNGSSTAND
    # --------------------------------------------------------------------------
    is_kernsaniert = any(w in combined_text for w in ["kernsaniert", "erstbezug nach sanierung", "vollsaniert", "luxussaniert"])
    is_renoviert = any(w in combined_text for w in ["renoviert", "modernisiert", "gepflegt", "bezugsfertig"]) and not is_kernsaniert
    needs_renovation = any(w in combined_text for w in [
        "renovierungsbedürftig", "sanierungsbedürftig", "sanierungsstau",
        "handwerkerobjekt", "für bastler", "ausbaufähig", "entkernt", "renovieren!!!!"
    ])

    windows_type = "Standard Doppelverglasung"
    if "3-fach" in combined_text or "dreifach" in combined_text:
        windows_type = "3-fach Isolierglas (Modern)"
    elif "einfachverglasung" in combined_text or "holzfenster einfach" in combined_text:
        windows_type = "Einfachverglasung (Sanierungsbedarf)"
    elif "isolierverglasung" in combined_text or "kunststofffenster" in combined_text:
        windows_type = "2-fach Isolierverglast"

    electrics_modern = is_kernsaniert or ("elektrik neu" in combined_text or "fi-schalter" in combined_text or "elektrounterverteilung" in combined_text)
    bath_modern = is_kernsaniert or ("bad neu" in combined_text or "badezimmer saniert" in combined_text or "bad erneuert" in combined_text)
    elevator = "aufzug" in combined_text or "fahrstuhl" in combined_text or "lift" in combined_text
    balcony = "balkon" in combined_text or "terrasse" in combined_text or "loggia" in combined_text

    is_erbbaurecht = "erbbaurecht" in combined_text or "erbbauzins" in combined_text
    is_denkmalschutz = "denkmalschutz" in combined_text or "denkmalgeschützt" in combined_text

    return {
        "is_package_deal": is_package_deal,
        "unit_count_in_offer": 2 if ("zwei wohnungen" in combined_text or "2 wohnungen" in combined_text or "2 vermietete wohneinheiten" in combined_text) else 1,
        "package_breakdown": None,
        "stated_purchase_price_in_text": stated_purchase_price_in_text,
        "is_private": is_private,
        "commission_free": commission_free,
        "commission_percent": commission_percent,
        "broker_negation_matched": broker_negation_matched,
        "extracted_rent": extracted_rent,
        "rent_source": rent_source,
        "stated_yield": stated_yield,
        "is_rented": is_rented,
        "is_vacant": is_vacant,
        "energy_class": energy_class or "Nicht angegeben",
        "energy_kwh": energy_kwh,
        "cert_type": cert_type,
        "heating_type": heating_type,
        "energy_certificate": energy_cert,
        "no_reserve_found": no_reserve_found,
        "reserve_amount": reserve_amount,
        "missing_protocols": missing_protocols,
        "special_assessment": special_assessment,
        "units_count": units_count or 12,
        "hausgeld": hausgeld if hausgeld is not None else 250.0,
        "has_explicit_hausgeld": (hausgeld is not None),
        "hausgeld_non_recoverable": round(hausgeld * 0.35, 2) if hausgeld is not None else None,
        "is_kernsaniert": is_kernsaniert,
        "is_renoviert": is_renoviert,
        "needs_renovation": needs_renovation,
        "windows_type": windows_type,
        "electrics_modern": electrics_modern,
        "bath_modern": bath_modern,
        "elevator": elevator,
        "balcony": balcony,
        "is_erbbaurecht": is_erbbaurecht,
        "is_denkmalschutz": is_denkmalschutz,
        "corrected_city": None,
        "corrected_address": None,
        "corrected_property_type": None,
        "year_built_extracted": None,
        "year_built_label": None,
        "land_area_extracted": None
    }


# ==============================================================================
# 2. ECHTE LLM-TEXTANALYSE (GOOGLE GEMINI / OPENAI API)
# ==============================================================================

def _build_llm_prompt(full_description: str, property_data: Dict[str, Any], details: Optional[Dict[str, Any]] = None) -> str:
    details_block = json.dumps(details, ensure_ascii=False) if details else "{}"
    return f"""Du bist ein institutioneller deutscher Immobilien-Gutachter und Due-Diligence-Analyst.
Analysiere die folgende Immobilie anhand aller Portal-Basisdaten und des vollständigen Original-Exposés mit höchster Präzision.

Portal-Basisdaten:
- Titel: {property_data.get('title')}
- Ort / PLZ: {property_data.get('postal_code', '')} {property_data.get('city', '')}
- Straße / Adresse (Portal): {property_data.get('address') or 'nicht angegeben'}
- Kaufpreis: {property_data.get('purchase_price')} €
- Wohnfläche (Portal): {property_data.get('living_space')} m²
- Objekttyp: {property_data.get('property_type', 'Eigentumswohnung')}
- Baujahr (bisher): {property_data.get('year_built')}
- Portal-Details (Attribute): {details_block}

Vollständige Original-Beschreibung des Exposés:
\"\"\"{full_description[:7000]}\"\"\"

STRENGE REGELN FÜR DIE EXTRAKTION:
1. PAKET-DEALS / MEHRERE WOHNEINHEITEN:
   - Wenn mehrere Wohnungen als Paket verkauft werden (z.B. Wohnung 1 + Wohnung 2), MUSST du zwingend:
     * `is_package_deal: true`, `unit_count_in_offer`: Anzahl der verkauften Wohnungen.
     * `current_cold_rent_monthly`: Die SUMME aller monatlichen Ist-Kaltmieten aller Einheiten (z.B. 350 + 680 = 1030.0)! Nimm NIEMALS nur die Miete der ersten Einheit!
     * `hausgeld_monthly_total`: Die SUMME aller monatlichen Hausgelder aller Einheiten (z.B. 500 + 700 = 1200.0)! Verwechsle NIEMALS Flächenangaben wie "70 m²" mit dem Hausgeld in Euro!
2. PRIVATVERKAUF & MAKLERPROVISION (NEGATIONEN BEACHTEN):
   - Steht "keine Makleranfragen", "Makler Anfragen nicht erwünscht", "Privatverkauf", "von privat", "ohne Makler" oder "provisionsfrei", setze `is_private: true`, `commission_free: true`, `commission_percent: 0.0`.
   - Steht eine Käuferprovision (z.B. 3,57% oder 4,76%) oder vermittelt ein Makler ohne Provisionsfreiheit, übernimm diesen Prozentsatz und setze `commission_free: false`.
3. VERMIETUNGSSTATUS & IST-MIETE:
   - `is_rented: true` und `rent_is_verified_in_text: true`: NUR wenn aus dem Text klar hervorgeht, dass die Immobilie AKTUELL VERMIETET ist UND eine konkrete aktuelle Miete (Kaltmiete, Warmmiete minus Nebenkosten, Jahresnettokaltmiete / 12 oder explizit bezifferte Bruttorendite %) im Exposé steht.
   - Wenn "vermietet", aber KEINE Miete beziffert ist: `is_rented: true`, `rent_is_verified_in_text: false`, `current_cold_rent_monthly: null`.
   - Wenn unvermietet / bezugsfrei / leer / "für Eigennutzer" / nur theoretische Mietperspektive: `is_rented: false`, `is_vacant: true`, `rent_is_verified_in_text: false`, `current_cold_rent_monthly: null`.
   - `stated_gross_yield`: Gib eine Prozentzahl NUR an, wenn wörtlich eine Bruttorendite in % im Inseratstext steht (z.B. "6,56 % Bruttorendite"). Berechne sie NICHT selbst in diesem Feld (sonst `null`).
4. GRUNDSTÜCKSFLÄCHE (`land_area`):
   - Gib NUR dann eine Zahl > 0 an, wenn es sich um ein Haus / Mehrfamilienhaus / Grundstück handelt UND eine konkrete Grundstücksfläche in m² im Inserat steht.
   - Bei Eigentumswohnungen oder Wohnungspaketen ohne eigenen Grundstücksverkauf setze `land_area: null`.
5. WEG-RISIKEN (KEINE UNBELEGTEN UNTERSTELLUNGEN):
   - `no_reserve_found`: Setze `true` AUSSCHLIESSLICH dann, wenn im Text ausdrücklich steht, dass keine Instandhaltungsrücklagen existieren ("keine Rücklagen", "0 € Rücklage"). Wenn das Thema Rücklagen im Text gar nicht erwähnt wird, setze `false`.
   - `missing_protocols`: Setze `true` AUSSCHLIESSLICH dann, wenn fehlende Hausgeldabrechnungen/Protokolle oder eine ungeordnete Verwaltung explizit erwähnt sind. Wenn gar nicht erwähnt, setze `false`.
6. STADT, ADRESSE & BAUJAHR:
   - Extrahiere den echten Haupt-Stadtnamen (z.B. "Dortmund" statt nur Stadtteil "Körne"), die volle Straßenadresse falls genannt (z.B. "Düsterstraße 15, 44143 Dortmund"), sowie das früheste/primäre Baujahr als Zahl (`year_built`) und ggf. `year_built_label` (z.B. "1908 & 1956").
   - WICHTIG ZUR ADRESSE: Wenn in den Portal-Basisdaten unter 'Straße / Adresse (Portal)' bereits eine Adresse mit Hausnummer bekannt ist (oder aus dem Text hervorgeht), behalte diese Hausnummer ZWINGEND bei! Ersetze eine bereits verifizierte Adresse mit Hausnummer NIEMALS durch eine Adresse ohne Hausnummer in `corrected_address`, selbst wenn im Exposé-Fließtext nur der Straßenname ohne Hausnummer erwähnt wird.
7. DEUTSCHES ZAHLENFORMAT im Fließtext:
   - Verwende durchgehend Punkt als Tausendertrennzeichen und Komma als Dezimalzeichen (z.B. `210.000,00 €`, `1.030,00 €`, `5,89 %`).
8. EXECUTIVE SUMMARY & VERHANDLUNGSEMPFEHLUNG (STRIKT KEINE ERFUNDENEN ZAHLEN):
   - In `executive_summary` NIEMALS eigenständig Verhandlungsempfehlungen, Zielangebote, Nachlässe oder CaPEx-Gesamtsummen hineinschreiben!
   - Die quantitative Berechnung von CaPEx, Zielangeboten und Nachlässen erfolgt STRIKT deterministisch durch das Rechenwerk des Backends.
   - Die `executive_summary` soll AUSSCHLIESSLICH die qualitative Due-Diligence zusammenfassen: Lagequalität, Bausubstanz, Sanierungsstand, Vermietungssituation und erforderliche Prüfschritte (z.B. Protokolle der Eigentümerversammlung, Jahresabrechnungen, Energieausweis). Schließe KEINE Verhandlungsempfehlung ab.
9. RISIKEN & K.O.-KRITERIEN (STRIKTE SEVERITY-REGELN):
   - Ein günstiger Kaufpreis, ein Preisniveau unter dem Marktdurchschnitt oder ein hoher Marktwert-Abschlag ist für einen Immobilien-Investor eine CHANCE, niemals ein kritisches K.O.-Risiko!
   - Setze `severity` für Preisabweichungen oder günstige Kaufpreise NIEMALS auf 'CRITICAL', sondern maximal auf 'HIGH' oder 'MEDIUM' als Due-Diligence-Prüfhinweis (z.B. 'Auffällig günstiger Kaufpreis – Substanz, Mietverträge und WEG vor Ort prüfen').
   - 'CRITICAL' darf AUSSCHLIESSLICH vergeben werden für echte juristische oder bauliche K.O.-Fallen: Erbpacht mit kurzer Restlaufzeit, akute Einsturzgefahr/Abrissverfügung, schwere Bergschäden oder nachweislich unbewohnbarer/gesperrter Zustand.

Antworte AUSSCHLIESSLICH mit gültigem JSON:
{{
  "is_package_deal": false,
  "unit_count_in_offer": 1,
  "package_breakdown": "Kurze Einheitenzusammenfassung oder null",
  "corrected_city": "Hauptstadt oder null",
  "district": "Stadtteil oder null",
  "corrected_address": "Straße Hausnummer, PLZ Stadt oder null",
  "corrected_property_type": "Wohnungspaket (2 ETW) / Eigentumswohnung / Mehrfamilienhaus / Einfamilienhaus / Grundstück oder null",
  "total_living_space": null,
  "stated_purchase_price_in_text": null,
  "land_area": null,
  "year_built": null,
  "year_built_label": null,
  "is_private": false,
  "commission_free": false,
  "commission_percent": 3.57,
  "is_rented": false,
  "is_vacant": false,
  "rent_is_verified_in_text": false,
  "current_cold_rent_monthly": null,
  "annual_cold_rent": null,
  "stated_gross_yield": null,
  "hausgeld_monthly_total": null,
  "hausgeld_non_recoverable_monthly": null,
  "reserve_amount": 0.0,
  "no_reserve_found": false,
  "missing_protocols": false,
  "special_assessment": false,
  "energy_class": null,
  "energy_kwh": null,
  "cert_type": "Unbekannt",
  "heating_type": "Zentralheizung",
  "windows_type": "Standard Doppelverglasung",
  "is_kernsaniert": false,
  "is_renoviert": false,
  "needs_renovation": false,
  "electrics_modern": false,
  "bath_modern": false,
  "elevator": false,
  "balcony": false,
  "is_erbbaurecht": false,
  "is_denkmalschutz": false,
  "capex_adjustments": [
    {{"title": "...", "amount": 5000.0, "urgency": "HOCH", "source_type": "EXPOSE_TEXT", "evidence_source": "📄 Laut Exposé-Text bzw. ⚠️ Baujahrs-Mutmaßung: Konkrete Herkunft...", "description": "..."}}
  ],
  "risk_highlights": [
    {{"title": "...", "severity": "CRITICAL", "category": "...", "badge": "...", "description": "...", "impact": "...", "action": "..."}}
  ],
  "executive_summary": "Prägnante 2-3 Absätze Gutachter-Zusammenfassung auf Deutsch (qualitative Einordnung und Prüfschritte, strikt OHNE erfundene Verhandlungsempfehlungen oder CaPEx-Zahlen)."
}}"""


def _clean_and_parse_llm_json(raw_text: str) -> Dict[str, Any]:
    """
    Robuster JSON-Parser für LLM-Ausgaben (Google Gemini, OpenAI, OneProvider/Claude/Qwen).
    Bereinigt Markdown-Codeblöcke (```json ... ```), führende/nachfolgende Freitexte,
    trailing commas und Token-Truncations am Textende.
    """
    cleaned = raw_text.strip()
    if "```" in cleaned:
        m = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned)
        if m:
            cleaned = m.group(1).strip()
        else:
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned)
            cleaned = re.sub(r'\s*```$', '', cleaned).strip()

    # 1. Direkter json.loads-Versuch
    try:
        return json.loads(cleaned)
    except Exception:
        pass

    # 2. Trailing commas vor } und ] entfernen
    cleaned_no_trailing = re.sub(r',\s*([\]}])', r'\1', cleaned)
    try:
        return json.loads(cleaned_no_trailing)
    except Exception:
        pass

    # 3. Truncation Auto-Repair (falls Ausgabe am Token-Limit abgeschnitten wurde)
    for base in [cleaned, cleaned_no_trailing]:
        for suffix in ['"}', '"]}', '"}}', '"}]}', '}\n}', '"]}\n}', '}', '"]']:
            try:
                return json.loads(base + suffix)
            except Exception:
                pass

    # 4. Äußerstes { ... } per Regex erfassen
    m_outer = re.search(r'(\{[\s\S]*\})', cleaned)
    if m_outer:
        cand = m_outer.group(1).strip()
        try:
            return json.loads(cand)
        except Exception:
            cand_no_trailing = re.sub(r',\s*([\]}])', r'\1', cand)
            try:
                return json.loads(cand_no_trailing)
            except Exception:
                for suffix in ['"}', '"]}', '"}}', '}\n}']:
                    try:
                        return json.loads(cand_no_trailing + suffix)
                    except Exception:
                        pass

    raise ValueError("Kein valides JSON in LLM-Antwort gefunden")


def call_llm_text_analysis(
    full_description: str,
    property_data: Dict[str, Any],
    details: Optional[Dict[str, Any]] = None,
    api_key: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Synchroner Aufruf der Google Gemini / OpenAI API für semantische Text-Due-Diligence.
    """
    gemini_key, openai_key = resolve_llm_keys(api_key)
    if not gemini_key and not openai_key:
        return None
    if not full_description or len(full_description.strip()) < 40:
        return None

    prompt = _build_llm_prompt(full_description, property_data, details)

    if gemini_key:
        if gemini_key.startswith("sk-"):
            # OneProvider Dev AI Text-Modelle (spezialisiert für präzise deutschsprachige Immobilien-Due-Diligence)
            oneprovider_models = ["claude-haiku-5-5", "gpt-6-luna", "deepseek-v4.1-flash"]
            for model in oneprovider_models:
                try:
                    url = "https://api.oneprovider.dev/v1/chat/completions"
                    req_payload = {
                        "model": model,
                        "messages": [
                            {
                                "role": "system",
                                "content": "Du bist ein erfahrener institutioneller Immobilien-Analyst und Bausachverständiger in Deutschland. Antworte ausschließlich mit einem validen JSON-Objekt ohne Markdown-Codeblöcke."
                            },
                            {
                                "role": "user",
                                "content": prompt
                            }
                        ],
                        "max_tokens": 8192
                    }
                    if model not in ["gpt-6-luna"] and not ("claude" in model.lower()):
                        req_payload["response_format"] = {"type": "json_object"}

                    resp = requests.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {gemini_key}",
                            "Content-Type": "application/json"
                        },
                        json=req_payload,
                        timeout=75.0
                    )
                    if resp.status_code == 200:
                        res_data = resp.json()
                        choices = res_data.get("choices", [])
                        if choices:
                            text_resp = choices[0].get("message", {}).get("content", "")
                            parsed = _clean_and_parse_llm_json(text_resp)
                            if isinstance(parsed, dict):
                                parsed["ai_provider"] = f"OneProvider {model} Text-KI (Live API-Key)"
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
                        logger.warning(f"OneProvider Text API status {resp.status_code} ({model}): {resp.text[:160]}")
                except Exception as e:
                    logger.warning(f"OneProvider Text API error ({model}): {e}")
        else:
            # Google Gemini Multimodal / Text AI (Günstige Flash-Lite-Modelle für den Regelbetrieb)
            models = ["gemini-flash-lite-latest", "gemini-3.5-flash-lite", "gemini-flash-latest"]
            for model in models:
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={gemini_key}"
                    resp = requests.post(
                        url,
                        json={
                            "contents": [{"parts": [{"text": prompt}]}],
                            "generationConfig": {
                                "response_mime_type": "application/json",
                                "temperature": 0.0
                            }
                        },
                        headers={"Content-Type": "application/json"},
                        timeout=15.0
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        text_resp = data["candidates"][0]["content"]["parts"][0]["text"]
                        parsed = _clean_and_parse_llm_json(text_resp)
                        if isinstance(parsed, dict):
                            parsed["ai_provider"] = f"Google {model} Text-KI (Live API-Key)"
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
                        logger.warning(f"Gemini Text API status {resp.status_code} ({model}): {resp.text[:160]}")
                except Exception as e:
                    logger.warning(f"Gemini Text API error ({model}): {e}")

    if openai_key:
        try:
            url = "https://api.openai.com/v1/chat/completions"
            resp = requests.post(
                url,
                headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                json={
                    "model": "gpt-4o-mini",
                    "temperature": 0.0,
                    "messages": [
                        {"role": "system", "content": "Du bist ein institutioneller deutscher Immobilien-Analyst. Antworte ausschließlich mit gültigem JSON."},
                        {"role": "user", "content": prompt}
                    ],
                    "response_format": {"type": "json_object"}
                },
                timeout=25.0
            )
            if resp.status_code == 200:
                res_data = resp.json()
                text_resp = res_data["choices"][0]["message"]["content"]
                parsed = json.loads(text_resp)
                if isinstance(parsed, dict):
                    parsed["ai_provider"] = "OpenAI gpt-4o-mini Text-KI (Live API-Key)"
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
        except Exception as e:
            logger.warning(f"OpenAI Text API error: {e}")

    return None


async def call_llm_if_available(
    full_description: str,
    property_data: Dict[str, Any],
    api_key: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Asynchroner Wrapper für Legacy-Aufrufer."""
    return call_llm_text_analysis(full_description, property_data, details=details, api_key=api_key)


def _merge_llm_into_insights(base_insights: Dict[str, Any], llm: Dict[str, Any], property_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Kombiniert semantische LLM-Ergebnisse mit deterministischen Sicherheitsregeln.
    """
    merged = dict(base_insights)

    # Package deal info
    if "is_package_deal" in llm:
        merged["is_package_deal"] = bool(llm["is_package_deal"])
    if llm.get("unit_count_in_offer"):
        try:
            merged["unit_count_in_offer"] = max(1, int(llm["unit_count_in_offer"]))
        except (ValueError, TypeError):
            pass
    if llm.get("package_breakdown"):
        merged["package_breakdown"] = str(llm["package_breakdown"])

    # Commission / Private status (protect explicit broker negation safety!)
    if base_insights.get("broker_negation_matched"):
        merged["is_private"] = True
        merged["commission_free"] = True
        merged["commission_percent"] = 0.0
    else:
        if "is_private" in llm:
            merged["is_private"] = bool(llm["is_private"])
        if "commission_free" in llm:
            merged["commission_free"] = bool(llm["commission_free"])
        if llm.get("commission_free"):
            merged["commission_percent"] = 0.0
        elif llm.get("commission_percent") is not None:
            try:
                merged["commission_percent"] = float(llm["commission_percent"])
            except (ValueError, TypeError):
                pass

    # Rent & Tenancy Status
    llm_rented = bool(llm.get("is_rented", False))
    llm_vacant = bool(llm.get("is_vacant", False))
    llm_verified = bool(llm.get("rent_is_verified_in_text", False))
    llm_rent = llm.get("current_cold_rent_monthly")
    llm_annual = llm.get("annual_cold_rent")
    llm_yield = llm.get("stated_gross_yield")

    parsed_rent = None
    if llm_rent is not None:
        try:
            r_val = float(llm_rent)
            if 100.0 <= r_val <= 50000.0:
                parsed_rent = round(r_val, 2)
        except (ValueError, TypeError):
            pass
    if not parsed_rent and llm_annual is not None:
        try:
            a_val = float(llm_annual)
            if 1200.0 <= a_val <= 600000.0:
                parsed_rent = round(a_val / 12.0, 2)
        except (ValueError, TypeError):
            pass

    if llm_yield is not None:
        try:
            y_val = float(llm_yield)
            if 1.5 <= y_val <= 25.0:
                merged["stated_yield"] = y_val
        except (ValueError, TypeError):
            pass

    if llm_rented and llm_verified and parsed_rent:
        merged["is_rented"] = True
        merged["is_vacant"] = False
        merged["extracted_rent"] = parsed_rent
        merged["rent_source"] = "EXPOSE_STATED_COLD"
    elif llm_rented and not llm_verified:
        if merged.get("stated_yield") and float(property_data.get("purchase_price", 0)) > 0:
            price = float(property_data["purchase_price"])
            merged["extracted_rent"] = round((price * (merged["stated_yield"] / 100.0)) / 12.0, 2)
            merged["rent_source"] = "DERIVED_FROM_STATED_YIELD"
            merged["is_rented"] = True
            merged["is_vacant"] = False
        else:
            merged["is_rented"] = True
            merged["is_vacant"] = False
            merged["extracted_rent"] = None
            merged["rent_source"] = "BENCHMARK_ESTIMATE"
    elif not llm_rented or llm_vacant:
        merged["is_rented"] = False
        merged["is_vacant"] = True
        merged["extracted_rent"] = None
        merged["rent_source"] = "BENCHMARK_ESTIMATE"

    # Hausgeld (LLM sums across package units and excludes sqm numbers!)
    hg_llm = llm.get("hausgeld_monthly_total")
    if hg_llm is not None:
        try:
            hg_val = float(hg_llm)
            if 10.0 <= hg_val <= 25000.0:
                merged["hausgeld"] = round(hg_val, 2)
                merged["has_explicit_hausgeld"] = True
        except (ValueError, TypeError):
            pass

    hg_non_rec = llm.get("hausgeld_non_recoverable_monthly")
    if hg_non_rec is not None:
        try:
            merged["hausgeld_non_recoverable"] = round(float(hg_non_rec), 2)
        except (ValueError, TypeError):
            pass
    elif merged.get("has_explicit_hausgeld") and merged.get("hausgeld"):
        merged["hausgeld_non_recoverable"] = round(float(merged["hausgeld"]) * 0.35, 2)

    # WEG Reserves & Risks
    if llm.get("reserve_amount") is not None:
        try:
            merged["reserve_amount"] = float(llm["reserve_amount"])
        except (ValueError, TypeError):
            pass
    merged["no_reserve_found"] = bool(llm.get("no_reserve_found", False)) or base_insights.get("no_reserve_found", False)
    merged["missing_protocols"] = bool(llm.get("missing_protocols", False)) or base_insights.get("missing_protocols", False)
    merged["special_assessment"] = bool(llm.get("special_assessment", False)) or base_insights.get("special_assessment", False)

    # Energy & Building condition
    if llm.get("energy_kwh") is not None:
        try:
            merged["energy_kwh"] = float(llm["energy_kwh"])
        except (ValueError, TypeError):
            pass
    if llm.get("energy_class"):
        c_str = str(llm["energy_class"]).upper().strip()
        if c_str in ["A+", "A", "B", "C", "D", "E", "F", "G", "H"]:
            merged["energy_class"] = c_str

    # Strict GEG consistency check: If energy_kwh is known, derive class deterministically
    if merged.get("energy_kwh") is not None:
        derived_c = derive_energy_class_from_kwh(merged["energy_kwh"])
        cur_cls = str(merged.get("energy_class") or "")
        if not cur_cls or "geschätzt" in cur_cls.lower() or cur_cls not in ["A+", "A", "B", "C", "D", "E", "F", "G", "H"]:
            merged["energy_class"] = derived_c
        elif cur_cls != derived_c and not (base_insights.get("energy_certificate") and base_insights["energy_certificate"].get("efficiency_class")):
            merged["energy_class"] = derived_c

    if llm.get("cert_type") and llm["cert_type"] != "Unbekannt":
        merged["cert_type"] = str(llm["cert_type"])
    if llm.get("heating_type") and llm["heating_type"] != "Unbekannt":
        merged["heating_type"] = str(llm["heating_type"])
    if llm.get("windows_type"):
        merged["windows_type"] = str(llm["windows_type"])
    if base_insights.get("energy_certificate"):
        merged["energy_certificate"] = base_insights["energy_certificate"]

    for b_field in [
        "is_kernsaniert", "is_renoviert", "needs_renovation",
        "electrics_modern", "bath_modern", "elevator", "balcony",
        "is_erbbaurecht", "is_denkmalschutz"
    ]:
        if b_field in llm:
            merged[b_field] = bool(llm[b_field])

    # Metadata corrections
    if llm.get("corrected_city"):
        merged["corrected_city"] = str(llm["corrected_city"]).strip()
    if llm.get("district"):
        merged["district"] = str(llm["district"]).strip()

    portal_addr = (property_data.get("address") or "").strip()
    portal_city = property_data.get("city") or merged.get("corrected_city")
    portal_exact, _ = check_address_is_exact(portal_addr, city=portal_city)

    if llm.get("corrected_address"):
        cand_addr = str(llm["corrected_address"]).strip()
        cand_exact, _ = check_address_is_exact(cand_addr, city=portal_city)
        if portal_exact and not cand_exact:
            merged["corrected_address"] = portal_addr
        else:
            merged["corrected_address"] = cand_addr
    elif portal_exact:
        merged["corrected_address"] = portal_addr

    if llm.get("corrected_property_type"):
        merged["corrected_property_type"] = str(llm["corrected_property_type"]).strip()
    if llm.get("year_built"):
        try:
            yb = int(llm["year_built"])
            if 1700 <= yb <= 2030:
                merged["year_built_extracted"] = yb
        except (ValueError, TypeError):
            pass
    if llm.get("year_built_label"):
        merged["year_built_label"] = str(llm["year_built_label"]).strip()
    if llm.get("land_area") is not None:
        try:
            la = float(llm["land_area"])
            if la > 0:
                merged["land_area_extracted"] = round(la, 2)
        except (ValueError, TypeError):
            pass

    # Stated Purchase Price in Exposé Text
    txt_price = llm.get("stated_purchase_price_in_text") or base_insights.get("stated_purchase_price_in_text")
    if txt_price is not None:
        try:
            val_p = float(txt_price)
            if val_p >= 20000.0:
                merged["stated_purchase_price_in_text"] = val_p
        except (ValueError, TypeError):
            pass

    merged["ai_provider"] = llm.get("ai_provider", "Google Gemini Text-KI")
    if "token_usage" in llm:
        merged["token_usage"] = llm["token_usage"]
    merged["llm_executive_summary"] = llm.get("executive_summary")
    merged["llm_risk_highlights"] = llm.get("risk_highlights") or []
    merged["llm_capex_adjustments"] = llm.get("capex_adjustments") or []

    return merged


# ==============================================================================
# 3. CAPEX & SANIERUNGSKOSTEN-KALKULATION
# ==============================================================================

def _find_visual_evidence(
    image_analysis: Optional[Dict[str, Any]],
    item_keywords: List[str],
    cat_key: Optional[str] = None,
    photo_tag_keywords: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Extrahiert Bildbelege und Kosten zu einem Gewerk aus einer vorhandenen KI-Bildanalyse."""
    if not isinstance(image_analysis, dict):
        return {"has_analysis": False, "has_interior": False}

    has_interior = bool(image_analysis.get("has_interior_photos"))
    analyzed_photos = image_analysis.get("analyzed_photos") or []
    vis_cats = image_analysis.get("categories") or {}
    vis_items = image_analysis.get("visual_capex_items") or []

    matched_item = None
    for vi in vis_items:
        if not isinstance(vi, dict):
            continue
        title_txt = str(vi.get("title", "")).lower()
        if any(kw in title_txt for kw in item_keywords):
            matched_item = vi
            break
    if not matched_item:
        for vi in vis_items:
            if not isinstance(vi, dict):
                continue
            txt = f"{vi.get('category', '')} {vi.get('description', '')}".lower()
            if "holzpaneel" in str(vi.get("title", "")).lower():
                continue
            if any(kw in txt for kw in item_keywords):
                matched_item = vi
                break

    cat_obj = vis_cats.get(cat_key) if cat_key else None
    photo_indices: List[int] = []
    if matched_item and matched_item.get("photo_indices"):
        photo_indices = [int(p) for p in matched_item["photo_indices"] if str(p).isdigit()]
    elif has_interior and photo_tag_keywords:
        for p in analyzed_photos:
            if p.get("is_blueprint"):
                continue
            ptxt = f"{p.get('tag', '')} {p.get('description', '')} {' '.join(p.get('findings') or [])}".lower()
            if any(tk in ptxt for tk in photo_tag_keywords):
                photo_indices.append(int(p.get("index", 0)))
        photo_indices = [i for i in photo_indices if i > 0][:3]

    return {
        "has_analysis": True,
        "has_interior": has_interior,
        "photo_count": len(analyzed_photos),
        "matched_item": matched_item,
        "cat_obj": cat_obj,
        "photo_indices": photo_indices
    }


def estimate_capex_and_hidden_costs(
    insights: Dict[str, Any],
    property_data: Dict[str, Any],
    image_analysis: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Kalkuliert realistische CaPEx-Investitionskosten mit expliziter Quellen- & Bildbeleg-Angabe
    (FOTO_BELEG vs. EXPOSE_TEXT vs. BAUJAHR_MUTMASSUNG) und gleicht sie gegen Bildbefunde ab.
    """
    sqm = float(property_data.get("living_space", 65.0) or 65.0)
    year_built = int(insights.get("year_built_extracted") or property_data.get("year_built") or 1975)
    has_vis = isinstance(image_analysis, dict) and bool(image_analysis.get("analyzed_photos"))
    has_interior_vis = bool(image_analysis.get("has_interior_photos")) if has_vis else False
    vis_photo_count = len((image_analysis or {}).get("analyzed_photos") or [])
    items = []

    # 1. WEG Instandhaltungsrücklagen-Puffer / Sonderumlagen-Risiko
    if insights.get("no_reserve_found"):
        items.append({
            "title": "Sicherheitsrücklage für WEG (0 € Rücklage gebildet)",
            "amount": 12000.0,
            "urgency": "KRITISCH",
            "source_type": "EXPOSE_TEXT",
            "photo_indices": [],
            "evidence_source": "📄 Laut Exposé-Text: Keine WEG-Instandhaltungsrücklage vorhanden – direktes Sonderumlagen-Risiko.",
            "description": "Die WEG hat keine Instandhaltungsrücklage gebildet. Für notwendige Gemeinschaftssanierungen (Dach/Heizung) drohen ungedeckte Sonderumlagen."
        })
    elif insights.get("special_assessment"):
        items.append({
            "title": "Geplante/beschlossene Sonderumlage WEG",
            "amount": 7500.0,
            "urgency": "HOCH",
            "source_type": "EXPOSE_TEXT",
            "photo_indices": [],
            "evidence_source": "📄 Laut Exposé-Text: Sonderumlage im Inserat oder in Unterlagen erwähnt.",
            "description": "Im Exposé oder Protokoll erwähnte anstehende Sonderumlage für Gemeinschaftseigentum."
        })
    elif year_built < 1980 and insights.get("reserve_amount", 0.0) < 3000.0 and insights.get("reserve_amount", 0.0) > 0:
        items.append({
            "title": "WEG-Sanierungspuffer (Baujahr < 1980)",
            "amount": 4000.0,
            "urgency": "MITTEL",
            "source_type": "BAUJAHR_MUTMASSUNG",
            "photo_indices": [],
            "evidence_source": f"⚠️ Baujahrs-/WEG-Pauschale (Bj. {year_built}): Im Exposé genannte Rücklage gering; Gemeinschaftseigentum über Fotos nicht vollständig prüfbar.",
            "description": "Empfohlener Puffer für ältere Wohnanlagen mit mäßiger Rücklagenausstattung."
        })

    # 2. Heizung / GEG
    heating = insights.get("heating_type", "Zentralheizung")
    vis_heat = _find_visual_evidence(
        image_analysis, ["heiz", "therme", "nachtspeicher", "rippenheizkörper"], "heating_electrics", ["heizkörper", "therme", "nachtspeicher", "rippenheiz"]
    )
    if "Gas-Etagenheizung" in heating and not insights.get("is_kernsaniert"):
        p_idx = vis_heat.get("photo_indices", []) if has_interior_vis else []
        items.append({
            "title": "Erneuerung Gas-Etagenheizung / Brennwerttherme",
            "amount": 6500.0,
            "urgency": "HOCH" if insights.get("energy_class") in ["F", "G", "H"] else "MITTEL",
            "source_type": "FOTO_BELEG" if p_idx else "EXPOSE_TEXT",
            "photo_indices": p_idx,
            "evidence_source": (
                f"📄 Laut Exposé-Text (Gas-Etagenheizung) & 📸 sichtbar auf Foto {', '.join(f'#{i}' for i in p_idx)}"
                if p_idx else "📄 Laut Exposé-Text: Gas-Etagenheizung als Heizungsart angegeben (Alter/Effizienz am Objekt prüfen)."
            ),
            "description": "Austausch älterer Thermen gegen moderne Brennwerttechnik nach GEG-Anforderung."
        })
    elif "Nachtspeicher" in heating:
        p_idx = vis_heat.get("photo_indices", []) if has_interior_vis else []
        items.append({
            "title": "Rückbau & Austausch Nachtspeicheröfen",
            "amount": 14000.0,
            "urgency": "KRITISCH",
            "source_type": "FOTO_BELEG" if p_idx else "EXPOSE_TEXT",
            "photo_indices": p_idx,
            "evidence_source": (
                f"📄 Laut Exposé & 📸 Foto {', '.join(f'#{i}' for i in p_idx)}: Nachtspeicherheizung im Objekt."
                if p_idx else "📄 Laut Exposé-Text: Nachtspeicherheizung im Inserat angegeben."
            ),
            "description": "Nachtspeicherheizungen sind extrem ineffizient und mindern die Vermietbarkeit drastisch. Umbau auf moderne Wärmepumpen- oder Split-Systeme erforderlich."
        })

    # 3. Fenster (Mit KI-Bildanalyse abgleichen, um Fehl-Schätzungen wie bei Baujahr < 1985 zu vermeiden!)
    win = insights.get("windows_type", "")
    vis_win = _find_visual_evidence(
        image_analysis, ["fenster", "verglasung", "rollladen"], "windows", ["fenster", "verglasung", "kunststoffrahmen"]
    )
    vis_win_cat = vis_win.get("cat_obj") or {}
    vis_win_item = vis_win.get("matched_item")
    vis_win_ok = (
        has_interior_vis
        and vis_win_cat
        and float(vis_win_cat.get("cost") or 0.0) == 0.0
        and float(vis_win_cat.get("score") or 0.0) >= 7.0
        and not vis_win_item
    )
    if vis_win_item and float(vis_win_item.get("cost") or 0.0) > 0:
        p_idx = vis_win.get("photo_indices") or []
        items.append({
            "title": vis_win_item.get("title") or "Fenstermodernisierung (Wärmeschutzverglasung)",
            "amount": round(float(vis_win_item["cost"]), 2),
            "urgency": "HOCH" if "einfach" in str(vis_win_item.get("description", "")).lower() else "MITTEL",
            "source_type": "FOTO_BELEG" if p_idx else "BAUJAHR_MUTMASSUNG",
            "photo_indices": p_idx,
            "evidence_source": vis_win_item.get("evidence_source") or (
                f"📸 Sichtbar auf Foto {', '.join(f'#{i}' for i in p_idx)}: {vis_win_cat.get('findings', '')}"
                if p_idx else f"⚠️ Baujahrs-/Zustands-Mutmaßung (Bj. {year_built}): Kein klares Fenster-Nahbild."
            ),
            "description": vis_win_item.get("description") or "Austausch älterer Fensterflügel gegen isolierte Wärmeschutzfenster."
        })
    elif not vis_win_ok and ("Einfachverglasung" in win or (year_built < 1985 and not insights.get("is_kernsaniert") and "3-fach" not in win)):
        cost = round(max(3, round(sqm / 18)) * 850.0, 2)
        if "Einfachverglasung" in win:
            src_type = "EXPOSE_TEXT"
            ev_txt = "📄 Laut Exposé-Text: Einfachverglasung im Inserat angegeben."
        else:
            src_type = "BAUJAHR_MUTMASSUNG"
            vis_hint = (
                "noch keine KI-Bildanalyse gestartet"
                if not has_vis else ("nur Grundrisse im Inserat" if not has_interior_vis else f"auf den {vis_photo_count} Fotos Zustand nicht eindeutig ablesbar")
            )
            ev_txt = f"⚠️ Reine Baujahrs-Mutmaßung (Bj. {year_built}): Im Exposé keine genaue Fenster-Angabe ({vis_hint}) – vorsorglicher Kalkulationspuffer."
        items.append({
            "title": "Fenstermodernisierung (Wärmeschutzverglasung)",
            "amount": cost,
            "urgency": "MITTEL",
            "source_type": src_type,
            "photo_indices": [],
            "evidence_source": ev_txt,
            "description": "Austausch älterer 2-fach oder 1-fach Fensterflügel gegen isolierte Wärmeschutzfenster."
        })

    # 4. Elektrik (Explizit unterscheiden: Sicherungskasten auf Foto vs. Baujahrs-Mutmaßung!)
    vis_elec = _find_visual_evidence(
        image_analysis,
        ["elektrik", "sicherung", "unterverteilung", "fi", "leitung"],
        "heating_electrics",
        ["sicherungskasten", "unterverteilung", "elektro", "kippschalter", "steckdose"]
    )
    vis_elec_cat = vis_elec.get("cat_obj") or {}
    vis_elec_item = vis_elec.get("matched_item")
    vis_elec_photos = [
        p["index"] for p in ((image_analysis or {}).get("analyzed_photos") or [])
        if not p.get("is_blueprint") and any(k in (p.get("description", "") + " " + " ".join(p.get("findings") or [])).lower() for k in ["sicherungskasten", "unterverteilung", "schaltschrank", "kippschalter"])
    ]
    vis_elec_modern = (
        has_interior_vis
        and vis_elec_cat
        and float(vis_elec_cat.get("score") or 0.0) >= 8.0
        and float(vis_elec_cat.get("cost") or 0.0) == 0.0
        and any(k in str(vis_elec_cat.get("findings", "")).lower() for k in ["fi-schalter", "unterverteilung", "modern", "neu"])
    )
    if not insights.get("electrics_modern") and not vis_elec_modern and (year_built < 1985 or insights.get("needs_renovation")):
        if vis_elec_photos:
            p_refs = ", ".join(f"#{i}" for i in vis_elec_photos)
            src_type = "FOTO_BELEG"
            p_idx = vis_elec_photos
            ev_txt = f"📸 Beobachtet auf Foto {p_refs}: Veralteter Sicherungskasten bzw. älteres Schalterprogramm sichtbar."
        elif "alt" in str((insights.get("raw_text") or "")).lower() and "elektrik" in str((insights.get("raw_text") or "")).lower():
            src_type = "EXPOSE_TEXT"
            p_idx = []
            ev_txt = "📄 Laut Exposé-Text: Hinweise auf veraltete Elektroinstallation."
        else:
            src_type = "BAUJAHR_MUTMASSUNG"
            p_idx = []
            if not has_vis:
                ev_txt = f"⚠️ Reine Baujahrs-Mutmaßung (Bj. {year_built}): Noch keine KI-Bildanalyse durchgeführt – Sicherheitspuffer bis zur Prüfung des Sicherungskastens."
            elif not has_interior_vis:
                ev_txt = f"⚠️ Reine Baujahrs-Mutmaßung (Bj. {year_built}): Im Inserat liegen keine Innenraumfotos vor – Unterverteilung vor Ort prüfen."
            else:
                ev_txt = f"⚠️ Reine Baujahrs-Mutmaßung (Bj. {year_built}): Auf keinem der {vis_photo_count} Fotos ist ein Sicherungskasten oder eine Unterverteilung sichtbar – vorsorglicher Puffer für FI-Nachrüstung."
        items.append({
            "title": "Elektrik-Modernisierung (Unterverteilung & FI)",
            "amount": 4500.0,
            "urgency": "HOCH" if year_built < 1975 else "MITTEL",
            "source_type": src_type,
            "photo_indices": p_idx,
            "evidence_source": ev_txt,
            "description": "Nachrüstung von FI-Schutzschaltern und 3-adriger Leitungsführung zur Personensicherheit."
        })

    # 5. Bad & Sanitär (Mit Bildanalyse abgleichen!)
    unit_mult = max(1, int(insights.get("unit_count_in_offer", 1) or 1))
    vis_bad = _find_visual_evidence(
        image_analysis, ["bad", "sanitär", "wc", "dusche", "wanne"], "bathroom", ["bad", "wc", "sanitär", "dusche", "wanne"]
    )
    vis_bad_cat = vis_bad.get("cat_obj") or {}
    vis_bad_item = vis_bad.get("matched_item")
    vis_bad_photos = vis_bad.get("photo_indices") or []
    vis_bad_modern = (
        has_interior_vis
        and vis_bad_cat
        and (
            float(vis_bad_cat.get("score") or 0.0) >= 7.0
            or any(k in str(vis_bad_cat.get("status", "")).lower() for k in ["gut", "sehr gut", "gepflegt", "modern", "solide"])
        )
        and float(vis_bad_cat.get("cost") or 0.0) == 0.0
        and (not vis_bad_item or float(vis_bad_item.get("cost") or 0.0) == 0.0)
    )
    if not insights.get("bath_modern") and not vis_bad_modern and (
        insights.get("needs_renovation") or year_built < 1990 or (vis_bad_item and float(vis_bad_item.get("cost") or 0.0) > 0)
    ):
        if has_interior_vis and vis_bad_photos:
            p_refs = ", ".join(f"#{i}" for i in vis_bad_photos)
            src_type = "FOTO_BELEG"
            p_idx = vis_bad_photos
            bad_finding = str((vis_bad_item or {}).get("description") or vis_bad_cat.get("findings") or "").strip()

            # Determine cost safely (avoiding 0.0 or fallback bug)
            if vis_bad_item and vis_bad_item.get("cost") is not None:
                amt = float(vis_bad_item["cost"])
            elif vis_bad_cat and vis_bad_cat.get("cost") is not None and float(vis_bad_cat.get("cost") or 0.0) >= 0:
                amt = float(vis_bad_cat["cost"])
            elif float(vis_bad_cat.get("score") or 0.0) >= 6.5 or any(k in str(vis_bad_cat.get("status", "")).lower() for k in ["gut", "gepflegt", "solide", "modern"]):
                amt = 1500.0
            else:
                amt = round(7500.0 * (1.0 + 0.6 * (unit_mult - 1)), 2)

            if amt > 0:
                is_minor = amt <= 2500.0
                title_bad = f"Badezimmer-Auffrischung / Kosmetik ({unit_mult} Einheit{'en' if unit_mult > 1 else ''})" if is_minor else f"Badezimmer-Renovierung / Sanitär ({unit_mult} Einheit{'en' if unit_mult > 1 else ''})"
                ev_txt = f"📸 Sichtbar auf Foto {p_refs}: {bad_finding or ('Gepflegte Sanitärausstattung / Verfliesung im Foto belegt (optische Auffrischung).' if is_minor else 'Ältere Sanitärausstattung / Modernisierungsbedarf erkennbar.')}"
                items.append({
                    "title": title_bad,
                    "amount": round(amt, 2),
                    "urgency": "GERING" if is_minor else ("HOCH" if insights.get("needs_renovation") else "MITTEL"),
                    "source_type": src_type,
                    "photo_indices": p_idx,
                    "evidence_source": ev_txt,
                    "description": "Kosmetische Auffrischung von Silikonfugen, Armaturen und Sanitärobjekten." if is_minor else "Modernisierung von Sanitärobjekten, Fliesen und Armaturen für gehobene Vermietbarkeit."
                })
        else:
            src_type = "EXPOSE_TEXT" if insights.get("needs_renovation") else "BAUJAHR_MUTMASSUNG"
            p_idx = []
            ev_txt = (
                "📄 Laut Exposé-Text: Renovierungs-/Sanierungsbedarf im Inserat erwähnt."
                if insights.get("needs_renovation")
                else f"⚠️ Baujahrs-Mutmaßung (Bj. {year_built}): Im Exposé nicht modernisiert gemeldet" + (f" und kein Bad auf den {vis_photo_count} Fotos erkennbar." if has_interior_vis else " (kein Innenraumfoto vom Bad geprüft).")
            )
            amt = round(7500.0 * (1.0 + 0.6 * (unit_mult - 1)), 2)
            items.append({
                "title": f"Badezimmer-Renovierung / Sanitär ({unit_mult} Einheit{'en' if unit_mult > 1 else ''})",
                "amount": round(amt, 2),
                "urgency": "HOCH" if insights.get("needs_renovation") else "GERING",
                "source_type": src_type,
                "photo_indices": p_idx,
                "evidence_source": ev_txt,
                "description": "Modernisierung von Sanitärobjekten, Fliesen und Armaturen für gehobene Vermietbarkeit."
            })

    # 6. Maler- & Bodenbelagsarbeiten (Mit Bildanalyse abgleichen!)
    vis_wall = _find_visual_evidence(
        image_analysis, ["maler", "ausbesserung", "boden", "laminat", "parkett", "tapete", "wand", "wände"], "walls_substance", ["wohn", "schlaf", "zimmer", "flur"]
    )
    vis_floor_cat = (image_analysis or {}).get("categories", {}).get("flooring", {}) if has_interior_vis else {}
    vis_wall_cat = vis_wall.get("cat_obj") or {}
    vis_wall_item = vis_wall.get("matched_item")
    vis_surfaces_perfect = (
        has_interior_vis
        and vis_floor_cat and vis_wall_cat
        and float(vis_floor_cat.get("score") or 0.0) >= 8.5
        and float(vis_wall_cat.get("score") or 0.0) >= 8.5
        and float(vis_floor_cat.get("cost") or 0.0) == 0.0
        and float(vis_wall_cat.get("cost") or 0.0) == 0.0
        and not vis_wall_item
    )
    if not insights.get("is_kernsaniert") and not vis_surfaces_perfect:
        vis_item_cost = float((vis_wall_item or {}).get("cost") or 0.0) + float(vis_floor_cat.get("cost") or 0.0)
        vis_cat_cost = float(vis_floor_cat.get("cost") or 0.0) + float(vis_wall_cat.get("cost") or 0.0)
        cost = round(vis_item_cost if vis_item_cost > 0 else vis_cat_cost, 2) if (has_interior_vis and (vis_item_cost > 0 or vis_cat_cost > 0)) else round(sqm * (45.0 if insights.get("needs_renovation") else 20.0), 2)
        room_photos = vis_wall.get("photo_indices") or []
        if has_interior_vis and room_photos and (vis_item_cost > 0 or vis_cat_cost > 0):
            p_refs = ", ".join(f"#{i}" for i in room_photos)
            src_type = "FOTO_BELEG"
            p_idx = room_photos
            ev_txt = (vis_wall_item or {}).get("evidence_source") or f"📸 Sichtbar auf Foto {p_refs}: {vis_floor_cat.get('findings') or vis_wall_cat.get('findings') or 'Gebrauchs- und Renovierungsspuren an Böden/Wänden erkennbar.'}"
        elif insights.get("needs_renovation"):
            src_type = "EXPOSE_TEXT"
            p_idx = []
            ev_txt = "📄 Laut Exposé-Text: Renovierungsbedarf im Beschreibungstext erwähnt."
        else:
            src_type = "BAUJAHR_MUTMASSUNG"
            p_idx = []
            ev_txt = f"⚠️ Pauschale Neuvermietungs-Kalkulation ({round(sqm)} m²): Standard-Budget für Anstrich & Bodenauffrischung bei Mieterwechsel."
        items.append({
            "title": "Bodenbeläge & Malerarbeiten",
            "amount": cost,
            "urgency": "MITTEL" if insights.get("needs_renovation") else "GERING",
            "source_type": src_type,
            "photo_indices": p_idx,
            "evidence_source": ev_txt,
            "description": "Laminat/Vinyl neu verlegen, Wände spachteln und weiß streichen."
        })

    # 7. Zusätzliche visuelle Bildbefunde übernehmen (z. B. fehlende EBK, Holzdecken, sichtbare Mängel), sofern noch nicht abgedeckt
    existing_keywords = " ".join(i["title"].lower() for i in items)
    if has_vis:
        for vi in (image_analysis.get("visual_capex_items") or []):
            if not isinstance(vi, dict):
                continue
            vt = str(vi.get("title", "")).strip()
            try:
                vamt = float(vi.get("cost") or 0.0)
            except (ValueError, TypeError):
                vamt = 0.0
            if not vt or vamt <= 0:
                continue
            v_l = vt.lower()
            # If kitchen is already present on photos or category shows EBK, DO NOT charge for buying a new kitchen!
            if any(k in v_l for k in ["küche", "ebk", "einbauküche"]):
                k_cat = (image_analysis.get("categories") or {}).get("kitchen") or {}
                k_status = str(k_cat.get("status") or "").lower()
                k_photos = [
                    p.get("index") for p in (image_analysis.get("analyzed_photos") or [])
                    if not p.get("is_blueprint") and (
                        p.get("tag") == "🍳 Küche / EBK" or
                        "küche" in (str(p.get("tag", "")) + " " + str(p.get("description", ""))).lower()
                    )
                ]
                if (k_photos or "vorhanden" in k_status or "gepflegt" in k_status or "intakt" in k_status) and any(w in v_l for w in ["beschaffen", "neuanschaffung", "kauf", "einbauküche beschaffen"]):
                    continue

            is_holzdecke = "holz" in v_l and "decke" in v_l
            already_covered = False if is_holzdecke else any(
                (k in v_l and k in existing_keywords)
                for k in [
                    "bad", "sanitär", "fenster", "elektrik", "unterverteilung", "sicherung", "fi-",
                    "boden", "maler", "wand", "wände", "heizung"
                ]
            )
            if not already_covered:
                p_idx = [int(p) for p in (vi.get("photo_indices") or []) if str(p).isdigit()]
                items.append({
                    "title": vt,
                    "amount": round(vamt, 2),
                    "urgency": "HOCH" if "sofort" in str(vi.get("urgency", "")).lower() else "MITTEL",
                    "source_type": vi.get("source_type") or ("FOTO_BELEG" if p_idx else "BAUJAHR_MUTMASSUNG"),
                    "photo_indices": p_idx,
                    "evidence_source": vi.get("evidence_source") or (
                        f"📸 Sichtbar auf Foto {', '.join(f'#{i}' for i in p_idx)}: Bildbefund aus Fotoscan."
                        if p_idx else "⚠️ Pauschal-Annahme aus Objektprüfung."
                    ),
                    "description": str(vi.get("description", ""))
                })
                existing_keywords += " " + v_l

    # 8. Merge any distinct LLM-specific CapEx items from text exposé
    for adj in insights.get("llm_capex_adjustments", []):
        if not isinstance(adj, dict):
            continue
        t = str(adj.get("title", "")).strip()
        try:
            amt = float(adj.get("amount", 0.0) or 0.0)
        except (ValueError, TypeError):
            amt = 0.0
        if t and amt > 0:
            t_l = t.lower()
            if len(items) >= 2 and any(p in t_l for p in ["modernisierungspuffer", "instandhaltungspuffer", "sanierungspuffer", "puffer"]):
                continue
            key_words = [w for w in re.split(r"[\W_]+", t_l) if len(w) > 4 and w not in {"prüfung", "erneuerung", "sanierung", "modernisierung", "baujahr"}]
            if not any(kw in existing_keywords for kw in key_words):
                ev_raw = str(adj.get("evidence_source") or "").strip()
                st_llm = str(adj.get("source_type") or "EXPOSE_TEXT").upper()
                if "mutmaßung" in ev_raw.lower() or "baujahr" in ev_raw.lower():
                    st_llm = "BAUJAHR_MUTMASSUNG"
                if st_llm not in ["FOTO_BELEG", "EXPOSE_TEXT", "BAUJAHR_MUTMASSUNG"]:
                    st_llm = "EXPOSE_TEXT"
                items.append({
                    "title": t,
                    "amount": round(amt, 2),
                    "urgency": str(adj.get("urgency", "MITTEL")).upper(),
                    "source_type": st_llm,
                    "photo_indices": [],
                    "evidence_source": ev_raw or ("📄 Laut Exposé-Text: Aus Objektbeschreibung abgeleitet." if st_llm == "EXPOSE_TEXT" else f"⚠️ Baujahrs-/Zustands-Mutmaßung (Bj. {year_built})"),
                    "description": str(adj.get("description", ""))
                })

    total_capex = round(sum(i["amount"] for i in items), 2)

    res = {
        "items": items,
        "total_capex": total_capex,
        "has_high_capex": total_capex > 15000.0
    }
    if has_vis:
        res["visual_capex"] = float(image_analysis.get("total_visual_capex") or 0.0)
        res["visual_items"] = image_analysis.get("visual_capex_items") or []
    return res


def reconcile_capex_with_images(
    ai_analysis: Dict[str, Any],
    property_data: Dict[str, Any],
    image_analysis: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Aktualisiert die CaPEx-Positionen einer bestehenden KI-Due-Diligence sofort anhand neu analysierter Fotos
    (ohne erneuten Text-LLM-Aufruf) und aktualisiert den Verhandlungspreis konsistent.
    """
    if not isinstance(ai_analysis, dict) or not isinstance(image_analysis, dict):
        return ai_analysis
    updated = dict(ai_analysis)
    updated["image_analysis"] = image_analysis
    insights = updated.get("insights") or {}
    new_capex = estimate_capex_and_hidden_costs(insights, property_data, image_analysis=image_analysis)
    updated["capex"] = new_capex
    if isinstance(updated.get("metrics"), dict):
        m = dict(updated["metrics"])
        m["total_capex"] = new_capex["total_capex"]
        m["effective_total_investment"] = round(
            float(m.get("purchase_price") or 0.0) + float(m.get("ancillary_costs") or 0.0) + new_capex["total_capex"], 2
        )
        updated["metrics"] = m
        price = float(m.get("purchase_price") or property_data.get("purchase_price") or 0.0)
        if price > 0:
            rec_disc = round(min(price * 0.25, new_capex["total_capex"] * 0.85 + (8000.0 if insights.get("no_reserve_found") else 0.0)), -2)
            updated["recommended_discount"] = rec_disc
            tgt_price = max(10000.0, round(price - rec_disc, -2))
            updated["negotiation_target_price"] = tgt_price

            if "executive_summary" in updated and isinstance(updated["executive_summary"], str):
                def _fmt(val, dec=2):
                    return f"{val:,.{dec}f}".replace(",", "X").replace(".", ",").replace("X", ".")
                disc_pct = round((rec_disc / price) * 100, 1)
                nego_replacement = (
                    f"VERHANDLUNGSEMPFEHLUNG: Empfohlenes Kaufpreis-Zielangebot: {_fmt(tgt_price)} € "
                    f"(Nachlass ca. {_fmt(rec_disc)} € bzw. {_fmt(disc_pct, 1)}% "
                    f"aufgrund ermitteltem CaPEx/Prüfbedarf von {_fmt(new_capex['total_capex'])} €). "
                    f"Sprengnetter-Marktwertbenchmark: {_fmt(m.get('market_value') or 0.0)} € ({_fmt(m.get('market_value_per_sqm') or 0.0)} €/m²)."
                )
                updated["executive_summary"] = re.sub(
                    r"(?i)VERHANDLUNGSEMPFEHLUNG:.*?(?=\n\n|$)",
                    nego_replacement,
                    updated["executive_summary"]
                )
    return updated


# ==============================================================================
# 4. VERSTECKTE PROBLEME & FINANZIELLE RISIKEN
# ==============================================================================

def detect_hidden_problems_and_risks(insights: Dict[str, Any], property_data: Dict[str, Any], capex: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Erkennt versteckte Probleme, rechtliche Fallstricke und wirtschaftliche Risiken.
    Berücksichtigt explizit Paket-Risiken (z. B. Hausgeld > Kaltmiete), GEG, WEG & Provisionsfreiheit.
    """
    risks = []
    price = float(property_data.get("purchase_price") or 0.0)
    year_built = int(insights.get("year_built_extracted") or property_data.get("year_built") or 1975)

    # 0. Kritisches Paket-/Cashflow-Risiko: Hausgeld > Ist-Kaltmiete!
    rent_val = float(insights.get("extracted_rent") or 0.0)
    hg_val = float(insights.get("hausgeld") or 0.0)
    if insights.get("has_explicit_hausgeld") and hg_val > 0 and rent_val > 0 and hg_val > rent_val:
        risks.append({
            "title": f"Hohes Hausgeld ({fmt_de(hg_val)} €/M.) übersteigt Ist-Kaltmiete ({fmt_de(rent_val)} €/M.)",
            "severity": "CRITICAL",
            "category": "Cashflow & Bewirtschaftung",
            "badge": "🔴 Kritischer Kostentreiber",
            "description": f"Das im Exposé angegebene Gesamthausgeld von {fmt_de(hg_val)} € liegt über der gesamten Kaltmiete von {fmt_de(rent_val)} €. Selbst wenn ein Teil umlagefähig ist, muss zwingend geprüft werden, wie hoch die nicht-umlagefähigen Kosten und Zuführungen zur Rücklage sind.",
            "impact": "Hohe monatliche Liquiditätsbelastung möglich, falls nicht-umlagefähiger Anteil stark ins Gewicht fällt.",
            "action": "Die letzten 3 Hausgeldabrechnungen und den aktuellen Wirtschaftsplan (Aufteilung umlagefähig vs. nicht umlagefähig) vor Besichtigung anfordern."
        })

    # 0b. Vermietet ohne Kaltmieten-Angabe (Intransparente Ertragslage)
    if insights.get("is_rented") and not insights.get("extracted_rent"):
        risks.append({
            "title": "Intransparente Ertragslage trotz Vermietung (Kaltmiete fehlt im Exposé)",
            "severity": "HIGH",
            "category": "Ertrag & Miete",
            "badge": "🟡 Fehlende Ist-Miete",
            "description": "Die Wohnung wird als vermietet angeboten (oftmals mit Rendite-Werbung), nennt im Exposé jedoch keine konkrete monatliche Ist-Kaltmiete. Die ausgewiesene Rendite im Tool basiert daher ausschließlich auf dem unverbindlichen Sprengnetter-Soll-Benchmark und ist keine bestätigte Ist-Rendite.",
            "impact": "Risiko einer stark unter Markt liegenden Altmiete oder von Mietrückständen – reale Ist-Rendite kann deutlich niedriger ausfallen.",
            "action": "Aktuellen Mietvertrag, letzte Mieterhöhung und Mieteingänge der letzten 6 Monate zwingend vor Besichtigung anfordern."
        })

    # 1. 0 € Instandhaltungsrücklage
    if insights.get("no_reserve_found"):
        risks.append({
            "title": "Eigentümergemeinschaft ohne Instandhaltungsrücklage (0 €)",
            "severity": "CRITICAL",
            "category": "WEG & Finanzen",
            "badge": "🔴 Kritisch",
            "description": "In der Wohnanlage wurden laut Exposé keine Instandhaltungsrücklagen gebildet. Jegliche Instandsetzung an Dach, Fassade, Treppenhaus oder Heizanlage muss sofort über Bar-Sonderumlagen aller Eigentümer getragen werden.",
            "impact": "Unkalkulierbares Nachschussrisiko im fünfstelligen Bereich.",
            "action": "Mindestens 10.000,00 € bis 15.000,00 € Barreserve vorhalten und Kaufpreis um diesen Betrag rabattieren."
        })

    # 2. Fehlende Abrechnungen / Historie
    if insights.get("missing_protocols"):
        risks.append({
            "title": "Intransparente WEG-Historie (keine vollständigen Abrechnungen/Protokolle)",
            "severity": "CRITICAL",
            "category": "Recht & Transparenz",
            "badge": "🔴 Kritisch",
            "description": "Aus der Vergangenheit liegen keine lückenlosen Hausgeldabrechnungen oder Versammlungsprotokolle vor. Es besteht Blindflug-Risiko bezüglich offener Streitigkeiten oder Sanierungspflichten.",
            "impact": "Mögliche Haftung für Altschulden der Gemeinschaft oder unentdeckte Großschäden.",
            "action": "Vor Notartermin Einsicht in die Beschlusssammlung (§ 24 Abs. 7 WEG) erzwingen."
        })

    # 3. Energieeffizienzklasse (GEG)
    ec = insights.get("energy_class", "")
    if ec in ["F", "G", "H"]:
        risks.append({
            "title": f"Schlechte Energieeffizienzklasse ({ec}) / Hoher Endenergiebedarf",
            "severity": "HIGH",
            "category": "Energie & Sanierungsstau",
            "badge": "🟡 Erhöhtes Risiko",
            "description": f"Die Immobilie weist Energieklasse {ec} auf (Endenergiebedarf ca. {insights.get('energy_kwh') or 200} kWh/m²a). Nach Gebäudeenergiegesetz (GEG) drohen mittelfristige Sanierungspflichten.",
            "impact": "CO2-Kostenaufteilung nach CO2KostAufG belastet den Vermieter überproportional.",
            "action": "Sanierungsfahrplan (iSFP) erstellen lassen; KfW/BAFA-Förderung prüfen."
        })

    # 4. Erbbaurecht
    if insights.get("is_erbbaurecht"):
        risks.append({
            "title": "Erbbaurecht vorhanden (Kein Grundstückseigentum)",
            "severity": "HIGH",
            "category": "Recht & Eigentum",
            "badge": "🟡 Erhöhtes Risiko",
            "description": "Es handelt sich um ein Erbbaurecht. Das Grundstück gehört einem Erbbaurechtsgeber, an den laufend Erbbauzins gezahlt werden muss.",
            "impact": "Geringerer Beleihungswert bei Banken, laufende Kosten, Wertverlust bei kurzer Restlaufzeit.",
            "action": "Restlaufzeit und Erbbauzinsanpassungsklauseln prüfen."
        })

    # 5. Nachtspeicherheizung
    if "Nachtspeicher" in insights.get("heating_type", ""):
        risks.append({
            "title": "Nachtspeicherheizung (Veraltete Stromheizung)",
            "severity": "CRITICAL",
            "category": "Heiztechnik",
            "badge": "🔴 Kritisch",
            "description": "Nachtspeicheröfen führen zu sehr hohen Stromkosten und erschweren die Vermietbarkeit.",
            "impact": "Wertabschlag am Markt von 15-25%.",
            "action": "Austausch gegen Luft-Luft-Wärmepumpen oder moderne Systeme budgetieren."
        })

    # 6. Maklerprovision vs. Provisionsfreier Privatverkauf
    if insights.get("commission_free") or insights.get("is_private") or float(insights.get("commission_percent", 0.0)) == 0.0:
        saved_amount = round(price * 0.0357, 2)
        risks.append({
            "title": "Provisionsfreier Privatverkauf (0 € Maklerprovision)",
            "severity": "POSITIVE",
            "category": "Kaufnebenkosten-Vorteil",
            "badge": "🟢 0% Provision",
            "description": "Der Verkauf erfolgt von privat bzw. provisionsfrei. Es fällt keine Käuferprovision an.",
            "impact": f"Echtes Eigenkapital-Plus: Sie sparen ca. {fmt_de(saved_amount)} € an reinen Maklernebenkosten ein.",
            "action": "Kaufnebenkosten auf reine Notar- und Grunderwerbsteuerkosten reduzieren."
        })
    else:
        comm_pct = float(insights.get("commission_percent", 3.57))
        comm_amount = round(price * (comm_pct / 100.0), 2)
        risks.append({
            "title": f"Maklerprovision beachten ({fmt_de(comm_pct, 2)}% = {fmt_de(comm_amount)} €)",
            "severity": "MEDIUM",
            "category": "Kaufnebenkosten",
            "badge": "ℹ️ Info",
            "description": f"Beim Kauf über Makler fällt eine Käuferprovision von {fmt_de(comm_pct, 2)}% an, die das erforderliche Eigenkapital bindet.",
            "impact": f"Erhöht die Kaufnebenkosten um {fmt_de(comm_amount)} €.",
            "action": "In die Cashflow- und Eigenkapitalrendite-Kalkulation einpreisen."
        })

    # 7. Altbau ohne Sanierungsnachweis
    if year_built < 1960 and not insights.get("is_kernsaniert") and not insights.get("is_renoviert"):
        risks.append({
            "title": f"Altbausubstanz Baujahr {insights.get('year_built_label') or year_built} ohne Sanierungsnachweis",
            "severity": "MEDIUM",
            "category": "Bausubstanz",
            "badge": "🟡 Prüfbedarf",
            "description": "Bei Altbauten vor 1960 ohne dokumentierte Kernsanierung können Steigleitungen, Dachdämmung und Schallschutz veraltet sein.",
            "impact": "Erhöhter laufender Instandhaltungsaufwand (Empfehlung: 15–18 €/m²/Jahr Rücklage).",
            "action": "Bausachverständigen zur Vor-Ort-Besichtigung hinzuziehen."
        })

    # 8. Merge extra semantic risks found by LLM (e.g. missing Energieausweis, below-market leases)
    existing_cats = {r["category"].lower() for r in risks}
    for rh in insights.get("llm_risk_highlights", []):
        if not isinstance(rh, dict):
            continue
        title_r = str(rh.get("title", "")).strip()
        sev_r = str(rh.get("severity", "MEDIUM")).upper()
        cat_r = str(rh.get("category", "Exposé-Befund")).strip()
        if not title_r:
            continue
        # Skip duplicate commission/hausgeld/missing-rent alerts already explicitly constructed
        if "maklerprovision" in title_r.lower() or "provision" in title_r.lower():
            continue
        if "hausgeld" in title_r.lower() and any("hausgeld" in r["title"].lower() for r in risks):
            continue
        if any(k in title_r.lower() for k in ["kaltmiete", "ertragslage", "ist-miete", "mietertrag", "mietangabe", "miethöhe", "mietvertrag", "renditekennzahl"]) and any("kaltmiete" in r["title"].lower() or "ertragslage" in r["title"].lower() for r in risks):
            continue
        # Safeguard: Below-market price or low purchase price is an INVESTMENT OPPORTUNITY, NEVER a CRITICAL K.O. risk!
        desc_r = str(rh.get("description", ""))
        is_price_anomaly = any(k in title_r.lower() or k in desc_r.lower() for k in [
            "unter marktdurchschnitt", "unter marktwert", "kaufpreisniveau", "niedriger kaufpreis", 
            "günstiger kaufpreis", "preisniveau", "marktabweichung", "preis unter", "preisabstand",
            "weit unter", "preis weit", "verdecktes risiko"
        ])
        if is_price_anomaly and sev_r == "CRITICAL":
            sev_r = "HIGH"
            badge_r = "🟡 Prüfhinweis (Unter Marktwert)"
        else:
            badge_r = str(rh.get("badge") or ("🔴 Kritisch" if sev_r == "CRITICAL" else ("🟡 Hinweis" if sev_r == "HIGH" else "ℹ️ Prüfbedarf")))

        if cat_r.lower() not in existing_cats or sev_r in ["HIGH", "CRITICAL"]:
            risks.append({
                "title": title_r,
                "severity": sev_r if sev_r in ["CRITICAL", "HIGH", "MEDIUM", "POSITIVE"] else "MEDIUM",
                "category": cat_r,
                "badge": badge_r,
                "description": desc_r,
                "impact": str(rh.get("impact", "")),
                "action": str(rh.get("action", ""))
            })

    return risks


# ==============================================================================
# 5. KENNZAHLEN & RENDITE-BERECHNUNG (IST VS. SOLL)
# ==============================================================================

def compute_investment_metrics(property_data: Dict[str, Any], insights: Dict[str, Any], capex: Dict[str, Any]) -> Dict[str, Any]:
    """
    Berechnet alle Investoren-Kennzahlen auf Basis der verifizierten Exposé-Daten
    (inkl. Paket-Summierung und strikter Trennung von IST vs. SOLL-Benchmark).
    """
    price = float(property_data.get("purchase_price") or 0.0)
    sqm = float(property_data.get("living_space") or 0.0)
    city = str(insights.get("corrected_city") or property_data.get("city") or "").strip()
    state = str(property_data.get("state") or "").strip()
    if not state and city:
        try:
            from app.geo_resolver import resolve_location_and_tax
            geo = resolve_location_and_tax(city=city)
            if geo and geo.get("state"):
                state = geo["state"]
        except Exception:
            pass
    if not state:
        state = "Deutschland"
    year_built = int(insights.get("year_built_extracted") or property_data.get("year_built") or 1980)

    benchmark = evaluate_property_valuation(city, sqm, price, year_built=year_built)
    market_val = benchmark.get("estimated_market_value", price)
    market_val_sqm = benchmark.get("estimated_market_value_per_sqm", 2500.0)
    expected_rent_sqm = benchmark.get("expected_rent_per_sqm", 11.50)
    expected_market_rent = round(sqm * expected_rent_sqm, 2)

    is_rented_explicit = bool(insights.get("is_rented", False))

    if insights.get("extracted_rent") is not None and float(insights["extracted_rent"]) > 0:
        current_rent = float(insights["extracted_rent"])
        rent_source = insights["rent_source"]
        rent_is_verified = True
        is_actually_rented = True
    elif is_rented_explicit:
        # Vermietet laut Exposé, aber OHNE konkrete Kaltmieten-Angabe:
        # Ist-Miete bleibt 0.0 (k. A.), Rendite darf NICHT als IST ausgewiesen werden!
        current_rent = 0.0
        rent_source = "BENCHMARK_ESTIMATE"
        rent_is_verified = False
        is_actually_rented = True
    else:
        current_rent = 0.0
        rent_source = "BENCHMARK_ESTIMATE"
        rent_is_verified = False
        is_actually_rented = False

    potential_annual_rent = round(expected_market_rent * 12.0, 2)
    potential_gross_yield = round((potential_annual_rent / price) * 100.0, 2) if price > 0 else 0.0

    if insights.get("stated_yield") is not None and rent_source == "DERIVED_FROM_STATED_YIELD":
        gross_yield = float(insights["stated_yield"])
        yield_type = "IST"
        actual_gross_yield = gross_yield
        annual_rent = round(current_rent * 12.0, 2)
    elif rent_is_verified and current_rent > 0:
        annual_rent = round(current_rent * 12.0, 2)
        gross_yield = round((annual_rent / price) * 100.0, 2) if price > 0 else 0.0
        yield_type = "IST"
        actual_gross_yield = gross_yield
    elif is_actually_rented:
        annual_rent = 0.0
        gross_yield = potential_gross_yield
        yield_type = "SOLL (Benchmark)"
        actual_gross_yield = 0.0
    else:
        annual_rent = 0.0
        gross_yield = potential_gross_yield
        yield_type = "SOLL"
        actual_gross_yield = 0.0

    price_per_sqm = round(price / sqm, 2) if sqm > 0 else 0.0
    market_deviation = round(((price - market_val) / market_val) * 100.0, 1) if market_val > 0 else 0.0
    commission_pct = 0.0 if insights.get("commission_free") else float(insights.get("commission_percent", property_data.get("commission_percent", 3.57)))

    rent_for_cf = current_rent if (rent_is_verified and current_rent > 0) else expected_market_rent
    non_rec_monthly = float(insights.get("hausgeld_non_recoverable") or 50.0) if insights.get("has_explicit_hausgeld") else 50.0
    prop_plz = str(property_data.get("postal_code") or insights.get("postal_code") or "").strip() or None
    cf_data = calculate_cashflow(
        price=price,
        rent=rent_for_cf,
        city=city,
        state=state,
        postal_code=prop_plz,
        living_space=sqm,
        commission_percent=commission_pct,
        additional_costs=non_rec_monthly
    )

    potential_cf_data = calculate_cashflow(
        price=price,
        rent=expected_market_rent,
        city=city,
        state=state,
        postal_code=prop_plz,
        living_space=sqm,
        commission_percent=commission_pct,
        additional_costs=non_rec_monthly
    )
    potential_monthly_cashflow = potential_cf_data.get("monthly_cashflow", 0.0)

    ancillary_costs = cf_data.get("total_buy_costs", round(price * 0.10, 2))
    total_capex = capex.get("total_capex", 0.0)
    effective_total_investment = round(price + ancillary_costs + total_capex, 2)

    capex_amount = float(total_capex or 0.0)
    cf_data_capex = calculate_cashflow(
        price=price,
        rent=rent_for_cf,
        city=city,
        state=state,
        postal_code=prop_plz,
        living_space=sqm,
        commission_percent=commission_pct,
        additional_costs=non_rec_monthly,
        renovation_costs=capex_amount
    ) if capex_amount > 0 else cf_data

    monthly_hausgeld = float(insights.get("hausgeld") or 250.0)
    non_recoverable_annual = non_rec_monthly * 12.0 if insights.get("has_explicit_hausgeld") else (monthly_hausgeld * 12.0 * 0.35)
    net_rental_income = (annual_rent if annual_rent > 0 else potential_annual_rent) - non_recoverable_annual
    real_net_yield = round((net_rental_income / effective_total_investment) * 100.0, 2) if effective_total_investment > 0 else 0.0

    rent_for_ratio = current_rent if (rent_is_verified and current_rent > 0) else expected_market_rent
    price_to_rent_ratio = round(price / (rent_for_ratio * 12.0), 1) if rent_for_ratio > 0 else 0.0
    rent_upside_percent = round(((expected_market_rent - current_rent) / current_rent) * 100.0, 1) if (rent_is_verified and current_rent > 0) else 0.0

    current_rent_per_sqm = round(current_rent / sqm, 2) if (sqm > 0 and rent_is_verified and current_rent > 0) else 0.0
    expected_rent_per_sqm = round(expected_market_rent / sqm, 2) if sqm > 0 else expected_rent_sqm

    size_bm = get_mietspiegel_size_benchmark(
        city,
        sqm,
        is_package_deal=bool(insights.get("is_package_deal")),
        unit_count=int(insights.get("unit_count_in_offer") or 1)
    )
    mietspiegel_range_sqm = size_bm["range_label"]
    mietspiegel_size_label = size_bm["size_label"]

    # 7. Mietspiegel-Rentabilitäts-Prüfung & Hebel-Analyse
    # WICHTIG: Ein Ist-vs.-Mietspiegel Hebel / Turnaround existiert NUR bei aktuell vermieteten Objekten mit Ist-Miete!
    # Bei unvermieteten / bezugsfreien Objekten gibt es keinen Altvertrag, daher wird keine Hebel-Differenz berechnet.
    is_profitable_at_mietspiegel = bool(potential_monthly_cashflow >= 0)

    if is_actually_rented and rent_is_verified and current_rent > 0:
        cur_cf = cf_data.get("monthly_cashflow", 0.0)
        is_cf_negative = (cur_cf < 0)
        turns_profitable = (is_cf_negative and potential_monthly_cashflow >= 0)
        cf_delta = round(potential_monthly_cashflow - cur_cf, 2)
        is_above_mietspiegel = (current_rent >= expected_market_rent)

        if turns_profitable:
            ana_status = "PROFITABLE_UPSIDE"
            badge_text = f"🚀 Bei Mietspiegel: {fmt_de(potential_monthly_cashflow)} €/M"
            summary_text = (
                f"Mietspiegel-Rentabilität: Bei Anhebung der Ist-Miete von {fmt_de(current_rent)} €/M auf das örtliche Mietspiegel-Niveau ({fmt_de(expected_market_rent)} €/M, {fmt_signed_de(rent_upside_percent, 1)}%) "
                f"dreht der monatliche Cashflow von {fmt_de(cur_cf)} €/M auf {fmt_de(potential_monthly_cashflow)} €/M ({fmt_signed_de(cf_delta)} €/M Hebel)! "
                f"Die Bruttorendite steigt von {fmt_de(gross_yield, 2)}% auf {fmt_de(potential_gross_yield, 2)}%."
            )
        elif is_cf_negative:
            ana_status = "REMAINS_NEGATIVE"
            badge_text = f"⚠️ Mietspiegel: {fmt_de(potential_monthly_cashflow)} €/M"
            summary_text = (
                f"Mietspiegel-Rentabilität: Selbst bei Erreichen der ortsüblichen Vergleichsmiete von {fmt_de(expected_market_rent)} €/M verbleibt ein Cashflow-Defizit von {fmt_de(potential_monthly_cashflow)} €/M (aktuell {fmt_de(cur_cf)} €/M). Kaufpreisverhandlung oder Zinsoptimierung zwingend erforderlich."
            )
        elif is_above_mietspiegel:
            ana_status = "ABOVE_MIETSPIEGEL"
            diff_pct = abs(rent_upside_percent)
            badge_text = f"ℹ️ Ist-Miete über Mietspiegel (+{fmt_de(diff_pct, 1)}%)"
            summary_text = (
                f"Mietspiegel-Analyse: Die aktuelle Ist-Kaltmiete ({fmt_de(current_rent)} €/M bzw. {fmt_de(current_rent_per_sqm)} €/m²) "
                f"liegt bereits ca. {fmt_de(diff_pct, 1)}% über dem örtlichen Mietspiegel-Benchmark ({fmt_de(expected_market_rent)} €/M bzw. {fmt_de(expected_rent_per_sqm)} €/m²). "
                f"Ein gesetzlicher Mieterhöhungsspielraum nach § 558 BGB besteht aktuell nicht. "
                f"Das Objekt erwirtschaftet mit der bestehenden Kaltmiete einen monatlichen Cashflow von {fmt_de(cur_cf)} €/M. "
                f"Im Rahmen einer konservativen Stresstest-Betrachtung (Absenkung auf den Mietspiegel bei Mieterwechsel) "
                f"verbleibt ein Cashflow von {fmt_de(potential_monthly_cashflow)} €/M ({fmt_signed_de(cf_delta)} €/M Differenz)."
            )
        else:
            ana_status = "UPSIDE_EXPANSION"
            badge_text = f"📈 Mietspiegel: {fmt_de(potential_monthly_cashflow)} €/M"
            summary_text = (
                f"Mietspiegel-Potenzial: Bereits heute cashflow-positiv ({fmt_de(cur_cf)} €/M). "
                f"Bei Anpassung an die ortsübliche Vergleichsmiete ({fmt_de(expected_market_rent)} €/M, {fmt_signed_de(rent_upside_percent, 1)}%) "
                f"steigt der monatliche Überschuss weiter auf {fmt_de(potential_monthly_cashflow)} €/M ({fmt_signed_de(cf_delta)} €/M Hebel)."
            )

        mietspiegel_analysis = {
            "evaluated": True,
            "is_currently_negative": is_cf_negative,
            "turns_profitable": turns_profitable,
            "is_profitable_at_mietspiegel": is_profitable_at_mietspiegel,
            "is_above_mietspiegel": is_above_mietspiegel,
            "current_monthly_rent": current_rent,
            "expected_monthly_rent": expected_market_rent,
            "current_rent_per_sqm": current_rent_per_sqm,
            "expected_rent_per_sqm": expected_rent_per_sqm,
            "mietspiegel_range_sqm": mietspiegel_range_sqm,
            "mietspiegel_size_label": mietspiegel_size_label,
            "package_breakdown": insights.get("package_breakdown"),
            "rent_upside_percent": rent_upside_percent,
            "current_cashflow": cur_cf,
            "mietspiegel_cashflow": potential_monthly_cashflow,
            "cashflow_delta": cf_delta,
            "current_yield": gross_yield,
            "mietspiegel_yield": potential_gross_yield,
            "status": ana_status,
            "badge_text": badge_text,
            "summary": summary_text
        }
    else:
        # Unvermietet / Bezugsfrei / keine Ist-Miete: Kein Altmietvertrag vorhanden, daher entfällt der Turnaround-Hebel
        mietspiegel_analysis = None

    if rent_source in ["EXPOSE_STATED_COLD", "EXPOSE_STATED_RENT", "EXPOSE_STATED_ANNUAL"]:
        pkg_suffix = f" ({insights.get('unit_count_in_offer', 2)} Einheiten)" if insights.get("is_package_deal") else ""
        rent_badge = f"🎯 Exposé-Ist-Miete: {fmt_de(current_rent)} €{pkg_suffix}"
    elif rent_source == "DERIVED_FROM_STATED_YIELD":
        rent_badge = f"🎯 Abgeleitet aus {fmt_de(gross_yield, 2)}% Rendite (Exposé)"
    elif is_actually_rented:
        rent_badge = f"⚠️ Vermietet – Kaltmiete k. A. (Soll-Benchmark: {fmt_de(expected_market_rent)} €)"
    else:
        rent_badge = f"ℹ️ Unvermietet: Marktschätzung {fmt_de(expected_market_rent)} €"

    return {
        "purchase_price": price,
        "living_space": sqm,
        "price_per_sqm": price_per_sqm,
        "market_value": market_val,
        "market_value_per_sqm": market_val_sqm,
        "market_deviation_percent": market_deviation,
        "current_monthly_rent": current_rent,
        "expected_monthly_rent": expected_market_rent,
        "rent_source": rent_source,
        "rent_is_verified": rent_is_verified,
        "is_rented": is_actually_rented,
        "rent_badge": rent_badge,
        "yield_type": yield_type,
        "annual_gross_rent": annual_rent,
        "current_rent_per_sqm": current_rent_per_sqm,
        "expected_rent_per_sqm": expected_rent_per_sqm,
        "mietspiegel_range_sqm": mietspiegel_range_sqm,
        "mietspiegel_size_label": mietspiegel_size_label,
        "gross_yield": gross_yield,
        "actual_gross_yield": actual_gross_yield,
        "potential_gross_yield": potential_gross_yield,
        "real_net_yield": real_net_yield,
        "price_to_rent_ratio": price_to_rent_ratio,
        "rent_upside_percent": rent_upside_percent,
        "ancillary_costs": ancillary_costs,
        "total_capex": total_capex,
        "effective_total_investment": effective_total_investment,
        "monthly_cashflow": cf_data.get("monthly_cashflow", 0.0),
        "potential_monthly_cashflow": potential_monthly_cashflow,
        "monthly_cashflow_with_capex": cf_data_capex.get("monthly_cashflow", cf_data.get("monthly_cashflow", 0.0)),
        "monthly_rate_with_capex": cf_data_capex.get("monthly_rate", cf_data.get("monthly_rate", 0.0)),
        "applied_interest_rate": cf_data.get("interest_rate", 3.93),
        "applied_repayment_rate": cf_data.get("repayment_rate", 2.0),
        "mortgage_bank_name": cf_data.get("mortgage_bank_name", "Dr. Klein Partnerbank"),
        "is_drklein_live_quote": cf_data.get("is_drklein_live_quote", False),
        "drklein_calculator_url": cf_data.get("drklein_calculator_url"),
        "loan_amount": cf_data.get("loan_amount", 0.0),
        "monthly_rate": cf_data.get("monthly_rate", 0.0),
        "additional_costs": cf_data.get("additional_costs", non_rec_monthly),
        "annual_cashflow": cf_data.get("annual_cashflow", 0.0),
        "grunderwerbsteuer_rate": cf_data.get("transfer_tax_rate", 6.5),
        "is_profitable_at_mietspiegel": is_profitable_at_mietspiegel,
        "mietspiegel_analysis": mietspiegel_analysis,
        "rates_by_term": cf_data.get("rates_by_term", [])
    }


# ==============================================================================
# 6. GESAMT-GUTACHTEN ERSTELLUNG (LLM-FIRST HYBRID ENGINE)
# ==============================================================================

def generate_ai_due_diligence_report(
    property_data: Dict[str, Any],
    full_description: str = "",
    details: Optional[Dict[str, Any]] = None,
    api_key: Optional[str] = None,
    image_analysis: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Führt die ganzheitliche KI-Due-Diligence durch (Zahlen, Text & Bilder Fusion):
    1. STUFE 1 (Hard Knockout): Prüft Verfügbarkeit (online/offline) und Datenvollständigkeit (0 Token / Sofortabbruch).
    2. STUFE 2 (Quantitativ & Semantisch): Nutzt LLM und Heuristiken zur Analyse von Hausgeld, WEG-Rücklagen, GEG.
    3. STUFE 3 (Smart Vision Fusion): Gleicht Bauteilzustände und CaPEx gegen vorhandene Fotos ab.
    4. STUFE 4 (Holistic Verdict): Berechnet All-In Rentabilität inkl. Value-Add Hebel für Sanierungsobjekte.
    """
    desc_clean = (full_description or property_data.get("description") or "").strip()
    is_offline = (property_data.get("is_online", 1) == 0 or str(property_data.get("availability_status", "")).lower() == "offline")
    portal_url_check = str(property_data.get("portal_url") or "").lower()
    is_search_url = bool(re.search(r"/(?:suche|search|s-immobilien|s-wohnung-kaufen|s-haus-kaufen)\b", portal_url_check))
    is_missing_desc = (len(desc_clean) < 50 or is_search_url)

    # --------------------------------------------------------------------------
    # STAGE 1: HARD KNOCKOUT GATING (0 Token / Sofortprüfung ohne externe Aufrufe)
    # --------------------------------------------------------------------------
    if is_offline:
        base_metrics = compute_investment_metrics(property_data, {}, {"items": [], "total_capex": 0.0, "has_high_capex": False})
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "engine_version": "llm-v2",
            "used_llm": False,
            "ai_provider": "Knockout-Gate (0 Token – Inserat offline)",
            "token_usage": {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0},
            "score": 1.0,
            "status": "RED",
            "status_label": "🔴 Inserat offline / gelöscht",
            "executive_summary": (
                "Das Inserat ist auf dem Ursprungsportal nicht mehr verfügbar (gelöscht oder beendet). "
                "Eine Due Diligence, Kaufpreisverhandlung und Kaufempfehlung ist ausgeschlossen."
            ),
            "negotiation_target_price": 0.0,
            "recommended_discount": 0.0,
            "metrics": base_metrics,
            "price_discrepancy": None,
            "mietspiegel_analysis": None,
            "location_analysis": None,
            "energy_certificate": None,
            "capex": {"items": [], "total_capex": 0.0, "has_high_capex": False},
            "risks": [{
                "category": "VERFÜGBARKEIT",
                "severity": "CRITICAL",
                "badge": "🔴 Inserat offline",
                "title": "🚨 Angebot nicht mehr verfügbar (Inserat gelöscht / beendet)",
                "description": "Die Immobilie wurde vom Portalbetreiber oder Makler offline genommen. Es können keine weiteren Prüfungen oder Besichtigungen stattfinden.",
                "impact": "Kaufabschluss unmöglich.",
                "action": "Deal archivieren oder nach alternativen Angeboten suchen."
            }],
            "insights": {"is_offline": True, "availability_status": "offline"},
            "description_length": len(desc_clean),
            "knockout_reason": "OFFLINE"
        }

    if is_missing_desc:
        base_metrics = compute_investment_metrics(property_data, {}, {"items": [], "total_capex": 0.0, "has_high_capex": False})
        url_hint = "Die verlinkte Portal-Adresse ist eine allgemeine Suchübersicht statt ein direktes Einzel-Exposé." if is_search_url else "Im Inserat bzw. der Datenbank fehlt der Fließtext der Objektbeschreibung vollständig."
        return {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "engine_version": "llm-v2",
            "used_llm": False,
            "ai_provider": "Knockout-Gate (0 Token – Datenbasis unzureichend)",
            "token_usage": {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0},
            "score": 2.0,
            "status": "RED",
            "status_label": "🔴 Nicht prüfbar (Keine Objektbeschreibung / 0 Zeichen)",
            "executive_summary": (
                f"Für diese Immobilie liegt kein auswertbarer Beschreibungstext vor ({len(desc_clean)} Zeichen). {url_hint} "
                "Ohne Angaben zu Sanierungsstand, WEG-Rücklagen und Mietverhältnissen ist keine seriöse Due Diligence möglich. "
                "Pauschale Mutmaßungen und Kaufempfehlungen werden abgewiesen."
            ),
            "negotiation_target_price": 0.0,
            "recommended_discount": 0.0,
            "metrics": base_metrics,
            "price_discrepancy": None,
            "mietspiegel_analysis": None,
            "location_analysis": None,
            "energy_certificate": None,
            "capex": {"items": [], "total_capex": 0.0, "has_high_capex": False},
            "risks": [{
                "category": "DATENQUALITÄT",
                "severity": "CRITICAL",
                "badge": "🔴 Keine Beschreibung (0 Zeichen)",
                "title": "🚨 Unvollständiges Inserat: Keine Original-Objektbeschreibung vorhanden",
                "description": (
                    f"Es liegt kein verwertbarer Beschreibungstext vor ({len(desc_clean)} Zeichen). {url_hint} "
                    "Das System konnte weder Sanierungsstand, Hausgeld, Mietverträge noch bauliche Mängel prüfen."
                ),
                "impact": "Unkalkulierbares Gesamtrisiko. Sämtliche Berechnungen beruhten rein auf statistischen Mutmaßungen.",
                "action": "Hinterlegen Sie ein konkretes Einzel-Exposé (/expose/...) oder pflegen Sie den Originaltext ein."
            }],
            "insights": {"is_missing_description": True, "description_length": len(desc_clean)},
            "description_length": len(desc_clean),
            "knockout_reason": "MISSING_DESCRIPTION"
        }

    # 1. Baseline deterministic parsing + optional Live LLM semantic extraction
    base_insights = extract_listing_insights(full_description, details or {}, property_data)
    llm_raw = call_llm_text_analysis(full_description, property_data, details=details, api_key=api_key)

    if llm_raw:
        insights = _merge_llm_into_insights(base_insights, llm_raw, property_data)
        insights["used_llm"] = True
        used_llm = True
        ai_provider = llm_raw.get("ai_provider", "Google Gemini Text-KI (Live API-Key)")
    else:
        insights = base_insights
        insights["used_llm"] = False
        used_llm = False
        ai_provider = "Integrierte Immobilien-NLP-Engine (Fallback ohne API-Key)"

    # 2. CaPEx & Investitionskosten (abgeglichen mit vorhandenen Fotos)
    capex = estimate_capex_and_hidden_costs(insights, property_data, image_analysis=image_analysis)

    # 3. Rendite- & Cashflow-Kennzahlen (Ist vs. Soll)
    metrics = compute_investment_metrics(property_data, insights, capex)

    # 4. Versteckte Probleme & Risiken
    risks = detect_hidden_problems_and_risks(insights, property_data, capex)

    # 5. Deterministisches Deal Scoring (1.0 bis 9.9)
    score = 7.5
    if metrics["gross_yield"] >= 7.0:
        score += 1.5
    elif metrics["gross_yield"] >= 5.5:
        score += 0.8

    if metrics["market_deviation_percent"] < -15.0:
        score += 1.2
    elif metrics["market_deviation_percent"] < -5.0:
        score += 0.6

    if insights.get("is_kernsaniert"):
        score += 1.0
    if insights.get("commission_free"):
        score += 0.8

    # Value-Add Bonus: Property turns cashflow-positive at local Mietspiegel benchmark
    miet_ana = metrics.get("mietspiegel_analysis")
    turns_profitable_miet = bool(miet_ana and miet_ana.get("turns_profitable"))
    if turns_profitable_miet:
        score += 0.8

    # Price discrepancy check between portal purchase_price and exposé text
    portal_price = float(metrics.get("purchase_price", property_data.get("purchase_price", 0.0)))
    text_price = float(insights.get("stated_purchase_price_in_text") or 0.0)

    price_discrepancy = None
    if text_price > 0 and portal_price > 0 and abs(text_price - portal_price) >= 5000.0:
        diff = round(text_price - portal_price, 2)
        diff_pct = round((diff / portal_price) * 100.0, 1)
        sign_str = "+" if diff > 0 else ""
        price_discrepancy = {
            "has_discrepancy": True,
            "portal_price": portal_price,
            "text_price": text_price,
            "difference": diff,
            "difference_percent": diff_pct,
            "warning": (
                f"Auffällige Kaufpreis-Diskrepanz: Im Inserats-Kopf / Portal steht {fmt_de(portal_price)} €, "
                f"im Exposé-Text wird jedoch ein Kaufpreis von {fmt_de(text_price)} € genannt "
                f"({sign_str}{fmt_de(diff)} € / {sign_str}{fmt_de(diff_pct, 1)}%)! "
                f"Mögliche Ursachen: Teaser-Preis / Mindestgebot im Bieterverfahren oder redaktioneller Fehler. "
                f"Die Rentabilität sollte mit beiden Kaufpreis-Varianten geprüft werden."
            )
        }
        risks.insert(0, {
            "category": "PREIS-DISKREPANZ",
            "severity": "CRITICAL",
            "title": "🚨 Erhebliche Kaufpreis-Diskrepanz: Portal vs. Exposé-Text!",
            "description": (
                f"Im Portal-Kopf steht {fmt_de(portal_price)} €, im Fließtext der Ausstattung wird jedoch {fmt_de(text_price)} € gefordert "
                f"({sign_str}{fmt_de(diff)} € / {sign_str}{fmt_de(diff_pct, 1)}%). Die tatsächliche Rentabilität muss mit {fmt_de(text_price)} € verifiziert werden."
            ),
            "impact": f"Unkalkulierbare Mehrkosten von {fmt_de(abs(diff))} € oder Lockvogel-Angebot.",
            "action": f"Vor Angebotsabgabe zwingend Makler kontaktieren und verbindlichen Kaufpreis ({fmt_de(text_price)} € vs. {fmt_de(portal_price)} €) klären."
        })

    metrics["price_discrepancy"] = price_discrepancy

    # Data Quality Gate: Check if property has missing or empty description (< 50 chars)
    desc_clean = (full_description or "").strip()
    is_missing_description = (len(desc_clean) < 50)
    portal_url_check = str(property_data.get("portal_url") or "").lower()
    is_search_url = bool(re.search(r"/(?:suche|search|s-immobilien|s-wohnung-kaufen|s-haus-kaufen)\b", portal_url_check))

    if is_missing_description:
        url_hint = "Die verlinkte Portal-Adresse ist eine allgemeine Suchübersicht statt ein direktes Einzel-Exposé." if is_search_url else "Im Inserat bzw. der Datenbank fehlt der Fließtext der Objektbeschreibung vollständig."
        risks.insert(0, {
            "category": "DATENQUALITÄT",
            "severity": "CRITICAL",
            "badge": "🚨 Keine Beschreibung (0 Zeichen)",
            "title": "🚨 Unvollständiges Inserat: Keine Original-Objektbeschreibung vorhanden (0 Zeichen)!",
            "description": (
                f"Für diese Immobilie liegt kein auswertbarer Beschreibungstext vor ({len(desc_clean)} Zeichen). {url_hint} "
                f"Das System konnte weder Sanierungsstand, Hausgeld, Mietverträge noch bauliche Mängel prüfen."
            ),
            "impact": "Sehr hohes Fehleinschätzungsrisiko. Sämtliche CaPEx- und Renditeberechnungen basieren ausschließlich auf theoretischen statistischen Baujahres-Pauschalen ohne Belege.",
            "action": "Hinterlegen Sie ein konkretes Einzel-Exposé (/expose/...) oder pflegen Sie die vollständige Objektbeschreibung manuell ein."
        })

    # --------------------------------------------------------------------------
    # STAGE 4: GANZHEITLICHE ENTSCHEIDUNG (HOLISTIC VERDICT: ZAHLEN, TEXT & BILDER)
    # --------------------------------------------------------------------------
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

    critical_risks = [r for r in risks if _is_true_critical_deal_killer(r)]
    high_risks = [r for r in risks if r["severity"] == "HIGH" or (r.get("severity") == "CRITICAL" and not _is_true_critical_deal_killer(r))]
    score -= len(critical_risks) * 1.5
    score -= len(high_risks) * 0.7

    # Calculate All-In Rentabilität (Kaufpreis + Kaufnebenkosten + Gesamt-CaPEx)
    purchase_price = float(metrics.get("purchase_price", property_data.get("purchase_price", 0.0)))
    total_capex = float(capex.get("total_capex", 0.0))
    ancillary_costs = float(metrics.get("ancillary_costs", 0.0))
    all_in_invest = purchase_price + ancillary_costs + total_capex
    
    living_sqm = float(property_data.get("living_space") or 50.0)
    expected_monthly_rent = float(metrics.get("expected_monthly_rent") or (metrics.get("expected_rent_per_sqm", 10.0) * living_sqm))
    annual_soll_rent = expected_monthly_rent * 12.0
    all_in_gross_yield = (annual_soll_rent / all_in_invest * 100.0) if all_in_invest > 0 else 0.0
    metrics["all_in_investment"] = round(all_in_invest, 2)
    metrics["all_in_gross_yield"] = round(all_in_gross_yield, 2)

    potential_cf = float(metrics.get("potential_monthly_cashflow", metrics.get("monthly_cashflow", 0.0)))
    has_visual_capex = bool(capex.get("visual_capex", 0) > 0 or any(it.get("source_type") == "FOTO_BELEG" for it in capex.get("items", [])))

    # Economic Knockout: Yield < 2.5% AND cashflow < -300 €
    is_economically_toxic = (metrics["gross_yield"] < 2.5 and potential_cf < -300.0 and not turns_profitable_miet)

    # Value-Add Recognition (Renovation Opportunity with Strong Upside)
    # Unrenovated != Bad! If all-in math works out at market rent, reward as Green Value-Add!
    is_value_add = (
        len(critical_risks) == 0
        and not is_economically_toxic
        and (turns_profitable_miet or (potential_cf >= -50.0 and all_in_gross_yield >= 4.5))
        and (has_visual_capex or total_capex >= 5000.0 or metrics.get("market_deviation_percent", 0.0) < -10.0)
    )

    if is_economically_toxic:
        score = min(score, 3.5)
        status = "RED"
        status_label = "🔴 Wirtschaftlich unrentabel (Negativer Cashflow & Niedrigrendite)"
    elif is_value_add:
        # Boost score: Renovation upside is recognized as an investment opportunity!
        score = max(score, 7.8)
        status = "GREEN"
        status_label = "🟢 Value-Add Empfehlung (Hoher Rendite-Hebel nach Sanierung)"
    elif score >= 7.5 and len(critical_risks) == 0:
        status = "GREEN"
        status_label = "🟢 Kaufempfehlung / Solider Deal"
    elif turns_profitable_miet and len(critical_risks) == 0:
        status = "GREEN" if score >= 6.8 else "YELLOW"
        status_label = "🟢 Value-Add Deal (Rentabel bei Mietspiegel)" if score >= 6.8 else "🟡 Value-Add (Rentabel bei Mietspiegel)"
    elif score >= 5.0 or len(critical_risks) <= 1:
        status = "YELLOW"
        status_label = "🟡 Prüfbedarf & Risiken verhandeln"
    else:
        status = "RED"
        status_label = "🔴 Hohes Risiko / Finger weg"

    score = max(1.0, min(9.9, round(score, 1)))

    recommended_discount = round(min(metrics["purchase_price"] * 0.25, capex["total_capex"] * 0.85 + (8000.0 if insights.get("no_reserve_found") else 0.0)), -2)
    negotiation_target_price = max(10000.0, round(metrics["purchase_price"] - recommended_discount, -2))

    # 6. Executive Summary (LLM narrative enriched with verified institutional metrics)
    if used_llm and insights.get("llm_executive_summary"):
        llm_narrative = str(insights["llm_executive_summary"]).strip()
        # Strictly strip out any hallucinated negotiation recommendations, Zielangebote, or CaPEx formulas from the LLM
        llm_narrative = re.sub(r"(?i)\n*VERHANDLUNGSEMPFEHLUNG:.*", "", llm_narrative, flags=re.DOTALL).strip()
        llm_narrative = re.sub(r"(?i)\n*Empfohlenes Kaufpreis-Zielangebot:.*", "", llm_narrative).strip()
        llm_narrative = re.sub(r"(?i)\n*Sprengnetter-Marktwertbenchmark:.*", "", llm_narrative).strip()

        # Append single authoritative, deterministically computed negotiation & Sprengnetter benchmark line
        nego_line = (
            f"VERHANDLUNGSEMPFEHLUNG: Empfohlenes Kaufpreis-Zielangebot: {fmt_de(negotiation_target_price)} € "
            f"(Nachlass ca. {fmt_de(recommended_discount)} € bzw. {fmt_de((recommended_discount / metrics['purchase_price']) * 100, 1)}% "
            f"aufgrund ermitteltem CaPEx/Prüfbedarf von {fmt_de(capex['total_capex'])} €). "
            f"Sprengnetter-Marktwertbenchmark: {fmt_de(metrics['market_value'])} € ({fmt_de(metrics['market_value_per_sqm'])} €/m²)."
        )
        executive_summary = f"{llm_narrative}\n\n{nego_line}"
    else:
        title = property_data.get("title", "Immobilie")
        city = insights.get("corrected_city") or property_data.get("city", "Standort")
        price = metrics["purchase_price"]
        summary_paragraphs = []

        if metrics["rent_is_verified"]:
            summary_paragraphs.append(
                f"Das Objekt '{title}' in {city} wird zu einem Kaufpreis von {fmt_de(price)} € angeboten "
                f"({fmt_de(metrics['price_per_sqm'])} €/m²). Das Objekt ist aktuell vermietet mit einer "
                f"verifizierten Ist-Kaltmiete von {fmt_de(metrics['current_monthly_rent'])} € / Monat ({metrics['rent_badge']}). "
                f"Daraus errechnet sich eine reale Ist-Bruttomietrendite von {fmt_de(metrics['gross_yield'], 2)}% "
                f"und ein monatlicher Netto-Cashflow von ca. {fmt_de(metrics['monthly_cashflow'])} €."
            )
        elif metrics.get("is_rented"):
            summary_paragraphs.append(
                f"Das Objekt '{title}' in {city} wird zu einem Kaufpreis von {fmt_de(price)} € angeboten "
                f"({fmt_de(metrics['price_per_sqm'])} €/m²). Es ist laut Exposé vermietet, ohne dass im Anzeigentext "
                f"eine bezifferte Ist-Miete genannt wird. Über den Sprengnetter-Benchmark wurde eine marktübliche "
                f"Soll-Kaltmiete von {fmt_de(metrics['expected_monthly_rent'])} € / Monat ({fmt_de(metrics['expected_rent_per_sqm'])} €/m²) angesetzt "
                f"({fmt_de(metrics['gross_yield'], 2)}% Soll-Rendite / unverbindliche Benchmark-Schätzung)."
            )
        else:
            summary_paragraphs.append(
                f"Das Objekt '{title}' in {city} wird zu einem Kaufpreis von {fmt_de(price)} € angeboten "
                f"({fmt_de(metrics['price_per_sqm'])} €/m²). Das Objekt ist aktuell unvermietet / bezugsfrei "
                f"(tatsächliche Ist-Miete: 0,00 €). Über den Sprengnetter-Marktbenchmark wurde bei Neuvermietung "
                f"ein Soll-Mietpotenzial von {fmt_de(metrics['expected_monthly_rent'])} € / Monat ({fmt_de(metrics['expected_rent_per_sqm'])} €/m²) ermittelt "
                f"({fmt_de(metrics['gross_yield'], 2)}% Soll-Rendite)."
            )

        if insights.get("commission_free"):
            summary_paragraphs.append(
                f"✅ PROVISIONSFREIER PRIVATVERKAUF: Keine Käuferprovision (0,0 %). Eigenkapital-Ersparnis ca. {fmt_de(price * 0.0357)} €."
            )
        else:
            cpct = float(insights.get("commission_percent", 3.57))
            summary_paragraphs.append(
                f"ℹ️ MAKLERPROVISION: Käuferprovision beträgt {fmt_de(cpct, 2)} % ({fmt_de(price * (cpct / 100.0))} €)."
            )

        if insights.get("has_explicit_hausgeld"):
            hg_note = f"Hausgeld laut Exposé: {fmt_de(insights['hausgeld'])} € / Monat."
            if float(insights["hausgeld"]) > float(metrics["current_monthly_rent"] or 0) > 0:
                hg_note += " ACHTUNG: Hausgeld übersteigt die Ist-Kaltmiete – Abrechnungen dringend prüfen!"
            summary_paragraphs.append(f"ℹ️ BEWIRTSCHAFTUNG: {hg_note}")

        if miet_ana and miet_ana.get("turns_profitable"):
            summary_paragraphs.append(
                f"🚀 MIETSPIEGEL-RENTABILITÄT (VALUE-ADD HEBEL):\n"
                f"{miet_ana.get('summary')}"
            )
        elif miet_ana and miet_ana.get("is_currently_negative") and not miet_ana.get("turns_profitable"):
            summary_paragraphs.append(
                f"⚠️ MIETSPIEGEL-CHECK:\n"
                f"{miet_ana.get('summary')}"
            )
        elif miet_ana and (miet_ana.get("status") == "ABOVE_MIETSPIEGEL" or miet_ana.get("is_above_mietspiegel")):
            summary_paragraphs.append(
                f"ℹ️ MIETSPIEGEL-ABGLEICH (IST-MIETE ÜBER BENCHMARK):\n"
                f"{miet_ana.get('summary')}"
            )
        elif miet_ana:
            summary_paragraphs.append(
                f"📈 MIETSPIEGEL-POTENZIAL:\n"
                f"{miet_ana.get('summary')}"
            )

        if recommended_discount > 0:
            summary_paragraphs.append(
                f"VERHANDLUNGSEMPFEHLUNG: Zielangebot ca. {fmt_de(negotiation_target_price)} € "
                f"(Nachlass {fmt_de(recommended_discount)} € bzw. {fmt_de((recommended_discount / price) * 100, 1)}% "
                f"wegen CaPEx von {fmt_de(capex['total_capex'])} €)."
            )

        executive_summary = "\n\n".join(summary_paragraphs)

    if is_value_add and "VALUE-ADD ANALYSE" not in executive_summary:
        va_block = (
            f"🚀 VALUE-ADD ANALYSE (SANIERUNGSPOTENZIAL):\n"
            f"Die Zustands- und Ausstattungsprüfung zeigt gezielten Modernisierungsbedarf (CaPEx: {fmt_de(total_capex)} €). "
            f"Trotz dieses Sanierungsaufwands erzielt die Immobilie bei marktgerechter Sollmiete ({fmt_de(expected_monthly_rent)} €/M) "
            f"eine hervorragende All-In Rendite von {fmt_de(all_in_gross_yield, 2)}% auf das Gesamtkapital "
            f"({fmt_de(all_in_invest)} € inkl. Kaufnebenkosten & CaPEx). Ein hochattraktives Wertsteigerungsobjekt für aktive Anleger!"
        )
        executive_summary = f"{executive_summary}\n\n{va_block}"

    if is_missing_description:
        warning_header = (
            "⚠️ WICHTIGER QUALITÄTSHINWEIS: Zu dieser Immobilie liegt keine Original-Objektbeschreibung vor "
            f"({len(desc_clean)} Zeichen im Inseratstext). Die hinterlegte Portal-URL ist eine allgemeine Suchübersicht und kein direktes Einzel-Exposé. "
            "Sämtliche Kennzahlen, Risikobewertungen und CaPEx-Kalkulationen basieren ausschließlich auf statistischen Durchschnittswerten "
            f"des Baujahres ({property_data.get('year_built', 'unbekannt')}) und ersetzen keinesfalls eine manuelle Prüfung der Objektunterlagen!\n\n"
        )
        executive_summary = warning_header + executive_summary

    # 7. Micro-Location & Infrastructure Proximity Analysis (Google Maps & POI Distances)
    location_analysis = analyze_property_location_and_pois(
        property_data,
        full_description=full_description,
        insights=insights
    )

    # Clean up temporary raw LLM arrays before storing cleanly in insights
    clean_insights = {k: v for k, v in insights.items() if k not in ["llm_executive_summary", "llm_risk_highlights", "llm_capex_adjustments"]}

    interim_report = {
        "score": score,
        "negotiation_target_price": negotiation_target_price,
        "recommended_discount": recommended_discount,
        "metrics": metrics,
        "capex": capex,
        "risks": risks,
        "insights": clean_insights,
        "image_analysis": image_analysis
    }
    doc_request = build_missing_documents_request(property_data, interim_report)

    tok_usage = llm_raw.get("token_usage") if (llm_raw and isinstance(llm_raw, dict) and "token_usage" in llm_raw) else {"prompt_tokens": 0, "candidates_tokens": 0, "total_tokens": 0}

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "engine_version": "llm-v2",
        "used_llm": used_llm,
        "ai_provider": ai_provider,
        "token_usage": tok_usage,
        "score": score,
        "status": status,
        "status_label": status_label,
        "executive_summary": executive_summary,
        "negotiation_target_price": negotiation_target_price,
        "recommended_discount": recommended_discount,
        "metrics": metrics,
        "price_discrepancy": price_discrepancy,
        "mietspiegel_analysis": miet_ana,
        "location_analysis": location_analysis,
        "energy_certificate": clean_insights.get("energy_certificate"),
        "capex": capex,
        "risks": risks,
        "insights": clean_insights,
        "description_length": len(full_description or ""),
        "missing_documents": doc_request.get("missing_documents", []),
        "doc_checklist": doc_request.get("missing_documents", []),
        "document_request": doc_request
    }


def build_missing_documents_request(
    property_data: Dict[str, Any],
    ai_analysis: Optional[Dict[str, Any]] = None,
    buyer_name: str = "Max Mustermann",
    buyer_email: str = "investor@beispiel.de",
    buyer_phone: str = "+49 170 1234567"
) -> Dict[str, Any]:
    """
    Erzeugt automatisch eine sachliche, objektspezifische Vorab-E-Mail zur Nachforderung
    fehlender Objekt- und Verkaufsunterlagen (Schritt 1 vor dem Letter of Intent).
    """
    analysis = ai_analysis or {}
    insights = analysis.get("insights") or {}
    metrics = analysis.get("metrics") or {}
    capex_items = (analysis.get("capex") or {}).get("items") or []
    img_analysis = analysis.get("image_analysis") or property_data.get("image_analysis") or {}
    if isinstance(img_analysis, str):
        try:
            img_analysis = json.loads(img_analysis)
        except Exception:
            img_analysis = {}

    title = str(property_data.get("title") or "Immobilienangebot").strip()
    postal_code = str(property_data.get("postal_code") or "").strip()
    city = str(insights.get("corrected_city") or property_data.get("city") or "").strip()
    location_label = f"{postal_code} {city}".strip()
    price = float(property_data.get("purchase_price") or 0.0)
    sqm = float(property_data.get("living_space") or 0.0)
    prop_type = str(insights.get("corrected_property_type") or property_data.get("property_type") or "Eigentumswohnung")
    is_house_or_mfh = any(k in prop_type.lower() for k in ["haus", "grundstück", "villa", "mehrfamilien"]) and "wohnung" not in prop_type.lower()

    docs: List[Dict[str, Any]] = []

    # 1. Mietvertrag & Mieteingänge (besonders dringlich bei "vermietet ohne Kaltmiete")
    is_rented = bool(metrics.get("is_rented") or property_data.get("is_rented") or insights.get("is_rented"))
    rent_verified = bool(metrics.get("rent_is_verified"))
    if is_rented and not rent_verified:
        docs.append({
            "id": "lease_missing_rent",
            "label": "Aktueller Mietvertrag & Ist-Kaltmiete (inkl. Nebenkosten & letzter Mietanpassung)",
            "reason": "FEHLT IM EXPOSÉ (als vermietet gemeldet, Kaltmiete k. A.)",
            "priority": "HIGH",
            "checked": True,
            "email_line": "Aktueller Mietvertrag (Angabe der aktuellen monatlichen Nettokaltmiete, Betriebskostenvorauszahlung, Mietbeginn, Kaution sowie Datum der letzten Mieterhöhung nach § 558 BGB)"
        })
        docs.append({
            "id": "rent_payments",
            "label": "Nachweis pünktlicher Mieteingänge (letzte 6 Monate, geschwärzt)",
            "reason": "Ertragsprüfung",
            "priority": "HIGH",
            "checked": True,
            "email_line": "Bestätigung bzw. geschwärzter Zahlungsnachweis über pünktliche Mieteingänge der letzten 6 Monate"
        })
    elif is_rented:
        docs.append({
            "id": "lease_verify",
            "label": "Aktueller Mietvertrag & Mietdauer-Übersicht",
            "reason": "Vertragsprüfung",
            "priority": "MEDIUM",
            "checked": True,
            "email_line": "Aktueller Mietvertrag (zur Überprüfung der Nettokaltmiete, Kautionshaltung und etwaiger Index-/Staffelvereinbarungen)"
        })

    # 2. Paket-Aufschlüsselung
    if insights.get("is_package_deal"):
        docs.append({
            "id": "package_breakdown",
            "label": "Einzelaufstellung je Wohneinheit im Paket (Flächen, Mieten, Hausgelder)",
            "reason": "PAKET-DEAL",
            "priority": "HIGH",
            "checked": True,
            "email_line": "Getrennte Aufstellung für alle Wohneinheiten im Paket (einzelne Wohnflächen, Grundbuchblätter, jeweilige Ist-Kaltmieten und Hausgeldbeträge)"
        })

    # 3. WEG vs. Haus-Unterlagen
    if not is_house_or_mfh:
        hg_missing = not insights.get("has_explicit_hausgeld")
        res_missing = insights.get("no_reserve_found") or float(insights.get("reserve_amount") or 0.0) <= 0.0
        docs.append({
            "id": "hausgeld_plan",
            "label": "Wirtschaftsplan & letzte 2–3 Hausgeldabrechnungen (umlagefähig vs. nicht umlagefähig)",
            "reason": "FEHLT IM EXPOSÉ" if hg_missing else "Bewirtschaftung",
            "priority": "HIGH" if hg_missing else "MEDIUM",
            "checked": True,
            "email_line": "Aktueller Wirtschaftsplan sowie die letzten 2–3 Hausgeldabrechnungen (inkl. Aufschlüsselung in umlagefähige und nicht umlagefähige Kosten)"
        })
        docs.append({
            "id": "weg_reserve_protocols",
            "label": "WEG-Protokolle (letzte 3 Jahre), Beschlusssammlung & Stand Instandhaltungsrücklage",
            "reason": "FEHLT / SONDERUMLAGEN-CHECK" if (res_missing or insights.get("missing_protocols") or insights.get("special_assessment")) else "WEG-Prüfung",
            "priority": "HIGH" if (res_missing or insights.get("missing_protocols") or insights.get("special_assessment")) else "MEDIUM",
            "checked": True,
            "email_line": "Protokolle der letzten 3 Eigentümerversammlungen inkl. Beschlusssammlung (§ 24 Abs. 7 WEG) sowie aktueller Stand der Instandhaltungsrücklage (inkl. Information zu geplanten oder beschlossenen Sonderumlagen)"
        })
        docs.append({
            "id": "teilungserklaerung",
            "label": "Teilungserklärung, Aufteilungsplan & aktueller Grundbuchauszug (Abt. I–III)",
            "reason": "Standard-Ankauf",
            "priority": "MEDIUM",
            "checked": True,
            "email_line": "Teilungserklärung inkl. Gemeinschaftsordnung/Aufteilungsplan sowie ein aktueller Grundbuchauszug (Abteilungen I, II und III)"
        })
    else:
        docs.append({
            "id": "land_docs",
            "label": "Aktueller Grundbuchauszug, Flurkarte, Baulastenverzeichnis & Gebäudeversicherung",
            "reason": "Standard-Ankauf",
            "priority": "HIGH",
            "checked": True,
            "email_line": "Aktueller Grundbuchauszug (Abt. I–III), amtliche Flurkarte/Liegenschaftskataster, Auskunft aus dem Baulastenverzeichnis sowie aktuelle Wohngebäudeversicherungs-Police"
        })

    # 4. Energieausweis (§ 80 GEG)
    ec = str(insights.get("energy_class") or "")
    energy_missing = (not ec) or ("geschätzt" in ec.lower()) or (insights.get("cert_type") == "Unbekannt")
    if energy_missing or ec in ["F", "G", "H"]:
        docs.append({
            "id": "energy_cert",
            "label": "Gültiger Energieausweis (§ 80 GEG)",
            "reason": "FEHLT IM EXPOSÉ (§ 80 GEG)" if energy_missing else f"GEG-Check (Klasse {ec})",
            "priority": "HIGH" if energy_missing else "MEDIUM",
            "checked": True,
            "email_line": "Gültiger Energieausweis (Bedarfs- oder Verbrauchsausweis gemäß § 80 GEG)"
        })

    # 5. Sanierungs-/Techniknachweise aus Baujahrs-Mutmaßungen
    guessed_trades = []
    for c_item in capex_items:
        if c_item.get("source_type") == "BAUJAHR_MUTMASSUNG":
            t_low = str(c_item.get("title", "")).lower()
            if "elektrik" in t_low:
                guessed_trades.append("Elektrik / Unterverteilung mit FI-Schutzschalter")
            elif "fenster" in t_low:
                guessed_trades.append("Fenster (Einbaujahr / Verglasung)")
            elif "bad" in t_low or "sanitär" in t_low:
                guessed_trades.append("Bad / Steigleitungen")
            elif "heiz" in t_low:
                guessed_trades.append("Heizungsanlage / Baujahr Brenner")
    guessed_trades = list(dict.fromkeys(guessed_trades))
    if guessed_trades or any(c.get("source_type") == "BAUJAHR_MUTMASSUNG" for c in capex_items):
        spec_list = ", ".join(guessed_trades) if guessed_trades else "Elektrik/Sicherungskasten, Steigleitungen, Heizung und Fenster"
        docs.append({
            "id": "renovation_proof",
            "label": f"Sanierungs- & Modernisierungsnachweise ({spec_list})",
            "reason": "MUTMASSUNG PRÜFEN",
            "priority": "HIGH",
            "checked": True,
            "email_line": f"Kurze Aufstellung bzw. Rechnungsnachweise über bereits erfolgte Modernisierungen (insbesondere zu {spec_list})"
        })

    # 6. Grundriss & Wohnflächenberechnung
    has_bp = any(bool(p.get("is_blueprint")) for p in (img_analysis.get("analyzed_photos") or []))
    docs.append({
        "id": "floorplan_area",
        "label": "Maßstabsgerechter Grundriss & Wohnflächenberechnung",
        "reason": "FEHLT IN GALERIE" if not has_bp else "Bankenprüfung",
        "priority": "MEDIUM" if not has_bp else "LOW",
        "checked": not has_bp,
        "email_line": "Maßstabsgerechter Grundriss sowie offizielle Wohnflächenberechnung (für die Bankenbewertung)"
    })

    subject = f"Objektunterlagen & Vorabanfrage: {title}" + (f" ({location_label})" if location_label else "")
    bullet_lines = "\n".join(f"{idx + 1}. {d['email_line']}" for idx, d in enumerate(docs) if d.get("checked"))

    email_body = (
        f"Sehr geehrte Damen und Herren,\n\n"
        f"vielen Dank für Ihr Immobilienangebot „{title}“"
        + (f" in {location_label}" if location_label else "")
        + f" ({fmt_de(sqm, 0)} m² Wohnfläche, Angebotspreis {fmt_de(price)} €).\n"
        f"Das Objekt passt sehr gut in mein Ankaufsprofil – eine entsprechende Finanzierungsbestätigung bzw. ein Eigenkapitalnachweis liegt bereits vor.\n\n"
        f"Bevor ich Ihnen im nächsten Schritt ein konkretes Kaufangebot (Letter of Intent) übermittle, bitte ich zur abschließenden kaufmännischen Prüfung vorab um Übersendung folgender noch offener Objektunterlagen:\n\n"
        f"{bullet_lines}\n\n"
        f"Digitale Scans oder kurze E-Mail-Auskünfte genügen mir zunächst vollkommen. Nach Durchsicht der Unterlagen melde ich mich kurzfristig zwecks Besichtigung bzw. konkretem Kaufangebot bei Ihnen.\n\n"
        f"Vielen Dank vorab für Ihre Unterstützung!\n\n"
        f"Mit freundlichen Grüßen\n"
        f"{buyer_name}\n"
        f"Telefon: {buyer_phone}\n"
        f"E-Mail: {buyer_email}"
    )

    for d in docs:
        if "email_line" in d and "emailLine" not in d:
            d["emailLine"] = d["email_line"]

    return {
        "subject": subject,
        "email_text": email_body,
        "missing_documents": docs
    }
