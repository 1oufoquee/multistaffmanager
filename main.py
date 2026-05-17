import os
import sys
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from bot.handlers.start import start_handler, MAIN_KEYBOARD
from bot.handlers.orders import orders_handler
from bot.handlers.stats import stats_handler
from bot.handlers.staff import staff_handler
from bot.handlers.writeoffs_popcorn import build_writeoff_conversation

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def get_token() -> str:
    token = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("No bot token found. Set BOT_TOKEN environment variable.")
        sys.exit(1)
    return token


def check_firebase_credentials():
    if not os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON"):
        logger.error("FIREBASE_SERVICE_ACCOUNT_JSON environment variable is not set.")
        sys.exit(1)


async def help_handler(update, context):
    await update.message.reply_text(
        "*Cinema Staff Bot*\n\n"
        "Оберіть розділ за допомогою кнопок нижче або команд:\n"
        "/orders — Замовлення\n"
        "/staff — Працівники\n"
        "/stats — Статистика",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


async def keyboard_router(update: Update, context):
    text = update.message.text
    if text == "📦 Замовлення":
        await orders_handler(update, context)
    elif text == "👥 Працівники":
        await staff_handler(update, context)
    elif text == "📊 Статистика":
        await stats_handler(update, context)
    # "🍿 Списання" is handled by the ConversationHandler — no case needed here


async def unknown_handler(update, context):
    await update.message.reply_text(
        "Невідома команда. Скористайтесь кнопками меню або /help.",
        reply_markup=MAIN_KEYBOARD,
    )


def main():
    logger.info("=== Cinema Staff Bot starting ===")

    check_firebase_credentials()
    token = get_token()

    logger.info("Environment: OK")
    logger.info("Building application...")

    app = ApplicationBuilder().token(token).build()

    # ConversationHandler must be registered BEFORE general text handler
    app.add_handler(build_writeoff_conversation())

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("orders", orders_handler))
    app.add_handler(CommandHandler("staff", staff_handler))
    app.add_handler(CommandHandler("stats", stats_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, keyboard_router))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_handler))

    logger.info("Handlers registered: /start /help /orders /staff /stats + keyboard + writeoffs")
    logger.info("Starting polling... Bot is ready.")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
