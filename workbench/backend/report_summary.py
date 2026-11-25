"""Stable, old-style financial summary from saved engine data, never from a PDF.

This is a read-only view: no recalculation, model call or historical overwrite.
"""
import json
import re
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

TERMS=json.loads((Path(__file__).resolve().parent.parent/'shared'/'reportTerms.json').read_text(encoding='utf-8'))
GROUPS=(
    ('增长与盈利',('revenue_yoy','operating_cost_yoy','r_and_d_expense_yoy','net_profit_yoy',
        'attributable_net_profit_yoy','attributable_net_profit_ex_nonrecurring_yoy','total_profit_yoy',
        'revenue_q4_qoq','attributable_net_profit_q4_qoq','attributable_net_profit_ex_nonrecurring_q4_qoq','total_profit_q4_qoq')),
    ('利润率',('gross_margin','consolidated_net_margin','adjusted_attributable_net_margin',
        'r_and_d_expense_to_revenue','nonrecurring_to_attributable_profit')),
    ('现金流',('operating_cash_flow_yoy','operating_cash_flow_q4_qoq','cash_to_consolidated_profit','simplified_free_cash_flow')),
    ('营运效率与回报',('inventory_days','receivables_days_approx','return_on_assets','return_on_attributable_equity','fixed_assets_cip_to_assets')),
    ('资产质量',('inventory_impairment_to_gross','receivables_over_1y_to_gross')),
)
SHORT_METRIC_LABELS={
    'revenue_yoy':'营业收入同比','operating_cost_yoy':'营业成本同比','r_and_d_expense_yoy':'研发费用同比',
    'net_profit_yoy':'合并净利润同比','attributable_net_profit_yoy':'归母净利润同比',
    'attributable_net_profit_ex_nonrecurring_yoy':'扣非归母净利润同比','total_profit_yoy':'利润总额同比',
    'revenue_q4_qoq':'营业收入环比','attributable_net_profit_q4_qoq':'归母净利润环比',
    'attributable_net_profit_ex_nonrecurring_q4_qoq':'扣非归母净利润环比','total_profit_q4_qoq':'利润总额环比',
    'gross_margin':'合并毛利率','consolidated_net_margin':'合并净利率','adjusted_attributable_net_margin':'扣非归母净利率',
    'r_and_d_expense_to_revenue':'研发费用/营收','nonrecurring_to_attributable_profit':'非经常性损益/归母净利润',
    'operating_cash_flow_yoy':'经营活动现金流同比','operating_cash_flow_q4_qoq':'经营现金流环比',
    'cash_to_consolidated_profit':'经营现金流/合并净利润','simplified_free_cash_flow':'简化自由现金流',
    'inventory_days':'存货周转天数','receivables_days_approx':'应收周转天数（近似）',
    'return_on_assets':'总资产回报率','return_on_attributable_equity':'归母权益回报率',
    'fixed_assets_cip_to_assets':'固定资产及在建工程/总资产',
    'liabilities_to_assets':'资产负债率',
    'inventory_impairment_to_gross':'存货跌价准备/存货原值','receivables_over_1y_to_gross':'一年以上应收/应收原值',
}


def chinese(value,labels):
    text=str(value or '')
    for code,label in sorted({**TERMS,**labels}.items(),key=lambda pair:-len(pair[0])):
        text=re.sub(r'(?<![A-Za-z0-9_])'+re.escape(code)+r'(?![A-Za-z0-9_])',lambda _:label,text)
    return text


def number(value,places=2):
    try:
        with localcontext() as context:
            context.prec=80
            result=Decimal(str(value))
            if not result.is_finite():return '未提供'
            return f'{result:,.{places}f}'
    except (ValueError,InvalidOperation,TypeError):return '未提供'


def cell(value):
    # Do not allow saved text to create accidental Markdown columns or headings.
    return str(value if value is not None else '未提供').replace('|','／').replace('\r',' ').replace('\n','；').replace('`','')


