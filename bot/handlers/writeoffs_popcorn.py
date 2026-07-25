import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)
from bot.firebase_client import (
    is_authorized_user, get_user_info, get_recipes,
    get_admin_users, save_writeoff, get_writeoffs_history,
)
from bot.utils import format_timestamp

logger = logging.getLogger(__name__)

# ── Conversation states ──────────────────────────────────────────────────────
WRITEOFF_MENU = 0   # admin-only: [Нове списання | Архів]
FLAVOR_SELECT = 1   # pick a product from inline buttons
WEIGHT_INPUT  = 2   # enter weight as text (popcorn or potato)
CONFIRMING    = 3   # review report → save or cancel


# ── Emoji helpers ─────────────────────────────────────────────────────────────

def _ing_emoji(name: str) -> str:
    n = name.lower()
    if "кукурудза" in n:                       return "🌽"
    if "масло" in n:                            return "🥥"
    if "flavacol" in n:                        return "🧂"
    if "сіль" in n or "соль" in n:             return "🧂"
    if "сир" in n:                              return "🧀"
    if "бекон" in n:                            return "🥓"
    if "краб" in n:                             return "🦀"
    if "червона" in n or "красная" in n:       return "🔴"
    if "ікра" in n or "икра" in n:             return "🐟"
    if "карамель" in n:                        return "🍯"
    if "цукор" in n:                            return "🍚"
    if "добавка" in n:                          return "🍓"
    return "🍓"


def _flavor_emoji(name: str) -> str:
    n = name.lower()
    if "сир" in n:                     return "🧀"
    if "бекон" in n:                   return "🥓"
    if "краб" in n:                    return "🦀"
    if "ікра" in n or "икра" in n:    return "🐟"
    if "карамель" in n:               return "🍯"
    if "сіль" in n or "соль" in n:    return "🧂"
    if "полуниц" in n:                return "🍓"
    return "🍿"


# ── Keyboard builder ──────────────────────────────────────────────────────────

