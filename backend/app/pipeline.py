"""File-backed, validated retail ETL orchestration."""
from __future__ import annotations
import csv, math, os, threading, time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from .db import SessionLocal
from .models import Supplier,Product,Sale,Inventory,PipelineRun,PipelineStage,ReplenishmentRecommendation,AuditEvent

DATASETS={
 'suppliers':(['supplier_id','supplier_name','lead_time_days','reliability'],Supplier,'supplier_id'),
 'products':(['product_id','product_name','category','unit_price','supplier_id','lead_time_days','reorder_threshold','status'],Product,'product_id'),
 'sales':(['transaction_id','product_id','quantity_sold','transaction_date','revenue'],Sale,'transaction_id'),
 'inventory':(['product_id','available_stock','reserved_stock','updated_at'],Inventory,'product_id')}
STAGES=('extract','validate','transform','load','analytics')
MAX_FILE_BYTES=50*1024*1024;MAX_ROWS=250_000
_local_lock=threading.Lock();_LOCK_KEY=913570126

def configured_source_dir()->Path:
    configured=os.getenv('PIPELINE_SOURCE_DIR')
    root=Path(__file__).resolve().parents[2]
    folder=Path(configured).expanduser() if configured else root/'data'
    resolved=folder.resolve(strict=True)
    if not resolved.is_dir():raise ValueError('Configured pipeline source is not a directory')
    return resolved

def extract_sources(source:Path)->tuple[dict[str,list[dict[str,str]]],dict[str,dict[str,Any]]]:
    frames={};summary={}
    for name,(required,_,_) in DATASETS.items():
        path=(source/f'{name}.csv').resolve(strict=True)
        if path.parent!=source:raise ValueError(f'Invalid source path for {name}')
        if not path.is_file() or path.stat().st_size>MAX_FILE_BYTES:raise ValueError(f'{name}.csv is missing, unreadable, or exceeds the 50 MiB limit')
        with path.open('r',encoding='utf-8-sig',newline='') as stream:
            reader=csv.DictReader(stream)
            if not reader.fieldnames or not set(required).issubset(reader.fieldnames):raise ValueError(f'{name}.csv missing required columns: {sorted(set(required)-set(reader.fieldnames or []))}')
            rows=[]
            for row in reader:
                if len(rows)>=MAX_ROWS:raise ValueError(f'{name}.csv exceeds {MAX_ROWS} rows')
                rows.append(row)
        frames[name]=rows;summary[name]={'file':path.name,'bytes':path.stat().st_size,'rows':len(rows),'columns':reader.fieldnames}
    return frames,summary

class RowValidationError(ValueError):
    def __init__(self,field:str,description:str):
        super().__init__(description);self.field=field;self.description=description

def _float(value:str,field:str,minimum:float|None=None,maximum:float|None=None)->float:
    try:result=float(value)
    except (ValueError,TypeError,OverflowError):raise RowValidationError(field,'must be a valid number') from None
    if not math.isfinite(result):raise RowValidationError(field,'must be a finite number')
    if minimum is not None and result<minimum:raise RowValidationError(field,f'must be greater than or equal to {minimum:g}')
    if maximum is not None and result>maximum:raise RowValidationError(field,f'must be less than or equal to {maximum:g}')
    return result

def _money(value:str,field:str)->Decimal:
    try:result=Decimal(str(value).strip())
    except (InvalidOperation,ValueError,TypeError):raise RowValidationError(field,'must be a valid INR amount with at most two decimal places') from None
    if not result.is_finite():raise RowValidationError(field,'must be a finite INR amount')
    if result<0:raise RowValidationError(field,'must be greater than or equal to 0 INR')
    if result>Decimal('999999999999.99'):raise RowValidationError(field,'exceeds the supported Numeric(14,2) range')
    if result!=result.quantize(Decimal('0.01')):raise RowValidationError(field,'must have no more than two decimal places')
    return result.quantize(Decimal('0.01'))

def _int(value:str,field:str,minimum:int|None=None,maximum:int|None=1_000_000)->int:
    try:result=int(value)
    except (ValueError,TypeError,OverflowError):raise RowValidationError(field,'must be a whole number') from None
    if str(result)!=str(value).strip():raise RowValidationError(field,'must be a whole number')
    if minimum is not None and result<minimum:raise RowValidationError(field,f'must be greater than or equal to {minimum}')
    if maximum is not None and result>maximum:raise RowValidationError(field,f'must be less than or equal to {maximum}')
    return result

