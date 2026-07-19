"""Phase 3 server settings: locale, custom success message, unverified role,
auto-verify-on-join toggle.

Apply with:

    alembic upgrade head

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-18

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "servers",
        sa.Column("instructions_locale", sa.String(length=10), nullable=False, server_default="en-US"),
    )
    op.add_column(
        "servers",
        sa.Column("custom_verification_message", sa.String(length=1000), nullable=True),
    )
    op.add_column(
        "servers",
        sa.Column("unverified_role_id", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "servers",
        sa.Column("auto_verify_new_members", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("servers", "auto_verify_new_members")
    op.drop_column("servers", "unverified_role_id")
    op.drop_column("servers", "custom_verification_message")
    op.drop_column("servers", "instructions_locale")
