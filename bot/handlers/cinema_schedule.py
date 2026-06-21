"""
Cinema Schedule Handler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Reads session data from the NEW schedule collection ONLY:

  Cinema/{cinema}/cinema_schedule/{date}/sessions/{sessionId}

New document fields: movie, hall (SessionScreenName), startTime (ISO 8601),
endTime (ISO 8601), sessionId, generatedAt.

Data flow for reads:
  Firestore cinema_schedule  →  get_schedule_sessions()  →  this handler

"📋 Today" also triggers an immediate schedule refresh from Multiplex so
the user always sees up-to-date data without waiting until 06:00.
"""

import logging
from datetime import date, timedelta, datetime, timezone, timedelta as td

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from bot.firebase_client import (
    is_authorized_user,
    get_user_info,
    get_user_cinema,
    get_schedule_sessions,
    update_staff_user,
)
from bot.hall_config import schedule_hall_label

logger = logging.getLogger(__name__)

_KYIV_TZ = timezone(td(hours=3))


def _now_kyiv() -> datetime:
    return datetime.now(tz=_KYIV_TZ)


# ── Keyboards ─────────────────────────────────────────────────────────────────

def _kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(list(rows))

def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text, callback_data=data)

SCHEDULE_KB = _kb(
    [_btn("📋 Сьогодні",         "cs_today"),
     _btn("📋 Завтра",           "cs_tomorrow")],
    [_btn("🎞 Найближчий сеанс", "cs_next")],
    [_btn("💡 Нагадування світла", "cs_light")],
)

# ── Cinema display names ──────────────────────────────────────────────────────

CINEMA_LABELS: dict[str, str] = {
    "atmosfera": "Атмосфера",
    "karavan":   "Каравань",
}

def _cinema_label(cinema: str) -> str:
    return CINEMA_LABELS.get(cinema, cinema.title())


# ── ISO time helpers ──────────────────────────────────────────────────────────

def _parse_iso(iso: str) -> datetime | None:
    """Parse ISO 8601 timestamp → aware datetime, or None on error."""
    try:
        return datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return None


def _iso_to_hhmm(iso: str) -> str:
    """'2026-06-21T10:00:00+03:00' → '10:00'   (in Kyiv tz).  '—' on error."""
    dt = _parse_iso(iso)
    if dt:
        return dt.astimezone(_KYIV_TZ).strftime("%H:%M")
    return "—"


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_session_block(s: dict) -> str:
    """
    Format one session from the new schedule collection:
        ⏰ 10:00 — Minecraft
        🏛 Зал 4 (VIP)
    Hall line is omitted when hall is empty/missing.
    """
    movie    = s.get("movie", "—")
    time_str = _iso_to_hhmm(s.get("startTime", ""))
    hall     = schedule_hall_label(s.get("hall", ""))

    first_line = f"⏰ {time_str} — {movie}"
    if hall:
        return f"{first_line}\n🏛 {hall}"
    return first_line


def _upcoming(sessions: list[dict], now: datetime) -> list[dict]:
    """Keep sessions whose startTime is in the future (relative to now)."""
    result = []
    for s in sessions:
        dt = _parse_iso(s.get("startTime", ""))
        if dt and dt > now:
            result.append(s)
    return result


def _format_day_block(
    sessions: list[dict],
    heading: str,
    upcoming_only: bool = False,
    now: datetime | None = None,
) -> str:
    if upcoming_only:
        items = _upcoming(sessions, now or _now_kyiv())
    else:
        items = sessions

    if not items:
        msg = "Немає сеансів." if not upcoming_only else "Всі сеанси вже завершились."
        return f"{heading}\n_{msg}_"

    lines = [heading, ""]
    for s in items:
        lines.append(_fmt_session_block(s))
        lines.append("")

    if lines and lines[-1] == "":
        lines.pop()

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

    sessions = get_schedule_sessions(cinema, now.strftime("%Y-%m-%d"))
    logger.info(
        "[schedule] User %d | cinema=%s | Kyiv=%s | sessions today=%d",
        tid, cinema, now.strftime("%Y-%m-%d %H:%M"), len(sessions),
    )

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

    # ── Today: refresh from Multiplex then display ────────────────────────────
    if d == "cs_today":
        await query.edit_message_text("🔄 _Оновлення розкладу…_", parse_mode="Markdown")
        try:
            from services.multiplex_schedule_parser import generate_daily_schedule
            total = await generate_daily_schedule(cinema)
            logger.info("[cs_today] Generated %d sessions for %s", total, cinema)
        except Exception as exc:
            logger.error("[cs_today] Schedule refresh failed: %s", exc)

        today_str = today.strftime("%Y-%m-%d")
        sessions  = get_schedule_sessions(cinema, today_str)
        logger.info(
            "[cs_today] Loaded %d sessions from cinema_schedule, upcoming=%d",
            len(sessions), len(_upcoming(sessions, now)),
        )
        text = _format_day_block(
            sessions,
            f"🎬 *{label} — Сьогодні {today.strftime('%d.%m')}*",
            upcoming_only=True,
            now=now,
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=SCHEDULE_KB)

    # ── Tomorrow: read from new collection ────────────────────────────────────
    elif d == "cs_tomorrow":
        tomorrow     = today + timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%Y-%m-%d")
        sessions     = get_schedule_sessions(cinema, tomorrow_str)
        logger.info("[cs_tomorrow] Loaded %d sessions", len(sessions))
        text = _format_day_block(
            sessions,
            f"🎬 *{label} — Завтра {tomorrow.strftime('%d.%m')}*",
            upcoming_only=False,
        )
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=SCHEDULE_KB)

    # ── Nearest session ───────────────────────────────────────────────────────
    elif d == "cs_next":
        today_str = today.strftime("%Y-%m-%d")
        sessions  = get_schedule_sessions(cinema, today_str)
        upcoming  = _upcoming(sessions, now)
        logger.info("[cs_next] today=%d upcoming=%d", len(sessions), len(upcoming))

        if not upcoming:
            await query.edit_message_text(
                "🎬 На сьогодні більше немає запланованих сеансів.",
                reply_markup=SCHEDULE_KB,
            )
            return

        nxt      = upcoming[0]
        start_dt = _parse_iso(nxt.get("startTime", ""))
        hall     = schedule_hall_label(nxt.get("hall", ""))

        if start_dt:
            diff_sec = (start_dt - now).total_seconds()
            mins     = max(0, int(diff_sec // 60))
            countdown = f"⌛ Через {mins} хв" if mins > 0 else "⌛ Починається зараз"
        else:
            countdown = ""

        end_str  = _iso_to_hhmm(nxt.get("endTime", ""))
        end_line = f"\n🏁 Закінчення: {end_str}" if end_str and end_str != "—" else ""
        hall_line = f"\n🏛 {hall}" if hall else ""

        await query.edit_message_text(
            f"🎞 *Найближчий сеанс*\n\n"
            f"🎬 {nxt.get('movie', '—')}\n"
            f"🕐 {_iso_to_hhmm(nxt.get('startTime', ''))}"
            f"{hall_line}{end_line}\n\n"
            f"{countdown}",
            parse_mode="Markdown",
            reply_markup=SCHEDULE_KB,
        )

    # ── Light reminders toggle ────────────────────────────────────────────────
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
