"""reject non-finite instruments.market_cap (NaN/inf)

A NaN market cap is a valid NUMERIC in Postgres and a valid Decimal in Python,
but has no JSON encoding — one such row 500s the entire /instruments response and
takes down every consumer that lists instruments. The provider, write, and
serializer layers now guard against it; this constraint is the last line of
defence at the storage layer, and the pre-cleanup heals any NaN already stored.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-06
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

_CK = "ck_instruments_market_cap_finite"
# NaN/±Infinity all fall outside the open (-Infinity, Infinity) interval in
# Postgres numeric ordering, so this passes only NULL or a real finite value.
_FINITE = (
    "market_cap IS NULL OR "
    "(market_cap > '-Infinity'::numeric AND market_cap < 'Infinity'::numeric)"
)


def upgrade() -> None:
    # Heal any non-finite value already stored, so the constraint can be added.
    op.execute(f"UPDATE instruments SET market_cap = NULL WHERE NOT ({_FINITE})")
    op.execute(f"ALTER TABLE instruments ADD CONSTRAINT {_CK} CHECK ({_FINITE})")


def downgrade() -> None:
    op.execute(f"ALTER TABLE instruments DROP CONSTRAINT IF EXISTS {_CK}")
