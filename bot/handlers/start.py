from telegram import Update
from telegram.ext import ContextTypes
from bot.firebase_client import is_authorized_user, get_user_info


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    telegram_id = user.id

    if not is_authorized_user(telegram_id):
        await update.message.reply_text(
            "Access denied. Your Telegram ID is not registered in the system.\n\n"
            f"Your Telegram ID: `{telegram_id}`\n"
            "Please contact your administrator.",
            parse_mode="Markdown"
        )
        return

    info = get_user_info(telegram_id)
    name = info.get("name", user.first_name) if info else user.first_name

    await update.message.reply_text(
        f"Welcome, *{name}*!\n\n"
        "You have access to the cinema staff panel.\n\n"
        "Available commands:\n"
        "/orders — Active orders\n"
        "/stats — Sales statistics\n"
        "/writeoffs — View recent write-offs\n"
        "/addwriteoff — Record a new write-off\n"
        "/help — Show this menu",
        parse_mode="Markdown"
    )
