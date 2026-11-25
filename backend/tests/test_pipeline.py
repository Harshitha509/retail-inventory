from datetime import date
from pathlib import Path
import csv
import pytest
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.models import Base,PipelineRun,PipelineStage,Supplier,Product,Sale,Inventory,ReplenishmentRecommendation,User,AuditEvent
import app.pipeline as pipeline
from app.generate_data import generate

def _csv_rows(path):
    with path.open(newline='',encoding='utf-8') as stream:return list(csv.DictReader(stream))

def _append_csv(path,row):
    with path.open('a',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(row));writer.writerow(row)

@pytest.fixture
def pipeline_env(tmp_path,monkeypatch):
    source=tmp_path/'source';generate(source,seed=42,products=3,days=12,reference_date=date.today())
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    Base.metadata.create_all(engine);Factory=sessionmaker(bind=engine,expire_on_commit=False)
    monkeypatch.setattr(pipeline,'SessionLocal',Factory);monkeypatch.setenv('PIPELINE_SOURCE_DIR',str(source))
    with Factory.begin() as db:
        user=User(email='engineer@example.test',full_name='Engineer',password_hash='hash',role='DATA_ENGINEER',is_active=True);db.add(user);db.flush()
        run=PipelineRun(status='RUNNING',records_processed=0,rejected=0,duration_ms=0,actor_user_id=user.user_id,configuration={'safety_days':5,'target_days':30},source_files={},validation_report={},errors=[],warnings=[]);db.add(run);db.flush()
        for i,name in enumerate(pipeline.STAGES,1):db.add(PipelineStage(run_id=run.run_id,stage_name=name,stage_order=i,status='QUEUED',duration_ms=0,input_rows=0,accepted_rows=0,rejected_rows=0,details={}))
        run_id=run.run_id
    return Factory,source,run_id

def test_successful_run_loads_all_datasets_and_stages(pipeline_env):
    Factory,_,run_id=pipeline_env;pipeline.execute_pipeline(run_id)
    with Factory() as db:
        run=db.get(PipelineRun,run_id)
        assert run.status=='SUCCESS' and run.finished_at and run.duration_ms>0
        assert [s.status for s in run.stages]==['SUCCESS']*5
        assert run.input_rows>0 and run.accepted_rows==run.input_rows and run.rejected==0
        assert db.scalar(select(func.count()).select_from(Supplier))==8
        assert db.scalar(select(func.count()).select_from(Product))==3
        assert db.scalar(select(func.count()).select_from(Sale))>0
        assert db.scalar(select(func.count()).select_from(Inventory))==3
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action_type=='pipeline.run'))==1

def test_repeat_run_is_idempotent_and_does_not_duplicate_recommendations(pipeline_env):
    Factory,_,first_id=pipeline_env;pipeline.execute_pipeline(first_id)
    with Factory.begin() as db:
        run=PipelineRun(status='RUNNING',records_processed=0,rejected=0,duration_ms=0,source_files={},validation_report={},errors=[],warnings=[],configuration={});db.add(run);db.flush()
        for i,name in enumerate(pipeline.STAGES,1):db.add(PipelineStage(run_id=run.run_id,stage_name=name,stage_order=i,status='QUEUED',duration_ms=0,input_rows=0,accepted_rows=0,rejected_rows=0,details={}))
        second_id=run.run_id
    pipeline.execute_pipeline(second_id)
    with Factory() as db:
        assert db.scalar(select(func.count()).select_from(Supplier))==8
        assert db.scalar(select(func.count()).select_from(Product))==3
        assert db.scalar(select(func.count()).select_from(Sale))==db.scalar(select(func.count(Sale.transaction_id)))
        assert db.scalar(select(func.count()).select_from(ReplenishmentRecommendation).where(ReplenishmentRecommendation.status=='open'))<=3
        run=db.get(PipelineRun,second_id);assert run.status=='SUCCESS' and run.duplicate_rows>=run.input_rows

def test_missing_file_records_failure_without_loading(pipeline_env,monkeypatch):
    Factory,source,run_id=pipeline_env;(source/'sales.csv').unlink()
    pipeline.execute_pipeline(run_id)
    with Factory() as db:
        run=db.get(PipelineRun,run_id);assert run.status=='FAILED' and run.finished_at and run.errors
        assert db.scalar(select(func.count()).select_from(Supplier))==0

