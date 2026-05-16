# Cinema Staff Bot

A Telegram bot for cinema staff to manage active orders, view sales statistics, and record write-offs. Access is restricted to users whose Telegram ID is stored in Firebase Firestore.

## Run & Operate

- `python main.py` — run the Telegram bot
- Bot workflow: "Cinema Staff Bot" (configured in Replit workflows)

## Stack

- Python 3.11
- python-telegram-bot 20.7 (async polling)
- firebase-admin 6.4.0 (Firestore)
- pnpm workspaces, Node.js 24, TypeScript 5.9 (existing API server)

## Where things live

- `main.py` — bot entry point, registers all handlers
- `bot/firebase_client.py` — Firebase/Firestore client (auth checks, data queries)
- `bot/handlers/start.py` — /start command, authorization gate
- `bot/handlers/orders.py` — /orders command, shows active orders
- `bot/handlers/stats.py` — /stats command, shows sales statistics
- `bot/handlers/writeoffs.py` — /writeoffs and /addwriteoff (multi-step conversation)
- `bot/utils.py` — shared helpers (timestamp formatting)

## Architecture decisions

- Access control is enforced in every handler by checking `telegramId` against the `staff_users` Firestore collection.
- Firebase client is lazily initialized (singleton pattern) to avoid repeated credential parsing.
- Write-offs use a ConversationHandler to guide staff through a 4-step flow (item → qty → unit → reason).
- All Firestore field names follow camelCase to match the existing Firebase schema convention.
- The bot uses long-polling (not webhooks) so no public URL or extra server config is needed.

## Product

Staff can:
1. **Check active orders** — see all orders with `status == "active"` from Firestore, with customer name, seat/hall, items and total.
2. **View statistics** — total orders, active/completed/cancelled counts, and total revenue from completed orders.
3. **View recent write-offs** — last 30 write-offs with item, quantity, reason, and staff name.
4. **Record a write-off** — guided 4-step conversation to log item name, quantity, unit, and reason.

## Firebase Firestore Schema Expected

### `staff_users` collection
```
{ telegramId: number, name: string }
```

### `orders` collection
```
{ status: "active"|"completed"|"cancelled", customerName: string, hall: string, items: array, total: number, createdAt: timestamp }
```

### `writeoffs` collection
```
{ itemName: string, quantity: number, unit: string, reason: string, staffName: string, createdAt: timestamp }
```

## Required Secrets

- `TELEGRAM_BOT_TOKEN` — from @BotFather on Telegram
- `FIREBASE_SERVICE_ACCOUNT_JSON` — Firebase service account JSON (full content)

## User preferences

- Python for the bot
- Firebase Firestore as database
- Access control via telegramId stored in Firebase

## Gotchas

- The `staff_users` collection must have documents with a `telegramId` field (integer) matching the user's Telegram ID. Without this, all users are denied access.
- Field names in Firestore queries (`status`, `createdAt`, `telegramId`) must match exactly what's in your Firebase collections.
- If your Firestore collection names differ (e.g. `orders` → `cinema_orders`), update the collection names in `bot/firebase_client.py`.
