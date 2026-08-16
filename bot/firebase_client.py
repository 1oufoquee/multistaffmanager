import os
import json
import logging
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from contextvars import ContextVar
import time

_KYIV_TZ = ZoneInfo("Europe/Kyiv")
logger = logging.getLogger(__name__)

PROJECTS = {
    "atmosfera": {
        "label": "Atmosfera",
        "env": "FIREBASE_SERVICE_ACCOUNT_JSON",
        "cinema_document": "atmosfera",
    },
    "karavan": {
        "label": "Karavan",
        "env": "FIREBASE_KARAVAN_SERVICE_ACCOUNT_JSON",
        "cinema_document": "karavan",
    },
    "retroville": {
        "label": "Retroville",
        "env": "FIREBASE_RETROVILLE_SERVICE_ACCOUNT_JSON",
        "cinema_document": "retroville",
    },
}

_active_project: ContextVar[str | None] = ContextVar(
    "active_firebase_project",
    default=None,
)
_db_by_project: dict = {}
_user_project_cache: dict[int, tuple[float, str | None]] = {}
_feature_config_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL_SECONDS = 30

DEFAULT_FEATURES = {
    # Match the current intended normal-user menu: orders and statistics are
    # hidden, while the other existing staff features are enabled.
    "sessions": True,
    "writeoffs": True,
    "orders": False,
    "statistics": False,
    "admin_panel": True,
    "light_reminders": True,
}

FEATURE_LABELS = {
    "sessions": "🎬 Сеанси",
    "writeoffs": "🗑 Списання",
    "orders": "📦 Замовлення",
    "statistics": "📊 Статистика",
    "admin_panel": "👑 Адмін панель",
    "light_reminders": "💡 Нагадування світла",
}


def _project_config(project_key: str) -> dict:
    try:
        return PROJECTS[project_key]
    except KeyError as exc:
        raise ValueError(f"Unknown cinema project: {project_key}") from exc


def _get_project_db(project_key: str):
    """Return a cached Firestore client for one configured Firebase project."""
    if project_key in _db_by_project:
        return _db_by_project[project_key]

    config = _project_config(project_key)
    service_account_json = os.environ.get(config["env"])
    if not service_account_json:
        raise ValueError(
            f"{config['env']} env var is not set for the {config['label']} project"
        )

    try:
        service_account_info = json.loads(service_account_json)
        cred = credentials.Certificate(service_account_info)
        app_name = f"multistaff_{project_key}"
        try:
            app = firebase_admin.get_app(app_name)
        except ValueError:
            app = firebase_admin.initialize_app(cred, name=app_name)
        db = firestore.client(app=app)
    except Exception:
        logger.exception("Failed to initialize Firebase project %s", project_key)
        raise

    _db_by_project[project_key] = db
    return db


def _project_for_user(telegram_id: int) -> str | None:
    """Resolve a user's project without ever assuming Atmosfera."""
    try:
        telegram_id = int(telegram_id)
    except (TypeError, ValueError):
        return None

    developer = get_developer_info(telegram_id)
    if developer:
        selected = developer.get("selectedProject")
        if selected in PROJECTS:
            return selected
        return None

    cached = _user_project_cache.get(telegram_id)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    found_project = None
    for project_key in PROJECTS:
        try:
            db = _get_project_db(project_key)
            ref = _cinema_ref_for_project(db, project_key).collection("Users")
            for doc in ref.get():
                data = doc.to_dict() or {}
                try:
                    if int(data.get("telegramId") or 0) == telegram_id:
                        found_project = project_key
                        break
                except (TypeError, ValueError):
                    continue
            if found_project:
                break
        except Exception:
            logger.exception(
                "Could not search Users for Telegram ID %s in project %s",
                telegram_id,
                project_key,
            )

    _user_project_cache[telegram_id] = (time.monotonic(), found_project)
    if found_project:
        logger.info(
            "Resolved Telegram ID %s to Firebase project %s / cinemaId %s",
            telegram_id,
            found_project,
            PROJECTS[found_project]["cinema_document"],
        )
    else:
        logger.info("No Firebase cinema project found for Telegram ID %s", telegram_id)
    return found_project


def _project_key_for_context(telegram_id: int | None = None) -> str:
    project_key = (
        _project_for_user(telegram_id)
        if telegram_id is not None
        else _active_project.get()
    )
    if project_key not in PROJECTS:
        raise LookupError(
            f"No active Firebase project for Telegram ID {telegram_id}"
        )
    return project_key


