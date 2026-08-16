import os
import sys
import logging

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    TypeHandler,
    filters,
)

from bot.handlers.start import start_handler, get_keyboard
from bot.handlers.orders import orders_handler
from bot.handlers.stats import stats_handler
from bot.handlers.staff import staff_handler
from bot.handlers.writeoffs_popcorn import build_writeoff_conversation
from bot.handlers.admin_panel import build_admin_panel
from bot.handlers.developer import (
    CHOOSE_CINEMA_BUTTON,
    SETTINGS_BUTTON,
    CHANGE_CINEMA_BUTTON,
    developer_text_handler,
    handle_developer_callback,
)
from bot.handlers.daily_writeoff_summary import (
    SUMMARY_BUTTON,
    daily_writeoff_summary_handler,
)
from bot.handlers.sessions import (
    sessions_handler,
    handle_ses_today,
    handle_ses_nearest,
    handle_ses_toggle,
    handle_ses_back,
)
from bot.firebase_client import (
    is_authorized_user,
    get_user_info,
    is_feature_enabled,
    activate_project_for_user,
)
from jobs.light_notifications import check_light_notifications, handle_light_confirm

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
        await sessions_handler(update, context)
    elif text == SUMMARY_BUTTON:
        await daily_writeoff_summary_handler(update, context)
    # "🍿 Списання" and "👑 Адмін-Панель" are handled by ConversationHandlers.


async def unknown_handler(update: Update, context):
    info = get_user_info(update.effective_user.id)
    await update.message.reply_text(
        "Невідома команда. Скористайтесь кнопками меню або /help.",
        reply_markup=get_keyboard(info),
    )


async def activate_project_update(update: Update, context):
    """Bind every update to its developer-selected Firebase project."""
    user = update.effective_user
    if not user:
        return
    try:
        activate_project_for_user(user.id)
    except Exception:
        logger.exception(
            "Failed to activate Firebase project for Telegram ID %s",
            user.id,
        )


def main():
    logger.info("=== Cinema Staff Bot starting ===")
    check_firebase_credentials()
    token = get_token()
    logger.info("Environment: OK — building application...")

    app = ApplicationBuilder().token(token).build()

    # Must run before every handler so all Firestore calls use the correct
    # per-developer project without changing the global client for other users.
    app.add_handler(TypeHandler(Update, activate_project_update), group=-1)

    # ── ConversationHandlers — must be registered before the generic text handler
    app.add_handler(build_admin_panel())
    app.add_handler(build_writeoff_conversation())

    # ── Command handlers ──────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",  start_handler))
    app.add_handler(CommandHandler("help",   help_handler))
    app.add_handler(CommandHandler("orders", orders_handler))
    app.add_handler(CommandHandler("staff",  staff_handler))
    app.add_handler(CommandHandler("stats",  stats_handler))

    # ── Sessions callbacks ────────────────────────────────────────────────────
    app.add_handler(CallbackQueryHandler(handle_ses_today,   pattern=r"^ses_today$"))
    app.add_handler(CallbackQueryHandler(handle_ses_nearest, pattern=r"^ses_nearest$"))
    app.add_handler(CallbackQueryHandler(handle_ses_toggle,  pattern=r"^ses_toggle$"))
    app.add_handler(CallbackQueryHandler(handle_ses_back,    pattern=r"^ses_back$"))

    # ── Light-reminder confirmation callbacks ─────────────────────────────────
    app.add_handler(CallbackQueryHandler(handle_light_confirm, pattern=r"^lc_(off|on)_"))
    app.add_handler(
        CallbackQueryHandler(handle_developer_callback, pattern=r"^dev_(change|back|cinema_)")
    )

    # ── Developer-only navigation ─────────────────────────────────────────────
    app.add_handler(
        MessageHandler(
            filters.Regex(
                rf"^({SETTINGS_BUTTON}|{CHANGE_CINEMA_BUTTON}|{CHOOSE_CINEMA_BUTTON})$"
            ),
            developer_text_handler,
        )
    )

    # ── Keyboard / text handler ───────────────────────────────────────────────
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, keyboard_router))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_handler))

    # ── Background job: light notifications (every 60 s) ─────────────────────
    app.job_queue.run_repeating(
        check_light_notifications,
        interval=60,
        first=10,     # first run 10 s after startup to let Firebase warm up
        name="light_notifications",
    )

    logger.info("Handlers registered. Starting polling — Bot is ready.")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
