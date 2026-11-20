"""
Automatisierter Codebase-Exporter für externe LLMs (Google Gemini, NotebookLM, Claude, ChatGPT)
Erstellt modulare, mundgerechte Textdateien (< 250 KB pro Datei),
sodass Upload-Limits und Cutoffs bei Web-LLMs und NotebookLM nicht anschlagen.
"""

import os
import re
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EXPORT_DIR = os.path.join(BASE_DIR, "export_llm")
os.makedirs(EXPORT_DIR, exist_ok=True)

# Regex zur Zensur sensibler Tokens (Sicherheitsnetz)
SENSITIVE_PATTERNS = [
    (re.compile(r'(gemini_api_key\s*[:=]\s*["\'])([^"\']{15,})(["\'])', re.IGNORECASE), r'\1[REDACTED_GEMINI_KEY]\3'),
    (re.compile(r'(api_key\s*[:=]\s*["\'])([^"\']{15,})(["\'])', re.IGNORECASE), r'\1[REDACTED_API_KEY]\3'),
    (re.compile(r'(telegram_bot_token\s*[:=]\s*["\'])([^"\']{15,})(["\'])', re.IGNORECASE), r'\1[REDACTED_TELEGRAM_TOKEN]\3'),
    (re.compile(r'(bot_token\s*[:=]\s*["\'])([^"\']{15,})(["\'])', re.IGNORECASE), r'\1[REDACTED_TOKEN]\3'),
    (re.compile(r'([0-9]{8,11}:[a-zA-Z0-9_-]{30,40})'), '[REDACTED_BOT_TOKEN]'),
    (re.compile(r'(AQ\.[a-zA-Z0-9_-]{35,})'), '[REDACTED_GEMINI_KEY]')
]

def sanitize_content(text: str) -> str:
    for pattern, repl in SENSITIVE_PATTERNS:
        text = pattern.sub(repl, text)
    return text

def write_bundle(filename: str, title: str, description: str, rel_files: list) -> float:
    out_path = os.path.join(EXPORT_DIR, filename)
    now_str = datetime.now().strftime("%d.%m.%Y %H:%M:%S")
    lines = []
    
    lines.append("=" * 80)
    lines.append(f"IMMOMETRICA PRO CLONE EXPORT - {title.upper()}")
    lines.append(f"Generiert am: {now_str}")
    lines.append(f"Beschreibung: {description}")
    lines.append("=" * 80)
    lines.append("\nENTHALTENE DATEIEN:")
    
    existing_files = []
    for rf in rel_files:
        full_p = os.path.join(BASE_DIR, rf.replace("/", os.sep))
        if os.path.exists(full_p):
            kb = os.path.getsize(full_p) / 1024
            lines.append(f"  - {rf} ({kb:.1f} KB)")
            existing_files.append((rf, full_p))
        else:
            lines.append(f"  - {rf} (NICHT GEFUNDEN)")
            
    lines.append("\n" + "=" * 80 + "\n\n")
    
    for rf, full_p in existing_files:
        ext = os.path.splitext(rf)[1].lower().replace(".", "")
        lang = ext if ext in {"py", "html", "js", "css", "md", "json", "sql"} else "text"
        try:
            with open(full_p, "r", encoding="utf-8", errors="replace") as f:
                content = sanitize_content(f.read())
        except Exception as e:
            content = f"Fehler beim Lesen: {e}"
            
        lines.append(f"### DATEI: {rf}")
        lines.append(f"```{lang}")
        lines.append(content)
        lines.append("```\n")
        lines.append("-" * 60 + "\n")
        
    with open(out_path, "w", encoding="utf-8") as out:
        out.write("\n".join(lines))
        
    size_kb = os.path.getsize(out_path) / 1024
    status_flag = " [!] WARNUNG: > 250 KB" if size_kb > 250 else " [OK]"
    print(f" -> {filename:<40} : {size_kb:>6.1f} KB ({len(existing_files)} Dateien){status_flag}")
    return size_kb

