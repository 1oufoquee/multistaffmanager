from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes
from bot.firebase_client import (
    is_authorized_user,
    get_user_info,
    get_developer_info,
    DEFAULT_FEATURES,
    is_feature_enabled,
)

ELEVATED_ROLES = ("admin", "Директор", "developer")

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("👥 Працівники"), KeyboardButton("🍿 Списання")],
        [KeyboardButton("🎬 Сеанси")],
        [KeyboardButton("📊 Підсумок списань за сьогодні")],
    ],
    resize_keyboard=True,
)

ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("👥 Працівники"), KeyboardButton("🍿 Списання")],
        [KeyboardButton("🎬 Сеанси"), KeyboardButton("👑 Адмін-Панель")],
        [KeyboardButton("📊 Підсумок списань за сьогодні")],
    ],
    resize_keyboard=True,
)

def get_keyboard(info: dict | None) -> ReplyKeyboardMarkup:
    role = (info or {}).get("userRole", "")
    developer = role == "developer"

    def enabled(feature: str) -> bool:
        if developer:
            return True
        try:
            return is_feature_enabled(feature)
        except LookupError:
            # The user has no active cinema context (usually an unauthorized
            # /help request), so retain the safe current default menu.
            return DEFAULT_FEATURES[feature]

    rows = [[KeyboardButton("👥 Працівники")]]
    if enabled("writeoffs"):
        rows[0].append(KeyboardButton("🍿 Списання"))

    feature_row = []
    if enabled("sessions"):
        feature_row.append(KeyboardButton("🎬 Сеанси"))
    if enabled("orders"):
        feature_row.append(KeyboardButton("📦 Замовлення"))
    if feature_row:
        rows.append(feature_row)

    if enabled("statistics"):
        rows.append([KeyboardButton("📊 Статистика")])
    rows.append([KeyboardButton("📊 Підсумок списань за сьогодні")])

    if role in ELEVATED_ROLES and enabled("admin_panel"):
        rows.append([KeyboardButton("👑 Адмін-Панель")])

    if developer:
        rows.extend([
            [KeyboardButton("⚙️ Налаштування бота")],
            [KeyboardButton("🔄 Змінити кінотеатр")],
        ])

    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    telegram_id = user.id

    # Developers are checked in the global Developers collection first and
    # must choose a project before receiving the normal cinema menu.
    developer_info = get_developer_info(telegram_id)
    if developer_info:
        from bot.handlers.developer import send_developer_landing
        await send_developer_landing(update, context, developer_info)
        return

    if not is_authorized_user(telegram_id):
        await update.message.reply_text(
            "Доступ заборонено. Ваш Telegram ID не зареєстровано в системі.\n\n"
            f"Ваш Telegram ID: `{telegram_id}`\n"
            "Зверніться до адміністратора.",
            parse_mode="Markdown",
        )
        return

    info = get_user_info(telegram_id)
    name = info.get("name", user.first_name) if info else user.first_name
    role = (info or {}).get("userRole", "user")
    role_label = {"admin": "Менеджер", "Директор": "Директор"}.get(role, "Касир")

    await update.message.reply_text(
        f"Вітаємо, *{name}* ({role_label})!\n\n"
        "Оберіть розділ за допомогою кнопок нижче:",
        parse_mode="Markdown",
        reply_markup=get_keyboard(info),
    )
