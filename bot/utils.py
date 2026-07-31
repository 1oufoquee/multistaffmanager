import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Single source of truth for the bot's operating timezone.
KYIV_TZ = ZoneInfo("Europe/Kyiv")


def format_timestamp(ts) -> str:
    """Format a Firestore timestamp (or epoch int/float) as 'DD.MM.YYYY HH:MM' in Kyiv time."""
    if ts is None:
        return "—"
    try:
        if hasattr(ts, "strftime"):
            dt = ts
            # Firestore datetimes come as UTC-aware; naive ones are assumed UTC.
            if getattr(dt, "tzinfo", None) is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(KYIV_TZ).strftime("%d.%m.%Y %H:%M")
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.astimezone(KYIV_TZ).strftime("%d.%m.%Y %H:%M")
    except Exception:
        pass
    return str(ts)


def format_seat_id(seat_id: str) -> str:
    """Convert Hall4_Row2_Seat5 → Зала 4 Ряд 2 Місце 5"""
    if not seat_id or seat_id == "—":
        return seat_id
    result = seat_id
    result = re.sub(r'(?i)hall(\d+)', r'Зала \1', result)
    result = re.sub(r'(?i)row(\d+)', r'Ряд \1', result)
    result = re.sub(r'(?i)seat(\d+)', r'Місце \1', result)
    result = result.replace("_", " ").strip()
    return result


def format_items(items) -> str:
    if not items:
        return "—"
    if isinstance(items, list):
        parts = []
        for it in items:
            if isinstance(it, dict):
                name = it.get("name", it.get("title", it.get("productName", "")))
                qty = it.get("quantity", it.get("qty", it.get("count", "")))
                price = it.get("price", "")
                part = name or str(it)
                if qty:
                    part += f" ×{qty}"
                if price:
                    part += f" ({price} грн)"
                parts.append(part)
            else:
                parts.append(str(it))
        return ", ".join(parts) if parts else "—"
    if isinstance(items, str):
        return items
    return str(items)