def table(headers,rows):
    return ['| '+' | '.join(map(cell,headers))+' |','|'+'---|'*len(headers),
            *['| '+' | '.join(map(cell,row))+' |' for row in rows],'']


def observation(condition,labels,concise=False):
    code=condition.get('condition_code') or condition.get('code') or ''
    label=chinese(code,labels) or '规则条件'
    observed=condition.get('observed') or {}
    passed=condition.get('passed',condition.get('met'))
    state='满足' if passed is True else '未满足' if passed is False else '无法判断'
    detail=''
    if code=='consecutive_negative_quarterly_cfo':
        detail=f"最长连续 {cell(observed.get('longest_run','未提供'))} 个季度，要求至少 {cell(observed.get('minimum_required','未提供'))} 个季度"
    elif code in {'gross_receivables_growth_above_revenue_growth','gross_inventory_growth_above_revenue_growth'}:
        key='receivables_growth_percent' if 'receivables' in code else 'inventory_growth_percent'
        noun='应收账款' if 'receivables' in code else '存货'
        detail=f"{noun}增速 {number(observed.get(key))}%，营业收入增速 {number(observed.get('revenue_growth_percent'))}%"
        if observed.get('growth_gap_percentage_points') is not None:detail+=f"，增速差 {number(observed['growth_gap_percentage_points'])} 个百分点"
    elif 'current_percent' in observed or 'previous_percent' in observed:
        detail=f"本期 {number(observed.get('current_percent'))}%，上期 {number(observed.get('previous_percent'))}%"
        if observed.get('share_change_percentage_points') is not None:detail+=f"，变化 {number(observed['share_change_percentage_points'])} 个百分点"
    elif 'free_cash_flow_cny' in observed:
        detail=f"简化自由现金流 {number(observed['free_cash_flow_cny'])} 元"
    elif 'net_profit_cny' in observed:
        detail=f"合并净利润 {number(observed['net_profit_cny'])} 元"
    if concise and detail:
        short={'inventory_allowance_share_increased':'存货跌价准备占比','over_1y_share_increased':'一年以上账龄占比',
               'gross_margin_declined':'合并毛利率','fixed_assets_cip_share_increased':'固定资产及在建工程占比'}.get(code)
        return (f'{short}：' if short else '')+detail+f'（{state}）'
    return f'{label}：{state}'+(f'（{detail}）' if detail else '')


