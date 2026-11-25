"""Curated renewable-energy candidates, with independent identity/data states."""
import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from fastapi import HTTPException, Query

CATALOG = json.loads((Path(__file__).resolve().parent.parent / 'shared' / 'companyCatalog.json').read_text(encoding='utf-8'))
COMPANIES = CATALOG['items']
REGISTERED_NAMES = json.loads((Path(__file__).resolve().parent.parent / 'shared' / 'companyNames.json').read_text(encoding='utf-8'))
MASTER_URL = CATALOG['source_url']
LOCK = threading.Lock()
DATA_CACHE = {}
MASTER_CACHE = {'time':0, 'items':[]}
NAME_CACHE = {'key':None,'items':()}


def normalized(value):
    return re.sub(r'[\s·（）()\-_.]+','',str(value)).lower()


def match_score(item, query):
    q = normalized(query)
    if not q:return 1
    name, code, pinyin = (normalized(item.get(key,'')) for key in ('name','code','pinyin'))
    full_name=normalized(item.get('full_name',''))
    if q in {name,code,full_name}:return 100
    if name.startswith(q):return 90
    if code.startswith(q):return 85
    if pinyin.startswith(q):return 80
    if q in name or q in code:return 70
    if len(name)>=2 and name in q:return 60
    if (full_name and q in full_name) or any(q in normalized(alias) for alias in item.get('aliases',[])):return 55
    if q in normalized(item.get('segment','')):return 40
    return 0


def saved_data_states(core, year):
    """Read saved data without changing task state or starting any analysis."""
    library=core.get_report_library()
    library_stamp=(str(library.root),library.index_path.stat().st_mtime_ns) if library else None
    cache_key=(str(core.TASK_DIR.resolve()),str(core.CACHE_DIR.resolve()),year,library_stamp)
    with LOCK:
        previous=DATA_CACHE.get(cache_key)
        if previous and time.monotonic()-previous[0]<4:return previous[1]
    states={}
    priorities={'unknown':0,'failed':1,'pdf_saved':2,'limited':3,'analysis_saved':4}
    def record(code,state,count=0):
        if not re.fullmatch(r'\d{6}',str(code or '')):return
        old=states.get(code,{})
        if priorities[state]>priorities.get(old.get('state','unknown'),0) or (state==old.get('state') and count>old.get('metrics',0)):
            states[code]={'state':state,'metrics':count,'year':year}
    if library:
        for (code,report_year),entries in library.by_key.items():
            # Search is read-only and inexpensive; full SHA-256 is checked at
            # acquisition, not on each autocomplete keystroke.
            selected=[entry for entry in entries if entry.get('selected_for_comparison') is True]
            if report_year==year and len(selected)==1 and library.path_for(selected[0]).is_file():
                record(code,'pdf_saved')
    for path in core.CACHE_DIR.glob('*.json'):
        try:
            if path.stat().st_size>1024*1024:continue
            item=json.loads(path.read_text(encoding='utf-8'))
            pdf=Path(item.get('local_path') or '').resolve()
            if item.get('report_year')==year and item.get('report_type')=='annual' and pdf.is_relative_to(core.CACHE_DIR.resolve()) and pdf.is_file():
                with pdf.open('rb') as stream:
                    if stream.read(5)==b'%PDF-':record(item.get('stock_code'),'pdf_saved')
        except (OSError,ValueError,TypeError):continue
    for path in core.TASK_DIR.glob('*.json'):
        try:
            task=json.loads(path.read_text(encoding='utf-8'))
            if task.get('trashed'):continue
            result=task.get('result') or {}; payload=result.get('payload') or {}
            config=task.get('request') or {}
            if task.get('status') in {'failed','interrupted'} and config.get('report_year')==year:
                record(task.get('stock_code') or (config.get('query') if re.fullmatch(r'\d{6}',str(config.get('query',''))) else None),'failed')
            if task.get('status')!='completed':continue
            analyses=[result.get('analysis') or {},(payload.get('history') or {}).get('analysis') or {}]
            analyses.extend(((payload.get('peers') or {}).get('reports') or {}).values())
            for analysis in analyses:
                if not isinstance(analysis,dict):continue
                documents=analysis.get('documents') or []
                for doc in documents:
                    if doc.get('report_year')!=year or doc.get('report_type')!='annual':continue
                    code=doc.get('security_code') or (payload.get('identity') or {}).get('stock_code')
                    count=sum(metric.get('document_id')==doc.get('document_id') and str(metric.get('period'))==str(year) and metric.get('status')=='calculated' for metric in analysis.get('metrics') or [])
                    record(code,'analysis_saved' if count else 'limited',count)
        except (OSError,ValueError,TypeError):continue
    with LOCK:DATA_CACHE[cache_key]=(time.monotonic(),states)
    return states


