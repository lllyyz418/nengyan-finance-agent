"""One explicit DeepSeek acceptance call; all writes isolated under artifacts."""
import json
import os
import sys
import time
import uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import app as core
import research_reports as jobs
from research_writer import generate
from fastapi.testclient import TestClient

OUT=ROOT/'artifacts/research-reports'
session=OUT/('acceptance-'+uuid.uuid4().hex)
for key in ('TASK_DIR','RESULT_DIR','LOG_DIR'):
    path=session/key;path.mkdir(parents=True);setattr(core,key,path)
core.TASKS={};core._CANCELLED_TASKS=set()
core.PARSED_DIR=OUT/'source-cache'
os.environ['FINANCE_RESEARCH_AUTOGENERATE']='0'
result=json.loads((ROOT/'artifacts/policy-reviews/300750-result.json').read_text(encoding='utf-8'))
task=core.create_task('upload','公开年报研究解读验收.pdf',{'lookback_years':0})
core.update_task(task['id'],status='completed',result=result,
    file_path=str(ROOT/'backend/data/report_cache/300750_2025_1225002214.pdf'))
before=(core.TASK_DIR/f"{task['id']}.json").read_bytes()
client=TestClient(core.app)


def audited_generate(packet):
    try:return generate(packet,trace=lambda attempt,content:(session/f'model-attempt-{attempt}.json').write_text(content or '',encoding='utf-8'))
    except ValueError as exc:
        print(json.dumps({'validation_error':str(exc)},ensure_ascii=False),flush=True)
        raise


jobs.generate=audited_generate
started=time.monotonic();metadata=jobs.start(core,task['id'],'current')
path=jobs.directory(core,task['id'])/f"{metadata['id']}.json"
previous=None
while time.monotonic()-started<380:
    job=jobs.read(path)
    if job['status']!=previous:
        print(json.dumps({'status':job['status'],'elapsed':round(time.monotonic()-started,2)}),flush=True)
        previous=job['status']
    assert client.get('/api/history').status_code==200
    assert client.get(f"/api/tasks/{task['id']}/result").status_code==200
    if job['status'] not in jobs.ACTIVE:break
    time.sleep(1)
assert job['status']=='completed',job.get('error')
assert job['body']['business_context']['status']=='available',job['body'].get('business_context')
assert before==(core.TASK_DIR/f"{task['id']}.json").read_bytes()
references={(source['task_id'],source['fact_id']) for section in job['body']['sections']
            for claim in section['claims'] for point in claim['evidence'] for source in point['sources']}
for identifier,fact in references:
    response=client.get(f'/api/tasks/{identifier}/evidence/{fact}')
    assert response.status_code==200,(identifier,fact)
    if fact.startswith('research:'):
        assert response.json()['source_available'] is True
        assert client.get(f'/api/tasks/{identifier}/file',params={'fact_id':fact}).status_code==200
assert any(fact.startswith('research:') for identifier,fact in references),'Model ignored the issuer prose'
(session/'research.md').write_text(job['markdown'],encoding='utf-8')
(session/'acceptance.json').write_text(json.dumps({'base_task_id':task['id'],'report_id':job['id'],
    'elapsed_seconds':round(time.monotonic()-started,3),'sections':len(job['body']['sections']),
    'claims':sum(len(section['claims']) for section in job['body']['sections']),'evidence_links':len(references),
    'business_context':job['body']['business_context'],
    'disclosure_links':sum(fact.startswith('research:') for identifier,fact in references),
    'financial_snapshot_unchanged':True},ensure_ascii=False,indent=2),encoding='utf-8')
print('Accepted: '+str(session),flush=True)
for section in job['body']['sections']:
    print(section['title']+': '+'；'.join(claim['headline'] for claim in section['claims']),flush=True)