def test_invalid_rows_are_reported_and_valid_rows_can_load_partially(pipeline_env):
    Factory,source,run_id=pipeline_env
    with (source/'sales.csv').open('a',newline='') as f:f.write('TX-BAD,SKU-999,2,not-a-date,5\n')
    pipeline.execute_pipeline(run_id)
    with Factory() as db:
        run=db.get(PipelineRun,run_id);assert run.status=='PARTIAL_SUCCESS' and run.rejected>=1
        assert run.validation_report['sales']['rejected']>=1
        assert db.scalar(select(func.count()).select_from(Product))==3

def test_duplicate_primary_key_and_foreign_key_rows_are_rejected(pipeline_env):
    Factory,source,run_id=pipeline_env
    valid_product=_csv_rows(source/'products.csv')[0]['product_id']
    with (source/'sales.csv').open('a',newline='') as f:
        f.write(f'TX-EXTRA,{valid_product},2,2026-11-24,8\nTX-EXTRA,{valid_product},2,2026-11-24,8\nTX-FK,SKU-MISSING,1,2026-11-24,2\n')
    pipeline.execute_pipeline(run_id)
    with Factory() as db:
        run=db.get(PipelineRun,run_id);assert run.status=='PARTIAL_SUCCESS'
        assert run.duplicate_rows==1 and run.rejected==1

def test_load_failure_rolls_back_retail_writes(pipeline_env,monkeypatch):
    Factory,_,run_id=pipeline_env
    def fail(*args,**kwargs):raise RuntimeError('controlled test failure')
    monkeypatch.setattr(pipeline,'persist_recommendations',fail)
    pipeline.execute_pipeline(run_id)
    with Factory() as db:
        assert db.get(PipelineRun,run_id).status=='FAILED'
        assert db.scalar(select(func.count()).select_from(Supplier))==0
        assert db.scalar(select(func.count()).select_from(Product))==0

