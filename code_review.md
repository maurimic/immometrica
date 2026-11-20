Als Python-Architekt habe ich den vorliegenden Projektcode (Fokus `ai_analyst.py`, `availability.py`, `calculator.py`, `database.py`, `geo_resolver.py`, `image_analyst.py`, `interest_service.py`, `location_analyst.py`, `main.py`, `mcp_server.py`, `scheduler.py`, `scraper.py`) einer kritischen PrÃ¼fung unterzogen.

### 1. Kritische Bugs & Logikfehler

*   **`ai_analyst.py` (Doppel-Dekodierung/Encoding):** In `fmt_de` wird mit `__TMP__` hantiert, was bei unerwarteten Eingaben (z.B. `"1.000,00"`) instabil ist.
    *   *LÃ¶sung:* Nutze `babel.numbers.format_decimal(value, locale='de_DE')`.
*   **`scraper.py` (Threading):** `scrape_multi_portal_deals` nutzt `ThreadPoolExecutor` ohne PrÃ¼fung, ob die Thread-Pool-GrÃ¶Ãe in einer asyncio-Umgebung (FastAPI) blockierend wirkt.
    *   *LÃ¶sung:* Nutze `await asyncio.to_thread` fÃ¼r IO-lastige Aufgaben, statt manuelle Threads zu verwalten.
*   **`database.py` (Race Conditions):** `get_connection` nutzt `sqlite3` ohne explizite Sperren bei SchreibvorgÃ¤ngen aus verschiedenen Routen.
    *   *LÃ¶sung:* `timeout` ist gesetzt, aber `check_same_thread=False` fÃ¼r SQLite mit FastAPI ist zwingend.

### 2. UnnÃ¶tige API-Aufrufe & Kostentreiber

*   **`ai_analyst.py` (Redundante LLM-Aufrufe):** `call_llm_text_analysis` wird synchron aufgerufen und enthÃ¤lt keine explizite PrÃ¼fung auf Cache-Existenz in der DB. Wenn ein User das Audit fÃ¼r dasselbe Inserat 5x klickt, zahlt er 5x LLM-Tokens.
    *   *LÃ¶sung:* PrÃ¼fe vor dem Aufruf `SELECT ai_analysis FROM properties WHERE id = ...`.
*   **`image_analyst.py` (Vision-Batching):** Die `analyze_property_images`-Funktion analysiert *alle* Bilder, was bei 20+ Fotos extreme Kosten verursacht.
    *   *LÃ¶sung:* Implementiere eine harte Begrenzung auf die 5 wichtigsten Bilder (Fassade, Bad, KÃ¼che, Boden, Grundriss) gemÃ¤Ã `select_key_investment_photos` konsequent fÃ¼r *alle* Aufrufe.

### 3. SicherheitslÃ¼cken & Datenbank-Probleme

*   **SQL-Injections:** In `scraper.py` und `database.py` werden Daten korrekt mit Parametern (`?`) eingefÃ¼gt, aber in `database.py` (in `init_db` / `seed_data`) teilweise Strings manuell verkettet (z.B. Tabellennamen).
    *   *LÃ¶sung:* Strikte Nutzung von `execute(sql, params)`.
*   **API-Key Exposure:** `resolve_llm_keys` sucht in `os.environ` nach `GEMINI_API_KEY`. Wenn Umgebungsvariablen falsch konfiguriert sind, landen diese im Log.
    *   *LÃ¶sung:* API-Keys sollten nur Ã¼ber eine sichere `Vault` oder via `pydantic-settings` geladen werden.
*   **Keine Validierung der User-Eingaben:** In `mcp_server.py` werden Argumente aus `Body(...)` ungeprÃ¼ft in SQL-Queries (z.B. `city LIKE ?`) Ã¼bernommen.
    *   *LÃ¶sung:* Pydantic-Models zur Validierung verwenden.

### Konkrete Handlungsempfehlungen

1.  **`ai_analyst.py`:** Implementiere ein `functools.lru_cache` oder DB-Caching fÃ¼r `call_llm_text_analysis`, basierend auf einem Hash des `full_description` Strings.
2.  **`scraper.py`:** Die `fusion_deals` Logik sollte statt `all_raw_deals` eine `set()` PrÃ¼fung basierend auf einem URL-Hash haben, um Anfragen an das gleiche Listing zu verhindern, bevor die DB Ã¼berhaupt erreicht wird.
3.  **`image_analyst.py`:** Ãndere die Vision-Strategie auf "Lazy Analysis": Analysiere nur das Titelbild und den Grundriss beim ersten Durchlauf. Weitere Bilder erst bei explizitem Nutzer-Request ("Detaillierte Analyse anfordern").
4.  **`database.py`:** Verwende `contextlib.closing` oder einen Connection-Pool (z.B. `aiosqlite`), um sicherzustellen, dass Datenbank-Verbindungen sauber geschlossen werden, insbesondere bei asynchronen API-Calls.

**Zusammenfassend:** Das Projekt ist architektonisch gut strukturiert, aber die "KI-Kosten-Explosion" durch ungefilterte Bildanalysen und fehlendes Caching der LLM-Antworten ist das grÃ¶Ãte finanzielle Risiko.
