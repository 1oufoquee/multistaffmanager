from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler

from bot.firebase_client import (
    is_authorized_user,
    get_user_info,
    update_staff_user,
)

# States
CS_HOME = 500


def _kb(*rows):
    return InlineKeyboardMarkup(list(rows))


def _btn(text, data):
    return InlineKeyboardButton(text, callback_data=data)


SCHEDULE_KB = _kb(
    [_btn("🎞 Найближчий сеанс", "cs_next")],
    [_btn("💡 Нагадування світла", "cs_light")],
)


async def cinema_schedule_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id

    if not is_authorized_user(telegram_id):
        await update.message.reply_text("⛔ Доступ заборонено.")
        return

    await update.message.reply_text(
        "🎬 *Сеанси*",
        parse_mode="Markdown",
        reply_markup=SCHEDULE_KB,
    )


async def handle_schedule_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    d = query.data
    telegram_id = update.effective_user.id

    if d == "cs_next":
        await query.edit_message_text(
            "🎞 Найближчий сеанс\n\n(тут буде логіка розкладу)",
        )

    elif d == "cs_light":
        info = get_user_info(telegram_id) or {}

        enabled = info.get("lightReminders", False)
        new_state = not enabled

        update_staff_user(
            info["_id"],
            {"lightReminders": new_state},
        )

        state_text = "УВІМК" if new_state else "ВИМК"

        await query.edit_message_text(
            f"💡 Нагадування світла: {state_text}",
            reply_markup=SCHEDULE_KB,
        )