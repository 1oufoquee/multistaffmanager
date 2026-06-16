"""
Light Notification Job
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Runs every 60 seconds via PTB JobQueue.

Algorithm per cinema:
  1. Load today's sessions from Firestore.
  2. Filter to halls that should trigger light reminders.
  3. For sessions whose start time has been reached:
       → send "turn OFF lights" notification (once, guarded by startNotifSent).
  4. For sessions 7 minutes before their end time:
       → send "prepare to turn ON lights" notification (once, guarded by endNotifSent).

Notifications are sent to all active staff of the same cinema.

Configuration (set via environment variables):
  NOTIFY_HALL_NAMES   Comma-separated Multiplex hall ShortNames that trigger alerts.
                      Default: "VIP,LUX"
  END_NOTIFY_MINUTES  Minutes before end to send the end reminder. Default: 7
"""

import logging
import os
from datetime import datetime, timezone, timedelta

from telegram.ext import ContextTypes

from bot.firebase_client import (
    get_sessions,
    get_cinema_staff_tids,
    mark_session_notification_sent,
)
from services.schedule_import import CINEMA_URLS

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

def _load_notify_halls() -> set[str]:
    raw = os.getenv("NOTIFY_HALL_NAMES", "VIP,LUX")
    return {h.strip().upper() for h in raw.split(",") if h.strip()}

def _load_end_notify_minutes() -> int:
    try:
        return int(os.getenv("END_NOTIFY_MINUTES", "7"))
    except (ValueError, TypeError):
        return 7

NOTIFY_HALL_NAMES:    set[str] = _load_notify_halls()
END_NOTIFY_MINUTES:   int      = _load_end_notify_minutes()

# ── Hall display labels ───────────────────────────────────────────────────────

_HALL_DISPLAY: dict[str, str] = {
    "VIP":      "VIP зал",
    "LUX":      "LUX зал",
    "STANDART": "Стандарт зал",
}

def _hall_label(hall: str) -> str:
    return _HALL_DISPLAY.get(hall.upper(), hall)

# ── Kyiv time ─────────────────────────────────────────────────────────────────

_KYIV_TZ = timezone(timedelta(hours=3))

def _now_kyiv() -> datetime:
    return datetime.now(tz=_KYIV_TZ)

def _to_mins(t: str) -> int:
    """'14:30' → 870"""
    try:
        h, m = map(int, t.split(":"))
        return h * 60 + m
    except (ValueError, AttributeError):
        return -1

# ── Notification senders ──────────────────────────────────────────────────────

async def _send_to_staff(
    bot,
    cinema: str,
    text: str,
) -> None:
    """Send *text* to all active staff of *cinema*."""
    tids = get_cinema_staff_tids(cinema)
    if not tids:
        logger.warning("[light] No staff found for cinema=%s", cinema)
        return

    for tid in tids:
        try:
            await bot.send_message(chat_id=tid, text=text, parse_mode="Markdown")
            logger.info("[light] Sent to %d (cinema=%s)", tid, cinema)
        except Exception as exc:
            logger.warning("[light] Could not send to %d: %s", tid, exc)


async def _notify_start(bot, cinema: str, session: dict) -> None:
    hall  = _hall_label(session.get("hall", ""))
    movie = session.get("movieTitle", "—")
    text  = (
        f"💡 *{hall}*\n\n"
        f"Сеанс розпочався:\n"
        f"*{movie}*\n\n"
        f"Будь ласка, *вимкніть світло*."
    )
    await _send_to_staff(bot, cinema, text)


async def _notify_end(bot, cinema: str, session: dict) -> None:
    hall  = _hall_label(session.get("hall", ""))
    movie = session.get("movieTitle", "—")
    text  = (
        f"💡 *{hall}*\n\n"
        f"*{movie}* закінчується через {END_NOTIFY_MINUTES} хвилин.\n\n"
        f"Будь ласка, *підготуйтеся увімкнути світло*."
    )
    await _send_to_staff(bot, cinema, text)


# ── Main job ──────────────────────────────────────────────────────────────────

async def check_light_notifications(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    PTB JobQueue callback — runs every 60 seconds.

    For each cinema, checks today's sessions and fires light reminders
    at the right moment. Each notification is sent exactly once per session
    (guarded by startNotifSent / endNotifSent flags in Firestore).
    """
    now       = _now_kyiv()
    today_str = now.strftime("%Y-%m-%d")
    now_mins  = now.hour * 60 + now.minute

    logger.debug("[light] tick Kyiv=%s halls=%s", now.strftime("%H:%M"), NOTIFY_HALL_NAMES)

    bot = context.bot

    for cinema in CINEMA_URLS:
        try:
            sessions = get_sessions(cinema, today_str)
        except Exception as exc:
            logger.error("[light][%s] get_sessions failed: %s", cinema, exc)
            continue

        for s in sessions:
            hall = (s.get("hall") or "").upper()
            if hall not in NOTIFY_HALL_NAMES:
                continue

            session_id = s.get("_id", "")
            if not session_id:
                continue

            # ── Start notification ────────────────────────────────────────────
            if not s.get("startNotifSent", False):
                start_mins = _to_mins(s.get("sessionTime", ""))
                if start_mins >= 0 and start_mins <= now_mins < start_mins + 2:
                    logger.info(
                        "[light][%s] START %s %s hall=%s",
                        cinema, s.get("sessionTime"), s.get("movieTitle"), hall,
                    )
                    try:
                        await _notify_start(bot, cinema, s)
                        mark_session_notification_sent(cinema, session_id, "startNotifSent")
                    except Exception as exc:
                        logger.error("[light][%s] start notify failed: %s", cinema, exc)

            # ── End notification ──────────────────────────────────────────────
            if not s.get("endNotifSent", False):
                end_time = s.get("endTime", "")
                if end_time:
                    end_mins    = _to_mins(end_time)
                    notify_at   = end_mins - END_NOTIFY_MINUTES
                    if notify_at >= 0 and notify_at <= now_mins < notify_at + 2:
                        logger.info(
                            "[light][%s] END-WARN %s ends=%s hall=%s",
                            cinema, s.get("movieTitle"), end_time, hall,
                        )
                        try:
                            await _notify_end(bot, cinema, s)
                            mark_session_notification_sent(cinema, session_id, "endNotifSent")
                        except Exception as exc:
                            logger.error("[light][%s] end notify failed: %s", cinema, exc)
