"""
Background Scheduler for ImmoMetrica Search Agent & Crawler.
Runs periodic scans in non-blocking worker threads according to configured intervals.
Controlled via system settings (crawler_active, crawler_interval_minutes).
"""

import asyncio
import logging
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from app.database import get_system_setting, set_system_setting
from app.datetime_utils import get_berlin_now

logger = logging.getLogger("immometrica.scheduler")

_scheduler_task: Optional[asyncio.Task] = None
_crawler_lock = threading.RLock()
_is_running_scan: bool = False
_scan_depth: int = 0


@contextmanager
def crawler_scan_guard(blocking: bool = False):
    """
    Re-entrant thread-safe lock ensuring scheduled, manual, and MCP crawler runs never overlap
    across different threads while allowing nested calls within the same thread.
    Yields True if acquired, False if another thread already holds the crawler lock.
    """
    global _is_running_scan, _scan_depth
    acquired = _crawler_lock.acquire(blocking=blocking)
    if not acquired:
        yield False
        return
    _scan_depth += 1
    _is_running_scan = True
    try:
        yield True
    finally:
        _scan_depth = max(0, _scan_depth - 1)
        if _scan_depth == 0:
            _is_running_scan = False
        _crawler_lock.release()


async def _crawler_loop():
    """
    Periodic loop checking interval and triggering active search profiles.
    """
    logger.info("ImmoMetrica background crawler scheduler loop started.")

    while True:
        try:
            # Check if crawler is activated by user
            active_setting = get_system_setting("crawler_active", "0")
            is_active = active_setting in ("1", "true", "True")

            if is_active and not _is_running_scan:
                interval_str = get_system_setting("crawler_interval_minutes", "60")
                try:
                    interval_min = max(5, int(interval_str))
                except ValueError:
                    interval_min = 60

                last_run_str = get_system_setting("crawler_last_run")
                should_run = False

                if not last_run_str:
                    should_run = True
                else:
                    try:
                        last_run_dt = datetime.strptime(last_run_str, "%Y-%m-%d %H:%M:%S")
                        elapsed_seconds = (get_berlin_now().replace(tzinfo=None) - last_run_dt).total_seconds()
                        if elapsed_seconds >= (interval_min * 60):
                            should_run = True
                    except Exception:
                        should_run = True

                if should_run:
                    with crawler_scan_guard(blocking=False) as acquired:
                        if acquired:
                            logger.info(f"Triggering scheduled crawler scan (Interval: {interval_min} min)...")
                            try:
                                from app.search_agent import execute_all_active_profiles
                                # Run blocking scraping & AI pre-check in separate thread
                                await asyncio.to_thread(execute_all_active_profiles)
                            except Exception as e:
                                logger.error(f"Error during scheduled crawler run: {e}")

            # Periodic Portal Health / Canary Check (every 6 hours or upon initial start)
            last_canary_str = get_system_setting("portal_health_last_check")
            should_run_canary = False
            if not last_canary_str:
                should_run_canary = True
            else:
                try:
                    last_canary_dt = datetime.strptime(last_canary_str, "%Y-%m-%d %H:%M:%S")
                    if (get_berlin_now().replace(tzinfo=None) - last_canary_dt).total_seconds() >= (6 * 3600):
                        should_run_canary = True
                except Exception:
                    should_run_canary = True

            if should_run_canary:
                try:
                    from app.portal_health import run_canary_checks
                    await asyncio.to_thread(run_canary_checks)
                except Exception as e:
                    logger.error(f"Error during background portal canary check: {e}")

        except asyncio.CancelledError:
            logger.info("Crawler scheduler loop cancelled.")
            break
        except Exception as e:
            logger.error(f"Scheduler tick error: {e}")

        # Sleep between checks (30 seconds)
        await asyncio.sleep(30)


def start_background_scheduler():
    """Starts the scheduler asyncio background task."""
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        try:
            loop = asyncio.get_running_loop()
            _scheduler_task = loop.create_task(_crawler_loop())
            logger.info("Background crawler task registered.")
        except RuntimeError:
            logger.warning("No running asyncio event loop to attach crawler scheduler.")


def stop_background_scheduler():
    """Cancels the scheduler background task."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None
        logger.info("Background crawler task stopped.")


def is_crawler_busy() -> bool:
    return _is_running_scan
