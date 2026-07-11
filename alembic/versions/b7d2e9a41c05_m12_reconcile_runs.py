"""M12: reconcile_runs table (persisted POS reconciliation reports)

Revision ID: b7d2e9a41c05
Revises: 8c4f1a2d9b30
Create Date: 2026-07-10

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d2e9a41c05'
down_revision: Union[str, None] = '8c4f1a2d9b30'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('reconcile_runs',
    sa.Column('id', sa.String(length=26), nullable=False),
    sa.Column('restaurant_id', sa.Integer(), nullable=False),
    sa.Column('date', sa.String(length=10), nullable=False),
    sa.Column('uploaded_filename', sa.String(length=255), nullable=True),
    sa.Column('rows_json', sa.Text(), nullable=False),
    sa.Column('total_pos_quantity', sa.Float(), nullable=False),
    sa.Column('total_counted_net', sa.Integer(), nullable=False),
    sa.Column('created_ts', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['restaurant_id'], ['restaurants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('reconcile_runs', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_reconcile_runs_restaurant_id'), ['restaurant_id'], unique=False
        )
        batch_op.create_index(
            'ix_reconcile_runs_restaurant_created', ['restaurant_id', 'created_ts'], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table('reconcile_runs', schema=None) as batch_op:
        batch_op.drop_index('ix_reconcile_runs_restaurant_created')
        batch_op.drop_index(batch_op.f('ix_reconcile_runs_restaurant_id'))
    op.drop_table('reconcile_runs')
