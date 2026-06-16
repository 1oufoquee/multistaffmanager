"""
Schedule Import Service
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fetches session data from Multiplex.ua using a headless browser,
parses the HTML, normalises the fields, and writes to Firestore.

Data flow:
  Multiplex.ua → services/schedule_import.py → Firestore
  Telegram Bot reads ONLY from Firestore — never scrapes directly.

To add a new cinema:
  Add an entry to CINEMA_URLS below — no other file needs changing.

To override a URL at runtime:
  Set env var MULTIPLEX_ATMOSFERA_URL=https://... etc.
"""

import logging
import os
from datetime import date, timedelta

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

from bot.firebase_client import save_session, clear_sessions

logger = logging.getLogger(__name__)

# ── Cinema URL map ────────────────────────────────────────────────────────────
# key   = cinema slug in Firestore ( Cinema/{slug}/Sessions )
# value = Multiplex schedule page for that cinema

CINEMA_URLS: dict[str, str] = {
    "atmosfera": os.getenv(
        "MULTIPLEX_ATMOSFERA_URL",
        "https://multiplex.ua/ru/cinema/kyiv/atmosphera",
    ),
    "karavan": os.getenv(
        "MULTIPLEX_KARAVAN_URL",
        "https://multiplex.ua/ru/cinema/kyiv/karavan",
    ),
}

# ── Selector constants — update here if Multiplex changes their markup ────────
SESSION_SEL  = "div.ns"
MOVIE_ATTR   = "data-name"
SID_ATTR     = "data-id"
TIME_SEL     = "p.time"
TAG_SEL      = "p.tag"

_FORMATS = {"IMAX", "4DX", "ScreenX", "VIP", "3D", "2D"}


# ── HTML parsing ──────────────────────────────────────────────────────────────

def _split_tag(tag_text: str) -> tuple[str, str]:
    """Return (format_str, hall_str) from a p.tag element like '3D · Зал 2'."""
    text = tag_text.strip()
    fmt  = "2D"
    hall = text
    for known in _FORMATS:
        if known in text.upper():
            fmt  = known
            hall = text.upper().replace(known, "").strip(" ·—-").strip()
            break
    return fmt, hall or "—"


def _parse_html(html: str, date_str: str) -> list[dict]:
    soup     = BeautifulSoup(html, "html.parser")
    blocks   = soup.select(SESSION_SEL)
    sessions = []

    for block in blocks:
        try:
            movie = (block.get(MOVIE_ATTR) or "").strip()
            sid   = (block.get(SID_ATTR)   or "").strip()
            time_p = block.find("p", class_="time")
            tag_p  = block.find("p", class_="tag")

            if not movie or not time_p:
                continue

            time_str = time_p.get_text(strip=True)
            tag_str  = tag_p.get_text(strip=True) if tag_p else ""
            fmt, hall = _split_tag(tag_str)

            sessions.append({
                "movieTitle":  movie,
                "sessionDate": date_str,
                "sessionTime": time_str,
                "hall":        hall,
                "format":      fmt,
                "sessionId":   sid,
            })
        except Exception as exc:
            logger.warning("Parse error on session block: %s", exc)

    return sessions


# ── Browser fetch ─────────────────────────────────────────────────────────────

async def _fetch_html(url: str) -> str:
    """Render page with headless Chromium; wait for session blocks to appear."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page(
                extra_http_headers={"Accept-Language": "ru-RU,ru;q=0.9"},
            )
            await page.goto(url, wait_until="networkidle", timeout=30_000)
            try:
                await page.wait_for_selector(SESSION_SEL, timeout=15_000)
            except PWTimeout:
                logger.warning("Selector '%s' not found at %s — page may be empty or structure changed", SESSION_SEL, url)
            html = await page.content()
        finally:
            await browser.close()
    return html


# ── Public API ────────────────────────────────────────────────────────────────

async def fetch_sessions_for_date(cinema: str, target: date) -> list[dict]:
    """
    Scrape Multiplex for *cinema* on *target* date.
    Returns a list of normalised session dicts.
    """
    base = CINEMA_URLS.get(cinema)
    if not base:
        logger.error("No Multiplex URL configured for cinema '%s'", cinema)
        return []

    date_str = target.strftime("%Y-%m-%d")
    url      = f"{base}?date={date_str}"

    logger.info("[%s] Fetching %s → %s", cinema, date_str, url)
    try:
        html = await _fetch_html(url)
    except Exception as exc:
        logger.error("[%s] Browser fetch failed: %s", cinema, exc, exc_info=True)
        return []

    sessions = _parse_html(html, date_str)
    logger.info("[%s] Parsed %d session(s) for %s", cinema, len(sessions), date_str)
    return sessions


async def import_cinema(cinema: str, days_ahead: int = 2) -> int:
    """
    Full schedule refresh for *cinema*:
      1. Delete all existing sessions from Firestore.
      2. Fetch today + days_ahead future days from Multiplex.
      3. Save every parsed session to Firestore.

    Returns total number of sessions written.
    """
    today = date.today()

    # Step 1 — clear stale data
    try:
        deleted = clear_sessions(cinema)
        logger.info("[%s] Cleared %d stale session(s)", cinema, deleted)
    except Exception as exc:
        logger.error("[%s] clear_sessions failed: %s", cinema, exc)

    # Steps 2 + 3 — fetch and persist each day
    total_written = 0
    for offset in range(days_ahead + 1):
        target   = today + timedelta(days=offset)
        sessions = await fetch_sessions_for_date(cinema, target)

        for sess in sessions:
            try:
                save_session(cinema, sess)
                total_written += 1
            except Exception as exc:
                logger.error("[%s] Firestore write failed: %s  data=%s", cinema, exc, sess)

    logger.info("[%s] Import complete — %d session(s) written", cinema, total_written)
    return total_written