def get_active_firestore(telegram_id: int | None = None):
    """Return the Firestore client selected for this Telegram/cinema context."""
    project_key = _project_key_for_context(telegram_id)
    db = _get_project_db(project_key)
    logger.info(
        "Using Firebase project %s / cinemaId %s",
        project_key,
        PROJECTS[project_key]["cinema_document"],
    )
    return db


def get_active_cinema_id(telegram_id: int | None = None) -> str:
    project_key = _project_key_for_context(telegram_id)
    return PROJECTS[project_key]["cinema_document"]


def get_active_cinema_ref(telegram_id: int | None = None):
    """Return the selected project's Cinema/{cinemaId} document reference."""
    project_key = _project_key_for_context(telegram_id)
    db = _get_project_db(project_key)
    cinema_id = PROJECTS[project_key]["cinema_document"]
    path = f"Cinema/{cinema_id}"
    logger.info("Using Firestore path %s in project %s", path, project_key)
    return db.collection("Cinema").document(cinema_id)


def get_db(project_key: str | None = None):
    """Backward-compatible alias for the active context client."""
    return (
        _get_project_db(project_key)
        if project_key
        else get_active_firestore()
    )


def set_active_project(project_key: str | None) -> None:
    """Select a Firebase project for the current async update context."""
    if project_key is not None:
        _project_config(project_key)
    _active_project.set(project_key)
    if project_key:
        logger.info(
            "Selected Firebase project %s / cinemaId %s",
            project_key,
            PROJECTS[project_key]["cinema_document"],
        )
    else:
        logger.info("Cleared active Firebase project")


def get_active_project() -> str | None:
    return _active_project.get()


# ── Collection refs ──────────────────────────────────────────────────────────

def _cinema_ref_for_project(db, project_key: str):
    project = _project_config(project_key)
    path = f"Cinema/{project['cinema_document']}"
    logger.info("Using Firestore path %s in project %s", path, project_key)
    return db.collection("Cinema").document(project["cinema_document"])


def _cinema_ref(db):
    project_key = _project_key_for_context()
    return _cinema_ref_for_project(db, project_key)


def _users_ref(db):
    return _cinema_ref(db).collection("Users")

def _orders_ref(db):
    return _cinema_ref(db).collection("Orders")

def _recipes_ref(db):
    return _cinema_ref(db).collection("Recipes")

def _writeoffs_ref(db):
    return _cinema_ref(db).collection("Writeoffs")

def _menu_ref(db):
    return _cinema_ref(db).collection("Menu")

def _schedules_ref(db):
    return _cinema_ref(db).collection("Schedules")

def _light_confirmations_ref(db):
    return _cinema_ref(db).collection("LightConfirmations")

def _active_writeoffs_ref(db):
    return _cinema_ref(db).collection("ActiveWriteoffs")


def _developers_ref(db):
    return db.collection("Developers")


# ── Auth ─────────────────────────────────────────────────────────────────────

def _find_developer_doc(telegram_id: int):
    """Return (snapshot, sanitized_data) from the global Developers collection."""
    db = get_db("atmosfera")
    for doc in _developers_ref(db).get():
        data = doc.to_dict() or {}
        try:
            if int(data.get("telegramId") or 0) == int(telegram_id):
                data.pop("password", None)
                data["_id"] = doc.id
                return doc, data
        except (TypeError, ValueError):
            continue
    return None, None


def get_developer_info(telegram_id: int) -> dict | None:
    """Return a developer profile only when userRole is exactly developer."""
    doc, data = _find_developer_doc(telegram_id)
    if doc is None or data.get("userRole") != "developer":
        return None
    return data


def set_developer_project(telegram_id: int, project_key: str) -> None:
    """Persist a developer's selected project in Developers/{documentId}."""
    _project_config(project_key)
    # Fail before changing the developer's stored selection if the target
    # project credentials are unavailable or invalid.
    _get_project_db(project_key)
    db = get_db("atmosfera")
    doc, data = _find_developer_doc(telegram_id)
    if doc is None or data.get("userRole") != "developer":
        raise PermissionError("Developer account not found")
    doc.reference.update({"selectedProject": project_key})


def get_developer_project(telegram_id: int) -> str | None:
    info = get_developer_info(telegram_id)
    selected = (info or {}).get("selectedProject")
    return selected if selected in PROJECTS else None


