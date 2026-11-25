"""Independent, versioned research jobs; never overwrite a financial snapshot."""
import copy
import json
import os
import re
import threading
import uuid
import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel
from research_context import build_context, compatible, primary
from research_writer import generate, markdown, PROMPT_VERSION

EXECUTOR=ThreadPoolExecutor(max_workers=2,thread_name_prefix='research-writer')
CAPACITY=threading.BoundedSemaphore(6)
LOCK=threading.Lock()
SESSION=uuid.uuid4().hex
IDENTIFIER=re.compile(r'^[a-f0-9]{32}$')
ACTIVE={'queued','processing'}


def now():return datetime.now(timezone.utc).isoformat()


def configured():return bool(os.getenv('DEEPSEEK_API_KEY','').strip())


def financial_task(core,identifier):
    if not IDENTIFIER.fullmatch(identifier):raise HTTPException(404,'分析任务不存在')
    task=core.task_by_id(identifier)
    if not task or task.get('trashed'):raise HTTPException(404,'分析任务不存在或已移入回收站')
    if task.get('status')!='completed' or not task.get('result'):raise HTTPException(409,'请先完成本期财务计算；研究解读不会替代财务计算。')
    return task


def report_root(core,task):
    seen={task['id']}
    while task.get('source') in {'history_backfill','peer_compare'} and len(seen)<16:
        candidate=task.get('backfill_root_id') or (task.get('request') or {}).get('base_task_id') or task.get('parent_task_id')
        if not candidate or candidate in seen:break
        seen.add(candidate);parent=core.task_by_id(candidate)
        if not parent or parent.get('trashed') or not compatible(primary(task),primary(parent)):break
        task=parent
    return task['id']


def directory(core,root):return core.RESULT_DIR.parent/'research_reports'/root


def read(path):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else None
    except (OSError,ValueError):return None


def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(f'.research-{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
        temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)


def records(core,root):
    folder=directory(core,root)
    paths=sorted((p for p in folder.glob('*.json') if IDENTIFIER.fullmatch(p.stem)),key=lambda p:p.stat().st_mtime_ns,reverse=True)
    result=[]
    for path in paths[:100]:
        item=read(path)
        if not item or item.get('root_task_id')!=root or item.get('id')!=path.stem:continue
        if item.get('status') in ACTIVE and item.get('session_id')!=SESSION:
            item={**item,'status':'interrupted','error':'服务曾停止，研究解读未完成；财务结果和历史版本仍保留，可重新生成。'}
        result.append(item)
    return sorted(result,key=lambda item:(item.get('created_at',''),item['id']),reverse=True)


def metadata(item):
    keys=('id','base_task_id','status','stage','created_at','updated_at','error','scope','prompt_version','model','data_signature')
    return {key:item.get(key) for key in keys}


def public_report(item):
    if not item:return None
    return {**metadata(item),'body':item.get('body'),'markdown':item.get('markdown','')}


def run(core,folder,job,packet):
    path=folder/f"{job['id']}.json"
    try:
        with LOCK:
            if (read(path) or {}).get('status')=='interrupted':return
            job={**job,'status':'processing','stage':'补充经营原文','updated_at':now()};save(path,job)
        def checkpoint():
            if (read(path) or {}).get('status')=='interrupted':raise RuntimeError('Research generation stopped')
        from research_enrichment import enrich
        packet=enrich(core,financial_task(core,job['base_task_id']),job['id'],packet,folder,checkpoint=checkpoint)
        checkpoint()
        save(folder/f"{job['id']}.input.json",packet)
        job={**job,'stage':'生成研究主线与验证条件',
             'input_snapshot_hash':hashlib.sha256(json.dumps(packet,ensure_ascii=False,sort_keys=True).encode()).hexdigest()}
        with LOCK:
            if (read(path) or {}).get('status')=='interrupted':return
            save(path,job)
        body=generate(packet)
        # The model never has authority over original saved reports or data.
        job={**job,'status':'completed','stage':'研究解读已保存','updated_at':now(),'body':body,'markdown':markdown(body)}
        with LOCK:
            if (read(path) or {}).get('status')=='interrupted':return
            save(path,job)
        try:core.log_event(job['base_task_id'],'success','研究解读完成','独立研究解读已另存版本，原财务结果未改写。')
        except OSError:pass
    except Exception as exc:
        kind=type(exc).__name__
        code='RESEARCH_VALIDATION_FAILED' if kind in {'ValueError','ValidationError','JSONDecodeError'} else 'RESEARCH_GENERATION_FAILED'
        job={**job,'status':'failed','updated_at':now(),
             'failure_code':code,'diagnostic_type':kind,
             'error':'研究解读生成未成功或未通过引用/数值校验，未展示未经核对的文字。财务摘要和已完成版本仍可查看，可重试。'}
        with LOCK:
            if (read(path) or {}).get('status')=='interrupted':return
            save(path,job)
        try:core.log_event(job['base_task_id'],'warning','研究解读未完成','财务摘要仍可用；'+code+' · '+kind)
        except OSError:pass
    finally:CAPACITY.release()


