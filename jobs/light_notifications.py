"""
Light Notification Job
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Runs every 60 seconds via PTB JobQueue.

Algorithm per cinema:
  1. Load today's sessions from Firestore.
  2. Filter to halls listed in NOTIFY_HALLS (from bot/hall_config.py).
  3. For sessions whose start time has been reached:
       → send "turn OFF lights" notification (once, guarded by startNotifSent).
  4. For sessions END_NOTIFY_MINUTES minutes before their end:
       → send "prepare to turn ON lights" notification (once, guarded by endNotifSent).

Each notification is sent to all active staff of the same cinema (never cross-cinema).
Notification flags are stored in Firestore — survive bot restarts and 15-min re-imports.

Hall display names and notification targets are configured via environment variables —
see bot/hall_config.py for full documentation.
"""

import logging
from datetime import datetime, timezone, timedelta

from telegram.ext import ContextTypes

from bot.firebase_client import (
    get_sessions,
    get_cinema_staff_tids,
    mark_session_notification_sent,
)
from bot.hall_config import HALL_DISPLAY, NOTIFY_HALLS, END_NOTIFY_MINUTES, hall_label
from services.schedule_import import CINEMA_URLS

logger = logging.getLogger(__name__)

# ── Kyiv timezone ─────────────────────────────────────────────────────────────

_KYIV_TZ = timezone(timedelta(hours=3))


def _now_kyiv() -> datetime:
    return datetime.now(tz=_KYIV_TZ)


def _to_mins(t: str) -> int:
    """'14:30' → 870.  Returns -1 on parse error."""
    try:
        h, m = map(int, t.split(":"))
        return h * 60 + m
    except (ValueError, AttributeError):
        return -1


# ── Notification senders ──────────────────────────────────────────────────────

async def _broadcast(bot, cinema: str, text: str) -> None:
    """Send *text* to every active staff member of *cinema*."""
    tids = get_cinema_staff_tids(cinema)
    if not tids:
        logger.warning("[light] No staff found for cinema=%s", cinema)
        return
    for tid in tids:
        try:
            await bot.send_message(chat_id=tid, text=text, parse_mode="Markdown")
            logger.info("[light] → %d (cinema=%s)", tid, cinema)
        except Exception as exc:
            logger.warning("[light] Could not send to %d: %s", tid, exc)


async def _notify_start(bot, cinema: str, session: dict) -> None:
    label = hall_label(session.get("hall", ""))
    movie = session.get("movieTitle", "—")
    await _broadcast(
        bot, cinema,
        f"💡 *{label}*\n\n"
        f"Сеанс розпочався:\n*{movie}*\n\n"
        f"Будь ласка, *вимкніть світло*.",
    )


async def _notify_end(bot, cinema: str, session: dict) -> None:
    label = hall_label(session.get("hall", ""))
    movie = session.get("movieTitle", "—")
    await _broadcast(
        bot, cinema,
        f"💡 *{label}*\n\n"
        f"*{movie}* закінчується через {END_NOTIFY_MINUTES} хвилин.\n\n"
        f"Будь ласка, *підготуйтеся увімкнути світло*.",
    )


# ── Main job ──────────────────────────────────────────────────────────────────

async def check_light_notifications(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    PTB JobQueue callback — runs every 60 seconds.

    Time window for triggering: target_minute ≤ now < target_minute + 2.
    The 2-minute window absorbs scheduler drift while the Firestore flag
    prevents the same notification from being sent more than once.
    """
    now       = _now_kyiv()
    today_str = now.strftime("%Y-%m-%d")
    now_mins  = now.hour * 60 + now.minute

    logger.debug(
        "[light] tick Kyiv=%s  notify_halls=%s  end_warn=%d min",
        now.strftime("%H:%M"), NOTIFY_HALLS, END_NOTIFY_MINUTES,
    )

    bot = context.bot

    for cinema in CINEMA_URLS:
        try:
            sessions = get_sessions(cinema, today_str)
        except Exception as exc:
            logger.error("[light][%s] get_sessions failed: %s", cinema, exc)
            continue

        for s in sessions:
            hall = (s.get("hall") or "").strip().upper()
            if hall not in NOTIFY_HALLS:
                continue

            session_id = s.get("_id", "")
            if not session_id:
                continue

            # ── Session start → turn lights OFF ──────────────────────────────
            if not s.get("startNotifSent", False):
                start_mins = _to_mins(s.get("sessionTime", ""))
                if start_mins >= 0 and start_mins <= now_mins < start_mins + 2:
                    logger.info(
                        "[light][%s] START  %s  %s  hall=%s",
                        cinema, s.get("sessionTime"), s.get("movieTitle"), hall,
                    )
                    try:
                        await _notify_start(bot, cinema, s)
                        mark_session_notification_sent(cinema, session_id, "startNotifSent")
                    except Exception as exc:
                        logger.error("[light][%s] start notify error: %s", cinema, exc)

            # ── N min before end → prepare to turn lights ON ──────────────────
            if not s.get("endNotifSent", False):
                end_time = s.get("endTime", "")
                if end_time:
                    end_mins  = _to_mins(end_time)
                    notify_at = end_mins - END_NOTIFY_MINUTES
                    if notify_at >= 0 and notify_at <= now_mins < notify_at + 2:
                        logger.info(
                            "[light][%s] END-WARN  %s  ends=%s  hall=%s",
                            cinema, s.get("movieTitle"), end_time, hall,
                        )
                        try:
                            await _notify_end(bot, cinema, s)
                            mark_session_notification_sent(cinema, session_id, "endNotifSent")
                        except Exception as exc:
                            logger.error("[light][%s] end notify error: %s", cinema, exc)
