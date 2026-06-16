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
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta

import requests
from bs4 import BeautifulSoup

from bot.firebase_client import save_session, clear_stale_sessions

logger = logging.getLogger(__name__)

# ── Cinema URL map ────────────────────────────────────────────────────────────

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

_MULTIPLEX_BASE = "https://multiplex.ua"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "uk-UA,uk;q=0.9,ru;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_KNOWN_FORMATS = {"IMAX", "4DX", "3D", "SCREENX"}

# Fallback duration (minutes) used when movie page cannot be fetched
_DEFAULT_DURATION_MINS = 100


# ── Time helpers ──────────────────────────────────────────────────────────────

def _add_minutes(time_str: str, mins: int) -> str:
    """'14:30' + 95  →  '16:05'  (wraps past midnight)."""
    h, m = map(int, time_str.split(":"))
    dt = datetime(2000, 1, 1, h, m) + timedelta(minutes=mins)
    return dt.strftime("%H:%M")


# ── Multiplex HTML helpers ────────────────────────────────────────────────────

def _anchor_to_date(anchor: str) -> str:
    if not anchor or len(anchor) != 8 or not anchor.isdigit():
        return ""
    try:
        return datetime.strptime(anchor, "%d%m%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _parse_data_attributes(raw: str) -> list[dict]:
    if not raw:
        return []
    cleaned = re.sub(r",\s*\]", "]", raw)
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        return []


def _extract_format(tag_text: str, attrs: list[dict] | None = None) -> str:
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


def _extract_hall(attrs: list[dict]) -> tuple[str, int | None]:
    """Return (shortName, hallId) from data-attributes, or ('', None)."""
    for a in attrs:
        if a.get("Typ") == "hall":
            return a.get("ShortName", ""), a.get("Id")
    return "", None


# ── Duration fetching ─────────────────────────────────────────────────────────

def _parse_duration_mins(html: str) -> int | None:
    """
    Extract movie runtime from Multiplex movie page HTML.
    The page embeds JSON with  "duration": "H:MM"  (e.g. "1:35" = 95 min).
    """
    m = re.search(r'"duration"\s*:\s*"(\d+):(\d{2})"', html)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    return None


def _fetch_duration_for_href(movie_href: str) -> int | None:
    """Fetch one Multiplex movie page and return runtime in minutes."""
    url = f"{_MULTIPLEX_BASE}{movie_href}"
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=10)
        if resp.status_code == 200:
            mins = _parse_duration_mins(resp.text)
            if mins:
                logger.debug("Duration %s → %d min", movie_href, mins)
                return mins
    except Exception as exc:
        logger.debug("Duration fetch failed for %s: %s", movie_href, exc)
    return None


def _enrich_with_durations(sessions: list[dict]) -> None:
    """
    Fetch movie pages for each unique movieHref, extract duration,
    and add endTime + durationMins to each session (in-place).

    Caches by href so each movie page is fetched only once.
    Sequential with a small delay to be polite to Multiplex servers.
    """
    hrefs: set[str] = {s["movieHref"] for s in sessions if s.get("movieHref")}
    logger.info("Fetching durations for %d unique movie(s)...", len(hrefs))

    cache: dict[str, int | None] = {}
    for href in hrefs:
        cache[href] = _fetch_duration_for_href(href)
        time.sleep(0.25)   # gentle rate limiting

    missing = sum(1 for v in cache.values() if v is None)
    if missing:
        logger.info(
            "Duration fetch: %d/%d successful, %d fallback to %d min",
            len(hrefs) - missing, len(hrefs), missing, _DEFAULT_DURATION_MINS,
        )

    for s in sessions:
        href  = s.get("movieHref", "")
        mins  = cache.get(href) or _DEFAULT_DURATION_MINS
        start = s.get("sessionTime", "")
        if start:
            s["durationMins"] = mins
            s["endTime"]      = _add_minutes(start, mins)
        else:
            s["durationMins"] = None
            s["endTime"]      = None


# ── Fetch & parse ─────────────────────────────────────────────────────────────

def _fetch_html(url: str) -> str:
    logger.info("HTTP GET %s", url)
    resp = requests.get(url, headers=_HEADERS, timeout=25)
    logger.info("Response: %d  len=%d  content-type=%s",
                resp.status_code, len(resp.text),
                resp.headers.get("Content-Type", "")[:60])
    resp.raise_for_status()
    return resp.text