def activate_project_for_user(telegram_id: int) -> str:
    """
    Select the project for one incoming Telegram update.

    Developers use their persisted selection; normal users are resolved by
    locating their Telegram ID in the three cinema projects.
    """
    developer = get_developer_info(telegram_id)
    if developer:
        project = developer.get("selectedProject")
        if project in PROJECTS:
            set_active_project(project)
            return project
        set_active_project(None)
        return ""

    project = _project_for_user(telegram_id)
    set_active_project(project)
    return project or ""


def _find_user_doc(telegram_id: int):
    """Returns (doc_snapshot, dict) or (None, None). Always fetches fresh."""
    project_key = get_active_project() or _project_for_user(telegram_id)
    if project_key not in PROJECTS:
        return None, None
    if get_active_project() != project_key:
        set_active_project(project_key)
    db = get_db(project_key)
    logger.info(
        "User lookup path: Cinema/%s/Users for Telegram ID %s",
        PROJECTS[project_key]["cinema_document"],
        telegram_id,
    )
    for doc in _cinema_ref_for_project(db, project_key).collection("Users").get():
        data = doc.to_dict() or {}
        try:
            if int(data.get("telegramId") or 0) == int(telegram_id):
                return doc, data
        except (TypeError, ValueError):
            continue
    return None, None


def is_authorized_user(telegram_id: int) -> bool:
    developer = get_developer_info(telegram_id)
    if developer:
        return True
    doc, data = _find_user_doc(telegram_id)
    if doc is None:
        return False
    return not data.get("isBlocked", False)


def get_user_info(telegram_id: int) -> dict | None:
    developer = get_developer_info(telegram_id)
    if developer:
        selected = developer.get("selectedProject")
        developer["cinema"] = selected or "atmosfera"
        developer["project"] = selected
        return developer
    doc, data = _find_user_doc(telegram_id)
    if doc is None:
        return None
    data["_id"] = doc.id
    data.setdefault("cinema", get_active_cinema_id())
    data["project"] = get_active_project()
    return data


def get_user_cinema(telegram_id: int) -> str:
    """Return the active cinema slug for this staff member."""
    developer = get_developer_info(telegram_id)
    if developer:
        return developer.get("selectedProject") or ""
    info = get_user_info(telegram_id)
    return (info or {}).get("cinema") or get_active_cinema_id(telegram_id)


def _feature_ref(project_key: str):
    db = _get_project_db(project_key)
    cinema_id = PROJECTS[project_key]["cinema_document"]
    path = f"Cinema/{cinema_id}/BotConfig/features"
    logger.info("Using feature configuration path %s in project %s", path, project_key)
    return (
        db.collection("Cinema")
        .document(cinema_id)
        .collection("BotConfig")
        .document("features")
    )


def _load_feature_config(project_key: str, force_refresh: bool = False) -> dict:
    cached = _feature_config_cache.get(project_key)
    if (
        not force_refresh
        and cached
        and time.monotonic() - cached[0] < _CACHE_TTL_SECONDS
    ):
        return dict(cached[1])

    ref = _feature_ref(project_key)
    snapshot = ref.get()
    if snapshot.exists:
        stored = snapshot.to_dict() or {}
        config = {**DEFAULT_FEATURES, **{
            key: bool(stored[key])
            for key in DEFAULT_FEATURES
            if key in stored
        }}
    else:
        config = dict(DEFAULT_FEATURES)
        ref.set(config)
        logger.info(
            "Created default feature configuration for project %s",
            project_key,
        )

    _feature_config_cache[project_key] = (time.monotonic(), config)
    return dict(config)


def get_feature_config(
    telegram_id: int | None = None,
    force_refresh: bool = False,
) -> dict:
    project_key = _project_key_for_context(telegram_id)
    return _load_feature_config(project_key, force_refresh=force_refresh)


def refresh_feature_config(telegram_id: int | None = None) -> dict:
    return get_feature_config(telegram_id, force_refresh=True)


def is_feature_enabled(feature: str, telegram_id: int | None = None) -> bool:
    if feature not in DEFAULT_FEATURES:
        raise ValueError(f"Unknown feature: {feature}")
    if telegram_id is not None and get_developer_info(telegram_id):
        return True
    return bool(get_feature_config(telegram_id).get(feature, DEFAULT_FEATURES[feature]))


