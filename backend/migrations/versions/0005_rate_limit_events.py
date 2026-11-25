"""Add shared request throttling records without changing retail data."""
from alembic import op
import sqlalchemy as sa

revision='0005_rate_limit_events'
down_revision='0004_pipeline_orchestration'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('rate_limit_events',
        sa.Column('event_id',sa.Integer(),primary_key=True,autoincrement=True),
        sa.Column('scope',sa.String(length=32),nullable=False),
        sa.Column('key_hash',sa.String(length=64),nullable=False),
        sa.Column('occurred_at',sa.DateTime(),server_default=sa.func.now(),nullable=False))
    op.create_index('ix_rate_limit_events_scope','rate_limit_events',['scope'])
    op.create_index('ix_rate_limit_events_key_hash','rate_limit_events',['key_hash'])
    op.create_index('ix_rate_limit_events_occurred_at','rate_limit_events',['occurred_at'])
    op.create_index('ix_rate_limit_scope_key_time','rate_limit_events',['scope','key_hash','occurred_at'])

def downgrade():
    op.drop_table('rate_limit_events')
