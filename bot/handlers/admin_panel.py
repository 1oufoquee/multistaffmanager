import logging
import re as _re
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ContextTypes, ConversationHandler, CommandHandler,
    MessageHandler, CallbackQueryHandler, filters,
)
from bot.firebase_client import (
    is_authorized_user, get_user_info,
    get_menu_items, get_menu_item, search_menu_items,
    create_menu_item, update_menu_item, delete_menu_item,
    get_all_staff, add_staff_user, update_staff_user, delete_staff_user,
    get_writeoffs_history,
)
from bot.utils import format_timestamp

logger = logging.getLogger(__name__)

ELEVATED_ROLES = ("admin", "Директор")

# ── States ────────────────────────────────────────────────────────────────────
AP_HOME          = 10

AP_MENU_HOME     = 20
AP_MENU_ADD_ID   = 21
AP_MENU_ADD_NAME = 22
AP_MENU_ADD_PRICE= 23
AP_MENU_ADD_MOD  = 24
AP_MENU_SEARCH   = 25
AP_MENU_EDIT_VAL = 26

AP_STAFF_HOME    = 30
AP_STAFF_ADD_NAME= 31
AP_STAFF_ADD_ROLE= 32
AP_STAFF_ADD_TG  = 33
AP_STAFF_LINK_TG = 34
AP_STAFF_CHG_ROLE= 35

AP_WO_HOME       = 40
AP_WO_DATE       = 41

# ── Role helpers ──────────────────────────────────────────────────────────────

ROLE_LABELS = {"admin": "Менеджер", "user": "Касир", "Директор": "Директор"}

def _role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role or "—")


# ── Access guard ──────────────────────────────────────────────────────────────

async def _deny(update: Update) -> int:
    await update.message.reply_text("⛔ Доступ заборонено.")
    return ConversationHandler.END


def _is_elevated(info: dict | None) -> bool:
    return (info or {}).get("userRole") in ELEVATED_ROLES


# ── Inline keyboard factories ─────────────────────────────────────────────────

def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(list(rows))

def _btn(label: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(label, callback_data=data)

def _back_btn(cb: str = "ap_home") -> list[InlineKeyboardButton]:
    return [_btn("← Назад", cb)]

AP_HOME_KB = _kb(
    [_btn("🍔 Меню", "ap_menu"), _btn("👥 Працівники", "ap_staff")],
    [_btn("🍿 Списання",  "ap_wo"),  _btn("⚙️ Налаштування", "ap_sets")],
    [_btn("❌ Закрити",  "ap_close")],
)

MENU_HOME_KB = _kb(
    [_btn("➕ Додати позицію",       "ap_m_add")],
    [_btn("🔍 Знайти / Редагувати", "ap_m_srch")],
    _back_btn("ap_home"),
)

STAFF_HOME_KB = _kb(
    [_btn("➕ Додати співробітника", "ap_s_add")],
    [_btn("📋 Список співробітників","ap_s_list")],
    _back_btn("ap_home"),
)

WO_HOME_KB = _kb(
    [_btn("📋 Всі списання",     "ap_wo_all")],
    [_btn("🔍 Пошук за датою",   "ap_wo_srch")],
    _back_btn("ap_home"),
)

MOD_KB = _kb(
    [_btn("🌡 Температура",        "ap_mod_t")],
    [_btn("🧂 Соуси",              "ap_mod_s")],
    [_btn("❌ Без модифікаторів",  "ap_mod_n")],
    _back_btn("ap_m_add"),
)

ROLE_KB = _kb(
    [_btn("Менеджер",  "ap_role_a")],
    [_btn("Касир",     "ap_role_u")],
    [_btn("Директор",  "ap_role_d")],
)


def _item_actions_kb(item_id: str, is_hidden: bool) -> InlineKeyboardMarkup:
    hide_label = "👁 Показати" if is_hidden else "🙈 Сховати"
    return _kb(
        [_btn("✏️ Назва",  f"ap_men_{item_id}"), _btn("💰 Ціна", f"ap_mep_{item_id}")],
        [_btn("🧂 Модифікатори", f"ap_mem_{item_id}")],
        [_btn(hide_label,  f"ap_meh_{item_id}"), _btn("🗑 Видалити", f"ap_med_{item_id}")],
        _back_btn("ap_m_srch"),
    )


def _item_delete_confirm_kb(item_id: str) -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Так, видалити", f"ap_medx_{item_id}"),
         _btn("❌ Скасувати",    f"ap_medc_{item_id}")],
    )