def saved_names(core):
    from history_index import HistoryReader
    paths=list(core.TASK_DIR.glob('*.json'))
    try:
        key=(str(core.TASK_DIR.resolve()),tuple(sorted((path.name,path.stat().st_mtime_ns,path.stat().st_size) for path in paths)))
    except OSError:return ()
    with LOCK:
        if NAME_CACHE['key']==key:return NAME_CACHE['items']
    names=[]
    reader=HistoryReader(core)
    for path in paths:
        try:
            task=reader.task_by_id(path.stem)
            if not task:continue
            if task.get('trashed') or task.get('status')!='completed':continue
            result=task.get('result') or {}
            identity=(result.get('payload') or {}).get('identity') or {}
            if identity.get('status') in {'ambiguous','conflict','conflict_or_unverified','unverified'} or (result.get('report_identity') or {}).get('status')=='conflict':continue
            for doc in (result.get('analysis') or {}).get('documents') or []:
                codes={str(value) for value in (doc.get('security_code'),doc.get('stock_code'),identity.get('stock_code'),(result.get('report_identity') or {}).get('stock_code')) if value}
                if len(codes)!=1:continue
                code=next(iter(codes));name=doc.get('company_name')
                if not isinstance(name,str) or len(name)<4 or name in {'股份有限公司','有限公司','有限责任公司'}:continue
                names.append((code,name))
        except (OSError,ValueError,TypeError):continue
    with LOCK:NAME_CACHE.update(key=key,items=tuple(names))
    return tuple(names)


def directory(core):
    items={item['code']:{**item,'identity_checked_at':CATALOG['checked_at'],'identity_source':'巨潮资讯证券目录'} for item in COMPANIES}
    library=core.get_report_library()
    if library:
        for entry in library.companies:
            code=entry['code']
            if code in items:
                items[code]['aliases']=list(dict.fromkeys([*items[code].get('aliases',[]),*entry.get('aliases',[])]))
            else:
                items[code]={**entry,'pinyin':entry.get('pinyin',''),'verified':False,
                    'identity_status':'catalog_verified' if entry.get('identityVerified') else 'unverified',
                    'identity_source':entry.get('identitySource','本地财报库目录'),
                    'identity_checked_at':entry.get('identityCheckedAt',library.index.get('snapshot_version'))}
    cache=core.COMPANY_DIR/'verified.json'
    try:
        for item in json.loads(cache.read_text(encoding='utf-8')):
            if re.fullmatch(r'\d{6}',str(item.get('code',''))) and item.get('identity_status')=='online_verified':items[item['code']]={**items.get(item['code'],{}),**item}
    except (OSError,ValueError,TypeError):pass
    # Full registered names are search aliases, not inferred from short-name
    # substrings. Only accept saved names bound to an unambiguous report code.
    for code,name in REGISTERED_NAMES.items():
        if code in items:items[code]['full_name']=name
    for code,name in saved_names(core):
        if code in items:items[code]['aliases']=list(dict.fromkeys([*items[code].get('aliases',[]),name]))
    return list(items.values())


def decorate(item,states,year):
    return {**item,'data_status':states.get(item['code'],{'state':'unknown','metrics':0,'year':year})}


