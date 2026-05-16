from datetime import datetime, timezone


def format_timestamp(ts) -> str:
    if ts is None:
        return "—"
    try:
        if hasattr(ts, "timestamp"):
            dt = ts
            if hasattr(dt, "tzinfo") and dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.strftime("%d.%m.%Y %H:%M")
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return dt.strftime("%d.%m.%Y %H:%M")
    except Exception:
        pass
    return str(ts)
