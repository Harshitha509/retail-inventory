"""Persistent retail inventory API and CSV ingestion pipeline."""
from contextlib import asynccontextmanager
from datetime import date, timedelta, datetime
from io import BytesIO, StringIO
import csv, logging, os, time, re
import math
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import FastAPI, Depends, HTTPException, Query, UploadFile, File, Request, Response, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, func, delete, or_
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from .db import get_db, init_db
from .models import Base, Product, Supplier, Sale, Inventory, PipelineRun, PipelineStage, ReplenishmentRecommendation, User, UserSession, AuditEvent
from .auth import password_hash, verify_password, current_user, require_csrf, roles, login_rate_limit, create_session, clear_session_cookies, COOKIE, _digest, consume_rate_limit, clear_rate_limit, require_diagnostic_role, protected_diagnostics, MAX_FAILURES, WINDOW
from .currency import api_native_inr_rows, native_inr_amount
from .pipeline import execute_pipeline, serialize_run
from .config import validate_production_environment

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
log=logging.getLogger("retail.pipeline")
MAX_INGEST_ROWS=250_000
MAX_INVENTORY_UNITS=1_000_000

def _bounded_int(value:str,field:str,minimum:int=0,maximum:int=MAX_INVENTORY_UNITS)->int:
    try:numeric=float(value)
    except (ValueError,TypeError,OverflowError):raise ValueError(f'{field} must be a whole number') from None
    if not math.isfinite(numeric):raise ValueError(f'{field} must be a finite number')
    try:parsed=int(value)
    except (ValueError,TypeError,OverflowError):raise ValueError(f'{field} must be a whole number') from None
    if parsed<minimum or parsed>maximum:raise ValueError(f'{field} must be between {minimum} and {maximum}')
    return parsed

def _finite_float(value:str,field:str,minimum:float|None=None,maximum:float|None=None)->float:
    parsed=float(value)
    if not math.isfinite(parsed):raise ValueError(f'{field} must be a finite number')
    if minimum is not None and parsed<minimum:raise ValueError(f'{field} must be at least {minimum}')
    if maximum is not None and parsed>maximum:raise ValueError(f'{field} must be at most {maximum}')
    return parsed

def _money(value:str,field:str)->Decimal:
    try:parsed=Decimal(str(value).strip())
    except (InvalidOperation,ValueError,TypeError):raise ValueError(f'{field} must be a valid INR amount') from None
    if not parsed.is_finite():raise ValueError(f'{field} must be a finite INR amount')
    if parsed<0:raise ValueError(f'{field} must be non-negative')
    if parsed>Decimal('999999999999.99'):raise ValueError(f'{field} exceeds Numeric(14,2) range')
    if parsed!=parsed.quantize(Decimal('0.01')):raise ValueError(f'{field} must have at most two decimal places')
    return parsed.quantize(Decimal('0.01'))

@asynccontextmanager
async def lifespan(_: FastAPI):
    validate_production_environment()
    yield

