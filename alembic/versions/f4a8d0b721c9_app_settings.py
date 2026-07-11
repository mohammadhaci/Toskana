"""Dashboard-managed app settings (key/value JSON blobs)

Revision ID: f4a8d0b721c9
Revises: e9b3c6d215a8
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f4a8d0b721c9"
down_revision = "e9b3c6d215a8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("app_settings")
