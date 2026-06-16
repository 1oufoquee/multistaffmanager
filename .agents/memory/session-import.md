---
name: Session import architecture
description: How movie sessions are fetched, stored, and displayed — Multiplex scraper to Firestore to Telegram bot.
---

## Rule
The Telegram bot NEVER scrapes Multiplex.ua directly.
Data flow: `Multiplex.ua → services/schedule_import.py → Firestore → bot reads`

## Firestore path
`Cinema/{cinema}/Sessions/{doc_id}`
— always under the cinema document, never at the root `Sessions/` collection.

## Key files
- `services/schedule_import.py` — async Playwright scraper, `import_cinema(cinema, days_ahead=2)` for full refresh
- `jobs/update_sessions.py` — PTB JobQueue callback `update_all_cinemas`, runs every 15 min (first=60s after startup)
- `bot/firebase_client.py` — `get_sessions(cinema, date_str)`, `save_session(cinema, data)`, `clear_sessions(cinema)`, `get_user_cinema(telegram_id)`
- `bot/handlers/cinema_schedule.py` — reads from Firestore, shows today/tomorrow/next session

## Adding a new cinema
Add one entry to `CINEMA_URLS` dict in `services/schedule_import.py`. No other changes needed.

## get_user_cinema
Reads `cinema` field from user's Firestore doc (`Cinema/atmosfera/Users/{id}`). Defaults to `"atmosfera"` if field is absent.

## Playwright note
`playwright install chromium` must be run once in the environment to install browser binaries.
The selector `div.ns` with `data-name`, `p.time`, `p.tag` is the current Multiplex.ua HTML structure.

**Why:** Scraping from the bot on every user request was slow, fragile, and risked rate-limiting. Caching in Firestore keeps responses instant and independent from Multiplex uptime.
