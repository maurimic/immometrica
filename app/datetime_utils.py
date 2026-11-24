"""
ImmoMetrica Centralized Timezone & Datetime Utilities.
Guarantees consistent timestamps across all platforms (Windows, Linux, Docker)
fixed to the 'Europe/Berlin' timezone (MEZ/MESZ).
"""

from datetime import datetime, date
from zoneinfo import ZoneInfo
from typing import Optional

BERLIN_TZ = ZoneInfo("Europe/Berlin")


def get_berlin_now() -> datetime:
    """Returns timezone-aware datetime for Europe/Berlin."""
    return datetime.now(BERLIN_TZ)


def get_berlin_now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Returns formatted string of current Berlin time."""
    return get_berlin_now().strftime(fmt)


def get_berlin_today_str() -> str:
    """Returns YYYY-MM-DD for current Berlin date."""
    return get_berlin_now().strftime("%Y-%m-%d")


def parse_iso_or_berlin(dt_str: Optional[str]) -> Optional[datetime]:
    """Parses various date/time formats into a datetime object."""
    if not dt_str:
        return None
    s = str(dt_str).strip()
    s_clean = s.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s_clean[:19], fmt)
        except ValueError:
            continue
    return None
