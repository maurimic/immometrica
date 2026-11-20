# ImmoMetrica Clean-Room Clone & Forensic Reverse-Engineering

Dieses Projekt ist eine vollständige, saubere **Clean-Room Rekonstruktion** und Analyse der Plattform **ImmoMetrica** (`immometrica.de` / `immometrica.com`).

---

## 🔍 Forensische Analyse: Wie funktioniert ImmoMetrica?

Unsere Deobfuskation und Source-Analyse der Produktions-Bundles (`bundle_v2.js`, `v2.8d522247b4.css`, API-Routen und n8n-Nodes) hat die exakte Architektur aufgedeckt:

### 1. Architektur- & Tech-Stack
* **Frontend-Architektur:** 
  * Server-driven UI mit **HTMX** (`hx-swap`, `hx-get`, `hx-post`) für verzögerungsfreie Teil-Aktualisierungen und Infinite Scrolling.
  * Reaktivität auf Komponentenebene über **Alpine.js v3** mit `@vue/reactivity` Core (insgesamt 47 registrierte Module: `cashflowCalculator`, `locationPicker`, `offerMap`, `mcpSetup`, `zvgFilter`, `priceBands`, `tagEditor`, etc.).
  * Styling: Maßgeschneidertes **Tailwind CSS** mit Schriftart `Poppins` und den Markenfarben `#1e73b3` (ImmoMetrica Blau), `#1a7f47` (Positiver Cashflow) und `#b3261e` (Negativer Cashflow).
  * Kartendienst: **MapLibre GL** mit Open-Source Vektortiles über **OpenFreeMap** (`tiles.openfreemap.org`) ohne Tracking oder Drittanbieter-Cookies.
* **Backend-Architektur:** 
  * Python / Django mit `django-allauth`, WebAuthn / Passkeys (`@github/webauthn-json`), 2-Faktor-Authentifizierung und CSRF-Protection.
  * Hintergrund-Verarbeitung via Celery / Redis für asynchrone Portal-Scraper, Preisüberwachung und E-Mail-/Telegram-Benachrichtigungen.
  * Datenbank: PostgreSQL + PostGIS für Geokoordinaten und Polygon-Suche.
* **Sicherheit & Bot-Schutz:**
  * Eigenes Proof-of-Work CAPTCHA (`pow_captcha.js` / Web Worker) anstelle von Google reCAPTCHA, um strikte DSGVO-Konformität und Content-Security-Policy (CSP) zu garantieren.

---

### 2. Datenpipeline & 40+ Portale Metasuche
ImmoMetrica aggregiert Inserate aus über 40 Quellen im DACH-Raum:
* **Große Immobilienportale:** ImmoScout24, Immowelt, Immonet, Kleinanzeigen (ehemals eBay Kleinanzeigen).
* **Behördliche Versteigerungen:** Offizielles Justizportal der Länder für Zwangsversteigerungen (**ZVG-Portal**).
* **Regionale & Zeitungsportale:** Quoka, Kalaydo, MeineStadt, 1A-Immobilienmarkt sowie Makler-Schnittstellen.
* **Deduplizierungs-Engine:** Erkennt Mehrfachinserate derselben Immobilie auf verschiedenen Portalen über Hash-Vergleiche von Wohnfläche, Zimmeranzahl, Adresse/Geokoordinaten und Bild-Hashing.
* **Preishistorie & Preissenkungs-Detektor:** Protokolliert jeden Preiswechsel seit dem ersten Inseratstag (`firstSeenDate`, `daysOnline`). Preissenkungen werden visuell hervorgehoben (z.B. *-11% gesenkt*), da sie Verhandlungsspielraum signalisieren.

---

### 3. Bewertungsmodell (Sprengnetter-Standard)
Die Renditen und Marktwert-Einschätzungen basieren auf einem automatisierten Bewertungsmodell (AVM) nach dem **Sprengnetter-Verfahren**:
* **Marktwert/m²:** Lokaler Benchmark angepasst an Baujahr, Objektzustand (Saniert, Gepflegt, Renovierungsbedürftig), Vermietungsstatus (vermietete Einheiten haben typischerweise 10–15% Marktwert-Abschlag) und Objekttyp.
* **Marktwert-Abweichung (`marketDeviationPercent`):** 
  $$\text{Abweichung} = \frac{\text{Kaufpreis} - \text{Marktwert}}{\text{Marktwert}} \times 100\%$$
  Liegt der Wert unter -5%, gilt die Immobilie als **unter Marktwert** (Schnäppchen).