def _item_mod_kb(item_id: str) -> InlineKeyboardMarkup:
    return _kb(
        [_btn("🌡 Температура",       f"ap_mmt_{item_id}")],
        [_btn("🧂 Соуси",             f"ap_mms_{item_id}")],
        [_btn("❌ Без модифікаторів", f"ap_mmn_{item_id}")],
        [_btn("← Назад",             f"ap_mi_{item_id}")],
    )


def _staff_actions_kb(doc_id: str, is_blocked: bool) -> InlineKeyboardMarkup:
    block_label = "✅ Розблокувати" if is_blocked else "🚫 Заблокувати"
    return _kb(
        [_btn("✏️ Змінити роль",        f"ap_sfr_{doc_id}")],
        [_btn("📱 Прив'язати Telegram", f"ap_sft_{doc_id}")],
        [_btn(block_label,             f"ap_sfb_{doc_id}")],
        [_btn("🗑 Видалити",            f"ap_sfd_{doc_id}")],
        _back_btn("ap_s_list"),
    )


def _staff_delete_confirm_kb(doc_id: str) -> InlineKeyboardMarkup:
    return _kb(
        [_btn("✅ Так, видалити", f"ap_sfdx_{doc_id}"),
         _btn("❌ Скасувати",    f"ap_sfdc_{doc_id}")],
    )


# ── Format helpers ─────────────────────────────────────────────────────────────

def _menu_item_text(item: dict) -> str:
    iid     = item.get("_id", "—")
    name    = item.get("name", "—")
    price   = item.get("price", "—")
    mods    = item.get("modifiers")
    hidden  = item.get("isHidden", False)
    status  = " 🙈 прихована" if hidden else ""
    mod_str = ", ".join(mods) if isinstance(mods, list) else ("—" if mods is None else str(mods))
    return (
        f"🍔 *{name}*{status}\n"
        f"ID: `{iid}`\n"
        f"Ціна: {price} грн\n"
        f"Модифікатори: {mod_str}"
    )


def _staff_text(s: dict) -> str:
    name    = s.get("name", "—")
    role    = _role_label(s.get("userRole", ""))
    tid     = s.get("telegramId", "не прив'язано")
    blocked = " 🚫 заблокований" if s.get("isBlocked") else ""
    return f"👤 *{name}*{blocked}\nРоль: {role}\nTelegram ID: {tid}"


# ── Entry ─────────────────────────────────────────────────────────────────────

async def ap_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tid  = update.effective_user.id
    if not is_authorized_user(tid):
        return await _deny(update)

    info = get_user_info(tid)
    if not _is_elevated(info):
        return await _deny(update)

    context.user_data["ap_info"] = info or {}
    await update.message.reply_text(
        "👑 *Адмін-Панель*\n\nОберіть розділ:",
        parse_mode="Markdown",
        reply_markup=AP_HOME_KB,
    )
    return AP_HOME


# ── AP_HOME ───────────────────────────────────────────────────────────────────

async def handle_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = query.data

    if d == "ap_close":
        await query.edit_message_text("👑 Адмін-Панель закрито.")
        context.user_data.clear()
        return ConversationHandler.END

    if d == "ap_menu":
        context.user_data.pop("ap_add", None)
        context.user_data.pop("ap_sel_item", None)
        context.user_data.pop("ap_edit_field", None)

    await query.edit_message_text(
        "🍔 *Меню*\n\nОберіть дію:",
        parse_mode="Markdown",
        reply_markup=MENU_HOME_KB,)
    return AP_MENU_HOME

    if d == "ap_staff":
        await query.edit_message_text("👥 *Управління Працівниками*\n\nОберіть дію:", parse_mode="Markdown",       
        reply_markup=STAFF_HOME_KB)
        return AP_STAFF_HOME

    if d == "ap_wo":
        await query.edit_message_text("🍿 *Списання*\n\nОберіть дію:", parse_mode="Markdown", reply_markup=WO_HOME_KB)
        return AP_WO_HOME

    if d == "ap_sets":
        await query.edit_message_text(
            "⚙️ *Налаштування*\n\n_(Розділ у розробці)_",
            parse_mode="Markdown",
            reply_markup=_kb(_back_btn("ap_home")),
        )
        return AP_HOME

    return AP_HOME


