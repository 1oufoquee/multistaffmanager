import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime

_app = None
_db = None


def get_db():
    global _app, _db
    if _db is None:
        service_account_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
        if not service_account_json:
            raise ValueError("FIREBASE_SERVICE_ACCOUNT_JSON env var not set")
        service_account_info = json.loads(service_account_json)
        cred = credentials.Certificate(service_account_info)
        if not firebase_admin._apps:
            _app = firebase_admin.initialize_app(cred)
        else:
            _app = firebase_admin.get_app()
        _db = firestore.client()
    return _db


# ── Collection refs ──────────────────────────────────────────────────────────

def _users_ref(db):
    return db.collection("Cinema").document("atmosfera").collection("Users")

def _orders_ref(db):
    return db.collection("Cinema").document("atmosfera").collection("Orders")

def _recipes_ref(db):
    return db.collection("Cinema").document("atmosfera").collection("Recipes")

def _writeoffs_ref(db):
    return db.collection("Cinema").document("atmosfera").collection("Writeoffs")

def _menu_ref(db):
    return db.collection("Cinema").document("atmosfera").collection("Menu")


# ── Auth ─────────────────────────────────────────────────────────────────────

def _find_user_doc(telegram_id: int):
    """Returns (doc_snapshot, dict) or (None, None). Always fetches fresh."""
    db = get_db()
    for doc in _users_ref(db).get():
        data = doc.to_dict() or {}
        try:
            if int(data.get("telegramId") or 0) == int(telegram_id):
                return doc, data
        except (TypeError, ValueError):
            continue
    return None, None


def is_authorized_user(telegram_id: int) -> bool:
    doc, data = _find_user_doc(telegram_id)
    if doc is None:
        return False
    return not data.get("isBlocked", False)


def get_user_info(telegram_id: int) -> dict | None:
    doc, data = _find_user_doc(telegram_id)
    if doc is None:
        return None
    data["_id"] = doc.id
    return data


# ── Orders ───────────────────────────────────────────────────────────────────

def get_orders() -> list[dict]:
    db = get_db()
    results = []
    for doc in _orders_ref(db).get():
        data = doc.to_dict()
        if data.get("status") != "active":
            continue
        data["_id"] = doc.id
        results.append(data)
    results.sort(key=lambda x: x.get("createdAt") or 0, reverse=True)
    return results[:50]


# ── Staff ────────────────────────────────────────────────────────────────────

def get_all_staff() -> list[dict]:
    db = get_db()
    results = []
    for doc in _users_ref(db).get():
        data = doc.to_dict()
        data["_id"] = doc.id
        results.append(data)
    return results


def get_admin_users() -> list[dict]:
    """Only role='admin'. Директор intentionally excluded from notifications."""
    db = get_db()
    result = []
    for doc in _users_ref(db).get():
        data = doc.to_dict()
        if data.get("userRole") == "admin":
            data["_id"] = doc.id
            result.append(data)
    return result


def get_cinema_staff_tids(cinema: str) -> list[int]:
    """
    Return Telegram IDs of all active (non-blocked) staff for *cinema*.
    All user docs live under Cinema/atmosfera/Users; the 'cinema' field
    on each doc indicates which cinema the staff member works at.
    Users without a 'cinema' field default to 'atmosfera'.
    """
    db = get_db()
    result = []
    for doc in _users_ref(db).get():
        data = doc.to_dict() or {}
        if data.get("isBlocked"):
            continue
        user_cinema = data.get("cinema", "atmosfera")
        if user_cinema != cinema:
            continue
        tid = data.get("telegramId")
        if tid is not None:
            try:
                result.append(int(tid))
            except (TypeError, ValueError):
                pass
    return result


def add_staff_user(data: dict) -> str:
    db = get_db()
    _, doc_ref = _users_ref(db).add(data)
    return doc_ref.id


def update_staff_user(doc_id: str, updates: dict) -> None:
    db = get_db()
    _users_ref(db).document(doc_id).update(updates)