def _rejection(dataset:str,row_number:int,field:str,description:str,**extra)->dict[str,Any]:
    return {'dataset':dataset,'row_number':row_number,'field':field,'description':description,**extra}

def validate_transform(frames:dict[str,list[dict[str,str]]],db:Session)->dict[str,Any]:
    accepted={};rejected={};duplicates={};skipped_identical={};duplicate_candidates={}
    for name,(required,_,pk) in DATASETS.items():
        accepted[name]=[];rejected[name]=[];duplicates[name]=0;skipped_identical[name]=0;duplicate_candidates[name]=[];seen={}
        for line,row in enumerate(frames[name],2):
            try:
                missing=next((c for c in required if row.get(c) is None or not str(row[c]).strip()),None)
                if missing:raise RowValidationError(missing,'is required and cannot be blank')
                if name=='suppliers':
                    v={'supplier_id':row['supplier_id'].strip(),'supplier_name':row['supplier_name'].strip(),'lead_time_days':_int(row['lead_time_days'],'lead_time_days',0,365),'reliability':_float(row['reliability'],'reliability',0,1)}
                elif name=='products':
                    v={'product_id':row['product_id'].strip(),'product_name':row['product_name'].strip(),'category':row['category'].strip(),'unit_price':_money(row['unit_price'],'unit_price'),'supplier_id':row['supplier_id'].strip(),'lead_time_days':_int(row['lead_time_days'],'lead_time_days',0,365),'reorder_threshold':_int(row['reorder_threshold'],'reorder_threshold',0),'status':row['status'].strip().lower()}
                    if v['status'] not in {'active','inactive','discontinued'}:raise RowValidationError('status','must be active, inactive, or discontinued')
                elif name=='sales':
                    try:transaction_date=date.fromisoformat(row['transaction_date'].strip())
                    except (ValueError,TypeError):raise RowValidationError('transaction_date','must be a valid ISO date in YYYY-MM-DD format') from None
                    # The CSV has no transaction unit-price field. Historical sales may
                    # legitimately differ from today's catalog price, so validate money
                    # shape/range without deriving historical revenue from current price.
                    v={'transaction_id':row['transaction_id'].strip(),'product_id':row['product_id'].strip(),'quantity_sold':_int(row['quantity_sold'],'quantity_sold',1),'transaction_date':transaction_date,'revenue':_money(row['revenue'],'revenue')}
                else:
                    try:updated=datetime.fromisoformat(row['updated_at'].strip().replace('Z','+00:00')).replace(tzinfo=None)
                    except (ValueError,TypeError):raise RowValidationError('updated_at','must be a valid ISO datetime') from None
                    v={'product_id':row['product_id'].strip(),'available_stock':_int(row['available_stock'],'available_stock',0),'reserved_stock':_int(row['reserved_stock'],'reserved_stock',0),'updated_at':updated}
                    if v['reserved_stock']>v['available_stock']:raise RowValidationError('reserved_stock','cannot exceed available_stock')
                key=v[pk]
                comparable=dict(v)
                for field in ('supplier_name','product_name','category'):
                    if field in comparable:comparable[field]=' '.join(comparable[field].split())
                if key in seen:
                    duplicates[name]+=1
                    prior,prior_line=seen[key]
                    if comparable==prior:duplicate_candidates[name].append({'key':key,'row_number':line,'original_row':prior_line})
                    else:rejected[name].append(_rejection(name,line,pk,f'duplicate {pk} conflicts with row {prior_line}; the conflicting row was rejected',key=key))
                    continue
                seen[key]=(comparable,line);v['_row_number']=line;accepted[name].append(v)
            except RowValidationError as exc:rejected[name].append(_rejection(name,line,exc.field,exc.description))
            except (ValueError,TypeError,OverflowError) as exc:rejected[name].append(_rejection(name,line,'record',str(exc)))
    # Foreign key checks consider validated source records plus previously stored records.
    supplier_ids={x['supplier_id'] for x in accepted['suppliers']}|set(db.scalars(select(Supplier.supplier_id)))
    for key in ('products',):
        kept=[]
        for v in accepted[key]:
            if v['supplier_id'] not in supplier_ids:rejected[key].append(_rejection(key,v['_row_number'],'supplier_id',f"references unknown supplier '{v['supplier_id']}'",key=v['product_id']))
            else:kept.append(v)
        accepted[key]=kept
    product_ids={x['product_id'] for x in accepted['products']}|set(db.scalars(select(Product.product_id)))
    for key in ('sales','inventory'):
        kept=[]
        for v in accepted[key]:
            if v['product_id'] not in product_ids:rejected[key].append(_rejection(key,v['_row_number'],'product_id',f"references unknown product '{v['product_id']}'",key=v['transaction_id'] if key=='sales' else v['product_id']))
            else:kept.append(v)
        accepted[key]=kept
    for name,items in duplicate_candidates.items():
        accepted_keys={row[DATASETS[name][2]] for row in accepted[name]}
        for item in items:
            if item['key'] in accepted_keys:skipped_identical[name]+=1
            else:
                cause=next((r for r in rejected[name] if r.get('key')==item['key']),None)
                if cause:rejected[name].append(_rejection(name,item['row_number'],cause['field'],cause['description'],key=item['key']))
    report={name:{'dataset':name,'input':len(frames[name]),'valid':len(accepted[name])+skipped_identical[name],'rejected':len(rejected[name]),'duplicates':duplicates[name],'skipped_identical':skipped_identical[name],'inserted':0,'updated':0,'rejections':rejected[name]} for name in DATASETS}
    return {'accepted':accepted,'rejected':rejected,'duplicates':duplicates,'skipped_identical':skipped_identical,'duplicate_candidates':duplicate_candidates,'report':report}

