"""Peer comparisons bound to a saved annual report, never to an editable search box."""
import copy
import json
import re
from fastapi import BackgroundTasks, HTTPException
from pydantic import BaseModel, Field
from financial_engine.peer import _validate_group, compare_peer_group
from financial_engine.periods import period_spec, report_label
from periodic_reports import compare_period_peers


class PeerRequest(BaseModel):
    period_basis: str = Field(default='cumulative',pattern='^(cumulative|single)$')
    peer_codes: list[str] = Field(min_length=2, max_length=5)
    peer_group: str = Field(min_length=1)
    selection_note: str = Field(min_length=6)
    target_code: str | None = Field(default=None, pattern=r'^\d{6}$')
    confirm_identity: bool = False


def report_context(core, task_id, request=None):
    base = core.task_by_id(task_id)
    if not base or base.get('trashed'):
        raise HTTPException(404, '当前报告任务不存在')
    if base.get('status') != 'completed':
        raise HTTPException(409, '请先完成当前年度报告分析')
    result = core.result_with_identity(base)
    if (result.get('report_identity') or {}).get('status') == 'conflict':
        raise HTTPException(409, '当前报告的证券代码存在冲突，请核对原文后重新分析')
    analysis = result.get('analysis') or {}
    docs = analysis.get('documents') or []
    try:
        if len(docs)!=1:raise ValueError()
        period_spec(docs[0])
    except (ValueError,KeyError):raise HTTPException(409,'同行比较需要一份已分析完成且期间明确的定期报告')
    doc = docs[0]
    payload = result.get('payload') or {}
    codes = {str(value) for value in (doc.get('security_code'), doc.get('stock_code'),
             (payload.get('identity') or {}).get('stock_code'),
             (payload.get('peer_binding') or {}).get('target_code'), base.get('confirmed_peer_code')) if value and re.fullmatch(r'\d{6}', str(value))}
    if len(codes) > 1:
        raise HTTPException(409, '当前报告的证券代码存在冲突，请核对原文后重新分析')
    code = next(iter(codes), None)
    if request and request.target_code:
        if code and code != request.target_code:
            raise HTTPException(409, '证券代码与当前报告不一致，不能切换比较目标')
        if not code:
            if not request.confirm_identity:
                raise HTTPException(409, '请对照当前报告原文确认公司证券代码')
            try:
                identity = core.resolve_security(doc.get('company_name') or doc.get('company_short_name') or '')
            except Exception as exc:
                raise HTTPException(503, '暂时无法核验报告公司名称，请稍后重试') from exc
            if identity.get('stock_code') != request.target_code:
                raise HTTPException(409, '输入的证券代码与报告公司名称不匹配')
            code = request.target_code
            core.update_task(base['id'], confirmed_peer_code=code)
    return base, result, analysis, doc, code


def create_job(core, task_id, request):
    request = PeerRequest.model_validate(request)
    base, _, _, doc, code = report_context(core, task_id, request)
    if not code:
        raise HTTPException(409, '报告尚未识别到证券代码，请先确认目标公司身份')
    try:
        _validate_group(code, request.peer_codes, request.peer_group, request.selection_note)
    except ValueError as exc:
        raise HTTPException(400, '请选择 2—5 家不同且不包含目标公司的同行，并填写业务分组和选择理由') from exc
    if len(request.selection_note.strip()) < 6:
        raise HTTPException(400, '选择理由至少填写六个字')
    config = {**request.model_dump(), 'base_task_id': task_id, 'target_code': code,
              'report_type':period_spec(doc)['report_type'],'report_quarter':doc.get('report_quarter'),
              'report_year': doc['report_year'], 'company_name':doc.get('company_name')}
    task = core.create_task('peer_compare', f"{doc.get('company_name') or code}-{doc['report_year']}-同行比较", config)
    path = core.evidence_file_path(base)
    core.update_task(task['id'], parent_task_id=task_id, stock_code=code,
                     company_name=doc.get('company_name'), **({'file_path':str(path)} if path else {}))
    return task


from pdf_runtime import core_task_runtime


