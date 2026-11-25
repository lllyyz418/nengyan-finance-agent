"""Archive existing local tasks; keep stored results and shared source PDFs intact."""
from typing import Any
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from history_index import HistoryReader
from report_identity import result_with_identity
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

HISTORY_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix='history-reader')
HISTORY_TIMEOUT_SECONDS = 10


class HistoryPatch(BaseModel):
    favorite: bool | None = None
    note: str | None = Field(default=None, max_length=2000)
    trashed: bool | None = None


def summarize_task(task: dict[str, Any], file_available: bool) -> dict[str, Any]:
    result = task.get("result") or {}
    payload = result.get("payload") or {}
    analysis = result.get("analysis") or {}
    documents = analysis.get("documents") or []
    document = documents[0] if documents else {}
    request = task.get("request") or {}
    event = analysis.get("event") or {}
    scan = result.get("scan") or {}
    counts=task.get('_history_counts') or {
        'metrics':len(analysis.get('metrics') or []),
        'risks':sum(item.get('status')=='triggered' for item in analysis.get('risk_assessments') or []),
        'warnings':len(analysis.get('warnings') or []),
        'unavailable':len(analysis.get('unavailable_metrics') or []),
        'candidates':len(scan.get('candidates') or [])}
    identity = payload.get("identity") or {}
    kind = ("scan" if task.get("source") == "scan" else "announcement" if event or payload.get("document_type") == "announcement"
            else "peer" if request.get("peer_codes") or payload.get("request", {}).get("peer_codes") else "financial")
    year = document.get("report_year") or identity.get("report_year") or request.get("report_year") or scan.get("report_year")
    company = (document.get("company_name") or identity.get("company_name") or task.get("company_name")
               or event.get("company_short_name") or event.get("reported_identity", {}).get("company_name")
               or request.get("company_name") or request.get("query") or "未识别公司")
    report_type = document.get("report_type") or request.get('report_type')
    period = f"{year}年" + ("半年度报告" if report_type in {"semiannual", "half_year", "half_year_report"} else "年度报告") if year else "期间未记录"
    if report_type=='quarterly':
        quarter=document.get('report_quarter') or request.get('report_quarter')
        period=f'{year}年'+('第一季度报告' if quarter==1 else '第三季度报告' if quarter==3 else '季度未识别')
    if kind == "announcement":
        period = event.get("reported_identity", {}).get("announcement_date") or event.get("published_at") or "公告日期未识别"
    status = task.get("status", "failed")
    if status == "completed":
        if analysis.get("review_status") == "requires_human_review":
            status = "review_required"
        elif not documents and kind in {"financial", "peer"}:
            status = "partial"
        elif payload.get("status") in {"partial", "failed", "unsupported"} or counts['warnings'] or counts['unavailable']:
            status = "partial"
    source = task.get("source")
    try:
        created_day = datetime.fromisoformat(task.get("created_at", "").replace("Z", "+00:00")).astimezone(timezone(timedelta(hours=8))).date().isoformat()
    except (ValueError, TypeError):
        created_day = str(task.get("created_at") or "")[:10]
    return {
        "id": task["id"], "source": source, "file_name": unquote(task.get("file_name") or ""), "kind": kind,
        "company": company, "stock_code": "" if (result.get('report_identity') or {}).get('status') == 'conflict' else document.get("security_code") or identity.get("stock_code") or task.get("stock_code") or event.get("stock_code") or "",
        "year": year, "period": period, "status": status, "task_status": task.get("status"),
        "created_at": task.get("created_at"), "updated_at": task.get("updated_at"),
        "created_day": created_day,
        "stage": task.get("stage"), "progress": task.get("progress", 0), "error": task.get("error"),
        "metrics": counts['metrics'], "risks": counts['risks'], "warnings": counts['warnings'],
        "version": result.get("engine_version") or "未记录", "file_hash": document.get("file_sha256") or scan.get("file_sha256") or payload.get("input", {}).get("file_sha256"),
        "favorite": bool(task.get("favorite")), "note": task.get("note", ""), "trashed": bool(task.get("trashed")),
        "has_result": bool(result), "has_metrics": bool(counts['metrics'] or counts['unavailable']),
        "has_report": bool(result.get("report_markdown")), "source_available": file_available,
        "can_retry": bool(task.get("request") is not None and (source in {"cninfo", "peer_compare"} or file_available)),
        "request": task.get("request"), "parent_task_id": task.get("parent_task_id"), "review_candidates": counts['candidates'],
    }


