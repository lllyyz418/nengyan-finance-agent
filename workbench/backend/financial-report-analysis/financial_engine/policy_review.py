"""Deterministic policy observations, not accounting-compliance judgments."""
from __future__ import annotations

import re
import copy
from decimal import Decimal, InvalidOperation, localcontext

POLICY_RULES = {'r_and_d_capitalization_policy', 'depreciation_policy_change'}


def _identity_matches(left: dict, right: dict) -> bool:
    a, b = left.get('security_code'), right.get('security_code')
    if a and b:
        return a == b
    return bool(left.get('company_name') and left.get('company_name') == right.get('company_name'))


def _pct(value: Decimal, total: Decimal) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = max(60,len(value.as_tuple().digits)+len(total.as_tuple().digits)+20)
        return value / total * 100


def _display(value: Decimal) -> str:
    with localcontext() as ctx:
        ctx.prec = max(60,len(value.as_tuple().digits)+10)
        return f'{value:,.2f}'


def _canonical(value: str) -> str:
    text = re.sub(r'[\s%％]', '', value)
    text = re.sub(r'[～~至—–－]', '-', text).replace('，', '、').replace(',', '、')
    normalized=re.sub(r'\d+(?:\.\d+)?', lambda m: format(Decimal(m[0]).normalize(),'f'),text)
    if re.fullmatch(r'\d+(?:\.\d+)?[-、]\d+(?:\.\d+)?(?:、\d+(?:\.\d+)?)*',normalized):
        separator='-' if '-' in normalized else '、'
        return separator.join(format(n,'f') for n in sorted(set(Decimal(n) for n in normalized.split(separator))))
    return normalized