app=FastAPI(title="Retail Inventory Intelligence",version="1.0.0",description="Persistent retail inventory analytics API",lifespan=lifespan)
_origins=[origin.strip() for origin in os.getenv("CORS_ORIGINS","http://localhost:5173").split(",") if origin.strip()]
if '*' in _origins: raise RuntimeError('Wildcard CORS origins are not allowed')
app.add_middleware(CORSMiddleware,allow_origins=_origins,allow_credentials=True,allow_methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"],allow_headers=["Content-Type","X-CSRF-Token"])

class PipelineRequest(BaseModel): safety_days:float=Field(5,ge=0,le=90); target_days:float=Field(30,ge=1,le=365)

class LoginBody(BaseModel): email:str=Field(min_length=3,max_length=320); password:str=Field(min_length=1,max_length=256)
class PasswordBody(BaseModel): current_password:str=Field(min_length=1,max_length=256); new_password:str=Field(min_length=12,max_length=256)
ROLES={'ADMIN','INVENTORY_MANAGER','DATA_ENGINEER','VIEWER'}
class UserCreateBody(BaseModel):
    email:str=Field(min_length=3,max_length=320); full_name:str=Field(min_length=1,max_length=160); role:str; initial_password:str=Field(min_length=12,max_length=256)
class UserUpdateBody(BaseModel): full_name:str|None=Field(default=None,min_length=1,max_length=160); role:str|None=None; is_active:bool|None=None

def write_audit(db:Session,action:str,actor:int|None=None,target_entity:str|None=None,target_id:str|None=None,outcome:str='success',details:dict|None=None):
    # Caller owns the transaction. Secret-bearing keys are rejected defensively.
    if details and any(k.lower() in {'password','password_hash','token','session_token','csrf','csrf_token','secret'} for k in details):
        raise ValueError('Sensitive audit metadata is prohibited')
    db.add(AuditEvent(actor_user_id=actor,action_type=action,target_entity=target_entity,target_id=target_id,outcome=outcome,details=details or {}))

def admin_user(user:User=Depends(current_user)):
    if user.must_change_password:raise HTTPException(403,'Change your temporary password before using this operation')
    if user.role!='ADMIN': raise HTTPException(403,'Administrator permission required')
    return user

@app.post('/auth/login')
def login(body:LoginBody,response:Response,request:Request,db:Session=Depends(get_db),rate_key:str=Depends(login_rate_limit)):
    email=body.email.strip().lower()
    if '@' not in email or len(email)>320: raise HTTPException(422,'Enter a valid email and password.')
    user=db.scalar(select(User).where(User.email==email))
    if not user or not verify_password(user.password_hash,body.password) or not user.is_active:
        consume_rate_limit(db,'login',rate_key,MAX_FAILURES,WINDOW); write_audit(db,'auth.login',user.user_id if user else None,'user',str(user.user_id) if user else None,'failure',{'reason':'invalid_credentials'});db.commit();raise HTTPException(401,'Invalid email or password')
    clear_rate_limit(db,'login',rate_key); user.last_login_at=datetime.utcnow(); write_audit(db,'auth.login',user.user_id,'user',str(user.user_id));create_session(response,user,request,db)
    return {'user':{'id':user.user_id,'email':user.email,'full_name':user.full_name,'role':user.role,'must_change_password':user.must_change_password}}

@app.post('/auth/logout')
def logout(response:Response,request:Request,db:Session=Depends(get_db),user:User=Depends(require_csrf)):
    from .auth import _digest
    raw=request.cookies.get(COOKIE,''); row=db.scalar(select(UserSession).where(UserSession.token_hash==_digest(raw),UserSession.user_id==user.user_id,UserSession.revoked_at.is_(None)))
    if row: row.revoked_at=datetime.utcnow()
    write_audit(db,'auth.logout',user.user_id,'user',str(user.user_id));db.commit()
    clear_session_cookies(response); return {'status':'logged_out'}

@app.get('/auth/me')
def auth_me(user:User=Depends(current_user)):
    return {'id':user.user_id,'email':user.email,'full_name':user.full_name,'role':user.role,'must_change_password':user.must_change_password}

@app.post('/auth/change-password')
def change_password(body:PasswordBody,request:Request,db:Session=Depends(get_db),user:User=Depends(require_csrf)):
    if not verify_password(user.password_hash,body.current_password): raise HTTPException(400,'Current password is incorrect')
    try: user.password_hash=password_hash(body.new_password)
    except ValueError as e: raise HTTPException(422,str(e)) from e
    for session in db.scalars(select(UserSession).where(UserSession.user_id==user.user_id,UserSession.revoked_at.is_(None))):session.revoked_at=datetime.utcnow()
    user.must_change_password=False;write_audit(db,'auth.password_change',user.user_id,'user',str(user.user_id));db.commit(); response=Response(status_code=204); clear_session_cookies(response); return response

def public_user(user:User):
    return {'id':user.user_id,'email':user.email,'full_name':user.full_name,'role':user.role,'is_active':user.is_active,'created_at':user.created_at.isoformat() if user.created_at else None,'last_login_at':user.last_login_at.isoformat() if user.last_login_at else None,'must_change_password':user.must_change_password}

@app.get('/users')
def list_users(q:str='',role:str|None=None,status:str|None=None,page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),db:Session=Depends(get_db),actor:User=Depends(admin_user)):
    stmt=select(User)
    if q.strip(): stmt=stmt.where(or_(User.email.ilike(f'%{q.strip()}%'),User.full_name.ilike(f'%{q.strip()}%')))
    if role:
        if role not in ROLES:raise HTTPException(422,'Invalid role filter')
        stmt=stmt.where(User.role==role)
    if status not in (None,'active','inactive'):raise HTTPException(422,'status must be active or inactive')
    if status:stmt=stmt.where(User.is_active==(status=='active'))
    total=db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    users=db.scalars(stmt.order_by(User.created_at.desc(),User.user_id.desc()).offset((page-1)*page_size).limit(page_size))
    return {'items':[public_user(u) for u in users],'total':total,'page':page,'page_size':page_size}

