"""Additive host integration: replace only the three legacy scan handlers."""
from __future__ import annotations

import io
import re
import threading
from functools import wraps
from pathlib import Path
from urllib.parse import unquote

from fastapi import BackgroundTasks, HTTPException, Request
from fastapi.responses import Response
from financial_engine.scan_review import ScanReviewError, _verify_pdf

from .extract import analyze_scanned_financial_pages
from .ocr_backend import make_ocr
from .store import ReviewConflict, ReviewStore

_pdf_lock = threading.RLock()  # PDFium is not thread-safe across concurrent renders.
_submit_lock = threading.RLock()  # Serialize commit and host synchronization in this API process.


def serialized_submit(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with _submit_lock:
            return function(*args, **kwargs)
    return wrapped


def render_page(path, page_number, dpi=130):
    import pypdfium2 as pdfium
    with _pdf_lock, pdfium.PdfDocument(str(path)) as document:
        if not 1 <= page_number <= len(document):
            raise ValueError("PDF物理页码超出范围。")
        page = document[page_number - 1]
        try:
            width, height = page.get_size()
            if width * height * (dpi / 72) ** 2 > 24_000_000:
                raise ValueError("页面过大，请降低预览分辨率。")
            bitmap = page.render(scale=dpi / 72)
            try:
                image = bitmap.to_pil()
                try:
                    buffer = io.BytesIO()
                    image.save(buffer, format="PNG")
                finally:
                    image.close()
            finally:
                bitmap.close()
            return buffer.getvalue(), width, height
        finally:
            page.close()


def register_scan_review_routes(host):
    if getattr(host.app.state, "scan_review_extension", False):
        return
    store = ReviewStore(Path(host.BASE_DIR) / "data" / "scan_reviews" / "review.sqlite3")
    host.app.state.scan_review_store = store
    ocr = make_ocr(Path(host.BASE_DIR) / "data" / "scan_models")

    def valid_id(task_id):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", task_id):
            raise HTTPException(404, "扫描任务不存在。")

    def retain(task_id):
        valid_id(task_id)
        # The stored snapshot is authoritative after its first retention.
        try:
            store.source(task_id)
            return
        except KeyError:
            result = host.get_result(task_id)
            if not result.get("scan") or not result.get("source_path"):
                raise HTTPException(409, "该任务没有可复核的扫描候选。")
            store.retain(task_id, result["scan"], result["source_path"])

    def actor(request, body):
        resolver = getattr(host, "scan_review_actor", None)
        if resolver:
            value = resolver(request)
            if not isinstance(value, dict) or value.get("kind") != "human" or not isinstance(value.get("id"), str) or not value["id"].strip():
                raise HTTPException(403, "需要人工复核账号。")
            return {"id": value["id"].strip(), "identity_basis": "host_authenticated"}
        # Local prototype has no account system; disclose that identity is self-declared.
        if not request.client or request.client.host not in {"127.0.0.1", "::1", "localhost"}:
            raise HTTPException(403, "远程部署请接入后端 scan_review_actor 身份回调；本地原型仅接受本机复核。")
        name = (body.get("reviewer") or {}).get("id") if isinstance(body.get("reviewer"), dict) else None
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise HTTPException(400, "请填写人工复核人姓名或编号。")
        return {"id": name.strip(), "identity_basis": "local_self_declared"}

    def sync_result(task_id, reviewed):
        path, scan = store.source(task_id)
        analysis = reviewed["analysis"]
        report = host.deterministic_report(analysis)
        result = {"engine_version": "0.17", "extension_version": "1.0.0", "payload": reviewed,
                  "scan": scan, "source_path": str(path), "analysis": analysis,
                  "report_markdown": report, "report_generator": "deterministic",
                  "review_revision": reviewed["review_revision"], "review_status": reviewed["status"],
                  "evidence_index": {f["fact_id"]: f for f in analysis.get("facts", [])}}
        completed = reviewed["status"] == "ready"
        host.update_task(task_id, status="completed" if completed else "review_required", progress=100 if completed else 75,
                         stage="扫描数据复核完成" if completed else "已保存复核，仍有缺失或待处理项", result=result)
        return result

    def run_scan(task_id, path, pages, company, year):
        try:
            if hasattr(host, "ensure_task_running"):
                host.ensure_task_running(task_id)
            host.update_task(task_id, status="processing", stage="扫描页OCR识别", progress=15)
            with _pdf_lock:
                scan = analyze_scanned_financial_pages(path, page_numbers=pages, company_name=company,
                                                       report_year=year, ocr_engine=ocr)
            methods = [p["read_method"] for p in scan.get("page_geometries", [])]
            if methods and all(method == "ocr_unavailable" for method in methods):
                raise RuntimeError("OCR不可用，请检查依赖、模型下载或后端日志。")
            if hasattr(host, "ensure_task_running"):
                host.ensure_task_running(task_id)
            store.retain(task_id, scan, path)
            host.update_task(task_id, status="review_required", stage="候选已生成，等待人工复核", progress=65,
                             result={"engine_version": "0.17", "extension_version": "1.0.0", "scan": scan,
                                     "source_path": str(path), "review_revision": 0})
            host.log_event(task_id, "info", "候选提取", f"生成 {len(scan['candidates'])} 个候选，尚未进入计算。")
        except Exception as exc:
            host.update_task(task_id, status="failed", stage="扫描识别失败", error=str(exc), progress=100)

    replaced = {"/api/scan/tasks", "/api/scan/{task_id}/review-template", "/api/scan/{task_id}/review"}
    host.app.router.routes[:] = [r for r in host.app.router.routes
                                if not (getattr(r, "path", "") in replaced and "POST" in getattr(r, "methods", set()))]

    @host.app.post("/api/scan/tasks", status_code=202)
    async def upload(request: Request, background_tasks: BackgroundTasks, page_numbers: str,
                     company_name: str, report_year: int):
        try:
            if not re.fullmatch(r"\s*\d+(?:\s*,\s*\d+)*\s*", page_numbers):
                raise ValueError()
            pages = [int(p.strip()) for p in page_numbers.split(",")]
            if not 1 <= len(pages) <= 5 or len(set(pages)) != len(pages) or min(pages) < 1:
                raise ValueError()
        except ValueError:
            raise HTTPException(400, "请填写1～5个不重复的正整数物理页码，例如11,12。")
        if not company_name.strip() or len(company_name) > 120 or not 2000 <= report_year <= 2100:
            raise HTTPException(400, "需要有效公司名称和2000～2100报告年度。")
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > host.MAX_FILE_SIZE:
                raise HTTPException(413, "文件超过80MB限制。")
        if not data.startswith(b"%PDF-"):
            raise HTTPException(400, "文件内容不是有效PDF。")
        task = host.create_task("scan", Path(unquote(request.headers.get("x-filename") or "scan.pdf")).name,
                                {"page_numbers": pages, "company_name": company_name.strip(), "report_year": report_year})
        path = Path(host.UPLOAD_DIR) / f"{task['id']}.pdf"
        path.write_bytes(data)
        host.update_task(task["id"], file_path=str(path))
        background_tasks.add_task(run_scan, task["id"], path, pages, company_name.strip(), report_year)
        return host.public_task(task)

    @host.app.get("/api/scan/{task_id}/state")
    @serialized_submit
    def state(task_id: str):
        retain(task_id)
        value = store.state(task_id)
        if value["result"] and not (hasattr(host, "task_cancelled") and host.task_cancelled(task_id)):
            # Recover a crash between durable review commit and host JSON synchronization.
            try:
                current = host.get_result(task_id)
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                current = {}
            if current.get("review_revision") != value["revision"]:
                sync_result(task_id, value["result"])
        return value

    @host.app.post("/api/scan/{task_id}/review-template")
    def template(task_id: str):
        retain(task_id)
        return store.state(task_id)["template"]

    @host.app.get("/api/scan/{task_id}/reviews")
    def history(task_id: str):
        retain(task_id)
        return {"task_id": task_id, "reviews": store.history(task_id)}

    @host.app.get("/api/scan/{task_id}/pages/{page_number}")
    def page_image(task_id: str, page_number: int):
        retain(task_id)
        path, scan = store.source(task_id)
        try:
            _verify_pdf(path, scan["document"]["file_sha256"])
            data, width, height = render_page(path, page_number)
            return Response(data, media_type="image/png", headers={"Cache-Control": "no-store",
                            "X-PDF-Width": str(width), "X-PDF-Height": str(height),
                            "Access-Control-Expose-Headers": "X-PDF-Width, X-PDF-Height"})
        except (ValueError, RuntimeError, OSError) as exc:
            raise HTTPException(400, str(exc))

    @host.app.post("/api/scan/{task_id}/review")
    @serialized_submit
    def submit(task_id: str, review: dict, request: Request):
        retain(task_id)
        if hasattr(host, "task_cancelled") and host.task_cancelled(task_id):
            raise HTTPException(409, "任务已停止，不能提交新复核。")
        try:
            reviewed = store.submit(task_id, review, actor(request, review))
        except ReviewConflict as exc:
            raise HTTPException(409, str(exc))
        except (ScanReviewError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        result = sync_result(task_id, reviewed)
        host.log_event(task_id, "info", "人工复核", f"已保存复核版本{reviewed['review_revision']}，状态：{reviewed['status']}。")
        # LLM report generation stays under zyc's existing report workflow.
        return result

    host.app.state.scan_review_extension = True
    host.app.openapi_schema = None
