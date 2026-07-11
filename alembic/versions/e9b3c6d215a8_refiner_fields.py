"""AI event refiner fields on events

Revision ID: e9b3c6d215a8
Revises: c3e8f5a17d42
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e9b3c6d215a8"
down_revision = "c3e8f5a17d42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.add_column(
            sa.Column("refined", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.add_column(sa.Column("refiner_note", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("events") as batch:
        batch.drop_column("refiner_note")
        batch.drop_column("refined")