@app.post('/users',status_code=201)
def create_user(body:UserCreateBody,db:Session=Depends(get_db),actor:User=Depends(roles('ADMIN'))):
    email=body.email.strip().lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',email):raise HTTPException(422,'Enter a valid email address')
    if body.role not in ROLES:raise HTTPException(422,'Invalid role')
    if db.scalar(select(User).where(User.email==email)):raise HTTPException(409,'An account with that email already exists')
    try:hashed=password_hash(body.initial_password)
    except ValueError as e:raise HTTPException(422,str(e)) from e
    user=User(email=email,full_name=body.full_name.strip(),password_hash=hashed,role=body.role,is_active=True,must_change_password=True)
    db.add(user);db.flush();write_audit(db,'user.create',actor.user_id,'user',str(user.user_id),details={'role':user.role});db.commit();db.refresh(user)
    return public_user(user)

@app.patch('/users/{user_id}')
def update_user(user_id:int,body:UserUpdateBody,db:Session=Depends(get_db),actor:User=Depends(roles('ADMIN'))):
    target=db.get(User,user_id)
    if not target:raise HTTPException(404,'User not found')
    changes=body.model_dump(exclude_unset=True)
    if not changes:raise HTTPException(422,'Provide at least one update')
    if 'full_name' in changes:target.full_name=changes['full_name'].strip()
    if 'role' in changes:
        if changes['role'] not in ROLES:raise HTTPException(422,'Invalid role')
        if target.user_id==actor.user_id and changes['role']!='ADMIN':raise HTTPException(409,'You cannot change your own administrator role')
    if 'is_active' in changes and target.user_id==actor.user_id and changes['is_active'] is False:raise HTTPException(409,'You cannot deactivate your own account')
    role_change='role' in changes and changes['role']!=target.role
    deactivate=changes.get('is_active') is False and target.is_active
    would_remove_admin=target.role=='ADMIN' and target.is_active and (deactivate or (role_change and changes['role']!='ADMIN'))
    if would_remove_admin and (db.scalar(select(func.count(User.user_id)).where(User.role=='ADMIN',User.is_active.is_(True))) or 0)<=1:raise HTTPException(409,'The last active administrator cannot be removed or deactivated')
    before={'role':target.role,'is_active':target.is_active}
    if role_change:target.role=changes['role'];write_audit(db,'user.role_change',actor.user_id,'user',str(target.user_id),details={'from':before['role'],'to':target.role})
    if 'is_active' in changes:
        target.is_active=changes['is_active']
        if target.is_active!=before['is_active']:
            write_audit(db,'user.activate' if target.is_active else 'user.deactivate',actor.user_id,'user',str(target.user_id))
            if not target.is_active:
                for s in db.scalars(select(UserSession).where(UserSession.user_id==target.user_id,UserSession.revoked_at.is_(None))):s.revoked_at=datetime.utcnow()
    if 'full_name' in changes:write_audit(db,'user.update',actor.user_id,'user',str(target.user_id),details={'fields':['full_name']})
    db.commit();db.refresh(target);return public_user(target)

