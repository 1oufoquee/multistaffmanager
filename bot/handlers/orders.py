from telegram import Update
from telegram.ext import ContextTypes
from bot.firebase_client import is_authorized_user, get_active_orders
from bot.utils import format_timestamp


async def orders_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    if not is_authorized_user(telegram_id):
        await update.message.reply_text("Access denied.")
        return

    await update.message.reply_text("Fetching active orders...")

    try:
        orders = get_active_orders()
    except Exception as e:
        await update.message.reply_text(f"Error fetching orders: {e}")
        return

    if not orders:
        await update.message.reply_text("No active orders at the moment.")
        return

    lines = [f"*Active Orders ({len(orders)})*\n"]
    for i, order in enumerate(orders, 1):
        order_id = order.get("id", "N/A")
        customer = order.get("customerName", order.get("customer", "—"))
        items = order.get("items", [])
        total = order.get("total", order.get("totalAmount", 0))
        hall = order.get("hall", order.get("seatNumber", "—"))
        created = format_timestamp(order.get("createdAt"))

        items_str = ""
        if isinstance(items, list):
            item_names = []
            for it in items:
                if isinstance(it, dict):
                    n = it.get("name", it.get("title", ""))
                    q = it.get("quantity", it.get("qty", 1))
                    item_names.append(f"{n} x{q}" if n else str(it))
                else:
                    item_names.append(str(it))
            items_str = ", ".join(item_names) if item_names else "—"
        elif isinstance(items, str):
            items_str = items
        else:
            items_str = "—"

        lines.append(
            f"*{i}. Order #{order_id[:8]}*\n"
            f"  Customer: {customer}\n"
            f"  Hall/Seat: {hall}\n"
            f"  Items: {items_str}\n"
            f"  Total: {total} UAH\n"
            f"  Created: {created}\n"
        )

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(truncated)"

    await update.message.reply_text(text, parse_mode="Markdown")
