"""
Cinema Schedule Handler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Reads session data ONLY from Firestore.
The Telegram bot never scrapes Multiplex directly.

Data flow for reads:
  Firestore  →  get_sessions()  →  this handler  →  Telegram

Data flow for writes (background job):
  Multiplex  →  services/schedule_import  →  Firestore
"""

import logging
from datetime import date, timedelta, datetime, timezone, timedelta as td

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

# Kyiv is UTC+3 in summer (EEST) / UTC+2 in winter (EET)
# We use a fixed UTC+3 offset which covers the cinema operating season.
# Switch to pytz / zoneinfo if sub-hour DST precision is ever required.
_KYIV_TZ = timezone(td(hours=3))


def _now_kyiv() -> datetime:
    """Current datetime in Kyiv time."""
    return datetime.now(tz=_KYIV_TZ)


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
    hall   = s.get("hall", "")
    parts  = [f"⏰ {time} — {movie}"]
    extras = []
    if fmt and fmt not in ("—", "2D", ""):
        extras.append(fmt)
    if hall and hall not in ("—", ""):
        extras.append(hall)
    if extras:
        parts.append(f"({', '.join(extras)})")
    return " ".join(parts)


def _upcoming(sessions: list[dict], now: datetime | None = None) -> list[dict]:
    """
    Keep only sessions that haven't started yet.
    Compares session time against Kyiv local time.
    """
    if now is None:
        now = _now_kyiv()
    now_str = now.strftime("%H:%M")
    logger.debug("_upcoming: Kyiv time=%s, total sessions=%d", now_str, len(sessions))
    result = [s for s in sessions if (s.get("sessionTime") or "00:00") >= now_str]
    logger.debug("_upcoming: sessions after time filter=%d", len(result))
    return result


def _format_day_block(sessions: list[dict], heading: str, upcoming_only: bool = False) -> str:
    now   = _now_kyiv()
    items = _upcoming(sessions, now) if upcoming_only else sessions
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
    now    = _now_kyiv()
    today  = now.date()

    # Debug: count total sessions in Firestore for today
    today_sessions = get_sessions(cinema, today.strftime("%Y-%m-%d"))
    logger.info("[schedule] User %d opened schedule | cinema=%s | Kyiv time=%s | today sessions in DB=%d",
                tid, cinema, now.strftime("%Y-%m-%d %H:%M"), len(today_sessions))

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
    now    = _now_kyiv()
    today  = now.date()
    d      = query.data

    logger.info("[schedule_cb] %s | cinema=%s | Kyiv=%s", d, cinema, now.strftime("%H:%M"))

    if d == "cs_today":
        sessions = get_sessions(cinema, today.strftime("%Y-%m-%d"))
        logger.info("[schedule_cb] cs_today: loaded %d session(s) from Firestore", len(sessions))
        upcoming = _upcoming(sessions, now)
        logger.info("[schedule_cb] cs_today: %d upcoming after %s Kyiv", len(upcoming), now.strftime("%H:%M"))
        text = _format_day_block(
            sessions,
            f"🎬 *{label} — Сьогодні {today.strftime('%d.%m')}*",
            upcoming_only=True,
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=SCHEDULE_KB)

    elif d == "cs_tomorrow":
        tomorrow = today + timedelta(days=1)
        sessions = get_sessions(cinema, tomorrow.strftime("%Y-%m-%d"))
        logger.info("[schedule_cb] cs_tomorrow: loaded %d session(s)", len(sessions))
        text = _format_day_block(
            sessions,
            f"🎬 *{label} — Завтра {tomorrow.strftime('%d.%m')}*",
            upcoming_only=False,
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=SCHEDULE_KB)

    elif d == "cs_next":
        sessions = get_sessions(cinema, today.strftime("%Y-%m-%d"))
        upcoming = _upcoming(sessions, now)
        logger.info("[schedule_cb] cs_next: %d sessions today, %d upcoming", len(sessions), len(upcoming))

        if not upcoming:
            await query.edit_message_text(
                "🎬 На сьогодні більше немає запланованих сеансів.",
                reply_markup=SCHEDULE_KB,
            )
            return

        nxt = upcoming[0]

        # Minutes until session (Kyiv time)
        try:
            h, m     = map(int, nxt["sessionTime"].split(":"))
            sess_dt  = now.replace(hour=h, minute=m, second=0, microsecond=0)
            diff_sec = (sess_dt - now).total_seconds()
            mins     = max(0, int(diff_sec // 60))
            time_str = f"⌛ Через: {mins} хв" if mins > 0 else "⌛ Починається зараз"
        except Exception as exc:
            logger.warning("Could not compute minutes to session: %s", exc)
            time_str = ""

        fmt  = nxt.get("format", "")
        hall = nxt.get("hall", "")
        fmt_line  = f"\n🎟 {fmt}"  if fmt  and fmt  not in ("—", "2D", "") else ""
        hall_line = f"\n🏛 {hall}" if hall and hall not in ("—", "")       else ""

        await query.edit_message_text(
            f"🎞 *Найближчий сеанс*\n\n"
            f"🎬 {nxt.get('movieTitle', '—')}\n"
            f"🕐 {nxt.get('sessionTime', '—')}{fmt_line}{hall_line}\n"
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
