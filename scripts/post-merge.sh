#!/bin/bash
set -e

echo "→ Installing Python dependencies…"
pip install -r requirements.txt -q

echo "→ Running Alembic migrations…"
# Use 'heads' (plural) to handle parallel branches created by concurrent task agents.
alembic upgrade heads

echo "✓ Post-merge setup complete."
