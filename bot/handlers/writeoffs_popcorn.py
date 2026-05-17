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

# Conversation states
WRITEOFF_MENU = 0
ENTERING_WEIGHTS = 1
CONFIRMING = 2


# ── Entry point ──────────────────────────────────────────────────────────────

async def writeoff_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ Нове списання", callback_data="wo_new")],
            [InlineKeyboardButton("📋 Архів списань", callback_data="wo_archive")],
        ])
        await update.message.reply_text(
            "🍿 *Поп-корн — Списання*\n\nОберіть дію:",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        return WRITEOFF_MENU
    else:
        return await _begin_weight_entry(context)


# ── Admin menu ───────────────────────────────────────────────────────────────

async def handle_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "wo_new":
        await query.edit_message_text("✍️ Починаємо нове списання...")
        return await _begin_weight_entry(context)

    elif query.data == "wo_archive":
        await _show_archive(query, context)
        return ConversationHandler.END


# ── Weight entry ─────────────────────────────────────────────────────────────

async def _begin_weight_entry(context: ContextTypes.DEFAULT_TYPE) -> int:
    chat_id = context.user_data["chat_id"]

    try:
        recipes = get_recipes()
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Помилка завантаження рецептів: {e}")
        return ConversationHandler.END

    if not recipes:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "❌ Рецепти не знайдено у Firebase.\n\n"
                "Додайте рецепти до колекції:\n"
                "Cinema → atmosfera → Recipes"
            ),
        )
        return ConversationHandler.END

    context.user_data["recipes"] = recipes
    context.user_data["weights"] = {}
    context.user_data["recipe_index"] = 0

    await _ask_next_weight(context, chat_id)
    return ENTERING_WEIGHTS


async def _ask_next_weight(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    recipes = context.user_data["recipes"]
    idx = context.user_data["recipe_index"]
    recipe = recipes[idx]
    name = recipe.get("name", f"Рецепт {idx + 1}")
    total = len(recipes)

    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            f"🍿 *{name}* ({idx + 1}/{total})\n\n"
            f"Введіть вагу в кг:\n"
            f"_Приклад: 2.5_\n\n"
            f"/cancel — скасувати"
        ),
        parse_mode="Markdown",
    )


async def receive_weight(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text.strip().replace(",", ".")

    try:
        weight = float(text)
        if weight < 0:
            raise ValueError("negative weight")
    except ValueError:
        await update.message.reply_text(
            "⚠️ Введіть коректне число.\nПриклад: `2.5` або `3`",
            parse_mode="Markdown",
        )
        return ENTERING_WEIGHTS

    recipes = context.user_data["recipes"]
    idx = context.user_data["recipe_index"]
    recipe = recipes[idx]
    recipe_name = recipe.get("name", f"Рецепт {idx + 1}")

    context.user_data["weights"][recipe_name] = {
        "weight": weight,
        "recipe": recipe,
    }
    context.user_data["recipe_index"] = idx + 1

    if context.user_data["recipe_index"] < len(recipes):
        await _ask_next_weight(context, chat_id)
        return ENTERING_WEIGHTS
    else:
        return await _show_summary(context, chat_id)


# ── Summary & confirmation ───────────────────────────────────────────────────

def _calculate_ingredients(recipe: dict, weight: float) -> dict:
    ingredients = recipe.get("ingredients", [])
    result = {}
    for ing in ingredients:
        name = ing.get("name", "?")
        per_kg = float(
            ing.get("amountPerKg", ing.get("perKg", ing.get("amount", 0))) or 0
        )
        result[name] = round(per_kg * weight, 3)
    return result


async def _show_summary(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> int:
    weights = context.user_data["weights"]
    total_ingredients: dict[str, float] = {}
    items_summary = []

    for popcorn_name, entry in weights.items():
        weight = entry["weight"]
        recipe = entry["recipe"]
        calc = _calculate_ingredients(recipe, weight)

        items_summary.append({
            "popcornName": popcorn_name,
            "weight": weight,
            "ingredients": calc,
        })

        for ing_name, amount in calc.items():
            total_ingredients[ing_name] = round(
                total_ingredients.get(ing_name, 0) + amount, 3
            )

    context.user_data["items_summary"] = items_summary
    context.user_data["total_ingredients"] = total_ingredients

    lines = ["📋 *Підсумок списання*\n"]
    for item in items_summary:
        lines.append(f"🍿 *{item['popcornName']}* — {item['weight']} кг")
        if item["ingredients"]:
            for ing, amount in item["ingredients"].items():
                lines.append(f"   • {ing}: {amount} кг")
        else:
            lines.append("   _рецепт без інгредієнтів_")
        lines.append("")

    if total_ingredients:
        lines.append("📦 *Загальні інгредієнти:*")
        for ing, amount in total_ingredients.items():
            lines.append(f"  • {ing}: {amount} кг")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Підтвердити", callback_data="wo_confirm"),
            InlineKeyboardButton("❌ Скасувати", callback_data="wo_cancel"),
        ]
    ])

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return CONFIRMING


