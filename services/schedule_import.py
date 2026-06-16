"""
Schedule Import Service
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fetches session data from Multiplex.ua using plain HTTP requests
(no browser / Playwright required — sessions are in the static HTML).

Data flow:
  Multiplex.ua  →  services/schedule_import.py  →  Firestore
  Telegram Bot reads ONLY from Firestore — never scrapes directly.

To add a new cinema:
  Add an entry to CINEMA_URLS below — no other file needs changing.

To override a URL at runtime:
  Set env var MULTIPLEX_ATMOSFERA_URL=https://... etc.
"""

import json
import logging
import os
import re
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from bot.firebase_client import save_session, clear_sessions

logger = logging.getLogger(__name__)

# ── Cinema URL map ────────────────────────────────────────────────────────────
# key   = cinema slug used in Firestore ( Cinema/{slug}/Sessions )
# value = Multiplex schedule page for that cinema (no date param needed —
#         the page contains all upcoming days in one HTML load)

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

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Known video formats (checked against p.tag text, uppercase)
_KNOWN_FORMATS = {"IMAX", "4DX", "3D", "SCREENX"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _anchor_to_date(anchor: str) -> str:
    """
    Convert Multiplex date anchor DDMMYYYY → ISO string YYYY-MM-DD.
    Returns empty string on error.
    """
    if not anchor or len(anchor) != 8 or not anchor.isdigit():
        return ""
    try:
        return datetime.strptime(anchor, "%d%m%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _parse_data_attributes(raw: str) -> list[dict]:
    """
    Parse the (slightly broken) JSON in data-attributes.
    Multiplex sometimes trails the last item with ',]' — we fix that.
    """
    if not raw:
        return []
    cleaned = re.sub(r",\s*\]", "]", raw)   # remove trailing comma before ]
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return []


def _extract_format(tag_text: str, attrs: list[dict] | None = None) -> str:
    """
    Return detected video format. Priority:
      1. data-attributes Typ=="format"  (most reliable — e.g. "3D")
      2. p.tag text scan                (fallback for IMAX / 4DX / ScreenX)
    Defaults to "2D".
    """
    for a in (attrs or []):
        if a.get("Typ") == "format":
            sn = a.get("ShortName", "").strip()
            if sn:
                return sn
    upper = tag_text.upper()
    for fmt in _KNOWN_FORMATS:
        if fmt in upper:
            return fmt
    return "2D"


def _extract_hall(attrs: list[dict]) -> str:
    """Return hall short-name from parsed data-attributes, or ''."""
    for a in attrs:
        if a.get("Typ") == "hall":
            return a.get("ShortName", "")
    return ""


# ── Fetch & parse ─────────────────────────────────────────────────────────────

def _fetch_html(url: str) -> str:
    """Fetch the Multiplex cinema page (plain HTTP — no headless browser needed)."""
    logger.info("HTTP GET %s", url)
    resp = requests.get(url, headers=_HEADERS, timeout=25)
    logger.info("Response: %d  len=%d  content-type=%s",
                resp.status_code, len(resp.text),
                resp.headers.get("Content-Type", "")[:60])
    resp.raise_for_status()
    return resp.text


def _parse_html(html: str, cinema: str) -> list[dict]:
    """
    Parse all session blocks from the Multiplex cinema page HTML.

    Key selectors (verified 2026-06-16):
      • Sessions are  <a class="ns ...">  elements (NOT div.ns)
      • data-session-id  = numeric session ID
      • data-name        = movie title
      • data-anchor      = date in DDMMYYYY format
      • data-attributes  = JSON array with hall/lang/format info
      • p.time > span    = time string e.g. "13:00"
      • p.tag            = space-separated tags e.g. "SDH VIP" or "IMAX 3D"
    """
    soup     = BeautifulSoup(html, "lxml")
    elements = soup.select("a.ns")
    logger.info("[%s] Found %d a.ns elements in HTML", cinema, len(elements))

    sessions = []
    skipped  = 0

    for el in elements:
        try:
            movie  = (el.get("data-name") or "").strip()
            sid    = (el.get("data-session-id") or "").strip()
            anchor = (el.get("data-anchor") or "").strip()

            time_el = el.select_one("p.time span")
            tag_el  = el.select_one("p.tag")

            if not movie or not time_el:
                skipped += 1
                continue

            time_str = time_el.get_text(strip=True)
            tag_str  = tag_el.get_text(strip=True) if tag_el else ""
            date_str = _anchor_to_date(anchor)

            raw_attrs = el.get("data-attributes", "")
            parsed_attrs = _parse_data_attributes(raw_attrs)
            hall   = _extract_hall(parsed_attrs)
            fmt    = _extract_format(tag_str, parsed_attrs)

            if not date_str:
                logger.debug("Skipping session %s — bad anchor '%s'", sid, anchor)
                skipped += 1
                continue

            sessions.append({
                "sessionId":   sid,
                "movieTitle":  movie,
                "sessionDate": date_str,
                "sessionTime": time_str,
                "hall":        hall,
                "format":      fmt,
                "tags":        tag_str,
                "cinema":      cinema,
            })
        except Exception as exc:
            logger.warning("[%s] Parse error on session element: %s", cinema, exc)
            skipped += 1

    logger.info("[%s] Parsed %d session(s), skipped %d", cinema, len(sessions), skipped)

    # Print first 5 for quick verification
    for s in sessions[:5]:
        logger.info("  SAMPLE → %s | %s %s | hall=%s fmt=%s",
                    s["sessionDate"], s["sessionTime"], s["movieTitle"],
                    s["hall"], s["format"])

    return sessions


# ── Public API ────────────────────────────────────────────────────────────────

async def fetch_sessions_for_cinema(cinema: str) -> list[dict]:
    """
    Fetch and parse all upcoming sessions for *cinema* from Multiplex.
    The page contains today + several future days — all are returned.
    """
    base = CINEMA_URLS.get(cinema)
    if not base:
        logger.error("No Multiplex URL configured for cinema '%s'", cinema)
        return []

    try:
        html = _fetch_html(base)
    except Exception as exc:
        logger.error("[%s] HTTP fetch failed: %s", cinema, exc, exc_info=True)
        return []

    return _parse_html(html, cinema)


# kept for backward compatibility with any code that still calls this
async def fetch_sessions_for_date(cinema: str, target) -> list[dict]:
    """Deprecated — use fetch_sessions_for_cinema() instead."""
    all_sessions = await fetch_sessions_for_cinema(cinema)
    date_str = target.strftime("%Y-%m-%d") if hasattr(target, "strftime") else str(target)
    return [s for s in all_sessions if s.get("sessionDate") == date_str]


async def import_cinema(cinema: str, days_ahead: int = 2) -> int:
    """
    Full schedule refresh for *cinema*:
      1. Delete all existing sessions from Firestore.
      2. Fetch all upcoming sessions (single HTTP request).
      3. Save every parsed session to Firestore.

    Returns total number of sessions written.
    """
    logger.info("[%s] Starting import...", cinema)

    # Step 1 — clear stale data
    try:
        deleted = clear_sessions(cinema)
        logger.info("[%s] Cleared %d stale session(s)", cinema, deleted)
    except Exception as exc:
        logger.error("[%s] clear_sessions failed: %s", cinema, exc)

    # Step 2 — fetch all sessions in one request
    sessions = await fetch_sessions_for_cinema(cinema)
    logger.info("[%s] Total sessions fetched: %d", cinema, len(sessions))

    # Step 3 — persist to Firestore
    total_written = 0
    for sess in sessions:
        try:
            save_session(cinema, sess)
            total_written += 1
        except Exception as exc:
            logger.error("[%s] Firestore write failed: %s  data=%s", cinema, exc, sess)

    logger.info("[%s] Import complete — %d session(s) written to Firestore", cinema, total_written)
    return total_written
