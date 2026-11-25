from datetime import date

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from decimal import Decimal
from app.currency import api_native_inr_rows
from app.models import Base, AuditEvent, PipelineRun, PipelineStage, Product, Supplier, Sale, Inventory, User
from app.provision_demo_users import DEMO_USERS, provision
import app.pipeline as pipeline
from app.generate_data import generate
from app.auth import roles
from fastapi import HTTPException


def test_native_inr_api_serialization_does_not_apply_conversion():
    assert api_native_inr_rows([{"price": Decimal("149.00")}]) == [{"price": 149.0, "currency": "INR"}]


def test_mutation_role_policy_matrix():
    policies = {
        "POST /ingestion/{dataset}": ({"ADMIN", "DATA_ENGINEER"}, {"ADMIN", "DATA_ENGINEER"}),
        "POST /pipeline/run": ({"ADMIN", "DATA_ENGINEER"}, {"ADMIN", "DATA_ENGINEER"}),
        "PUT /inventory/{product_id}": ({"ADMIN", "INVENTORY_MANAGER"}, {"ADMIN", "INVENTORY_MANAGER"}),
    }
    for _, (allowed, expected) in policies.items():
        for role in ("ADMIN", "DATA_ENGINEER", "INVENTORY_MANAGER", "VIEWER"):
            actor = User(role=role)
            guard = roles(*allowed)
            if role in expected:
                assert guard(user=actor) is actor
            else:
                try:
                    guard(user=actor)
                except HTTPException as error:
                    assert error.status_code == 403
                else:
                    raise AssertionError(f"{role} unexpectedly passed {allowed}")


def test_demo_provisioning_is_idempotent_and_does_not_overwrite_accounts():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as db:
        db.add(User(email="admin@example.test", full_name="Existing Admin", password_hash="preserve-admin", role="ADMIN", is_active=True))
    generated = iter(("Strong-Temporary-Passphrase-123!", "Another-Strong-Temp-Password-987!", "Demo-Viewer-Temporary-Passphrase-456!"))
    def token_factory(_: int) -> str:
        return next(generated)
    first = provision(factory, token_factory, lambda raw: f"argon-hash:{raw}")
    assert [item["email"] for item in first] == [email for email, _, _ in DEMO_USERS]
    assert len({item["password"] for item in first}) == 3
    with factory() as db:
        records = list(db.scalars(select(User).where(User.email.in_([x[0] for x in DEMO_USERS]))))
        assert {user.role for user in records} == {"DATA_ENGINEER", "INVENTORY_MANAGER", "VIEWER"}
        assert all(user.must_change_password and user.is_active for user in records)
        assert all(user.password_hash.startswith("argon-hash:") for user in records)
        existing = records[0]
        existing.role = "VIEWER"
        existing.password_hash = "preserve-this-hash"
        db.commit()
    assert provision(factory, token_factory, lambda raw: f"argon-hash:{raw}") == []
    with factory() as db:
        user = db.scalar(select(User).where(User.email == DEMO_USERS[0][0]))
        assert user.role == "VIEWER"
        assert user.password_hash == "preserve-this-hash"


def test_pipeline_lock_rejects_concurrent_run_then_allows_retry(tmp_path, monkeypatch):
    source = tmp_path / "data"
    generate(source, seed=42, products=2, days=8, reference_date=date(2026, 11, 24))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(pipeline, "SessionLocal", factory)
    monkeypatch.setenv("PIPELINE_SOURCE_DIR", str(source))
    with factory.begin() as db:
        actor = User(email="de.demo@example.test", full_name="Data Engineer", password_hash="not-used", role="DATA_ENGINEER")
        db.add(actor)
        db.flush()
        run = PipelineRun(status="RUNNING", records_processed=0, rejected=0, duration_ms=0, actor_user_id=actor.user_id,
                          source_files={}, validation_report={}, errors=[], warnings=[], configuration={})
        db.add(run)
        db.flush()
        for order, name in enumerate(pipeline.STAGES, 1):
            db.add(PipelineStage(run_id=run.run_id, stage_name=name, stage_order=order, status="QUEUED",
                                 duration_ms=0, input_rows=0, accepted_rows=0, rejected_rows=0, details={}))
        run_id = run.run_id

    assert pipeline._local_lock.acquire(blocking=False)
    try:
        pipeline.execute_pipeline(run_id)
    finally:
        pipeline._local_lock.release()
    with factory() as db:
        failed = db.get(PipelineRun, run_id)
        assert failed.status == "FAILED"
        assert failed.errors == ["Another pipeline execution is already active"]
        assert db.scalar(select(func.count()).select_from(Supplier)) == 0
    pipeline.execute_pipeline(run_id)
    with factory() as db:
        assert db.get(PipelineRun, run_id).status == "SUCCESS"
        assert db.scalar(select(func.count()).select_from(Product)) == 2
        assert db.scalar(select(func.count()).select_from(Inventory)) == 2
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action_type == "pipeline.run")) == 1
