"""Rebuild the isolated acceptance prose packet, without contacting a model."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import app as core
import research_reports as jobs
import pdf_runtime
from research_context import build_context
from research_enrichment import enrich
session=Path(sys.argv[1]).resolve();assert session.is_relative_to((ROOT/'artifacts/research-reports').resolve())
for name in ('TASK_DIR','RESULT_DIR','LOG_DIR'):setattr(core,name,session/name)
core.TASKS={}
core.PARSED_DIR=ROOT/'artifacts/research-reports/source-cache'
task=core.task_by_id(next(core.TASK_DIR.glob('*.json')).stem)
packet,_=build_context(core,task,'current')
original=pdf_runtime.extract
def diagnostic(*args,**kwargs):
    try:return original(*args,**kwargs)
    except Exception as exc:
        print(type(exc).__name__,str(exc),getattr(exc,'code',None),flush=True);raise
pdf_runtime.extract=diagnostic
identifier=next((session/'research_reports'/task['id']).glob('*.input.json')).name.split('.')[0]
enriched=enrich(core,task,identifier,packet,session/'research_reports'/task['id'])
jobs.save(session/'recovered-input.json',enriched)
print(json.dumps(enriched['business_context'],ensure_ascii=False),flush=True)
