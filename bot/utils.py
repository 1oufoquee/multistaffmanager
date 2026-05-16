from datetime import datetime, timezone


def format_timestamp(ts) -> str:
    if ts is None:
        return "—"
    try:
        if hasattr(ts, "strftime"):
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
