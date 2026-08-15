import logging
from collections import OrderedDict
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from bot.firebase_client import get_writeoffs_for_day, is_authorized_user
from bot.utils import KYIV_TZ

logger = logging.getLogger(__name__)

SUMMARY_BUTTON = "📊 Підсумок списань за сьогодні"


def _number(value) -> float:
    """Return a finite numeric value, or zero for malformed Firestore data."""
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _format_amount(value: float) -> str:
    """Keep useful precision while avoiding trailing zeroes."""
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _aggregate_writeoffs(records: list[dict]) -> tuple[OrderedDict, OrderedDict, float]:
    """
    Aggregate products by name and ingredients by field name.

    Each record's totalIngredients is preferred because it is the canonical
    value already stored by the write-off flow. If absent, item ingredients
    are summed as a compatibility fallback.
    """
    products: OrderedDict[str, float] = OrderedDict()
    ingredients: OrderedDict[str, float] = OrderedDict()
    potato_kg = 0.0

    for record in records:
        for item in record.get("items", []) or []:
            if not isinstance(item, dict):
                continue
            name = item.get("popcornName") or item.get("name") or "Невідомий продукт"
            products[name] = products.get(name, 0.0) + _number(item.get("weight"))

        potato_kg += _number(record.get("potato_wedges"))

        stored_total = record.get("totalIngredients") or {}
        if isinstance(stored_total, dict) and stored_total:
            source = stored_total
        else:
            source = {}
            for item in record.get("items", []) or []:
                if not isinstance(item, dict):
                    continue
                for name, amount in (item.get("ingredients") or {}).items():
                    source[name] = source.get(name, 0.0) + _number(amount)

        for name, amount in source.items():
            numeric_amount = _number(amount)
            ingredients[name] = ingredients.get(name, 0.0) + numeric_amount

    return products, ingredients, potato_kg


def _summary_text(records: list[dict]) -> str:
    today_label = datetime.now(KYIV_TZ).strftime("%d.%m.%Y")
    products, ingredients, potato_kg = _aggregate_writeoffs(records)

    lines = [
        f"📊 *Підсумок списань за сьогодні* — {today_label}",
        f"🧾 Записів: {len(records)}",
        "",
        "📦 *Продукти:*",
    ]

    if products:
        for name, weight in products.items():
            lines.append(f"🍿 {name} — {_format_amount(weight)} кг")
    else:
        lines.append("—")

    if potato_kg > 0:
        lines.append(f"🥔 Картопляні спеки — {_format_amount(potato_kg)} кг")

    if ingredients:
        lines.extend(["", "🧂 *Інгредієнти:*"])
        for name, amount in ingredients.items():
            lines.append(f"• {name} — {_format_amount(amount)}")

    return "\n".join(lines)


async def daily_writeoff_summary_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Display today's read-only write-off aggregate."""
    telegram_id = update.effective_user.id
    if not is_authorized_user(telegram_id):
        logger.warning("Daily write-off summary denied for unauthorized user %s", telegram_id)
        await update.message.reply_text("Доступ заборонено.")
        return

    try:
        records = get_writeoffs_for_day()
    except Exception:
        logger.exception("Failed to load today's write-offs for user %s", telegram_id)
        await update.message.reply_text(
            "❌ Не вдалося завантажити підсумок списань. Спробуйте ще раз."
        )
        return

    if not records:
        await update.message.reply_text("Сьогодні списань немає.")
        return

    text = _summary_text(records)
    if len(text) > 4000:
        text = text[:4000] + "\n...(скорочено)"
    await update.message.reply_text(text, parse_mode="Markdown")