def delete_staff_user(doc_id: str) -> None:
    db = get_db()
    _users_ref(db).document(doc_id).delete()


# ── Statistics ────────────────────────────────────────────────────────────────

def get_statistics() -> dict:
    db = get_db()

    total_orders = 0
    active = 0
    completed = 0
    total_revenue = 0.0

    today = datetime.now().date()

    for doc in _orders_ref(db).get():
        data = doc.to_dict()

        created = data.get("createdAt")
        if not created:
            continue

        try:
            order_date = created.date()
        except Exception:
            continue

        if order_date != today:
            continue

        total_orders += 1
        status = data.get("status", "")

        if status == "active":
            active += 1
        elif status == "closed":
            completed += 1
            total_revenue += float(data.get("total", 0) or 0)

    return {
        "total_orders": total_orders,
        "active": active,
        "completed": completed,
        "total_revenue": total_revenue,
    }


# ── Recipes ───────────────────────────────────────────────────────────────────

def get_recipes() -> list[dict]:
    db = get_db()
    results = []
    for doc in _recipes_ref(db).get():
        data = doc.to_dict()
        data["_id"] = doc.id
        results.append(data)
    return results


# ── Write-offs ────────────────────────────────────────────────────────────────

def save_writeoff(writeoff_data: dict) -> str:
    db = get_db()
    writeoff_data["createdAt"] = firestore.SERVER_TIMESTAMP
    _, doc_ref = _writeoffs_ref(db).add(writeoff_data)
    return doc_ref.id


def get_writeoffs_history(limit: int = 20) -> list[dict]:
    db = get_db()
    results = []
    for doc in _writeoffs_ref(db).get():
        data = doc.to_dict()
        data["_id"] = doc.id
        results.append(data)
    results.sort(key=lambda x: x.get("createdAt") or 0, reverse=True)
    return results[:limit]


# ── Menu ──────────────────────────────────────────────────────────────────────

def get_menu_items() -> list[dict]:
    db = get_db()
    results = []
    for doc in _menu_ref(db).get():
        data = doc.to_dict() or {}
        data["_id"] = doc.id
        results.append(data)
    results.sort(key=lambda x: x.get("name", ""))
    return results


def get_menu_item(item_id: str) -> dict | None:
    db = get_db()
    doc = _menu_ref(db).document(item_id).get()
    if doc.exists:
        data = doc.to_dict() or {}
        data["_id"] = doc.id
        return data
    return None


def search_menu_items(query: str) -> list[dict]:
    q = query.lower().strip()
    return [
        item for item in get_menu_items()
        if q in item.get("name", "").lower() or q in item.get("_id", "").lower()
    ][:10]


def create_menu_item(item_id: str, data: dict) -> None:
    db = get_db()
    _menu_ref(db).document(item_id).set(data)


def update_menu_item(item_id: str, updates: dict) -> None:
    db = get_db()
    _menu_ref(db).document(item_id).update(updates)


def delete_menu_item(item_id: str) -> None:
    db = get_db()
    _menu_ref(db).document(item_id).delete()


# ── Sessions ──────────────────────────────────────────────────────────────────
# Path: Cinema/{cinema}/Sessions/{sessionId}
# The bot READS from here; services/schedule_import.py WRITES here.

def _sessions_ref(db, cinema: str):
    return db.collection("Cinema").document(cinema).collection("Sessions")


def get_user_cinema(telegram_id: int) -> str:
    """Return the cinema slug for this staff member. Falls back to 'atmosfera'."""
    info = get_user_info(telegram_id)
    return (info or {}).get("cinema", "atmosfera")


def get_sessions(cinema: str, date_str: str | None = None) -> list[dict]:
    """
    Return sessions for *cinema*, optionally filtered to *date_str* (YYYY-MM-DD).
    Sorted by sessionDate then sessionTime.
    """
    db      = get_db()
    results = []
    for doc in _sessions_ref(db, cinema).get():
        data = doc.to_dict() or {}
        data["_id"] = doc.id
        if date_str and data.get("sessionDate") != date_str:
            continue
        results.append(data)
    results.sort(key=lambda x: (x.get("sessionDate", ""), x.get("sessionTime", "")))
    return results


