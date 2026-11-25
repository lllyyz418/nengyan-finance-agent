"""Question jobs and hash-checked source PDF access for announcement tools."""
import copy
import hashlib
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pdf_runtime import task_runtime


class AnnouncementQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=2, max_length=1200)
    as_of_mode: Literal["announcement", "today"] = "announcement"

    @field_validator("question")
    @classmethod
    def meaningful_question(cls, value):
        if len(value.strip()) < 2:
            raise ValueError("请输入具体问题")
        return value.strip()


def register_announcement_routes(app, core):
    @task_runtime
    def run_question(task_id, original, question, as_of_mode):
        try:
            core.ensure_task_running(task_id)
            core.update_task(task_id, status="processing", stage="公告追问取证", progress=15)
            analysis = copy.deepcopy(original["analysis"])
            report, generator = core.generate_announcement_report(analysis, task_id, question=question, as_of_mode=as_of_mode)
            core.ensure_task_running(task_id)
            result = {"engine_version": "0.17", "payload": {"document_type": "announcement", "analysis": analysis},
                      "analysis": analysis, "report_markdown": report, "report_generator": generator,
                      "evidence": {}, "announcement_question": question}
            import json
            (core.RESULT_DIR / (task_id + ".json")).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            core.update_task(task_id, status="completed", stage="公告追问完成", progress=100, result=result)
            core.log_event(task_id, "success", "公告追问完成", "新结果已独立保存，原公告分析保持不变。")
        except core.TaskCancelled:
            return
        except Exception:
            core.update_task(task_id, status="failed", stage="公告追问失败", error="公告追问未完成，请核对模型配置、来源和任务日志。")

    def create_question_task(task_id, request, background_tasks):
        task = core.task_by_id(task_id)
        if not task or task.get("trashed"):
            raise HTTPException(404, "公告任务不存在")
        if task.get("status") != "completed":
            raise HTTPException(409, "请等待原公告分析完成")
        original = task.get("result") or {}
        analysis = original.get("analysis") or {}
        if not analysis.get("event"):
            raise HTTPException(400, "该任务不是支持的公告事件")
        path = core.evidence_file_path(task)
        expected = (analysis.get('document') or {}).get('file_sha256')
        try:
            if path is None or not expected:
                raise ValueError('missing source')
            with Path(path).open('rb') as stream:
                if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                    raise ValueError('changed source')
        except (OSError, ValueError):
            raise HTTPException(409, "原公告 PDF 已缺失或来源校验失败，请重新上传后再追问。") from None
        new = core.create_task("announcement_question", file_name=task.get("file_name"),
                               request_data={"question": request.question, "as_of_mode": request.as_of_mode, "parent_task_id": task_id})
        core.update_task(new["id"], parent_task_id=task_id, file_path=task.get("file_path"))
        background_tasks.add_task(run_question, new["id"], original, request.question, request.as_of_mode)
        return core.public_task(new)

    core.create_announcement_question_task = create_question_task

    @app.post("/api/tasks/{task_id}/announcement-question", status_code=202)
    def ask_announcement(task_id: str, request: AnnouncementQuestion, background_tasks: BackgroundTasks):
        return create_question_task(task_id, request, background_tasks)

    @app.get("/api/tasks/{task_id}/announcement-evidence/{evidence_id}")
    def announcement_evidence(task_id: str, evidence_id: str):
        task = core.task_by_id(task_id)
        if not task or task.get("trashed"):
            raise HTTPException(404, "任务不存在")
        analysis = (task.get("result") or {}).get("analysis") or {}
        block = next((item for item in analysis.get("evidence_blocks", []) if item["evidence_id"] == evidence_id), None)
        if block is None:
            raise HTTPException(404, "证据不存在")
        source_id = block.get("source_id")
        source = next((s for s in analysis.get("agent_sources", []) if s["source_id"] == source_id), None)
        try:
            if source is None:
                path = core.evidence_file_path(task)
                expected = (analysis.get("document") or {}).get("file_sha256")
            elif source["kind"] == "report_library":
                library = core.get_report_library()
                if library is None:
                    raise ValueError("missing library")
                path = library.verified_path(library.root / source["relative_path"], source["file_sha256"])
                expected = source["file_sha256"]
            elif source["kind"] == "task_upload":
                linked = core.task_by_id(source["task_id"])
                path = core.evidence_file_path(linked) if linked and not linked.get("trashed") else None
                expected = source["file_sha256"]
            elif source["kind"] == "announcement_cache":
                base = (core.CACHE_DIR / "announcement_context").resolve()
                path = (base / source["cache_name"]).resolve()
                if not path.is_relative_to(base):
                    raise ValueError("invalid cache location")
                expected = source["file_sha256"]
            else:
                raise ValueError("unknown source kind")
            if path is None or not path.is_file():
                raise ValueError("source missing")
            with Path(path).open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                    raise ValueError("source changed")
        except Exception:
            raise HTTPException(409, "证据原 PDF 缺失、路径无效或哈希不符，请重新核对来源。") from None
        return FileResponse(path, media_type="application/pdf", filename=path.name, content_disposition_type="inline")