* **Mietspiegel (Soll-Miete/m²):** Regionale Richtwerte ermitteln die erzielbare Marktmiete bei Neuvermietung oder Mieterhöhung.

---

### 4. Finanzierungs- & Cashflow-Formeln (`cashflowCalculator`)
Die exakte Formel aus dem dekompilierten JavaScript-Quellcode:

1. **Kaufnebenkosten:**
   * Notar & Grundbuch: $2{,}0\%$
   * Grunderwerbsteuer: nach Bundesland ($3{,}5\%$ in Bayern bis $6{,}5\%$ in NRW/Thüringen/Brandenburg)
   * Maklerprovision: $0\%$ (Privat/ZVG) bis $3{,}57\%$ inkl. MwSt.
2. **Darlehensbetrag (Kredit):**
   $$\text{Kredit} = \text{Kaufpreis} - \text{Eigenkapital} + \text{Nebenkosten (falls finanziert)} + \text{Sanierungskosten}$$
3. **Monatliche Bankrate (Annuität):**
   $$\text{Rate} = \frac{\text{Kredit} \times (\text{Zinssatz} + \text{Tilgung})}{12}$$
4. **Monatlicher Netto-Cashflow vor Steuern:**
   $$\text{Cashflow} = \text{Kaltmiete} - \text{Bankrate} - \text{Nicht umlegbare Bewirtschaftungskosten (Hausgeld/Rücklage)}$$

---

### 5. KI- & Agenten-Integration: Model Context Protocol (MCP)
ImmoMetrica bietet Unterstützung für das **Model Context Protocol (MCP)**, womit AI-Assistenten (Claude Desktop, Cursor, Antigravity) Deals und Berechnungen direkt abfragen können:
* `search_deals`: Filtert nach Rendite, Stadt, positivem Cashflow und Unter-Marktwert.
* `calculate_cashflow`: Berechnet Nebenkosten, Bankrate und Cashflow live.
* `get_deal_details`: Liefert ALKIS-Flurstücke, Lage und Vermarktungsdauer.
* `get_city_benchmarks`: Liefert regionale m²-Preise und Durchschnittsrenditen.

---

## 🚀 Schnellanleitung: Klon starten

### 1. Starten des Servers
Öffne ein Terminal im Projektverzeichnis und starte den Server:
```bash
python run.py
```

### 2. Zugriff im Browser
* **Web-App:** [http://127.0.0.1:8000](http://127.0.0.1:8000)
* **Interaktive Swagger API-Dokumentation:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
* **MCP Tools Endpunkt:** [http://127.0.0.1:8000/mcp/tools](http://127.0.0.1:8000/mcp/tools)
* **CSV Export:** [http://127.0.0.1:8000/api/export/csv](http://127.0.0.1:8000/api/export/csv)

---

## 📁 Projektstruktur
```
immometrica-clone/
├── app/
│   ├── main.py              # FastAPI Webserver & Router-Initialisierung
│   ├── database.py          # SQLite Schema & Seed-Daten für deutsche Städte
│   ├── calculator.py        # Reverse-engineered Cashflow & Annuitäten-Engine
│   ├── valuation.py         # Sprengnetter-Marktwert- & Mietspiegel-Modell
│   ├── mcp_server.py        # Model Context Protocol Server für AI-Agenten
│   └── routers/
│       ├── properties.py    # Suchfilter, Details, Favoriten
│       ├── pipeline.py      # Kanban Dealboard CRM (7 Phasen)
│       ├── calculations.py  # Live-Berechnungs-Endpunkte
│       └── export.py        # Excel-kompatibler CSV-Export
├── static/
│   ├── css/style.css        # Poppins Font, Kanban Lanes & KPI Badges
│   ├── js/app.js            # Alpine.js Reaktivität & MapLibre Controller
│   └── index.html           # Gesamte ImmoMetrica Benutzeroberfläche
├── test_endpoints.py        # Automatisierte End-to-End Tests
└── run.py                   # One-Click Startskript
```
