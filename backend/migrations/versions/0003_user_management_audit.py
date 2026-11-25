"""User lifecycle and append-only audit records."""
from alembic import op
import sqlalchemy as sa
revision='0003_user_management_audit'; down_revision='0002_auth_sessions'; branch_labels=None; depends_on=None
def upgrade():
    op.add_column('users',sa.Column('must_change_password',sa.Boolean(),nullable=False,server_default=sa.false()))
    op.create_table('audit_events',sa.Column('event_id',sa.Integer(),primary_key=True,autoincrement=True),sa.Column('actor_user_id',sa.Integer(),sa.ForeignKey('users.user_id',ondelete='SET NULL'),nullable=True),sa.Column('action_type',sa.String(80),nullable=False),sa.Column('target_entity',sa.String(80),nullable=True),sa.Column('target_id',sa.String(128),nullable=True),sa.Column('occurred_at',sa.DateTime(),nullable=False,server_default=sa.func.now()),sa.Column('outcome',sa.String(20),nullable=False,server_default='success'),sa.Column('details',sa.JSON(),nullable=False,server_default='{}'))
    op.create_index('ix_audit_events_actor_user_id','audit_events',['actor_user_id']);op.create_index('ix_audit_events_action_type','audit_events',['action_type']);op.create_index('ix_audit_events_occurred_at','audit_events',['occurred_at'])
def downgrade():
    raise RuntimeError('Audit history and user onboarding state are retained; perform a reviewed archive/backup before downgrade.')
