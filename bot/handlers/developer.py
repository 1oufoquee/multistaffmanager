import logging

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from bot.firebase_client import (
    PROJECTS,
    DEFAULT_FEATURES,
    FEATURE_LABELS,
    get_developer_info,
    set_active_project,
    set_developer_project,
    get_user_info,
    get_feature_config,
    update_feature_config,
    refresh_feature_config,
    get_project_information,
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


def _panel_keyboard(config: dict) -> InlineKeyboardMarkup:
    rows = []
    for feature, label in FEATURE_LABELS.items():
        state = "✅ Увімк." if config.get(feature, DEFAULT_FEATURES[feature]) else "❌ Вимк."
        rows.append([
            InlineKeyboardButton(
                f"{label}: {state}",
                callback_data=f"dev_toggle_{feature}",
            )
        ])
    rows.extend([
        [InlineKeyboardButton("📋 Інформація про проект", callback_data="dev_info")],
        [InlineKeyboardButton("🔄 Оновити конфігурацію", callback_data="dev_refresh")],
        [InlineKeyboardButton("🔄 Змінити кінотеатр", callback_data="dev_change")],
        [InlineKeyboardButton("◀ Назад", callback_data="dev_back")],
    ])
    return InlineKeyboardMarkup(rows)


def _panel_text(config: dict, project_key: str | None) -> str:
    label = PROJECTS.get(project_key or "", {}).get("label", "не вибрано")
    lines = [
        "⚙️ *Налаштування бота*",
        "",
        f"Активний кінотеатр: *{label}*",
        "",
        "Натисніть функцію, щоб змінити її стан:",
    ]
    for feature, feature_label in FEATURE_LABELS.items():
        state = "✅ Увімк." if config.get(feature, DEFAULT_FEATURES[feature]) else "❌ Вимк."
        lines.append(f"{feature_label}: {state}")
    return "\n".join(lines)


def _settings_overview(developer: dict) -> tuple[str, InlineKeyboardMarkup]:
    selected = developer.get("selectedProject")
    label = PROJECTS.get(selected or "", {}).get("label", "не вибрано")
    return (
        f"⚙️ *Налаштування бота*\n\nАктивний кінотеатр: *{label}*\n"
        "Оберіть дію:",
        InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Панель функцій", callback_data="dev_panel")],
            [InlineKeyboardButton("📋 Інформація про проект", callback_data="dev_info")],
            [InlineKeyboardButton("🔄 Оновити конфігурацію", callback_data="dev_refresh")],
            [InlineKeyboardButton(CHANGE_CINEMA_BUTTON, callback_data="dev_change")],
        ]),
    )


def _project_info_text(info: dict) -> str:
    latest = info.get("latest_schedule_update")
    latest_text = str(latest) if latest is not None else "—"
    dates = info.get("schedule_dates") or []
    dates_text = ", ".join(dates[-10:]) if dates else "—"
    feature_lines = [
        f"{FEATURE_LABELS[key]}: "
        f"{'✅ Увімк.' if value else '❌ Вимк.'}"
        for key, value in info.get("features", {}).items()
        if key in FEATURE_LABELS
    ]
    return (
        "📋 *Інформація про проект*\n\n"
        f"Firebase project: `{info['project']}` ({info['project_label']})\n"
        f"cinemaId: `{info['cinema_id']}`\n"
        f"Статус Firebase: *{info['connection_status']}*\n"
        f"Користувачів: *{info['user_count']}*\n"
        f"Дат розкладу: *{len(dates)}*\n"
        f"Доступні дати: `{dates_text}`\n"
        f"Останнє оновлення розкладу: `{latest_text}`\n\n"
        "*Конфігурація функцій:*\n"
        + "\n".join(feature_lines)
    )


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

    if update.message.text in {CHANGE_CINEMA_BUTTON, CHOOSE_CINEMA_BUTTON}:
        await update.message.reply_text(
            "🏢 *Виберіть кінотеатр:*",
            parse_mode="Markdown",
            reply_markup=_cinema_picker(),
        )
        return

    try:
        config = get_feature_config(telegram_id)
        project_key = developer.get("selectedProject")
        text = _panel_text(config, project_key)
        keyboard = _panel_keyboard(config)
    except Exception as exc:
        logger.exception("Could not load developer panel for %s", telegram_id)
        text = f"❌ Не вдалося завантажити конфігурацію: {exc}"
        keyboard = _settings_keyboard()
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


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

    if data == "dev_panel":
        try:
            config = get_feature_config(telegram_id)
            await query.edit_message_text(
                _panel_text(config, developer.get("selectedProject")),
                parse_mode="Markdown",
                reply_markup=_panel_keyboard(config),
            )
        except Exception as exc:
            logger.exception("Could not open developer panel for %s", telegram_id)
            await query.edit_message_text(f"❌ Не вдалося завантажити панель: {exc}")
        return

    if data == "dev_info":
        try:
            info = get_project_information(telegram_id)
            await query.edit_message_text(
                _project_info_text(info),
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Панель функцій", callback_data="dev_panel")],
                    [InlineKeyboardButton("🔄 Оновити конфігурацію", callback_data="dev_refresh")],
                    [InlineKeyboardButton("◀ Назад", callback_data="dev_back")],
                ]),
            )
        except Exception as exc:
            logger.exception("Could not load project information for %s", telegram_id)
            await query.edit_message_text(
                f"❌ Не вдалося завантажити інформацію про проект: {exc}",
                reply_markup=_settings_keyboard(),
            )
        return

    if data == "dev_refresh":
        try:
            config = refresh_feature_config(telegram_id)
            await query.edit_message_text(
                "✅ Конфігурацію оновлено з Firestore.\n\n"
                + _panel_text(config, developer.get("selectedProject")),
                parse_mode="Markdown",
                reply_markup=_panel_keyboard(config),
            )
        except Exception as exc:
            logger.exception("Could not refresh developer config for %s", telegram_id)
            await query.edit_message_text(f"❌ Не вдалося оновити конфігурацію: {exc}")
        return

    toggle_prefix = "dev_toggle_"
    if data.startswith(toggle_prefix):
        feature = data[len(toggle_prefix):]
        if feature not in DEFAULT_FEATURES:
            await query.answer("Невідома функція.", show_alert=True)
            return
        try:
            current = get_feature_config(telegram_id).get(feature, DEFAULT_FEATURES[feature])
            config = update_feature_config(telegram_id, feature, not current)
            await query.edit_message_text(
                _panel_text(config, developer.get("selectedProject")),
                parse_mode="Markdown",
                reply_markup=_panel_keyboard(config),
            )
            logger.info(
                "Developer %s toggled feature %s to %s",
                telegram_id,
                feature,
                not current,
            )
        except Exception as exc:
            logger.exception(
                "Could not toggle developer feature %s for %s",
                feature,
                telegram_id,
            )
            await query.edit_message_text(f"❌ Не вдалося змінити функцію: {exc}")
        return

    if data == "dev_back":
        info = get_developer_info(telegram_id) or developer
        text, keyboard = _settings_overview(info)
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=keyboard)
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