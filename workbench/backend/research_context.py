"""Frozen, evidence-addressable input for research prose. Never recalculate values."""
import copy
import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from report_identity import same_report
from financial_engine.periods import report_kind, report_label

VERSION='research-context-1'
TERMS=json.loads((Path(__file__).resolve().parent.parent/'shared/reportTerms.json').read_text(encoding='utf-8'))


def period_label(value):
    if re.fullmatch(r'\d{4}',value):return value+'年'
    value=re.sub(r'^(\d{4})H1$',r'\1年上半年',value)
    value=re.sub(r'^(\d{4})YTD9$',r'\1年前三季度',value)
    return re.sub(r'^(\d{4})Q([1-4])$',r'\1年第\2季度',value)


def primary(task):
    return (((task.get('result') or {}).get('analysis') or {}).get('documents') or [{}])[0]


def compatible(left,right):
    return (same_report(left,right) and report_kind(left.get('report_type'))==report_kind(right.get('report_type'))
            and left.get('report_quarter')==right.get('report_quarter'))


def linked_snapshots(core,base,scope):
    selected=[base]
    if scope=='current':return selected
    seen={base['id']};queue=[base]
    while queue and len(seen)<16:
        task=queue.pop(0)
        for key in ('parent_task_id','backfill_root_id','latest_backfill_task_id','latest_peer_task_id'):
            identifier=task.get(key)
            if not identifier or identifier in seen:continue
            seen.add(identifier)
            related=core.task_by_id(identifier)
            if not related or related.get('trashed') or related.get('status')!='completed':continue
            if not compatible(primary(base),primary(related)):continue
            selected.append(related);queue.append(related)
    return sorted(selected,key=lambda task:(task.get('created_at',''),task['id']))


