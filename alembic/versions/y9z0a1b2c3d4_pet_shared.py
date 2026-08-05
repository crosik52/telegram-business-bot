"""One shared pet per pair (user_a_id / user_b_id canonical key).

Revision ID: y9z0a1b2c3d4
Revises: x8y9z0a1b2c3
Create Date: 2026-08-05
"""
from alembic import op
import sqlalchemy as sa

revision = "y9z0a1b2c3d4"
down_revision = "x8y9z0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Step 1: Add new columns (nullable while we populate) ─────────────────
    op.add_column("chat_pets", sa.Column("user_a_id", sa.BigInteger(), nullable=True))
    op.add_column("chat_pets", sa.Column("user_b_id", sa.BigInteger(), nullable=True))
    op.add_column("chat_pets", sa.Column("partner_name", sa.String(100),
                                         nullable=True, server_default=""))

    # ── Step 2: Populate pair columns from existing owner / chat ─────────────
    op.execute("""
        UPDATE chat_pets
        SET user_a_id = LEAST(owner_telegram_id, chat_id),
            user_b_id = GREATEST(owner_telegram_id, chat_id)
    """)

    # ── Step 3a: Merge alive duplicates – sum counters into the winner ────────
    # Winner = row with higher XP; tie-break: lower id.
    op.execute("""
        UPDATE chat_pets AS w
        SET total_feedings = w.total_feedings + l.loser_feedings,
            total_plays    = w.total_plays    + l.loser_plays,
            total_cuddles  = w.total_cuddles  + l.loser_cuddles,
            feed_streak    = GREATEST(w.feed_streak, l.loser_streak)
        FROM (
            SELECT
                CASE WHEN a.xp > b.xp OR (a.xp = b.xp AND a.id < b.id)
                     THEN a.id ELSE b.id END                                          AS winner_id,
                CASE WHEN a.xp > b.xp OR (a.xp = b.xp AND a.id < b.id)
                     THEN b.total_feedings ELSE a.total_feedings END                  AS loser_feedings,
                CASE WHEN a.xp > b.xp OR (a.xp = b.xp AND a.id < b.id)
                     THEN b.total_plays ELSE a.total_plays END                        AS loser_plays,
                CASE WHEN a.xp > b.xp OR (a.xp = b.xp AND a.id < b.id)
                     THEN b.total_cuddles ELSE a.total_cuddles END                    AS loser_cuddles,
                CASE WHEN a.xp > b.xp OR (a.xp = b.xp AND a.id < b.id)
                     THEN b.feed_streak ELSE a.feed_streak END                        AS loser_streak
            FROM chat_pets a
            JOIN chat_pets b
                ON a.user_a_id = b.user_a_id
               AND a.user_b_id = b.user_b_id
               AND a.id < b.id
            WHERE a.is_alive = TRUE AND b.is_alive = TRUE
        ) l
        WHERE w.id = l.winner_id
    """)

    # ── Step 3b: Delete loser alive-duplicate rows ────────────────────────────
    op.execute("""
        DELETE FROM chat_pets
        WHERE id IN (
            SELECT
                CASE WHEN a.xp > b.xp OR (a.xp = b.xp AND a.id < b.id)
                     THEN b.id ELSE a.id END AS loser_id
            FROM chat_pets a
            JOIN chat_pets b
                ON a.user_a_id = b.user_a_id
               AND a.user_b_id = b.user_b_id
               AND a.id < b.id
            WHERE a.is_alive = TRUE AND b.is_alive = TRUE
        )
    """)

    # ── Step 4: Remove dead mirror rows ──────────────────────────────────────
    # Keep only the one dead row with the lowest id per pair (discard mirrors).
    op.execute("""
        DELETE FROM chat_pets
        WHERE is_alive = FALSE
          AND id NOT IN (
              SELECT MIN(id)
              FROM chat_pets
              WHERE is_alive = FALSE
              GROUP BY user_a_id, user_b_id
          )
    """)

    # ── Step 5: Make columns NOT NULL ────────────────────────────────────────
    op.alter_column("chat_pets", "user_a_id",    nullable=False)
    op.alter_column("chat_pets", "user_b_id",    nullable=False)
    op.alter_column("chat_pets", "partner_name", nullable=False, server_default="")

    # ── Step 6: Partial unique index – at most one alive pet per pair ─────────
    op.create_index(
        "uq_chat_pets_pair_alive",
        "chat_pets",
        ["user_a_id", "user_b_id"],
        unique=True,
        postgresql_where=sa.text("is_alive = TRUE"),
    )


def downgrade() -> None:
    op.drop_index("uq_chat_pets_pair_alive", table_name="chat_pets")
    op.drop_column("chat_pets", "partner_name")
    op.drop_column("chat_pets", "user_b_id")
    op.drop_column("chat_pets", "user_a_id")
