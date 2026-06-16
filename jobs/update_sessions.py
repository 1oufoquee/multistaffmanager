"""
Session Update Job
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Registered with PTB's JobQueue and executed every 15 minutes.
Iterates every cinema in services/schedule_import.CINEMA_URLS and
triggers a full schedule refresh for each.

To add a new cinema:
  Add it to services/schedule_import.CINEMA_URLS — nothing else changes.
"""

import logging
from telegram.ext import ContextTypes
from services.schedule_import import CINEMA_URLS, import_cinema

logger = logging.getLogger(__name__)


async def update_all_cinemas(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    PTB JobQueue callback — runs every 15 minutes.
    Processes all configured cinemas sequentially.
    """
    cinemas = list(CINEMA_URLS.keys())
    logger.info("=== Session update started: %s ===", cinemas)

    for cinema in cinemas:
        try:
            count = await import_cinema(cinema)
            logger.info("[%s] Refreshed %d session(s)", cinema, count)
        except Exception as exc:
            logger.error("[%s] Update failed: %s", cinema, exc, exc_info=True)

    logger.info("=== Session update complete ===")
