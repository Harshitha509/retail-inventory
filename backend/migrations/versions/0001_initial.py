"""Initial normalized retail schema (auth is introduced by 0002)."""
from alembic import op
from app.models import Base
revision='0001_initial'; down_revision=None; branch_labels=None; depends_on=None
AUTH_TABLES={'users','user_sessions'}
def upgrade():
    # create_all is table-scoped here: never implicitly create future tables
    # before their own versioned migration runs.
    Base.metadata.create_all(bind=op.get_bind(),tables=[t for t in Base.metadata.sorted_tables if t.name not in AUTH_TABLES])
def downgrade():
    raise RuntimeError('The initial retail schema is a protected baseline; restore from a verified backup instead of dropping data.')
