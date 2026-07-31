import logging
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from bot.firebase_client import (
    is_authorized_user, get_user_info, get_schedule, toggle_light_reminders,
)

logger = logging.getLogger(__name__)


# ── Time helpers ──────────────────────────────────────────────────────────────

def _time_display(t) -> str:
    """Format a time value (string 'HH:MM[:SS]' or datetime) as 'HH:MM'."""
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


def _time_to_minutes(t) -> int | None:
    """Convert time value to total minutes since midnight for sorting/comparison."""
    if t is None:
        return None
    if isinstance(t, str):
        parts = t.strip().split(":")
        try:
            return int(parts[0]) * 60 + int(parts[1])
        except (IndexError, ValueError):
            return None
    if hasattr(t, "hour"):
        return t.hour * 60 + t.minute
    return None


def _sort_key(session: dict) -> int:
    return _time_to_minutes(session.get("startTime")) or 9999


# ── Menu builder ──────────────────────────────────────────────────────────────

def _sessions_menu_keyboard(reminders_on: bool) -> InlineKeyboardMarkup:
    reminder_label = (
        "💡 Нагадування про світло: ✅ Увімк."
        if reminders_on
        else "💡 Нагадування про світло: ❌ Вимк."
    )
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 Сьогоднішні сеанси", callback_data="ses_today")],
        [InlineKeyboardButton("🎯 Найближчий сеанс",   callback_data="ses_nearest")],
        [InlineKeyboardButton(reminder_label,           callback_data="ses_toggle")],
    ])


# ── Entry point ───────────────────────────────────────────────────────────────

async def sessions_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    telegram_id = update.effective_user.id
    if not is_authorized_user(telegram_id):
        await update.message.reply_text("Доступ заборонено.")
        return

    info = get_user_info(telegram_id)
    reminders_on = bool((info or {}).get("lightReminders", False))

    await update.message.reply_text(
        "🎬 *Сеанси*\n\nОберіть дію:",
        parse_mode="Markdown",
        reply_markup=_sessions_menu_keyboard(reminders_on),
    )


# ── Today's sessions ──────────────────────────────────────────────────────────

async def handle_ses_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    today_str = datetime.now().strftime("%Y-%m-%d")
    today_label = datetime.now().strftime("%d.%m.%Y")

    try:
        schedule = get_schedule(today_str)
    except Exception as e:
        await query.edit_message_text(f"❌ Помилка завантаження: {e}")
        return

    back_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("◀ Назад", callback_data="ses_back")
    ]])

    if not schedule:
        await query.edit_message_text(
            f"📅 *Сеанси на {today_label}*\n\nРозклад не знайдено.",
            parse_mode="Markdown",
            reply_markup=back_kb,
        )
        return

    sessions = sorted(schedule.get("sessions", []), key=_sort_key)

    if not sessions:
        await query.edit_message_text(
            f"📅 *Сеанси на {today_label}*\n\nСеансів немає.",
            parse_mode="Markdown",
            reply_markup=back_kb,
        )
        return

    lines = [f"📅 *Сеанси на {today_label}* — {len(sessions)} сеансів\n"]
    for s in sessions:
        movie  = s.get("movie", "?")
        hall   = s.get("hall", "?")
        start  = _time_display(s.get("startTime"))
        end    = _time_display(s.get("endTime"))
        lines.append(f"🎬 *{movie}*")
        lines.append(f"   📍 {hall}  |  ⏰ {start}–{end}")
        lines.append("")

    text = "\n".join(lines).rstrip()
    if len(text) > 4000:
        text = text[:4000] + "\n...(скорочено)"

    await query.edit_message_text(
        text,
        parse_mode="Markdown",
        reply_markup=back_kb,
    )


# ── Nearest session ───────────────────────────────────────────────────────────

async def handle_ses_nearest(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    back_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("◀ Назад", callback_data="ses_back")
    ]])

    now         = datetime.now()
    now_minutes = now.hour * 60 + now.minute
    today_str   = now.strftime("%Y-%m-%d")

    nearest   = None
    is_today  = True
    when_label = ""

    try:
        schedule = get_schedule(today_str)
    except Exception as e:
        await query.edit_message_text(f"❌ Помилка завантаження: {e}", reply_markup=back_kb)
        return

    if schedule:
        sessions = sorted(schedule.get("sessions", []), key=_sort_key)
        for s in sessions:
            start_min = _time_to_minutes(s.get("startTime"))
            if start_min is not None and start_min > now_minutes:
                nearest  = s
                is_today = True
                diff_min = start_min - now_minutes
                when_label = f"🕐 Через {diff_min} хв"
                break

    if nearest is None:
        # Try tomorrow
        tomorrow_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            tomorrow_schedule = get_schedule(tomorrow_str)
        except Exception as e:
            await query.edit_message_text(f"❌ Помилка завантаження: {e}", reply_markup=back_kb)
            return

        if tomorrow_schedule:
            tomorrow_sessions = sorted(tomorrow_schedule.get("sessions", []), key=_sort_key)
            if tomorrow_sessions:
                nearest   = tomorrow_sessions[0]
                is_today  = False
                start_str = _time_display(nearest.get("startTime"))
                tomorrow_label = (now + timedelta(days=1)).strftime("%d.%m")
                when_label = f"🕐 Завтра ({tomorrow_label}) о {start_str}"

    if nearest is None:
        await query.edit_message_text(
            "🎯 *Найближчий сеанс*\n\nСеансів не знайдено.",
            parse_mode="Markdown",
            reply_markup=back_kb,
        )
        return

    movie = nearest.get("movie", "?")
    hall  = nearest.get("hall", "?")
    start = _time_display(nearest.get("startTime"))
    end   = _time_display(nearest.get("endTime"))
    label = "сьогодні" if is_today else "завтра"

    text = (
        f"🎯 *Найближчий сеанс* ({label})\n\n"
        f"🎬 {movie}\n"
        f"📍 {hall}\n"
        f"⏰ {start}–{end}\n"
        f"{when_label}"
    )

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb)


# ── Toggle light reminders ────────────────────────────────────────────────────

async def handle_ses_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query       = update.callback_query
    telegram_id = update.effective_user.id
    await query.answer()

    try:
        new_val = toggle_light_reminders(telegram_id)
    except Exception as e:
        await query.answer(f"Помилка: {e}", show_alert=True)
        return

    status = "✅ увімкнено" if new_val else "❌ вимкнено"
    await query.answer(f"Нагадування про світло {status}", show_alert=True)

    # Refresh the menu with updated toggle state
    await query.edit_message_reply_markup(
        reply_markup=_sessions_menu_keyboard(new_val)
    )


# ── Back to sessions menu ─────────────────────────────────────────────────────

async def handle_ses_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query       = update.callback_query
    telegram_id = update.effective_user.id
    await query.answer()

    info = get_user_info(telegram_id)
    reminders_on = bool((info or {}).get("lightReminders", False))

    await query.edit_message_text(
        "🎬 *Сеанси*\n\nОберіть дію:",
        parse_mode="Markdown",
        reply_markup=_sessions_menu_keyboard(reminders_on),
    )