@app.post('/users/{user_id}/reset-password')
def admin_reset_password(user_id:int,body:dict,db:Session=Depends(get_db),actor:User=Depends(roles('ADMIN'))):
    if set(body)!={'new_password'} or not isinstance(body['new_password'],str):raise HTTPException(422,'new_password is required')
    target=db.get(User,user_id)
    if not target:raise HTTPException(404,'User not found')
    try:target.password_hash=password_hash(body['new_password'])
    except ValueError as e:raise HTTPException(422,str(e)) from e
    target.must_change_password=True
    for s in db.scalars(select(UserSession).where(UserSession.user_id==target.user_id,UserSession.revoked_at.is_(None))):s.revoked_at=datetime.utcnow()
    write_audit(db,'user.password_reset',actor.user_id,'user',str(target.user_id));db.commit();return {'status':'password_reset','must_change_password':True}

@app.get('/audit-logs')
def audit_logs(q:str='',action_type:str|None=None,actor_user_id:int|None=None,start_date:date|None=None,end_date:date|None=None,page:int=Query(1,ge=1),page_size:int=Query(25,ge=1,le=100),db:Session=Depends(get_db),actor:User=Depends(admin_user)):
    stmt=select(AuditEvent)
    if action_type:stmt=stmt.where(AuditEvent.action_type==action_type)
    if actor_user_id is not None:stmt=stmt.where(AuditEvent.actor_user_id==actor_user_id)
    if start_date:stmt=stmt.where(AuditEvent.occurred_at>=datetime.combine(start_date,datetime.min.time()))
    if end_date:stmt=stmt.where(AuditEvent.occurred_at<datetime.combine(end_date+timedelta(days=1),datetime.min.time()))
    if q.strip():stmt=stmt.where(or_(AuditEvent.action_type.ilike(f'%{q.strip()}%'),AuditEvent.target_entity.ilike(f'%{q.strip()}%'),AuditEvent.target_id.ilike(f'%{q.strip()}%')))
    total=db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items=db.scalars(stmt.order_by(AuditEvent.occurred_at.desc(),AuditEvent.event_id.desc()).offset((page-1)*page_size).limit(page_size))
    return {'items':[{'event_id':e.event_id,'actor_user_id':e.actor_user_id,'actor_name':db.get(User,e.actor_user_id).full_name if e.actor_user_id and db.get(User,e.actor_user_id) else None,'action_type':e.action_type,'target_entity':e.target_entity,'target_id':e.target_id,'occurred_at':e.occurred_at.isoformat() if e.occurred_at else None,'outcome':e.outcome,'details':e.details} for e in items],'total':total,'page':page,'page_size':page_size}

def _demand(db:Session, product_id:str, window:int=30)->tuple[float,int]:
    cutoff=date.today()-timedelta(days=window)
    units=db.scalar(select(func.coalesce(func.sum(Sale.quantity_sold),0)).where(Sale.product_id==product_id,Sale.transaction_date>=cutoff)) or 0
    count=db.scalar(select(func.count(Sale.transaction_id)).where(Sale.product_id==product_id,Sale.transaction_date>=cutoff)) or 0
    return float(units)/window, int(count)

def inventory_rows(db:Session,safety_days:float=5,target_days:float=30):
    stmt=select(Product,Inventory,Supplier).join(Inventory,Inventory.product_id==Product.product_id).outerjoin(Supplier,Supplier.supplier_id==Product.supplier_id).where(Product.status=='active')
    out=[]
    for p,inv,s in db.execute(stmt):
        daily,n=_demand(db,p.product_id); available=max(0,inv.available_stock-inv.reserved_stock); lead=(s.lead_time_days if s else p.lead_time_days)
        days=(available/daily if daily>0 else None); point=daily*((lead or 0)+safety_days)
        risk='Insufficient history' if n<7 else 'Critical' if available<=0 or (days is not None and days<=(lead or 0)) else 'High Risk' if days is not None and days<(lead or 0)+safety_days else 'Moderate' if days is not None and days<target_days else 'Healthy'
        out.append({'id':p.product_id,'name':p.product_name,'category':p.category,'price':p.unit_price,'supplier':s.supplier_name if s else None,'supplier_id':p.supplier_id,'lead_time':lead,'stock':inv.available_stock,'reserved':inv.reserved_stock,'available':available,'daily':round(daily,3),'sales_days':n,'days_remaining':round(days,1) if days is not None else None,'reorder_point':round(point),'reorder_threshold':p.reorder_threshold,'risk':risk})
    return out