def save_session(cinema: str, data: dict) -> str:
    """
    Upsert one session document using merge=True.

    merge=True means:
      • On first write  → creates the document with all provided fields.
      • On re-import    → updates schedule fields (movieTitle, endTime, …)
                          but PRESERVES existing fields NOT in data
                          (startNotifSent, endNotifSent keep their values).

    This prevents duplicate light notifications when the 15-min import runs
    right after a notification was sent.
    """
    db   = get_db()
    data = dict(data)
    data["updatedAt"] = firestore.SERVER_TIMESTAMP
    sid  = data.pop("sessionId", None)
    if sid:
        _sessions_ref(db, cinema).document(str(sid)).set(data, merge=True)
        return str(sid)
    _, doc_ref = _sessions_ref(db, cinema).add(data)
    return doc_ref.id


def clear_stale_sessions(cinema: str, keep_ids: set) -> int:
    """
    Delete session documents whose ID is NOT in keep_ids.
    Called during import to remove sessions that disappeared from Multiplex.
    Returns count deleted.
    """
    db    = get_db()
    docs  = _sessions_ref(db, cinema).get()
    count = 0
    for doc in docs:
        if doc.id not in keep_ids:
            doc.reference.delete()
            count += 1
    return count


def clear_sessions(cinema: str) -> int:
    """Delete ALL session documents for *cinema*. Returns count deleted."""
    db    = get_db()
    docs  = _sessions_ref(db, cinema).get()
    count = 0
    for doc in docs:
        doc.reference.delete()
        count += 1
    return count


# ── cinema_schedule collection (used by Unity / external consumers) ───────────
#
#  Path: Cinema/{cinema}/cinema_schedule/{date}/sessions/{sessionId}
#
#  Document format:
#    { sessionId, movie, hall, startTime (ISO 8601), endTime (ISO 8601),
#      generatedAt: SERVER_TIMESTAMP }

def _schedule_sessions_ref(db, cinema: str, date_str: str):
    return (
        db.collection("Cinema")
          .document(cinema)
          .collection("cinema_schedule")
          .document(date_str)
          .collection("sessions")
    )


def save_schedule_session(cinema: str, date_str: str, data: dict) -> str:
    """
    Write one session document to the cinema_schedule subcollection.
    Uses set() (full overwrite) — the daily job clears old docs first.
    """
    db   = get_db()
    data = dict(data)
    data["generatedAt"] = firestore.SERVER_TIMESTAMP
    sid  = data.get("sessionId")
    ref  = _schedule_sessions_ref(db, cinema, date_str)
    if sid:
        ref.document(str(sid)).set(data)
        return str(sid)
    _, doc_ref = ref.add(data)
    return doc_ref.id


def clear_schedule_sessions(cinema: str, date_str: str) -> int:
    """
    Delete all session documents for *cinema* on *date_str*.
    Called before writing fresh data so removed sessions don't linger.
    Returns count deleted.
    """
    db    = get_db()
    ref   = _schedule_sessions_ref(db, cinema, date_str)
    docs  = ref.get()
    count = 0
    for doc in docs:
        doc.reference.delete()
        count += 1
    return count


def mark_session_notification_sent(cinema: str, session_id: str, field: str) -> None:
    """
    Persist a notification flag on a session document.

    field should be 'startNotifSent' or 'endNotifSent'.
    The flag survives subsequent imports because save_session uses merge=True
    and does not include these fields in the import payload.
    """
    db = get_db()
    try:
        _sessions_ref(db, cinema).document(session_id).update({field: True})
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "mark_session_notification_sent failed [%s/%s/%s]: %s",
            cinema, session_id, field, exc,
        )