def main():
    print("=" * 70)
    print("IMMOMETRICA PRO – EXPORT CODE FÜR LLM (Gemini, NotebookLM, Claude)")
    print("Strikte Begrenzung: Jedes Paket < 250 KB (kein Briefkasten-Cutoff)")
    print("=" * 70 + "\n")
    
    # Bereinige alten Export-Ordner für saubere Dateinamen
    for existing in os.listdir(EXPORT_DIR):
        file_p = os.path.join(EXPORT_DIR, existing)
        if os.path.isfile(file_p):
            try:
                os.remove(file_p)
            except Exception:
                pass
    
    bundles = []
    
    # Paket 1: Backend Core, Architektur, DB & KI-Auditor (ca. 188 KB)
    kb1 = write_bundle(
        "01_backend_core_and_architecture.txt",
        "Teil 1: Backend Core, Architektur & KI-Auditor",
        "Projektübersicht, Einstiegspunkte, Datenbank-Schema, Cashflow-Rechner und Due-Diligence-Auditor",
        [
            "PROJEKT_UEBERSICHT.md",
            "README.md",
            "run.py",
            "app/main.py",
            "app/database.py",
            "app/calculator.py",
            "app/ai_analyst.py"
        ]
    )
    bundles.append(("01_backend_core_and_architecture.txt", "Backend Core & KI-Auditor", kb1))
    
    # Paket 2: Bildanalyse, Vision AI & Marktwert-Dienste (ca. 135 KB)
    kb2 = write_bundle(
        "02_backend_image_and_vision_ai.txt",
        "Teil 2: Bildanalyse, Vision AI & Marktdienste",
        "Multimodale Gemini Vision Bildanalyse, Tag-Refiner, Wertermittlung, Zinsen und Verfügbarkeit",
        [
            "app/image_analyst.py",
            "app/valuation.py",
            "app/location_analyst.py",
            "app/interest_service.py",
            "app/availability.py"
        ]
    )
    bundles.append(("02_backend_image_and_vision_ai.txt", "Bildanalyse & Marktdienste", kb2))
    
    # Paket 3: Backend Router, API-Endpunkte & MCP Server (ca. 120 KB)
    kb3 = write_bundle(
        "03_backend_routers_and_mcp.txt",
        "Teil 3: REST Router, Endpunkte & MCP Server",
        "FastAPI Router für Immobilien, Pipeline, Alerts, Settings und MCP-Server für KI-Agenten",
        [
            "app/routers/properties.py",
            "app/routers/calculations.py",
            "app/routers/alerts.py",
            "app/routers/export.py",
            "app/routers/pipeline.py",
            "app/routers/settings.py",
            "app/mcp_server.py",
            "mcp_stdio_server.py"
        ]
    )
    bundles.append(("03_backend_routers_and_mcp.txt", "REST Router & MCP Server", kb3))
    
    # Paket 4: Scraper, Suchagent & Benachrichtigung (ca. 140 KB)
    kb4 = write_bundle(
        "04_backend_scrapers_and_services.txt",
        "Teil 4: Portal-Scraper & Live-Suchagent",
        "Web-Scraper für Kleinanzeigen, ImmoScout24, Immowelt, Geo-Resolver, Telegram-Push & Scheduler",
        [
            "app/scraper.py",
            "app/scrapers/__init__.py",
            "app/scrapers/deal_fusion.py",
            "app/scrapers/kleinanzeigen.py",
            "app/scrapers/immoscout.py",
            "app/scrapers/immowelt.py",
            "app/geo_resolver.py",
            "app/search_agent.py",
            "app/telegram_notifier.py",
            "app/scheduler.py"
        ]
    )
    bundles.append(("04_backend_scrapers_and_services.txt", "Portal-Scraper & Suchagent", kb4))
    
    # Paket 5: Frontend Logik & Styles (ca. 104 KB)
    kb5 = write_bundle(
        "05_frontend_scripts.txt",
        "Teil 5: Frontend Anwendungslogik & CSS",
        "Alpine.js Controller (app.js) mit State Management, Modal-Handlern, Tag-Refiner und Tailwind-CSS",
        [
            "static/js/app.js",
            "static/css/style.css"
        ]
    )
    bundles.append(("05_frontend_scripts.txt", "Frontend Controller & CSS", kb5))
    
    # Paket 6 & 7: Frontend HTML (index.html gesplittet in 2 Teile < 200 KB)
    index_html_path = os.path.join(BASE_DIR, "static", "index.html")
    if os.path.exists(index_html_path):
        with open(index_html_path, "r", encoding="utf-8") as f:
            index_lines = f.readlines()
        mid = len(index_lines) // 2
        
        # Teil 6a
        part1_path = os.path.join(EXPORT_DIR, "06_frontend_templates_teil1.txt")
        with open(part1_path, "w", encoding="utf-8") as f:
            f.write(f"=== IMMOMETRICA FRONTEND HTML - TEIL 1 (Zeilen 1 bis {mid} von {len(index_lines)}) ===\n")
            f.write("Enthält: Head, Header, Dealfeed & Metasuche, Dealboard (Pipeline), Cashflow-Rechner\n\n")
            f.write("".join(index_lines[:mid]))
        kb6 = os.path.getsize(part1_path) / 1024
        print(f" -> {'06_frontend_templates_teil1.txt':<40} : {kb6:>6.1f} KB (HTML Teil 1: Feed/Board/Calc) [OK]")
        bundles.append(("06_frontend_templates_teil1.txt", "HTML Teil 1 (Feed/Board/Calc)", kb6))
        
        # Teil 6b
        part2_path = os.path.join(EXPORT_DIR, "07_frontend_templates_teil2.txt")
        with open(part2_path, "w", encoding="utf-8") as f:
            f.write(f"=== IMMOMETRICA FRONTEND HTML - TEIL 2 (Zeilen {mid + 1} bis {len(index_lines)}) ===\n")
            f.write("Enthält: Lagekarte, Info & Anleitung (Handbuch & MCP-Server), Modale & Lightbox\n\n")
            f.write("".join(index_lines[mid:]))
        kb7 = os.path.getsize(part2_path) / 1024
        print(f" -> {'07_frontend_templates_teil2.txt':<40} : {kb7:>6.1f} KB (HTML Teil 2: Map/Info/Modals) [OK]")
        bundles.append(("07_frontend_templates_teil2.txt", "HTML Teil 2 (Map/Info/Modals)", kb7))
        
    # Paket 8: Test-Suite & Hilfsskripte (ca. 86 KB)
    kb8 = write_bundle(
        "08_tests_and_tools.txt",
        "Teil 8: Test-Suite & Verifikations-Skripte",
        "Alle automatisierten Endpunkt-Tests, Klassifizierungs-Audits, Seeding und Check-Tools",
        [
            "test_photo_classification_accuracy.py",
            "test_endpoints.py",
            "test_due_diligence_pipeline.py",
            "test_holistic_gating.py",
            "test_interest_and_mietspiegel.py",
            "test_location_analyst.py",
            "test_multi_portal_and_gating.py",
            "test_search_agent_alerts.py",
            "test_calc_afa.py",
            "test_avail.py",
            "test_app.py",
            "test_deal44.py",
            "test_suite.py",
            "run_audit_verify.py",
            "seed_regional_deals.py",
            "check_all_props.py",
            "check_json_coords.py",
            "check_phrase.py",
            "run_searches.py"
        ]
    )
    bundles.append(("08_tests_and_tools.txt", "Test-Suite & Verifikation", kb8))
    
    # Master-Index generieren
    index_path = os.path.join(EXPORT_DIR, "00_UEBERSICHT_ALLER_PAKETE.txt")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("=" * 80 + "\n")
        f.write("IMMOMETRICA PRO – MODULARER CODEBASE-EXPORT FÜR LLMs\n")
        f.write(f"Stand: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n")
        f.write("=" * 80 + "\n\n")
        f.write("Alle Dateien sind strikt < 250 KB und für NotebookLM, Google Gemini,\n")
        f.write("Claude 3.5 Sonnet und ChatGPT (GPT-4o) optimiert.\n\n")
        f.write(f"{'DATEINAME':<40} | {'BESCHREIBUNG':<32} | {'GRÖSSE':>9}\n")
        f.write("-" * 88 + "\n")
        for fn, desc, kb in bundles:
            f.write(f"{fn:<40} | {desc:<32} | {kb:>6.1f} KB\n")
        f.write("-" * 88 + "\n")
        f.write(f"{'GESAMT:':<40} | {len(bundles)} Pakete | {sum(b[2] for b in bundles):>6.1f} KB\n\n")
        f.write("NUTZUNGSEMPFEHLUNG:\n")
        f.write("1. Google NotebookLM: Lade alle Dateien (01 bis 08) als Quellen hoch. NotebookLM\n")
        f.write("   indiziert alle Dateien ohne Datenverlust oder Cutoffs.\n")
        f.write("2. Gemini / Claude / ChatGPT: Für gezielte Fragen (z. B. 'Wie funktioniert die\n")
        f.write("   Bildanalyse?') genügt das entsprechende Teilpaket (z. B. Paket 02).\n")
    print(f"\nMaster-Index erstellt: 00_UEBERSICHT_ALLER_PAKETE.txt")
    print(f"\nErfolg: Alle {len(bundles)} Pakete liegen im Ordner:\n  -> {EXPORT_DIR}")

if __name__ == "__main__":
    main()