@app.get('/health')
def health(db:Session=Depends(get_db)):
    try: db.execute(select(1)); return {'status':'ok','database':'connected','dataset':'database-backed; source may be synthetic demo data'}
    except Exception as e: log.exception('Database health check failed'); raise HTTPException(503,'Database unavailable') from e

@app.get('/products')
def products(q:str='',category:str|None=None,page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),sort_by:str='product_name',order:str='asc',db:Session=Depends(get_db)):
    allowed={'product_name':Product.product_name,'category':Product.category,'unit_price':Product.unit_price,'product_id':Product.product_id}
    if sort_by not in allowed: raise HTTPException(400,'Unsupported sort field')
    stmt=select(Product).where(Product.product_name.ilike(f'%{q}%'))
    if category: stmt=stmt.where(Product.category==category)
    total=db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    col=allowed[sort_by]; stmt=stmt.order_by(col.desc() if order=='desc' else col.asc()).offset((page-1)*page_size).limit(page_size)
    return {'items':[{'id':p.product_id,'name':p.product_name,'category':p.category,'price':native_inr_amount(p.unit_price),'currency':'INR','supplier_id':p.supplier_id,'lead_time':p.lead_time_days,'status':p.status} for p in db.scalars(stmt)],'total':total,'page':page,'page_size':page_size,'currency':'INR'}

@app.get('/inventory')
def inventory(q:str='',category:str|None=None,risk_level:str|None=None,page:int=Query(1,ge=1),page_size:int=Query(100,ge=1,le=500),db:Session=Depends(get_db)):
    rows=inventory_rows(db); rows=[r for r in rows if q.lower() in r['name'].lower() and (not category or r['category']==category) and (not risk_level or r['risk']==risk_level)]
    return {'items':api_native_inr_rows(rows[(page-1)*page_size:page*page_size]),'total':len(rows),'page':page,'page_size':page_size,'currency':'INR'}

@app.get('/inventory/risks')
def risks(db:Session=Depends(get_db)):
    return api_native_inr_rows([r for r in inventory_rows(db) if r['risk'] in ('Critical','High Risk','Insufficient history')])

@app.get('/sales/summary')
def sales_summary(db:Session=Depends(get_db)):
    cutoff=date.today()-timedelta(days=30); row=db.execute(select(func.coalesce(func.sum(Sale.quantity_sold),0),func.coalesce(func.sum(Sale.revenue),0)).where(Sale.transaction_date>=cutoff)).one()
    return {'units_30d':int(row[0]),'revenue_30d':native_inr_amount(row[1]),'avg_daily_units':round(float(row[0])/30,2),'currency':'INR'}

@app.get('/sales/trends')
def sales_trends(days:int=Query(30,ge=1,le=365),db:Session=Depends(get_db)):
    cutoff=date.today()-timedelta(days=days-1)
    rows=db.execute(select(Sale.transaction_date,func.sum(Sale.quantity_sold),func.sum(Sale.revenue)).where(Sale.transaction_date>=cutoff).group_by(Sale.transaction_date).order_by(Sale.transaction_date)).all()
    return [{'date':d.isoformat(),'units':int(u),'revenue':native_inr_amount(v),'currency':'INR'} for d,u,v in rows]

@app.get('/replenishment/recommendations')
def recommendations(db:Session=Depends(get_db),safety_days:float=Query(5,ge=0,le=90),target_days:float=Query(30,ge=1,le=365)):
    current={row['id']:row for row in inventory_rows(db,safety_days,target_days)}
    records=db.scalars(select(ReplenishmentRecommendation).where(ReplenishmentRecommendation.status=='open').order_by(ReplenishmentRecommendation.recommendation_id))
    return api_native_inr_rows([{**current[record.product_id],'recommendation_id':record.recommendation_id,'quantity':record.recommended_quantity,'risk_level':record.risk_level,'status':record.status,'reason':record.reason} for record in records if record.product_id in current])

