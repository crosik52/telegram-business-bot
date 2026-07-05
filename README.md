# Telegram Business Bot

A production-ready Telegram Business Bot that logs every personal Telegram
Business message — including edits and deletions — into a database, with
full history preserved (nothing is ever overwritten or destroyed), plus a
modern web dashboard for browsing, searching, and exporting that data.

## What it does

- Connects to your personal Telegram account via the **Business API**
  (requires Telegram Premium + a Business account connection).
- Logs every incoming/outgoing business message.
- When a message is edited, the **original content is preserved** and the
  edit is recorded as a new entry in an append-only edit history — the row
  is never overwritten.
- When a message is deleted, it is **soft-deleted** (flagged, never removed)
  so its last known content remains visible in the dashboard.
  > Telegram limitation: the `deleted_business_messages` update only
  > contains the chat ID and message IDs — never the deleted content. This
  > bot can only preserve content for messages it had already captured
  > before deletion. This is a platform limitation, not a bug.
- Ships a Jinja2 + Tailwind (CDN) web dashboard with:
  - Session-based login (CSRF-protected)
  - Message search & filtering (text, sender, chat, date range, edited-only,
    media-only)
  - Message detail view with full edit history timeline
  - Deleted-messages view
  - Statistics page (message/edit/delete counts, top chats, media breakdown)
  - JSON/CSV export of filtered results
  - Dark mode, responsive layout

## Stack

- Python 3.12, [aiogram 3.x](https://docs.aiogram.dev/) (webhook mode only —
  no polling)
- FastAPI + Jinja2 (dashboard + webhook endpoint in a single ASGI app)
- SQLAlchemy 2.x (async) + Alembic migrations
- SQLite by default; switch to PostgreSQL by changing `DATABASE_URL`
- Tailwind via CDN (no Node build step)
- Fully async, repository pattern, type-hinted, PEP 8

## Project layout

```
app/
  business/        aiogram dispatcher + Business API update handlers
  dashboard/        Jinja2 templates, static assets, dashboard routes
  database/         async engine/session setup
  middlewares/      request logging middleware
  models/           SQLAlchemy models (users, connections, messages, edit history)
  repositories/     data-access layer (repository pattern)
  routers/          /health and /webhook FastAPI routers
  services/         business logic (ingestion, auth, stats, export)
  utils/            CSRF, pagination, formatting helpers
  config.py         environment-driven settings
  main.py           FastAPI app factory + lifespan (webhook registration, DB init)
alembic/            migrations
```

## 1. Create your bot with BotFather

1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. `/newbot` → follow the prompts → copy the bot token.
3. Enable Business mode for the bot: `/mybots` → select your bot →
   **Business Mode** → **Turn on**.
   - Optionally set a custom "Business Mode" greeting/intro under the same
     menu — this is unrelated to logging and purely cosmetic.

## 2. Connect the bot to your personal account

1. In the Telegram app, go to **Settings → Business → Chatbots**.
2. Select the bot you just created and grant it access to the chats you
   want logged (you can allow all chats or specific ones).
3. Once connected, Telegram will start sending `business_connection` and
   `business_message` updates to your bot for the messages in those chats.

> The bot only ever sees messages in chats you explicitly connect it to via
> **Settings → Business → Chatbots** — it cannot see your other private
> chats.

## 3. Configure environment variables

Copy `.env.example` to `.env` and fill in the values:

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token from BotFather |
| `WEBHOOK_BASE_URL` | Public HTTPS base URL of your deployment (e.g. `https://your-app.up.railway.app`) |
| `WEBHOOK_PATH` | Path the webhook is served on (default `/webhook`) |
| `TELEGRAM_WEBHOOK_SECRET` | Random string Telegram echoes back to verify requests are genuinely from Telegram |
| `PORT` | Port to listen on (Railway sets this automatically) |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/bot.db` by default; use a `postgres://...` URL for Postgres |
| `DASHBOARD_ADMIN_USERNAME` / `DASHBOARD_ADMIN_PASSWORD` | Dashboard login credentials |
| `SESSION_SECRET` | Long random string used to sign dashboard session cookies |

Generate secrets with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## 4. Run locally

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The dashboard is at `http://localhost:8000/` and the health check at
`http://localhost:8000/health`.

For local testing without a public HTTPS URL, leave `WEBHOOK_BASE_URL` unset
— the app will start normally but skip calling `setWebhook`. You can feed
test updates directly to `POST /webhook` to exercise the message pipeline.

## 5. Deploy to Railway

This project deploys to [Railway](https://railway.app) with zero extra
configuration beyond environment variables.

### One-click steps

1. Push this repository to GitHub.
2. In Railway, click **New Project → Deploy from GitHub repo** and select
   the repository.
3. Railway detects the `Dockerfile` automatically and builds the image.
4. Under **Variables**, set:
   - `TELEGRAM_BOT_TOKEN`
   - `WEBHOOK_BASE_URL` — set this to your Railway-provided domain, e.g.
     `https://<your-app>.up.railway.app` (available under **Settings →
     Networking → Generate Domain** if not already assigned)
   - `TELEGRAM_WEBHOOK_SECRET` — any random string
   - `DASHBOARD_ADMIN_USERNAME`, `DASHBOARD_ADMIN_PASSWORD`
   - `SESSION_SECRET`
   - `DATABASE_URL` — for production, add a Railway PostgreSQL plugin and
     use its connection string (Railway injects `DATABASE_URL`
     automatically when you attach a Postgres plugin to the service — it
     will already be in `postgres://...` form, which this app normalizes
     to the async driver automatically). If omitted, the app falls back to
     a local SQLite file, which does **not** persist across redeploys
     unless you attach a [volume](https://docs.railway.app/reference/volumes).
5. Deploy. Railway assigns `PORT` automatically; the container listens on
   `0.0.0.0:$PORT` and Railway's healthcheck hits `/health`.
6. On startup, the app automatically:
   - Runs `alembic upgrade head` (via the Docker `CMD`)
   - Creates any missing tables as a safety net
   - Calls Telegram's `setWebhook` with `WEBHOOK_BASE_URL` + `WEBHOOK_PATH`
     and the configured secret token
7. Push to `main` on GitHub — Railway auto-redeploys on every push.

### Persisting SQLite across redeploys (optional)

If you don't want to use Postgres, attach a Railway **Volume** mounted at
`/app/data` so `data/bot.db` survives redeploys. Otherwise, use Postgres for
production — it's the recommended path and requires no code changes.

## 6. Verify the webhook

After deploying, confirm Telegram registered the webhook:

```bash
curl "https://api.telegram.org/bot<TOKEN>/getWebhookInfo"
```

You should see your `WEBHOOK_BASE_URL + WEBHOOK_PATH` as the `url` field
with no recent `last_error_message`.

## Database migrations

Schema changes are managed with Alembic:

```bash
# after changing a model
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

`alembic upgrade head` runs automatically on container start in production
(see `Dockerfile` CMD), so a fresh deploy always has an up-to-date schema.

## CI

`.github/workflows/ci.yml` runs on every push/PR to `main`:

- `ruff check` — linting
- `mypy` — static type checking
- Validates the FastAPI app imports cleanly with placeholder env vars
- Validates Alembic migrations apply cleanly to a throwaway SQLite DB

## Troubleshooting

- **Webhook not receiving updates**: check `getWebhookInfo` (above) for
  `last_error_message`. Common causes: `WEBHOOK_BASE_URL` not HTTPS, wrong
  domain, or the app not listening on the Railway-provided `PORT`.
- **Dashboard login fails**: confirm `DASHBOARD_ADMIN_USERNAME` /
  `DASHBOARD_ADMIN_PASSWORD` are set exactly as intended (case-sensitive,
  no surrounding whitespace).
- **Business messages aren't appearing**: verify the bot is connected under
  **Settings → Business → Chatbots** in the Telegram app for the specific
  chats you expect to see, and that Business Mode is enabled for the bot in
  BotFather.
- **`sqlalchemy.exc` SSL/connection errors on Postgres**: this app strips
  `sslmode`/`channel_binding` query params (unsupported by asyncpg) from
  `DATABASE_URL` automatically and negotiates SSL via `ssl=True` when the
  original URL requested `sslmode=require`.