def start(core,identifier,scope='available'):
    task=financial_task(core,identifier)
    if not configured():raise HTTPException(409,'尚未配置 DeepSeek，财务摘要可正常使用。配置完成后可生成研究解读。')
    try:packet,signature=build_context(core,task,scope)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    # Prompt changes invalidate reusable prose without altering prior versions.
    signature=PROMPT_VERSION+':'+signature
    root=report_root(core,task);folder=directory(core,root)
    with LOCK:
        prior=records(core,root)
        for job in prior:
            if job['data_signature']==signature and job['status'] in ACTIVE|{'completed'}:
                return metadata(job)
        if any(job['status'] in ACTIVE for job in prior):raise HTTPException(409,'研究解读正在生成，请完成后再更新。')
        if not CAPACITY.acquire(blocking=False):raise HTTPException(503,'研究解读任务较多，请稍后重试；财务计算与摘要不受影响。')
        job={'id':uuid.uuid4().hex,'root_task_id':root,'base_task_id':identifier,'status':'queued',
             'created_at':now(),'updated_at':now(),'data_signature':signature,'scope':scope,
             'session_id':SESSION,'prompt_version':PROMPT_VERSION,
             'model':os.getenv('DEEPSEEK_MODEL','').strip() or 'deepseek-flash','error':None}
        try:
            save(folder/f"{job['id']}.input.json",packet)
            save(folder/f"{job['id']}.json",job)
            EXECUTOR.submit(run,core,folder,copy.deepcopy(job),packet)
        except Exception:
            CAPACITY.release()
            job.update(status='failed',error='研究任务未能启动，财务结果已保留。')
            save(folder/f"{job['id']}.json",job)
            raise HTTPException(503,job['error'])
    return metadata(job)


def automatic(core,task_id):
    if os.getenv('FINANCE_RESEARCH_AUTOGENERATE','1')!='1' or not configured():return
    try:start(core,task_id,'current')
    except Exception:
        # Model availability must never turn a saved financial task into failure.
        try:core.log_event(task_id,'warning','研究解读未启动','本期财务结果已保存；可在研究解读区手动生成。')
        except OSError:pass


class ResearchRequest(BaseModel):
    scope:str='available'


def excerpt_evidence(core,task_id,fact_id):
    match=re.fullmatch(r'research:([a-f0-9]{32}):(b\d{4})',fact_id)
    if not match:raise HTTPException(404,'经营原文证据不存在')
    task=financial_task(core,task_id);root=report_root(core,task)
    job=read(directory(core,root)/f'{match[1]}.json')
    if not job or job.get('root_task_id')!=root or job.get('status')!='completed':raise HTTPException(404,'该研究版本尚未保存有效原文')
    packet=read(directory(core,root)/f'{match[1]}.input.json') or {}
    point=next((p for p in packet.get('registry',{}).values() if p.get('kind')=='disclosure'
                and any(s.get('fact_id')==fact_id for s in p.get('sources') or [])),None)
    if not point:raise HTTPException(404,'该版本未引用这段经营原文')
    source=point['source_evidence']
    return {'id':fact_id,'fact_id':fact_id,'evidence_kind':'research','metric_code':point['label'],
        'file':source['file'],'file_hash':source['file_hash'],'document_id':source['document_id'],
        'parser_version':packet.get('business_source',{}).get('version'),'page':source['page'],
        'section':source['section'],'excerpt':source['excerpt'],'location':[],
        'original_field':point['label'],'original_value':'报告原文','original_unit':'文本',
        'normalized_value':source['statement_type'],'period_type':point['period_label'],
        'statement_scope':'issuer_disclosure','quality':'extracted','source_task_id':job['base_task_id']}


def excerpt_file(core,evidence):
    from pdf_runtime import digest
    task=core.task_by_id(evidence['source_task_id'])
    if not task or task.get('trashed'):return None
    path=core.evidence_file_path(task,{'document_id':evidence['document_id']})
    if not path or digest(path)!=evidence['file_hash']:return None
    return path


def register_research_routes(app,core):
    @app.get('/api/tasks/{task_id}/research')
    def summary(task_id:str):
        task=financial_task(core,task_id);root=report_root(core,task)
        prior=records(core,root);last=next((job for job in prior if job['status']=='completed'),None)
        try:
            packet,signature=build_context(core,task)
            signature=PROMPT_VERSION+':'+signature
            eligible=True;message=''
        except ValueError as exc:
            eligible=False;message=str(exc);packet={};signature=None
        return {'configured':configured(),'eligible':eligible,'message':message,
                'latest_job':metadata(prior[0]) if prior else None,
                'versions':[metadata(job) for job in prior[:30]],'report':public_report(last),
                'available_signature':signature,
                'new_data_available':bool(last and signature and signature!=last['data_signature']),
                'available_coverage':packet.get('coverage',[])}

    @app.post('/api/tasks/{task_id}/research',status_code=202)
    def create(task_id:str,request:ResearchRequest):
        if request.scope not in {'current','available'}:raise HTTPException(400,'请选择本期或已取得资料范围')
        return start(core,task_id,request.scope)

    @app.get('/api/tasks/{task_id}/research/{report_id}')
    def version(task_id:str,report_id:str):
        task=financial_task(core,task_id)
        if not IDENTIFIER.fullmatch(report_id):raise HTTPException(404,'研究版本不存在')
        root=report_root(core,task);job=read(directory(core,root)/f'{report_id}.json')
        if not job or job.get('root_task_id')!=root or job.get('status')!='completed':raise HTTPException(404,'该研究版本尚未完成或不存在')
        return public_report(job)

    @app.post('/api/tasks/{task_id}/research/{report_id}/cancel')
    def cancel(task_id:str,report_id:str):
        task=financial_task(core,task_id)
        if not IDENTIFIER.fullmatch(report_id):raise HTTPException(404,'研究任务不存在')
        root=report_root(core,task);path=directory(core,root)/f'{report_id}.json'
        with LOCK:
            job=read(path)
            if not job or job.get('root_task_id')!=root:raise HTTPException(404,'研究任务不存在')
            if job.get('status') in ACTIVE:
                job.update(status='interrupted',updated_at=now(),error='已停止本次研究解读；财务结果和已保存版本仍保留。')
                save(path,job)
        return metadata(job)
