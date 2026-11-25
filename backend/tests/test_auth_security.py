from datetime import datetime, timedelta
from decimal import Decimal
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from argon2 import PasswordHasher

from app.models import Base, User, UserSession, Inventory, Product, AuditEvent, PipelineRun, Sale, RateLimitEvent
from app.db import get_db
from app.main import app
from app.auth import password_hash, verify_password

@pytest.fixture(autouse=True)
def fast_test_hasher(monkeypatch):
    # Keep API tests quick while still exercising the Argon2 encoding and verification path.
    import app.auth as auth
    import app.main as main
    monkeypatch.setattr(auth,'ph',PasswordHasher(time_cost=1,memory_cost=1024,parallelism=1))
    monkeypatch.setattr(main,'execute_pipeline',lambda *args: None)

@pytest.fixture
def client_db():
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine); Factory=sessionmaker(bind=engine,expire_on_commit=False)
    def override():
        db=Factory()
        try:yield db
        finally:db.close()
    app.dependency_overrides[get_db]=override
    with Factory.begin() as db:
        for role in ['ADMIN','INVENTORY_MANAGER','DATA_ENGINEER','VIEWER']:
            db.add(User(email=f'{role.lower()}@example.test',full_name=role,password_hash=password_hash('long test password 123'),role=role,is_active=True))
        db.add(Product(product_id='SKU-X',product_name='Test',category='Test',unit_price=1,supplier_id='SUP-X',lead_time_days=2,reorder_threshold=2,status='active'))
        db.add(Inventory(product_id='SKU-X',available_stock=10,reserved_stock=0))
    with TestClient(app) as test:
        yield test,Factory
    app.dependency_overrides.clear()

def signin(client,role):
    return client.post('/auth/login',json={'email':f'{role.lower()}@example.test','password':'long test password 123'})

def test_argon2_hash_and_policy():
    hashed=password_hash('correct horse battery staple')
    assert hashed.startswith('$argon2') and hashed!='correct horse battery staple'
    assert verify_password(hashed,'correct horse battery staple')
    assert not verify_password(hashed,'incorrect')
    with pytest.raises(ValueError):password_hash('tiny')

def test_login_unknown_and_wrong_password_are_generic(client_db):
    client,_=client_db
    wrong=client.post('/auth/login',json={'email':'admin@example.test','password':'wrong'})
    unknown=client.post('/auth/login',json={'email':'nobody@example.test','password':'wrong'})
    assert wrong.status_code==unknown.status_code==401
    assert wrong.json()==unknown.json()=={'detail':'Invalid email or password'}

def test_login_creates_session_cookie_and_me(client_db):
    client,factory=client_db
    result=signin(client,'ADMIN')
    assert result.status_code==200 and 'password_hash' not in result.text
    cookie=result.cookies.get('fieldnote_session'); csrf=result.cookies.get('fieldnote_csrf')
    assert cookie and csrf
    set_cookie='\n'.join(result.headers.get_list('set-cookie'))
    assert 'fieldnote_session=' in set_cookie and 'httponly' in set_cookie.lower() and 'samesite=lax' in set_cookie.lower()
    with factory() as db:
        row=db.scalar(select(UserSession)); assert row and row.token_hash!=cookie and row.expires_at>datetime.utcnow()
    assert client.get('/auth/me').json()['role']=='ADMIN'

def test_secure_cookie_setting_is_honored(client_db,monkeypatch):
    import app.auth as auth
    client,_=client_db; monkeypatch.setattr(auth,'COOKIE_SECURE',True)
    result=signin(client,'VIEWER')
    assert result.status_code==200
    assert any('fieldnote_session=' in h and '; Secure' in h for h in result.headers.get_list('set-cookie'))

def test_session_expiry_and_logout_invalidate(client_db):
    client,factory=client_db; signin(client,'VIEWER')
    with factory.begin() as db: db.scalar(select(UserSession)).expires_at=datetime.utcnow()-timedelta(seconds=1)
    assert client.get('/auth/me').status_code==401
    client.cookies.clear(); signin(client,'VIEWER')
    csrf=client.cookies.get('fieldnote_csrf')
    assert client.post('/auth/logout',headers={'X-CSRF-Token':csrf}).status_code==200
    assert client.get('/auth/me').status_code==401

def test_csrf_missing_and_mismatch_rejected(client_db):
    client,_=client_db; signin(client,'ADMIN')
    assert client.post('/pipeline/run',json={}).status_code==403
    assert client.post('/pipeline/run',json={},headers={'X-CSRF-Token':'bad'}).status_code==403

