"""Isolated offline UI/API acceptance server; never touches production task data."""
import asyncio,json,sys,uuid,shutil,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import app as core
from fastapi.staticfiles import StaticFiles
import uvicorn
OUT=ROOT/'artifacts/periodic-reports';session=OUT/('session-'+uuid.uuid4().hex)
for key in ('TASK_DIR','RESULT_DIR','UPLOAD_DIR','CACHE_DIR','LOG_DIR','BACKFILL_DIR','PARSED_DIR','COMPANY_DIR'):
    directory=session/key;directory.mkdir(parents=True);setattr(core,key,directory)
core.TASKS={};core._CANCELLED_TASKS=set();core.get_report_library=lambda:None;core.library_online_enabled=lambda:False
core.generate_report=lambda analysis:(core.deterministic_report(analysis),'deterministic_template')
for name,kind,quarter,year,report_id,day in [('q3','quarterly',3,2025,'1224721971','2025-10-21'),('h1','semiannual',None,2025,'1224342874','2025-07-31'),('q1','quarterly',1,2025,'1223094849','2025-04-15'),('q3','quarterly',3,2024,'1221432744','2024-10-19')]:
    source=OUT/f'catl-{year}-{name}.pdf';path=core.CACHE_DIR/f'300750_{year}_{report_id}.pdf';shutil.copy2(source,path)
    record={'stock_code':'300750','report_type':kind,'report_quarter':quarter,'report_year':year,'announcement_id':report_id,
            'title':f'{year}年'+('半年度报告' if kind=='semiannual' else '第一季度报告' if quarter==1 else '第三季度报告'),
            'source_url':f'https://static.cninfo.com.cn/finalpage/{day}/{report_id}.PDF','published_at':day+'T00:00:00+00:00',
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    path.with_suffix('.json').write_text(json.dumps(record),encoding='utf8')
@core.app.post('/__qa__/shutdown')
def shutdown():server.should_exit=True;return {'stopping':True}
core.app.mount('/',StaticFiles(directory=OUT/'build',html=True),name='periodic-qa')
server=uvicorn.Server(uvicorn.Config(core.app,host='127.0.0.1',port=5189,log_level='warning'))
async def main():
    asyncio.get_running_loop().call_later(1800,setattr,server,'should_exit',True)
    print('Periodic QA: http://127.0.0.1:5189/index.html',flush=True)
    await server.serve()
if __name__=='__main__':asyncio.run(main())
