"""Period-aware acquisition and comparison, preserving the annual engine."""
import copy
from pathlib import Path
from financial_engine.disclosure import discover_annual_reports, latest_reports_by_year, download_report, resolve_security
from financial_engine.mining import _cached_records, _verify_cache, _pdf_security_identity
from financial_engine.periods import period_spec, period_key, report_label, report_kind
from financial_engine.interim import analyze_interim_pdf
from financial_engine.history import build_annual_history
from financial_engine.peer import compare_peer_group

def project_period(analysis, kind, quarter=None, basis='cumulative'):
    """Private comparison projection. Original documents and saved values stay intact."""
    projected=copy.deepcopy(analysis)
    docs=[]
    for doc in projected.get('documents',[]):
        if report_kind(doc.get('report_type'))!=kind or kind=='quarterly' and doc.get('report_quarter')!=quarter:continue
        doc['report_type']='annual';docs.append(doc)
    projected['documents']=docs;by_id={doc['document_id']:doc for doc in docs}
    support_ids={doc['document_id'] for doc in projected.get('support_documents',[]) if doc.get('security_code') and docs and doc.get('security_code')==docs[0].get('security_code')}
    for collection in ('metrics','unavailable_metrics','facts'):
        selected=[]
        for item in projected.get(collection,[]):
            doc=by_id.get(item.get('document_id'))
            if collection=='facts' and item.get('document_id') in support_ids:
                selected.append(item);continue
            if not doc:continue
            period=item.get('period','');year=int(str(period)[:4]) if str(period)[:4].isdigit() else None
            if year is None:continue
            key=period_key(year,kind,quarter,basis)
            month=6 if kind=='semiannual' else quarter*3
            if period==key or collection=='facts' and period.startswith(f'{year}-{month:02d}-'):
                item['period']=str(year);selected.append(item)
        projected[collection]=selected
    return projected

def build_period_history(analysis,code,kind,quarter=None,basis='cumulative'):
    if kind=='annual':return build_annual_history(analysis,code)
    result=build_annual_history(project_period(analysis,kind,quarter,basis),code)
    result.update(report_type=kind,report_quarter=quarter,period_basis=basis,
                  period_label='上半年' if kind=='semiannual' else '第一季度' if quarter==1 else '前三季度' if basis=='cumulative' else '第三季度')
    return result

def compare_period_peers(reports,*,kind,quarter=None,basis='cumulative',**config):
    if kind=='annual':return compare_peer_group(reports,**config)
    views={}
    for code,analysis in reports.items():
        source=copy.deepcopy(analysis)
        if basis=='single' and source.get('single_quarter'):
            source['metrics']=source['single_quarter']['metrics'];source['unavailable_metrics']=source['single_quarter']['unavailable_metrics']
        source['period_basis']=basis
        views[code]=project_period(source,kind,quarter,basis)
        # Formula signatures must include period duration; equal ratios remain comparable.
        for item in views[code].get('metrics',[]):item['formula']=item['formula'].replace('182 *','period_days *').replace('181 *','period_days *')
    result=compare_peer_group(views,**config)
    result.update(report_type=kind,report_quarter=quarter,period_basis=basis)
    return result

def acquire(core,code,year,kind,quarter=None,*,online=True,options=None,checkpoint=lambda:None):
    """Verified matching cache first, then matching public catalogue; no annual fallback."""
    checkpoint();warnings=[]
    records=_cached_records(core.CACHE_DIR.resolve(),warnings,kind,quarter)
    selected,_=latest_reports_by_year([r for r in records if r['stock_code']==code],[year])
    record=selected.get(year)
    if record:info=_verify_cache(record)
    else:
        if not online or not core.library_online_enabled():raise ValueError(f'{year}年{report_label(kind,quarter)}未在本地找到，联网补齐已关闭；可上传本地完整报告')
        security=resolve_security(code);checkpoint()
        records=discover_annual_reports(security,[year],report_type=kind,report_quarter=quarter)
        selected,alternatives=latest_reports_by_year(records,[year]);record=selected.get(year)
        if not record:raise ValueError(f'未找到 {code} 的 {year}年{report_label(kind,quarter)}；可能尚未披露，可上传本地完整报告')
        info=download_report(record,core.CACHE_DIR);info['alternative_versions']=alternatives
    checkpoint()
    path=Path(info['local_path']);identity=_pdf_security_identity(path)
    if identity.get('stock_code')!=code or identity.get('status') in ('ambiguous','conflict'):
        raise ValueError('公开报告 PDF 的公司身份未通过核验，未采用该文件')
    analysis=analyze_interim_pdf(path,kind,options,security_code=code)
    doc=analysis['documents'][0];spec=period_spec(doc)
    if doc['report_year']!=year or spec['report_type']!=kind or kind=='quarterly' and spec['report_quarter']!=quarter:
        raise ValueError('PDF 实际报告期间与搜索条件不一致，未采用该文件')
    doc['disclosure']={k:info.get(k) for k in ('announcement_id','title','published_at','source_url','version_label')}
    return analysis,{**info,'document_id':doc['document_id'],'role':'target','status':'accepted','report_type':kind,'report_quarter':quarter}