def test_login_rate_limit(client_db):
    client,_=client_db
    for _ in range(8): client.post('/auth/login',json={'email':'missing@example.test','password':'bad'})
    assert client.post('/auth/login',json={'email':'missing@example.test','password':'bad'}).status_code==429

@pytest.mark.parametrize('role,endpoint,expected',[
 ('ADMIN','ingestion',200),('ADMIN','pipeline',202),('ADMIN','inventory',200),
 ('DATA_ENGINEER','ingestion',200),('DATA_ENGINEER','pipeline',202),('DATA_ENGINEER','inventory',403),
 ('INVENTORY_MANAGER','ingestion',403),('INVENTORY_MANAGER','pipeline',403),('INVENTORY_MANAGER','inventory',200),
 ('VIEWER','ingestion',403),('VIEWER','pipeline',403),('VIEWER','inventory',403),
])
def test_role_mutation_permissions(client_db,role,endpoint,expected):
    client,_=client_db; signin(client,role); csrf=client.cookies.get('fieldnote_csrf');headers={'X-CSRF-Token':csrf}
    if endpoint=='ingestion': result=client.post('/ingestion/suppliers',headers=headers,files={'file':('s.csv','supplier_id,supplier_name,lead_time_days,reliability\nSUP-T,Test Supplier,5,0.95\n')})
    elif endpoint=='pipeline': result=client.post('/pipeline/run',headers=headers,json={})
    else: result=client.put('/inventory/SKU-X?available_stock=20&reserved_stock=0',headers=headers)
    assert result.status_code==expected

def test_mutations_require_authentication(client_db):
    client,_=client_db
    assert client.post('/pipeline/run',json={}).status_code==401
    assert client.put('/inventory/SKU-X?available_stock=2').status_code==401
    assert client.post('/ingestion/sales',files={'file':('x.csv','x')}).status_code==401

def test_pipeline_submission_and_persisted_run_read_contract(client_db):
    client,factory=client_db;signin(client,'ADMIN');csrf=client.cookies.get('fieldnote_csrf')
    response=client.post('/pipeline/run',headers={'X-CSRF-Token':csrf},json={'safety_days':5,'target_days':30})
    assert response.status_code==202
    run_id=response.json()['run_id']
    assert client.get('/pipeline/status').json()['run_id']==run_id
    assert client.get('/pipeline/history').json()[0]['run_id']==run_id
    assert client.get(f'/pipeline/runs/{run_id}').json()['stages'][0]['name']=='extract'
    assert client.get(f'/pipeline/runs/{run_id}/validation').status_code==200
    assert client.get(f'/pipeline/runs/{run_id}/errors').json()['errors']==[]
    with factory.begin() as db:
        run=db.get(PipelineRun,run_id);run.validation_report={'metrics':{'rows_extracted':7,'valid_rows':4,'new_records_inserted':2,'existing_records_skipped':1,'records_updated':0,'rejected_rows':3}};run.errors=[{'dataset':'sales','row_number':5,'field':'transaction_date','description':'must be a valid ISO date in YYYY-MM-DD format'}]
    detail=client.get(f'/pipeline/runs/{run_id}').json()
    assert detail['metrics']=={'rows_extracted':7,'valid_rows':4,'new_records_inserted':2,'existing_records_skipped':1,'records_updated':0,'rejected_rows':3}
    assert detail['errors'][0]['dataset']=='sales' and detail['errors'][0]['row_number']==5
    assert client.get(f'/pipeline/runs/{run_id}/errors').json()['errors']==detail['errors']
    assert client.get('/pipeline/runs/99999').status_code==404

def test_overview_and_recommendations_use_persisted_records(client_db):
    from app.models import ReplenishmentRecommendation
    client,factory=client_db
    with factory.begin() as db:
        db.add(ReplenishmentRecommendation(product_id='SKU-X',recommended_quantity=17,risk_level='High Risk',status='open',reason='Persisted recommendation'))
    overview=client.get('/analytics/overview')
    assert overview.status_code==200 and overview.json()['recommendations']==1
    response=client.get('/replenishment/recommendations')
    assert response.status_code==200
    assert response.json()[0]['quantity']==17 and response.json()[0]['reason']=='Persisted recommendation'

def test_monetary_api_returns_native_inr_without_exchange_metadata(client_db):
    from datetime import date
    client,factory=client_db
    with factory.begin() as db:
        db.get(Product,'SKU-X').unit_price=149.99
        db.add(Sale(transaction_id='TX-INR',product_id='SKU-X',quantity_sold=2,transaction_date=date.today(),revenue=299.98))
    product=client.get('/products').json()['items'][0]
    inventory=client.get('/inventory').json()['items'][0]
    summary=client.get('/sales/summary').json()
    trends=client.get('/sales/trends').json()
    assert product['price']==149.99 and product['currency']=='INR' and 'conversion_rate_date' not in product
    assert inventory['price']==149.99 and inventory['currency']=='INR' and 'conversion_rate_date' not in inventory
    assert summary['revenue_30d']==299.98 and summary['currency']=='INR' and 'conversion_rate_date' not in summary
    assert trends[0]['revenue']==299.98 and trends[0]['currency']=='INR'
    with factory() as db:
        assert db.get(Product,'SKU-X').unit_price==Decimal('149.99')
        assert db.get(Sale,'TX-INR').revenue==Decimal('299.98')

