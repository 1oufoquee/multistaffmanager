"""
Light-control notifications for Hall 4 and Hall 5.

Job runs every 60 seconds and checks today's Kyiv-time schedule:
  - At session startTime        → "Turn OFF the lights"  (🔴)
  - 7 min before session endTime → "Turn ON the lights"   (🟢)

Each notification carries an inline ✅ button. The first press atomically
writes the confirmation to Firestore; all tracked message copies are then
edited to show who confirmed it and the button is removed.

Restart-safe:
  - Past notifications are silently marked as sent so they don't re-fire.
  - bot_data["reminders"] is rebuilt for messages sent in the current run.
  - If a user taps the button on a pre-restart message, the handler reads
    Firestore and edits at least that one message (plus any tracked copies
    from the current session).
"""

import re
import logging
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from bot.firebase_client import (
    get_schedule,
    get_light_reminder_users,
    get_light_confirmation,
    confirm_light_reminder,
    get_user_info,
)
from bot.utils import KYIV_TZ

logger = logging.getLogger(__name__)

# Only Hall 4 and Hall 5
_HALL_PATTERN = re.compile(r"\b[45]\b")


# ── Time helpers ──────────────────────────────────────────────────────────────

def _is_target_hall(hall) -> bool:
    return bool(_HALL_PATTERN.search(str(hall)))


def _parse_to_today_dt(t) -> datetime | None:
    """Convert time value to a tz-aware datetime on today's Kyiv date."""
    if t is None:
        return None
    today = datetime.now(KYIV_TZ).date()
    if hasattr(t, "hour"):
        return datetime(today.year, today.month, today.day,
                        t.hour, t.minute, getattr(t, "second", 0),
                        tzinfo=KYIV_TZ)
    s = str(t).strip()
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            p = datetime.strptime(s, fmt)
            return datetime(today.year, today.month, today.day,
                            p.hour, p.minute, p.second,
                            tzinfo=KYIV_TZ)
        except ValueError:
            continue
    return None


def _fmt(t) -> str:
    """Format a time value as HH:MM."""
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


# ── Reminder ID ───────────────────────────────────────────────────────────────

def _make_reminder_id(date_str: str, hall: str, start_raw, kind: str) -> str:
    """
    Stable doc ID for LightConfirmations, matching the spec:
      {date}_{hallNormalized}_{HH:MM}_light_{off|on}
    """
    hall_norm = str(hall).strip().replace(" ", "")
    time_norm = _fmt(start_raw)
    return f"{date_str}_{hall_norm}_{time_norm}_light_{kind}"


# ── Message text builders ─────────────────────────────────────────────────────

def _build_base_text(kind: str, movie: str, hall_str: str,
                     start_str: str, end_str: str) -> str:
    """Message body WITHOUT the confirmation footer."""
    hall_upper = hall_str.upper()
    if kind == "off":
        header    = f"🔴 {hall_upper} — ВИМКНІТЬ СВІТЛО"
        time_line = f"🕐 {start_str}–{end_str}"
    else:
        header    = f"🟢 {hall_upper} — УВІМКНІТЬ СВІТЛО"
        time_line = f"🕐 Кінець: {end_str}"
    return f"{header}\n\n🎬 {movie}\n{time_line}"


def _append_confirmation(base_text: str, kind: str, conf: dict) -> str:
    """Append the ✅ footer to *base_text* using data from *conf*."""
    name   = conf.get("confirmedByName", "?")
    action = "вимкнено" if kind == "off" else "увімкнено"
    return f"{base_text}\n\n✅ Світло {action} — {name}"


def _confirm_button(reminder_id: str, kind: str) -> InlineKeyboardMarkup:
    label = "✅ Вимкнув" if kind == "off" else "✅ Увімкнув"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(label, callback_data=f"lc_{kind}_{reminder_id}")
    ]])


# ── Sender ────────────────────────────────────────────────────────────────────