def evaluate_policy_reviews(document: dict, facts: list[dict], documents: list[dict] | None = None) -> list[dict]:
    year, did = int(document['report_year']), document['document_id']
    policy = document.get('policy_review') or {}
    previous_docs=[doc for doc in documents or [] if doc.get('document_id')!=did and doc.get('report_year')==year-1
                   and doc.get('report_type','annual')=='annual' and _identity_matches(document,doc)]

    def base(rule: str) -> dict:
        return {'assessment_id': f'{did}:{year}:{rule}', 'document_id':did,
                'company_name':document.get('company_name'),'period':str(year), 'rule_id':rule,
                'rule_version':'2.0', 'status':'insufficient_data', 'screen_type':'policy_observation',
                'materiality_assessed':False,'conditions':[], 'unmet_condition_codes':[],
                'missing_or_invalid':[], 'evidence_fact_ids':[], 'evidence_metric_ids':[],
                'evidence_policy_ids':[], 'evidence_links':[], 'review_details':[],
                'limitations':'仅按已取得的披露复核；变化不等于违规，未发现变化也不代表没有风险。'}

    rd = base('r_and_d_capitalization_policy')
    rd.update(summary='核对研发总投入、资本化金额与两期资本化比例',
              review_scope='研发投入金额口径；不以利润表研发费用或期末开发支出余额替代')
    values = {}
    all_facts = {(f.get('document_id'),f.get('period'),f.get('metric_code')):f for f in facts}
    candidates = policy.get('rd_candidates') or []
    for candidate in candidates:
        eid = candidate['evidence_id']
        rd['evidence_policy_ids'].append(eid)
        rd['evidence_links'].append({'id':eid,'label':'研发总投入原表' if candidate['code']=='r_and_d_total_investment' else '资本化金额原表'})
    for period in (str(year),str(year-1)):
        for code,label in [('r_and_d_total_investment','研发总投入'),('r_and_d_capitalized_expenditure','资本化研发投入')]:
            item = all_facts.get((did,period,code))
            source_note=''
            if item is None and period==str(year-1) and len(previous_docs)==1:
                item=all_facts.get((previous_docs[0]['document_id'],period,code))
                if item is not None:
                    source_note='（来自同公司上期原报告）'
            if item is None:
                reason = next((m['reason'] for m in policy.get('missing_inputs',[]) if m['period']==period and m['code']==code), 'POLICY_EXTRACTION_FAILED' if policy.get('extraction_failed') else 'POLICY_INPUT_NOT_EXTRACTED')
                rd['missing_or_invalid'].append({'period':period,'code':code,'reason':reason})
                continue
            rd['evidence_fact_ids'].append(item['fact_id'])
            rd['evidence_links'].append({'id':item['fact_id'],'label':f'{period}年{label}'})
            try:
                value=Decimal(item['value'])
                if item.get('quality') in {'conflict','rejected','unconfirmed'} or item.get('unit') != 'CNY' or item.get('statement_scope','consolidated')!='consolidated' or not value.is_finite():
                    raise ValueError()
                values[(period,code)]=value
                rd['review_details'].append(f'{period}年{label}：{_display(value)} 元{source_note}')
            except (ValueError,InvalidOperation,TypeError):
                rd['missing_or_invalid'].append({'period':period,'code':code,'reason':'CONFLICTING_INPUT'})
    ratios = {}
    for period in (str(year),str(year-1)):
        total=values.get((period,'r_and_d_total_investment')); cap=values.get((period,'r_and_d_capitalized_expenditure'))
        if total is not None and cap is not None:
            if total <= 0 or cap < 0 or cap > total:
                rd['missing_or_invalid'].append({'period':period,'code':'r_and_d_capitalized_expenditure','reason':'POLICY_AMOUNT_INVALID'})
            else:
                ratios[period]=_pct(cap,total)
                rd['review_details'].append(f'{period}年研发投入资本化比例：{_display(ratios[period])}%')
    if not rd['missing_or_invalid'] and len(ratios)==2:
        c,p=str(year),str(year-1)
        with localcontext() as ctx:
            ctx.prec=max(80,sum(len(value.as_tuple().digits) for value in values.values())+20)
            gap=ratios[c]-ratios[p]
            increased=values[(c,'r_and_d_capitalized_expenditure')]*values[(p,'r_and_d_total_investment')]>values[(p,'r_and_d_capitalized_expenditure')]*values[(c,'r_and_d_total_investment')]
        rd.update(status='review_required' if increased else 'reviewed',
                  summary=f'资本化比例 {_display(ratios[p])}% → {_display(ratios[c])}%；'+('比例上升，建议核对项目及确认依据' if increased else '本次未观察到比例上升'),
                  review_metric='资本化比例上升' if increased else '已完成两期核对')
        rd['conditions']=[{'condition_code':'r_and_d_capitalization_share_increased','passed':increased,
                           'observed':{'current_percent':format(ratios[c],'f'),'previous_percent':format(ratios[p],'f'),'share_change_percentage_points':format(gap,'f')}}]
        rd['review_details'].append(f'比例变化：{_display(gap)} 个百分点；只观察方向，未设置经济重要性阈值')
    else:
        rd['summary']='尚未取得完整的两期研发投入口径，暂不能比较资本化比例'
        rd['review_metric']='待补齐研发投入数据'
        rd['review_details'].append('空白、横线及“不适用”不能直接当作资本化金额为零；也不由费用化金额倒推')

    dep=base('depreciation_policy_change')
    dep.update(summary='核对固定资产折旧参数与实际变更披露',
               review_scope='按资产类别比较折旧方法、使用年限、残值率及年折旧率；仅覆盖已可靠提取的类别')
    def tables_for(doc):
        groups=(doc.get('policy_review') or {}).get('depreciation_tables') or []
        mapping={}; conflict=False
        for table in groups:
            for row in table['rows']:
                category=row['category']; norm=tuple(_canonical(row[k]) for k in ('method','useful_life','residual_rate','annual_rate'))
                if category in mapping and mapping[category][0]!=norm:
                    conflict=True
                mapping[category]=(norm,row)
            ids=table.get('evidence_ids') or []
            if ids:
                dep['evidence_policy_ids'].append(ids[0])
                entry=next((e for e in (doc.get('policy_review') or {}).get('evidence',[]) if e.get('evidence_id')==ids[0]),{})
                page=(entry.get('source') or {}).get('page_start')
                suffix=f' · {page}页' if len(groups)>1 and page else ''
                dep['evidence_links'].append({'id':ids[0],'label':f"{doc['report_year']}年折旧表{suffix}"})
        return mapping,conflict
    current,conflict=tables_for(document)
    for _,row in current.values():
        if row['method']=='不适用':
            dep['review_details'].append(f"{year}年{row['category']}：原表列示不适用折旧，年限为永久")
        else:
            dep['review_details'].append(f"{year}年{row['category']}：{row['method']}；年限 {row['useful_life']} 年；残值率 {row['residual_rate'].rstrip('%％')}%；年折旧率 {row['annual_rate'].rstrip('%％')}%")
    states=[]
    unverified=policy.get('depreciation_unverified') or []
    for eid in unverified:
        dep['evidence_policy_ids'].append(eid);dep['evidence_links'].append({'id':eid,'label':'折旧原表（待确认）'})
    if unverified:
        dep['missing_or_invalid'].append({'period':str(year),'code':'fixed_asset_depreciation_policy_current','reason':'POLICY_TABLE_UNVERIFIED'})
    for entry in policy.get('estimate_disclosures',[]):
        states.append(entry['state']);dep['evidence_policy_ids'].append(entry['evidence_id'])
        dep['evidence_links'].append({'id':entry['evidence_id'],'label':'会计估计变更说明'})
    previous={}
    if len(previous_docs)==1:
        previous,prior_conflict=tables_for(previous_docs[0]);conflict|=prior_conflict
        prior_unverified=(previous_docs[0].get('policy_review') or {}).get('depreciation_unverified') or []
        if prior_unverified:
            unverified=[*unverified,*prior_unverified]
            dep['missing_or_invalid'].append({'period':str(year-1),'code':'fixed_asset_depreciation_policy_previous','reason':'POLICY_TABLE_UNVERIFIED'})
            dep['evidence_policy_ids'].extend(prior_unverified)
            dep['evidence_links'].extend({'id':eid,'label':'上期折旧原表（待确认）'} for eid in prior_unverified)
    changes=[];unmatched=sorted(set(current)^set(previous)) if previous else []
    matched=set(current)&set(previous)
    if previous and current:
        labels=('折旧方法','使用年限','残值率','年折旧率')
        for category in sorted(set(current)&set(previous)):
            a,b=previous[category][0],current[category][0]
            for i,label in enumerate(labels):
                if a[i]!=b[i]:
                    changes.append(f'{category}{label}：{a[i]} → {b[i]}')
        dep['review_details'].extend(changes or ([f'已匹配 {len(matched)} 类资产，四项折旧参数一致'] if matched else ['没有可直接匹配的资产类别，不能比较两期参数']))
        if unmatched:
            dep['review_details'].append('两期资产分类不一致，以下类别不能直接比较：'+'、'.join(unmatched))
    if conflict or len(previous_docs)>1 or ('none' in states and ('changed' in states or changes)):
        dep.update(status='manual_review',summary='折旧表或变更说明之间存在差异，需要人工核对',review_metric='披露需人工确认')
        dep['missing_or_invalid'].append({'period':str(year),'code':'fixed_asset_depreciation_policy_current','reason':'POLICY_DISCLOSURE_CONFLICT'})
    elif 'changed' in states or changes:
        dep.update(status='review_required',summary='发现折旧参数差异或相关变更披露，请核对适用范围、时间及影响',review_metric='发现变化，需复核')
    elif unverified:
        dep.update(status='manual_review',summary='已找到折旧原表，但部分参数尚未通过完整性校验',review_metric='折旧原表待确认')
    elif current and previous and not unmatched:
        dep.update(status='reviewed',summary='本次已匹配的资产类别折旧参数一致',review_metric='已完成两期核对')
    elif current and previous and unmatched:
        dep.update(status='manual_review',summary=('已匹配类别的折旧参数一致；' if matched else '')+'两期资产分类差异需要人工确认',review_metric='资产分类差异待确认')
        dep['missing_or_invalid'].append({'period':str(year),'code':'fixed_asset_depreciation_policy_current','reason':'POLICY_CATEGORY_MISMATCH'})
    elif current and 'none' in states and not previous:
        dep.update(status='reviewed',summary='报告明确列示重要会计估计变更不适用；已提取本期折旧参数',review_metric='已核对本期变更披露')
        dep['review_details'].append('这是本期报告的披露口径，尚未独立对照上期折旧表，不代表所有参数均未变化')
    else:
        has_candidates=bool(current or states)
        dep.update(status='manual_review' if states and not current else 'insufficient_data',
                   summary='已定位部分披露，仍缺少可靠的折旧参数或可比依据' if has_candidates else '尚未可靠提取固定资产折旧表及实际变更说明',
                   review_metric='需要核对原文' if states and not current else '待补齐折旧比较依据')
        if not current:
            dep['missing_or_invalid'].append({'period':str(year),'code':'fixed_asset_depreciation_policy_current','reason':'POLICY_EXTRACTION_FAILED' if policy.get('extraction_failed') else 'POLICY_TABLE_UNVERIFIED'})
        if not previous:
            dep['missing_or_invalid'].append({'period':str(year-1),'code':'fixed_asset_depreciation_policy_previous','reason':'POLICY_PRIOR_REPORT_NEEDED'})
        if unmatched:
            dep['missing_or_invalid'].append({'period':str(year),'code':'fixed_asset_depreciation_policy_current','reason':'POLICY_CATEGORY_MISMATCH'})
    if 'none' in states:
        dep['review_details'].append('本期“重要会计估计变更”小节明确勾选不适用；不把未提及变更当作没有变更')
    if 'changed' in states:
        dep['review_details'].append('已定位涉及固定资产折旧的实际变更说明；具体影响请通过证据查看原文')
    for item in (rd,dep):
        item['evidence_policy_ids']=list(dict.fromkeys(item['evidence_policy_ids']))
        item['evidence_links']=list({entry['id']:entry for entry in item['evidence_links']}.values())
    return [rd,dep]


