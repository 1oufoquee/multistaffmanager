import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from bot.handlers.start import start_handler
from bot.handlers.orders import orders_handler
from bot.handlers.stats import stats_handler
from bot.handlers.writeoffs import writeoffs_handler, build_addwriteoff_conversation

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def help_handler(update, context):
    await update.message.reply_text(
        "*Cinema Staff Bot*\n\n"
        "Available commands:\n"
        "/orders — View active orders\n"
        "/stats — Sales statistics\n"
        "/writeoffs — View recent write-offs\n"
        "/addwriteoff — Record a new write-off\n"
        "/help — Show this menu",
        parse_mode="Markdown"
    )


async def unknown_handler(update, context):
    await update.message.reply_text(
        "Unknown command. Use /help to see available commands."
    )


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("orders", orders_handler))
    app.add_handler(CommandHandler("stats", stats_handler))
    app.add_handler(CommandHandler("writeoffs", writeoffs_handler))
    app.add_handler(build_addwriteoff_conversation())
    app.add_handler(MessageHandler(filters.COMMAND, unknown_handler))

    logger.info("Cinema Staff Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