def test_password_change_revokes_sessions(client_db):
    client,factory=client_db; signin(client,'ADMIN');csrf=client.cookies.get('fieldnote_csrf')
    response=client.post('/auth/change-password',headers={'X-CSRF-Token':csrf},json={'current_password':'long test password 123','new_password':'a much longer password 456'})
    assert response.status_code==204
    assert client.get('/auth/me').status_code==401
    with factory() as db: assert all(s.revoked_at is not None for s in db.scalars(select(UserSession)))

def test_admin_creates_user_and_audit_contains_no_password(client_db):
    client,factory=client_db;signin(client,'ADMIN');csrf=client.cookies.get('fieldnote_csrf')
    response=client.post('/users',headers={'X-CSRF-Token':csrf},json={'email':'new@example.test','full_name':'New User','role':'DATA_ENGINEER','initial_password':'temporary password 123'})
    assert response.status_code==201 and response.json()['must_change_password'] is True
    assert 'password' not in response.json() and 'password_hash' not in response.json()
    with factory() as db:
        user=db.scalar(select(User).where(User.email=='new@example.test'));event=db.scalar(select(AuditEvent).where(AuditEvent.action_type=='user.create'))
        assert user and user.password_hash!='temporary password 123' and event and 'temporary password 123' not in str(event.details)

def test_non_admin_cannot_list_users_and_self_role_change_rejected(client_db):
    client,_=client_db;signin(client,'VIEWER')
    assert client.get('/users').status_code==403
    client.cookies.clear();signin(client,'ADMIN');csrf=client.cookies.get('fieldnote_csrf')
    assert client.patch('/users/1',headers={'X-CSRF-Token':csrf},json={'role':'VIEWER'}).status_code==409
    assert client.patch('/users/1',headers={'X-CSRF-Token':csrf},json={'is_active':False}).status_code==409

def test_deactivating_user_revokes_sessions_and_prevents_login(client_db):
    admin,factory=client_db;viewer=TestClient(app);signin(viewer,'VIEWER');signin(admin,'ADMIN')
    token=admin.cookies.get('fieldnote_csrf')
    resp=admin.get('/users?role=VIEWER');assert resp.status_code==200
    viewer_id=resp.json()['items'][0]['id']
    result=admin.patch(f'/users/{viewer_id}',headers={'X-CSRF-Token':token},json={'is_active':False});assert result.status_code==200
    assert viewer.get('/auth/me').status_code==401
    assert viewer.post('/auth/login',json={'email':'viewer@example.test','password':'long test password 123'}).status_code==401
    with factory() as db:
        event=db.scalar(select(AuditEvent).where(AuditEvent.action_type=='user.deactivate'));assert event

def test_audit_endpoint_is_admin_only_and_failed_login_is_logged(client_db):
    client,factory=client_db
    client.post('/auth/login',json={'email':'missing@example.test','password':'wrong password'})
    with factory() as db:
        event=db.scalar(select(AuditEvent).where(AuditEvent.action_type=='auth.login',AuditEvent.outcome=='failure'))
        assert event and 'password' not in str(event.details).lower()
    signin(client,'VIEWER');assert client.get('/audit-logs').status_code==403

def test_forced_password_change_blocks_direct_protected_api_until_change(client_db):
    client,factory=client_db
    with factory.begin() as db:
        user=db.scalar(select(User).where(User.role=='DATA_ENGINEER'));user.must_change_password=True
    assert signin(client,'DATA_ENGINEER').status_code==200
    csrf=client.cookies.get('fieldnote_csrf');headers={'X-CSRF-Token':csrf}
    assert client.get('/pipeline/history').status_code==403
    assert client.post('/pipeline/run',headers=headers,json={}).status_code==403
    response=client.post('/auth/change-password',headers=headers,json={'current_password':'long test password 123','new_password':'updated password that is secure 123'})
    assert response.status_code==204
    login=client.post('/auth/login',json={'email':'data_engineer@example.test','password':'updated password that is secure 123'})
    assert login.status_code==200
    assert client.get('/pipeline/history').status_code==200
    csrf=client.cookies.get('fieldnote_csrf')
    assert client.post('/pipeline/run',headers={'X-CSRF-Token':csrf},json={}).status_code==202
    with factory() as db:assert db.scalar(select(User).where(User.role=='DATA_ENGINEER')).must_change_password is False

