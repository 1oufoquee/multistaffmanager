from telegram import Update
from telegram.ext import ContextTypes
from bot.firebase_client import is_authorized_user, get_statistics


async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    if not is_authorized_user(telegram_id):
        await update.message.reply_text("Доступ заборонено.")
        return

    await update.message.reply_text("Підрахунок статистики...")

    try:
        stats = get_statistics()
    except Exception as e:
        await update.message.reply_text(f"Помилка завантаження: {e}")
        return

    text = (
        "*📊 Статистика*\n\n"
        f"Всього замовлень: *{stats['total_orders']}*\n\n"
        f"🟢 Активні: *{stats['active']}*\n"
        f"✅ Виконані: *{stats['completed']}*\n"
        f"❌ Скасовані: *{stats['cancelled']}*\n\n"
        f"💰 Виручка: *{stats['total_revenue']:.2f} грн*"
    )

    await update.message.reply_text(text, parse_mode="Markdown")