def refresh_policy_reviews(analysis: dict, documents: list[dict] | None = None, facts: list[dict] | None = None) -> None:
    """Refresh only current policy observations in a NEW explicit-backfill result."""
    docs=documents or analysis.get('documents',[])
    for doc in analysis.get('documents',[]):
        if doc.get('report_type','annual')!='annual':
            continue
        refreshed=[item for item in docs if item.get('document_id')==doc['document_id'] and item.get('policy_review')]
        if len(refreshed)==1:
            doc['policy_review']=copy.deepcopy(refreshed[0]['policy_review'])
        if not doc.get('policy_review'):
            # Legacy saved analyses are not silently upgraded without parsing
            # their original PDF. Their historical state remains intact.
            continue
        reviews=evaluate_policy_reviews(doc,facts if facts is not None else analysis.get('facts',[]),docs)
        analysis['risk_assessments']=[r for r in analysis.get('risk_assessments',[]) if not (r.get('document_id')==doc['document_id'] and r.get('rule_id') in POLICY_RULES)]+reviews
        for audit in analysis.get('audit',[]):
            match=next((r for r in reviews if r['assessment_id']==audit.get('assessment_id')),None)
            if match:
                audit.update(status=match['status'],evidence_fact_ids=match['evidence_fact_ids'],evidence_policy_ids=match['evidence_policy_ids'])