# ═══════════════════════════════════════════════════════════════════════════════
# MENU SECTION
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_menu_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = query.data
    if d == "ap_m_add":
        context.user_data["ap_add"] = {}

        await query.edit_message_text(
            "➕ *Нова позиція*\n\nВведіть ID позиції англійською.\n\nНаприклад:\nburger\nmirinda\nice_cream_mango",
            parse_mode="Markdown",
        )

        return AP_MENU_ADD_ID

    if d == "ap_home":
        await query.edit_message_text("👑 *Адмін-Панель*\n\nОберіть розділ:", parse_mode="Markdown", reply_markup=AP_HOME_KB)
        return AP_HOME

    if d == "ap_m_add":
        context.user_data.pop("ap_add", None)
        await query.edit_message_text(
            "➕ *Додати позицію меню*\n\n"
            "Введіть ID (англійською, лише літери/цифри/_):\n"
            "_Приклад: cheese\\_popcorn\\_l_\n\n"
            "/cancel — скасувати",
            parse_mode="Markdown",
        )
        return AP_MENU_ADD_ID

    if d == "ap_m_srch":
        await query.edit_message_text(
            "🔍 *Пошук у меню*\n\nВведіть ID або назву позиції:\n\n/cancel — скасувати",
            parse_mode="Markdown",
        )
        return AP_MENU_SEARCH

    return AP_MENU_HOME


# ── Add menu item flow ────────────────────────────────────────────────────────

async def receive_add_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    if not _re.match(r'^[a-zA-Z0-9_]+$', raw):
        await update.message.reply_text(
            "⚠️ ID може містити лише латинські літери, цифри та _\n_Спробуйте ще раз:_",
            parse_mode="Markdown",
        )
        return AP_MENU_ADD_ID

    if get_menu_item(raw):
        await update.message.reply_text(
            f"⚠️ Позиція з ID `{raw}` вже існує. Введіть інший ID:",
            parse_mode="Markdown",
        )
        return AP_MENU_ADD_ID

    context.user_data["ap_add"] = {"id": raw}
    await update.message.reply_text(
        f"✅ ID: `{raw}`\n\nВведіть *назву* позиції (наприклад: Бургер з картоплею фрі):",
        parse_mode="Markdown",
    )
    return AP_MENU_ADD_NAME


async def receive_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    context.user_data["ap_add"]["name"] = name
    await update.message.reply_text(
        f"✅ Назва: *{name}*\n\nВведіть *ціну* (число, грн):",
        parse_mode="Markdown",
    )
    return AP_MENU_ADD_PRICE


async def receive_add_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip().replace(",", ".")
    try:
        price = float(raw)
        if price < 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Введіть коректне число (наприклад: 45 або 120.5)")
        return AP_MENU_ADD_PRICE

    context.user_data["ap_add"]["price"] = price
    await update.message.reply_text(
        f"✅ Ціна: *{price} грн*\n\nОберіть тип модифікаторів:",
        parse_mode="Markdown",
        reply_markup=MOD_KB,
    )
    return AP_MENU_ADD_MOD


