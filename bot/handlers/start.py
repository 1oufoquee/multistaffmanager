from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes
from bot.firebase_client import is_authorized_user, get_user_info

ELEVATED_ROLES = ("admin", "Директор")

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📦 Замовлення"), KeyboardButton("👥 Працівники")],
        [KeyboardButton("📊 Статистика"), KeyboardButton("🍿 Списання")],
        [KeyboardButton("🎬 Сеанси")]
    ],
    resize_keyboard=True,
)

ADMIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📦 Замовлення"), KeyboardButton("👥 Працівники")],
        [KeyboardButton("📊 Статистика"), KeyboardButton("🍿 Списання")],
        [KeyboardButton("👑 Адмін-Панель")],
    ],
    resize_keyboard=True,
)


def get_keyboard(info: dict | None) -> ReplyKeyboardMarkup:
    role = (info or {}).get("userRole", "")
    return ADMIN_KEYBOARD if role in ELEVATED_ROLES else MAIN_KEYBOARD


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    telegram_id = user.id

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