def load_master():
    """Bounded, cached official identity directory; no report or model calls."""
    with LOCK:
        if time.monotonic()-MASTER_CACHE['time']<3600:master=MASTER_CACHE['items']
        else:master=None
    if master is None:
        request=Request(MASTER_URL,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.cninfo.com.cn/'})
        with urlopen(request,timeout=12) as response:body=response.read(4*1024*1024+1)
        if len(body)>4*1024*1024:raise ValueError('证券目录过大')
        master=json.loads(body)['stockList']
        if not isinstance(master,list) or any(not isinstance(item,dict) for item in master):raise ValueError('证券目录格式不正确')
        with LOCK:MASTER_CACHE.update(time=time.monotonic(),items=master)
    return master


def official_item(raw,existing=None):
    code=raw['code'];existing=existing or {}
    return {**existing,'name':raw['zwjc'],'code':code,'exchange':'SH' if code.startswith(('60','68')) else 'SZ' if code.startswith(('00','30')) else 'BJ',
            'segment':existing.get('segment','名单外公司（待人工分类）'),'pinyin':raw.get('pinyin',existing.get('pinyin','')),
            'verified':True,'identity_status':'online_verified','identity_source':'巨潮资讯联网核验','identity_checked_at':datetime.now(timezone.utc).isoformat()}


def valid_official(raw):
    return isinstance(raw,dict) and raw.get('category')=='A股' and re.fullmatch(r'\d{6}',str(raw.get('code',''))) and isinstance(raw.get('zwjc'),str) and bool(raw['zwjc'].strip())


def verify_code(core,code):
    # This is a public company-identity lookup, not a report download or LLM call.
    try:
        master=load_master()
    except (OSError,ValueError,KeyError,TypeError):
        try:
            identity=core._online_resolve_security(code)
            if identity.get('stock_code')!=code:raise ValueError('代码不一致')
            master=[{'code':code,'category':'A股','zwjc':identity.get('company_short_name')}]
        except Exception as exc:
            raise HTTPException(503,'暂时无法联网核验证券代码，请稍后重试；不能仅凭输入代码认定公司身份。') from exc
    matches=[item for item in master if valid_official(item) and item['code']==code]
    if len(matches)!=1:raise HTTPException(404,'未查到唯一有效的 A 股公司，请检查六位证券代码；暂不支持港股、美股代码。')
    raw=matches[0];existing=next((item for item in directory(core) if item['code']==code),{})
    item=official_item(raw,existing)
    core.COMPANY_DIR.mkdir(parents=True,exist_ok=True)
    with LOCK:
        cache=core.COMPANY_DIR/'verified.json'
        try:old=json.loads(cache.read_text(encoding='utf-8'))
        except (OSError,ValueError):old=[]
        items={entry['code']:entry for entry in old if isinstance(entry,dict) and entry.get('code')}
        items[code]=item
        temporary=core.COMPANY_DIR/f'{uuid.uuid4().hex}.tmp'
        temporary.write_text(json.dumps(list(items.values()),ensure_ascii=False),encoding='utf-8')
        temporary.replace(cache)
    return item


def register_company_routes(app,core):
    @app.get('/api/companies')
    def search(q:str=Query(default='',max_length=100),report_year:int|None=Query(default=None,ge=1990,le=2100),
               segment:str='',exclude:str='',offset:int=Query(default=0,ge=0),limit:int=Query(default=20,ge=1,le=50)):
        items=directory(core);excluded=set(exclude.split(','))
        matches=[(match_score(item,q),item) for item in items if item['code'] not in excluded and (not segment or item['segment']==segment)]
        selected=[item for score,item in sorted(matches,key=lambda pair:(-pair[0],pair[1]['name'],pair[1]['code'])) if score]
        states=saved_data_states(core,report_year) if report_year is not None else {}
        return {'items':[decorate(item,states,report_year) for item in selected[offset:offset+limit]],'total':len(selected),'has_more':offset+limit<len(selected),
                'segments':list(dict.fromkeys(item['segment'] for item in items)),'catalog_size':len(items),'source':'verified_local_catalog',
                'identity_checked_at':CATALOG['checked_at'],'max_peers':5}

    @app.get('/api/companies/lookup')
    def lookup(code:str=Query(pattern=r'^\d{6}$'),report_year:int|None=Query(default=None,ge=1990,le=2100)):
        item=verify_code(core,code)
        return {'item':decorate(item,saved_data_states(core,report_year) if report_year is not None else {},report_year),'source':'official_online_lookup'}

    @app.get('/api/companies/online')
    def online(q:str=Query(min_length=2,max_length=100),report_year:int|None=Query(default=None,ge=1990,le=2100),
               offset:int=Query(default=0,ge=0),limit:int=Query(default=20,ge=1,le=50)):
        if not normalized(q):raise HTTPException(422,'请输入至少两个字符的公司名称或证券代码')
        local=directory(core);by_code={item['code']:item for item in local}
        try:
            master=load_master()
        except (OSError,ValueError,KeyError,TypeError) as exc:
            raise HTTPException(503,'联网公司查询失败，本地候选仍可使用；请检查网络后重试。') from exc
        # Remove duplicate official records but never silently select conflicting names.
        groups={}
        for raw in master:
            if valid_official(raw):groups.setdefault(raw['code'],[]).append(raw)
        candidates=[]
        for code,records in groups.items():
            if len({record['zwjc'] for record in records})!=1:continue
            item=official_item(records[0],by_code.get(code))
            score=match_score(item,q)
            if score:candidates.append((score,item))
        selected=[item for _,item in sorted(candidates,key=lambda pair:(-pair[0],pair[1]['name'],pair[1]['code']))]
        states=saved_data_states(core,report_year) if report_year is not None else {}
        return {'items':[decorate(item,states,report_year) for item in selected[offset:offset+limit]],'total':len(selected),
                'has_more':offset+limit<len(selected),'catalog_size':len(groups),'segments':[],
                'source':'official_online_catalog','max_peers':5}
