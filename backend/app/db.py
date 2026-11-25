"""Database configuration. PostgreSQL is primary; SQLite is a local fallback."""
import os
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from .models import Base
from .config import validate_production_environment

DATABASE_URL=os.getenv('DATABASE_URL','sqlite:///./retail_inventory.db')
def make_engine(url=None):
    url=url or DATABASE_URL
    validate_production_environment()
    parsed=make_url(url)
    connect_args={"check_same_thread":False} if parsed.get_backend_name()=='sqlite' else {}
    if parsed.get_backend_name()=='postgresql':
        sslmode=parsed.query.get('sslmode') or os.getenv('DB_SSLMODE')
        if sslmode: parsed=parsed.update_query_dict({'sslmode':sslmode})
        url=parsed
    return create_engine(url,connect_args=connect_args,pool_pre_ping=True)
engine=make_engine()
SessionLocal=sessionmaker(bind=engine,autoflush=False,expire_on_commit=False)
def get_db():
    db=SessionLocal()
    try: yield db
    finally: db.close()
def init_db():
    """No implicit schema mutation; apply reviewed Alembic migrations explicitly."""
    return None
