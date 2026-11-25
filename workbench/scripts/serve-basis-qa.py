"""Isolated, read-only workbench QA using copied public-report snapshots."""
import asyncio
import copy
import json
import os
import sys
import uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import app as core
import uvicorn
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

identifier=sys.argv[1]
assert len(identifier)==32 and all(c in '0123456789abcdef' for c in identifier)
sample=core.task_by_id(identifier);assert sample and sample.get('status')=='completed'
copies={identifier:copy.deepcopy(sample)};cursor=sample
while len(copies)<10:
    parent=cursor.get('parent_task_id') or cursor.get('backfill_root_id')
    if not parent or parent in copies:break
    cursor=core.task_by_id(parent)
    if not cursor:break
    copies[parent]=copy.deepcopy(cursor)
session=ROOT/'artifacts/history-basis'/('preview-'+uuid.uuid4().hex)
for name in ('TASK_DIR','RESULT_DIR','LOG_DIR'):
    path=session/name;path.mkdir(parents=True);setattr(core,name,path)
for key,value in copies.items():(core.TASK_DIR/f'{key}.json').write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
core.TASKS=copies;core._CANCELLED_TASKS=set()
os.environ['DEEPSEEK_API_KEY']='';os.environ['FINANCE_RESEARCH_AUTOGENERATE']='0'

@core.app.middleware('http')
async def read_only(request,call_next):
    if request.method not in {'GET','HEAD','OPTIONS'} and request.url.path!='/__qa__/shutdown':
        return JSONResponse({'detail':'只读验收，不提交任务或修改历史记录。'},status_code=405)
    return await call_next(request)

@core.app.post('/__qa__/shutdown')
def shutdown():server.should_exit=True;return {'stopping':True}

core.app.mount('/',StaticFiles(directory=ROOT/'artifacts/history-basis/build',html=True),name='basis-preview')
server=uvicorn.Server(uvicorn.Config(core.app,host='127.0.0.1',port=5191,log_level='warning'))
async def main():
    asyncio.get_running_loop().call_later(1200,setattr,server,'should_exit',True)
    print('History basis QA: http://127.0.0.1:5191/index.html',flush=True)
    print('Isolated fixture: '+str(session),flush=True)
    await server.serve()
if __name__=='__main__':asyncio.run(main())