@app.get('/analytics/overview')
def overview(db:Session=Depends(get_db)):
    rows=inventory_rows(db); counts={k:sum(x['risk']==k for x in rows) for k in ['Critical','High Risk','Moderate','Healthy','Insufficient history']}; covers=[x['days_remaining'] for x in rows if x['days_remaining'] is not None]
    recommendation_count=db.scalar(select(func.count()).select_from(ReplenishmentRecommendation).where(ReplenishmentRecommendation.status=='open')) or 0
    return {'products':len(rows),'at_risk':counts['Critical']+counts['High Risk'],'critical':counts['Critical'],'coverage':round(sum(covers)/len(covers)) if covers else 0,'recommendations':recommendation_count,'risk_distribution':counts,'synthetic':True}

@app.get('/pipeline/status')
def pipeline_status(db:Session=Depends(get_db),user:User=Depends(protected_diagnostics)):
    last=db.scalar(select(PipelineRun).order_by(PipelineRun.run_id.desc()).limit(1))
    if not last:return {'status':'idle','last_run':None,'run':None}
    return {'status':last.status,'last_run':last.started_at.isoformat() if last.started_at else None,'run_id':last.run_id,'duration_ms':last.duration_ms,'records_processed':last.records_processed,'accepted_rows':last.accepted_rows,'rejected_rows':last.rejected,'duplicate_rows':last.duplicate_rows}

@app.get('/pipeline/history')
def pipeline_history(limit:int=Query(50,ge=1,le=500),db:Session=Depends(get_db),user:User=Depends(protected_diagnostics)):
    items=[]
    for run in db.scalars(select(PipelineRun).order_by(PipelineRun.run_id.desc()).limit(limit)):
        summary=serialize_run(run);summary.update(errors=[],warnings=[],validation_report={},configuration={},source_files={},actor_user_id=None,stages=[]);items.append(summary)
    return items

@app.get('/pipeline/runs/{run_id}')
def pipeline_run_detail(run_id:int,db:Session=Depends(get_db),user:User=Depends(require_diagnostic_role('ADMIN','DATA_ENGINEER'))):
    run=db.get(PipelineRun,run_id)
    if not run:raise HTTPException(404,'Pipeline run not found')
    return serialize_run(run)

@app.get('/pipeline/runs/{run_id}/validation')
def pipeline_validation(run_id:int,db:Session=Depends(get_db),user:User=Depends(require_diagnostic_role('ADMIN','DATA_ENGINEER'))):
    run=db.get(PipelineRun,run_id)
    if not run:raise HTTPException(404,'Pipeline run not found')
    return {'run_id':run_id,'status':run.status,'validation_report':run.validation_report or {}}

@app.get('/pipeline/runs/{run_id}/errors')
def pipeline_errors(run_id:int,db:Session=Depends(get_db),user:User=Depends(require_diagnostic_role('ADMIN','DATA_ENGINEER'))):
    run=db.get(PipelineRun,run_id)
    if not run:raise HTTPException(404,'Pipeline run not found')
    return {'run_id':run_id,'errors':run.errors or [],'warnings':run.warnings or [],'stages':[{'name':s.stage_name,'status':s.status,'error':s.error} for s in run.stages if s.error or s.status=='FAILED']}

