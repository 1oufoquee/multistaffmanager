from telegram import Update
from telegram.ext import ContextTypes
from bot.firebase_client import is_authorized_user, get_orders
from bot.utils import format_timestamp, format_items


async def orders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    if not is_authorized_user(telegram_id):
        await update.message.reply_text("Доступ заборонено.")
        return

    await update.message.reply_text("Завантаження замовлень...")

    try:
        orders = get_orders()
    except Exception as e:
        await update.message.reply_text(f"Помилка завантаження: {e}")
        return

    if not orders:
        await update.message.reply_text("Замовлень не знайдено.")
        return

    lines = [f"*📦 Замовлення ({len(orders)})*\n"]
    for i, order in enumerate(orders, 1):
        seat_id = order.get("seatId", "—")
        status = order.get("status", "—")
        total = order.get("total", 0)
        user_id = order.get("userId", "—")
        created = format_timestamp(order.get("createdAt"))
        items_str = format_items(order.get("items", []))

        lines.append(
            f"*{i}. Місце:* {seat_id}\n"
            f"  Статус: {status}\n"
            f"  Позиції: {items_str}\n"
            f"  Сума: {total} грн\n"
            f"  Клієнт: {user_id}\n"
            f"  Час: {created}\n"
        )

    text = "\n".join(lines)
    # Split into chunks if too long
    chunks = _split_text(text, 4000)
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="Markdown")


def _split_text(text: str, max_len: int) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks = []
    while len(text) > max_len:
        split_at = text.rfind("\n", 0, max_len)
        if split_at == -1:
            split_at = max_len
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks
