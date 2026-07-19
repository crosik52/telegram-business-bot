# Telegram Business Bot

A production-ready Telegram Business Bot that logs every personal Telegram Business message — including edits and deletions — into a database, with a web dashboard for browsing, searching, and exporting that data.

## Stack

- **Python 3.12** — FastAPI + Uvicorn (ASGI)
- **aiogram 3.x** — Telegram Bot API (webhook mode only, no polling)
- **SQLAlchemy 2.x (async)** + Alembic migrations
- **PostgreSQL** (Replit-managed, connected automatically)
- **Jinja2** + Tailwind CDN — server-rendered dashboard

## How to run

The app starts automatically via the **Start application** workflow:

```
uvicorn app.main:app --host 0.0.0.0 --port 5000
```

On startup, the app automatically registers the Telegram webhook at `WEBHOOK_BASE_URL/webhook`.

## Required environment variables / secrets

| Variable | Description |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from @BotFather (Business Mode must be enabled) |
| `WEBHOOK_BASE_URL` | Public HTTPS base URL (set to the Replit dev domain) |
| `DASHBOARD_ADMIN_USERNAME` | Web dashboard login username |
| `DASHBOARD_ADMIN_PASSWORD` | Web dashboard login password |
| `SESSION_SECRET` | Long random string for signing session cookies |

Optional:
- `TELEGRAM_WEBHOOK_SECRET` — Secret token echoed by Telegram in webhook headers (recommended in production)

## Database migrations

Schema changes are managed with Alembic:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

## Project layout

```
app/
  business/     aiogram dispatcher + Business API update handlers
  dashboard/    Jinja2 templates, static assets, dashboard routes
  database/     async engine/session setup
  middlewares/  request logging middleware
  models/       SQLAlchemy models
  repositories/ data-access layer (repository pattern)
  routers/      /health and /webhook FastAPI routers
  services/     business logic (ingestion, auth, stats, export)
  utils/        CSRF, pagination, formatting helpers
  config.py     environment-driven settings (pydantic-settings)
  main.py       FastAPI app factory + lifespan
alembic/        migrations
```

## User preferences

- Keep the existing structure and stack — do not restructure or migrate.
- Every new feature must have a corresponding admin panel section: config controls, enable/disable toggle, editable parameters, and (where relevant) a list/management UI. Build both sides together by default.
- **Always ask clarifying questions before implementing** when there are design choices, ambiguities, or multiple reasonable approaches. Do not jump straight to code.
