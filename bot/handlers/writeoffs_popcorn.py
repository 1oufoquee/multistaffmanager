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
WRITEOFF_MENU   = 0   # admin-only: [Нове списання | Архів]
FLAVOR_SELECT   = 1   # pick a popcorn flavor from inline buttons
WEIGHT_INPUT    = 2   # enter finished popcorn weight as text
CONFIRMING      = 3   # review ingredient report → save or cancel


# ── Ingredient emoji map ─────────────────────────────────────────────────────

def _ing_emoji(name: str) -> str:
    n = name.lower()
    if "кукурудза" in n:   return "🌽"
    if "масло" in n:        return "🥥"
    if "flavacol" in n:     return "🧂"
    if "сіль" in n or "соль" in n: return "🧂"
    if "сир" in n:          return "🧀"
    if "бекон" in n:        return "🥓"
    if "краб" in n:         return "🦀"
    if "ікра" in n or "икра" in n: return "🐟"
    if "карамель" in n:     return "🍯"
    if "цукор" in n:        return "🍚"
    if "добавка" in n:      return "🔸"
    return "•"


# ── Keyboard builders ────────────────────────────────────────────────────────

def _flavor_keyboard(recipes: list, has_entries: bool) -> InlineKeyboardMarkup:
    """Inline keyboard with one button per recipe (2 per row)."""
    rows = []
    row: list = []
    for i, recipe in enumerate(recipes):
        name = recipe.get("name") or recipe.get("_id", f"#{i}")
        row.append(InlineKeyboardButton(name, callback_data=f"wo_f_{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    if has_entries:
        rows.append([InlineKeyboardButton("✅ Підтвердити списання", callback_data="wo_done")])
    rows.append([InlineKeyboardButton("❌ Скасувати", callback_data="wo_cancel")])
    return InlineKeyboardMarkup(rows)


# ── Ingredient calculation ───────────────────────────────────────────────────

def _calculate(recipe: dict, weight: float) -> dict[str, float]:
    """Return {ingredient_name: amount} for given finished popcorn weight."""
    result: dict[str, float] = {}
    for ing in recipe.get("ingredients", []):
        name = ing.get("name", "?")
        # Support multiple possible field names for the per-kg ratio
        per_kg = float(
            ing.get("amountPerKg")
            or ing.get("perKg")
            or ing.get("amount")
            or 0
        )
        result[name] = round(per_kg * weight, 3)
    return result


def _accumulate(total: dict, new: dict) -> dict:
    for k, v in new.items():
        total[k] = round(total.get(k, 0.0) + v, 3)
    return total


# ── Report formatting ─────────────────────────────────────────────────────────

def _format_ingredient_report(total_ingredients: dict[str, float]) -> str:
    if not total_ingredients:
        return "_Рецепти не містять інгредієнтів_"
    lines = []
    for name, amount in total_ingredients.items():
        lines.append(f"{_ing_emoji(name)} {name} — {amount}")
    return "\n".join(lines)


def _format_flavor_summary(flavor_entries: list) -> str:
    if not flavor_entries:
        return ""
    parts = [f"{e['name']} {e['weight']} кг" for e in flavor_entries]
    return "📝 Додано: " + " | ".join(parts)


# ── Entry point ──────────────────────────────────────────────────────────────

async def writeoff_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    telegram_id = update.effective_user.id
    if not is_authorized_user(telegram_id):
        await update.message.reply_text("Доступ заборонено.")
        return ConversationHandler.END

    info = get_user_info(telegram_id)
    context.user_data["staff_info"] = info or {}
    context.user_data["telegram_id"] = telegram_id
    context.user_data["chat_id"] = update.effective_chat.id

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


# ── Admin menu ───────────────────────────────────────────────────────────────

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


# ── Flavor selection ─────────────────────────────────────────────────────────

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

    context.user_data["recipes"]          = recipes
    context.user_data["flavor_entries"]   = []
    context.user_data["total_ingredients"] = {}

    await context.bot.send_message(
        chat_id=chat_id,
        text="🍿 *Оберіть смак попкорну:*",
        parse_mode="Markdown",
        reply_markup=_flavor_keyboard(recipes, has_entries=False),
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

    # Parse flavor index: "wo_f_<idx>"
    try:
        idx = int(query.data[len("wo_f_"):])
        recipe = context.user_data["recipes"][idx]
    except (ValueError, IndexError):
        await query.answer("Невідомий смак, спробуйте знову.", show_alert=True)
        return FLAVOR_SELECT

    flavor_name = recipe.get("name") or recipe.get("_id", f"#{idx}")
    context.user_data["current_flavor_name"]  = flavor_name
    context.user_data["current_flavor_recipe"] = recipe

    await query.edit_message_text(
        f"✍️ *{flavor_name}*\n\n"
        f"Введіть вагу готового попкорну (кг):\n"
        f"_Приклад: 2.5_\n\n"
        f"/cancel — скасувати",
        parse_mode="Markdown",
    )
    return WEIGHT_INPUT


# ── Weight input ─────────────────────────────────────────────────────────────

async def receive_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = update.effective_chat.id
    raw = update.message.text.strip().replace(",", ".")

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

    flavor_name   = context.user_data["current_flavor_name"]
    recipe        = context.user_data["current_flavor_recipe"]
    ingredients   = _calculate(recipe, weight)

    # Accumulate
    context.user_data["flavor_entries"].append({"name": flavor_name, "weight": weight})
    _accumulate(context.user_data["total_ingredients"], ingredients)

    summary = _format_flavor_summary(context.user_data["flavor_entries"])
    recipes  = context.user_data["recipes"]

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"✅ Додано: *{flavor_name}* — {weight} кг\n\n"
            f"{summary}\n\n"
            f"Оберіть ще один смак або підтвердіть:"
        ),
        parse_mode="Markdown",
        reply_markup=_flavor_keyboard(recipes, has_entries=True),
    )
    return FLAVOR_SELECT


# ── Ingredient summary before save ───────────────────────────────────────────

async def _show_ingredient_summary(query, context: ContextTypes.DEFAULT_TYPE) -> int:
    total_ingredients = context.user_data.get("total_ingredients", {})
    flavor_entries    = context.user_data.get("flavor_entries", [])

    if not flavor_entries:
        await query.answer("Додайте хоча б один смак!", show_alert=True)
        return FLAVOR_SELECT

    report = _format_ingredient_report(total_ingredients)
    flavors_line = " | ".join(f"{e['name']} {e['weight']}кг" for e in flavor_entries)

    text = (
        f"📋 *Звіт про списання*\n\n"
        f"🍿 {flavors_line}\n\n"
        f"{report}"
    )

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Зберегти", callback_data="wo_save"),
            InlineKeyboardButton("❌ Скасувати", callback_data="wo_cancel"),
        ]
    ])

    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    return CONFIRMING