# ── Save & notify ────────────────────────────────────────────────────────────

async def handle_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "wo_cancel":
        await query.edit_message_text("❌ Списання скасовано.")
        context.user_data.clear()
        return ConversationHandler.END

    staff_info = context.user_data.get("staff_info", {})
    telegram_id = context.user_data.get("telegram_id")
    items_summary = context.user_data.get("items_summary", [])
    total_ingredients = context.user_data.get("total_ingredients", {})

    try:
        doc_id = save_writeoff({
            "staffName": staff_info.get("name", "—"),
            "staffAppId": staff_info.get("_id", "—"),
            "telegramId": telegram_id,
            "items": items_summary,
            "totalIngredients": total_ingredients,
        })
    except Exception as e:
        await query.edit_message_text(f"❌ Помилка збереження: {e}")
        return ConversationHandler.END

    await query.edit_message_text(
        f"✅ *Списання збережено!*\n\nID: `{doc_id}`",
        parse_mode="Markdown",
    )

    await _notify_admins(context, staff_info, items_summary)
    context.user_data.clear()
    return ConversationHandler.END


async def _notify_admins(
    context: ContextTypes.DEFAULT_TYPE,
    staff_info: dict,
    items_summary: list,
):
    try:
        admins = get_admin_users()
    except Exception as e:
        logger.warning(f"Could not fetch admins for notification: {e}")
        return

    staff_name = staff_info.get("name", "—")
    lines = [
        "🔔 Списання готове!\n",
        f"👤 Співробітник: {staff_name}",
    ]
    for item in items_summary:
        lines.append(f"🍿 {item['popcornName']}: {item['weight']} кг")

    text = "\n".join(lines)

    for admin in admins:
        admin_tid = admin.get("telegramId")
        if not admin_tid:
            continue
        try:
            await context.bot.send_message(chat_id=int(admin_tid), text=text)
        except Exception as e:
            logger.warning(f"Failed to notify admin {admin_tid}: {e}")


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
        staff = entry.get("staffName", "—")
        items = entry.get("items", [])
        total_ings = entry.get("totalIngredients", {})

        popcorn_parts = [
            f"{it.get('popcornName','?')} {it.get('weight',0)}кг"
            for it in items if isinstance(it, dict)
        ]
        popcorn_line = ", ".join(popcorn_parts) or "—"

        lines.append(f"🕐 {created}  👤 {staff}")
        lines.append(f"🍿 {popcorn_line}")
        if total_ings:
            ings_str = ", ".join(f"{k}: {v}кг" for k, v in total_ings.items())
            lines.append(f"📦 {ings_str}")
        lines.append("─────────────")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(скорочено)"

    await query.edit_message_text(text, parse_mode="Markdown")


# ── Cancel ───────────────────────────────────────────────────────────────────

async def cancel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Списання скасовано.")
    return ConversationHandler.END


# ── Build handler ────────────────────────────────────────────────────────────

def build_writeoff_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^🍿 Списання$"), writeoff_start),
        ],
        states={
            WRITEOFF_MENU: [
                CallbackQueryHandler(handle_menu_callback, pattern="^wo_(new|archive)$"),
            ],
            ENTERING_WEIGHTS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_weight),
            ],
            CONFIRMING: [
                CallbackQueryHandler(handle_confirm_callback, pattern="^wo_(confirm|cancel)$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_handler),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,
    )
