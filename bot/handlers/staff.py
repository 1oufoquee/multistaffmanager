from telegram import Update
from telegram.ext import ContextTypes
from bot.firebase_client import is_authorized_user, get_all_staff

ROLE_MAP = {
    "admin": "Менеджер",
    "user": "Касир",
}


async def staff_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    telegram_id = update.effective_user.id
    if not is_authorized_user(telegram_id):
        await update.message.reply_text("Доступ заборонено.")
        return

    await update.message.reply_text("⏳ Завантаження списку працівників...")

    try:
        staff = get_all_staff()
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка завантаження: {e}")
        return

    if not staff:
        await update.message.reply_text("Працівників не знайдено.")
        return

    lines = [f"👥 *Працівники* — {len(staff)} чол\.\n"]
    for member in staff:
        app_user_id = member.get("_id", "—")
        name = member.get("name", "—")
        telegram_id_val = member.get("telegramId", "—")
        raw_role = member.get("userRole", "")
        role = ROLE_MAP.get(raw_role, raw_role) if raw_role else "—"

        lines.append(
            f"👤 *{_esc(name)}*\n"
            f"🎭 {_esc(role)}\n"
            f"🆔 `{app_user_id}`\n"
            f"📱 `{telegram_id_val}`"
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