SCHEMAS={'suppliers':(['supplier_id','supplier_name','lead_time_days','reliability'],Supplier),'products':(['product_id','product_name','category','unit_price','supplier_id','lead_time_days','reorder_threshold','status'],Product),'sales':(['transaction_id','product_id','quantity_sold','transaction_date','revenue'],Sale),'inventory':(['product_id','available_stock','reserved_stock','updated_at'],Inventory)}
def ingest_csv(db:Session, dataset:str, content:bytes):
    if dataset not in SCHEMAS: raise ValueError('dataset must be suppliers, products, sales, or inventory')
    required,model=SCHEMAS[dataset]
    try:text=content.decode('utf-8-sig')
    except UnicodeDecodeError as exc:raise ValueError('CSV must use UTF-8 encoding') from exc
    reader=csv.DictReader(StringIO(text))
    if not reader.fieldnames or len(reader.fieldnames)!=len(set(reader.fieldnames)) or not set(required).issubset(reader.fieldnames): raise ValueError(f'Missing required columns or duplicate headers: {sorted(set(required)-set(reader.fieldnames or []))}')
    accepted=[]; rejected=[]; seen={}; duplicates=0
    for line,row in enumerate(reader,2):
        if line>MAX_INGEST_ROWS+1: raise ValueError(f'CSV exceeds the {MAX_INGEST_ROWS:,} row upload limit')
        try:
            missing=next((column for column in required if row.get(column) is None or not str(row[column]).strip()),None)
            if missing:raise ValueError(f'{missing} is required')
            key=row['product_id'] if dataset=='inventory' else row['supplier_id'] if dataset=='suppliers' else row['transaction_id'] if dataset=='sales' else row['product_id']
            if dataset=='suppliers': values={'supplier_id':key,'supplier_name':row['supplier_name'].strip(),'lead_time_days':_bounded_int(row['lead_time_days'],'lead_time_days',0,365),'reliability':_finite_float(row['reliability'],'reliability',0,1)}
            elif dataset=='products': values={'product_id':key,'product_name':row['product_name'].strip(),'category':row['category'].strip(),'unit_price':_money(row['unit_price'],'unit_price'),'supplier_id':row['supplier_id'].strip(),'lead_time_days':_bounded_int(row['lead_time_days'],'lead_time_days',0,365),'reorder_threshold':_bounded_int(row['reorder_threshold'],'reorder_threshold'),'status':row['status'].strip()}
            elif dataset=='sales':
                try:transaction_date=date.fromisoformat(row['transaction_date'])
                except ValueError:raise ValueError('transaction_date must be a valid ISO date') from None
                # Sales rows contain totals but no transaction-specific unit price. Do
                # not compare historical totals with today's catalog price.
                values={'transaction_id':key,'product_id':row['product_id'].strip(),'quantity_sold':_bounded_int(row['quantity_sold'],'quantity_sold',1,MAX_INVENTORY_UNITS),'transaction_date':transaction_date,'revenue':_money(row['revenue'],'revenue')}
            else:
                try:updated_at=datetime.fromisoformat(row['updated_at'].replace('Z','+00:00')).replace(tzinfo=None)
                except ValueError:raise ValueError('updated_at must be a valid ISO datetime') from None
                values={'product_id':key,'available_stock':_bounded_int(row['available_stock'],'available_stock'),'reserved_stock':_bounded_int(row['reserved_stock'],'reserved_stock'),'updated_at':updated_at}
            if dataset=='inventory' and values['reserved_stock']>values['available_stock']: raise ValueError('reserved_stock cannot exceed available_stock')
            if dataset=='products' and values['status'] not in {'active','inactive','discontinued'}:raise ValueError('status must be active, inactive, or discontinued')
            if key in seen:
                duplicates+=1
                if values!=seen[key]:rejected.append({'row':line,'field':{'suppliers':'supplier_id','products':'product_id','sales':'transaction_id','inventory':'product_id'}[dataset],'reason':f'conflicting duplicate primary key {key}'})
                continue
            seen[key]=dict(values)
            values['_row_number']=line;accepted.append(values)
        except (ValueError,TypeError,OverflowError) as e:
            reason=str(e);field=next((column for column in required if reason.startswith(column+' ')),'record')
            rejected.append({'row':line,'field':field,'reason':reason})
    # Referential validation happens before writes. SQLAlchemy flush plus the route transaction
    # makes accepted rows atomic and rejected rows unable to leave partial changes.
    reference_field='supplier_id' if dataset=='products' else 'product_id'
    if dataset=='products':valid_references=set(db.scalars(select(Supplier.supplier_id)))
    elif dataset in {'sales','inventory'}:valid_references=set(db.scalars(select(Product.product_id)))
    else:valid_references=None
    if valid_references is not None:
        retained=[]
        for values in accepted:
            if values[reference_field] not in valid_references:
                rejected.append({'row':values['_row_number'],'field':reference_field,'reason':f"unknown {reference_field} reference"})
            else:retained.append(values)
        accepted=retained
    # Upsert semantics are retained for the established ingestion contract.
    for values in accepted:
        values.pop('_row_number',None)
        obj=db.get(model, values.get('transaction_id') or values.get('supplier_id') or values.get('product_id'))
        if obj is None: db.add(model(**values))
        else:
            for k,v in values.items(): setattr(obj,k,v)
    db.flush()
    return {'dataset':dataset,'accepted':len(accepted),'rejected':len(rejected),'duplicates':duplicates,'rejections':rejected}

