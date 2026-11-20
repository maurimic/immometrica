"""
Scrapers package for ImmoMetrica.
Provides multi-portal scrapers for Kleinanzeigen, ImmoScout24, Immowelt,
and cross-portal deal fusion engine.
"""

from app.scrapers.kleinanzeigen import scrape_kleinanzeigen_deals
from app.scrapers.immoscout import search_immoscout_deals, fetch_immoscout_expose
from app.scrapers.immowelt import search_immowelt_deals, fetch_immowelt_expose
from app.scrapers.deal_fusion import fuse_deals

__all__ = [
    "scrape_kleinanzeigen_deals",
    "search_immoscout_deals",
    "fetch_immoscout_expose",
    "search_immowelt_deals",
    "fetch_immowelt_expose",
    "fuse_deals"
]
