"""Baseline: users, servers, command_usage as defined in src/models.py.

For an EXISTING database that already has these tables (e.g. the current
production verify_me_database), do NOT run upgrade — mark it as already
at this revision instead:

    alembic stamp head

For a FRESH database:

    alembic upgrade head

Revision ID: 0001
Revises:
Create Date: 2026-07-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("discord_id", sa.String(length=50), nullable=False),
        sa.Column("verification_status", sa.Boolean(), nullable=True),
        sa.Column("last_verification_attempt", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dob", sa.String(length=255), nullable=True),
    )
    op.create_table(
        "servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_id", sa.String(length=30), nullable=False, unique=True),
        sa.Column("owner_id", sa.String(length=30), nullable=False),
        sa.Column("role_id", sa.String(length=30), nullable=True),
        sa.Column("tier", sa.String(length=50), nullable=True),
        sa.Column("subscription_status", sa.Boolean(), nullable=True),
        sa.Column("verifications_count", sa.Integer(), nullable=True),
        sa.Column("subscription_start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=255), nullable=True),
        sa.Column("minimum_age", sa.Integer(), nullable=False, server_default="18"),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("last_renewal_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("instructions_channel_id", sa.String(length=30), nullable=True),
        sa.Column("instructions_message_id", sa.String(length=30), nullable=True),
    )
    op.create_table(
        "command_usage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("server_id", sa.String(length=30), nullable=False),
        sa.Column("user_id", sa.String(length=30), nullable=False),
        sa.Column("command", sa.String(length=50), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("command_usage")
    op.drop_table("servers")
    op.drop_table("users")