def _flavor_keyboard(
    recipes: list,
    has_entries: bool,
    entered_names: set | None = None,
    has_potato: bool = False,
) -> InlineKeyboardMarkup:
    """
    2-column inline keyboard for popcorn flavors.
    Already-entered flavors get a ✅ suffix.
    Potato wedges button always shown; ✅ when already entered.
    Confirm button appears once at least one product has been added.
    """
    entered_names = entered_names or set()
    rows = []
    row: list = []
    for i, recipe in enumerate(recipes):
        name  = recipe.get("name") or recipe.get("_id", f"#{i}")
        label = f"{name} ✅" if name in entered_names else name
        row.append(InlineKeyboardButton(label, callback_data=f"wo_f_{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    # Potato wedges — always visible, ✅ when already added this session
    potato_label = "🥔 Картопляні спеки ✅" if has_potato else "🥔 Картопляні спеки"
    rows.append([InlineKeyboardButton(potato_label, callback_data="wo_potato")])

    if has_entries or has_potato:
        rows.append([InlineKeyboardButton("✅ Підтвердити списання", callback_data="wo_done")])
    rows.append([InlineKeyboardButton("❌ Скасувати", callback_data="wo_cancel")])
    return InlineKeyboardMarkup(rows)


# ── Ingredient calculation ────────────────────────────────────────────────────

def _is_numeric(value) -> bool:
    """True only for int/float values, excluding booleans."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _calculate(recipe: dict, weight: float) -> dict[str, float]:
    """
    All recipe values are in kilograms, exactly as stored in Firestore.

    Formula per ingredient:
        ingredient_used = (weight / ГОТОВИЙ_ПРОДУКТ) * ingredient_amount

    Fields skipped: _id, id, name, ГОТОВИЙ ПРОДУКТ, and any non-numeric field.
    No unit conversion — values are used as-is.
    """
    raw_batch = recipe.get("ГОТОВИЙ ПРОДУКТ")
    batch_weight = float(raw_batch) if _is_numeric(raw_batch) and raw_batch > 0 else 1.0

    SKIP = {"_id", "id", "name", "ГОТОВИЙ ПРОДУКТ"}
    result: dict[str, float] = {}
    for key, value in recipe.items():
        if key in SKIP:
            continue
        if not _is_numeric(value):
            continue
        result[key] = round((weight / batch_weight) * float(value), 3)
    return result


def _accumulate(total: dict, new: dict) -> dict:
    for k, v in new.items():
        total[k] = round(total.get(k, 0.0) + v, 3)
    return total


def _split_common_specific(flavor_entries: list) -> tuple[dict, dict]:
    """
    Ingredients that appear in MORE THAN ONE flavor entry → common (summed).
    Ingredients that appear in exactly ONE flavor entry → specific (kept separate).

    This automatically makes Кукурудза / Масло / FLAVACOL common (shared base)
    while Добавка сир / Добавка Бекон / etc. stay separate (single-flavor use).
    """
    if not flavor_entries:
        return {}, {}

    # Count how many different flavor entries each ingredient key appears in
    presence: dict[str, int] = {}
    for entry in flavor_entries:
        for key in entry.get("ingredients", {}):
            presence[key] = presence.get(key, 0) + 1

    common:   dict[str, float] = {}
    specific: dict[str, float] = {}

    for entry in flavor_entries:
        for key, val in entry.get("ingredients", {}).items():
            if presence[key] > 1:
                common[key]   = round(common.get(key, 0.0) + val, 3)
            else:
                specific[key] = round(specific.get(key, 0.0) + val, 3)

    return common, specific


def _format_final_report(flavor_entries: list, potato_kg: float = 0.0) -> str:
    """
    Popcorn section: common ingredients summed at top, flavor-specific below.
    Potato wedges section: appended as a single line after a blank separator.
    """
    lines: list[str] = []

    if flavor_entries:
        common, specific = _split_common_specific(flavor_entries)

        for name, amount in common.items():
            lines.append(f"{_ing_emoji(name)} {name} — {amount}")

        if specific:
            if common:
                lines.append("")            # blank line separator
            for name, amount in specific.items():
                lines.append(f"{_ing_emoji(name)} {name} — {amount}")

    if potato_kg > 0:
        if lines:
            lines.append("")               # blank line separator before potato
        lines.append(f"🥔 Картопляні спеки — {potato_kg} кг")

    if not lines:
        return "_Немає даних_"

    return "\n".join(lines)


# ── Report formatting ─────────────────────────────────────────────────────────

def _format_flavor_summary(flavor_entries: list, potato_kg: float = 0.0) -> str:
    parts = [f"{_flavor_emoji(e['name'])} {e['name']} {e['weight']} кг" for e in flavor_entries]
    if potato_kg > 0:
        parts.append(f"🥔 Картопляні спеки {potato_kg} кг")
    if not parts:
        return ""
    return "📝 " + " | ".join(parts)


# ── Entry point ───────────────────────────────────────────────────────────────

async def writeoff_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    telegram_id = update.effective_user.id
    if not is_authorized_user(telegram_id):
        await update.message.reply_text("Доступ заборонено.")
        return ConversationHandler.END

    info = get_user_info(telegram_id)
    context.user_data["staff_info"]  = info or {}
    context.user_data["telegram_id"] = telegram_id
    context.user_data["chat_id"]     = update.effective_chat.id

    is_admin = info and info.get("userRole") == "admin"

    if is_admin:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ Нове списання", callback_data="wo_new")],
            [InlineKeyboardButton("📋 Архів списань",  callback_data="wo_archive")],
        ])
        await update.message.reply_text(
            "🍿 *Поп-корн — Списання*\n\nОберіть дію:",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        return WRITEOFF_MENU

    return await _begin_flavor_select(context, update.message)


# ── Admin menu ────────────────────────────────────────────────────────────────

async def handle_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "wo_new":
        await query.edit_message_text("✍️ Починаємо нове списання...")
        return await _begin_flavor_select(context, query.message, use_bot=True)

    if query.data == "wo_archive":
        await _show_archive(query, context)
        return ConversationHandler.END

    return WRITEOFF_MENU


# ── Product selection ─────────────────────────────────────────────────────────

async def _begin_flavor_select(
    context: ContextTypes.DEFAULT_TYPE,
    msg_obj,
    use_bot: bool = False,
) -> int:
    chat_id = context.user_data["chat_id"]

    try:
        recipes = get_recipes()
    except Exception as e:
        text = f"❌ Помилка завантаження рецептів: {e}"
        if use_bot:
            await context.bot.send_message(chat_id=chat_id, text=text)
        else:
            await msg_obj.reply_text(text)
        return ConversationHandler.END

    if not recipes:
        text = (
            "❌ Рецепти не знайдено у Firebase.\n\n"
            "Переконайтесь, що колекція Cinema → atmosfera → Recipes містить документи."
        )
        if use_bot:
            await context.bot.send_message(chat_id=chat_id, text=text)
        else:
            await msg_obj.reply_text(text)
        return ConversationHandler.END

    context.user_data["recipes"]           = recipes
    context.user_data["flavor_entries"]    = []
    context.user_data["total_ingredients"] = {}
    context.user_data["potato_wedges_kg"]  = 0.0

    await context.bot.send_message(
        chat_id=chat_id,
        text="🍿 *Оберіть продукт для списання:*",
        parse_mode="Markdown",
        reply_markup=_flavor_keyboard(recipes, has_entries=False, has_potato=False),
    )
    return FLAVOR_SELECT


async def handle_flavor_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "wo_cancel":
        await query.edit_message_text("❌ Списання скасовано.")
        context.user_data.clear()
        return ConversationHandler.END

    if query.data == "wo_done":
        return await _show_ingredient_summary(query, context)

    # ── Картопляні спеки ─────────────────────────────────────────────────────
    if query.data == "wo_potato":
        context.user_data["pending_potato"] = True
        await query.edit_message_text(
            "🥔 *Картопляні спеки*\n\n"
            "Введіть вагу списання (кг):\n"
            "_Приклад: 2.5_\n\n"
            "/cancel — скасувати",
            parse_mode="Markdown",
        )
        return WEIGHT_INPUT

    # ── Popcorn flavor ────────────────────────────────────────────────────────
    try:
        idx    = int(query.data[len("wo_f_"):])
        recipe = context.user_data["recipes"][idx]
    except (ValueError, IndexError):
        await query.answer("Невідомий смак, спробуйте знову.", show_alert=True)
        return FLAVOR_SELECT

    flavor_name = recipe.get("name") or recipe.get("_id", f"#{idx}")
    context.user_data["pending_potato"]        = False
    context.user_data["current_flavor_name"]   = flavor_name
    context.user_data["current_flavor_recipe"] = recipe

    await query.edit_message_text(
        f"{_flavor_emoji(flavor_name)} *{flavor_name}*\n\n"
        f"Введіть вагу готового попкорну (кг):\n"
        f"_Приклад: 2.5_\n\n"
        f"/cancel — скасувати",
        parse_mode="Markdown",
    )
    return WEIGHT_INPUT


# ── Weight input ──────────────────────────────────────────────────────────────

async def receive_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    raw     = update.message.text.strip().replace(",", ".")

    try:
        weight = float(raw)
        if weight <= 0:
            raise ValueError("non-positive")
    except ValueError:
        await update.message.reply_text(
            "⚠️ Введіть коректне число більше нуля.\n_Приклад: 2.5 або 3_",
            parse_mode="Markdown",
        )
        return WEIGHT_INPUT

    recipes        = context.user_data["recipes"]
    flavor_entries = context.user_data["flavor_entries"]
    potato_kg      = context.user_data.get("potato_wedges_kg", 0.0)

    # ── Картопляні спеки branch — no recipe, no calculation ──────────────────
    if context.user_data.get("pending_potato"):
        context.user_data["potato_wedges_kg"] = weight
        context.user_data["pending_potato"]   = False
        potato_kg = weight

        entered_names = {e["name"] for e in flavor_entries}
        summary       = _format_flavor_summary(flavor_entries, potato_kg)

        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"✅ Додано: *Картопляні спеки* — {weight} кг\n\n"
                f"{summary}\n\n"
                f"Оберіть ще продукт або підтвердіть:"
            ),
            parse_mode="Markdown",
            reply_markup=_flavor_keyboard(
                recipes,
                has_entries=bool(flavor_entries),
                entered_names=entered_names,
                has_potato=True,
            ),
        )
        return FLAVOR_SELECT

    # ── Popcorn branch — recipe calculation ───────────────────────────────────
    flavor_name = context.user_data["current_flavor_name"]
    recipe      = context.user_data["current_flavor_recipe"]
    ingredients = _calculate(recipe, weight)

    context.user_data["flavor_entries"].append({
        "name":        flavor_name,
        "weight":      weight,
        "ingredients": ingredients,
    })
    _accumulate(context.user_data["total_ingredients"], ingredients)

    flavor_entries = context.user_data["flavor_entries"]
    entered_names  = {e["name"] for e in flavor_entries}
    summary        = _format_flavor_summary(flavor_entries, potato_kg)

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ Додано: *{flavor_name}* — {weight} кг\n\n"
            f"{summary}\n\n"
            f"Оберіть ще продукт або підтвердіть:"
        ),
        parse_mode="Markdown",
        reply_markup=_flavor_keyboard(
            recipes,
            has_entries=True,
            entered_names=entered_names,
            has_potato=potato_kg > 0,
        ),
    )
    return FLAVOR_SELECT


# ── Summary before save ───────────────────────────────────────────────────────

async def _show_ingredient_summary(query, context: ContextTypes.DEFAULT_TYPE) -> int:
    flavor_entries = context.user_data.get("flavor_entries", [])
    potato_kg      = context.user_data.get("potato_wedges_kg", 0.0)

    if not flavor_entries and not potato_kg:
        await query.answer("Додайте хоча б один продукт!", show_alert=True)
        return FLAVOR_SELECT

    report = _format_final_report(flavor_entries, potato_kg)
    text   = f"📋 *Звіт про списання*\n\n{report}"

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Зберегти", callback_data="wo_save"),
            InlineKeyboardButton("❌ Скасувати", callback_data="wo_cancel"),
        ]
    ])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    return CONFIRMING


# ── Save & notify ─────────────────────────────────────────────────────────────

async def handle_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "wo_cancel":
        await query.edit_message_text("❌ Списання скасовано.")
        context.user_data.clear()
        return ConversationHandler.END

    staff_info        = context.user_data.get("staff_info", {})
    telegram_id       = context.user_data.get("telegram_id")
    flavor_entries    = context.user_data.get("flavor_entries", [])
    total_ingredients = context.user_data.get("total_ingredients", {})
    potato_kg         = context.user_data.get("potato_wedges_kg", 0.0)

    payload = {
        "staffName":        staff_info.get("name", "—"),
        "staffAppId":       staff_info.get("_id", "—"),
        "telegramId":       telegram_id,
        "items":            flavor_entries,
        "totalIngredients": total_ingredients,
    }
    if potato_kg > 0:
        payload["potato_wedges"] = potato_kg

    try:
        doc_id = save_writeoff(payload)
    except Exception as e:
        await query.edit_message_text(f"❌ Помилка збереження: {e}")
        return ConversationHandler.END

    report = _format_final_report(flavor_entries, potato_kg)
    await query.edit_message_text(
        f"✅ *Списання збережено!*\n\n{report}\n\n`{doc_id}`",
        parse_mode="Markdown",
    )

    await _notify_admins(context, staff_info, flavor_entries, potato_kg)
    context.user_data.clear()
    return ConversationHandler.END


async def _notify_admins(
    context: ContextTypes.DEFAULT_TYPE,
    staff_info: dict,
    flavor_entries: list,
    potato_kg: float = 0.0,
):
    try:
        admins = get_admin_users()
    except Exception as e:
        logger.warning(f"Could not fetch admins: {e}")
        return

    staff_name = staff_info.get("name", "—")
    report     = _format_final_report(flavor_entries, potato_kg)

    text = (
        f"🔔 Списання готове!\n\n"
        f"👤 {staff_name}\n\n"
        f"{report}"
    )

    for admin in admins:
        tid = admin.get("telegramId")
        if not tid:
            continue
        try:
            await context.bot.send_message(chat_id=int(tid), text=text)
        except Exception as e:
            logger.warning(f"Failed to notify admin {tid}: {e}")


# ── Archive ───────────────────────────────────────────────────────────────────

async def _show_archive(query, context: ContextTypes.DEFAULT_TYPE):
    try:
        history = get_writeoffs_history(limit=20)
    except Exception as e:
        await query.edit_message_text(f"❌ Помилка завантаження архіву: {e}")
        return

    if not history:
        await query.edit_message_text("📋 Архів порожній — списань ще не було.")
        return

    lines = [f"📋 *Архів списань* — {len(history)} записів\n"]
    for entry in history:
        created = format_timestamp(entry.get("createdAt"))
        staff   = entry.get("staffName", "—")
        items   = entry.get("items", [])
        potato  = entry.get("potato_wedges", 0.0) or 0.0

        lines.append(f"🕐 {created}  👤 {staff}")
        for item in items:
            if not isinstance(item, dict):
                continue
            flavor_name = item.get("popcornName") or item.get("name", "?")
            weight      = item.get("weight", 0)
            ingredients = item.get("ingredients", {})
            lines.append(f"{_flavor_emoji(flavor_name)} {flavor_name} ({weight} кг)")
            for ing_name, amount in ingredients.items():
                lines.append(f"  • {ing_name} — {amount}")
        if potato > 0:
            lines.append(f"🥔 Картопляні спеки — {potato} кг")
        lines.append("─────────────")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(скорочено)"

    await query.edit_message_text(text, parse_mode="Markdown")


# ── Cancel ────────────────────────────────────────────────────────────────────

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Списання скасовано.")
    return ConversationHandler.END


# ── Build ConversationHandler ─────────────────────────────────────────────────

def build_writeoff_conversation() -> ConversationHandler:
    flavor_or_cancel = CallbackQueryHandler(
        handle_flavor_select,
        pattern=r"^wo_(f_\d+|done|cancel|potato)$",
    )
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🍿 Списання$"), writeoff_start),
        ],
        states={
            WRITEOFF_MENU: [
                CallbackQueryHandler(handle_admin_menu, pattern=r"^wo_(new|archive)$"),
            ],
            FLAVOR_SELECT: [
                flavor_or_cancel,
            ],
            WEIGHT_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_weight),
                CallbackQueryHandler(handle_flavor_select, pattern=r"^wo_cancel$"),
            ],
            CONFIRMING: [
                CallbackQueryHandler(handle_confirm, pattern=r"^wo_(save|cancel)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_handler),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,
    )
