from telegram import Update
from telegram.ext import ContextTypes
from bot.firebase_client import is_authorized_user, get_orders
from bot.utils import format_timestamp, format_items

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
        seat_id = order.get("seatId", "—")
        raw_status = order.get("status", "—")
        status_icon = STATUS_EMOJI.get(raw_status, "❔")
        status_label = _esc(raw_status)
        total = order.get("total", 0)
        user_id = order.get("userId", "—")
        created = format_timestamp(order.get("createdAt"))
        items_str = format_items(order.get("items", []))

        lines.append(
            f"🪑 *Місце:* {_esc(str(seat_id))}\n"
            f"📋 *Позиції:* {_esc(items_str)}\n"
            f"💰 *Сума:* {_esc(str(total))} грн\n"
            f"{status_icon} *Статус:* {status_label}\n"
            f"🕐 *Час:* {_esc(created)}\n"
            f"👤 *Клієнт:* `{user_id}`"
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
