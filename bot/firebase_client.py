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


def is_authorized_user(telegram_id: int) -> bool:
    db = get_db()
    users_ref = db.collection("staff_users")
    query = users_ref.where("telegramId", "==", telegram_id).limit(1).get()
    return len(query) > 0


def get_user_info(telegram_id: int) -> dict | None:
    db = get_db()
    users_ref = db.collection("staff_users")
    query = users_ref.where("telegramId", "==", telegram_id).limit(1).get()
    if query:
        return query[0].to_dict()
    return None


def get_orders() -> list[dict]:
    db = get_db()
    # Path: atmosfera (collection) → Orders (document) → Orders (subcollection)
    orders_ref = db.collection("atmosfera").document("Orders").collection("Orders")
    query = orders_ref.order_by("createdAt", direction=firestore.Query.DESCENDING).limit(50).get()
    results = []
    for doc in query:
        data = doc.to_dict()
        data["_id"] = doc.id
        results.append(data)
    return results


def get_all_staff() -> list[dict]:
    db = get_db()
    # Path: atmosfera (collection) → Users (document with subcollection or direct collection)
    ref = db.collection("atmosfera").document("Users").collection("Users")
    docs = ref.get()
    results = []
    for doc in docs:
        data = doc.to_dict()
        data["_id"] = doc.id
        results.append(data)
    return results


def get_statistics() -> dict:
    db = get_db()
    orders_ref = db.collection("atmosfera").document("Orders").collection("Orders")
    all_orders = orders_ref.get()

    total_orders = 0
    total_revenue = 0.0
    completed = 0
    cancelled = 0
    active = 0

    for doc in all_orders:
        data = doc.to_dict()
        total_orders += 1
        status = data.get("status", "unknown")
        if status == "active":
            active += 1
        elif status == "completed":
            completed += 1
            total_revenue += float(data.get("total", 0) or 0)
        elif status == "cancelled":
            cancelled += 1

    return {
        "total_orders": total_orders,
        "active": active,
        "completed": completed,
        "cancelled": cancelled,
        "total_revenue": total_revenue,
    }
