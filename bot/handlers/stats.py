from telegram import Update
from telegram.ext import ContextTypes
from bot.firebase_client import is_authorized_user, get_statistics


async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    if not is_authorized_user(telegram_id):
        await update.message.reply_text("Access denied.")
        return

    await update.message.reply_text("Calculating statistics...")

    try:
        stats = get_statistics()
    except Exception as e:
        await update.message.reply_text(f"Error fetching statistics: {e}")
        return

    status_breakdown = ""
    for status, count in stats.get("status_counts", {}).items():
        status_breakdown += f"  • {status}: {count}\n"

    text = (
        "*Cinema Sales Statistics*\n\n"
        f"Total orders: *{stats['total_orders']}*\n"
        f"Active: *{stats['active']}*\n"
        f"Completed: *{stats['completed']}*\n"
        f"Cancelled: *{stats['cancelled']}*\n\n"
        f"Total Revenue: *{stats['total_revenue']:.2f} UAH*\n"
    )

    if status_breakdown:
        text += f"\nBreakdown by status:\n{status_breakdown}"

    await update.message.reply_text(text, parse_mode="Markdown")
