from telegram import Update
from telegram.ext import ContextTypes
from bot.firebase_client import is_authorized_user, get_all_staff


async def staff_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    if not is_authorized_user(telegram_id):
        await update.message.reply_text("Доступ заборонено.")
        return

    await update.message.reply_text("Завантаження списку працівників...")

    try:
        staff = get_all_staff()
    except Exception as e:
        await update.message.reply_text(f"Помилка завантаження: {e}")
        return

    if not staff:
        await update.message.reply_text("Працівників не знайдено.")
        return

    lines = [f"*👥 Працівники ({len(staff)})*\n"]
    for i, member in enumerate(staff, 1):
        app_user_id = member.get("_id", "—")
        name = member.get("name", "—")
        telegram_id = member.get("telegramId", "—")
        role = member.get("userRole", "")
        role_str = f" · {role}" if role else ""
        lines.append(
            f"{i}. *{name}*{role_str}\n"
            f"   App ID: `{app_user_id}`\n"
            f"   Telegram ID: `{telegram_id}`"
        )

    text = "\n".join(lines)
    await update.message.reply_text(text, parse_mode="Markdown")