def _parse_html(html: str, cinema: str) -> list[dict]:
    soup     = BeautifulSoup(html, "lxml")
    elements = soup.select("a.ns")
    logger.info("[%s] Found %d a.ns elements in HTML", cinema, len(elements))

    sessions = []
    skipped  = 0

    for el in elements:
        try:
            movie      = (el.get("data-name") or "").strip()
            sid        = (el.get("data-session-id") or "").strip()
            anchor     = (el.get("data-anchor") or "").strip()
            movie_href = (el.get("data-moviehref") or "").strip()

            time_el = el.select_one("p.time span")
            tag_el  = el.select_one("p.tag")

            if not movie or not time_el:
                skipped += 1
                continue

            time_str = time_el.get_text(strip=True)
            tag_str  = tag_el.get_text(strip=True) if tag_el else ""
            date_str = _anchor_to_date(anchor)

            if not date_str:
                logger.debug("Skipping session %s — bad anchor '%s'", sid, anchor)
                skipped += 1
                continue

            raw_attrs    = el.get("data-attributes", "")
            parsed_attrs = _parse_data_attributes(raw_attrs)
            hall, hall_id = _extract_hall(parsed_attrs)
            fmt           = _extract_format(tag_str, parsed_attrs)

            sessions.append({
                "sessionId":   sid,
                "movieTitle":  movie,
                "movieHref":   movie_href,
                "sessionDate": date_str,
                "sessionTime": time_str,
                "hall":        hall,
                "hallId":      hall_id,
                "format":      fmt,
                "tags":        tag_str,
                "cinema":      cinema,
                # endTime and durationMins filled in by _enrich_with_durations()
            })
        except Exception as exc:
            logger.warning("[%s] Parse error on session element: %s", cinema, exc)
            skipped += 1

    logger.info("[%s] Parsed %d session(s), skipped %d", cinema, len(sessions), skipped)
    for s in sessions[:5]:
        logger.info("  SAMPLE → %s | %s %s | hall=%s fmt=%s",
                    s["sessionDate"], s["sessionTime"], s["movieTitle"],
                    s["hall"], s["format"])
    return sessions


# ── Public API ────────────────────────────────────────────────────────────────

async def fetch_sessions_for_cinema(cinema: str) -> list[dict]:
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


async def fetch_sessions_for_date(cinema: str, target) -> list[dict]:
    """Deprecated — use fetch_sessions_for_cinema() instead."""
    all_sessions = await fetch_sessions_for_cinema(cinema)
    date_str = target.strftime("%Y-%m-%d") if hasattr(target, "strftime") else str(target)
    return [s for s in all_sessions if s.get("sessionDate") == date_str]


async def import_cinema(cinema: str) -> int:
    """
    Full schedule refresh for *cinema*:
      1. Fetch all sessions from Multiplex (one HTTP request).
      2. Enrich with end times (one HTTP request per unique movie).
      3. Delete sessions no longer on Multiplex (stale).
      4. Upsert all sessions to Firestore (merge=True preserves notification flags).

    Returns total number of sessions upserted.
    """
    logger.info("[%s] Starting import...", cinema)

    # Step 1 — fetch sessions from Multiplex
    sessions = await fetch_sessions_for_cinema(cinema)
    logger.info("[%s] Total sessions from Multiplex: %d", cinema, len(sessions))

    if not sessions:
        logger.warning("[%s] No sessions returned — skipping Firestore update", cinema)
        return 0

    # Step 2 — enrich with end times
    _enrich_with_durations(sessions)

    # Step 3 — delete stale sessions (not in new data)
    new_ids = {s["sessionId"] for s in sessions if s.get("sessionId")}
    try:
        deleted = clear_stale_sessions(cinema, keep_ids=new_ids)
        if deleted:
            logger.info("[%s] Deleted %d stale session(s)", cinema, deleted)
    except Exception as exc:
        logger.error("[%s] clear_stale_sessions failed: %s", cinema, exc)

    # Step 4 — upsert to Firestore (preserves startNotifSent / endNotifSent flags)
    total_written = 0
    for sess in sessions:
        try:
            save_session(cinema, sess)
            total_written += 1
        except Exception as exc:
            logger.error("[%s] Firestore write failed: %s  data=%s", cinema, exc, sess)

    logger.info("[%s] Import complete — %d session(s) written to Firestore", cinema, total_written)
    return total_written
