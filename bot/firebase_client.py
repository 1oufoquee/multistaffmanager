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

def is_authorized_user(telegram_id: int) -> bool:
    db = get_db()
    query = _users_ref(db).where("telegramId", "==", telegram_id).limit(1).get()
    return len(query) > 0


def get_user_info(telegram_id: int) -> dict | None:
    db = get_db()
    query = _users_ref(db).where("telegramId", "==", telegram_id).limit(1).get()
    if query:
        data = query[0].to_dict()
        data["_id"] = query[0].id
        return data
    return None


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