def build_context(core,base,scope='available'):
    doc=primary(base)
    if not doc.get('document_id') or report_kind(doc.get('report_type')) not in {'annual','semiannual','quarterly'}:
        raise ValueError('请先载入有明确期间的财报分析结果；当前版本不对公告或待复核扫描件生成研究解读。')
    if ((base.get('result') or {}).get('report_identity',{}).get('status')=='conflict' or
        ((base.get('result') or {}).get('payload',{}).get('identity') or {}).get('status') in {'ambiguous','conflict','conflict_or_unverified'}):
        raise ValueError('当前报告公司身份存在冲突，请先核对原文件。')
    tasks=linked_snapshots(core,base,scope)
    datasets=[('本期',base,base['result']['analysis'])]
    if scope=='available':
        history=None;peers={};peer_tasks=[]
        for task in tasks:
            payload=(task.get('result') or {}).get('payload') or {}
            item=(payload.get('history') or {}).get('analysis')
            if item and (payload.get('history') or {}).get('status') in {'complete','partial'}:history=(task,item)
            peer_data=payload.get('peers') or {}
            if peer_data.get('status') not in {'complete','partial'}:continue
            peer_tasks.append(task)
        explicit=[task for task in peer_tasks if task.get('source')=='peer_compare']
        chosen=(explicit or peer_tasks)[-1:]
        for task in chosen:
            payload=(task.get('result') or {}).get('payload') or {}
            peer_data=payload.get('peers') or {}
            # A newer explicit comparison replaces the old selection, rather
            # than silently merging previously removed peer companies.
            peers={}
            selected=(payload.get('peer_binding') or {}).get('peer_codes')
            for code,item in (peer_data.get('reports') or {}).items():
                if selected is not None and code not in selected:continue
                item_doc=(item.get('documents') or [{}])[0] if isinstance(item,dict) else {}
                if isinstance(item,dict) and code!=doc.get('security_code') and not same_report(doc,item_doc):peers[code]=(task,item)
        if history:datasets.append(('历年同期',*history))
        datasets.extend(('所选同行',task,analysis) for task,analysis in list(sorted(peers.values(),key=lambda pair:primary(pair[0]).get('company_name','')))[:5])
    registry={};used=set();coverage=[];missing=[];risks=[]
    for role,task,analysis in datasets:
        basis_checks=[];blocked=set()
        if role=='历年同期':
            from history_basis_view import enrich_history
            comparison=((enrich_history(task['result']).get('payload') or {}).get('history') or {}).get('comparison') or {}
            basis_checks=comparison.get('restatement_checks') or []
            blocked={m.get('metric_id') for check in basis_checks if check.get('status') not in {'matched','rounding_compatible'}
                     for m in check.get('affected_metrics') or []}
        docs={d.get('document_id'):d for d in [*analysis.get('documents',[]),*analysis.get('support_documents',[])]}
        eligible={identifier:d for identifier,d in docs.items()
                  if report_kind(d.get('report_type'))==report_kind(doc.get('report_type'))
                  and d.get('report_quarter')==doc.get('report_quarter')
                  and (role!='所选同行' or d.get('report_year')==doc.get('report_year'))
                  and (role!='历年同期' or (d.get('security_code')==doc['security_code'] if doc.get('security_code')
                       else bool(doc.get('company_name')) and d.get('company_name')==doc.get('company_name')))}
        if not eligible:continue
        # Only stored evidence is addressable by the evidence drawer endpoint.
        # Reconstructing an index here would create links that can return 404.
        evidence=(task.get('result') or {}).get('evidence') or {}
        added=0
        def sources(ids):
            result=[]
            for identifier in ids:
                source=evidence.get(identifier)
                if not source or source.get('quality')=='conflict':continue
                page=source.get('page') or source.get('page_start')
                if not page:continue
                if source.get('document_id') not in eligible:continue
                result.append({'task_id':task['id'],'fact_id':identifier,'page':page,
                               'label':f'原文第 {page} 页'})
            return result
        # Keep each dataset bounded, prioritising calculated metrics and their
        # original input amounts; unavailable/conflicting inputs are never zeros.
        items=[]
        for metric in analysis.get('metrics',[]):
            items.append(('metric',metric))
            comparison=metric.get('comparison') or {}
            if comparison.get('status')=='calculated':
                items.append(('metric',{**comparison,'metric_code':metric.get('metric_code'),
                    'document_id':comparison.get('document_id') or metric.get('document_id')}))
        items.extend(('fact',item) for item in analysis.get('facts',[]))
        for kind,item in items:
            if added>=90 or len(registry)>=400:break
            source_doc=eligible.get(item.get('document_id'))
            if not source_doc or item.get('status') not in {None,'calculated'} or item.get('quality')=='conflict':continue
            try:value=Decimal(str(item.get('value')))
            except (InvalidOperation,ValueError,TypeError):continue
            if not value.is_finite():continue
            code=item.get('metric_code');period=str(item.get('period') or '')
            # Stock facts must be year-end for annual filings; quarter/support
            # facts are not silently promoted to full-period observations.
            identity=(source_doc.get('file_sha256') or item.get('document_id'),kind,code,period,str(value),item.get('unit'))
            if identity in used:continue
            ids=item.get('input_fact_ids') or ([item.get('fact_id')] if kind=='fact' else [])
            refs=sources(ids)
            if not refs or len(refs)!=len(ids):continue
            label=core.METRIC_LABELS.get(code) or TERMS.get(code,code)
            if not label or label==code:continue
            used.add(identity);added+=1
            identifier=f'm{len(registry)+1:04d}'
            basis=('single_quarter' if 'Q' in period else 'cumulative' if any(part in period for part in ('H1','YTD'))
                   else 'instant' if re.fullmatch(r'\d{4}-\d{2}-\d{2}',period) else analysis.get('period_basis') or 'annual')
            registry[identifier]={'id':identifier,'kind':kind,'code':code,'label':label,'period':period,
                'period_label':period_label(period),
                'company':source_doc.get('company_name') or '', 'scope':role,'report_type':source_doc.get('report_type'),
                'security_code':source_doc.get('security_code'),'restated':bool(item.get('comparative_restated') or any(evidence.get(ref,{}).get('comparative_restated') for ref in ids)),
                'period_basis':basis,'value':str(value),'unit':item.get('unit') or '',
                'display':core.format_metric_value(str(value),item.get('unit')),'sources':refs,
                'comparison_blocked':item.get('metric_id') in blocked if item.get('metric_id') else False}
        for check in sorted((c for c in basis_checks if c.get('status') not in {'matched','rounding_compatible'}),key=lambda c:0 if c.get('status') in {'difference','adjusted'} else 1)[:8]:
            if len(registry)>=400:break
            if check.get('status') in {'matched','rounding_compatible'}:continue
            ids=[identifier for identifier in (check.get('original_fact_id'),check.get('later_comparative_fact_id')) if identifier]
            refs=sources(ids)
            if not refs or len(refs)!=len(ids):continue
            identifier=f'm{len(registry)+1:04d}'
            label=(core.METRIC_LABELS.get(check.get('metric_code')) or TERMS.get(check.get('metric_code')) or '财务项目')+'历史口径核对'
            state={'difference':'待核查','adjusted':'原文确认有调整','unavailable':'无法核对'}.get(check.get('status'),'待核查')
            registry[identifier]={'id':identifier,'kind':'basis_check','code':check['metric_code'],
                'label':label,'period':str(check['year']),'period_label':period_label(str(check['year'])),
                'company':doc.get('company_name'),'security_code':doc.get('security_code'),'scope':role,
                'report_type':doc.get('report_type'),'period_basis':'annual' if doc.get('report_type')=='annual' else 'cumulative',
                'value':check['status'],'unit':'状态','display':state,'sources':refs,
                'condition_text':check.get('explanation') or '原因尚待核对，不认定为公司财报错误或重述。'}
        if not added:continue
        coverage.append({'role':role,'task_id':task['id'],'company':next(iter(eligible.values())).get('company_name'),
                         'years':sorted({d.get('report_year') for d in eligible.values() if d.get('report_year')}),'measures':added})
        missing.extend({'scope':role,'name':core.METRIC_LABELS.get(item.get('metric_code'),item.get('metric_code')),
                        'period':item.get('period'),'reason':item.get('reason_code'),'detail':item.get('detail')}
                       for item in (analysis.get('unavailable_metrics') or [])[:15])
        if role=='本期':
            risks=copy.deepcopy(analysis.get('risk_assessments') or [])
            from report_summary import observation
            for risk in risks:
                status=core.RISK_STATUS_LABELS.get(risk.get('status'))
                ids=risk.get('evidence_fact_ids') or []
                refs=sources(ids)
                if not status or not refs or len(refs)!=len(ids) or not risk.get('conditions'):continue
                identifier=f'm{len(registry)+1:04d}'
                registry[identifier]={'id':identifier,'kind':'rule','code':risk['rule_id'],
                    'label':core.RISK_LABELS.get(risk['rule_id'],'组合规则'),'period':str(risk.get('period') or ''),
                    'period_label':period_label(str(risk.get('period') or '')),'company':doc.get('company_name'),
                    'security_code':doc.get('security_code'),'scope':role,'period_basis':'annual',
                    'report_type':doc.get('report_type'),'value':risk['status'],'unit':'状态','display':status,
                    'sources':list({(ref['task_id'],ref['page']):ref for ref in refs}.values()),
                    'condition_text':'；'.join(observation(c,{**TERMS,**core.METRIC_LABELS},concise=True) for c in risk['conditions'])}
    if not registry:raise ValueError('当前结果缺少可关联原文的有效财务数据，暂不能生成有据可查的研究解读。')
    period=f"{doc.get('report_year')}年{report_label(doc.get('report_type'),doc.get('report_quarter'))}"
    from research_enrichment import source_info
    packet={'version':VERSION,'company':doc.get('company_name'),'period':period,
            'business_source':source_info(core,base),
            'report_type':doc.get('report_type'),'report_quarter':doc.get('report_quarter'),
            'coverage':coverage,'registry':registry,'risks':risks,'missing':missing,
            'limits':['仅覆盖已提取财务数据；尚未系统提取产品、产销及管理层经营解释。',
                      '未取得的历史或所选同行数据不参与判断；同行选择不等于已确认可比。',
                      '未引入市场价格、行业数据库或新闻，不形成目标价、买卖建议或盈利预测。']}
    from research_observations import observations
    packet['observations']=observations(packet)
    if not packet['observations']:raise ValueError('当前没有足够的同口径指标形成研究观察，请先补齐财务数据。')
    signature=hashlib.sha256(json.dumps(packet,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
    return packet,signature
