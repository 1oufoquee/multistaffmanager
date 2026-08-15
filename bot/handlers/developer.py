import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from bot.firebase_client import (
    PROJECTS,
    get_developer_info,
    get_developer_project,
    set_active_project,
    set_developer_project,
    get_user_info,
)
from bot.handlers.start import get_keyboard

logger = logging.getLogger(__name__)

CHOOSE_CINEMA_BUTTON = "🏢 Вибрати кінотеатр"
SETTINGS_BUTTON = "⚙️ Налаштування бота"
CHANGE_CINEMA_BUTTON = "🔄 Змінити кінотеатр"


def _cinema_picker() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                PROJECTS["atmosfera"]["label"],
                callback_data="dev_cinema_atmosfera",
            )
        ],
        [
            InlineKeyboardButton(
                PROJECTS["karavan"]["label"],
                callback_data="dev_cinema_karavan",
            )
        ],
        [
            InlineKeyboardButton(
                PROJECTS["retroville"]["label"],
                callback_data="dev_cinema_retroville",
            )
        ],
    ])


def _settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(CHANGE_CINEMA_BUTTON, callback_data="dev_change")],
        [InlineKeyboardButton("◀ Назад", callback_data="dev_back")],
    ])


async def send_developer_landing(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    developer_info: dict | None = None,
) -> None:
    """Show the project chooser instead of opening a cinema menu."""
    info = developer_info or get_developer_info(update.effective_user.id)
    selected = (info or {}).get("selectedProject")
    current = PROJECTS.get(selected, {}).get("label") if selected else None
    current_line = f"\nПоточний кінотеатр: *{current}*\n" if current else ""

    await update.effective_message.reply_text(
        "👨‍💻 *Режим розробника*\n"
        f"{current_line}\n"
        "Оберіть кінотеатр для роботи:",
        parse_mode="Markdown",
        reply_markup=_cinema_picker(),
    )


async def developer_text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Handle developer-only reply-keyboard navigation buttons."""
    telegram_id = update.effective_user.id
    developer = get_developer_info(telegram_id)
    if not developer:
        logger.warning(
            "Developer navigation denied for Telegram ID %s",
            telegram_id,
        )
        await update.message.reply_text("⛔ Доступ заборонено.")
        return

    if update.message.text == CHANGE_CINEMA_BUTTON:
        await update.message.reply_text(
            "🏢 *Виберіть кінотеатр:*",
            parse_mode="Markdown",
            reply_markup=_cinema_picker(),
        )
        return

    await update.message.reply_text(
        "⚙️ *Налаштування бота*\n\n"
        "Функції розробника будуть додані пізніше.\n"
        "Ви можете змінити активний кінотеатр:",
        parse_mode="Markdown",
        reply_markup=_settings_keyboard(),
    )


async def handle_developer_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    query = update.callback_query
    telegram_id = update.effective_user.id
    developer = get_developer_info(telegram_id)
    if not developer:
        await query.answer("⛔ Доступ заборонено.", show_alert=True)
        logger.warning(
            "Developer callback denied for Telegram ID %s: %s",
            telegram_id,
            query.data,
        )
        return

    await query.answer()
    data = query.data

    if data == "dev_change":
        await query.edit_message_text(
            "🏢 *Виберіть кінотеатр:*",
            parse_mode="Markdown",
            reply_markup=_cinema_picker(),
        )
        return

    if data == "dev_back":
        info = get_developer_info(telegram_id) or developer
        selected = info.get("selectedProject")
        label = PROJECTS.get(selected, {}).get("label", "не вибрано")
        await query.edit_message_text(
            f"⚙️ *Налаштування бота*\n\nАктивний кінотеатр: *{label}*",
            parse_mode="Markdown",
            reply_markup=_settings_keyboard(),
        )
        return

    prefix = "dev_cinema_"
    if not data.startswith(prefix):
        return

    project_key = data[len(prefix):]
    if project_key not in PROJECTS:
        await query.answer("Невідомий кінотеатр.", show_alert=True)
        return

    try:
        set_developer_project(telegram_id, project_key)
        set_active_project(project_key)
    except Exception:
        logger.exception(
            "Failed to switch developer %s to project %s",
            telegram_id,
            project_key,
        )
        await query.edit_message_text(
            "❌ Не вдалося підключити цей кінотеатр. "
            "Перевірте налаштування Firebase."
        )
        return

    label = PROJECTS[project_key]["label"]
    await query.edit_message_text(
        f"✅ Обрано кінотеатр: *{label}*",
        parse_mode="Markdown",
    )

    # Send the normal menu only after the project was selected successfully.
    info = get_user_info(telegram_id) or {
        "name": developer.get("name", "Developer"),
        "userRole": "developer",
        "project": project_key,
    }
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            f"Вітаємо, *{info.get('name', 'Developer')}*!\n\n"
            f"Активний кінотеатр: *{label}*\n"
            "Оберіть розділ за допомогою кнопок нижче:"
        ),
        parse_mode="Markdown",
        reply_markup=get_keyboard(info),
    )