"""Versioned annual-history jobs. Current-period results are never overwritten."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import unquote

from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from financial_engine.core import analyze_parsed_documents
from financial_engine.disclosure import resolve_security, discover_annual_reports, latest_reports_by_year, download_report
from financial_engine.history import build_annual_history
from financial_engine.mining import _pdf_security_identity, _cached_records, _verify_cache
from financial_engine.pdf_adapter import parse_pdf
from task_logs import safe_text
from report_identity import resolve_identity
from financial_engine.security_identity import IDENTITY_VERSION
from financial_engine.periods import period_spec, report_label
from periodic_reports import build_period_history
from pdf_runtime import core_task_runtime

CACHE_VERSION = 'annual-history-8-policy-reviews'
LAUNCH_LOCK = threading.Lock()


def explain_error(error):
    text = safe_text(error)
    unit_page = re.search(r'Unit not found for table on PDF page (\d+)', text)
    if unit_page:
        return f'PDF 第 {unit_page[1]} 页表格未识别到明确金额单位，未采用该年度数据；请核对原文或补传可识别版本'
    return text


class BackfillRequest(BaseModel):
    lookback_years: int = Field(default=2, ge=0, le=5)
    file_tokens: list[str] = Field(default_factory=list, max_length=6)
    stock_code: str | None = Field(default=None, pattern=r'^\d{6}$')
    confirm_identity: bool = False
    online: bool = True


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def allowed_path(core, path) -> Path:
    resolved = Path(path).resolve()
    library = core.get_report_library()
    if library and resolved in library.by_path:
        return library.verified_path(resolved)
    if not resolved.is_file() or not any(resolved.is_relative_to(root.resolve()) for root in
                                        (core.UPLOAD_DIR, core.CACHE_DIR, core.BACKFILL_DIR)):
        raise ValueError('原文件不存在或不在受管理的文件目录中')
    return resolved


def context(core, task_id: str):
    task = core.task_by_id(task_id)
    if not task:
        raise HTTPException(404, '分析任务不存在')
    result = task.get('result') or {}
    docs = (result.get('analysis') or {}).get('documents') or []
    if not docs and task.get('source') == 'history_backfill':
        parent = core.task_by_id((task.get('request') or {}).get('base_task_id')) or {}
        docs = ((parent.get('result') or {}).get('analysis') or {}).get('documents') or []
    doc = docs[0] if docs else {}
    try:
        period_spec(doc)
        eligible=type(doc.get('report_year')) is int
    except (ValueError,KeyError):eligible=False
    return task, result, doc, eligible


def job_summary(core, base, job=None):
    _, result, doc, eligible = context(core, base['id'])
    saved = result.get('history_backfill') or {}
    latest_id = base.get('latest_backfill_task_id')
    latest = core.task_by_id(latest_id) if latest_id else None
    if job is None:
        job = base if base.get('source') == 'history_backfill' else latest
    requested = (job or {}).get('request') or base.get('request') or {}
    rows = (job or {}).get('backfill_rows') or []
    public_rows = [{key: row.get(key) for key in ('year', 'status', 'file_name', 'source', 'message', 'version_note')}
                   for row in rows]
    return {'eligible': eligible, 'company': doc.get('company_name') or '', 'report_year': doc.get('report_year'),
            'report_type':doc.get('report_type'),'report_quarter':doc.get('report_quarter'),
            'stock_code': (job or {}).get('verified_stock_code') or saved.get('stock_code') or doc.get('security_code'),
            'lookback_years': requested.get('lookback_years', 0),
            'task': core.public_task(job) if job else None, 'rows': public_rows,
            'rejected_files': (job or {}).get('backfill_rejected') or [],
            'pending': bool(not job and eligible and base.get('auto_backfill_pending') and base.get('session_id')==core.SESSION_ID and not base.get('auto_backfill_error')),
            'result_task_id': job['id'] if job and job.get('status') == 'completed' else None,
            'newer_result_task_id': latest['id'] if latest and latest.get('status') == 'completed' and latest['id'] != (job or {}).get('id') else None,
            'is_current_version': bool(job and job['id'] == base['id']),
            'message': base.get('auto_backfill_error', '') if eligible else '当前结果未识别到完整的定期报告及期间，暂不能回溯。'}


def create_job(core, base_id: str, config: dict):
    with LAUNCH_LOCK:
        return _create_job(core, base_id, BackfillRequest.model_validate(config).model_dump())


def _create_job(core, base_id: str, config: dict):
    base, _, doc, eligible = context(core, base_id)
    if not eligible:
        raise HTTPException(409, '请先完成定期报告本期分析，确认公司、年度与报告类型')
    path = core.evidence_file_path(base)
    if not path:
        raise HTTPException(409, '本期原文件已不存在，请重新上传')
    root_id = base.get('backfill_root_id') or base['id']
    root = core.task_by_id(root_id) or base
    latest = core.task_by_id(root.get('latest_backfill_task_id')) if root.get('latest_backfill_task_id') else None
    if latest and latest.get('status') in {'queued', 'processing'}:
        raise HTTPException(409, '历史回溯仍在运行，请完成后再调整范围或补传文件')
    for token in config.get('file_tokens', []):
        if not re.fullmatch(r'[a-f0-9]{32}', token):
            raise HTTPException(400, '无效的补传文件编号')
        manifest = core.BACKFILL_DIR / f'{token}.json'
        if not manifest.is_file() or json.loads(manifest.read_text(encoding='utf-8')).get('root_task_id') != root_id:
            raise HTTPException(400, '补传文件不属于本次报告，请重新选择')
    year = doc['report_year']
    records = {}
    for source in (root, base, latest or {}):
        records.update(source.get('backfill_files') or {})
        for entry in ((source.get('result') or {}).get('payload') or {}).get('retrieval', {}).get('files', []):
            if entry.get('status') == 'accepted' and entry.get('role') in {'history', 'local_history'}:
                records[str(entry.get('report_year'))] = entry
    task = core.create_task('history_backfill', f"{doc.get('company_name') or '公司'}-{year}-历史回溯", {
        **config, 'base_task_id': base_id, 'root_task_id': root_id,
        'options': {key: ((base.get('request') or {}).get('options') or base.get('request') or {}).get(key, default) for key, default in
                    [('nonrecurring_ratio_threshold', '0.30'), ('minimum_negative_cash_quarters', 3)]}})
    rows = [{'year': item, 'status': 'ready' if item == year else 'queued',
             'source': '本期原文件' if item == year else '', 'file_name': (doc.get('file_name') or root.get('file_name')) if item == year else '',
             'message': '本期结果已保存' if item == year else '等待核验本地文件或获取公开年报'}
            for item in range(year, year - config['lookback_years'] - 1, -1)]
    core.update_task(task['id'], parent_task_id=base_id, backfill_root_id=root_id,
                     file_path=str(path), backfill_files=records, backfill_rows=rows)
    core.update_task(root_id, latest_backfill_task_id=task['id'],auto_backfill_pending=False)
    if base_id != root_id:
        core.update_task(base_id, latest_backfill_task_id=task['id'])
    return task


def identity_for(core, path: Path):
    core.PARSED_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256((IDENTITY_VERSION + digest(path)).encode()).hexdigest()
    cache = core.PARSED_DIR / f'i-{key[:40]}.json'
    if cache.is_file():
        try:
            return json.loads(cache.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            pass
    identity = _pdf_security_identity(path)
    write_cache(cache, identity)
    return identity


def write_cache(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f'.cache-{uuid.uuid4().hex[:12]}.tmp'
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def report_analysis(core, path: Path, code: str, company: str, options: dict, report_type='annual', report_quarter=None):
    path = allowed_path(core, path)
    sha = digest(path)
    key = hashlib.sha256((sha + code + (company or '') + CACHE_VERSION + report_type+str(report_quarter)+json.dumps(options, sort_keys=True)).encode()).hexdigest()
    cache = core.PARSED_DIR / f'{key[:40]}.json'
    if cache.is_file():
        try:
            return json.loads(cache.read_text(encoding='utf-8')), sha, True
        except (ValueError, OSError):
            pass
    if report_type=='annual':parsed=parse_pdf(path)
    else:
        from financial_engine.interim import parse_interim_pdf
        parsed=parse_interim_pdf(path,report_type)
    if parsed.get('report_type') != report_type or report_type=='quarterly' and parsed.get('report_quarter')!=report_quarter:
        raise ValueError('补传文件报告类型或季度与本期不一致，不能混入同期趋势')
    identity = identity_for(core, path)
    if identity.get('status') == 'ambiguous' or identity.get('stock_code') not in {None, code}:
        raise ValueError('文件证券代码与本期公司不一致或存在多个代码')
    if not identity.get('stock_code') and parsed.get('company_name') != company:
        raise ValueError('文件未识别证券代码，且公司全称与本期报告不一致')
    parsed['security_code'] = code
    if report_type=='annual':analysis=analyze_parsed_documents([parsed],options)
    else:
        from financial_engine.interim import analyze_parsed_interim
        analysis=analyze_parsed_interim(parsed,options)
    analysis.setdefault('warnings', []).extend(parsed.get('parser_warnings') or [])
    write_cache(cache, analysis)
    return analysis, sha, False


def merge_analyses(analyses: list[dict], code: str):
    merged = {name: [] for name in ('documents', 'facts', 'metrics', 'unavailable_metrics', 'warnings')}
    for name in merged:
        seen = set()
        for analysis in analyses:
            for item in analysis.get(name) or []:
                key = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if key not in seen:
                    seen.add(key); merged[name].append(copy.deepcopy(item))
    for doc in merged['documents']:
        doc['security_code'] = code
    return merged


@core_task_runtime
def run_job(core, task_id: str):
    task = core.task_by_id(task_id)
    config = task['request']
    base, base_result, doc, _ = context(core, config['base_task_id'])
    spec=period_spec(doc);kind=spec['report_type'];quarter=spec['report_quarter']
    rows = copy.deepcopy(task['backfill_rows'])
    records = copy.deepcopy(task.get('backfill_files') or {})
    rejected = []

    def stage(year, status, message, **extra):
        for row in rows:
            if row['year'] == year:
                row.update(status=status, message=safe_text(message), **extra)
        core.update_task(task_id, backfill_rows=rows, backfill_files=records, backfill_rejected=rejected,
                         stage=f'{year} 年：{message}',parse_report_label=f'{year}年报告')

    def save_result(status='completed'):
        merged = merge_analyses(analyses, code) if code else copy.deepcopy(base_result['analysis'])
        comparison = build_period_history(merged,code,kind,quarter) if code else None
        wanted = [row['year'] for row in rows]
        available = [year for year in wanted if any(d.get('report_year') == year for d in merged.get('documents', []))]
        complete = all(row['status'] == 'ready' for row in rows)
        result = copy.deepcopy(base_result)
        if kind=='annual':
            from financial_engine.policy_review import refresh_policy_reviews
            # Only this newly saved version changes; original task/text stay intact.
            refresh_policy_reviews(result['analysis'],merged.get('documents',[]),merged.get('facts',[]))
        if code:
            result['analysis']['documents'][0]['security_code'] = code
            result.setdefault('payload', {})['identity'] = {'stock_code':code,
                'status':'manual_confirmation' if not identity.get('stock_code') else identity.get('status','verified_history'),
                'sources':identity.get('sources',[])}
        result.setdefault('payload', {})['history'] = {
            'calculation_version': CACHE_VERSION,'report_type':kind,'report_quarter':quarter,'period_basis':'cumulative',
            'status': 'not_requested' if not config['lookback_years'] else 'complete' if complete else 'partial',
            'requested_years': sorted(wanted), 'available_years': sorted(available),
            'missing_years': sorted(set(wanted) - set(available)), 'analysis': merged, 'comparison': comparison}
        result['payload']['status'] = 'complete' if complete else 'partial'
        retrieval = result['payload'].setdefault('retrieval', {})
        retrieval['files'] = [entry for entry in retrieval.get('files', []) if entry.get('role') not in {'history', 'local_history'}]
        retrieval['files'].extend({**entry, 'role':'history', 'status':'accepted'} for year, entry in records.items()
                                   if int(year) in wanted and any(row['year'] == int(year) and row['status'] == 'ready' for row in rows))
        result['evidence'] = {**core.evidence_index(merged), **result.get('evidence', {})}
        result['history_backfill'] = {'task_id': task_id, 'base_task_id':base['id'], 'root_task_id':config['root_task_id'],
                                      'lookback_years':config['lookback_years'], 'stock_code':code,
                                      'rows':rows, 'rejected_files':rejected,
                                      'note':'本期正文沿用原结果；年度趋势按当前解析器核验本地文件，另存版本，不覆盖原分析。'}
        (core.RESULT_DIR / f'{task_id}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        core.update_task(task_id, result=result, status=status, progress=100 if status == 'completed' else 15,
                         backfill_rows=rows, backfill_files=records, backfill_rejected=rejected,
                         stage='历史回溯完成' if complete else '历史回溯部分完成，可重试或手动补齐' if status == 'completed' else '请人工确认公司证券代码')

    code = None
    analyses = [copy.deepcopy(base_result['analysis'])]
    try:
        core.ensure_task_running(task_id)
        core.update_task(task_id, status='processing', stage='核验本期报告身份与年度', progress=10)
        path = allowed_path(core, task['file_path'])
        identity = identity_for(core, path) if config['lookback_years'] else {'stock_code':doc.get('security_code')}
        identity = resolve_identity(core, base, pdf_identity=identity)
        code = identity.get('stock_code')
        supplied = config.get('stock_code')
        if identity.get('status') == 'conflict' or code and supplied and code != supplied:
            raise ValueError('本期报告证券代码存在冲突，请重新核对或上传清晰的完整年报')
        if not code and supplied and config.get('confirm_identity'):
            code = supplied
            core.log_event(task_id, 'warning', '人工身份确认', f'用户已对照原文确认证券代码 {code}，此确认不证明文件真实性。')
        if not code and config['lookback_years']:
            for row in rows:
                if row['year'] != doc['report_year']:
                    row.update(status='needs_confirmation', message='本期 PDF 未能唯一识别证券代码，请核对后确认')
            save_result('review_required')
            return
        core.update_task(task_id, verified_stock_code=code)
        wanted = {row['year'] for row in rows if row['year'] != doc['report_year']}
        options = config.get('options') or {}
        # Refresh legacy current-period data only in this explicit history job,
        # never on GET or in the original task/report text.
        current_analysis = base_result['analysis']
        current_inputs = [*current_analysis.get('metrics', []), *current_analysis.get('unavailable_metrics', [])]
        if code and kind=='annual' and not any(item.get('metric_code') == 'liabilities_to_assets' and item.get('period') == str(doc['report_year']) for item in current_inputs):
            stage(doc['report_year'], 'parsing', '从本期本地年报补提取合并负债数据')
            try:
                refreshed, _, reused = report_analysis(core, path, code, doc.get('company_name'), options)
                if refreshed['documents'][0]['report_year'] != doc['report_year']:
                    raise ValueError('本期 PDF 内部年度与原分析不一致')
                analyses[0] = refreshed
                stage(doc['report_year'], 'ready', '本期年度趋势已按当前解析器核验，原正文保留',
                      version_note='复用当前解析缓存' if reused else '从本地原文件补提取，不覆盖旧分析')
            except Exception as exc:
                stage(doc['report_year'], 'ready', '沿用原本期数据，新增负债指标暂未补齐')
                core.log_event(task_id, 'warning', '本期负债数据未补齐', '原本期结果保留，新指标不估算。', str(exc))
        local_candidates = {}
        for token in config.get('file_tokens', []):
            manifest = json.loads((core.BACKFILL_DIR / f'{token}.json').read_text(encoding='utf-8'))
            local = core.BACKFILL_DIR / f'{token}.pdf'
            try:
                analysis, sha, reused = report_analysis(core, local, code, doc.get('company_name'), options,kind,quarter)
                year = analysis['documents'][0]['report_year']
                if year not in wanted:
                    raise ValueError('补传报告的年度不在所选历史范围中，或与本期年度重复')
                local_candidates.setdefault(year, {})[sha] = (analysis, local, manifest['file_name'], sha, reused)
            except Exception as exc:
                rejected.append({'file_name':manifest['file_name'], 'message':safe_text(exc)})
                core.log_event(task_id, 'warning', '补传校验', f"未采用文件：{manifest['file_name']}", str(exc))
        cache_warnings = []
        cache_records = _cached_records(core.CACHE_DIR.resolve(),cache_warnings,kind,quarter) if wanted else []
        for warning in cache_warnings:
            core.log_event(task_id, 'warning', '缓存检查', '跳过无法验证的缓存记录。', str(warning))
        remote = None
        remote_error = ''
        for index, year in enumerate(sorted(wanted, reverse=True)):
            core.ensure_task_running(task_id)
            core.update_task(task_id, progress=20 + int(index * 65 / max(1,len(wanted))))
            try:
                candidates = local_candidates.get(year, {})
                if str(year) in records:
                    entry = records[str(year)]
                    try:
                        saved_path = allowed_path(core, entry['local_path'])
                        if digest(saved_path) != entry['sha256']:
                            raise ValueError('文件校验值发生变化')
                    except (ValueError, OSError, KeyError):
                        records.pop(str(year))
                        core.log_event(task_id, 'warning', '缓存检查', f'{year} 年原缓存失效，将尝试其他来源。')
                if len(candidates) > 1:
                    raise ValueError('同一年度补传了多个不同版本，请只选择一个版本后重新补传')
                if candidates:
                    analysis, local, name, sha, reused = next(iter(candidates.values()))
                    note = '本次采用用户补传版本，旧文件及旧结果保留'
                    source = '手动补传'
                elif str(year) in records:
                    entry = records[str(year)]
                    local = allowed_path(core, entry['local_path'])
                    if digest(local) != entry['sha256']:
                        raise ValueError('已保存文件的校验值发生变化，请重新补传原文件')
                    stage(year, 'parsing', '核验并复用已取得的文件')
                    analysis, sha, reused = report_analysis(core, local, code, doc.get('company_name'), options,kind,quarter)
                    name = entry.get('file_name') or local.name
                    note = entry.get('version_note') or '复用当前任务链已选择的版本'
                    source = '本地缓存'
                else:
                    stage(year, 'retrieving', '查找本地财报库、缓存或公开披露')
                    library = core.get_report_library()
                    local_info = library.provide(code, year) if library and kind=='annual' else None
                    selected, _ = latest_reports_by_year([record for record in cache_records if record['stock_code'] == code], [year])
                    record = selected.get(year)
                    if local_info:
                        info = local_info
                        source = '本地财报库'
                    elif record:
                        info = _verify_cache(record)
                        source = '已校验公开报告缓存'
                    elif config.get('online', True) and core.library_online_enabled():
                        if remote is None:
                            try:
                                remote = discover_annual_reports(resolve_security(code), sorted(wanted),**({'report_type':kind,'report_quarter':quarter} if kind!='annual' else {}))
                            except Exception as exc:
                                remote = []; remote_error = safe_text(exc)
                        selected, _ = latest_reports_by_year(remote, [year])
                        record = selected.get(year)
                        if record is None:
                            raise ValueError(remote_error or f'公开披露中未找到该年度{report_label(kind,quarter)}')
                        stage(year, 'downloading', f'下载选定年度{report_label(kind,quarter)}')
                        info = download_report(record, core.CACHE_DIR)
                        source = '公开披露下载'
                    else:
                        raise ValueError('尚未取得此年度文件，可自动重试或手动补传')
                    local = allowed_path(core, info['local_path'])
                    stage(year, 'parsing', '解析并校验公司、年份与报表口径')
                    analysis, sha, reused = report_analysis(core, local, code, doc.get('company_name'), options,kind,quarter)
                    name = info.get('title') or local.name
                    version = info.get('version_label')
                    note = (f"本地财报库快照 {info.get('library_snapshot', '')}；不代表实时最新版本" if local_info else
                            {'original':'原始披露版本','revised':'修订披露版本','updated':'更新披露版本'}.get(version, '公开披露选定版本') + '；缓存不代表实时最新版本')
                if analysis['documents'][0]['report_year'] != year:
                    raise ValueError('PDF 内部报告年度与请求年度不一致')
                records[str(year)] = {'report_year':year, 'local_path':str(local), 'sha256':sha,
                                      'report_type':kind,'report_quarter':quarter,
                                      'file_name':name, 'document_id':analysis['documents'][0]['document_id'],
                                      'source':source, 'version_note':note}
                analyses.append(analysis)
                stage(year, 'ready', '已解析，可检查同期指标可用性', file_name=name, source=source,
                      version_note=note + ('；复用已保存解析结果' if reused else ''))
            except Exception as exc:
                stage(year, 'failed', explain_error(exc))
                core.log_event(task_id, 'warning', '历史年度未完成', f'{year} 年未取得有效结果', str(exc))
        core.ensure_task_running(task_id)
        core.update_task(task_id, stage='汇总跨年度指标与重述检查', progress=90)
        save_result()
    except core.TaskCancelled:
        core.update_task(task_id, status='interrupted', stage='已由用户停止', progress=task.get('progress', 0),
                         error='用户主动停止历史回溯；已取得的年度文件和结果会保留。', backfill_rows=rows, backfill_rejected=rejected)
    except Exception as exc:
        core.update_task(task_id, status='failed', stage='历史回溯失败，本期结果仍保留', progress=100,
                         error=safe_text(exc), backfill_rows=rows, backfill_rejected=rejected)


def register_backfill_routes(app, core):
    core.create_backfill_task = lambda base_id, config: create_job(core, base_id, config)
    core.run_backfill_task = lambda task_id: run_job(core, task_id)

    @app.get('/api/tasks/{task_id}/backfill')
    def summary(task_id: str):
        base, _, _, _ = context(core, task_id)
        return job_summary(core, base)

    @app.post('/api/tasks/{task_id}/backfill', status_code=202)
    def create(task_id: str, request: BackfillRequest, background_tasks: BackgroundTasks):
        task = create_job(core, task_id, request.model_dump())
        background_tasks.add_task(run_job, core, task['id'])
        return core.public_task(task)

    @app.post('/api/tasks/{task_id}/backfill/files', status_code=201)
    async def stage_file(task_id: str, request: Request):
        base, _, _, eligible = context(core, task_id)
        if not eligible:
            raise HTTPException(409, '请先完成本期定期报告分析')
        body = await request.body()
        name = Path(unquote(request.headers.get('x-filename') or 'history.pdf')).name
        if len(body) > core.MAX_FILE_SIZE:
            raise HTTPException(413, '文件超过80MB限制')
        if body[:5] != b'%PDF-' or Path(name).suffix.lower() != '.pdf':
            raise HTTPException(400, '仅支持有效 PDF 文件')
        token = uuid.uuid4().hex
        core.BACKFILL_DIR.mkdir(parents=True, exist_ok=True)
        (core.BACKFILL_DIR / f'{token}.pdf').write_bytes(body)
        (core.BACKFILL_DIR / f'{token}.json').write_text(json.dumps({
            'root_task_id':base.get('backfill_root_id') or base['id'], 'file_name':name}, ensure_ascii=False), encoding='utf-8')
        return {'token':token, 'file_name':name}

    @app.get('/api/tasks/{task_id}/backfill/files/{year}')
    def file(task_id: str, year: int):
        task, _, doc, _ = context(core, task_id)
        entry = (task.get('backfill_files') or {}).get(str(year))
        path = core.evidence_file_path(task) if doc.get('report_year') == year else None
        if entry:
            try:
                path = allowed_path(core, entry['local_path'])
            except ValueError:
                path = None
        if not path:
            raise HTTPException(404, '该年度没有可打开的本地原文件')
        return FileResponse(path, media_type='application/pdf', filename=f'{year}-{period_spec(doc)["report_type"]}.pdf', content_disposition_type='inline')