def transform_records(validated:dict[str,Any])->dict[str,list[dict[str,Any]]]:
    """Canonicalize human-readable labels after type and relationship validation."""
    transformed={name:[dict(row) for row in rows] for name,rows in validated['accepted'].items()}
    def clean(value:str)->str:return ' '.join(value.split())
    for row in transformed['suppliers']:row['supplier_name']=clean(row['supplier_name'])
    for row in transformed['products']:
        row['product_name']=clean(row['product_name']);row['category']=clean(row['category'])
    return transformed

def _same(obj,values):
    return all(getattr(obj,k)==v for k,v in values.items())

def load_validated(db:Session,result:dict[str,Any])->dict[str,int]:
    inserted={n:0 for n in DATASETS};identical={n:0 for n in DATASETS};conflicts={n:[] for n in DATASETS};conflict_keys={n:set() for n in DATASETS}
    for name,(_,model,pk) in DATASETS.items():
        for row in result['accepted'][name]:
            values={k:v for k,v in row.items() if k!='_row_number'};existing=db.get(model,row[pk])
            if existing:
                if _same(existing,values):identical[name]+=1
                else:
                    conflicts[name].append(_rejection(name,row['_row_number'],pk,f"conflicts with existing {name[:-1] if name.endswith('s') else name} '{row[pk]}'; existing record was preserved",key=row[pk]))
                    conflict_keys[name].add(row[pk])
                continue
            db.add(model(**values));inserted[name]+=1
        db.flush()
    for name,items in conflicts.items():
        result['accepted'][name]=[row for row in result['accepted'][name] if row[DATASETS[name][2]] not in conflict_keys[name]]
        result['rejected'][name].extend(items);result['report'][name]['rejected']+=len(items);result['report'][name]['valid']-=len(items)
        pk=DATASETS[name][2]
        for duplicate in result['duplicate_candidates'][name]:
            if duplicate['key'] in conflict_keys[name]:
                item=_rejection(name,duplicate['row_number'],pk,f"duplicate {pk} conflicts with an existing record; the row was rejected",key=duplicate['key'])
                result['rejected'][name].append(item);result['report'][name]['rejected']+=1;result['report'][name]['valid']-=1;result['report'][name]['skipped_identical']-=1;result['skipped_identical'][name]-=1
    for name in DATASETS:result['report'][name]['inserted']=inserted[name];result['report'][name]['skipped_identical']+=identical[name]
    return {'inserted':inserted,'identical_existing':identical,'updated':{name:0 for name in DATASETS}}

def persist_recommendations(db:Session,rows:list[dict[str,Any]],target_days:float=30,safety_days:float=5)->dict[str,int]:
    wanted={r['id']:r for r in rows if r['risk'] not in ('Healthy','Insufficient history') and r['daily']>0}
    active={r.product_id:r for r in db.scalars(select(ReplenishmentRecommendation).where(ReplenishmentRecommendation.status=='open'))}
    created=updated=superseded=0
    for pid,row in wanted.items():
        qty=max(0,round(row['daily']*target_days-row['available']))
        recommendation=active.get(pid)
        reason=f"Available stock below lead-time demand plus {safety_days:g} safety days ({row['lead_time']} lead days); target is {target_days:g} days of demand."
        if recommendation:
            recommendation.recommended_quantity=qty;recommendation.risk_level=row['risk'];recommendation.reason=reason;updated+=1
        else:
            db.add(ReplenishmentRecommendation(product_id=pid,recommended_quantity=qty,risk_level=row['risk'],status='open',reason=reason));created+=1
    for pid,recommendation in active.items():
        if pid not in wanted:recommendation.status='superseded';superseded+=1
    db.flush();return {'created':created,'updated':updated,'superseded':superseded,'open':len(wanted)}

