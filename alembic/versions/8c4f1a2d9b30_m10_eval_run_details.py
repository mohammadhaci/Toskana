"""M10: counting_eval_runs detail columns (video ref, config, count payloads)

Revision ID: 8c4f1a2d9b30
Revises: 500e6e3fa5c4
Create Date: 2026-07-10

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8c4f1a2d9b30'
down_revision: Union[str, None] = '500e6e3fa5c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('counting_eval_runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('video_ref', sa.String(length=2000), nullable=True))
        batch_op.add_column(sa.Column('config_json', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('gt_counts_json', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('measured_counts_json', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('counting_eval_runs', schema=None) as batch_op:
        batch_op.drop_column('measured_counts_json')
        batch_op.drop_column('gt_counts_json')
        batch_op.drop_column('config_json')
        batch_op.drop_column('video_ref')
