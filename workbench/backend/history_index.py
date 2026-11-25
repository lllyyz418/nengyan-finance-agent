"""Small read-only history projections; never cache full financial results here.

Old task files are read once per file signature. No migration, PDF hashing or
model call is needed to list history. The bounded cache can be rebuilt on restart.
"""
import copy
import json
import re
import threading
import uuid
from collections import OrderedDict

CACHE_LOCK = threading.Lock()
CACHE = OrderedDict()
MAX_CACHED_TASKS = 1024
INDEX_VERSION = 1


def documents(analysis):
    fields = ('document_id','report_year','report_type','company_name','company_short_name',
              'file_sha256','security_code','stock_code','report_quarter')
    return [{key:doc[key] for key in fields if key in doc}
            for doc in analysis.get('documents') or [] if isinstance(doc,dict)]


def compact_task(task):
    """Copy only summary/identity/source metadata, not rows, facts or Markdown."""
    result=task.get('result') or {}
    analysis=result.get('analysis') or {};payload=result.get('payload') or {};scan=result.get('scan') or {}
    counts={'metrics':len(analysis.get('metrics') or []),
            'risks':sum(item.get('status')=='triggered' for item in analysis.get('risk_assessments') or []),
            'warnings':len(analysis.get('warnings') or []),
            'unavailable':len(analysis.get('unavailable_metrics') or []),
            'candidates':len(scan.get('candidates') or [])}
    fields=('id','source','file_name','status','stage','progress','created_at','updated_at',
            'session_id','error','company_name','stock_code','verified_stock_code','confirmed_peer_code',
            'parent_task_id','latest_backfill_task_id','latest_peer_task_id','favorite','note','trashed','file_path','request')
    small={key:copy.deepcopy(task[key]) for key in fields if key in task}
    small['_history_counts']=counts
    small['result']={} if not result else {
        'engine_version':result.get('engine_version'),
        'report_markdown':bool(result.get('report_markdown')),
        'report_identity':copy.deepcopy(result.get('report_identity') or {}),
        'history_backfill':copy.deepcopy(result.get('history_backfill') or {}),
        'analysis':{'documents':documents(analysis),'event':copy.deepcopy(analysis.get('event') or {}),
                    'review_status':analysis.get('review_status')},
        'scan':{key:scan[key] for key in ('company_name','report_year','file_sha256') if key in scan},
        'payload':{'status':payload.get('status'),'document_type':payload.get('document_type'),
                   'identity':copy.deepcopy(payload.get('identity') or {}),
                   'peer_binding':copy.deepcopy(payload.get('peer_binding') or {}),
                   'request':{'peer_codes':(payload.get('request') or {}).get('peer_codes')},
                   'input':{'file_sha256':(payload.get('input') or {}).get('file_sha256')},
                   'history':{'analysis':{'documents':documents((payload.get('history') or {}).get('analysis') or {})}},
                   'retrieval':{'files':[{key:record[key] for key in ('role','status','local_path','document_id','sha256') if key in record}
                       for record in (payload.get('retrieval') or {}).get('files') or []
                       if record.get('status')=='accepted' and record.get('role') in {'target','input'}]}}}
    return small


def save_projection(task_dir, task, stamp=None):
    """Disposable sidecar, never rewrite a historical result or source PDF."""
    path = task_dir / f"{task['id']}.json"
    stamp = stamp or path.stat()
    directory = task_dir / '.summaries'
    directory.mkdir(exist_ok=True)
    target = directory / path.name
    temporary = directory / f'.{uuid.uuid4().hex}.tmp'
    try:
        temporary.write_text(json.dumps({'version': INDEX_VERSION,
            'signature': [stamp.st_mtime_ns, stamp.st_size], 'task': compact_task(task)},
            ensure_ascii=False), encoding='utf-8')
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def read_projection(task_dir, task_id, stamp):
    try:
        saved = json.loads((task_dir / '.summaries' / f'{task_id}.json').read_text(encoding='utf-8'))
        small = saved.get('task')
        if (saved.get('version') == INDEX_VERSION and saved.get('signature') == [stamp.st_mtime_ns, stamp.st_size]
                and isinstance(small, dict) and small.get('id') == task_id):
            return small
    except (OSError, ValueError, TypeError):
        pass
    return None


class HistoryReader:
    def __init__(self,core):
        self.core=core
        self.tasks={}

    def __getattr__(self,name):
        return getattr(self.core,name)

    def ids(self):
        # List committed snapshots. Never wait on an analysis/result-write lock.
        return {path.stem for path in self.core.TASK_DIR.glob('*.json')}

    def task_by_id(self,task_id):
        if task_id in self.tasks:return self.tasks[task_id]
        if not re.fullmatch(r'[a-f0-9]{32}',task_id):return None
        path=self.core.TASK_DIR/f'{task_id}.json'
        try:
            stamp=path.stat()
            key=(str(path.absolute()),stamp.st_mtime_ns,stamp.st_size)
            with CACHE_LOCK:
                small=CACHE.get(key)
                if small is not None:CACHE.move_to_end(key)
            if small is None:
                small=read_projection(self.core.TASK_DIR,task_id,stamp)
                if small is None:
                    raw=json.loads(path.read_text(encoding='utf-8'))
                    if not isinstance(raw,dict) or raw.get('id')!=task_id:return None
                    small=compact_task(raw)
                    # Legacy migration is a derived index only. Failure must not
                    # hide a valid archived report or change its saved content.
                    try:save_projection(self.core.TASK_DIR,raw,stamp)
                    except OSError:pass
                with CACHE_LOCK:
                    CACHE[key]=small
                    while len(CACHE)>MAX_CACHED_TASKS:CACHE.popitem(last=False)
            small=copy.deepcopy(small)
        except (OSError,ValueError,TypeError):return None
        if small.get('status') in {'queued','processing'} and small.get('session_id')!=self.core.SESSION_ID:
            small.update(status='interrupted',stage='服务重启，任务已中断',
                         error='任务未完成时后端已停止，请重新分析。原文件及已有结果仍保留。')
        self.tasks[task_id]=small
        return small
