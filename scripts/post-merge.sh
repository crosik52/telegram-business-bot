#!/bin/bash
set -e

echo "→ Installing Python dependencies…"
pip install -r requirements.txt -q

echo "→ Running Alembic migrations…"
alembic upgrade head

echo "✓ Post-merge setup complete."