def register_history_routes(app, core):
    @app.get("/api/history")
    async def list_history(q: str = "", status: str = "", kind: str = "", year: int | None = None,
                     start_date: str = "", end_date: str = "", favorites: bool = False,
                     trashed: bool = False, page: int = 1, page_size: int = 10, sort: str = "favorites",include_source:bool=True):
        started=time.monotonic()
        diagnostic={'phase':'queued','task_id':None}
        future=asyncio.get_running_loop().run_in_executor(HISTORY_EXECUTOR,lambda:read_history(
            q,status,kind,year,start_date,end_date,favorites,trashed,page,page_size,sort,include_source,diagnostic))
        try:
            return await asyncio.wait_for(future,HISTORY_TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            logging.getLogger(__name__).warning('history_timeout phase=%s task=%s',diagnostic['phase'],diagnostic['task_id'])
            raise HTTPException(503,'历史摘要读取超时，本地记录仍保留。PDF 分析可能仍在运行，请稍后重试；无需删除历史数据。') from exc
        finally:
            elapsed=time.monotonic()-started
            if elapsed>2:
                logging.getLogger(__name__).warning('history_read seconds=%.2f page=%s size=%s source=%s',elapsed,page,page_size,include_source)

    def read_history(q,status,kind,year,start_date,end_date,favorites,trashed,page,page_size,sort,include_source,diagnostic):
        if page < 1 or not 1 <= page_size <= 100:
            raise HTTPException(400, "分页参数无效")
        records = []
        reader=HistoryReader(core)
        diagnostic.update(phase='enumerate',task_id=None)
        for task_id in reader.ids():
            diagnostic.update(phase='summary',task_id=task_id)
            task=reader.task_by_id(task_id)
            if task:
                diagnostic['phase']='identity'
                records.append(summarize_task({**task,'result':result_with_identity(reader,task)},False))
        diagnostic.update(phase='filter',task_id=None)
        visible = [r for r in records if r["trashed"] == trashed]
        statistics = {"total": len(visible), "completed": sum(r["status"] in {"completed", "partial"} for r in visible),
                      "review_required": sum(r["status"] == "review_required" for r in visible),
                      "failed": sum(r["status"] in {"failed", "interrupted"} for r in visible)}
        years = sorted({int(r["year"]) for r in visible if r["year"]}, reverse=True)
        query = q.strip().casefold()
        selected = [r for r in visible if
                    (not query or query in " ".join(str(r.get(key) or "") for key in ("company", "stock_code", "file_name", "note", "id")).casefold())
                    and (not status or r["status"] == status) and (not kind or r["kind"] == kind)
                    and (year is None or r["year"] == year) and (not favorites or r["favorite"])
                    and (not start_date or r["created_day"] >= start_date)
                    and (not end_date or r["created_day"] <= end_date)]
        selected.sort(key=lambda r: (False if sort == "recent" else r["favorite"], r["created_at"] or ""), reverse=True)
        start = (page - 1) * page_size
        items=selected[start:start+page_size]
        # Only the current page needs file-existence checks. The actual file
        # opening/analysis endpoints retain full library SHA-256 validation.
        for record in items if include_source else []:
            diagnostic.update(phase='source-availability',task_id=record['id'])
            try:available=bool(core.evidence_file_path(reader.task_by_id(record['id']),verify=False))
            except (OSError,ValueError):available=False
            record['source_available']=available
            record['can_retry']=bool(record['request'] is not None and (record['source'] in {'cninfo','peer_compare'} or available))
        diagnostic.update(phase='complete',task_id=None)
        return {"items": items, "total": len(selected), "page": page,
                "page_size": page_size, "statistics": statistics, "years": years}

    @app.patch("/api/history/{task_id}")
    def edit_history(task_id: str, patch: HistoryPatch):
        with core.TASK_LOCK:
            task = core.task_by_id(task_id)
            if not task:
                raise HTTPException(404, "历史记录不存在")
            changes = patch.model_dump(exclude_unset=True, exclude_none=True)
            if changes.get("trashed") and task["status"] in {"queued", "processing"}:
                raise HTTPException(409, "任务仍在运行，完成后可移入回收站")
            task.update(changes, updated_at=core.utc_now())
            core.persist_task(task)
            snapshot=dict(task)
        return summarize_task(snapshot, bool(core.evidence_file_path(snapshot,verify=False)))

    @app.get("/api/history/{task_id}/report")
    def history_report(task_id: str):
        result = core.get_result(task_id)
        markdown = result.get("report_markdown")
        if not markdown:
            raise HTTPException(409, "该任务尚未生成可下载的文字报告")
        return Response(markdown.encode("utf-8"), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="analysis-{task_id[:8]}.md"'})

    @app.post("/api/history/{task_id}/retry", status_code=202)
    def retry_history(task_id: str, background_tasks: BackgroundTasks):
        original = core.task_by_id(task_id)
        if not original:
            raise HTTPException(404, "历史记录不存在")
        if original["status"] in {"queued", "processing"}:
            raise HTTPException(409, "原任务仍在运行，请等待完成")
        config = original.get("request")
        if config is None:
            raise HTTPException(409, "旧任务未保存原配置，请从工作台重新选择文件或搜索公司")
        source = original["source"]
        if source == 'announcement_question':
            from announcement_api import AnnouncementQuestion
            return core.create_announcement_question_task(task_id, AnnouncementQuestion.model_validate({
                'question': config.get('question'), 'as_of_mode': config.get('as_of_mode','announcement'),
            }), background_tasks)
        if source == 'peer_compare':
            task = core.create_peer_task(config['base_task_id'], config)
            background_tasks.add_task(core.run_peer_task, task['id'])
            return core.public_task(task)
        if source == 'history_backfill':
            from backfill_api import BackfillRequest
            request = BackfillRequest.model_validate(config)
            task = core.create_backfill_task(config['base_task_id'], request.model_dump())
            background_tasks.add_task(core.run_backfill_task, task['id'])
            return core.public_task(task)
        path = core.evidence_file_path(original)
        if source != "cninfo" and not path:
            raise HTTPException(404, "原PDF文件已不存在，请重新上传")
        if source == "cninfo":
            if len(config.get('peer_codes') or [])>5:
                raise HTTPException(409,'旧任务超过当前五家同行上限。原结果可继续查看，请回工作台调整同行后重新计算。')
            try:
                request = core.OnlineAnalysisRequest.model_validate(config)
            except ValueError as exc:
                raise HTTPException(400, "历史任务配置无效，请重新设置") from exc
        task = core.create_task(source, original.get("file_name"), config)
        core.update_task(task["id"], parent_task_id=task_id)
        if source == "cninfo":
            background_tasks.add_task(core.run_online_task, task["id"], request)
        elif source == "scan":
            core.update_task(task["id"], file_path=str(path))
            background_tasks.add_task(core.run_scan_task, task["id"], path, config["page_numbers"], config["company_name"], config["report_year"])
        else:
            core.update_task(task["id"], file_path=str(path))
            background_tasks.add_task(core.run_upload_task, task["id"], path, config.get("ocr_mode", "off"))
        return core.public_task(task)
