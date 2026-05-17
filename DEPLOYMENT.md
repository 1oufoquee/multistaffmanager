# Cinema Staff Bot — Railway Deployment Guide

## Prerequisites

- GitHub account
- Railway account (railway.app)
- Telegram bot token from @BotFather
- Firebase service account JSON

---

## Step 1 — Push to GitHub

1. Create a new repository on GitHub (can be private)
2. In your terminal, from the project root:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

> **Important:** Never commit secrets. The `.gitignore` already excludes `.env` and `*.json` files.

---

## Step 2 — Create a Railway project

1. Go to [railway.app](https://railway.app) and log in
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your repository
4. Railway will detect the `Procfile` and use it automatically

---

## Step 3 — Add environment variables

In your Railway project, go to **Variables** and add:

| Variable | Value |
|---|---|
| `BOT_TOKEN` | Your Telegram bot token from @BotFather |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | The full contents of your Firebase service account JSON (paste as one value) |

> To get Firebase credentials: Firebase Console → Project Settings → Service Accounts → Generate new private key

---

## Step 4 — Configure the service type

1. In Railway, open your service settings
2. Under **Deploy**, make sure the start command is: `python main.py`
3. Railway uses the `Procfile` worker command automatically

---

## Step 5 — Deploy

1. Click **Deploy** (or push a new commit — Railway auto-deploys on every push)
2. Watch the build logs — you should see:
   ```
   === Cinema Staff Bot starting ===
   Environment: OK
   Building application...
   Handlers registered: /start /help /orders /staff /stats + keyboard
   Starting polling... Bot is ready.
   ```

---

## Keeping the bot alive 24/7

Railway's **Hobby plan** ($5/month) keeps workers running continuously.
The free tier may sleep — use the Hobby plan for 24/7 uptime.

The bot uses **long-polling** (not webhooks), so no public URL is required.
Railway does not need to expose any ports for this bot to work.

---

## Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `BOT_TOKEN` | Yes | Telegram bot token |
| `FIREBASE_SERVICE_ACCOUNT_JSON` | Yes | Firebase service account JSON (full content) |

---

## Updating the bot

Just push a new commit to your GitHub repository:

```bash
git add .
git commit -m "Update bot"
git push
```

Railway will automatically redeploy within seconds.