def test_pipeline_diagnostics_require_auth_and_role(client_db):
    client,factory=client_db
    with factory.begin() as db:
        run=PipelineRun(status='SUCCESS',records_processed=4,rejected=1,duration_ms=12,source_files={'sales':'private.csv'},validation_report={'private':'details'},errors=[{'description':'sensitive row value'}],warnings=[],configuration={});db.add(run);db.flush();run_id=run.run_id
    assert client.get('/pipeline/history').status_code==401
    signin(client,'VIEWER')
    history=client.get('/pipeline/history');assert history.status_code==200
    assert history.json()[0]['source_files']=={} and history.json()[0]['errors']==[] and history.json()[0]['actor_user_id'] is None
    assert client.get(f'/pipeline/runs/{run_id}').status_code==403
    assert client.get(f'/pipeline/runs/{run_id}/errors').status_code==403
    client.cookies.clear();signin(client,'DATA_ENGINEER')
    assert client.get(f'/pipeline/runs/{run_id}').status_code==200
    assert client.get(f'/pipeline/runs/{run_id}/errors').json()['errors'][0]['description']=='sensitive row value'

def test_shared_rate_limit_events_store_only_key_hash(client_db):
    client,factory=client_db
    for _ in range(8):client.post('/auth/login',json={'email':'missing@example.test','password':'bad'})
    with factory() as db:
        events=list(db.scalars(select(RateLimitEvent).where(RateLimitEvent.scope=='login')))
        assert len(events)==8 and all(len(event.key_hash)==64 and 'testclient' not in event.key_hash for event in events)

def test_inventory_update_ranges_and_reserved_relationship(client_db):
    client,_=client_db;signin(client,'INVENTORY_MANAGER');csrf=client.cookies.get('fieldnote_csrf');headers={'X-CSRF-Token':csrf}
    assert client.put('/inventory/SKU-X?available_stock=-1',headers=headers).status_code==422
    assert client.put('/inventory/SKU-X?available_stock=1000001',headers=headers).status_code==422
    assert client.put('/inventory/SKU-X?available_stock=3&reserved_stock=4',headers=headers).status_code==422
    assert client.put('/inventory/SKU-X?available_stock=8&reserved_stock=4',headers=headers).status_code==200

def test_upload_csv_rejects_nonfinite_stock_and_unknown_foreign_keys(client_db):
    client,_=client_db;signin(client,'DATA_ENGINEER');csrf=client.cookies.get('fieldnote_csrf');headers={'X-CSRF-Token':csrf}
    invalid='product_id,available_stock,reserved_stock,updated_at\nSKU-X,NaN,0,2026-11-24T00:00:00\nSKU-X,3,4,2026-11-24T00:00:00\nSKU-MISSING,1,0,2026-11-24T00:00:00\n'
    result=client.post('/ingestion/inventory',headers=headers,files={'file':('inventory.csv',invalid,'text/csv')})
    assert result.status_code==200 and result.json()['accepted']==0 and result.json()['rejected']==3
    assert any('finite number' in item['reason'] for item in result.json()['rejections'])
    assert any('cannot exceed' in item['reason'] for item in result.json()['rejections'])
    assert any(item['field']=='product_id' and 'unknown' in item['reason'] for item in result.json()['rejections'])

def test_csv_duplicate_ids_skip_identical_and_reject_conflicting_values(client_db):
    client,_=client_db;signin(client,'DATA_ENGINEER');csrf=client.cookies.get('fieldnote_csrf')
    csv_text='supplier_id,supplier_name,lead_time_days,reliability\nSUP-DUP,Demo,5,0.9\nSUP-DUP,Demo,5,0.9\nSUP-DUP,Altered,5,0.9\n'
    response=client.post('/ingestion/suppliers',headers={'X-CSRF-Token':csrf},files={'file':('suppliers.csv',csv_text,'text/csv')})
    assert response.status_code==200
    assert response.json()['accepted']==1 and response.json()['duplicates']==2 and response.json()['rejected']==1
    assert response.json()['rejections'][0]['field']=='supplier_id'

def test_expensive_pipeline_requests_are_limited_per_account(client_db):
    client,_=client_db;signin(client,'DATA_ENGINEER');csrf=client.cookies.get('fieldnote_csrf');headers={'X-CSRF-Token':csrf}
    assert [client.post('/pipeline/run',headers=headers,json={}).status_code for _ in range(3)]==[202,202,202]
    assert client.post('/pipeline/run',headers=headers,json={}).status_code==429
