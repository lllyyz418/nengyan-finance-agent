"""Serve a completed isolated acceptance sample; never start a paid model job."""
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import app as core
import uvicorn
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

session=Path(sys.argv[1]).resolve()
assert session.is_relative_to((ROOT/'artifacts/research-reports').resolve()) and (session/'acceptance.json').is_file()
for name in ('TASK_DIR','RESULT_DIR','LOG_DIR'):setattr(core,name,session/name)
core.TASKS={};core._CANCELLED_TASKS=set()
os.environ['DEEPSEEK_API_KEY']='';os.environ['FINANCE_RESEARCH_AUTOGENERATE']='0'
# The public acceptance snapshot was created without uploading another PDF.
# Bind its matching cached original in memory only; never rewrite the snapshot.
metadata=json.loads((session/'acceptance.json').read_text(encoding='utf-8'))
sample=core.task_by_id(metadata['base_task_id'])
document=sample['result']['analysis']['documents'][0]
source=core.CACHE_DIR/document['file_name']
if source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest()==document.get('file_sha256'):
    sample['file_path']=str(source)
    core.TASKS[sample['id']]=sample

@core.app.post('/__qa__/shutdown')
def shutdown():server.should_exit=True;return {'stopping':True}

@core.app.middleware('http')
async def read_only_preview(request,call_next):
    if request.method not in {'GET','HEAD','OPTIONS'} and request.url.path!='/__qa__/shutdown':
        return JSONResponse({'detail':'只读验收预览，不创建或修改分析任务。'},status_code=403)
    return await call_next(request)

core.app.mount('/',StaticFiles(directory=ROOT/'artifacts/research-reports/build',html=True),name='research-preview')
server=uvicorn.Server(uvicorn.Config(core.app,host='127.0.0.1',port=5190,log_level='warning'))
async def main():
    asyncio.get_running_loop().call_later(900,setattr,server,'should_exit',True)
    print('Research QA: http://127.0.0.1:5190/index.html',flush=True)
    await server.serve()
if __name__=='__main__':asyncio.run(main())
