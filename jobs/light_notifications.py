"""
Light-control notifications for Hall 4 and Hall 5.

Job runs every 60 seconds and checks today's schedule:
  - At session startTime  → "Turn OFF the lights"
  - 7 min before endTime  → "Turn ON the lights"

Sent-notification keys are stored in context.bot_data["sent_notifications"]
(a set of strings) so duplicates are never sent.  On restart the set starts
empty, but the first job run immediately marks past events as already sent
without re-sending them — this prevents flooding after a bot restart.
"""

import re
import logging
from datetime import datetime, timedelta

from telegram.ext import ContextTypes

from bot.firebase_client import get_schedule, get_light_reminder_users

logger = logging.getLogger(__name__)

# Only notify for these halls (matched as standalone digit in the hall string)
_HALL_PATTERN = re.compile(r"\b[45]\b")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_target_hall(hall) -> bool:
    return bool(_HALL_PATTERN.search(str(hall)))


def _parse_to_today_dt(t) -> datetime | None:
    """Convert time value to a datetime on today's date."""
    if t is None:
        return None
    today = datetime.now().date()
    if hasattr(t, "hour"):          # already a datetime / time object
        return datetime(today.year, today.month, today.day, t.hour, t.minute, getattr(t, "second", 0))
    s = str(t).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            p = datetime.strptime(s, fmt)
            return datetime(today.year, today.month, today.day, p.hour, p.minute, p.second)
        except ValueError:
            continue
    return None


def _fmt(t) -> str:
    """Format time value as HH:MM for display."""
    if t is None:
        return "?"
    if isinstance(t, str):
        parts = t.strip().split(":")
        if len(parts) >= 2:
            return f"{int(parts[0]):02d}:{parts[1][:2]}"
        return t.strip()
    if hasattr(t, "strftime"):
        return t.strftime("%H:%M")
    return str(t)


# ── Notification sender ───────────────────────────────────────────────────────

async def _send_to_all(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    try:
        recipients = get_light_reminder_users()
    except Exception as e:
        logger.warning(f"light_notifications: could not fetch recipients: {e}")
        return

    for tid in recipients:
        try:
            await context.bot.send_message(chat_id=tid, text=text)
        except Exception as e:
            logger.warning(f"light_notifications: failed to send to {tid}: {e}")


# ── Main job ──────────────────────────────────────────────────────────────────

async def check_light_notifications(context: ContextTypes.DEFAULT_TYPE) -> None:
    now       = datetime.now()
    date_str  = now.strftime("%Y-%m-%d")
    sent: set = context.bot_data.setdefault("sent_notifications", set())

    try:
        schedule = get_schedule(date_str)
    except Exception as e:
        logger.warning(f"light_notifications: could not load schedule: {e}")
        return

    if not schedule:
        return

    sessions = schedule.get("sessions", [])
    WINDOW   = 90  # seconds — notifications fire within this window after the trigger time

    for session in sessions:
        hall = session.get("hall", "")
        if not _is_target_hall(hall):
            continue

        start_dt = _parse_to_today_dt(session.get("startTime"))
        end_dt   = _parse_to_today_dt(session.get("endTime"))

        if start_dt is None or end_dt is None:
            continue

        movie    = session.get("movie", "?")
        hall_str = str(hall).strip()
        start_raw = session.get("startTime", "")

        # Stable key — uniquely identifies this session on this date
        key_base = f"{date_str}|{hall_str}|{start_raw}"
        key_off  = f"{key_base}|off"   # lights OFF at start
        key_on   = f"{key_base}|on"    # lights ON  7 min before end

        # ── Lights OFF at session start ───────────────────────────────────────
        if key_off not in sent:
            delta = (now - start_dt).total_seconds()
            if delta < 0:
                pass  # not yet
            elif delta <= WINDOW:
                msg = (
                    f"🔴 *Вимкніть світло*\n\n"
                    f"🎬 {movie}\n"
                    f"📍 {hall_str}\n"
                    f"⏰ Початок: {_fmt(session.get('startTime'))}"
                )
                logger.info(f"light_notifications: OFF → {hall_str} '{movie}' {_fmt(session.get('startTime'))}")
                await _send_to_all(context, msg)
                sent.add(key_off)
            else:
                # Past — mark as sent without notifying (prevents resend after restart)
                sent.add(key_off)

        # ── Lights ON — 7 minutes before session end ──────────────────────────
        notify_on_dt = end_dt - timedelta(minutes=7)
        if key_on not in sent:
            delta = (now - notify_on_dt).total_seconds()
            if delta < 0:
                pass  # not yet
            elif delta <= WINDOW:
                msg = (
                    f"🟢 *Увімкніть світло*\n\n"
                    f"🎬 {movie}\n"
                    f"📍 {hall_str}\n"
                    f"⏰ Кінець: {_fmt(session.get('endTime'))}"
                )
                logger.info(f"light_notifications: ON  → {hall_str} '{movie}' end {_fmt(session.get('endTime'))}")
                await _send_to_all(context, msg)
                sent.add(key_on)
            else:
                sent.add(key_on)
