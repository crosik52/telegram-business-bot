"""One-shot script: mark every 'pending' relationship row as 'broken'.

Usage:
  DATABASE_URL=postgresql://... python scripts/cancel_pending_relationships.py

On Railway:
  Open your service → Settings → Deploy → New Deployment command (one-off), or
  use the Railway Shell / CLI:
    railway run python scripts/cancel_pending_relationships.py
"""

import asyncio
import os
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker


async def main() -> None:
    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    # Normalise URL driver
    if raw_url.startswith("postgres://"):
        raw_url = raw_url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif raw_url.startswith("postgresql://") and "+asyncpg" not in raw_url:
        raw_url = raw_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif raw_url.startswith("sqlite://") and "+aiosqlite" not in raw_url:
        raw_url = raw_url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    engine = create_async_engine(raw_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        result = await session.execute(
            text(
                "UPDATE relationships "
                "SET status = 'broken' "
                "WHERE status = 'pending' "
                "RETURNING id, user_a_id, user_b_id, initiator_id"
            )
        )
        rows = result.fetchall()
        await session.commit()

    await engine.dispose()

    print(f"Done. Cancelled {len(rows)} pending relationship request(s).")
    for r in rows:
        print(f"  id={r.id}  {r.user_a_id} ↔ {r.user_b_id}  initiator={r.initiator_id}")


if __name__ == "__main__":
    asyncio.run(main())
