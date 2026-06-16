---
name: Auth type mismatch — telegramId
description: Firestore stores telegramId as float64; Python comparison must cast both sides to int.
---

## Rule
Never use `_users_ref(db).where("telegramId", "==", telegram_id)`.
Instead, fetch all users and compare with `int(stored) == int(incoming)`.

## Why
Firestore may store numeric fields as float64 (e.g. `123456789.0`).
Python-telegram-bot always provides `user.id` as Python `int`.
Firestore `==` filter does strict type comparison, so int ≠ float → user not found.

## How to apply
See `_find_user_doc()` in `bot/firebase_client.py` — fetch all docs, iterate, cast both sides to int.
Also: `is_authorized_user` checks `isBlocked` field; blocked users are denied bot access.

**Why no cache:** New users must be detectable immediately; no in-memory user cache anywhere in the bot.