def test_invalid_rows_duplicate_policies_and_valid_rows_are_reported(pipeline_env):
    Factory,source,run_id=pipeline_env
    supplier=_csv_rows(source/'suppliers.csv')[0]
    product=_csv_rows(source/'products.csv')[0]
    sales_before=_csv_rows(source/'sales.csv');sale=sales_before[0]
    inventory=_csv_rows(source/'inventory.csv')[0]
    with Factory.begin() as db:db.add(Supplier(supplier_id='SUP-DB',supplier_name='Stored Supplier',lead_time_days=4,reliability=.9))

    # suppliers: a blank required field, exact duplicate, conflicting in-file ID, DB conflict, and valid new row
    _append_csv(source/'suppliers.csv',{'supplier_id':'SUP-MISSING','supplier_name':'','lead_time_days':'5','reliability':'0.9'})
    _append_csv(source/'suppliers.csv',supplier)
    _append_csv(source/'suppliers.csv',{**supplier,'supplier_name':'Conflicting duplicate supplier'})
    db_conflict={'supplier_id':'SUP-DB','supplier_name':'CSV changed supplier','lead_time_days':'4','reliability':'0.9'}
    _append_csv(source/'suppliers.csv',db_conflict);_append_csv(source/'suppliers.csv',db_conflict)
    _append_csv(source/'suppliers.csv',{'supplier_id':'SUP-NEW','supplier_name':'New Supplier','lead_time_days':'3','reliability':'0.95'})

    # products: negative price, invalid supplier FK, exact and conflicting duplicate IDs, and a valid new row
    _append_csv(source/'products.csv',{'product_id':'SKU-NEG','product_name':'Negative price','category':'Test','unit_price':'-2','supplier_id':'SUP-001','lead_time_days':'2','reorder_threshold':'1','status':'active'})
    _append_csv(source/'products.csv',{'product_id':'SKU-BADFK','product_name':'Bad supplier','category':'Test','unit_price':'1','supplier_id':'SUP-NOT-FOUND','lead_time_days':'2','reorder_threshold':'1','status':'active'})
    _append_csv(source/'products.csv',product)
    _append_csv(source/'products.csv',{**product,'product_name':'Conflicting duplicate product'})
    _append_csv(source/'products.csv',{'product_id':'SKU-NEW','product_name':'Valid new product','category':'Test','unit_price':'4.5','supplier_id':'SUP-NEW','lead_time_days':'3','reorder_threshold':'2','status':'active'})

    # sales: invalid date, negative quantity, invalid product FK, valid new row, exact duplicate, conflicting ID
    _append_csv(source/'sales.csv',{'transaction_id':'TX-DATE','product_id':'SKU-001','quantity_sold':'1','transaction_date':'2026-02-30','revenue':'1'})
    _append_csv(source/'sales.csv',{'transaction_id':'TX-NEG','product_id':'SKU-001','quantity_sold':'-1','transaction_date':'2026-11-24','revenue':'1'})
    _append_csv(source/'sales.csv',{'transaction_id':'TX-BADFK','product_id':'SKU-NOT-FOUND','quantity_sold':'1','transaction_date':'2026-11-24','revenue':'1'})
    valid_sale={'transaction_id':'TX-NEW','product_id':'SKU-NEW','quantity_sold':'2','transaction_date':'2026-11-24','revenue':'9'}
    _append_csv(source/'sales.csv',valid_sale);_append_csv(source/'sales.csv',valid_sale)
    _append_csv(source/'sales.csv',{**valid_sale,'quantity_sold':'3','revenue':'13.5'})

    # inventory: missing field, invalid product FK, and a valid record for the new product
    _append_csv(source/'inventory.csv',{'product_id':'SKU-MISSING-STOCK','available_stock':'','reserved_stock':'0','updated_at':'2026-11-24T00:00:00'})
    _append_csv(source/'inventory.csv',{'product_id':'SKU-NOT-FOUND','available_stock':'4','reserved_stock':'0','updated_at':'2026-11-24T00:00:00'})
    _append_csv(source/'inventory.csv',{'product_id':'SKU-NEW','available_stock':'40','reserved_stock':'2','updated_at':'2026-11-24T00:00:00'})

    extracted=sum(len(_csv_rows(source/f'{name}.csv')) for name in pipeline.DATASETS)
    pipeline.execute_pipeline(run_id)
    with Factory() as db:
        run=db.get(PipelineRun,run_id);report=run.validation_report;metrics=report['metrics']
        assert run.status=='PARTIAL_SUCCESS'
        assert metrics['rows_extracted']==extracted
        assert metrics['valid_rows']+metrics['rejected_rows']==extracted
        assert metrics['new_records_inserted']==sum(report[name]['inserted'] for name in pipeline.DATASETS)
        assert metrics['existing_records_skipped']==sum(report[name]['skipped_identical'] for name in pipeline.DATASETS)>0
        assert metrics['records_updated']==0
        assert metrics['rejected_rows']==run.rejected==len(run.errors)
        rejections=run.errors
        assert report['suppliers']['skipped_identical']>=1
        assert report['products']['skipped_identical']>=1
        assert report['sales']['skipped_identical']>=1
        assert not any(item['dataset']=='suppliers' and item['row_number']==11 for item in rejections)
        assert not any(item['dataset']=='products' and item['row_number']==7 for item in rejections)
        assert not any(item['dataset']=='sales' and item['row_number']==len(sales_before)+6 for item in rejections)
        assert db.get(Supplier,'SUP-NEW') and db.get(Product,'SKU-NEW')
        assert db.get(Sale,'TX-NEW') and db.get(Inventory,'SKU-NEW')
        assert db.get(Supplier,'SUP-DB').supplier_name=='Stored Supplier'
        assert db.get(Supplier,'SUP-MISSING') is None and db.get(Product,'SKU-NEG') is None and db.get(Product,'SKU-BADFK') is None
        assert db.get(Sale,'TX-DATE') is None and db.get(Sale,'TX-NEG') is None and db.get(Sale,'TX-BADFK') is None
        assert db.get(Inventory,'SKU-NOT-FOUND') is None and db.get(Inventory,'SKU-MISSING-STOCK') is None
        assert all({'dataset','row_number','field','description'}<=item.keys() for item in rejections)
        checks={
            ('suppliers','supplier_name','required'),('suppliers','supplier_id','duplicate'),
            ('products','unit_price','greater than or equal to 0'),('products','supplier_id','unknown supplier'),
            ('sales','transaction_date','ISO date'),('sales','quantity_sold','greater than or equal to 1'),
            ('sales','product_id','unknown product'),('inventory','available_stock','required'),
        }
        for dataset,field,phrase in checks:
            assert any(item['dataset']==dataset and item['field']==field and phrase in item['description'] for item in rejections)
        assert any(item['dataset']=='suppliers' and item.get('key')=='SUP-DB' and 'existing record was preserved' in item['description'] for item in rejections)
        assert sum(item['dataset']=='suppliers' and item.get('key')=='SUP-DB' for item in rejections)==2
        assert any(item['dataset']=='sales' and item.get('key')=='TX-NEW' and 'conflicting row was rejected' in item['description'] for item in rejections)
