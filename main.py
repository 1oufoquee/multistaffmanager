import os
import sys
import logging

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from bot.handlers.start import start_handler, MAIN_KEYBOARD, get_keyboard
from bot.handlers.orders import orders_handler
from bot.handlers.stats import stats_handler
from bot.handlers.staff import staff_handler
from bot.handlers.writeoffs_popcorn import build_writeoff_conversation
from bot.handlers.admin_panel import build_admin_panel
from bot.handlers.cinema_schedule import (
    cinema_schedule_handler,
    handle_schedule_callbacks,
)
from bot.firebase_client import is_authorized_user, get_user_info

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


def get_token() -> str:
    token = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("No bot token found. Set BOT_TOKEN or TELEGRAM_BOT_TOKEN.")
        sys.exit(1)
    return token


def check_firebase_credentials():
    if not os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON"):
        logger.error("FIREBASE_SERVICE_ACCOUNT_JSON is not set.")
        sys.exit(1)


async def help_handler(update: Update, context):
    tid  = update.effective_user.id
    info = get_user_info(tid) if is_authorized_user(tid) else None
    await update.message.reply_text(
        "*Cinema Staff Bot*\n\n"
        "Оберіть розділ за допомогою кнопок нижче або команд:\n"
        "/orders — Замовлення\n"
        "/staff — Працівники\n"
        "/stats — Статистика",
        parse_mode="Markdown",
        reply_markup=get_keyboard(info),
    )


async def keyboard_router(update: Update, context):
    text = update.message.text
    if text == "📦 Замовлення":
        await orders_handler(update, context)
    elif text == "👥 Працівники":
        await staff_handler(update, context)
    elif text == "📊 Статистика":
        await stats_handler(update, context)
    elif text == "🎬 Сеанси":
        await cinema_schedule_handler(update, context)
    # "🍿 Списання" and "👑 Адмін-Панель" are handled by ConversationHandlers


async def unknown_handler(update: Update, context):
    info = get_user_info(update.effective_user.id)
    await update.message.reply_text(
        "Невідома команда. Скористайтесь кнопками меню або /help.",
        reply_markup=get_keyboard(info),
    )


def main():
    logger.info("=== Cinema Staff Bot starting ===")
    check_firebase_credentials()
    token = get_token()
    logger.info("Environment: OK — building application...")

    app = ApplicationBuilder().token(token).build()

    # ── Background job: refresh session schedule every 15 minutes ─────────────
    try:
        from jobs.update_sessions import update_all_cinemas
        app.job_queue.run_repeating(
            update_all_cinemas,
            interval=900,
            first=60,
            name="session_update",
        )
        logger.info("Session update job scheduled (interval=15 min, first=60 s)")
    except Exception as exc:
        logger.warning("Session update job NOT scheduled: %s", exc)

    # ── Background job: light notifications every 60 seconds ──────────────────
    try:
        from jobs.light_notifications import check_light_notifications, NOTIFY_HALL_NAMES, END_NOTIFY_MINUTES
        app.job_queue.run_repeating(
            check_light_notifications,
            interval=60,
            first=30,
            name="light_notifications",
        )
        logger.info(
            "Light notification job scheduled (interval=60 s, halls=%s, end_warn=%d min)",
            NOTIFY_HALL_NAMES, END_NOTIFY_MINUTES,
        )
    except Exception as exc:
        logger.warning("Light notification job NOT scheduled: %s", exc)

    # ── ConversationHandlers — must be registered before the generic text handler
    app.add_handler(build_admin_panel())
    app.add_handler(build_writeoff_conversation())

    # ── Command handlers ──────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",  start_handler))
    app.add_handler(CommandHandler("help",   help_handler))
    app.add_handler(CommandHandler("orders", orders_handler))
    app.add_handler(CommandHandler("staff",  staff_handler))
    app.add_handler(CommandHandler("stats",  stats_handler))

    # ── Callback query handlers ───────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(handle_schedule_callbacks, pattern=r"^cs_"))

    # ── Keyboard / text handler ───────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, keyboard_router))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_handler))

    logger.info("Handlers registered. Starting polling — Bot is ready.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