async def _send_reminder(
    context: ContextTypes.DEFAULT_TYPE,
    reminder_id: str,
    kind: str,
    base_text: str,
    conf: dict | None,
) -> None:
    """
    Send (or re-send as pre-confirmed) a light reminder to all subscribed users.
    Tracks each (chat_id, message_id) pair in bot_data for later batch-edit.
    """
    try:
        recipients = get_light_reminder_users()
    except Exception as e:
        logger.warning(f"light_notifications: could not fetch recipients: {e}")
        return

    already_confirmed = conf and conf.get("confirmed")
    text    = _append_confirmation(base_text, kind, conf) if already_confirmed else base_text
    kb      = None if already_confirmed else _confirm_button(reminder_id, kind)

    reminders: dict = context.bot_data.setdefault("reminders", {})
    if reminder_id not in reminders:
        reminders[reminder_id] = {"kind": kind, "base_text": base_text, "messages": []}

    for tid in recipients:
        try:
            msg = await context.bot.send_message(
                chat_id=tid,
                text=text,
                parse_mode="Markdown",
                reply_markup=kb,
            )
            reminders[reminder_id]["messages"].append((msg.chat.id, msg.message_id))
        except Exception as e:
            logger.warning(f"light_notifications: failed to send to {tid}: {e}")


# ── Confirmation callback ─────────────────────────────────────────────────────

