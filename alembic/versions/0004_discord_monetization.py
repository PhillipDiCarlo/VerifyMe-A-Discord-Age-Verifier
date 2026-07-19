"""Discord-native monetization columns (Phase 5).

Adds provider tracking and Discord entitlement state to servers. Existing
rows are backfilled to payment_provider='stripe' (the server_default), so
grandfathered Stripe subscribers are untouched.

Apply with:

    alembic upgrade head

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-19

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("payment_provider", sa.String(length=10), nullable=False, server_default="stripe"),
    )
    op.add_column(
        "servers",
        sa.Column("discord_sku_id", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "servers",
        sa.Column("discord_entitlement_id", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "servers",
        sa.Column("entitlement_ends_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("servers", "entitlement_ends_at")
    op.drop_column("servers", "discord_entitlement_id")
    op.drop_column("servers", "discord_sku_id")
    op.drop_column("servers", "payment_provider")