def _stage(db,run_id,name,order,status,**kwargs):
    s=db.scalar(select(PipelineStage).where(PipelineStage.run_id==run_id,PipelineStage.stage_name==name))
    if s is None:s=PipelineStage(run_id=run_id,stage_name=name,stage_order=order,status=status);db.add(s)
    s.status=status
    for key,value in kwargs.items():setattr(s,key,value)
    db.commit()

def execute_pipeline(run_id:int,safety_days:float=5,target_days:float=30):
    """Background task; persists progress at stage boundaries and loads atomically."""
    db=SessionLocal(); acquired=False; pg_lock=False; started=time.perf_counter()
    try:
        run=db.get(PipelineRun,run_id)
        if not run:return
        dialect=db.bind.dialect.name
        if dialect=='postgresql':
            pg_lock=bool(db.execute(text('SELECT pg_try_advisory_lock(:key)'),{'key':_LOCK_KEY}).scalar())
            acquired=pg_lock
        else: acquired=_local_lock.acquire(blocking=False)
        if not acquired:
            run.status='FAILED';run.finished_at=datetime.utcnow();run.duration_ms=(time.perf_counter()-started)*1000;run.errors=['Another pipeline execution is already active'];db.commit();return
        root=configured_source_dir()
        _stage(db,run_id,'extract',1,'RUNNING',started_at=datetime.utcnow())
        extract_started=time.perf_counter()
        frames,files=extract_sources(root);run.source_files=files;run.input_rows=sum(len(x) for x in frames.values())
        _stage(db,run_id,'extract',1,'SUCCESS',started_at=datetime.utcnow(),finished_at=datetime.utcnow(),duration_ms=(time.perf_counter()-extract_started)*1000,input_rows=run.input_rows,accepted_rows=run.input_rows,details={'files':files})

        _stage(db,run_id,'validate',2,'RUNNING',started_at=datetime.utcnow());t=time.perf_counter()
        validated=validate_transform(frames,db);run.validation_report=validated['report'];run.duplicate_rows=sum(validated['duplicates'].values())
        rej=sum(len(v) for v in validated['rejected'].values())
        _stage(db,run_id,'validate',2,'SUCCESS',started_at=datetime.utcnow(),finished_at=datetime.utcnow(),duration_ms=(time.perf_counter()-t)*1000,input_rows=run.input_rows,accepted_rows=sum(v['valid'] for v in validated['report'].values()),rejected_rows=rej,details={'validation':validated['report']})

        _stage(db,run_id,'transform',3,'RUNNING',started_at=datetime.utcnow());t=time.perf_counter()
        validated['accepted']=transform_records(validated)
        _stage(db,run_id,'transform',3,'SUCCESS',started_at=datetime.utcnow(),finished_at=datetime.utcnow(),duration_ms=(time.perf_counter()-t)*1000,input_rows=run.input_rows,accepted_rows=sum(len(v) for v in validated['accepted'].values()),details={'normalized_datasets':list(DATASETS),'transform':'trim/collapse whitespace in supplier and product labels'})

        _stage(db,run_id,'load',4,'RUNNING',started_at=datetime.utcnow());t=time.perf_counter()
        loaded=load_validated(db,validated)
        # All dataset writes and recommendations share one transaction; conflicts are rejected, never overwritten.
        db.flush()
        from .main import inventory_rows
        metrics=inventory_rows(db,safety_days,target_days)
        recommendations=persist_recommendations(db,metrics,target_days,safety_days)
        totals={key:sum(report[key] for report in validated['report'].values()) for key in ('input','valid','rejected','inserted','skipped_identical','updated')}
        metrics={'rows_extracted':totals['input'],'valid_rows':totals['valid'],'new_records_inserted':totals['inserted'],'existing_records_skipped':totals['skipped_identical'],'records_updated':totals['updated'],'rejected_rows':totals['rejected']}
        accepted=metrics['valid_rows'];rejected=metrics['rejected_rows']
        run.records_processed=metrics['rows_extracted'];run.accepted_rows=accepted;run.rejected=rejected;run.duplicate_rows=metrics['existing_records_skipped']
        run.validation_report={**validated['report'],'load':loaded,'recommendations':recommendations,'metrics':metrics}
        load_stage=db.scalar(select(PipelineStage).where(PipelineStage.run_id==run_id,PipelineStage.stage_name=='load'))
        load_stage.status='SUCCESS';load_stage.finished_at=datetime.utcnow();load_stage.duration_ms=(time.perf_counter()-t)*1000;load_stage.input_rows=run.input_rows;load_stage.accepted_rows=accepted;load_stage.rejected_rows=rejected;load_stage.details={'metrics':metrics,'load':loaded}
        analytics_stage=db.scalar(select(PipelineStage).where(PipelineStage.run_id==run_id,PipelineStage.stage_name=='analytics'))
        analytics_stage.status='SUCCESS';analytics_stage.started_at=datetime.utcnow();analytics_stage.finished_at=datetime.utcnow();analytics_stage.duration_ms=0;analytics_stage.input_rows=len(metrics);analytics_stage.accepted_rows=recommendations['open'];analytics_stage.details={'products_analyzed':len(metrics),'recommendations':recommendations}
        run.status='PARTIAL_SUCCESS' if rejected else 'SUCCESS';run.finished_at=datetime.utcnow();run.duration_ms=(time.perf_counter()-started)*1000
        run.errors=[item for name,items in validated['rejected'].items() for item in items]
        run.warnings=[f"{metrics['existing_records_skipped']} identical rows skipped idempotently; these rows were not inserted"] if metrics['existing_records_skipped'] else []
        db.commit()
        event=AuditEvent(actor_user_id=run.actor_user_id,action_type='pipeline.run',target_entity='pipeline_run',target_id=str(run_id),outcome=run.status.lower(),details={'input_rows':run.input_rows,'accepted_rows':accepted,'rejected_rows':rejected,'duration_ms':run.duration_ms});db.add(event);db.commit()
    except Exception as exc:
        db.rollback()
        run=db.get(PipelineRun,run_id)
        if run:
            safe_error=str(exc)[:300] if isinstance(exc,ValueError) else 'Pipeline stage failed; see server logs for diagnostic details.'
            run.status='FAILED';run.finished_at=datetime.utcnow();run.duration_ms=(time.perf_counter()-started)*1000;run.errors=[safe_error];db.commit()
            db.add(AuditEvent(actor_user_id=run.actor_user_id,action_type='pipeline.run',target_entity='pipeline_run',target_id=str(run_id),outcome='failure',details={'error_type':type(exc).__name__}));db.commit()
        # Record current failed stage for status details without exposing stack traces.
        active=db.scalar(select(PipelineStage).where(PipelineStage.run_id==run_id,PipelineStage.status=='RUNNING'))
        if active:active.status='FAILED';active.finished_at=datetime.utcnow();active.error=safe_error if 'safe_error' in locals() else 'Pipeline stage failed.';db.commit()
    finally:
        if pg_lock:
            try:db.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':_LOCK_KEY});db.commit()
            except Exception:db.rollback()
        elif acquired:_local_lock.release()
        db.close()