async def handle_light_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handles lc_off_{reminder_id} and lc_on_{reminder_id} callback queries.

    First press:
      1. Atomically writes confirmation to Firestore.
      2. Edits every tracked message (and the triggering message).
    Subsequent presses: shows an alert and does nothing else.
    """
    query       = update.callback_query
    telegram_id = update.effective_user.id
    data        = query.data  # e.g. "lc_off_2026-08-01_Зал4_15:40_light_off"

    # Parse kind and reminder_id
    if data.startswith("lc_off_"):
        kind        = "off"
        reminder_id = data[len("lc_off_"):]
    elif data.startswith("lc_on_"):
        kind        = "on"
        reminder_id = data[len("lc_on_"):]
    else:
        await query.answer()
        return

    # ── Check current Firestore state ─────────────────────────────────────────
    try:
        existing = get_light_confirmation(reminder_id)
    except Exception as e:
        logger.warning(f"handle_light_confirm: Firestore read error: {e}")
        await query.answer("Помилка сервера, спробуйте ще раз.", show_alert=True)
        return

    if existing and existing.get("confirmed"):
        name = existing.get("confirmedByName", "?")
        action = "вимкнено" if kind == "off" else "увімкнено"
        await query.answer(
            f"Вже підтверджено: світло {action} — {name}",
            show_alert=True,
        )
        # Still try to remove the stale button on this message in case it wasn't updated
        await _edit_one(context.bot, query.message, _build_confirmed_text(query.message.text, existing, kind))
        return

    # ── Attempt atomic first-write ────────────────────────────────────────────
    info    = get_user_info(telegram_id)
    confirmer_name = (info or {}).get("name") or f"ID {telegram_id}"

    conf_data = {
        "confirmed":              True,
        "confirmedByTelegramId":  telegram_id,
        "confirmedByName":        confirmer_name,
        "confirmedAt":            datetime.now(KYIV_TZ).isoformat(),
    }

    try:
        won = confirm_light_reminder(reminder_id, conf_data)
    except Exception as e:
        logger.warning(f"handle_light_confirm: Firestore write error: {e}")
        await query.answer("Помилка збереження, спробуйте ще раз.", show_alert=True)
        return

    if not won:
        # Race — someone else confirmed first
        try:
            existing = get_light_confirmation(reminder_id)
        except Exception:
            existing = None
        name = (existing or {}).get("confirmedByName", "?")
        action = "вимкнено" if kind == "off" else "увімкнено"
        await query.answer(
            f"Вже підтверджено: світло {action} — {name}",
            show_alert=True,
        )
        return

    await query.answer("✅ Підтверджено!")
    logger.info(f"light_confirm: {kind} {reminder_id} confirmed by {confirmer_name} ({telegram_id})")

    # ── Edit ALL tracked messages ─────────────────────────────────────────────
    reminders: dict = context.bot_data.get("reminders", {})
    entry           = reminders.get(reminder_id)
    base_text       = entry["base_text"] if entry else None
    tracked_msgs    = entry["messages"]  if entry else []

    # Build the updated text
    full_text = _append_confirmation(base_text, kind, conf_data) if base_text else None

    edited_pairs: set = set()

    # Edit the message the button was on first
    this_pair = (query.message.chat.id, query.message.message_id)
    text_for_edit = full_text or _build_confirmed_text(query.message.text, conf_data, kind)
    await _edit_one(context.bot, query.message, text_for_edit)
    edited_pairs.add(this_pair)

    # Edit remaining tracked messages
    for chat_id, msg_id in tracked_msgs:
        if (chat_id, msg_id) in edited_pairs:
            continue
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=full_text or text_for_edit,
                parse_mode="Markdown",
                reply_markup=None,
            )
        except Exception as e:
            logger.warning(f"handle_light_confirm: could not edit {chat_id}/{msg_id}: {e}")
        edited_pairs.add((chat_id, msg_id))


def _build_confirmed_text(original_text: str | None, conf: dict, kind: str) -> str:
    """
    Fallback for post-restart edits when base_text isn't in bot_data.
    Strips any previous confirmation footer then appends a fresh one.
    """
    base = (original_text or "").split("\n\n✅")[0]  # strip old footer if any
    return _append_confirmation(base, kind, conf)


async def _edit_one(bot, message, text: str) -> None:
    try:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message.message_id,
            text=text,
            parse_mode="Markdown",
            reply_markup=None,
        )
    except Exception as e:
        logger.warning(f"handle_light_confirm: could not edit own message: {e}")


# ── Main job ──────────────────────────────────────────────────────────────────

async def check_light_notifications(context: ContextTypes.DEFAULT_TYPE) -> None:
    now      = datetime.now(KYIV_TZ)
    date_str = now.strftime("%Y-%m-%d")
    sent: set = context.bot_data.setdefault("sent_notifications", set())

    try:
        schedule = get_schedule(date_str)
    except Exception as e:
        logger.warning(f"light_notifications: could not load schedule: {e}")
        return

    if not schedule:
        return

    sessions = schedule.get("sessions", [])
    WINDOW   = 90  # seconds — fire within this window after the trigger time

    for session in sessions:
        hall = session.get("hall", "")
        if not _is_target_hall(hall):
            continue

        start_dt = _parse_to_today_dt(session.get("startTime"))
        end_dt   = _parse_to_today_dt(session.get("endTime"))
        if start_dt is None or end_dt is None:
            continue

        movie     = session.get("movie", "?")
        hall_str  = str(hall).strip()
        start_raw = session.get("startTime", "")
        start_str = _fmt(start_raw)
        end_str   = _fmt(session.get("endTime"))

        rid_off = _make_reminder_id(date_str, hall_str, start_raw, "off")
        rid_on  = _make_reminder_id(date_str, hall_str, start_raw, "on")

        # ── Lights OFF at session start ───────────────────────────────────────
        if rid_off not in sent:
            delta = (now - start_dt).total_seconds()
            if 0 <= delta <= WINDOW:
                base = _build_base_text("off", movie, hall_str, start_str, end_str)
                try:
                    conf = get_light_confirmation(rid_off)
                except Exception:
                    conf = None
                logger.info(f"light_notifications: OFF → {hall_str} '{movie}' {start_str}")
                await _send_reminder(context, rid_off, "off", base, conf)
                sent.add(rid_off)
            elif delta > WINDOW:
                sent.add(rid_off)   # past — skip silently on restart

        # ── Lights ON — 7 minutes before session end ──────────────────────────
        notify_on_dt = end_dt - timedelta(minutes=+5)
        if rid_on not in sent:
            delta = (now - notify_on_dt).total_seconds()
            if 0 <= delta <= WINDOW:
                base = _build_base_text("on", movie, hall_str, start_str, end_str)
                try:
                    conf = get_light_confirmation(rid_on)
                except Exception:
                    conf = None
                logger.info(f"light_notifications: ON  → {hall_str} '{movie}' end {end_str}")
                await _send_reminder(context, rid_on, "on", base, conf)
                sent.add(rid_on)
            elif delta > WINDOW:
                sent.add(rid_on)
