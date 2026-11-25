"""Disclosure-precision checks, not audit materiality or restatement diagnoses."""
from decimal import Decimal, InvalidOperation, localcontext
import re

VERSION = 'precision-basis-1'
SAFE_STATUSES = {'matched', 'rounding_compatible'}
UNITS = {'元':Decimal(1),'人民币元':Decimal(1),'千元':Decimal(1000),
         '人民币千元':Decimal(1000),'万元':Decimal(10000),'人民币万元':Decimal(10000),
         '百万元':Decimal(1000000),'亿元':Decimal(100000000)}


def decimal(value):
    if value is None or isinstance(value, (bool,float)):return None
    try:result=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):return None
    return result if result.is_finite() else None


def precision(fact):
    """Infer a rounding interval only from a matching saved original token/unit.

    A normalized value alone never implies disclosure accuracy. Mixed units,
    scientific notation, percentages and unsupported rounding modes fail closed.
    """
    raw=fact.get('raw_value');unit=fact.get('raw_unit')
    if not isinstance(raw,str) or unit not in UNITS or fact.get('unit') not in {'CNY','yuan'}:return None
    text=raw.strip().replace(',','').replace('，','').replace('−','-')
    if re.fullmatch(r'\([0-9]+(?:\.[0-9]+)?\)',text):text='-'+text[1:-1]
    if not re.fullmatch(r'-?\d+(?:\.\d+)?',text):return None
    places=len(text.split('.')[1]) if '.' in text else 0
    with localcontext() as context:
        context.prec=max(60,len(text)+20)
        if Decimal(text)*UNITS[unit]!=decimal(fact.get('value')):return None
        step=UNITS[unit]*(Decimal(10)**-places)
    return {'raw_value':raw,'raw_unit':unit,'decimal_places':places,
            'step':format(step,'f'),'half_step':format(step/2,'f')}


def compare_facts(original,later):
    result={'status':'unavailable','reason_code':'MISSING_COMPARATIVE',
            'explanation':'后续报告中未提取到这一期间的有效比较值；不等于原报告未披露。',
            'materiality_assessed':False}
    if not original or not later:return result
    for fact in (original,later):
        if fact.get('quality') in {'conflict','rejected','unconfirmed'}:
            return {**result,'reason_code':'FACT_CONFLICT','explanation':'输入存在解析冲突或尚未确认，暂不判断一致性。'}
    a,b=decimal(original.get('value')),decimal(later.get('value'))
    if a is None or b is None:return {**result,'reason_code':'INVALID_VALUE','explanation':'至少一处数值缺失或格式无效，未按零处理。'}
    if original.get('unit')!=later.get('unit') or not original.get('unit'):
        return {**result,'reason_code':'UNIT_MISMATCH','explanation':'标准化单位不一致，暂不直接比较金额。'}
    for key in ('period','statement_scope','period_type','period_start','period_end'):
        if original.get(key)!=later.get(key):
            return {**result,'reason_code':'BASIS_MISMATCH','explanation':'期间、合并范围或时点/期间口径不一致，需先核对原文。'}
    with localcontext() as context:
        context.prec=max(60,len(str(a))+len(str(b))+20)
        delta=b-a
        result.update(difference=format(delta,'f'),absolute_difference=format(abs(delta),'f'),unit=original['unit'],
                      relative_difference_percent=format(delta/abs(a)*100,'f') if a else None)
    pa,pb=precision(original),precision(later)
    result['precision']={'original':pa,'later':pb,'method':'nearest_rounding_interval_compatibility',
                         'note':'按两处原始单位与小数位估计就近舍入区间；区间相容不证明公司实际舍入方式或差异原因。'}
    # Confirm only an explicit saved reconciliation excerpt binding this item,
    # period, pre/post amounts and same original unit. Never infer from magnitude
    # or a page-wide 'restated' flag. Ordinary two-column rows do not qualify.
    label=original.get('raw_label') or original.get('metric_code') or ''
    label=re.split(r'\s+\d|\s+-',label,1)[0].strip()
    if pa and pb and a!=b and original['raw_unit']==later['raw_unit'] and label:
        for source in later.get('sources') or []:
            text=source.get('raw_text') or ''
            reason=next((word for word in ('前期差错更正','追溯重述','会计政策变更','重分类') if word in text),None)
            if not reason or not source.get('page_start') or label not in text or str(original.get('period')) not in text:continue
            if re.search(r'(?:未|无|不)[^，。；\n]{0,12}'+re.escape(reason),text) or '不适用' in text or '不涉及' in text:continue
            pattern=r'调整前\s*[:：]?\s*'+re.escape(original['raw_value'])+r'\s*(?:'+re.escape(original['raw_unit'])+r')?\s*[，,；;\s]+调整后\s*[:：]?\s*'+re.escape(later['raw_value'])+r'(?![\d.])'
            if len(text)<=2000 and re.search(pattern,text):
                return {**result,'status':'adjusted','reason_code':'EXPLICIT_ADJUSTMENT',
                    'explanation':'已保存原文明确列出该期间、该项目的调整前后金额及调整事项。确认存在披露调整，不据此认定违规或已完成审计。',
                    'adjustment_evidence':{'document_id':later.get('document_id'),'fact_id':later.get('fact_id'),
                        'page':source['page_start'],'excerpt':text,'reason':reason}}
    # A page-level parser flag alone is insufficient to certify an adjustment.
    if later.get('comparative_restated') or original.get('comparative_restated'):
        return {**result,'status':'difference','reason_code':'ADJUSTMENT_MARKED',
                'explanation':'解析结果带有比较数调整标记，需核对调整说明；仅凭标记不能确认调整原因或金额。'}
    if a==b:return {**result,'status':'matched','reason_code':'EXACT_MATCH','explanation':'同期间、同口径的两处提取数值完全一致。'}
    if pa and pb:
        limit=Decimal(pa['half_step'])+Decimal(pb['half_step'])
        result['precision']['combined_half_step']=format(limit,'f')
        # Strict overlap: adjacent same-precision amounts touch at a rounding
        # boundary but cannot both be the same rounded amount. Never waive them.
        if abs(delta)<limit:
            return {**result,'status':'rounding_compatible','reason_code':'PRECISION_COMPATIBLE',
                    'explanation':'差额落在两处披露精度可解释的范围内，与舍入相容；保留原值，不认定为已确认重述，也不等于审计认可。'}
        reason='OUTSIDE_PRECISION_RANGE';text='差额超出两处披露精度可解释的范围，需核对原表、比较列及调整说明；不直接认定公司重述或财报错误。'
    else:
        reason='PRECISION_UNVERIFIED';text='数值不同，但缺少可验证的原始金额或单位精度，暂不能区分舍入、口径变化与提取问题。'
    return {**result,'status':'difference','reason_code':reason,'explanation':text}
