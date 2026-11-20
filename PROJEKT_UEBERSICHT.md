# ImmoMetrica Pro – Projektübersicht

Stand: 17. November 2026  
Projektpfad: `C:\Users\User\.gemini\antigravity\scratch\immometrica-clone`

---

## 1. Ziel des Projekts

Entwicklung einer autarken, professionellen Immobilien-Investoren-Plattform (**ImmoMetrica Pro Clean-Room Clone**) zur datengestützten Akquise und automatisierten Due-Diligence von Anlageimmobilien:

* **Aggregierte Metasuche über 40+ Portale:** Bündelung von Inseraten privater und gewerblicher Marktplätze (Kleinanzeigen, ImmoScout24, Immowelt) sowie ZVG-Versteigerungen.
* **Sprengnetter-Marktwertprüfung & Mietpotenzial:** Sofortige Gegenüberstellung von Angebotspreisen mit realen Gutachterausschuss-Daten (Unter-Marktwert-Indikator) sowie Soll- vs. Ist-Mietspiegel.
* **DEKRA-zertifizierte KI-Bild- & Bauteilanalyse:** Multimodale Prüfung aller Exposé-Fotos mit Gemini Vision zur Erkennung von Mängeln, Sanierungsstau und BKI-gestützter CaPEx-Kalkulation (DIN 276) inkl. Bildbelegen.
* **Steuer- & AfA-Hebel (§ 7 Abs. 4 EStG):** Dynamische Berechnung von Cashflow vor/nach Steuern, Restnutzungsdauer-Effekten und 15-%-Grenze bei Sanierungen.
* **End-to-End-Ankaufsworkflow:** 7-stufiges Kanban-Dealboard, automatische Unterlagen-Anforderungs-E-Mail und rechtssicherer Letter of Intent (LOI).
* **Model Context Protocol (MCP) Server:** Native Schnittstelle zur direkten Steuerung durch externe AI-Agenten (Claude Desktop, Cursor, Antigravity).

---

## 2. Aktueller Stand

### Frontend & Benutzeroberfläche
* **Dealfeed & Metasuche:** Vollständige Filterleiste (Rendite, Kaufpreis, Unter-Marktwert, Stadt, Umkreis), Tabellen- und Kachelansicht, Live-Status-Prüfung und Duplikatsentfernung.
* **Info & Anleitung:** Erfolgreich strukturiert mit integrierter Unterseiten-Navigation:
  1. *System-Handbuch & Leitfaden:* Detaillierte Funktionsweise der Portale, Sprengnetter-Benchmarks, AfA-Steuerlogik und Investoren-FAQ.
  2. *Model Context Protocol (MCP Server):* API-Token-Verwaltung, Ein-Klick-Kopieren der Konfigurations-JSON für Claude/Cursor und Übersicht der 10 MCP-Tools.
* **Navigation bereinigt:** Header-Leiste aufgeräumt, redundanter MCP-Button entfernt, Cache-Buster (`Cache-Control: no-cache`, `app.js?v=2.2`) serverseitig aktiviert.

### KI & Bildanalyse-Pipeline
* **Vollständige Galerieauswertung:** Alle verfügbaren Fotos eines Inserats werden analysiert (Begrenzung auf Schlüsselbilder vollständig entfernt).
* **Semantischer Tag-Refiner:** Automatischer Motivabgleich verhindert Fehlzuordnungen. Fassaden-, Straßen- und Hofansichten werden strikt von Innenräumen/Bädern getrennt; reale Freisitze werden sauber als `🌿 Balkon / Loggia` klassifiziert.
* **Datenbank synchron:** Alle 53 in der SQLite-Datenbank vorhandenen Deals wurden auf die korrigierten Raum-Tags migriert.

### Backend, MCP & Rechner
* **FastAPI Backend:** Alle 13 automatisierten Systemtests in [`test_endpoints.py`](file:///C:/Users/User/.gemini/antigravity/scratch/immometrica-clone/test_endpoints.py) und [`test_photo_classification_accuracy.py`](file:///C:/Users/User/.gemini/antigravity/scratch/immometrica-clone/test_photo_classification_accuracy.py) laufen fehlerfrei durch.
* **Geo- & Steuer-Engine:** Automatische Ermittlung von Bundesland und Grunderwerbsteuersatz aus der Postleitzahl.
* **Suchagent & Deal-Alerts:** Hintergrund-Crawler mit Multi-Suchprofilen und Benachrichtigungsvorbereitung für Telegram.

---
