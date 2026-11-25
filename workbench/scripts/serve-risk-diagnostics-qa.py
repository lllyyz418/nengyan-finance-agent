"""Isolated loopback QA for old/new report presentation, no production writes."""
import asyncio
import json
import sys
import uuid
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'backend'))
import app as core
from fastapi.staticfiles import StaticFiles
import uvicorn

qa_root=PROJECT/'artifacts/risk-diagnostics'
session_root=qa_root/('session-'+uuid.uuid4().hex)
for key in ('UPLOAD_DIR','CACHE_DIR','RESULT_DIR','TASK_DIR','LOG_DIR','BACKFILL_DIR','PARSED_DIR','COMPANY_DIR'):
    folder=session_root/key;folder.mkdir(parents=True,exist_ok=True);setattr(core,key,folder)
core.TASKS={}
core.get_report_library=lambda:None
core.library_online_enabled=lambda:False
core.generate_report=lambda analysis:(core.deterministic_report(analysis),'deterministic_template')
fixtures=[('修复后核验',qa_root/'verified-result.json'),
          ('旧结果兼容核验',PROJECT/'backend/data/results/ef059d8ccca24b0ab20b34a4ca06d60f.json')]
for label,path in fixtures:
    saved=json.loads(path.read_text(encoding='utf-8'));analysis=saved['analysis']
    task=core.create_task('upload',label+'.pdf',{'lookback_years':0,'minimum_negative_cash_quarters':3})
    result={'engine_version':'diagnostics-qa','analysis':analysis,'payload':{},
            'evidence':core.evidence_index(analysis),'report_markdown':core.deterministic_report(analysis),
            'report_generator':'deterministic_template'}
    core.update_task(task['id'],status='completed',progress=100,stage='本地核验完成',result=result)

@core.app.post('/__qa__/shutdown')
def shutdown():
    server.should_exit=True
    return {'stopping':True}

core.app.mount('/',StaticFiles(directory=qa_root/'build',html=True),name='diagnostics-qa')
server=uvicorn.Server(uvicorn.Config(core.app,host='127.0.0.1',port=5188,log_level='warning'))

async def main():
    asyncio.get_running_loop().call_later(900,setattr,server,'should_exit',True)
    print('Isolated diagnostics QA: http://127.0.0.1:5188/index.html',flush=True)
    await server.serve()

if __name__=='__main__':asyncio.run(main())
