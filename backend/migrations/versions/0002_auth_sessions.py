"""Add users and server-side sessions without altering retail tables."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
revision='0002_auth_sessions'; down_revision='0001_initial'; branch_labels=None; depends_on=None
def upgrade():
    bind=op.get_bind(); inspector=inspect(bind); existing=set(inspector.get_table_names())
    user_cols={'user_id','email','full_name','password_hash','role','is_active','created_at','updated_at','last_login_at'}
    session_cols={'session_id','user_id','token_hash','csrf_hash','created_at','expires_at','revoked_at','user_agent','client_ip'}
    if 'users' in existing:
        actual={c['name'] for c in inspector.get_columns('users')}
        if not user_cols.issubset(actual): raise RuntimeError(f'Existing users table requires reconciliation; missing columns: {sorted(user_cols-actual)}')
    else:
        op.create_table('users',sa.Column('user_id',sa.Integer(),primary_key=True,autoincrement=True),sa.Column('email',sa.String(320),nullable=False),sa.Column('full_name',sa.String(160),nullable=False),sa.Column('password_hash',sa.String(255),nullable=False),sa.Column('role',sa.String(30),nullable=False,server_default='VIEWER'),sa.Column('is_active',sa.Boolean(),nullable=False,server_default=sa.true()),sa.Column('created_at',sa.DateTime(),nullable=False,server_default=sa.func.now()),sa.Column('updated_at',sa.DateTime(),nullable=False,server_default=sa.func.now()),sa.Column('last_login_at',sa.DateTime(),nullable=True))
    inspector=inspect(bind); existing=set(inspector.get_table_names())
    if 'user_sessions' in existing:
        actual={c['name'] for c in inspector.get_columns('user_sessions')}
        if not session_cols.issubset(actual): raise RuntimeError(f'Existing user_sessions table requires reconciliation; missing columns: {sorted(session_cols-actual)}')
    else:
        op.create_table('user_sessions',sa.Column('session_id',sa.Integer(),primary_key=True,autoincrement=True),sa.Column('user_id',sa.Integer(),sa.ForeignKey('users.user_id',ondelete='CASCADE'),nullable=False),sa.Column('token_hash',sa.String(64),nullable=False),sa.Column('csrf_hash',sa.String(64),nullable=False),sa.Column('created_at',sa.DateTime(),nullable=False,server_default=sa.func.now()),sa.Column('expires_at',sa.DateTime(),nullable=False),sa.Column('revoked_at',sa.DateTime(),nullable=True),sa.Column('user_agent',sa.String(512),nullable=True),sa.Column('client_ip',sa.String(64),nullable=True))
    for name,table,cols,unique in [('ix_users_email','users',['email'],True),('ix_users_role','users',['role'],False),('ix_user_sessions_user_id','user_sessions',['user_id'],False),('ix_user_sessions_token_hash','user_sessions',['token_hash'],True),('ix_user_sessions_expires_at','user_sessions',['expires_at'],False)]:
        indexes={i['name'] for i in inspect(bind).get_indexes(table)}
        if name not in indexes: op.create_index(name,table,cols,unique=unique)
def downgrade():
    raise RuntimeError('Authentication downgrade would delete user/session records; perform a reviewed backup and migration plan.')
