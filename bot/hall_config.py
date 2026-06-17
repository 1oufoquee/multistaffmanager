"""
Hall name configuration
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Multiplex only exposes hall *type* names (VIP, LUX) — not numbered hall names.
This module maps those type names to whatever the cinema calls them internally
(e.g. "Зал 4", "Зал 5") via environment variables so no code changes are needed
when the mapping changes.

Environment variables
─────────────────────
HALL_NAMES
    Comma-separated "SHORTNAME:Display name" pairs.
    Example: HALL_NAMES=VIP:Зал 4,LUX:Зал 5
    Default (when not set): VIP→"VIP зал", LUX→"LUX зал"

NOTIFY_HALL_NAMES
    Comma-separated Multiplex ShortNames that should trigger light alerts.
    Example: NOTIFY_HALL_NAMES=VIP,LUX
    Default (when not set): all halls present in HALL_NAMES / the built-in default

END_NOTIFY_MINUTES
    Minutes before session end to send the "lights on soon" reminder.
    Default: 7
"""

import logging
import os

logger = logging.getLogger(__name__)

# ── Built-in defaults ─────────────────────────────────────────────────────────

_DEFAULT_HALL_DISPLAY: dict[str, str] = {
    "VIP":      "VIP зал",
    "LUX":      "LUX зал",
    "STANDART": "Стандарт зал",
}


# ── Loaders ───────────────────────────────────────────────────────────────────

def _load_hall_display() -> dict[str, str]:
    """
    Parse HALL_NAMES env var into a {SHORTNAME_UPPER: display_name} mapping.
    Falls back to built-in defaults when the var is absent or empty.
    """
    raw = os.getenv("HALL_NAMES", "").strip()
    if not raw:
        return dict(_DEFAULT_HALL_DISPLAY)

    result: dict[str, str] = {}
    for part in raw.split(","):
        key, sep, val = part.partition(":")
        if sep and key.strip() and val.strip():
            result[key.strip().upper()] = val.strip()

    if not result:
        logger.warning("HALL_NAMES is set but could not be parsed ('%s'); using defaults", raw)
        return dict(_DEFAULT_HALL_DISPLAY)

    logger.info("Hall name mapping loaded from env: %s", result)
    return result


def _load_notify_halls(hall_display: dict[str, str]) -> set[str]:
    """
    Return the set of hall ShortNames (uppercase) that trigger light alerts.
    Falls back to the full set of keys in hall_display.
    """
    raw = os.getenv("NOTIFY_HALL_NAMES", "").strip()
    if raw:
        return {h.strip().upper() for h in raw.split(",") if h.strip()}
    return set(hall_display.keys())


def _load_end_notify_minutes() -> int:
    try:
        return int(os.getenv("END_NOTIFY_MINUTES", "7"))
    except (ValueError, TypeError):
        return 7


# ── Module-level singletons (loaded once at import) ───────────────────────────

HALL_DISPLAY:       dict[str, str] = _load_hall_display()
NOTIFY_HALLS:       set[str]       = _load_notify_halls(HALL_DISPLAY)
END_NOTIFY_MINUTES: int            = _load_end_notify_minutes()


# ── Public helper ─────────────────────────────────────────────────────────────

def hall_label(hall: str) -> str:
    """
    Return the display name for a Multiplex hall ShortName.

    Examples (with default mapping):
        hall_label("VIP")  → "VIP зал"
        hall_label("LUX")  → "LUX зал"
        hall_label("")     → ""

    With HALL_NAMES=VIP:Зал 4,LUX:Зал 5:
        hall_label("VIP")  → "Зал 4"
        hall_label("LUX")  → "Зал 5"
    """
    if not hall:
        return ""
    return HALL_DISPLAY.get(hall.strip().upper(), hall)