def build_summary(analysis,metric_labels,rule_labels,status_labels):
    docs=analysis.get('documents') or []
    if not docs:return ''
    doc=docs[0];year=doc.get('report_year','未识别')
    report_type='半年度报告' if doc.get('report_type') in {'half_year','semiannual','half_year_report'} else '年度报告'
    if doc.get('report_type')=='quarterly':report_type='第一季度报告' if doc.get('report_quarter')==1 else '第三季度报告'
    title=f"{doc.get('company_name') or doc.get('company_short_name') or '报告公司'} {year}年{report_type}"
    labels={**metric_labels,**rule_labels,**status_labels}
    metrics=analysis.get('metrics') or [];unavailable=analysis.get('unavailable_metrics') or []
    def period_text(value):
        text=str(value)
        if not analysis.get('period_basis'):return text
        return re.sub(r'^(\d{4})YTD9$',r'\1年前三季度',re.sub(r'^(\d{4})H1$',r'\1年上半年',re.sub(r'^(\d{4})Q([1-4])$',r'\1年第\2季度',text)))
    risks=analysis.get('risk_assessments') or [];warnings=analysis.get('warnings') or []
    source=doc.get('file_name') or '当前已载入的财报'
    currency={'CNY':'人民币','RMB':'人民币','USD':'美元','HKD':'港元'}.get(doc.get('currency'),doc.get('currency') or '未识别')
    pages=doc.get('pdf_pages') or doc.get('page_count')
    lines=[f'# {cell(title)} - 财务分析摘要','',f'> 数据来源：{cell(source)}（{cell(report_type)}'+(f'，{pages} 页' if pages else '')+f'，币种：{cell(currency)}）','',
           '> 按旧版结构整理当前已保存的引擎结果；未重新计算，非新增 AI 研判。','', '## 一、数据范围','']
    periods=list(dict.fromkeys(period_text(item.get('period')) for item in [*metrics,*unavailable] if item.get('period') is not None))
    sections='、'.join(chinese(item,labels) for item in doc.get('sections_found') or []) or '未记录'
    missing='；'.join(dict.fromkeys(chinese(item.get('metric_code','指标'),labels)+'（'+chinese(item.get('reason_code') or 'METRIC_UNAVAILABLE',labels)+'）' for item in unavailable)) or '当前未列出不可计算指标'
    lines+=table(('项目','内容'),[('公司',doc.get('company_name') or doc.get('company_short_name') or '未识别'),('报告',f'{year}年{report_type}'),
        ('覆盖章节',sections),('指标期间','、'.join(periods) or '未记录'),('未覆盖/不可用',missing)])
    lines+=['## 二、核心指标','']
    grouped=set()
    for index,(heading,codes) in enumerate(GROUPS,1):
        lines += [f'### 2.{index} {heading}','']
        rows=[]
        for code in codes:
            for item in [*metrics,*unavailable]:
                if item.get('metric_code')!=code:continue
                grouped.add(code)
                label=SHORT_METRIC_LABELS.get(code,metric_labels.get(code,chinese(code,labels)))
                if item.get('period') is not None and str(item['period'])!=str(year):label=f"{period_text(item['period'])} {label}"
                if item.get('status') not in {None,'calculated'} or item.get('value') is None:
                    rows.append((label,'不可计算：'+chinese(item.get('reason_code') or 'METRIC_UNAVAILABLE',labels),'—'))
                else:
                    unit={'percent':'%','ratio':'倍','times':'倍','days':'天','CNY':'元','yuan':'元'}.get(item.get('unit'),item.get('unit') or '未标注')
                    rows.append((label,number(item.get('value')),unit))
        lines += table(('指标','数值','单位'),rows) if rows else ['当前结果未提供本组指标，不以零替代。','']
    extra=[item for item in [*metrics,*unavailable] if item.get('metric_code') not in grouped]
    if extra:
        lines+=['### 2.6 其他指标','']+table(('指标','期间','数值/状态'),[(chinese(item.get('metric_code','未命名指标'),labels),period_text(item.get('period','未提供')),
            number(item['value'])+' '+chinese(item.get('unit',''),labels) if item.get('value') is not None else '不可计算：'+chinese(item.get('reason_code') or 'METRIC_UNAVAILABLE',labels)) for item in extra])
    lines+=['## 三、风险复核','','### 3.1 已触发信号（建议关注并复核，非已证实风险）','']
    triggered=[risk for risk in risks if risk.get('status')=='triggered']
    if not triggered:lines+=['当前已评估规则中没有触发项；不代表公司不存在风险。','']
    for risk in triggered:
        lines += [f"**{cell(rule_labels.get(risk.get('rule_id'),chinese(risk.get('rule_id','规则'),labels)))}**",'']
        lines += ['- '+cell(observation(condition,labels,concise=True)) for condition in risk.get('conditions') or []]
        if not risk.get('conditions'):lines+=['- '+cell(chinese(risk.get('summary') or '当前未记录条件明细',labels))]
        if risk.get('materiality_assessed') is False:lines+=['- 尚未评估变化的重要性，不能据此认定风险严重程度。']
        if risk.get('limitations'):lines+=['- 限制：'+cell(chinese(risk['limitations'],labels))]
        lines+=['']
    lines+=['### 3.2 未触发信号','']
    not_triggered=[risk for risk in risks if risk.get('status')=='not_triggered']
    lines+=table(('筛查规则','状态','关键观察'),[(rule_labels.get(risk.get('rule_id'),chinese(risk.get('rule_id','规则'),labels)),'未触发',
        '；'.join(observation(condition,labels,concise=True) for condition in risk.get('conditions') or []) or chinese(risk.get('summary') or '未记录条件明细',labels)) for risk in not_triggered]) if not_triggered else ['当前未记录未触发规则。','']
    lines+=['### 3.3 未评估项','']
    not_evaluated=[risk for risk in risks if risk.get('status')=='not_evaluated']
    lines+=table(('规则','原因'),[(rule_labels.get(risk.get('rule_id'),chinese(risk.get('rule_id','规则'),labels)),
        chinese(risk.get('reason_code') or risk.get('summary') or '当前输入不足以评估',labels)+'；'+chinese(risk.get('limitations') or '不能据此认定公司未披露或存在违规。',labels)) for risk in not_evaluated]) if not_evaluated else ['当前未记录未评估项。','']
    policy_reviews=[risk for risk in risks if risk.get('screen_type')=='policy_observation']
    if policy_reviews:
        lines+=['### 3.4 会计政策与估计披露复核','']
        lines+=table(('复核项目','状态','观察及限制'),[(rule_labels.get(risk.get('rule_id'),'政策复核'),status_labels.get(risk.get('status'),'需人工确认'),risk.get('summary')) for risk in policy_reviews])
        for risk in policy_reviews:
            lines+=['**'+cell(rule_labels.get(risk.get('rule_id'),'政策复核'))+'**','']
            lines+=['- '+cell(detail) for detail in risk.get('review_details',[])]
            for missing in risk.get('missing_or_invalid',[]):
                lines+=['- 待补齐：'+cell(missing.get('period',''))+'年'+cell(chinese(missing.get('code',''),labels))+'；'+cell(chinese(missing.get('reason',''),labels))+'。']
            lines+=['- 复核范围：'+cell(risk.get('review_scope','仅覆盖已提取披露'))+'。','']
    insufficient=[risk for risk in risks if risk.get('screen_type')!='policy_observation' and risk.get('status') not in {'triggered','not_triggered','not_evaluated'}]
    if insufficient:
        lines+=[f"### {'3.5' if policy_reviews else '3.4'} 数据不足或其他状态",'']+table(('规则','状态','说明'),[(chinese(risk.get('rule_id','规则'),labels),status_labels.get(risk.get('status'),chinese(risk.get('status') or '未记录',labels)),chinese(risk.get('summary') or '需补齐输入后再评估',labels)) for risk in insufficient])
    lines+=['## 四、证据限制','']
    if warnings:
        lines+=['- 解析器警告：'+cell(chinese(item.get('message') or item.get('detail') or item.get('code') or '存在未详述的解析警告',labels)) if isinstance(item,dict) else '- 解析器警告：'+cell(chinese(item,labels)) for item in warnings]
    else:lines+=['- 当前结果没有记录解析器警告，不等于数据已获独立审计认证。']
    for item in unavailable:
        lines+=['- 不可用指标：'+cell(chinese(item.get('metric_code','指标'),labels))+'；'+cell(chinese(item.get('reason_code') or 'METRIC_UNAVAILABLE',labels))+('；缺少/异常输入：'+cell(chinese(item['detail'],labels)) if item.get('detail') else '')+'。']
    for risk in not_evaluated:
        lines+=['- 未评估项：'+cell(chinese(risk.get('rule_id','规则'),labels))+'；'+cell(chinese(risk.get('summary') or '未取得完整评估依据',labels))+'。']
    lines+=['- 信号性质：方向性组合筛查，不是审计结论、信用评级或违规认定。未触发不等于没有风险，数据缺失不等于零。']
    limitations=list(dict.fromkeys(chinese(risk['limitations'],labels) for risk in risks if risk.get('limitations')))
    lines+=['- 口径限制：'+cell(item) for item in limitations]
    lines+=['- 数据来源：当前报告及本任务已核验的相邻定期报告；单季推导保留原始输入和各自证据。' if analysis.get('support_documents') else '- 数据来源：仅使用当前报告已保存的引擎事实、指标及规则结果，未额外引入外部数据。','']
    return '\n'.join(lines)