# ── Save & notify ────────────────────────────────────────────────────────────

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

    # Build items list (popcorn name + weight + per-flavor ingredients)
    items = []
    for entry in flavor_entries:
        recipes = context.user_data.get("recipes", [])
        recipe  = next(
            (r for r in recipes if (r.get("name") or r.get("_id")) == entry["name"]),
            {},
        )
        items.append({
            "popcornName": entry["name"],
            "weight":      entry["weight"],
            "ingredients": _calculate(recipe, entry["weight"]),
        })

    try:
        doc_id = save_writeoff({
            "staffName":        staff_info.get("name", "—"),
            "staffAppId":       staff_info.get("_id", "—"),
            "telegramId":       telegram_id,
            "items":            items,
            "totalIngredients": total_ingredients,
        })
    except Exception as e:
        await query.edit_message_text(f"❌ Помилка збереження: {e}")
        return ConversationHandler.END

    report = _format_ingredient_report(total_ingredients)
    await query.edit_message_text(
        f"✅ *Списання збережено!*\n\n{report}\n\nID: `{doc_id}`",
        parse_mode="Markdown",
    )

    await _notify_admins(context, staff_info, flavor_entries, total_ingredients)
    context.user_data.clear()
    return ConversationHandler.END


async def _notify_admins(
    context: ContextTypes.DEFAULT_TYPE,
    staff_info: dict,
    flavor_entries: list,
    total_ingredients: dict,
):
    try:
        admins = get_admin_users()
    except Exception as e:
        logger.warning(f"Could not fetch admins: {e}")
        return

    staff_name   = staff_info.get("name", "—")
    flavors_line = " | ".join(f"{e['name']} {e['weight']}кг" for e in flavor_entries)
    report       = _format_ingredient_report(total_ingredients)

    text = (
        f"🔔 Списання готове!\n\n"
        f"👤 {staff_name}\n"
        f"🍿 {flavors_line}\n\n"
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


# ── Archive ──────────────────────────────────────────────────────────────────

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
        total   = entry.get("totalIngredients", {})

        flavors = " | ".join(
            f"{it.get('popcornName','?')} {it.get('weight',0)}кг"
            for it in items if isinstance(it, dict)
        ) or "—"

        lines.append(f"🕐 {created}  👤 {staff}")
        lines.append(f"🍿 {flavors}")
        if total:
            for name, amount in total.items():
                lines.append(f"  {_ing_emoji(name)} {name} — {amount}")
        lines.append("─────────────")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(скорочено)"

    await query.edit_message_text(text, parse_mode="Markdown")


# ── Cancel ───────────────────────────────────────────────────────────────────

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("❌ Списання скасовано.")
    return ConversationHandler.END


# ── Build ConversationHandler ────────────────────────────────────────────────

def build_writeoff_conversation() -> ConversationHandler:
    flavor_or_cancel = CallbackQueryHandler(
        handle_flavor_select,
        pattern=r"^wo_(f_\d+|done|cancel)$",
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