@core_task_runtime
def run_job(core, task_id):
    task = core.task_by_id(task_id)
    config = task['request']
    try:
        core.ensure_task_running(task_id)
        base, base_result, original, doc, code = report_context(core, config['base_task_id'], PeerRequest.model_validate(config))
        target = copy.deepcopy(original)
        kind=period_spec(doc)['report_type'];quarter=doc.get('report_quarter')
        target['documents'][0].update(security_code=code, report_type=kind)
        reports = {code:target}
        warnings = []
        files = []
        peers = config['peer_codes']
        base_config = base.get('request') or {}
        options = {key:value for key,value in (base_config.get('options') or base_config).items()
                   if key in {'nonrecurring_ratio_threshold', 'minimum_negative_cash_quarters'}}
        core.log_event(task_id, 'info', '绑定当前报告', '复用当前报告指标，优先读取本地同行年报，缺失时按配置联网获取。', f"主报告任务 {base['id']}，年度 {doc['report_year']}。")
        for index, peer in enumerate(peers):
            core.ensure_task_running(task_id)
            core.update_task(task_id, status='processing', stage=f'正在获取同行 {peer} 的 {doc["report_year"]} 年报（{index+1}/{len(peers)}）', progress=10+int(index*75/len(peers)))
            try:
                payload = core.analyze_disclosure_context(stock_code=peer, report_year=doc['report_year'],
                    report_type=kind,report_quarter=quarter,
                    lookback_years=0, cache_dir=core.CACHE_DIR, fetch_online=True, options=options, _task_id=task_id)
                core.ensure_task_running(task_id)
                analysis = payload.get('current')
                peer_docs = (analysis or {}).get('documents') or []
                if len(peer_docs) != 1 or peer_docs[0].get('report_year') != doc['report_year'] or peer_docs[0].get('security_code') != peer or period_spec(peer_docs[0])['report_type']!=kind or kind=='quarterly' and peer_docs[0].get('report_quarter')!=quarter:
                    raise ValueError('未取得同年度且身份一致的同行报告')
                reports[peer] = analysis
                files.extend({**entry,'role':'peer'} for entry in (payload.get('retrieval') or {}).get('files') or [])
            except core.TaskCancelled:
                raise
            except Exception as exc:
                warnings.append({'stock_code':peer, 'message':str(exc)})
                core.log_event(task_id, 'warning', '同行数据未取得', f'同行 {peer} 未取得有效年报', str(exc))
        core.ensure_task_running(task_id)
        comparison = compare_period_peers(reports,kind=kind,quarter=quarter,basis=config.get('period_basis','cumulative'), target_code=code, peer_codes=peers,
            report_year=doc['report_year'], peer_group=config['peer_group'], selection_note=config['selection_note'])
        result = copy.deepcopy(base_result)
        # Keep the base report, history and deterministic values; do not regenerate the AI report.
        result['analysis'] = target
        payload = result.setdefault('payload', {})
        payload['peers'] = {'status':'complete' if not warnings else 'partial', 'reports':reports,
                            'missing_codes':[peer for peer in peers if peer not in reports], 'comparison':comparison, 'warnings':warnings}
        payload['peer_binding'] = {'base_task_id':base['id'], 'target_code':code, 'report_year':doc['report_year'],
                                  'report_type':kind,'report_quarter':quarter,'period_basis':config.get('period_basis','cumulative'),
                                  'peer_codes':peers, 'peer_group':config['peer_group'], 'selection_note':config['selection_note']}
        payload.setdefault('retrieval', {}).setdefault('files', []).extend(files)
        for analysis in reports.values():
            result.setdefault('evidence', {}).update(core.evidence_index(analysis))
        core.ensure_task_running(task_id)
        (core.RESULT_DIR / f'{task_id}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        core.update_task(task_id, status='completed', stage='同行比较完成' if not warnings else '同行比较部分完成', progress=100, result=result)
        if not core.task_cancelled(task_id):
            core.update_task(base['id'], latest_peer_task_id=task_id)
    except core.TaskCancelled:
        return
    except Exception as exc:
        core.update_task(task_id, status='failed', stage='同行比较失败，当前报告仍保留', error=str(exc), progress=100)


def register_peer_routes(app, core):
    core.create_peer_task = lambda task_id, config: create_job(core, task_id, config)
    core.run_peer_task = lambda task_id: run_job(core, task_id)

    @app.post('/api/tasks/{task_id}/peers', status_code=202)
    def create(task_id: str, request: PeerRequest, background_tasks: BackgroundTasks):
        task = create_job(core, task_id, request)
        background_tasks.add_task(run_job, core, task['id'])
        return core.public_task(task)

    @app.get('/api/tasks/{task_id}/peers')
    def saved(task_id: str):
        base, _, _, doc, _ = report_context(core, task_id)
        visited = set()
        while base and base['id'] not in visited:
            visited.add(base['id'])
            latest = core.task_by_id(base.get('latest_peer_task_id', ''))
            if latest and latest.get('status') == 'completed' and not latest.get('trashed'):
                latest_docs = ((latest.get('result') or {}).get('analysis') or {}).get('documents') or []
                same_file = latest_docs and ((doc.get('file_sha256') and latest_docs[0].get('file_sha256') == doc['file_sha256'])
                    or (doc.get('document_id') and latest_docs[0].get('document_id') == doc['document_id']))
                if same_file and latest_docs[0].get('report_year') == doc.get('report_year'):
                    return {'task_id':latest['id'], 'result':latest['result']}
            if base.get('source') != 'history_backfill':break
            base = core.task_by_id(base.get('parent_task_id') or '')
        return {'task_id':None, 'result':None}
