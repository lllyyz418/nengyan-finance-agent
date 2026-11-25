"""Read real source PDFs; benchmark only an isolated copy of task snapshots."""
import json
import shutil
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import app as core
from fastapi.testclient import TestClient
from pdf_runtime import extract

OUT=ROOT/'artifacts/responsive-analysis'
session=OUT/('acceptance-'+uuid.uuid4().hex);tasks=session/'tasks';tasks.mkdir(parents=True)
original_tasks=core.TASK_DIR
for path in original_tasks.glob('*.json'):shutil.copy2(path,tasks/path.name)
core.TASK_DIR=tasks;core.TASKS={}
client=TestClient(core.app)
timings=[]


def history(label):
    started=time.monotonic()
    response=client.get('/api/history',params={'page_size':10})
    response.raise_for_status()
    row={'label':label,'seconds':round(time.monotonic()-started,3),'total':response.json()['total']}
    timings.append(row);print(json.dumps(row),flush=True)
    assert row['seconds']<(10 if label=='cold-summary-index' else 5),row


history('cold-summary-index')
history('warm-summary-index')
if '--real-pdf' in sys.argv:
    library=core.get_report_library()
    source=library.provide('300750',2023)['local_path']
    progress_time=[0]
    def progress(detail):
        if time.monotonic()-progress_time[0]>20:
            print(json.dumps(detail,ensure_ascii=False),flush=True);progress_time[0]=time.monotonic()
    started=time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as executor:
        job=executor.submit(extract,'annual',source,session/'parsed',progress=progress)
        while not job.done():
            history('while-real-pdf-parsing')
            try:job.result(timeout=5)
            except TimeoutError:
                if job.done():raise
        parsed=job.result()
    assert parsed['report_year']==2023 and parsed['report_type']=='annual'
    first_seconds=round(time.monotonic()-started,3)
    started=time.monotonic();again=extract('annual',source,session/'parsed')
    assert again==parsed
    print(json.dumps({'first_parse_seconds':first_seconds,'cached_parse_seconds':round(time.monotonic()-started,3),'rows':len(parsed['rows'])}),flush=True)
    (session/'real-parsed.json').write_text(json.dumps(parsed,ensure_ascii=False,indent=2),encoding='utf-8')
(session/'timings.json').write_text(json.dumps(timings,ensure_ascii=False,indent=2),encoding='utf-8')
print('Acceptance saved: '+str(session),flush=True)
