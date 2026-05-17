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


def _users_ref(db):
    return db.collection("Cinema").document("atmosfera").collection("Users")


def _orders_ref(db):
    return db.collection("Cinema").document("atmosfera").collection("Orders")


def _recipes_ref(db):
    return db.collection("Cinema").document("atmosfera").collection("Recipes")


def _writeoffs_ref(db):
    return db.collection("Cinema").document("atmosfera").collection("Writeoffs")


# ── Auth ────────────────────────────────────────────────────────────────────
# NOTE: We intentionally avoid Firestore .where("telegramId", "==", ...) because
# Firestore may store the field as float64, which causes type-mismatch misses
# when Python sends an int.  Instead we fetch all users fresh on every call
# and compare after casting both sides to int — safe for small staff lists.

def _find_user_doc(telegram_id: int):
    """
    Returns (doc_snapshot, dict) for the matching user, or (None, None).
    Fetches all users fresh from Firestore on every call — no in-memory cache.
    """
    db = get_db()
    docs = _users_ref(db).get()
    for doc in docs:
        data = doc.to_dict() or {}
        stored = data.get("telegramId")
        try:
            if int(stored) == int(telegram_id):
                return doc, data
        except (TypeError, ValueError):
            continue
    return None, None


def is_authorized_user(telegram_id: int) -> bool:
    doc, _ = _find_user_doc(telegram_id)
    return doc is not None


def get_user_info(telegram_id: int) -> dict | None:
    doc, data = _find_user_doc(telegram_id)
    if doc is None:
        return None
    data["_id"] = doc.id
    return data


# ── Orders ──────────────────────────────────────────────────────────────────

def get_orders() -> list[dict]:
    db = get_db()
    docs = _orders_ref(db).get()
    results = []
    for doc in docs:
        data = doc.to_dict()
        if data.get("status") != "active":
            continue
        data["_id"] = doc.id
        results.append(data)
    results.sort(key=lambda x: x.get("createdAt") or 0, reverse=True)
    return results[:50]


# ── Staff ───────────────────────────────────────────────────────────────────

def get_all_staff() -> list[dict]:
    db = get_db()
    docs = _users_ref(db).get()
    results = []
    for doc in docs:
        data = doc.to_dict()
        data["_id"] = doc.id
        results.append(data)
    return results


def get_admin_users() -> list[dict]:
    db = get_db()
    docs = _users_ref(db).get()
    admins = []
    for doc in docs:
        data = doc.to_dict()
        if data.get("userRole") == "admin":
            data["_id"] = doc.id
            admins.append(data)
    return admins


# ── Statistics ──────────────────────────────────────────────────────────────

def get_statistics() -> dict:
    db = get_db()
    all_orders = _orders_ref(db).get()

    total_orders = 0
    total_revenue = 0.0
    completed = 0
    active = 0

    for doc in all_orders:
        data = doc.to_dict()
        total_orders += 1
        status = data.get("status", "unknown")
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


# ── Recipes ─────────────────────────────────────────────────────────────────

def get_recipes() -> list[dict]:
    db = get_db()
    docs = _recipes_ref(db).get()
    results = []
    for doc in docs:
        data = doc.to_dict()
        data["_id"] = doc.id
        results.append(data)
    return results


# ── Write-offs ───────────────────────────────────────────────────────────────

def save_writeoff(writeoff_data: dict) -> str:
    db = get_db()
    writeoff_data["createdAt"] = firestore.SERVER_TIMESTAMP
    _, doc_ref = _writeoffs_ref(db).add(writeoff_data)
    return doc_ref.id


def get_writeoffs_history(limit: int = 20) -> list[dict]:
    db = get_db()
    docs = _writeoffs_ref(db).get()
    results = []
    for doc in docs:
        data = doc.to_dict()
        data["_id"] = doc.id
        results.append(data)
    results.sort(key=lambda x: x.get("createdAt") or 0, reverse=True)
    return results[:limit]
