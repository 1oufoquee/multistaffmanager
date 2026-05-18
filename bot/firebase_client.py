import os
import json
import firebase_admin
from firebase_admin import credentials, firestore

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
# Firestore may store telegramId as float64 — compare as int on the Python side.
# Blocked users are denied access regardless of telegramId match.

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
    total_orders = active = completed = 0
    total_revenue = 0.0
    for doc in _orders_ref(db).get():
        data = doc.to_dict()
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
