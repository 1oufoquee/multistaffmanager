import os
import json
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

_KYIV_TZ = ZoneInfo("Europe/Kyiv")

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

def _schedules_ref(db):
    return db.collection("Cinema").document("atmosfera").collection("Schedules")

def _light_confirmations_ref(db):
    return db.collection("Cinema").document("atmosfera").collection("LightConfirmations")

def _active_writeoffs_ref(db):
    return db.collection("Cinema").document("atmosfera").collection("ActiveWriteoffs")


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


def get_user_cinema(telegram_id: int) -> str:
    """Return the cinema slug for this staff member. Falls back to 'atmosfera'."""
    info = get_user_info(telegram_id)
    return (info or {}).get("cinema", "atmosfera")


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


def get_cinema_staff_list(cinema: str, exclude_tid: int) -> list[dict]:
    """
    Return list of {name, telegramId} for active (non-blocked) staff at *cinema*,
    excluding *exclude_tid*.  Used to build the transfer-to keyboard.
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
        if tid is None:
            continue
        try:
            tid_int = int(tid)
        except (TypeError, ValueError):
            continue
        if tid_int == int(exclude_tid):
            continue
        result.append({"name": data.get("name", "—"), "telegramId": tid_int})
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

    today = datetime.now(_KYIV_TZ).date()

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

# ── Schedules ─────────────────────────────────────────────────────────────────

def get_schedule(date_str: str) -> dict | None:
    """Load schedule doc for 'yyyy-MM-dd'. Returns dict or None if not found."""
    db = get_db()
    doc = _schedules_ref(db).document(date_str).get()
    if doc.exists:
        return doc.to_dict()
    return None


def get_light_reminder_users() -> list[int]:
    """Return telegram IDs of all non-blocked users with lightReminders == true."""
    db = get_db()
    result = []
    for doc in _users_ref(db).get():
        data = doc.to_dict() or {}
        if data.get("isBlocked"):
            continue
        if not data.get("lightReminders"):
            continue
        tid = data.get("telegramId")
        if tid is not None:
            try:
                result.append(int(tid))
            except (TypeError, ValueError):
                pass
    return result


def toggle_light_reminders(telegram_id: int) -> bool:
    """Toggle the lightReminders flag for a user. Returns the new bool value."""
    doc, data = _find_user_doc(telegram_id)
    if doc is None:
        raise ValueError(f"User {telegram_id} not found")
    new_val = not bool(data.get("lightReminders", False))
    doc.reference.update({"lightReminders": new_val})
    return new_val


# ── Light-reminder confirmations ──────────────────────────────────────────────

def get_light_confirmation(reminder_id: str) -> dict | None:
    """Return the confirmation doc for *reminder_id*, or None if not yet confirmed."""
    db = get_db()
    doc = _light_confirmations_ref(db).document(reminder_id).get()
    if doc.exists:
        return doc.to_dict()
    return None


def confirm_light_reminder(reminder_id: str, conf_data: dict) -> bool:
    """
    Atomically save *conf_data* for *reminder_id* only if not already confirmed.
    Returns True when this call wins the race (first confirmation).
    Returns False when the reminder was already confirmed by someone else.
    """
    db  = get_db()
    ref = _light_confirmations_ref(db).document(reminder_id)

    @firestore.transactional
    def _try(transaction, ref, data):
        snap = ref.get(transaction=transaction)
        if snap.exists and snap.to_dict().get("confirmed"):
            return False
        transaction.set(ref, data)
        return True

    return _try(db.transaction(), ref, conf_data)


def save_active_writeoff(data: dict) -> str:
    """Save a draft write-off (in-progress transfer). Returns doc_id."""
    db = get_db()
    _, doc_ref = _active_writeoffs_ref(db).add(data)
    return doc_ref.id


def get_active_writeoff(doc_id: str) -> dict | None:
    db = get_db()
    doc = _active_writeoffs_ref(db).document(doc_id).get()
    if doc.exists:
        data = doc.to_dict() or {}
        data["_id"] = doc.id
        return data
    return None


def update_active_writeoff(doc_id: str, updates: dict) -> None:
    db = get_db()
    _active_writeoffs_ref(db).document(doc_id).update(updates)


def delete_active_writeoff(doc_id: str) -> None:
    db = get_db()
    _active_writeoffs_ref(db).document(doc_id).delete()


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


def get_writeoffs_for_day(day: date | None = None) -> list[dict]:
    """
    Read all write-offs whose createdAt falls on the requested Kyiv calendar day.

    This function is intentionally read-only. It uses the existing Writeoffs
    collection and does not alter any document.
    """
    target_day = day or datetime.now(_KYIV_TZ).date()
    start = datetime.combine(target_day, datetime.min.time(), tzinfo=_KYIV_TZ)
    end = start + timedelta(days=1)

    db = get_db()
    results = []
    for doc in _writeoffs_ref(db).get():
        data = doc.to_dict() or {}
        created = data.get("createdAt")
        if not created:
            continue

        try:
            if isinstance(created, datetime):
                created_dt = created
                if created_dt.tzinfo is None:
                    # Firestore timestamps represent UTC instants.
                    created_dt = created_dt.replace(tzinfo=ZoneInfo("UTC"))
                created_dt = created_dt.astimezone(_KYIV_TZ)
            elif isinstance(created, (int, float)):
                created_dt = datetime.fromtimestamp(created, tz=_KYIV_TZ)
            else:
                created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                if created_dt.tzinfo is None:
                    created_dt = created_dt.replace(tzinfo=ZoneInfo("UTC"))
                created_dt = created_dt.astimezone(_KYIV_TZ)
        except (TypeError, ValueError, OverflowError):
            continue

        if start <= created_dt < end:
            data["_id"] = doc.id
            results.append(data)

    results.sort(key=lambda item: item.get("createdAt") or 0, reverse=True)
    return results


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
