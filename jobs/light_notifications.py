"""
Light Notification Job
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Runs every 60 seconds via PTB JobQueue.

Uses the NEW schedule collection:
  Cinema/{cinema}/cinema_schedule/{date}/sessions/{sessionId}

Document fields: movie, hall (SessionScreenName), startTime (ISO 8601),
endTime (ISO 8601), startNotifSent, endNotifSent.

Algorithm per cinema:
  1. Load today's sessions from cinema_schedule.
  2. Filter to halls listed in NOTIFY_SCHEDULE_HALLS.
     → Configure NOTIFY_SCHEDULE_HALLS=Зал №2,Зал №3 to restrict to
       Hall 4 and Hall 5 only; leave unset to notify all halls.
  3. For sessions whose startTime has been reached (within 2-min window):
       → send "turn OFF lights" notification (once, guarded by startNotifSent).
  4. For sessions END_NOTIFY_MINUTES before their endTime:
       → send "prepare to turn ON lights" notification (guarded by endNotifSent).

Hall display uses SCHEDULE_HALL_MAP for the full label, e.g. "Зал 4 (VIP)".
"""

import logging
from datetime import datetime, timezone, timedelta

from telegram.ext import ContextTypes

from bot.firebase_client import (
    get_schedule_sessions,
    get_cinema_staff_tids,
    mark_schedule_session_notification_sent,
)
from bot.hall_config import (
    END_NOTIFY_MINUTES,
    NOTIFY_SCHEDULE_HALLS,
    schedule_hall_label,
    should_notify_schedule_hall,
)

logger = logging.getLogger(__name__)

_KYIV_TZ = timezone(timedelta(hours=3))
_CINEMAS  = ["atmosfera", "karavan"]

# Time window (seconds) within which a notification is fired.
# Absorbs scheduler drift; Firestore flag prevents double-sends.
_NOTIFY_WINDOW_SECS = 120


def _now_kyiv() -> datetime:
    return datetime.now(tz=_KYIV_TZ)


def _parse_iso(iso: str) -> datetime | None:
    try:
        return datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None


def _in_window(target_dt: datetime, now: datetime) -> bool:
    """True if now ∈ [target_dt, target_dt + _NOTIFY_WINDOW_SECS)."""
    diff = (now - target_dt).total_seconds()
    return 0 <= diff < _NOTIFY_WINDOW_SECS


# ── Notification senders ──────────────────────────────────────────────────────

async def _broadcast(bot, cinema: str, text: str) -> None:
    tids = get_cinema_staff_tids(cinema)
    if not tids:
        logger.warning("[light] No staff for cinema=%s", cinema)
        return
    for tid in tids:
        try:
            await bot.send_message(chat_id=tid, text=text, parse_mode="Markdown")
            logger.info("[light] → %d (cinema=%s)", tid, cinema)
        except Exception as exc:
            logger.warning("[light] Could not send to %d: %s", tid, exc)


async def _notify_start(bot, cinema: str, session: dict) -> None:
    label = schedule_hall_label(session.get("hall", ""))
    movie = session.get("movie", "—")
    await _broadcast(
        bot, cinema,
        f"💡 *{label}*\n\n"
        f"Сеанс розпочався:\n*{movie}*\n\n"
        f"Будь ласка, *вимкніть світло*.",
    )


async def _notify_end(bot, cinema: str, session: dict) -> None:
    label = schedule_hall_label(session.get("hall", ""))
    movie = session.get("movie", "—")
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
    Reads from Cinema/{cinema}/cinema_schedule/{today}/sessions.
    """
    now       = _now_kyiv()
    today_str = now.strftime("%Y-%m-%d")
    bot       = context.bot

    logger.debug(
        "[light] tick Kyiv=%s  notify_halls=%s  end_warn=%d min",
        now.strftime("%H:%M"),
        "ALL" if not NOTIFY_SCHEDULE_HALLS else str(NOTIFY_SCHEDULE_HALLS),
        END_NOTIFY_MINUTES,
    )

    for cinema in _CINEMAS:
        try:
            sessions = get_schedule_sessions(cinema, today_str)
        except Exception as exc:
            logger.error("[light][%s] get_schedule_sessions failed: %s", cinema, exc)
            continue

        for s in sessions:
            hall = s.get("hall", "").strip()

            # ── Hall filter ───────────────────────────────────────────────────
            if not should_notify_schedule_hall(hall):
                continue

            session_id = s.get("_id") or s.get("sessionId", "")
            if not session_id:
                continue

            # ── Session start → turn lights OFF ──────────────────────────────
            if not s.get("startNotifSent", False):
                start_dt = _parse_iso(s.get("startTime", ""))
                if start_dt and _in_window(start_dt, now):
                    label = schedule_hall_label(hall)
                    logger.info(
                        "[light][%s] START  %s  %s  hall=%s",
                        cinema, s.get("startTime"), s.get("movie"), label,
                    )
                    try:
                        await _notify_start(bot, cinema, s)
                        mark_schedule_session_notification_sent(
                            cinema, today_str, str(session_id), "startNotifSent"
                        )
                    except Exception as exc:
                        logger.error("[light][%s] start notify error: %s", cinema, exc)

            # ── N min before end → prepare to turn lights ON ──────────────────
            if not s.get("endNotifSent", False):
                end_dt = _parse_iso(s.get("endTime", ""))
                if end_dt:
                    notify_dt = end_dt - timedelta(minutes=END_NOTIFY_MINUTES)
                    if _in_window(notify_dt, now):
                        label = schedule_hall_label(hall)
                        logger.info(
                            "[light][%s] END-WARN  %s  ends=%s  hall=%s",
                            cinema, s.get("movie"), s.get("endTime"), label,
                        )
                        try:
                            await _notify_end(bot, cinema, s)
                            mark_schedule_session_notification_sent(
                                cinema, today_str, str(session_id), "endNotifSent"
                            )
                        except Exception as exc:
                            logger.error("[light][%s] end notify error: %s", cinema, exc)
