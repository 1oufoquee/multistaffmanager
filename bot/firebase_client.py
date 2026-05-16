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


def get_active_orders() -> list[dict]:
    db = get_db()
    orders_ref = db.collection("orders")
    query = orders_ref.where("status", "==", "active").order_by("createdAt", direction=firestore.Query.DESCENDING).limit(50).get()
    results = []
    for doc in query:
        data = doc.to_dict()
        data["id"] = doc.id
        results.append(data)
    return results


def get_statistics() -> dict:
    db = get_db()
    orders_ref = db.collection("orders")
    all_orders = orders_ref.get()

    total_orders = 0
    total_revenue = 0.0
    completed = 0
    cancelled = 0
    active = 0
    status_counts = {}

    for doc in all_orders:
        data = doc.to_dict()
        total_orders += 1
        status = data.get("status", "unknown")
        status_counts[status] = status_counts.get(status, 0) + 1
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
        "status_counts": status_counts,
    }


def get_writeoffs() -> list[dict]:
    db = get_db()
    ref = db.collection("writeoffs")
    query = ref.order_by("createdAt", direction=firestore.Query.DESCENDING).limit(30).get()
    results = []
    for doc in query:
        data = doc.to_dict()
        data["id"] = doc.id
        results.append(data)
    return results


def add_writeoff(item_name: str, quantity: float, unit: str, reason: str, staff_name: str) -> str:
    db = get_db()
    ref = db.collection("writeoffs")
    from google.cloud.firestore_v1 import SERVER_TIMESTAMP
    doc_ref = ref.add({
        "itemName": item_name,
        "quantity": quantity,
        "unit": unit,
        "reason": reason,
        "staffName": staff_name,
        "createdAt": SERVER_TIMESTAMP,
    })
    return doc_ref[1].id
