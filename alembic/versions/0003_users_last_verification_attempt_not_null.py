"""Correct users.last_verification_attempt to NOT NULL.

Every code path that creates a User row (bot.py's track_verification_attempt,
stripe_webhook_service.py's two checkout/cancel handlers) always sets this
column, and running `alembic check` against the real production database
after applying 0002 showed it is already NOT NULL there -- the baseline
(0001) simply mis-declared it as nullable. This migration brings the model
and schema back in sync; on a database that already enforces NOT NULL (e.g.
production) it is a no-op.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch mode: plain ALTER COLUMN works on Postgres, but sqlite (used by
    # the test suite's fresh-upgrade check) requires table-recreation mode
    # for column alterations.
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "last_verification_attempt",
            existing_type=sa.DateTime(timezone=True),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column(
            "last_verification_attempt",
            existing_type=sa.DateTime(timezone=True),
            nullable=True,
        )