@app.post('/ingestion/{dataset}')
async def upload(dataset:str,file:UploadFile=File(...),db:Session=Depends(get_db),user:User=Depends(roles('ADMIN','DATA_ENGINEER'))):
    consume_rate_limit(db,'upload',str(user.user_id),30,3600)
    if not file.filename or not file.filename.lower().endswith('.csv'): raise HTTPException(415,'Upload a CSV file')
    content=await file.read(50*1024*1024+1)
    if len(content)>50*1024*1024: raise HTTPException(413,'CSV file exceeds the 50 MiB upload limit')
    safe_filename=os.path.basename(file.filename.replace('\\','/'))[:255]
    start=time.perf_counter(); run=PipelineRun(status='running',records_processed=0,rejected=0,duration_ms=0,source_files={dataset:safe_filename});db.add(run);db.flush()
    try:
        result=ingest_csv(db,dataset,content); run.status='completed';run.records_processed=result['accepted'];run.rejected=result['rejected'];run.duration_ms=(time.perf_counter()-start)*1000;write_audit(db,'data.ingestion',user.user_id,'dataset',dataset,details={'accepted':result['accepted'],'rejected':result['rejected'],'duplicates':result['duplicates']});db.commit();return {**result,'run_id':run.run_id,'duration_ms':run.duration_ms}
    except ValueError as e:
        db.rollback(); log.warning('CSV rejected: %s',e); raise HTTPException(422,str(e))
    except Exception as e:
        db.rollback(); log.exception('CSV ingestion failed'); raise HTTPException(500,'Ingestion failed; transaction rolled back') from e

@app.post('/pipeline/run',status_code=202)
def run_pipeline(body:PipelineRequest,background_tasks:BackgroundTasks,db:Session=Depends(get_db),user:User=Depends(roles('ADMIN','DATA_ENGINEER'))):
    consume_rate_limit(db,'pipeline',str(user.user_id),3,3600)
    run=PipelineRun(status='RUNNING',records_processed=0,rejected=0,duration_ms=0,actor_user_id=user.user_id,input_rows=0,accepted_rows=0,duplicate_rows=0,source_files={},validation_report={},errors=[],warnings=[],configuration={'safety_days':body.safety_days,'target_days':body.target_days})
    db.add(run);db.flush()
    for order,name in enumerate(('extract','validate','transform','load','analytics'),1):db.add(PipelineStage(run_id=run.run_id,stage_name=name,stage_order=order,status='QUEUED',duration_ms=0,input_rows=0,accepted_rows=0,rejected_rows=0,details={}))
    run_id=run.run_id;db.commit();background_tasks.add_task(execute_pipeline,run_id,body.safety_days,body.target_days)
    return {'run_id':run_id,'status':'RUNNING','status_url':f'/pipeline/runs/{run_id}'}

@app.put('/inventory/{product_id}')
def update_inventory(product_id:str,available_stock:int=Query(...,ge=0,le=MAX_INVENTORY_UNITS),reserved_stock:int=Query(0,ge=0,le=MAX_INVENTORY_UNITS),db:Session=Depends(get_db),user:User=Depends(roles('ADMIN','INVENTORY_MANAGER'))):
    if reserved_stock>available_stock:raise HTTPException(422,'reserved_stock cannot exceed available_stock')
    row=db.get(Inventory,product_id)
    if row is None: raise HTTPException(404,'Inventory record not found')
    row.available_stock=available_stock;row.reserved_stock=reserved_stock;row.updated_at=datetime.utcnow();write_audit(db,'inventory.update',user.user_id,'inventory',product_id,details={'available_stock':available_stock,'reserved_stock':reserved_stock});db.commit();return {'product_id':product_id,'available_stock':available_stock,'reserved_stock':reserved_stock}
