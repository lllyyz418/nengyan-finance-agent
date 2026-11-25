"""Read-only upgrade of saved history comparisons. No reparse, model, or writes."""
from financial_engine.basis_check import VERSION
from periodic_reports import build_period_history
from financial_engine.periods import report_kind
from financial_engine.context_risk import evaluate_context_risks


def enrich_history(result):
    payload=result.get('payload') or {};history=payload.get('history') or {}
    analysis=history.get('analysis') or {}
    documents=analysis.get('documents') or []
    if not documents or not history.get('comparison'):return result
    if history['comparison'].get('basis_check_version')==VERSION:return result
    target=((result.get('analysis') or {}).get('documents') or documents)[0]
    kind=report_kind(target.get('report_type'))
    if kind not in {'annual','semiannual','quarterly'}:return result
    comparison=build_period_history(analysis,target.get('security_code'),kind,target.get('report_quarter'),history.get('period_basis') or 'cumulative')
    updated={**history,'comparison':comparison,'basis_view':{
        'version':VERSION,'read_only':True,'message':'按当前精度规则核对已保存事实；未重新解析、修改原值或覆盖旧报告。'}}
    payload={**payload,'history':updated}
    if payload.get('risk_review') is not None:
        payload={**payload,'risk_review':evaluate_context_risks(payload)}
    return {**result,'payload':payload}
