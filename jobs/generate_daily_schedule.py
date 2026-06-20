"""
Daily Schedule Generation Job
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Registered in main.py as a PTB run_daily job at 06:00 Kyiv time.

Calls services.multiplex_schedule_parser.generate_daily_schedule()
for every configured cinema.
"""

import logging

logger = logging.getLogger(__name__)

_CINEMAS = ["atmosfera"]


async def generate_daily_schedule_job(context) -> None:
    """PTB job callback — wraps generate_daily_schedule with error isolation."""
    from services.multiplex_schedule_parser import generate_daily_schedule

    for cinema in _CINEMAS:
        try:
            logger.info("[Schedule] Starting daily schedule generation for '%s'", cinema)
            total = await generate_daily_schedule(cinema)
            logger.info("[Schedule] '%s' done — %d session(s) saved", cinema, total)
        except Exception as exc:
            logger.error(
                "[Schedule] Daily schedule job failed for '%s': %s",
                cinema, exc, exc_info=True,
            )
