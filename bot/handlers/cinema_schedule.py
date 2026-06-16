"""
Cinema Schedule Handler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Reads session data ONLY from Firestore.
The Telegram bot never scrapes Multiplex directly.

Data flow for reads:
  Firestore  →  get_sessions()  →  this handler  →  Telegram

Data flow for writes (background job):
  Multiplex  →  services/schedule_import  →  Firestore
"""

import logging
from datetime import date, timedelta, datetime

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from bot.firebase_client import (
    is_authorized_user,
    get_user_info,
    get_user_cinema,
    get_sessions,
    update_staff_user,
)

logger = logging.getLogger(__name__)

# ── Keyboards ─────────────────────────────────────────────────────────────────

def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(list(rows))

def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)

SCHEDULE_KB = _kb(
    [_btn("📋 Сьогодні",          "cs_today"),
     _btn("📋 Завтра",            "cs_tomorrow")],
    [_btn("🎞 Найближчий сеанс",  "cs_next")],
    [_btn("💡 Нагадування світла","cs_light")],
)

# ── Cinema display names ──────────────────────────────────────────────────────

CINEMA_LABELS: dict[str, str] = {
    "atmosfera": "Атмосфера",
    "karavan":   "Каравань",
}

def _cinema_label(cinema: str) -> str:
    return CINEMA_LABELS.get(cinema, cinema.title())


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_session_line(s: dict) -> str:
    movie  = s.get("movieTitle", "—")
    time   = s.get("sessionTime", "—")
    fmt    = s.get("format", "")
    suffix = f" ({fmt})" if fmt and fmt not in ("—", "") else ""
    return f"⏰ {time} — {movie}{suffix}"


def _upcoming(sessions: list[dict]) -> list[dict]:
    """Keep only sessions that haven't started yet (today only)."""
    now_str = datetime.now().strftime("%H:%M")
    return [s for s in sessions if (s.get("sessionTime") or "00:00") >= now_str]


def _format_day_block(sessions: list[dict], heading: str, upcoming_only: bool = False) -> str:
    items = _upcoming(sessions) if upcoming_only else sessions
    if not items:
        msg = "Немає сеансів." if not upcoming_only else "Всі сеанси вже завершились."
        return f"{heading}\n_{msg}_"
    lines = [heading, ""]
    for s in items:
        lines.append(_fmt_session_line(s))
    return "\n".join(lines)


# ── Handlers ──────────────────────────────────────────────────────────────────

async def cinema_schedule_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tid = update.effective_user.id
    if not is_authorized_user(tid):
        await update.message.reply_text("⛔ Доступ заборонено.")
        return

    cinema = get_user_cinema(tid)
    label  = _cinema_label(cinema)

    await update.message.reply_text(
        f"🎬 *Сеанси — {label}*\n\nОберіть день або перегляньте найближчий сеанс:",
        parse_mode="Markdown",
        reply_markup=SCHEDULE_KB,
    )


async def handle_schedule_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    tid    = update.effective_user.id
    info   = get_user_info(tid) or {}
    cinema = get_user_cinema(tid)
    label  = _cinema_label(cinema)
    today  = date.today()
    d      = query.data

    if d == "cs_today":
        sessions = get_sessions(cinema, today.strftime("%Y-%m-%d"))
        text = _format_day_block(
            sessions,
            f"🎬 *{label} — Сьогодні {today.strftime('%d.%m')}*",
            upcoming_only=True,
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=SCHEDULE_KB)

    elif d == "cs_tomorrow":
        tomorrow = today + timedelta(days=1)
        sessions = get_sessions(cinema, tomorrow.strftime("%Y-%m-%d"))
        text = _format_day_block(
            sessions,
            f"🎬 *{label} — Завтра {tomorrow.strftime('%d.%m')}*",
            upcoming_only=False,
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=SCHEDULE_KB)

    elif d == "cs_next":
        sessions = get_sessions(cinema, today.strftime("%Y-%m-%d"))
        upcoming = _upcoming(sessions)

        if not upcoming:
            await query.edit_message_text(
                "🎬 На сьогодні більше немає запланованих сеансів.",
                reply_markup=SCHEDULE_KB,
            )
            return

        nxt = upcoming[0]

        # Minutes until session
        try:
            h, m    = map(int, nxt["sessionTime"].split(":"))
            sess_dt = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
            diff    = (sess_dt - datetime.now()).total_seconds()
            mins    = max(0, int(diff // 60))
            time_str = f"⌛ Через: {mins} хв" if mins > 0 else "⌛ Починається зараз"
        except Exception:
            time_str = ""

        fmt = nxt.get("format", "")
        fmt_line = f"\n🎟 {fmt}" if fmt and fmt not in ("—", "") else ""

        await query.edit_message_text(
            f"🎞 *Найближчий сеанс*\n\n"
            f"🎬 {nxt.get('movieTitle', '—')}\n"
            f"🕐 {nxt.get('sessionTime', '—')}{fmt_line}\n"
            f"🏛 {nxt.get('hall', '—')}\n"
            f"{time_str}",
            parse_mode="Markdown",
            reply_markup=SCHEDULE_KB,
        )

    elif d == "cs_light":
        enabled   = info.get("lightReminders", False)
        new_state = not enabled
        if info.get("_id"):
            update_staff_user(info["_id"], {"lightReminders": new_state})
        state_text = "УВІМК 💡" if new_state else "ВИМК 🔇"
        await query.edit_message_text(
            f"💡 Нагадування про включення світла: *{state_text}*",
            parse_mode="Markdown",
            reply_markup=SCHEDULE_KB,
        )
