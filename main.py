import os
import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from bot.handlers.start import start_handler, MAIN_KEYBOARD
from bot.handlers.orders import orders_handler
from bot.handlers.stats import stats_handler
from bot.handlers.staff import staff_handler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


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


async def unknown_handler(update, context):
    await update.message.reply_text(
        "Невідома команда. Скористайтесь кнопками меню або /help.",
        reply_markup=MAIN_KEYBOARD,
    )


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN environment variable not set")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("orders", orders_handler))
    app.add_handler(CommandHandler("staff", staff_handler))
    app.add_handler(CommandHandler("stats", stats_handler))

    # Reply keyboard button handler
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        keyboard_router,
    ))

    app.add_handler(MessageHandler(filters.COMMAND, unknown_handler))

    logger.info("Cinema Staff Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
