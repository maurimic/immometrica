"""
Database models and seed data generator for ImmoMetrica Clone.
Uses SQLite for zero-configuration, robust local persistence.
"""

import sqlite3
import json
import os
import secrets
from typing import List, Dict, Any, Optional, Set

DB_PATH = os.environ.get("DATABASE_PATH") or os.path.join(os.path.dirname(__file__), "immometrica.db")
_WAL_INITIALIZED_PATHS: Set[str] = set()

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000;")
    conn.execute("PRAGMA foreign_keys=ON;")
    if DB_PATH not in _WAL_INITIALIZED_PATHS:
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            _WAL_INITIALIZED_PATHS.add(DB_PATH)
        except sqlite3.OperationalError:
            pass
    return conn

def get_system_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT value FROM system_settings WHERE key = ?", (key,))
        row = c.fetchone()
        return row[0] if row else default
    except Exception:
        return default
    finally:
        if conn is not None:
            conn.close()

def set_system_setting(key: str, value: str):
    from app.datetime_utils import get_berlin_now_str
    now_berlin = get_berlin_now_str()
    conn = get_connection()
    try:
        c = conn.cursor()
        try:
            c.execute("REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, ?)", (key, value, now_berlin))
        except sqlite3.OperationalError:
            c.execute("CREATE TABLE IF NOT EXISTS system_settings (key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            c.execute("REPLACE INTO system_settings (key, value, updated_at) VALUES (?, ?, ?)", (key, value, now_berlin))
        conn.commit()
    finally:
        conn.close()

def delete_system_setting(key: str):
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM system_settings WHERE key = ?", (key,))
        conn.commit()
    except Exception:
        pass
    finally:
        if conn is not None:
            conn.close()

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS properties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        address TEXT,
        postal_code TEXT,
        city TEXT NOT NULL,
        state TEXT NOT NULL,
        country TEXT DEFAULT 'DE',
        property_type TEXT NOT NULL,
        building_type TEXT,
        rooms REAL DEFAULT 1,
        living_space REAL NOT NULL,
        land_area REAL DEFAULT 0,
        year_built INTEGER,
        condition TEXT DEFAULT 'Gepflegt',
        purchase_price REAL NOT NULL,
        original_price REAL,
        price_drop_amount REAL DEFAULT 0,
        price_drop_percent REAL DEFAULT 0,
        commission_percent REAL DEFAULT 3.57,
        current_rent REAL DEFAULT 0,
        expected_rent REAL DEFAULT 0,
        gross_yield REAL DEFAULT 0,
        monthly_cashflow REAL DEFAULT 0,
        market_value REAL DEFAULT 0,
        market_value_per_sqm REAL DEFAULT 0,
        market_deviation_percent REAL DEFAULT 0,
        source_portal TEXT NOT NULL,
        portal_url TEXT,
        live_search_url TEXT,
        is_online INTEGER DEFAULT 1,
        availability_status TEXT DEFAULT 'online',
        status_message TEXT DEFAULT 'Inserat aktiv & online',
        last_checked_at TEXT,
        image_url TEXT,
        days_online INTEGER DEFAULT 1,
        first_seen_date TEXT,
        is_private INTEGER DEFAULT 0,
        is_rented INTEGER DEFAULT 0,
        is_foreclosure INTEGER DEFAULT 0,
        is_favorite INTEGER DEFAULT 0,
        pipeline_stage TEXT DEFAULT 'neu',
        tags TEXT DEFAULT '[]',
        lat REAL,
        lng REAL,
        parcel_id TEXT,
        parcel_area REAL,
        description TEXT,
        ai_analysis TEXT,
        image_analysis TEXT,
        gallery_images TEXT DEFAULT '[]'
    )
    """)

    # Ensure schema migrations for existing DB
    cursor.execute("PRAGMA table_info(properties)")
    existing_cols = [c[1] for c in cursor.fetchall()]
    new_cols = [
        ("is_online", "INTEGER DEFAULT 1"),
        ("availability_status", "TEXT DEFAULT 'online'"),
        ("status_message", "TEXT DEFAULT 'Inserat aktiv & online'"),
        ("last_checked_at", "TEXT"),
        ("live_search_url", "TEXT"),
        ("description", "TEXT"),
        ("ai_analysis", "TEXT"),
        ("image_analysis", "TEXT"),
        ("gallery_images", "TEXT DEFAULT '[]'"),
        ("source_portals", "TEXT DEFAULT '[]'")
    ]
    for col_name, col_type in new_cols:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE properties ADD COLUMN {col_name} {col_type}")

    # Clean up synthetic scraper placeholders for condominiums (apartments use MEA, not 1000 m² plot)
    cursor.execute("""
        UPDATE properties
        SET parcel_area = 0.0, parcel_id = NULL
        WHERE parcel_id LIKE 'KAT-%-LIVE'
          AND parcel_area = 1000.0
          AND (
            property_type LIKE '%Wohnung%' OR
            property_type LIKE '%Paket%' OR
            building_type LIKE '%Wohnung%'
          )
    """)

    # Prevent map markers from stacking identically when separate scrapes collide
    cursor.execute("SELECT id, lat, lng FROM properties WHERE lat IS NOT NULL AND lng IS NOT NULL ORDER BY id ASC")
    seen_coords = set()
    for pid, plat, plng in cursor.fetchall():
        key = (round(float(plat), 5), round(float(plng), 5))
        step = 1
        while key in seen_coords:
            key = (round(float(plat) + step * 0.0018, 5), round(float(plng) + step * 0.0024, 5))
            step += 1
        if step > 1:
            cursor.execute("UPDATE properties SET lat = ?, lng = ? WHERE id = ?", (key[0], key[1], pid))
        seen_coords.add(key)

    # Deduplicate existing identical portal_url entries before enforcing UNIQUE index
    cursor.execute("""
        SELECT portal_url, MIN(id) AS keep_id
        FROM properties
        WHERE portal_url IS NOT NULL AND portal_url != ''
        GROUP BY portal_url
        HAVING COUNT(*) > 1
    """)
    for dup_url, keep_id in cursor.fetchall():
        cursor.execute("SELECT id FROM properties WHERE portal_url = ? AND id != ?", (dup_url, keep_id))
        dup_ids = [r[0] for r in cursor.fetchall()]
        if dup_ids:
            placeholders = ",".join("?" for _ in dup_ids)
            cursor.execute(f"UPDATE deal_notes SET property_id = ? WHERE property_id IN ({placeholders})", [keep_id, *dup_ids])
            cursor.execute(f"UPDATE deal_alerts SET property_id = ? WHERE property_id IN ({placeholders})", [keep_id, *dup_ids])
            cursor.execute(f"DELETE FROM properties WHERE id IN ({placeholders})", dup_ids)

    cursor.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS idx_properties_portal_url_unique
    ON properties(portal_url)
    WHERE portal_url IS NOT NULL AND portal_url != ''
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS search_profiles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        city TEXT NOT NULL,
        postal_code TEXT DEFAULT '',
        radius_km INTEGER DEFAULT 25,
        max_price REAL DEFAULT 0,
        max_price_per_sqm REAL DEFAULT 0,
        min_yield REAL DEFAULT 0.0,
        yield_basis TEXT DEFAULT 'any',
        min_discount_pct REAL DEFAULT 0.0,
        min_rooms REAL DEFAULT 1.0,
        min_living_space REAL DEFAULT 0.0,
        property_type TEXT DEFAULT 'all',
        search_query TEXT DEFAULT '',
        is_active INTEGER DEFAULT 1,
        deals_found_count INTEGER DEFAULT 0,
        last_run_at TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS deal_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        profile_id INTEGER,
        property_id INTEGER,
        portal_url TEXT NOT NULL,
        title TEXT NOT NULL,
        city TEXT NOT NULL,
        price REAL NOT NULL,
        living_space REAL NOT NULL,
        price_per_sqm REAL NOT NULL,
        gross_yield REAL NOT NULL,
        market_discount_pct REAL DEFAULT 0.0,
        monthly_cashflow REAL DEFAULT 0.0,
        ai_verdict TEXT,
        has_critical_risks INTEGER DEFAULT 0,
        sent_to_telegram INTEGER DEFAULT 0,
        telegram_error TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(profile_id, portal_url),
        FOREIGN KEY (profile_id) REFERENCES search_profiles (id) ON DELETE SET NULL,
        FOREIGN KEY (property_id) REFERENCES properties (id) ON DELETE CASCADE
    )
    """)

    # Migrate legacy deal_alerts tables where portal_url had a global UNIQUE constraint blocking cross-profile alerts
    try:
        cursor.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='deal_alerts'")
        da_schema_row = cursor.fetchone()
        if da_schema_row and da_schema_row[0] and "portal_url text unique" in da_schema_row[0].lower():
            cursor.execute("ALTER TABLE deal_alerts RENAME TO _deal_alerts_legacy")
            cursor.execute("""
            CREATE TABLE deal_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id INTEGER,
                property_id INTEGER,
                portal_url TEXT NOT NULL,
                title TEXT NOT NULL,
                city TEXT NOT NULL,
                price REAL NOT NULL,
                living_space REAL NOT NULL,
                price_per_sqm REAL NOT NULL,
                gross_yield REAL NOT NULL,
                market_discount_pct REAL DEFAULT 0.0,
                monthly_cashflow REAL DEFAULT 0.0,
                ai_verdict TEXT,
                has_critical_risks INTEGER DEFAULT 0,
                sent_to_telegram INTEGER DEFAULT 0,
                telegram_error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(profile_id, portal_url),
                FOREIGN KEY (profile_id) REFERENCES search_profiles (id) ON DELETE SET NULL,
                FOREIGN KEY (property_id) REFERENCES properties (id) ON DELETE CASCADE
            )
            """)
            cursor.execute("""
            INSERT INTO deal_alerts (
                id, profile_id, property_id, portal_url, title, city, price, living_space,
                price_per_sqm, gross_yield, market_discount_pct, monthly_cashflow,
                ai_verdict, has_critical_risks, sent_to_telegram, telegram_error, created_at
            )
            SELECT id, profile_id, property_id, portal_url, title, city, price, living_space,
                price_per_sqm, gross_yield, market_discount_pct, monthly_cashflow,
                ai_verdict, has_critical_risks, sent_to_telegram, telegram_error, created_at
            FROM _deal_alerts_legacy
            """)
            cursor.execute("DROP TABLE _deal_alerts_legacy")
    except Exception:
        pass

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS deal_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        property_id INTEGER NOT NULL,
        note TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (property_id) REFERENCES properties (id) ON DELETE CASCADE
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS api_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        quota_limit INTEGER DEFAULT 2000,
        quota_used INTEGER DEFAULT 42,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS system_settings (
        key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS seen_listings (
        portal_url TEXT NOT NULL,
        profile_id INTEGER NOT NULL,
        status TEXT DEFAULT 'checked',
        checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (portal_url, profile_id),
        FOREIGN KEY (profile_id) REFERENCES search_profiles (id) ON DELETE CASCADE
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_seen_listings_profile ON seen_listings (profile_id)")

    try:
        cursor.execute("ALTER TABLE search_profiles ADD COLUMN yield_basis TEXT DEFAULT 'any'")
    except sqlite3.OperationalError:
        pass

    conn.commit()

    # Load stored GEMINI_API_KEY into process environment if available
    cursor.execute("SELECT value FROM system_settings WHERE key = 'gemini_api_key'")
    stored_gemini_key = cursor.fetchone()
    if stored_gemini_key and stored_gemini_key[0]:
        os.environ["GEMINI_API_KEY"] = stored_gemini_key[0].strip()

    # Prevent auto-reseeding demo data if DB was intentionally cleared by user
    cursor.execute("SELECT value FROM system_settings WHERE key = 'db_seeded'")
    seeded_flag = cursor.fetchone()

    cursor.execute("SELECT COUNT(*) FROM properties")
    count = cursor.fetchone()[0]
    if count == 0 and not seeded_flag:
        seed_data(conn)
        cursor.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('db_seeded', '1')")

    # Seed default search profiles if empty
    cursor.execute("SELECT COUNT(*) FROM search_profiles")
    if cursor.fetchone()[0] == 0:
        cursor.execute("""
        INSERT INTO search_profiles (name, city, radius_km, max_price, max_price_per_sqm, min_yield, min_discount_pct, min_rooms, min_living_space, property_type, is_active)
        VALUES
        ('Dortmund Rendite & Cashflow > 6.5%', 'Dortmund', 20, 250000, 2500, 6.5, 10.0, 2.0, 45.0, 'all', 1),
        ('Nürnberg & Franken Untermarktwert', 'Nürnberg', 25, 350000, 3200, 5.5, 12.0, 2.0, 50.0, 'all', 1),
        ('Ruhrgebiet Sanierung / Fix & Flip', 'Essen', 30, 180000, 1800, 5.0, 15.0, 1.0, 35.0, 'all', 0)
        """)

    # Seed default crawler settings if missing
    cursor.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('crawler_interval_minutes', '60')")
    cursor.execute("UPDATE system_settings SET value = '60' WHERE key = 'crawler_interval_minutes' AND value IN ('15', '30')")
    cursor.execute("INSERT OR IGNORE INTO system_settings (key, value) VALUES ('crawler_active', '0')")
    conn.commit()

    conn.close()

def seed_data(conn):
    from app.calculator import calculate_cashflow
    from app.valuation import evaluate_property_valuation
    from app.availability import generate_live_search_url
    from datetime import datetime

    from app.datetime_utils import get_berlin_now_str
    now_iso = get_berlin_now_str("%Y-%m-%d %H:%M")

    raw_properties = [
        {
            "title": "Vollvermietetes 6-Parteien-Mehrfamilienhaus mit Ausbaupotenzial",
            "address": "Gladbacher Str. 44",
            "postal_code": "45127",
            "city": "Essen",
            "state": "Nordrhein-Westfalen",
            "property_type": "Mehrfamilienhaus",
            "building_type": "Mehrfamilienhaus",
            "rooms": 14.0,
            "living_space": 380.0,
            "land_area": 450.0,
            "year_built": 1968,
            "condition": "Gepflegt",
            "purchase_price": 540000.0,
            "original_price": 610000.0,
            "current_rent": 3450.0,
            "source_portal": "ImmobilienScout24",
            "portal_url": "https://www.immobilienscout24.de/expose/148192830",
            "live_search_url": "https://www.immobilienscout24.de/Suche/de/nordrhein-westfalen/essen/wohnung-kaufen",
            "is_online": 1,
            "availability_status": "online",
            "status_message": "Live verifiziert & erreichbar",
            "image_url": "https://images.unsplash.com/photo-1545324418-cc1a3fa10c00?auto=format&fit=crop&w=600&q=80",
            "days_online": 24,
            "first_seen_date": "2026-10-13",
            "is_private": 0,
            "is_rented": 1,
            "is_foreclosure": 0,
            "commission_percent": 3.57,
            "lat": 51.4556,
            "lng": 7.0116,
            "parcel_id": "Flurstück 142/3 (Flur 12)",
            "parcel_area": 452.0
        },
        {
            "title": "Top-Renditeobjekt: Sanierte 2-Zimmer-Eigentumswohnung nahe Zentrum",
            "address": "Eisenbahnstraße 88",
            "postal_code": "04315",
            "city": "Leipzig",
            "state": "Sachsen",
            "property_type": "Eigentumswohnung",
            "building_type": "Altbauwohnung",
            "rooms": 2.0,
            "living_space": 58.0,
            "land_area": 0.0,
            "year_built": 1910,
            "condition": "Saniert",
            "purchase_price": 129000.0,
            "original_price": 145000.0,
            "current_rent": 620.0,
            "source_portal": "Kleinanzeigen",
            "portal_url": "https://www.kleinanzeigen.de/s-anzeige/top-renditeobjekt-sanierte-2-zimmer-wohnung/2891024810-196-4234",
            "live_search_url": "https://www.kleinanzeigen.de/s-wohnung-kaufen/leipzig/c196l4234",
            "is_online": 1,
            "availability_status": "online",
            "status_message": "Live verifiziert & erreichbar",
            "image_url": "https://images.unsplash.com/photo-1502672260266-1c1ef2d93688?auto=format&fit=crop&w=600&q=80",
            "days_online": 8,
            "first_seen_date": "2026-10-29",
            "is_private": 1,
            "is_rented": 1,
            "is_foreclosure": 0,
            "commission_percent": 0.0,
            "lat": 51.3468,
            "lng": 12.4042,
            "parcel_id": "Flurstück 88/1 (Flur 4)",
            "parcel_area": 310.0
        },
        {
            "title": "Zwangsversteigerung Amtsgericht: Gepflegtes Einfamilienhaus mit großem Garten",
            "address": "Kastanienallee 12",
            "postal_code": "39104",
            "city": "Magdeburg",
            "state": "Sachsen-Anhalt",
            "property_type": "Einfamilienhaus",
            "building_type": "Freistehendes Haus",
            "rooms": 5.0,
            "living_space": 145.0,
            "land_area": 780.0,
            "year_built": 1995,
            "condition": "Gepflegt",
            "purchase_price": 185000.0,
            "original_price": 270000.0,
            "current_rent": 0.0,
            "source_portal": "ZVG-Portal",
            "portal_url": "https://www.zvg-portal.de/",
            "live_search_url": "https://www.zvg-portal.de/",
            "is_online": 1,
            "availability_status": "online",
            "status_message": "Offizielles Justizportal ZVG online",
            "image_url": "https://images.unsplash.com/photo-1518780664697-55e3ad937233?auto=format&fit=crop&w=600&q=80",
            "days_online": 41,
            "first_seen_date": "2026-09-26",
            "is_private": 0,
            "is_rented": 0,
            "is_foreclosure": 1,
            "commission_percent": 0.0,
            "lat": 52.1205,
            "lng": 11.6276,
            "parcel_id": "Flurstück 512/8 (Flur 22)",
            "parcel_area": 784.0
        },
        {
            "title": "Kapitalanlage: 1-Zimmer Appartement vermietet an TU-Studenten",
            "address": "Universitätsstr. 15",
            "postal_code": "44789",
            "city": "Dortmund",
            "state": "Nordrhein-Westfalen",
            "property_type": "Eigentumswohnung",
            "building_type": "Apartment",
            "rooms": 1.0,
            "living_space": 32.0,
            "land_area": 0.0,
            "year_built": 1984,
            "condition": "Gepflegt",
            "purchase_price": 68000.0,
            "original_price": 72000.0,
            "current_rent": 410.0,
            "source_portal": "Immowelt",
            "portal_url": "https://www.immowelt.de/suche/dortmund/wohnungen/kaufen",
            "live_search_url": "https://www.immowelt.de/suche/dortmund/wohnungen/kaufen",
            "is_online": 1,
            "availability_status": "online",
            "status_message": "Live verifiziert & erreichbar",
            "image_url": "https://images.unsplash.com/photo-1522708323590-d24dbb6b0267?auto=format&fit=crop&w=600&q=80",
            "days_online": 5,
            "first_seen_date": "2026-11-01",
            "is_private": 0,
            "is_rented": 1,
            "is_foreclosure": 0,
            "commission_percent": 3.57,
            "lat": 51.5136,
            "lng": 7.4653,
            "parcel_id": "Flurstück 19/2 (Flur 8)",
            "parcel_area": 120.0
        },
        {
            "title": "Solides 8-Familienhaus in gefragter Mikrolage von Duisburg-Süd",
            "address": "Düsseldorfer Landstr. 112",
            "postal_code": "47249",
            "city": "Duisburg",
            "state": "Nordrhein-Westfalen",
            "property_type": "Mehrfamilienhaus",
            "building_type": "Mehrfamilienhaus",
            "rooms": 22.0,
            "living_space": 540.0,
            "land_area": 620.0,
            "year_built": 1974,
            "condition": "Gepflegt",
            "purchase_price": 790000.0,
            "original_price": 890000.0,
            "current_rent": 5300.0,
            "source_portal": "Immonet",
            "portal_url": "https://www.immonet.de/nordrhein-westfalen/duisburg-wohnung-kaufen.html",
            "live_search_url": "https://www.immonet.de/nordrhein-westfalen/duisburg-wohnung-kaufen.html",
            "is_online": 1,
            "availability_status": "online",
            "status_message": "Live verifiziert & erreichbar",
            "image_url": "https://images.unsplash.com/photo-1568605117036-5fe5e7bab0b7?auto=format&fit=crop&w=600&q=80",
            "days_online": 35,
            "first_seen_date": "2026-10-02",
            "is_private": 0,
            "is_rented": 1,
            "is_foreclosure": 0,
            "commission_percent": 3.57,
            "lat": 51.3789,
            "lng": 6.7584,
            "parcel_id": "Flurstück 41/7 (Flur 16)",
            "parcel_area": 625.0
        },
        {
            "title": "Moderne 3-Zimmer-Wohnung mit Balkon in Berlin-Pankow",
            "address": "Berliner Str. 65",
            "postal_code": "13189",
            "city": "Berlin",
            "state": "Berlin",
            "property_type": "Eigentumswohnung",
            "building_type": "Etagenwohnung",
            "rooms": 3.0,
            "living_space": 76.0,
            "land_area": 0.0,
            "year_built": 2002,
            "condition": "Neuwertig",
            "purchase_price": 315000.0,
            "original_price": 349000.0,
            "current_rent": 1280.0,
            "source_portal": "ImmobilienScout24",
            "portal_url": "https://www.immobilienscout24.de/expose/147289103",
            "live_search_url": "https://www.immobilienscout24.de/Suche/de/berlin/berlin/pankow/wohnung-kaufen",
            "is_online": 1,
            "availability_status": "online",
            "status_message": "Live verifiziert & erreichbar",
            "image_url": "https://images.unsplash.com/photo-1600585154340-be6161a56a0c?auto=format&fit=crop&w=600&q=80",
            "days_online": 14,
            "first_seen_date": "2026-10-23",
            "is_private": 0,
            "is_rented": 1,
            "is_foreclosure": 0,
            "commission_percent": 3.57,
            "lat": 52.5694,
            "lng": 13.4147,
            "parcel_id": "Flurstück 104/5 (Flur 7)",
            "parcel_area": 420.0
        },
        {
            "title": "Privatverkauf ohne Provision: Großzügige Altbauwohnung Dresden-Striesen",
            "address": "Borsbergstraße 28",
            "postal_code": "01309",
            "city": "Dresden",
            "state": "Sachsen",
            "property_type": "Eigentumswohnung",
            "building_type": "Altbauwohnung",
            "rooms": 4.0,
            "living_space": 98.0,
            "land_area": 0.0,
            "year_built": 1905,
            "condition": "Saniert",
            "purchase_price": 269000.0,
            "original_price": 289000.0,
            "current_rent": 1050.0,
            "source_portal": "Kleinanzeigen",
            "portal_url": "https://www.kleinanzeigen.de/s-anzeige/privatverkauf-ohne-provision-altbauwohnung/2893019284-196-3820",
            "live_search_url": "https://www.kleinanzeigen.de/s-wohnung-kaufen/dresden/c196l3820",
            "is_online": 1,
            "availability_status": "online",
            "status_message": "Live verifiziert & erreichbar",
            "image_url": "https://images.unsplash.com/photo-1600565193348-f74bd3c7ccdf?auto=format&fit=crop&w=600&q=80",
            "days_online": 18,
            "first_seen_date": "2026-10-19",
            "is_private": 1,
            "is_rented": 0,
            "is_foreclosure": 0,
            "commission_percent": 0.0,
            "lat": 51.0456,
            "lng": 13.7842,
            "parcel_id": "Flurstück 201/1 (Flur 3)",
            "parcel_area": 390.0
        },
        {
            "title": "Renditestarkes Wohn- und Geschäftshaus in zentraler Lage von Nürnberg",
            "address": "Fürther Straße 92",
            "postal_code": "90429",
            "city": "Nürnberg",
            "state": "Bayern",
            "property_type": "Mehrfamilienhaus",
            "building_type": "Wohn- und Geschäftshaus",
            "rooms": 18.0,
            "living_space": 490.0,
            "land_area": 510.0,
            "year_built": 1958,
            "condition": "Gepflegt",
            "purchase_price": 1280000.0,
            "original_price": 1380000.0,
            "current_rent": 7200.0,
            "source_portal": "ImmobilienScout24",
            "portal_url": "https://www.immobilienscout24.de/expose/149102845",
            "live_search_url": "https://www.immobilienscout24.de/Suche/de/bayern/nuernberg/haus-kaufen",
            "is_online": 1,
            "availability_status": "online",
            "status_message": "Live verifiziert & erreichbar",
            "image_url": "https://images.unsplash.com/photo-1570129477492-45c003edd2be?auto=format&fit=crop&w=600&q=80",
            "days_online": 29,
            "first_seen_date": "2026-10-08",
            "is_private": 0,
            "is_rented": 1,
            "is_foreclosure": 0,
            "commission_percent": 3.57,
            "lat": 49.4532,
            "lng": 11.0543,
            "parcel_id": "Flurstück 92/4 (Flur 18)",
            "parcel_area": 512.0
        }
    ]

    cursor = conn.cursor()
    cursor.execute("DELETE FROM properties")

    for item in raw_properties:
        val = evaluate_property_valuation(
            city=item["city"],
            living_space=item["living_space"],
            purchase_price=item["purchase_price"],
            year_built=item["year_built"],
            condition=item["condition"],
            is_rented=bool(item["is_rented"]),
            property_type=item["property_type"]
        )

        expected_rent = val["expected_rent_monthly"]
        rent_to_use = item["current_rent"] if item["current_rent"] > 0 else expected_rent

        cf = calculate_cashflow(
            price=item["purchase_price"],
            rent=rent_to_use,
            equity_value=20.0,
            equity_type="percent",
            interest_rate=3.6,
            repayment_rate=2.0,
            additional_costs=max(30.0, round(item["living_space"] * 0.4, 2)),
            country="DE",
            state=item["state"],
            postal_code=item["postal_code"],
            city=item["city"],
            commission_percent=item["commission_percent"],
            living_space=item["living_space"],
            market_value=val["estimated_market_value"]
        )

        price_drop_amount = max(0.0, item["original_price"] - item["purchase_price"])
        price_drop_pct = round((price_drop_amount / item["original_price"]) * 100.0, 1) if item["original_price"] > 0 else 0.0

        tags = []
        if cf["cashflow_tone"] == "cashflow-positive":
            tags.append({"name": "Cashflow+", "color": "#1a7f47"})
        if val["is_undervalued"]:
            tags.append({"name": f"{abs(val['market_deviation_percent'])}% Unter Marktwert", "color": "#1e73b3"})
        if item["is_private"]:
            tags.append({"name": "Provisionsfrei", "color": "#009e73"})
        if item["is_foreclosure"]:
            tags.append({"name": "ZVG Schnäppchen", "color": "#a15c07"})

        cursor.execute("""
        INSERT INTO properties (
            title, address, postal_code, city, state, country,
            property_type, building_type, rooms, living_space, land_area,
            year_built, condition, purchase_price, original_price,
            price_drop_amount, price_drop_percent, commission_percent,
            current_rent, expected_rent, gross_yield, monthly_cashflow,
            market_value, market_value_per_sqm, market_deviation_percent,
            source_portal, portal_url, live_search_url, is_online, availability_status,
            status_message, last_checked_at, image_url, days_online, first_seen_date,
            is_private, is_rented, is_foreclosure, is_favorite, pipeline_stage,
            tags, lat, lng, parcel_id, parcel_area
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            item["title"], item["address"], item["postal_code"], item["city"], item["state"], "DE",
            item["property_type"], item["building_type"], item["rooms"], item["living_space"], item["land_area"],
            item["year_built"], item["condition"], item["purchase_price"], item["original_price"],
            price_drop_amount, price_drop_pct, item["commission_percent"],
            item["current_rent"], expected_rent, cf["gross_yield"], cf["monthly_cashflow"],
            val["estimated_market_value"], val["estimated_market_value_per_sqm"], val["market_deviation_percent"],
            item["source_portal"], item["portal_url"], item["live_search_url"], item["is_online"], item["availability_status"],
            item["status_message"], now_iso, item["image_url"], item["days_online"], item["first_seen_date"],
            item["is_private"], item["is_rented"], item["is_foreclosure"], 0, "neu",
            json.dumps(tags), item["lat"], item["lng"], item["parcel_id"], item["parcel_area"]
        ))

    # Add initial API Token if table is empty
    cursor.execute("SELECT COUNT(*) FROM api_tokens")
    if cursor.fetchone()[0] == 0:
        initial_token = f"immo_live_{secrets.token_hex(16)}"
        cursor.execute("""
        INSERT INTO api_tokens (token, name, quota_limit, quota_used)
        VALUES (?, 'Antigravity AI Agent & n8n Sync', 2000, 0)
        """, (initial_token,))

    conn.commit()


# ==============================================================================
# SEARCH PROFILES & DEAL ALERTS CRUD HELPERS
# ==============================================================================

def get_search_profiles() -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM search_profiles ORDER BY id ASC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_search_profile_by_id(profile_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM search_profiles WHERE id = ?", (profile_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default

def _safe_int(val: Any, default: int = 0) -> int:
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default

def save_search_profile(data: Dict[str, Any]) -> int:
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO search_profiles (
            name, city, postal_code, radius_km, max_price, max_price_per_sqm,
            min_yield, yield_basis, min_discount_pct, min_rooms, min_living_space,
            property_type, search_query, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        str(data.get("name") or "Neues Suchprofil").strip(),
        str(data.get("city") or "").strip(),
        str(data.get("postal_code") or "").strip(),
        _safe_int(data.get("radius_km"), 25),
        _safe_float(data.get("max_price"), 0.0),
        _safe_float(data.get("max_price_per_sqm"), 0.0),
        _safe_float(data.get("min_yield"), 0.0),
        str(data.get("yield_basis") or "any").strip().lower(),
        _safe_float(data.get("min_discount_pct"), 0.0),
        _safe_float(data.get("min_rooms"), 1.0),
        _safe_float(data.get("min_living_space"), 0.0),
        str(data.get("property_type") or "all"),
        str(data.get("search_query") or "").strip(),
        1 if data.get("is_active", True) else 0
    ))
    new_id = c.lastrowid
    conn.commit()
    conn.close()
    return new_id


def update_search_profile(profile_id: int, data: Dict[str, Any]) -> bool:
    conn = get_connection()
    c = conn.cursor()
    fields = [
        "name", "city", "postal_code", "radius_km", "max_price", "max_price_per_sqm",
        "min_yield", "yield_basis", "min_discount_pct", "min_rooms", "min_living_space",
        "property_type", "search_query", "is_active", "deals_found_count", "last_run_at"
    ]
    numeric_floats = {"max_price", "max_price_per_sqm", "min_yield", "min_discount_pct", "min_rooms", "min_living_space"}
    numeric_ints = {"radius_km", "deals_found_count"}
    updates = []
    values = []
    for f in fields:
        if f in data:
            raw_val = data[f]
            if f in numeric_floats:
                val = _safe_float(raw_val, 0.0)
            elif f in numeric_ints:
                val = _safe_int(raw_val, 0)
            elif f == "is_active":
                val = 1 if raw_val else 0
            else:
                val = str(raw_val) if raw_val is not None else ""
            updates.append(f"{f} = ?")
            values.append(val)
    if not updates:
        conn.close()
        return False
    values.append(profile_id)
    c.execute(f"UPDATE search_profiles SET {', '.join(updates)} WHERE id = ?", values)
    conn.commit()
    success = c.rowcount > 0

    # Wenn sich Filterkriterien ändern, gelernte 'seen_listings' leeren, damit Deals neu geprüft werden
    filter_criteria_keys = {
        "city", "postal_code", "radius_km", "max_price", "max_price_per_sqm",
        "min_yield", "yield_basis", "min_discount_pct", "min_rooms", "min_living_space",
        "property_type", "search_query"
    }
    if any(k in data for k in filter_criteria_keys):
        c.execute("DELETE FROM seen_listings WHERE profile_id = ?", (profile_id,))
        conn.commit()

    conn.close()
    return success


def update_property(property_id: int, data: Dict[str, Any]) -> bool:
    conn = get_connection()
    c = conn.cursor()
    fields = [
        "title", "address", "postal_code", "city", "state", "country",
        "property_type", "building_type", "rooms", "living_space", "land_area",
        "year_built", "condition", "purchase_price", "original_price",
        "price_drop_amount", "price_drop_percent", "commission_percent",
        "current_rent", "expected_rent", "gross_yield", "monthly_cashflow",
        "market_value", "market_value_per_sqm", "market_deviation_percent",
        "source_portal", "portal_url", "live_search_url", "is_online",
        "availability_status", "status_message", "last_checked_at", "image_url",
        "days_online", "first_seen_date", "is_private", "is_rented",
        "is_foreclosure", "is_favorite", "pipeline_stage", "tags", "lat", "lng",
        "parcel_id", "parcel_area", "description", "ai_analysis", "image_analysis",
        "gallery_images", "source_portals"
    ]
    updates = []
    values = []
    for f in fields:
        if f in data:
            updates.append(f"{f} = ?")
            values.append(data[f])
    if not updates:
        conn.close()
        return False
    values.append(property_id)
    c.execute(f"UPDATE properties SET {', '.join(updates)} WHERE id = ?", values)
    conn.commit()
    success = c.rowcount > 0
    conn.close()
    return success


def delete_search_profile(profile_id: int) -> bool:
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM seen_listings WHERE profile_id = ?", (profile_id,))
    c.execute("DELETE FROM search_profiles WHERE id = ?", (profile_id,))
    conn.commit()
    success = c.rowcount > 0
    conn.close()
    return success


# ==============================================================================
# SEEN LISTINGS (MEMORY FOR SEARCH AGENT)
# ==============================================================================

def mark_listing_seen(portal_url: str, profile_id: int, status: str = "checked") -> bool:
    """
    Protokolliert eine vom Suchagenten für ein Profil geprüfte Portal-URL in 'seen_listings'.
    """
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            INSERT INTO seen_listings (portal_url, profile_id, status, checked_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(portal_url, profile_id) DO UPDATE SET
                status = excluded.status,
                checked_at = CURRENT_TIMESTAMP
        """, (portal_url, profile_id, status))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def get_seen_listing_urls(profile_id: int) -> Set[str]:
    """
    Liefert ein Set aller für dieses Profil bereits geprüften Portal-URLs.
    """
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("SELECT portal_url FROM seen_listings WHERE profile_id = ?", (profile_id,))
        rows = c.fetchall()
        return {r[0] for r in rows if r[0]}
    except Exception:
        return set()
    finally:
        if conn is not None:
            conn.close()


def clear_seen_listings_for_profile(profile_id: int) -> bool:
    """
    Löscht alle gespeicherten URLs eines Profils (z. B. nach Filter-Änderung).
    """
    conn = None
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute("DELETE FROM seen_listings WHERE profile_id = ?", (profile_id,))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        if conn is not None:
            conn.close()


def get_deal_alerts(limit: int = 50, profile_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        c = conn.cursor()
        if profile_id:
            c.execute("""
                SELECT da.*, sp.name as profile_name 
                FROM deal_alerts da 
                LEFT JOIN search_profiles sp ON da.profile_id = sp.id 
                WHERE da.profile_id = ? 
                ORDER BY da.id DESC LIMIT ?
            """, (profile_id, limit))
        else:
            c.execute("""
                SELECT da.*, sp.name as profile_name 
                FROM deal_alerts da 
                LEFT JOIN search_profiles sp ON da.profile_id = sp.id 
                ORDER BY da.id DESC LIMIT ?
            """, (limit,))
        return [dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def is_alert_already_logged(portal_url: str, profile_id: Optional[int] = None) -> bool:
    conn = get_connection()
    try:
        c = conn.cursor()
        if profile_id is not None:
            c.execute("SELECT id FROM deal_alerts WHERE portal_url = ? AND profile_id = ?", (portal_url, profile_id))
        else:
            c.execute("SELECT id FROM deal_alerts WHERE portal_url = ?", (portal_url,))
        row = c.fetchone()
        return bool(row)
    finally:
        conn.close()


def record_deal_alert(alert_data: Dict[str, Any]) -> int:
    from app.datetime_utils import get_berlin_now_str
    local_created_at = alert_data.get("created_at") or get_berlin_now_str()
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("""
            INSERT INTO deal_alerts (
                profile_id, property_id, portal_url, title, city, price, living_space,
                price_per_sqm, gross_yield, market_discount_pct, monthly_cashflow,
                ai_verdict, has_critical_risks, sent_to_telegram, telegram_error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, portal_url) DO UPDATE SET
                property_id = excluded.property_id,
                price = excluded.price,
                living_space = excluded.living_space,
                price_per_sqm = excluded.price_per_sqm,
                gross_yield = excluded.gross_yield,
                market_discount_pct = excluded.market_discount_pct,
                monthly_cashflow = excluded.monthly_cashflow,
                ai_verdict = excluded.ai_verdict,
                has_critical_risks = excluded.has_critical_risks,
                sent_to_telegram = MAX(deal_alerts.sent_to_telegram, excluded.sent_to_telegram),
                telegram_error = excluded.telegram_error
        """, (
            alert_data.get("profile_id"),
            alert_data.get("property_id"),
            alert_data["portal_url"],
            alert_data.get("title", "Immobilienangebot"),
            alert_data.get("city", ""),
            _safe_float(alert_data.get("price"), 0.0),
            _safe_float(alert_data.get("living_space"), 0.0),
            _safe_float(alert_data.get("price_per_sqm"), 0.0),
            _safe_float(alert_data.get("gross_yield"), 0.0),
            _safe_float(alert_data.get("market_discount_pct"), 0.0),
            _safe_float(alert_data.get("monthly_cashflow"), 0.0),
            alert_data.get("ai_verdict", ""),
            1 if alert_data.get("has_critical_risks") else 0,
            1 if alert_data.get("sent_to_telegram") else 0,
            alert_data.get("telegram_error"),
            local_created_at
        ))
        new_id = c.lastrowid
        conn.commit()
        return new_id
    finally:
        conn.close()


def delete_deal_alert(alert_id: int) -> bool:
    """Deletes a single deal alert by ID from the deal_alerts stream."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("DELETE FROM deal_alerts WHERE id = ?", (alert_id,))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()


def clear_deal_alerts(profile_id: Optional[int] = None) -> int:
    """Deletes all deal alerts, optionally filtered by profile_id. Returns number of deleted rows."""
    conn = get_connection()
    try:
        c = conn.cursor()
        if profile_id:
            c.execute("DELETE FROM deal_alerts WHERE profile_id = ?", (profile_id,))
        else:
            c.execute("DELETE FROM deal_alerts")
        conn.commit()
        return c.rowcount
    finally:
        conn.close()


# ==============================================================================
# MCP API TOKENS MANAGEMENT HELPERS
# ==============================================================================

def get_active_mcp_token() -> Dict[str, Any]:
    """
    Liest den aktuell aktiven MCP-API-Token und Quota-Status aus der Datenbank aus.
    Erzeugt automatisch einen zufälligen Token mit Präfix 'immo_live_', falls die Tabelle noch leer ist.
    """
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT id, token, name, quota_limit, quota_used, created_at FROM api_tokens ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        if not row:
            token_str = f"immo_live_{secrets.token_hex(16)}"
            c.execute("""
                INSERT INTO api_tokens (token, name, quota_limit, quota_used)
                VALUES (?, 'Antigravity AI Agent & n8n Sync', 2000, 0)
            """, (token_str,))
            conn.commit()
            c.execute("SELECT id, token, name, quota_limit, quota_used, created_at FROM api_tokens WHERE token = ?", (token_str,))
            row = c.fetchone()
        return {
            "id": row[0],
            "token": row[1],
            "name": row[2],
            "quota_limit": row[3],
            "quota_used": row[4],
            "created_at": str(row[5]) if row[5] else ""
        }
    finally:
        conn.close()


def regenerate_mcp_token(name: str = "Antigravity AI Agent & n8n Sync") -> Dict[str, Any]:
    """
    Erzeugt einen neuen zufälligen MCP-API-Token (secrets.token_hex(16) mit Präfix 'immo_live_')
    und aktualisiert den bestehenden Eintrag bzw. legt einen neuen an.
    Setzt den Zähler quota_used zurück.
    """
    new_token = f"immo_live_{secrets.token_hex(16)}"
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT id FROM api_tokens ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        if row:
            c.execute("""
                UPDATE api_tokens 
                SET token = ?, quota_used = 0, created_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (new_token, row[0]))
        else:
            c.execute("""
                INSERT INTO api_tokens (token, name, quota_limit, quota_used)
                VALUES (?, ?, 2000, 0)
            """, (new_token, name))
        conn.commit()
    finally:
        conn.close()
    return get_active_mcp_token()


def increment_mcp_quota(token: Optional[str] = None) -> int:
    """
    Erhöht bei MCP-Aufrufen den Zähler quota_used in der Datenbank.
    Gibt den neuen Zählerstand zurück.
    """
    conn = get_connection()
    try:
        c = conn.cursor()
        if token:
            c.execute("UPDATE api_tokens SET quota_used = quota_used + 1 WHERE token = ?", (token,))
        else:
            c.execute("""
                UPDATE api_tokens 
                SET quota_used = quota_used + 1 
                WHERE id = (SELECT id FROM api_tokens ORDER BY id DESC LIMIT 1)
            """)
        conn.commit()
        c.execute("SELECT quota_used FROM api_tokens ORDER BY id DESC LIMIT 1")
        row = c.fetchone()
        return row[0] if row else 0
    finally:
        conn.close()


def delete_all_properties(clear_alerts: bool = True, clear_seen: bool = True) -> int:
    """
    Entfernt alle Inserate / Immobilien aus der Datenbank.
    Löscht optional auch Deal-Alerts und den 'seen_listings' Cache,
    damit der Crawler bei einem Neuversuch wieder alle Inserate prüfen kann.
    Setzt 'db_seeded' auf 1, damit init_db() keine Fake-Daten automatisch nachschiebt.
    """
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM properties")
        count = c.fetchone()[0]

        c.execute("DELETE FROM properties")
        c.execute("DELETE FROM deal_notes")
        if clear_alerts:
            c.execute("DELETE FROM deal_alerts")
        if clear_seen:
            c.execute("DELETE FROM seen_listings")

        c.execute("UPDATE search_profiles SET deals_found_count = 0")
        c.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('db_seeded', '1')")
        c.execute("DELETE FROM sqlite_sequence WHERE name IN ('properties', 'deal_alerts', 'deal_notes')")

        conn.commit()
        return count
    finally:
        conn.close()


def reseed_demo_properties() -> int:
    """
    Stellt die Standard-Demo-Inserate wieder her, falls gewünscht.
    """
    conn = get_connection()
    try:
        seed_data(conn)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM properties")
        count = c.fetchone()[0]
        c.execute("INSERT OR REPLACE INTO system_settings (key, value) VALUES ('db_seeded', '1')")
        conn.commit()
        return count
    finally:
        conn.close()



