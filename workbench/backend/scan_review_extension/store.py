"""Transactional, restart-safe candidate snapshots and append-only review versions."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from financial_engine.scan_review import analyze_reviewed_scan, create_scan_review_template, _verify_pdf


class ReviewConflict(ValueError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(encode(value).encode("utf-8")).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


class ReviewStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    task_id TEXT PRIMARY KEY, scan_json TEXT NOT NULL,
                    source_path TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    task_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    request_id TEXT NOT NULL, body_hash TEXT NOT NULL,
                    review_json TEXT NOT NULL, result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(task_id, revision), UNIQUE(task_id, request_id),
                    FOREIGN KEY(task_id) REFERENCES sessions(task_id)
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    def retain(self, task_id, scan, source_path):
        create_scan_review_template(scan)  # Strict existing financial candidate contract.
        source = str(Path(source_path).resolve())
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM sessions WHERE task_id=?", (task_id,)).fetchone()
            if old and (digest(json.loads(old["scan_json"])) != digest(scan) or old["source_path"] != source):
                raise ReviewConflict("候选快照已变化，请新建扫描任务，不可覆盖已复核来源。")
            if not old:
                db.execute("INSERT INTO sessions VALUES (?,?,?,0,?)", (task_id, encode(scan), source, now()))
            db.commit()

    def state(self, task_id):
        with self.connection() as db:
            row = db.execute("SELECT * FROM sessions WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            scan = json.loads(row["scan_json"])
            latest = db.execute("SELECT * FROM reviews WHERE task_id=? ORDER BY revision DESC LIMIT 1", (task_id,)).fetchone()
            template = json.loads(latest["review_json"]) if latest else create_scan_review_template(scan)
            # Each new submission needs its own key, previous decisions remain editable.
            template.update(expected_revision=row["revision"], request_id=None)
            return {"extension_version": "1.0.0", "revision": row["revision"], "scan": scan,
                    "template": template, "result": json.loads(latest["result_json"]) if latest else None}

    def source(self, task_id):
        with self.connection() as db:
            row = db.execute("SELECT source_path,scan_json FROM sessions WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            return Path(row["source_path"]), json.loads(row["scan_json"])

    def history(self, task_id):
        with self.connection() as db:
            rows = db.execute("SELECT revision,request_id,review_json,created_at FROM reviews WHERE task_id=? ORDER BY revision", (task_id,)).fetchall()
        return [{"revision": r["revision"], "request_id": r["request_id"],
                 "created_at": r["created_at"], "review": json.loads(r["review_json"])} for r in rows]

    def submit(self, task_id, body, actor):
        if not isinstance(body, dict):
            raise ValueError("复核请求必须为 JSON 对象。")
        expected, key = body.get("expected_revision"), body.get("request_id")
        if type(expected) is not int or expected < 0 or not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", key):
            raise ValueError("需要有效的 expected_revision 和 request_id（8～128位字母数字、下划线或连字符）。")
        fingerprint = digest({"body": body, "actor": actor})
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            session = db.execute("SELECT * FROM sessions WHERE task_id=?", (task_id,)).fetchone()
            if not session:
                raise KeyError(task_id)
            _verify_pdf(session["source_path"], json.loads(session["scan_json"])["document"]["file_sha256"])
            existing = db.execute("SELECT * FROM reviews WHERE task_id=? AND request_id=?", (task_id, key)).fetchone()
            if existing:
                if existing["body_hash"] != fingerprint:
                    raise ReviewConflict("同一 request_id 已用于不同内容，请刷新状态后重新提交。")
                # Replays of older requests must not roll the host result back.
                if existing["revision"] != session["revision"]:
                    raise ReviewConflict("该请求已有更新的复核版本，请刷新状态。")
                return json.loads(existing["result_json"])
            if session["revision"] != expected:
                raise ReviewConflict("复核版本已更新，请加载最新版本后再修改。")
            review = json.loads(encode(body))
            for field in ("expected_revision", "request_id"):
                review.pop(field, None)
            review.update(review_id=uuid4().hex, reviewed_at=now(),
                          reviewer={"id": actor["id"], "kind": "human", "identity_basis": actor["identity_basis"]})
            result = analyze_reviewed_scan(json.loads(session["scan_json"]), review,
                                           source_path=session["source_path"])
            revision = expected + 1
            result.update(review_revision=revision, request_id=key, extension_version="1.0.0",
                          reviewer_identity_basis=actor["identity_basis"])
            db.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?,?)",
                       (task_id, revision, key, fingerprint, encode(review), encode(result), review["reviewed_at"]))
            db.execute("UPDATE sessions SET revision=? WHERE task_id=?", (revision, task_id))
            db.commit()
            return result