def update_feature_config(
    telegram_id: int,
    feature: str,
    enabled: bool,
) -> dict:
    """Update a feature only for a verified developer's selected project."""
    developer = get_developer_info(telegram_id)
    if not developer or developer.get("userRole") != "developer":
        raise PermissionError("Developer access required")
    project_key = developer.get("selectedProject")
    if project_key not in PROJECTS:
        raise LookupError("Select a cinema before changing feature settings")

    ref = _feature_ref(project_key)
    config = _load_feature_config(project_key)
    config[feature] = bool(enabled)
    ref.set(config, merge=True)
    _feature_config_cache[project_key] = (time.monotonic(), dict(config))
    logger.info(
        "Feature toggled: %s=%s for project %s / cinemaId %s",
        feature,
        enabled,
        project_key,
        PROJECTS[project_key]["cinema_document"],
    )
    return dict(config)


def get_project_information(telegram_id: int) -> dict:
    """Return diagnostic information for the developer's selected project."""
    developer = get_developer_info(telegram_id)
    if not developer or developer.get("userRole") != "developer":
        raise PermissionError("Developer access required")
    project_key = developer.get("selectedProject")
    if project_key not in PROJECTS:
        raise LookupError("Select a cinema before viewing project information")

    db = get_active_firestore(telegram_id)
    cinema_id = get_active_cinema_id(telegram_id)
    users_ref = db.collection("Cinema").document(cinema_id).collection("Users")
    schedules_ref = db.collection("Cinema").document(cinema_id).collection("Schedules")
    logger.info(
        "Project info paths: Cinema/%s/Users and Cinema/%s/Schedules",
        cinema_id,
        cinema_id,
    )

    users = users_ref.get()
    schedule_docs = schedules_ref.get()
    dates = sorted(doc.id for doc in schedule_docs)
    latest_update = None
    for doc in schedule_docs:
        data = doc.to_dict() or {}
        candidate = (
            data.get("updatedAt")
            or data.get("updated_at")
            or data.get("lastUpdated")
        )
        if candidate and (latest_update is None or str(candidate) > str(latest_update)):
            latest_update = candidate

    return {
        "project": project_key,
        "project_label": PROJECTS[project_key]["label"],
        "cinema_id": cinema_id,
        "connection_status": "connected",
        "user_count": len(users),
        "schedule_dates": dates,
        "latest_schedule_update": latest_update,
        "features": get_feature_config(telegram_id),
    }


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
    active_cinema = get_active_cinema_id()
    logger.info(
        "Admin/user list path: Cinema/%s/Users",
        active_cinema,
    )
    result = []
    for doc in _users_ref(db).get():
        data = doc.to_dict() or {}
        if data.get("isBlocked"):
            continue
        user_cinema = data.get("cinema", active_cinema)
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
        user_cinema = data.get("cinema", get_active_cinema_id())
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
    data = dict(data)
    if "telegramId" in data:
        data["telegramId"] = float(data["telegramId"])
    logger.info(
        "Creating staff user at Cinema/%s/Users",
        get_active_cinema_id(),
    )
    _, doc_ref = _users_ref(db).add(data)
    return doc_ref.id


def update_staff_user(doc_id: str, updates: dict) -> None:
    db = get_db()
    updates = dict(updates)
    if "telegramId" in updates:
        updates["telegramId"] = float(updates["telegramId"])
    logger.info(
        "Updating staff user at Cinema/%s/Users: %s",
        get_active_cinema_id(),
        doc_id,
    )
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
    project_key = _project_key_for_context()
    feature_config = _load_feature_config(project_key)
    db = get_db()
    result = []
    if feature_config.get("light_reminders", True):
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

    # Developers are global accounts, so they may not have a local Users
    # document in the selected cinema project.
    root_db = get_db("atmosfera")
    for doc in _developers_ref(root_db).get():
        data = doc.to_dict() or {}
        if data.get("userRole") != "developer" or not data.get("lightReminders"):
            continue
        if data.get("selectedProject") != get_active_project():
            continue
        tid = data.get("telegramId")
        if tid is not None:
            try:
                tid_int = int(tid)
                if tid_int not in result:
                    result.append(tid_int)
            except (TypeError, ValueError):
                pass
    return result


def toggle_light_reminders(telegram_id: int) -> bool:
    """Toggle the lightReminders flag for a user. Returns the new bool value."""
    developer_doc, developer = _find_developer_doc(telegram_id)
    if developer_doc is not None and developer.get("userRole") == "developer":
        new_val = not bool(developer.get("lightReminders", False))
        developer_doc.reference.update({"lightReminders": new_val})
        return new_val

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