def serialize_run(run:PipelineRun):
    report=run.validation_report or {};metrics=report.get('metrics') or {'rows_extracted':run.input_rows or run.records_processed,'valid_rows':run.accepted_rows,'new_records_inserted':sum((report.get('load',{}).get('inserted') or {}).values()),'existing_records_skipped':run.duplicate_rows,'records_updated':0,'rejected_rows':run.rejected}
    errors=run.errors or []
    return {'run_id':run.run_id,'status':run.status,'started_at':run.started_at.isoformat() if run.started_at else None,'finished_at':run.finished_at.isoformat() if run.finished_at else None,'duration_ms':run.duration_ms,'records_processed':run.records_processed,'input_rows':run.input_rows,'accepted_rows':run.accepted_rows,'rejected_rows':run.rejected,'duplicate_rows':run.duplicate_rows,'metrics':metrics,'configuration':run.configuration or {},'source_files':run.source_files or {},'validation_report':report,'errors':errors,'warnings':run.warnings or [],'actor_user_id':run.actor_user_id,'stages':[{'name':s.stage_name,'order':s.stage_order,'status':s.status,'started_at':s.started_at.isoformat() if s.started_at else None,'finished_at':s.finished_at.isoformat() if s.finished_at else None,'duration_ms':s.duration_ms,'input_rows':s.input_rows,'accepted_rows':s.accepted_rows,'rejected_rows':s.rejected_rows,'details':s.details or {},'error':s.error} for s in run.stages]}
