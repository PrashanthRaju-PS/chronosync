"""add instruments.is_fno / fno_as_of (F&O membership)

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-13
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE instruments "
        "ADD COLUMN is_fno BOOLEAN NOT NULL DEFAULT FALSE, "
        "ADD COLUMN fno_as_of DATE"
    )
    # Partial index: the F&O universe is a small slice (~200 of ~3000 rows), and
    # the scanner queries `WHERE is_fno = TRUE`. A partial index keeps that lookup
    # cheap without bloating the far more common full-instrument scans.
    op.execute("CREATE INDEX ix_instruments_is_fno ON instruments (is_fno) WHERE is_fno")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_instruments_is_fno")
    op.execute("ALTER TABLE instruments DROP COLUMN IF EXISTS fno_as_of")
    op.execute("ALTER TABLE instruments DROP COLUMN IF EXISTS is_fno")
