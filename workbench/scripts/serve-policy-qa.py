"""Isolated policy UI acceptance using checked parsed PDFs and marked fixtures."""
import asyncio,copy,hashlib,json,shutil,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import app as core
from financial_engine.core import analyze_parsed_documents
from financial_engine.policy_review import refresh_policy_reviews
from fastapi.staticfiles import StaticFiles
import uvicorn
OUT=ROOT/'artifacts/policy-reviews';session=OUT/('session-'+uuid.uuid4().hex)
original_cache=core.CACHE_DIR.resolve()
for key in ('TASK_DIR','RESULT_DIR','UPLOAD_DIR','CACHE_DIR','LOG_DIR','BACKFILL_DIR','PARSED_DIR','COMPANY_DIR'):
    directory=session/key;directory.mkdir(parents=True);setattr(core,key,directory)
core.TASKS={};core._CANCELLED_TASKS=set();core.get_report_library=lambda:None;core.library_online_enabled=lambda:False
core.generate_report=lambda analysis:(core.deterministic_report(analysis),'deterministic_template')
for code in ('300750','688032'):
    parsed=json.loads((OUT/f'{code}-parsed.json').read_text(encoding='utf8'))
    prior=json.loads((OUT/f'{code}-prior-parsed.json').read_text(encoding='utf8'))
    for doc in (parsed,prior):
        path=original_cache/doc['file_name']
        assert path.resolve().is_relative_to(original_cache)
        assert hashlib.sha256(path.read_bytes()).hexdigest()==doc['file_sha256']
        shutil.copy2(path,core.CACHE_DIR/path.name)
    analysis=analyze_parsed_documents([parsed]);previous=analyze_parsed_documents([prior])
    refresh_policy_reviews(analysis,[*analysis['documents'],*previous['documents']],[*analysis['facts'],*previous['facts']])
    analysis['warnings'].extend(parsed.get('parser_warnings',[]))
    analysis['support_documents']=previous['documents']
    result={'engine_version':'policy-qa','analysis':analysis,
            'payload':{'history':{'analysis':previous},'retrieval':{'files':[
                {'role':'target' if doc is parsed else 'history','status':'accepted','document_id':doc['document_id'],
                 'local_path':str(core.CACHE_DIR/doc['file_name'])} for doc in (parsed,prior)]}},
            'evidence':core.evidence_index(analysis),'report_markdown':core.deterministic_report(analysis),'report_generator':'deterministic_template'}
    (OUT/f'{code}-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    task=core.create_task('upload',parsed['company_name']+' · 政策复核验收.pdf',{'lookback_years':0})
    core.update_task(task['id'],file_path=str(core.CACHE_DIR/parsed['file_name']),status='completed',progress=100,stage='本地核验完成',result=result)

# Explicitly marked synthetic fixture exercises the new amber observation state.
from test_policy_reviews import document,rd_page,dep_page
synthetic=document(pages=[rd_page(),dep_page(change='changed')],company='合成测试样例（非真实财报）')
analysis=analyze_parsed_documents([synthetic])
task=core.create_task('upload','合成测试样例（非真实财报）.pdf')
core.update_task(task['id'],status='completed',progress=100,stage='界面测试',result={
    'engine_version':'policy-qa-synthetic','analysis':analysis,'payload':{},'evidence':core.evidence_index(analysis),
    'report_markdown':core.deterministic_report(analysis),'report_generator':'deterministic_template'})

@core.app.post('/__qa__/shutdown')
def shutdown():server.should_exit=True;return {'stopping':True}
core.app.mount('/',StaticFiles(directory=OUT/'build',html=True),name='policy-qa')
server=uvicorn.Server(uvicorn.Config(core.app,host='127.0.0.1',port=5190,log_level='warning'))
async def main():
    asyncio.get_running_loop().call_later(1200,setattr,server,'should_exit',True)
    print('Policy QA: http://127.0.0.1:5190/index.html',flush=True)
    await server.serve()
if __name__=='__main__':asyncio.run(main())