async def receive_add_mod(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = query.data

    if d == "ap_m_add":
        await query.edit_message_text("🍔 *Меню*\n\nОберіть дію:", parse_mode="Markdown", reply_markup=MENU_HOME_KB)
        return AP_MENU_HOME

    add = context.user_data.get("ap_add", {})
    item_data: dict = {"name": add.get("name", ""), "price": add.get("price", 0)}

    if d == "ap_mod_t":
        item_data["modifiers"] = ["temperature"]
    elif d == "ap_mod_s":
        item_data["modifiers"] = ["garlic", "tomato", "spicy", "piquant"]
    # ap_mod_n → no modifiers field

    try:
        create_menu_item(add["id"], item_data)
    except Exception as e:
        await query.edit_message_text(f"❌ Помилка збереження: {e}")
        return AP_MENU_HOME

    await query.edit_message_text(
    f"✅ *Позицію додано!*\\n\\nID: `{add['id']}`\\nНазва: {item_data['name']}\\nЦіна: {item_data['price']} грн",
    parse_mode="Markdown",
    reply_markup=_kb(
        [_btn("➕ Продовжити", "ap_m_add")],
        [_btn("← Назад", "ap_menu")],
    )

     context.user_data.pop("ap_add", None)
     return AP_MENU_HOME


# ── Search & edit ─────────────────────────────────────────────────────────────

async def receive_menu_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query_text = update.message.text.strip()
    results = search_menu_items(query_text)

    if not results:
        await update.message.reply_text(
            f"🔍 За запитом *{query_text}* нічого не знайдено.\n\nСпробуйте інший запит або /cancel",
            parse_mode="Markdown",
        )
        return AP_MENU_SEARCH

    if len(results) == 1:
        item = results[0]
        context.user_data["ap_sel_item"] = item["_id"]
        await update.message.reply_text(
            _menu_item_text(item),
            parse_mode="Markdown",
            reply_markup=_item_actions_kb(item["_id"], item.get("isHidden", False)),
        )
        return AP_MENU_SEARCH

    # Multiple results → show selection buttons
    rows = [[_btn(f"{it.get('name','?')} ({it['_id']})", f"ap_mi_{it['_id']}")] for it in results]
    rows.append(_back_btn("ap_m_srch"))
    await update.message.reply_text(
        f"🔍 Знайдено {len(results)} позицій. Оберіть:",
        reply_markup=_kb(*rows),
    )
    return AP_MENU_SEARCH


async def handle_menu_search_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = query.data

    # ── Back navigation ──
    if d == "ap_home":
        await query.edit_message_text("👑 *Адмін-Панель*\n\nОберіть розділ:", parse_mode="Markdown", reply_markup=AP_HOME_KB)
        return AP_HOME
    if d == "ap_menu" or d == "ap_m_srch":
        await query.edit_message_text("🍔 *Меню*\n\nОберіть дію:", parse_mode="Markdown", reply_markup=MENU_HOME_KB)
        return AP_MENU_HOME

    # ── Select item ──
    if d.startswith("ap_mi_"):
        item_id = d[len("ap_mi_"):]
        item = get_menu_item(item_id)
        if not item:
            await query.edit_message_text("❌ Позицію не знайдено.")
            return AP_MENU_SEARCH
        context.user_data["ap_sel_item"] = item_id
        await query.edit_message_text(
            _menu_item_text(item), parse_mode="Markdown",
            reply_markup=_item_actions_kb(item_id, item.get("isHidden", False)),
        )
        return AP_MENU_SEARCH

    # ── Edit name ──
    if d.startswith("ap_men_"):
        item_id = d[len("ap_men_"):]
        context.user_data["ap_sel_item"] = item_id
        context.user_data["ap_edit_field"] = "name"
        await query.edit_message_text(
            "✏️ Введіть нову *назву* позиції:\n\n/cancel — скасувати",
            parse_mode="Markdown",
        )
        return AP_MENU_EDIT_VAL

    # ── Edit price ──
    if d.startswith("ap_mep_"):
        item_id = d[len("ap_mep_"):]
        context.user_data["ap_sel_item"] = item_id
        context.user_data["ap_edit_field"] = "price"
        await query.edit_message_text(
            "💰 Введіть нову *ціну* (грн):\n\n/cancel — скасувати",
            parse_mode="Markdown",
        )
        return AP_MENU_EDIT_VAL

    # ── Edit modifiers ──
    if d.startswith("ap_mem_"):
        item_id = d[len("ap_mem_"):]
        context.user_data["ap_sel_item"] = item_id
        await query.edit_message_text(
            "🧂 Оберіть новий тип модифікаторів:",
            reply_markup=_item_mod_kb(item_id),
        )
        return AP_MENU_SEARCH

    # ── Modifier update callbacks ──
    if d.startswith("ap_mmt_") or d.startswith("ap_mms_") or d.startswith("ap_mmn_"):
        prefix_len = len("ap_mmt_")
        item_id = d[prefix_len:]
        if d.startswith("ap_mmt_"):
            updates = {"modifiers": ["temperature"]}
        elif d.startswith("ap_mms_"):
            updates = {"modifiers": ["garlic", "tomato", "spicy", "piquant"]}
        else:
            updates = {"modifiers": firestore_delete_field()}
        try:
            update_menu_item(item_id, updates)
        except Exception:
            pass
        item = get_menu_item(item_id)
        if item:
            await query.edit_message_text(
                "✅ Модифікатори оновлено!\n\n" + _menu_item_text(item),
                parse_mode="Markdown",
                reply_markup=_item_actions_kb(item_id, item.get("isHidden", False)),
            )
        return AP_MENU_SEARCH

    # ── Hide/show ──
    if d.startswith("ap_meh_"):
        item_id = d[len("ap_meh_"):]
        item = get_menu_item(item_id)
        if item:
            new_hidden = not item.get("isHidden", False)
            update_menu_item(item_id, {"isHidden": new_hidden})
            item["isHidden"] = new_hidden
            status = "сховано 🙈" if new_hidden else "показано 👁"
            await query.edit_message_text(
                f"✅ Позицію {status}\n\n" + _menu_item_text(item),
                parse_mode="Markdown",
                reply_markup=_item_actions_kb(item_id, new_hidden),
            )
        return AP_MENU_SEARCH

    # ── Delete: ask confirm ──
    if d.startswith("ap_med_") and not d.startswith("ap_medx_") and not d.startswith("ap_medc_"):
        item_id = d[len("ap_med_"):]
        item = get_menu_item(item_id)
        name = (item or {}).get("name", item_id)
        context.user_data["ap_sel_item"] = item_id
        await query.edit_message_text(
            f"🗑 Видалити *{name}*?\n\nЦю дію неможливо скасувати.",
            parse_mode="Markdown",
            reply_markup=_item_delete_confirm_kb(item_id),
        )
        return AP_MENU_SEARCH

    # ── Confirm delete ──
    if d.startswith("ap_medx_"):
        item_id = d[len("ap_medx_"):]
        try:
            delete_menu_item(item_id)
            await query.edit_message_text(
                f"✅ Позицію `{item_id}` видалено.",
                parse_mode="Markdown",
                reply_markup=_kb(_back_btn("ap_menu")),
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Помилка видалення: {e}")
        return AP_MENU_HOME

    # ── Cancel delete ──
    if d.startswith("ap_medc_"):
        item_id = d[len("ap_medc_"):]
        item = get_menu_item(item_id)
        if item:
            await query.edit_message_text(
                _menu_item_text(item), parse_mode="Markdown",
                reply_markup=_item_actions_kb(item_id, item.get("isHidden", False)),
            )
        return AP_MENU_SEARCH

    return AP_MENU_SEARCH


async def receive_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    field   = context.user_data.get("ap_edit_field")
    item_id = context.user_data.get("ap_sel_item")
    raw     = update.message.text.strip()

    if field == "price":
        try:
            value = float(raw.replace(",", "."))
        except ValueError:
            await update.message.reply_text("⚠️ Введіть коректне число.")
            return AP_MENU_EDIT_VAL
    else:
        value = raw

    try:
        update_menu_item(item_id, {field: value})
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка: {e}")
        return AP_MENU_EDIT_VAL

    item = get_menu_item(item_id)
    field_label = "Назву" if field == "name" else "Ціну"
    await update.message.reply_text(
        f"✅ {field_label} оновлено!\n\n" + _menu_item_text(item),
        parse_mode="Markdown",
        reply_markup=_item_actions_kb(item_id, (item or {}).get("isHidden", False)),
    )
    return AP_MENU_SEARCH


# ── Callback also needed in AP_MENU_EDIT_VAL for modifier/nav ─────────────────

async def handle_edit_val_cb(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    return await handle_menu_search_cb(update, context)


# ═══════════════════════════════════════════════════════════════════════════════
# STAFF SECTION
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_staff_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = query.data

    # FIX: correctly reopen staff menu after actions
    if d == "ap_staff":
        await query.edit_message_text(
            "👥 *Управління Працівниками*\n\nОберіть дію:",
            parse_mode="Markdown",
            reply_markup=STAFF_HOME_KB,
        )
        return AP_STAFF_HOME

    if d == "ap_home":
        await query.edit_message_text(
            "👑 *Адмін-Панель*\n\nОберіть розділ:",
            parse_mode="Markdown",
            reply_markup=AP_HOME_KB,
        )
        return AP_HOME

    if d == "ap_s_add":
        context.user_data.pop("ap_new_staff", None)
        await query.edit_message_text(
            "➕ *Новий співробітник*\n\nВведіть ім'я:\n\n/cancel — скасувати",
            parse_mode="Markdown",
        )
        return AP_STAFF_ADD_NAME

    if d == "ap_s_list" or d == "ap_s_rlist":
        try:
            staff = get_all_staff()
        except Exception as e:
            await query.edit_message_text(f"❌ Помилка: {e}")
            return AP_STAFF_HOME

        if not staff:
            await query.edit_message_text(
                "👥 Список співробітників порожній.",
                reply_markup=_kb(_back_btn("ap_home")),
            )
            return AP_STAFF_HOME

        rows = []
        for s in staff:
            name = s.get("name", "—")
            role = _role_label(s.get("userRole", ""))
            blocked = " 🚫" if s.get("isBlocked") else ""
            rows.append([_btn(f"{name} ({role}){blocked}", f"ap_sf_{s['_id']}")])

        rows.append(_back_btn("ap_home"))

        await query.edit_message_text(
            "👥 *Список співробітників:*",
            parse_mode="Markdown",
            reply_markup=_kb(*rows),
        )
        return AP_STAFF_HOME

    # Select staff member
    if d.startswith("ap_sf_") and len(d) > 6:
        suffix = d[len("ap_sf_"):]

        # dispatch actions
        if "_" in suffix:
            return await _handle_staff_action(query, context, suffix)

        # open employee
        doc_id = suffix
        return await _show_staff_member(query, context, doc_id)

    # back from delete confirm
    if d == "ap_s_back":
        await query.edit_message_text(
            "👥 *Управління Працівниками*\n\nОберіть дію:",
            parse_mode="Markdown",
            reply_markup=STAFF_HOME_KB,
        )
        return AP_STAFF_HOME

    return AP_STAFF_HOME


async def _show_staff_member(query, context, doc_id: str) -> int:
    staff = get_all_staff()
    member = next((s for s in staff if s["_id"] == doc_id), None)
    if not member:
        await query.edit_message_text("❌ Співробітника не знайдено.")
        return AP_STAFF_HOME
    context.user_data["ap_sel_staff"] = doc_id
    await query.edit_message_text(
        _staff_text(member), parse_mode="Markdown",
        reply_markup=_staff_actions_kb(doc_id, member.get("isBlocked", False)),
    )
    return AP_STAFF_HOME


async def _handle_staff_action(query, context, suffix: str) -> int:
    """Handle ap_sf_ callbacks that contain sub-action prefix."""
    # suffix examples: r_docid, t_docid, b_docid, d_docid, dx_docid, dc_docid
    parts = suffix.split("_", 1)
    if len(parts) < 2:
        return AP_STAFF_HOME
    action, doc_id = parts[0], parts[1]

    if action == "r":
        context.user_data["ap_sel_staff"] = doc_id
        await query.edit_message_text(
            "✏️ Оберіть нову роль:", reply_markup=ROLE_KB,
        )
        return AP_STAFF_CHG_ROLE

    if action == "t":
        context.user_data["ap_sel_staff"] = doc_id
        await query.edit_message_text(
            "📱 Введіть Telegram ID для прив'язки:\n\n/cancel — скасувати",
        )
        return AP_STAFF_LINK_TG

    if action == "b":
        staff = get_all_staff()
        member = next((s for s in staff if s["_id"] == doc_id), None)
        if member:
            new_blocked = not member.get("isBlocked", False)
            update_staff_user(doc_id, {"isBlocked": new_blocked})
            member["isBlocked"] = new_blocked
            status = "заблоковано 🚫" if new_blocked else "розблоковано ✅"
            await query.edit_message_text(
                f"Співробітника {status}\n\n" + _staff_text(member),
                parse_mode="Markdown",
                reply_markup=_staff_actions_kb(doc_id, new_blocked),
            )
        return AP_STAFF_HOME

    if action == "d":
        staff = get_all_staff()
        member = next((s for s in staff if s["_id"] == doc_id), None)
        name = (member or {}).get("name", doc_id)
        await query.edit_message_text(
            f"🗑 Видалити *{name}* з системи?\n\nЦю дію неможливо скасувати.",
            parse_mode="Markdown",
            reply_markup=_staff_delete_confirm_kb(doc_id),
        )
        return AP_STAFF_HOME

    if action == "dx":
        try:
            delete_staff_user(doc_id)
            await query.edit_message_text(
                "✅ Співробітника видалено.",
                reply_markup=_kb(_back_btn("ap_s_list")),
            )
        except Exception as e:
            await query.edit_message_text(f"❌ Помилка: {e}")
        return AP_STAFF_HOME

    if action == "dc":
        return await _show_staff_member(query, context, doc_id)

    return AP_STAFF_HOME


# ── Add staff flow ────────────────────────────────────────────────────────────

async def receive_staff_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    context.user_data["ap_new_staff"] = {"name": name}
    await update.message.reply_text(
        f"✅ Ім'я: *{name}*\n\nОберіть роль:",
        parse_mode="Markdown",
        reply_markup=ROLE_KB,
    )
    return AP_STAFF_ADD_ROLE


async def receive_staff_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    role_map = {"ap_role_a": "admin", "ap_role_u": "user", "ap_role_d": "Директор"}
    role = role_map.get(query.data, "user")
    context.user_data["ap_new_staff"]["userRole"] = role
    await query.edit_message_text(
        f"✅ Роль: *{_role_label(role)}*\n\n"
        f"Введіть Telegram ID (або 0 щоб пропустити):\n\n/cancel — скасувати",
        parse_mode="Markdown",
    )
    return AP_STAFF_ADD_TG


async def receive_staff_tg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    data = context.user_data.get("ap_new_staff", {})
    try:
        tid = int(raw)
        if tid != 0:
            data["telegramId"] = tid
    except ValueError:
        await update.message.reply_text("⚠️ Telegram ID — це число. Введіть ще раз або 0:")
        return AP_STAFF_ADD_TG

    try:
        doc_id = add_staff_user(data)
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка: {e}")
        return AP_STAFF_HOME

    tg_display = data.get("telegramId", "не вказано")
    await update.message.reply_text(
        f"✅ *Співробітника додано!*\n\n"
        f"Ім'я: {data.get('name')}\n"
        f"Роль: {_role_label(data.get('userRole', ''))}\n"
        f"Telegram ID: {tg_display}\n"
        f"Doc ID: `{doc_id}`",
        parse_mode="Markdown",
        reply_markup=_kb(_back_btn("ap_staff")),
    )
    context.user_data.pop("ap_new_staff", None)
    return AP_STAFF_HOME


# ── Link Telegram ID ──────────────────────────────────────────────────────────

async def receive_link_tg(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc_id = context.user_data.get("ap_sel_staff")
    raw = update.message.text.strip()
    try:
        tid = int(raw)
    except ValueError:
        await update.message.reply_text("⚠️ Telegram ID — це число. Введіть ще раз:")
        return AP_STAFF_LINK_TG

    try:
        update_staff_user(doc_id, {"telegramId": tid})
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка: {e}")
        return AP_STAFF_HOME

    await update.message.reply_text(
        f"✅ Telegram ID `{tid}` прив'язано.",
        parse_mode="Markdown",
        reply_markup=_kb(_back_btn("ap_staff")),
    )
    return AP_STAFF_HOME


# ── Change role ───────────────────────────────────────────────────────────────

async def receive_change_role(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    doc_id = context.user_data.get("ap_sel_staff")
    role_map = {"ap_role_a": "admin", "ap_role_u": "user", "ap_role_d": "Директор"}
    role = role_map.get(query.data, "user")
    try:
        update_staff_user(doc_id, {"userRole": role})
    except Exception as e:
        await query.edit_message_text(f"❌ Помилка: {e}")
        return AP_STAFF_HOME

    await query.edit_message_text(
        f"✅ Роль змінено на *{_role_label(role)}*",
        parse_mode="Markdown",
        reply_markup=_kb(_back_btn("ap_s_list")),
    )
    return AP_STAFF_HOME


# ═══════════════════════════════════════════════════════════════════════════════
# WRITE-OFFS SECTION
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_wo_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    d = query.data

    if d == "ap_home":
        await query.edit_message_text("👑 *Адмін-Панель*\n\nОберіть розділ:", parse_mode="Markdown", reply_markup=AP_HOME_KB)
        return AP_HOME

    if d == "ap_wo_all":
        await _send_wo_list(query, limit=30)
        return AP_WO_HOME

    if d == "ap_wo_srch":
        await query.edit_message_text(
            "🔍 *Пошук за датою*\n\nВведіть дату у форматі *ДД.ММ.РРРР* або *ММ.РРРР*:\n\n/cancel — скасувати",
            parse_mode="Markdown",
        )
        return AP_WO_DATE

    return AP_WO_HOME


async def _send_wo_list(query, limit: int = 30, filter_date: str | None = None):
    try:
        history = get_writeoffs_history(limit=limit)
    except Exception as e:
        await query.edit_message_text(f"❌ Помилка: {e}")
        return

    if filter_date:
        history = _filter_wo_by_date(history, filter_date)

    if not history:
        msg = "📋 Списань не знайдено."
        if filter_date:
            msg = f"📋 Списань за *{filter_date}* не знайдено."
        await query.edit_message_text(msg, parse_mode="Markdown", reply_markup=_kb(_back_btn("ap_wo")))
        return

    lines = [f"📋 *Списання* — {len(history)} записів\n"]
    for entry in history:
        created = format_timestamp(entry.get("createdAt"))
        staff   = entry.get("staffName", "—")
        items   = entry.get("items", [])
        flavors = " | ".join(
            f"{it.get('popcornName') or it.get('name','?')} {it.get('weight',0)}кг"
            for it in items if isinstance(it, dict)
        ) or "—"
        total = entry.get("totalIngredients", {})
        lines.append(f"🕐 {created}  👤 {staff}")
        lines.append(f"🍿 {flavors}")
        for k, v in total.items():
            lines.append(f"  • {k} — {v}")
        lines.append("─────────────")

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n...(скорочено)"

    await query.edit_message_text(
        text, parse_mode="Markdown",
        reply_markup=_kb(_back_btn("ap_wo")),
    )


def _filter_wo_by_date(history: list, filter_date: str) -> list:
    parts = filter_date.split(".")
    try:
        if len(parts) == 3:
            target = datetime(int(parts[2]), int(parts[1]), int(parts[0]), tzinfo=timezone.utc)
            return [
                e for e in history
                if _wo_matches_day(e.get("createdAt"), target)
            ]
        elif len(parts) == 2:
            month, year = int(parts[0]), int(parts[1])
            return [
                e for e in history
                if _wo_matches_month(e.get("createdAt"), month, year)
            ]
    except (ValueError, IndexError):
        pass
    return history


def _wo_matches_day(ts, target: datetime) -> bool:
    try:
        if hasattr(ts, "year"):
            dt = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
            return dt.year == target.year and dt.month == target.month and dt.day == target.day
    except Exception:
        pass
    return False


def _wo_matches_month(ts, month: int, year: int) -> bool:
    try:
        if hasattr(ts, "year"):
            dt = ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts
            return dt.month == month and dt.year == year
    except Exception:
        pass
    return False


async def receive_wo_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    if not _re.match(r'^\d{2}\.\d{2}(\.\d{4})?$', raw):
        await update.message.reply_text(
            "⚠️ Невірний формат. Використовуйте *ДД.ММ.РРРР* або *ММ.РРРР*\n_Приклад: 17.05.2026 або 05.2026_",
            parse_mode="Markdown",
        )
        return AP_WO_DATE

    # Need a dummy query-like object — send as new message instead
    class _Proxy:
        async def edit_message_text(self, text, **kw):
            await update.message.reply_text(text, **kw)

    await _send_wo_list(_Proxy(), limit=100, filter_date=raw)
    return AP_WO_HOME


# ═══════════════════════════════════════════════════════════════════════════════
# CANCEL / FALLBACK
# ═══════════════════════════════════════════════════════════════════════════════

async def ap_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("↩️ Адмін-Панель закрито.")
    return ConversationHandler.END


# ── Firestore field delete helper ─────────────────────────────────────────────

def firestore_delete_field():
    from google.cloud.firestore_v1 import DELETE_FIELD
    return DELETE_FIELD


# ═══════════════════════════════════════════════════════════════════════════════
# BUILD HANDLER
# ═══════════════════════════════════════════════════════════════════════════════

def build_admin_panel() -> ConversationHandler:
    txt = filters.TEXT & ~filters.COMMAND
    ap  = r"^ap_"
    return ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^👑 Адмін-Панель$"), ap_start),
        ],
        states={
            AP_HOME: [
                CallbackQueryHandler(handle_home, pattern=ap),
            ],
            # ── Menu ──
            AP_MENU_HOME: [
                CallbackQueryHandler(handle_menu_home, pattern=ap),
            ],
            AP_MENU_ADD_ID: [
                MessageHandler(txt, receive_add_id),
                CallbackQueryHandler(handle_menu_home, pattern=ap),
            ],
            AP_MENU_ADD_NAME: [
                MessageHandler(txt, receive_add_name),
            ],
            AP_MENU_ADD_PRICE: [
                MessageHandler(txt, receive_add_price),
            ],
            AP_MENU_ADD_MOD: [
                CallbackQueryHandler(receive_add_mod, pattern=ap),
            ],
            AP_MENU_SEARCH: [
                MessageHandler(txt, receive_menu_search),
                CallbackQueryHandler(handle_menu_search_cb, pattern=ap),
            ],
            AP_MENU_EDIT_VAL: [
                MessageHandler(txt, receive_edit_value),
                CallbackQueryHandler(handle_edit_val_cb, pattern=ap),
            ],
            # ── Staff ──
            AP_STAFF_HOME: [
                CallbackQueryHandler(handle_staff_home, pattern=ap),
            ],
            AP_STAFF_ADD_NAME: [
                MessageHandler(txt, receive_staff_name),
            ],
            AP_STAFF_ADD_ROLE: [
                CallbackQueryHandler(receive_staff_role, pattern=r"^ap_role_"),
            ],
            AP_STAFF_ADD_TG: [
                MessageHandler(txt, receive_staff_tg),
            ],
            AP_STAFF_LINK_TG: [
                MessageHandler(txt, receive_link_tg),
            ],
            AP_STAFF_CHG_ROLE: [
                CallbackQueryHandler(receive_change_role, pattern=r"^ap_role_"),
            ],
            # ── Write-offs ──
            AP_WO_HOME: [
                CallbackQueryHandler(handle_wo_home, pattern=ap),
            ],
            AP_WO_DATE: [
                MessageHandler(txt, receive_wo_date),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", ap_cancel),
        ],
        per_user=True,
        per_chat=True,
        per_message=False,
    )
