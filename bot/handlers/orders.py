from telegram import Update
from telegram.ext import ContextTypes
from bot.firebase_client import is_authorized_user, get_orders
from bot.utils import format_timestamp, format_items, format_seat_id

STATUS_EMOJI = {
    "active": "🟢",
    "completed": "✅",
    "cancelled": "❌",
    "pending": "🕐",
}


async def orders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    if not is_authorized_user(telegram_id):
        await update.message.reply_text("Доступ заборонено.")
        return

    await update.message.reply_text("⏳ Завантаження замовлень...")

    try:
        orders = get_orders()
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка завантаження: {e}")
        return

    if not orders:
        await update.message.reply_text("📭 Замовлень не знайдено.")
        return

    lines = [f"📦 *Замовлення* — {len(orders)} шт\.\n"]
    for order in orders:
        seat_id = format_seat_id(str(order.get("seatId", "—")))
        total = order.get("total", 0)
        user_id = order.get("userId", "—")
        created = format_timestamp(order.get("createdAt"))
        items_str = format_items(order.get("items", []))

        lines.append(
            f"🪑 *{_esc(seat_id)}*\n"
            f"📋 {_esc(items_str)}\n"
            f"💰 {_esc(str(total))} грн\n"
            f"🕐 {_esc(created)}\n"
            f"👤 `{user_id}`"
        )
        lines.append("─────────────────")

    # Remove trailing separator
    if lines and lines[-1].startswith("─"):
        lines.pop()

    text = "\n".join(lines)
    chunks = _split_text(text, 4000)
    for chunk in chunks:
        await update.message.reply_text(chunk, parse_mode="MarkdownV2")


def _esc(text: str) -> str:
    """Escape special characters for MarkdownV2."""
    special = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in str(text))


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
