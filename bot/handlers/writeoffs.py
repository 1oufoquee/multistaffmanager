from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, filters
from bot.firebase_client import is_authorized_user, get_writeoffs, add_writeoff, get_user_info
from bot.utils import format_timestamp

ITEM_NAME, QUANTITY, UNIT, REASON = range(4)

_writeoff_data: dict[int, dict] = {}


async def writeoffs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    if not is_authorized_user(telegram_id):
        await update.message.reply_text("Access denied.")
        return

    await update.message.reply_text("Fetching recent write-offs...")

    try:
        items = get_writeoffs()
    except Exception as e:
        await update.message.reply_text(f"Error fetching write-offs: {e}")
        return

    if not items:
        await update.message.reply_text("No write-offs recorded yet.")
        return

    lines = [f"*Recent Write-offs ({len(items)})*\n"]
    for i, item in enumerate(items, 1):
        name = item.get("itemName", "—")
        qty = item.get("quantity", "—")
        unit = item.get("unit", "")
        reason = item.get("reason", "—")
        staff = item.get("staffName", "—")
        created = format_timestamp(item.get("createdAt"))
        lines.append(
            f"*{i}. {name}*\n"
            f"  Qty: {qty} {unit}\n"
            f"  Reason: {reason}\n"
            f"  Staff: {staff}\n"
            f"  Date: {created}\n"
        )

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(truncated)"

    await update.message.reply_text(text, parse_mode="Markdown")


async def addwriteoff_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    if not is_authorized_user(telegram_id):
        await update.message.reply_text("Access denied.")
        return ConversationHandler.END

    _writeoff_data[telegram_id] = {}
    await update.message.reply_text(
        "Recording a new write-off.\n\n"
        "Step 1/4: What item is being written off?\n"
        "Send the item name, or /cancel to abort."
    )
    return ITEM_NAME


async def addwriteoff_item_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    _writeoff_data[telegram_id]["item_name"] = update.message.text.strip()
    await update.message.reply_text(
        "Step 2/4: How many units are being written off?\n"
        "Send a number (e.g. 3 or 1.5)."
    )
    return QUANTITY


async def addwriteoff_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    try:
        qty = float(update.message.text.strip().replace(",", "."))
        _writeoff_data[telegram_id]["quantity"] = qty
    except ValueError:
        await update.message.reply_text("Please send a valid number (e.g. 2 or 1.5).")
        return QUANTITY

    await update.message.reply_text(
        "Step 3/4: What is the unit of measurement?\n"
        "Examples: pcs, kg, liters, portions, boxes"
    )
    return UNIT


async def addwriteoff_unit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    _writeoff_data[telegram_id]["unit"] = update.message.text.strip()
    await update.message.reply_text(
        "Step 4/4: What is the reason for the write-off?\n"
        "Examples: expired, damaged, spilled, quality issue"
    )
    return REASON


async def addwriteoff_reason(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    data = _writeoff_data.get(telegram_id, {})
    data["reason"] = update.message.text.strip()

    info = get_user_info(telegram_id)
    staff_name = info.get("name", update.effective_user.first_name) if info else update.effective_user.first_name

    try:
        doc_id = add_writeoff(
            item_name=data.get("item_name", "Unknown"),
            quantity=data.get("quantity", 0),
            unit=data.get("unit", "pcs"),
            reason=data.get("reason", ""),
            staff_name=staff_name,
        )
        await update.message.reply_text(
            f"Write-off recorded successfully!\n\n"
            f"Item: *{data['item_name']}*\n"
            f"Quantity: *{data['quantity']} {data['unit']}*\n"
            f"Reason: *{data['reason']}*\n"
            f"Staff: *{staff_name}*\n"
            f"ID: `{doc_id}`",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"Error saving write-off: {e}")

    _writeoff_data.pop(telegram_id, None)
    return ConversationHandler.END


async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    _writeoff_data.pop(telegram_id, None)
    await update.message.reply_text("Operation cancelled.")
    return ConversationHandler.END


def build_addwriteoff_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("addwriteoff", addwriteoff_start)],
        states={
            ITEM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addwriteoff_item_name)],
            QUANTITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, addwriteoff_quantity)],
            UNIT: [MessageHandler(filters.TEXT & ~filters.COMMAND, addwriteoff_unit)],
            REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, addwriteoff_reason)],
        },
        fallbacks=[CommandHandler("cancel", cancel_handler)],
    )
