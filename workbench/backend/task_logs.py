"""Task-scoped execution events. Never serialize requests, secrets or PDF text."""
from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import HTTPException
from fastapi.responses import Response

LOG_LOCK = threading.Lock()
LEVELS = {"info": "信息", "success": "成功", "warning": "警告", "error": "错误"}


def safe_text(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"sk-[\w-]+", "[密钥已隐藏]", text, flags=re.I)
    text = re.sub(r"Bearer\s+[^\s,;\"']+", "Bearer [已隐藏]", text, flags=re.I)
    text = re.sub(r"((?:api[_-]?key|access[_-]?token|password|secret)\s*[\"']?\s*[:=]\s*)[^\s,;]+", r"\1[已隐藏]", text, flags=re.I)
    text = re.sub(r"[A-Za-z]:[\\/][^\n\"<>]*", "[本地路径]", text)
    return text.strip()[:4000]


def events_from_disk(root: Path, task_id: str) -> list[dict[str, Any]]:
    path = root / f"{task_id}.jsonl"
    if not path.is_file():
        return []
    events = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            try:
                event = json.loads(line)
                if isinstance(event, dict) and isinstance(event.get("seq"), int):
                    events.append(event)
            except json.JSONDecodeError:
                # Ignore a partial tail left by a forced process termination.
                continue
    return events


def append_event(root: Path, task_id: str, level: str, stage: str, message: str, details: str = "") -> None:
    if not re.fullmatch(r"[a-f0-9]{32}", task_id) or level not in LEVELS:
        raise ValueError("无效的日志任务或级别")
    with LOG_LOCK:
        root.mkdir(parents=True, exist_ok=True)
        existing = events_from_disk(root, task_id)
        seq = max((item["seq"] for item in existing), default=0) + 1
        event = {"id": f"{task_id}:{seq}", "seq": seq,
                 "timestamp": datetime.now(timezone.utc).isoformat(), "level": level,
                 "stage": safe_text(stage), "message": safe_text(message), "details": safe_text(details)}
        path = root / f"{task_id}.jsonl"
        # A newline separates an interrupted tail from the next valid record.
        with path.open("ab") as stream:
            stream.write(("\n" + json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))


def register_log_routes(app, core) -> None:
    def load(task_id: str):
        with core.TASK_LOCK:
            task = core.task_by_id(task_id)
            if not task:
                raise HTTPException(404, "分析任务不存在")
            metadata = {key: task.get(key) for key in (
                "id", "source", "file_name", "status", "stage", "progress", "created_at", "updated_at", "finished_at", "company_name")}
            analysis = (task.get("result") or {}).get("analysis") or {}
            documents = analysis.get("documents") or []
            if documents and isinstance(documents[0], dict):
                metadata["company_name"] = documents[0].get("company_name") or metadata.get("company_name")
        with LOG_LOCK:
            events = events_from_disk(core.LOG_DIR, task_id)
        return metadata, events

    @app.get("/api/tasks/{task_id}/logs")
    def logs(task_id: str, after_seq: int = 0, limit: int = 200,
             level: Literal["", "info", "success", "warning", "error"] = "", q: str = ""):
        if after_seq < 0 or not 1 <= limit <= 500:
            raise HTTPException(400, "日志序号或读取数量无效")
        task, all_events = load(task_id)
        matching = [event for event in all_events if event["seq"] > after_seq
                    and (not level or event["level"] == level)
                    and (not q or q.casefold() in (event["stage"] + event["message"] + event.get("details", "")).casefold())]
        selected = matching[:limit]
        has_more = len(matching) > limit
        cursor = selected[-1]["seq"] if has_more else max(after_seq, max((event["seq"] for event in all_events), default=0))
        return {"task": task, "events": selected, "next_seq": cursor, "has_more": has_more,
                "has_logs": bool(all_events), "total": len(all_events)}

    @app.get("/api/tasks/{task_id}/logs/download")
    def download(task_id: str, level: Literal["", "info", "success", "warning", "error"] = "", q: str = ""):
        task, events = load(task_id)
        lines = ["新能源财务洞察 Agent · 运行日志", f"任务编号：{task_id}",
                 f"文件：{safe_text(task.get('file_name'))}", "时间：北京时间（UTC+8）", ""]
        from datetime import timedelta
        for event in events:
            if level and event["level"] != level:
                continue
            if q and q.casefold() not in (event["stage"] + event["message"] + event.get("details", "")).casefold():
                continue
            timestamp = datetime.fromisoformat(event["timestamp"]).astimezone(timezone(timedelta(hours=8)))
            lines.append(f"{timestamp:%Y-%m-%d %H:%M:%S} [{LEVELS[event['level']]}] {event['stage']} · {event['message']}")
            if event.get("details"):
                lines.append(f"  {event['details']}")
        if not events:
            lines.append("该历史任务未记录详细运行日志。")
        return Response("\ufeff" + "\n".join(lines), media_type="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="task-{task_id}-logs.txt"'})