def analyze_context(core,*,stock_code,report_year,report_type,report_quarter=None,lookback_years=0,options=None,checkpoint=lambda:None,**kwargs):
    current,info=acquire(core,stock_code,report_year,report_type,report_quarter,options=options,checkpoint=checkpoint)
    # Single-quarter support uses only the immediately preceding cumulative filing.
    month=period_spec(current['documents'][0])['month'];support=[];files=[info];warnings=[]
    if month in (6,9):
        adjacent_kind='quarterly' if month==6 else 'semiannual';adjacent_q=1 if month==6 else None
        try:
            adjacent,entry=acquire(core,stock_code,report_year,adjacent_kind,adjacent_q,options=options,checkpoint=checkpoint)
            support.append(adjacent);files.append({**entry,'role':'quarter_support'})
            if month==9:
                q1,q1entry=acquire(core,stock_code,report_year,'quarterly',1,options=options,checkpoint=checkpoint)
                support.append(q1);files.append({**q1entry,'role':'quarter_support'})
        except core.TaskCancelled:raise
        except Exception as exc:warnings.append({'code':'ADJACENT_PERIOD_MISSING','message':str(exc)})
        current=analyze_interim_pdf(info['local_path'],report_type,options,support,stock_code)
    return {'status':'partial' if warnings else 'complete','current':current,'identity':{'stock_code':stock_code,'status':'verified','report_year':report_year,'report_type':report_type},
            'retrieval':{'files':files,'warnings':warnings},'warnings':warnings,'history':{'status':'not_requested'},'peers':{'status':'not_requested'}}

def enrich_upload(core,payload,path,options,task_id):
    analysis=payload.get('analysis') or {};docs=analysis.get('documents') or []
    if not docs or docs[0].get('report_type') not in ('semiannual','quarterly'):return payload
    doc=docs[0];spec=period_spec(doc);code=doc.get('security_code')
    if spec['month'] not in (6,9) or not code:return payload
    core.update_task(task_id,stage='核验相邻报告，补齐单季口径',progress=58)
    try:
        support,entry=acquire(core,code,doc['report_year'],'quarterly' if spec['month']==6 else 'semiannual',1 if spec['month']==6 else None,
            options=options,checkpoint=lambda:core.ensure_task_running(task_id))
        supporting=[support]
        payload.setdefault('retrieval',{}).setdefault('files',[]).append({**entry,'role':'quarter_support'})
        if spec['month']==9:
            try:
                q1,q1entry=acquire(core,code,doc['report_year'],'quarterly',1,options=options,checkpoint=lambda:core.ensure_task_running(task_id))
                supporting.append(q1);payload['retrieval']['files'].append({**q1entry,'role':'quarter_support'})
            except core.TaskCancelled:raise
            except Exception as exc:core.log_event(task_id,'warning','上季环比输入未齐全','本期和可取得的单季结果保留。',str(exc))
        payload['analysis']=analyze_interim_pdf(path,spec['report_type'],options,supporting,code)
    except core.TaskCancelled:raise
    except Exception as exc:
        analysis.setdefault('warnings',[]).append({'code':'ADJACENT_PERIOD_MISSING','message':str(exc)})
        core.log_event(task_id,'warning','单季数据未齐全','本期累计结果保留，单季缺失不估算。',str(exc))
    return payload
