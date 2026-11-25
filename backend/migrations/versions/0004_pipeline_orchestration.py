"""Persist detailed ETL run and stage results."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision='0004_pipeline_orchestration';down_revision='0003_user_management_audit';branch_labels=None;depends_on=None
def upgrade():
    op.add_column('pipeline_runs',sa.Column('finished_at',sa.DateTime(),nullable=True))
    op.add_column('pipeline_runs',sa.Column('actor_user_id',sa.Integer(),sa.ForeignKey('users.user_id'),nullable=True))
    op.add_column('pipeline_runs',sa.Column('input_rows',sa.Integer(),nullable=False,server_default='0'))
    op.add_column('pipeline_runs',sa.Column('accepted_rows',sa.Integer(),nullable=False,server_default='0'))
    op.add_column('pipeline_runs',sa.Column('duplicate_rows',sa.Integer(),nullable=False,server_default='0'))
    op.add_column('pipeline_runs',sa.Column('source_files',sa.JSON(),nullable=False,server_default=sa.text("'{}'::json")))
    op.add_column('pipeline_runs',sa.Column('validation_report',sa.JSON(),nullable=False,server_default=sa.text("'{}'::json")))
    op.add_column('pipeline_runs',sa.Column('errors',sa.JSON(),nullable=False,server_default=sa.text("'[]'::json")))
    op.add_column('pipeline_runs',sa.Column('warnings',sa.JSON(),nullable=False,server_default=sa.text("'[]'::json")))
    op.add_column('pipeline_runs',sa.Column('configuration',sa.JSON(),nullable=False,server_default=sa.text("'{}'::json")))
    op.create_index('ix_pipeline_runs_status','pipeline_runs',['status'])
    op.create_index('ix_pipeline_runs_actor_user_id','pipeline_runs',['actor_user_id'])
    op.create_table('pipeline_stages',sa.Column('stage_id',sa.Integer(),primary_key=True,autoincrement=True),sa.Column('run_id',sa.Integer(),sa.ForeignKey('pipeline_runs.run_id',ondelete='CASCADE'),nullable=False),sa.Column('stage_name',sa.String(32),nullable=False),sa.Column('stage_order',sa.Integer(),nullable=False),sa.Column('status',sa.String(20),nullable=False),sa.Column('started_at',sa.DateTime(),nullable=True),sa.Column('finished_at',sa.DateTime(),nullable=True),sa.Column('duration_ms',sa.Float(),nullable=False,server_default='0'),sa.Column('input_rows',sa.Integer(),nullable=False,server_default='0'),sa.Column('accepted_rows',sa.Integer(),nullable=False,server_default='0'),sa.Column('rejected_rows',sa.Integer(),nullable=False,server_default='0'),sa.Column('details',sa.JSON(),nullable=False,server_default=sa.text("'{}'::json")),sa.Column('error',sa.Text(),nullable=True))
    op.create_index('ix_pipeline_stages_run_id','pipeline_stages',['run_id'])
    op.create_index('uq_open_recommendation_product','replenishment_recommendations',['product_id'],unique=True,postgresql_where=sa.text("status = 'open'"))
def downgrade():
    raise RuntimeError('Pipeline history is retained; use an archival migration plan instead of dropping execution records.')
