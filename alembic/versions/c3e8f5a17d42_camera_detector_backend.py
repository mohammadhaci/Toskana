"""per-camera detector_backend override

Revision ID: c3e8f5a17d42
Revises: b7d2e9a41c05
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c3e8f5a17d42"
down_revision = "b7d2e9a41c05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("cameras") as batch:
        batch.add_column(sa.Column("detector_backend", sa.String(length=16), nullable=True))
    # Existing file cameras were built around the demo/synthetic detector;
    # pin them so flipping the global default to auto/yolo doesn't break the
    # out-of-the-box demo. Real (rtsp/usb) cameras stay NULL -> resolved global.
    op.execute("UPDATE cameras SET detector_backend = 'synthetic' WHERE source_type = 'file'")


def downgrade() -> None:
    with op.batch_alter_table("cameras") as batch:
        batch.drop_column("detector_backend")
