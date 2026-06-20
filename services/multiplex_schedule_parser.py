"""
Multiplex Schedule Parser
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Generates the full Atmosfera cinema schedule and saves it to:

  Cinema/{cinema}/cinema_schedule/YYYY-MM-DD/sessions/{sessionId}

Document format:
  {
    "sessionId":   "98102",
    "movie":       "Minecraft",
    "hall":        "Зал №1",
    "startTime":   "2026-06-21T10:00:00+03:00",
    "endTime":     "2026-06-21T11:42:00+03:00",
    "generatedAt": <SERVER_TIMESTAMP>
  }

Data flow:
  Cinema listing page  →  session IDs  (regex: session/\\d+)
  Session page         →  SessionShowTime / SessionShowTimeEnd /
                          SessionScreenName / movie title
  → Firestore cinema_schedule collection

Reuses HTTP helpers from services.schedule_import (headers, fetch).
"""

import logging
import re
import time

import requests
from bs4 import BeautifulSoup

from services.schedule_import import _HEADERS, _MULTIPLEX_BASE

logger = logging.getLogger(__name__)

_CINEMA_URLS: dict[str, str] = {
    "atmosfera": "https://multiplex.ua/ru/cinema/kyiv/atmosphera",
    "karavan":   "https://multiplex.ua/ru/cinema/kyiv/karavan",
}

# Seconds to sleep between individual session-page requests
_REQUEST_DELAY = 0.3


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _fetch(url: str, timeout: int = 20) -> str | None:
    """GET url, return text or None on error."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        if resp.status_code == 200:
            return resp.text
        logger.debug("HTTP %d for %s", resp.status_code, url)
    except Exception as exc:
        logger.debug("Fetch error %s: %s", url, exc)
    return None


def _extract_json_field(html: str, field: str) -> str | None:
    """Extract first occurrence of  "FIELD":"value"  from raw HTML."""
    m = re.search(rf'"{re.escape(field)}"\s*:\s*"([^"]+)"', html)
    return m.group(1) if m else None


def _extract_movie_title(html: str) -> str:
    """
    Try CSS selectors first (.movie-name.*), then JSON "name" field.
    Returns empty string when nothing is found.
    """
    soup = BeautifulSoup(html, "lxml")
    for selector in (
        ".movie-name.mobile-hidden",
        ".movie-name.desktop-hidden",
        ".movie-name",
        "h1.title",
        "h1",
    ):
        el = soup.select_one(selector)
        if el:
            text = el.get_text(strip=True)
            if text:
                return text

    m = re.search(r'"name"\s*:\s*"([^"]+)"', html)
    return m.group(1) if m else ""


def _date_from_iso(iso: str) -> str | None:
    """'2026-06-21T10:00:00+03:00'  →  '2026-06-21'"""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", iso or "")
    return m.group(1) if m else None


# ── Step 1: collect session IDs ───────────────────────────────────────────────

def _get_session_ids(cinema: str) -> list[str]:
    """
    Fetch the cinema listing page and extract all unique session IDs
    using the pattern  session/NNNNN  already present in the HTML.
    """
    url = _CINEMA_URLS.get(cinema)
    if not url:
        logger.error("[Schedule] No URL configured for cinema '%s'", cinema)
        return []

    logger.info("[Schedule] Fetching cinema page: %s", url)
    html = _fetch(url, timeout=25)
    if not html:
        logger.error("[Schedule] Failed to fetch cinema listing page")
        return []

    ids = re.findall(r"session/(\d+)", html)
    unique = list(dict.fromkeys(ids))   # dedupe, preserve order
    logger.info("[Schedule] Found %d unique session IDs", len(unique))
    return unique


# ── Step 2: parse one session page ───────────────────────────────────────────

def _parse_session_page(session_id: str) -> dict | None:
    """
    Fetch https://multiplex.ua/ru/session/{id} and extract:
      SessionShowTime     → startTime  (ISO 8601 with tz)
      SessionShowTimeEnd  → endTime
      SessionScreenName   → hall       (e.g. "Зал №1")
      movie title         → movie
    Returns None if the page is unreachable or missing required fields.
    """
    url  = f"{_MULTIPLEX_BASE}/ru/session/{session_id}"
    html = _fetch(url)
    if not html:
        logger.warning("[Schedule] Session %s: page unavailable", session_id)
        return None

    start = _extract_json_field(html, "SessionShowTime")
    end   = _extract_json_field(html, "SessionShowTimeEnd")
    hall  = _extract_json_field(html, "SessionScreenName")
    movie = _extract_movie_title(html)

    if not start:
        logger.debug("[Schedule] Session %s: SessionShowTime not found", session_id)
        return None

    return {
        "sessionId": session_id,
        "movie":     movie or "—",
        "hall":      hall  or "—",
        "startTime": start,
        "endTime":   end or "",
    }


# ── Public entry point ────────────────────────────────────────────────────────

async def generate_daily_schedule(cinema: str = "atmosfera") -> int:
    """
    Full pipeline:
      1. Collect session IDs from cinema listing page.
      2. Fetch each session page, extract structured data.
      3. Group sessions by date.
      4. For each date: clear old docs, write fresh ones.

    Returns total sessions written to Firestore.
    Errors on individual sessions are logged and skipped — never crash.
    """
    from bot.firebase_client import save_schedule_session, clear_schedule_sessions

    # Step 1 — collect IDs
    session_ids = _get_session_ids(cinema)
    if not session_ids:
        logger.warning("[Schedule] No session IDs found — aborting")
        return 0

    # Step 2 — parse session pages
    sessions_by_date: dict[str, list[dict]] = {}

    for sid in session_ids:
        logger.info("[Schedule] Processing session %s", sid)
        try:
            data = _parse_session_page(sid)
            if not data:
                logger.warning("[Schedule] Session %s: skipped (no data)", sid)
                time.sleep(_REQUEST_DELAY)
                continue

            date_str = _date_from_iso(data["startTime"])
            if not date_str:
                logger.warning(
                    "[Schedule] Session %s: cannot extract date from '%s'",
                    sid, data["startTime"],
                )
                time.sleep(_REQUEST_DELAY)
                continue

            sessions_by_date.setdefault(date_str, []).append(data)

        except Exception as exc:
            logger.error("[Schedule] Session %s: unexpected error: %s", sid, exc)

        time.sleep(_REQUEST_DELAY)

    if not sessions_by_date:
        logger.warning("[Schedule] No sessions parsed — Firestore not updated")
        return 0

    # Step 3 — rebuild Firestore for each date
    total_saved = 0
    for date_str in sorted(sessions_by_date):
        try:
            cleared = clear_schedule_sessions(cinema, date_str)
            logger.info("[Schedule] Cleared %d old doc(s) for %s/%s", cleared, cinema, date_str)
        except Exception as exc:
            logger.error("[Schedule] clear_schedule_sessions failed (%s/%s): %s",
                         cinema, date_str, exc)

        for sess in sessions_by_date[date_str]:
            try:
                save_schedule_session(cinema, date_str, sess)
                logger.info("[Schedule] Saved session %s (%s  %s)",
                            sess["sessionId"], date_str, sess.get("movie", ""))
                total_saved += 1
            except Exception as exc:
                logger.error("[Schedule] Save failed for session %s: %s",
                             sess.get("sessionId"), exc)

    logger.info(
        "[Schedule] Schedule generation complete — %d session(s) across %d date(s)",
        total_saved, len(sessions_by_date),
    )
    return total_saved
