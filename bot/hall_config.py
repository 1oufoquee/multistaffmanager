"""
Hall configuration
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Two independent hall systems are configured here:

OLD SYSTEM  (Cinema/{cinema}/Sessions)
  hall field = Multiplex type shortname: "VIP", "LUX", "STANDART"
  Used by: jobs/light_notifications (now migrated to new system)
  → retained only for backward compatibility

NEW SYSTEM  (Cinema/{cinema}/cinema_schedule/{date}/sessions)
  hall field = Multiplex SessionScreenName: "Зал №2", "Зал №3", …
  Used by: cinema_schedule handler, light_notifications job

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Environment variables — NEW system
──────────────────────────────────
SCHEDULE_HALL_MAP
    Maps Multiplex SessionScreenName to a display label shown in the bot.
    Format: "SessionName:DisplayLabel" pairs, comma-separated.
    Example: SCHEDULE_HALL_MAP=Зал №2:Зал 4 (VIP),Зал №3:Зал 5 (LUX),Зал №1:Зал 3
    Default: SessionScreenName is displayed as-is.

NOTIFY_SCHEDULE_HALLS
    Which Multiplex SessionScreenNames trigger light notifications.
    Only halls listed here will send "turn off / turn on" reminders.
    Example: NOTIFY_SCHEDULE_HALLS=Зал №2,Зал №3    (Hall 4 and Hall 5 only)
    Default (not set): ALL halls send notifications.

END_NOTIFY_MINUTES
    Minutes before session end to send the "lights on soon" reminder.
    Default: 7

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Environment variables — OLD system (legacy, kept for reference)
────────────────────────────────────────────────────────────────
HALL_NAMES         Comma-separated "SHORTNAME:Display name" pairs.
NOTIFY_HALL_NAMES  Comma-separated Multiplex ShortNames for alerts.
"""

import logging
import os

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  OLD SYSTEM — legacy type-shortname mapping
# ══════════════════════════════════════════════════════════════════════════════

_DEFAULT_HALL_DISPLAY: dict[str, str] = {
    "VIP":      "VIP зал",
    "LUX":      "LUX зал",
    "STANDART": "Стандарт",
}


def _load_hall_display() -> dict[str, str]:
    raw = os.getenv("HALL_NAMES", "").strip()
    if not raw:
        return dict(_DEFAULT_HALL_DISPLAY)
    result: dict[str, str] = {}
    for part in raw.split(","):
        key, sep, val = part.partition(":")
        if sep and key.strip() and val.strip():
            result[key.strip().upper()] = val.strip()
    if not result:
        logger.warning("HALL_NAMES could not be parsed ('%s'); using defaults", raw)
        return dict(_DEFAULT_HALL_DISPLAY)
    logger.info("Hall name mapping (old system) loaded: %s", result)
    return result


def _load_notify_halls(hall_display: dict[str, str]) -> set[str]:
    raw = os.getenv("NOTIFY_HALL_NAMES", "").strip()
    if raw:
        return {h.strip().upper() for h in raw.split(",") if h.strip()}
    return set(hall_display.keys())


HALL_DISPLAY: dict[str, str] = _load_hall_display()
NOTIFY_HALLS: set[str] = _load_notify_halls(HALL_DISPLAY)


def hall_label(hall: str) -> str:
    """Display name for a Multiplex type shortname (old system)."""
    if not hall:
        return ""
    return HALL_DISPLAY.get(hall.strip().upper(), hall)


# ══════════════════════════════════════════════════════════════════════════════
#  NEW SYSTEM — SessionScreenName mapping
# ══════════════════════════════════════════════════════════════════════════════

def _load_schedule_hall_map() -> dict[str, str]:
    """
    Parse SCHEDULE_HALL_MAP env var.
    Returns {SessionScreenName: display_label} dict.
    Empty dict when the var is absent — raw SessionScreenName is used as-is.
    """
    raw = os.getenv("SCHEDULE_HALL_MAP", "").strip()
    if not raw:
        return {}
    result: dict[str, str] = {}
    for part in raw.split(","):
        key, sep, val = part.partition(":")
        if sep and key.strip() and val.strip():
            result[key.strip()] = val.strip()
    if not result:
        logger.warning("SCHEDULE_HALL_MAP could not be parsed ('%s'); using raw names", raw)
        return {}
    logger.info("Schedule hall map loaded: %s", result)
    return result


def _load_notify_schedule_halls() -> set[str]:
    """
    Parse NOTIFY_SCHEDULE_HALLS env var.
    Returns a set of SessionScreenName strings that should trigger light alerts.
    Empty set = notify ALL halls (no filter).
    """
    raw = os.getenv("NOTIFY_SCHEDULE_HALLS", "").strip()
    if raw:
        halls = {h.strip() for h in raw.split(",") if h.strip()}
        logger.info("Light notification halls (new system): %s", halls)
        return halls
    logger.info(
        "NOTIFY_SCHEDULE_HALLS not set — light notifications will fire for ALL halls. "
        "Set NOTIFY_SCHEDULE_HALLS=Зал №2,Зал №3 to restrict to specific halls."
    )
    return set()


def _load_end_notify_minutes() -> int:
    try:
        return int(os.getenv("END_NOTIFY_MINUTES", "7"))
    except (ValueError, TypeError):
        return 7


# Module-level singletons (loaded once at import)
SCHEDULE_HALL_MAP:       dict[str, str] = _load_schedule_hall_map()
NOTIFY_SCHEDULE_HALLS:   set[str]       = _load_notify_schedule_halls()
END_NOTIFY_MINUTES:      int            = _load_end_notify_minutes()


def schedule_hall_label(hall: str) -> str:
    """
    Display label for a Multiplex SessionScreenName (new schedule system).

    Examples (SCHEDULE_HALL_MAP=Зал №2:Зал 4 (VIP),Зал №3:Зал 5 (LUX)):
        schedule_hall_label("Зал №2")  → "Зал 4 (VIP)"
        schedule_hall_label("Зал №3")  → "Зал 5 (LUX)"
        schedule_hall_label("Зал №1")  → "Зал №1"   (passthrough, unmapped)

    When SCHEDULE_HALL_MAP is not set, returns hall as-is.
    """
    if not hall:
        return ""
    return SCHEDULE_HALL_MAP.get(hall.strip(), hall.strip())


def should_notify_schedule_hall(hall: str) -> bool:
    """
    True if this SessionScreenName should trigger light notifications.

    When NOTIFY_SCHEDULE_HALLS is empty (not configured) → notifies ALL halls.
    When set → only halls in the set trigger notifications.

    Configure NOTIFY_SCHEDULE_HALLS=Зал №2,Зал №3 to restrict to Hall 4 and Hall 5.
    """
    if not NOTIFY_SCHEDULE_HALLS:
        return True
    return hall.strip() in NOTIFY_SCHEDULE_HALLS
