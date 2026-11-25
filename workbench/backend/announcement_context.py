"""Host adapter: point-in-time financial backgrounds and related disclosures.

No path or URL supplied by the model is executed. Library records and discovered
official records are registered first and opened by opaque IDs only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from announcement_agent import GroundingError


def publication_date(record):
    """Date-only catalogue values are local dates; timestamps use UTC+8."""
    raw = record.get("published_date_cn") or record.get("published_at") or record.get("published_date")
    if not isinstance(raw, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
            return date.fromisoformat(raw)
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone(timedelta(hours=8))).date()
    except ValueError:
        return None


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class HostAnnouncementContext:
    def __init__(self, core, analysis, *, as_of_mode="announcement", today=None):
        self.core, self.analysis = core, analysis
        identity = (analysis.get("event") or {}).get("reported_identity") or {}
        self.code = identity.get("security_code")
        self.as_of_mode = as_of_mode
        raw_date = identity.get("announcement_date")
        self.event_date = date.fromisoformat(raw_date) if raw_date else None
        if as_of_mode not in {"announcement", "today"}:
            raise ValueError("Unsupported analysis date mode")
        self.cutoff = ((today or datetime.now(timezone(timedelta(hours=8))).date()) if as_of_mode == "today" else self.event_date)
        self.as_of = self.cutoff.isoformat() if self.cutoff else None
        self.report_records, self.related_records, self.loaded = {}, {}, {}
        self.library = None
        self.financial_loads, self.related_loads = 0, 0
        self.online = str(os.getenv("ANNOUNCEMENT_ALLOW_ONLINE", "0")).lower() in {"1", "true", "on"}

    def _dated_record(self, record):
        if publication_date(record) is not None or not getattr(self.core, "BASE_DIR", None):
            return record
        sidecar = self.core.BASE_DIR / "announcement_report_provenance.json"
        if not sidecar.is_file():
            return record
        try:
            bindings = json.loads(sidecar.read_text(encoding="utf-8"))
            if bindings.get("schema_version") != "1.0":
                return record
            for binding in bindings.get("records", []):
                source = re.fullmatch(r"https://static\.cninfo\.com\.cn/finalpage/(\d{4}-\d{2}-\d{2})/\d+\.PDF", binding.get("source_url", ""))
                if (binding.get("sha256") == record["sha256"] and binding.get("stock_code") == record["stock_code"]
                        and binding.get("report_year") == record["report_year"] and source
                        and binding.get("published_at") == source.group(1) and binding.get("verification_method") == "official_pdf_sha256_match"):
                    return {**record, "published_at": binding["published_at"], "source_url": binding["source_url"],
                            "publication_metadata_method": binding["verification_method"]}
        except (ValueError, KeyError, TypeError):
            pass
        return record

    def _ready(self):
        if not self.cutoff or not isinstance(self.code, str) or not re.fullmatch(r"\d{6}", self.code):
            return {"status": "unavailable", "reason": "缺少原文证券代码或有效分析日期，禁止猜测公司或默认使用未来财报。"}
        if (self.analysis.get("event") or {}).get("identity_quality") == "conflict":
            return {"status": "unavailable", "reason": "公告公司身份存在冲突，暂停外部数据联动。"}
        return None

    def list_financial_reports(self):
        if issue := self._ready():
            return issue
        try:
            self.library = self.core.get_report_library()
        except Exception:
            return {"status": "unavailable", "reason": "本地财报库索引不可用，需要先核对库路径和索引。"}
        if self.library is None:
            return {"status": "unavailable", "reason": "本机未接入本地财报库；本包不会附带或自动猜测财报文件。"}
        accepted, excluded = [], {"future_or_unknown_publication": 0, "version_ambiguous": 0}
        by_year = {}
        for record in self.library.records:
            if record.get("stock_code") != self.code:
                continue
            record = self._dated_record(record)
            published = publication_date(record)
            if (published is None or published > self.cutoff or type(record.get("report_year")) is not int
                    or date(record["report_year"], 12, 31) > self.cutoff):
                excluded["future_or_unknown_publication"] += 1
                continue
            by_year.setdefault(record["report_year"], []).append(record)
        for year, records in by_year.items():
            # Multiple different files require an explicit as-of version record,
            # not today's selected_for_comparison flag or model preference.
            if len({r["sha256"] for r in records}) != 1:
                excluded["version_ambiguous"] += len(records)
                continue
            record = records[0]
            key = "report:" + record["sha256"][:24]
            self.report_records[key] = record
            accepted.append({"report_id": key, "report_year": year, "published_date": publication_date(record).isoformat(),
                             "title": record.get("title") or str(year) + "年年度报告", "sha256": record["sha256"],
                             "financial_fields_validated": False})
        accepted.sort(key=lambda r: r["report_year"], reverse=True)
        return {"status": "found" if accepted else "not_found", "as_of": self.as_of, "items": accepted[:5], "excluded": excluded,
                "reason": "未找到符合时点和版本条件的本地报告。" if not accepted else "文件元数据通过不等于财务字段已核验。"}

    def _parsed_report(self, path, record):
        from financial_engine import analyze_reports
        from financial_engine.mining import _pdf_security_identity
        identity = _pdf_security_identity(path)
        if identity.get("stock_code") != self.code or identity.get("status") == "ambiguous":
            raise GroundingError("REPORT_ISSUER_MISMATCH")
        cache_root = self.core.PARSED_DIR / "announcement_context"
        cache_root.mkdir(parents=True, exist_ok=True)
        engine_dir = self.core.BASE_DIR / "financial-report-analysis/financial_engine"
        fingerprint = hashlib.sha256(b"".join((engine_dir / name).read_bytes() for name in ("core.py", "pdf_adapter.py", "note_adapter.py"))).hexdigest()
        cache = cache_root / (record["sha256"] + ".json")
        if cache.is_file() and cache.stat().st_size < 20 * 1024 * 1024:
            try:
                stored = json.loads(cache.read_text(encoding="utf-8"))
                cached_digest = hashlib.sha256(json.dumps(stored.get("analysis"), ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                if (stored.get("sha256") == record["sha256"] and stored.get("parser_fingerprint") == fingerprint
                        and stored.get("analysis_sha256") == cached_digest):
                    return stored["analysis"]
            except (ValueError, KeyError):
                pass
        parsed = analyze_reports([path])
        cache.write_text(json.dumps({"sha256": record["sha256"], "parser_fingerprint": fingerprint, "analysis": parsed,
                                     "analysis_sha256": hashlib.sha256(json.dumps(parsed, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}, ensure_ascii=False), encoding="utf-8")
        return parsed

    def load_financial_report(self, report_id):
        if issue := self._ready():
            return issue
        if report_id not in self.report_records or self.library is None:
            raise GroundingError("REPORT_MUST_BE_LISTED_FIRST")
        if report_id in self.loaded:
            return self.loaded[report_id]
        if self.financial_loads >= 1:
            return {"status": "unavailable", "reason": "本次分析最多解析一份财报，请聚焦最相关报告。"}
        record = self.report_records[report_id]
        path = self.library.verified_path(self.library.path_for(record), record["sha256"])
        self.financial_loads += 1
        parsed = self._parsed_report(path, record)
        docs = [d for d in parsed.get("documents", []) if d.get("file_sha256") == record["sha256"]]
        if len(docs) != 1 or docs[0].get("report_year") != record["report_year"]:
            raise GroundingError("REPORT_PERIOD_OR_HASH_MISMATCH")
        document = docs[0]
        source_id = report_id
        blocks, facts, withheld = [], [], []
        allowed = {"revenue", "total_assets", "attributable_equity", "operating_cash_flow", "net_profit", "attributable_net_profit"}
        candidates = [fact for fact in parsed.get("facts", []) if fact.get("document_id") == document["document_id"]
                      and fact.get("period") == str(record["report_year"]) and fact.get("metric_code") in allowed]
        grouped = {}
        for fact in candidates:
            grouped.setdefault(fact["metric_code"], []).append(fact)
        for code, rows in grouped.items():
            for fact in rows:
                reasons = []
                if len({str(r.get("value")) for r in rows}) != 1:
                    reasons.append("multiple_values")
                if fact.get("conflicting_values") or fact.get("quality") not in {"cross_checked", "note_rounding_difference"}:
                    reasons.append("unverified_or_conflicting")
                if fact.get("statement_scope") != "consolidated" or fact.get("unit") not in {"CNY", "yuan"}:
                    reasons.append("scope_or_unit")
                try:
                    if not isinstance(fact.get("value"), str) or not Decimal(fact["value"]).is_finite():
                        reasons.append("invalid_decimal")
                except InvalidOperation:
                    reasons.append("invalid_decimal")
                sources = fact.get("sources") or []
                if not sources or any(not (s.get("page_start") or s.get("page")) for s in sources):
                    reasons.append("missing_evidence")
                if reasons:
                    withheld.append({"metric_code": code, "reasons": reasons})
                    continue
                key = "fin:" + hashlib.sha256((record["sha256"] + fact["fact_id"]).encode()).hexdigest()[:24]
                normalized_sources = [{"page": s.get("page_start") or s.get("page"), "bbox": s.get("row_bbox") or s.get("bbox"),
                                       "file_sha256": record["sha256"], "document_id": document["document_id"]} for s in sources]
                label = {"revenue": "营业收入", "total_assets": "总资产", "attributable_equity": "归母权益",
                         "operating_cash_flow": "经营活动现金流量净额", "net_profit": "合并净利润", "attributable_net_profit": "归母净利润"}[code]
                text = f"{record['report_year']}年{label}为{fact['value']}元；报告披露日期{publication_date(record).isoformat()}；口径为合并报表。"
                blocks.append({"evidence_id": key, "text": text, "sources": normalized_sources, "source_id": source_id,
                               "source_type": "normalized_financial_fact", "quality": fact["quality"], "is_heading": False})
                facts.append({"evidence_id": key, "metric_code": code, "value": fact["value"], "currency": "CNY",
                              "period": fact["period"], "statement_scope": "consolidated", "quality": fact["quality"], "source_id": source_id})
                break
        output = {"status": "loaded" if facts else "unavailable", "as_of": self.as_of, "report_id": report_id,
                  "evidence_blocks": blocks, "financial_facts": facts, "withheld": withheld,
                  "sources": [{"source_id": source_id, "kind": "report_library", "relative_path": record["relative_path"],
                               "file_sha256": record["sha256"], "file_name": path.name, "source_url": record.get("source_url"),
                               "published_date": publication_date(record).isoformat()}],
                  "reason": "仅提供有原文证据、无冲突且跨章节核对通过的本期字段；未提取不等于零。"}
        self.loaded[report_id] = output
        return output

    def search_related_announcements(self, query):
        if issue := self._ready():
            return issue
        items = []
        scanned = 0
        for path in sorted(self.core.RESULT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:80]:
            scanned += 1
            if path.stat().st_size > 20 * 1024 * 1024:
                continue
            try:
                saved = json.loads(path.read_text(encoding="utf-8")).get("analysis") or {}
                event = saved.get("event") or {}
                identity = event.get("reported_identity") or {}
                announced = publication_date(event)
                if (identity.get("security_code") != self.code or announced is None or announced > self.cutoff
                        or (saved.get("document") or {}).get("file_sha256") == (self.analysis.get("document") or {}).get("file_sha256")):
                    continue
                corpus = (saved.get("title") or "") + "".join(b["text"] for b in saved.get("evidence_blocks", []))
                if query.lower() not in corpus.lower():
                    continue
                key = "local-ann:" + path.stem
                self.related_records[key] = {"saved": saved, "task_id": path.stem}
                items.append({"report_id": key, "title": saved.get("title"), "published_date": announced.isoformat(), "linkage": "candidate_only"})
            except (ValueError, TypeError, KeyError):
                continue
            if len(items) >= 5:
                break
        if self.online and len(items) < 5:
            from financial_engine.disclosure import resolve_security, discover_event_announcements
            try:
                security = resolve_security(self.code)
                start = max(self.cutoff - timedelta(days=365), self.event_date or self.cutoff - timedelta(days=365))
                found = discover_event_announcements(security, start.isoformat(), self.cutoff.isoformat(), keywords=(query,), max_pages_per_keyword=1)
                for record in found[-5:]:
                    if record.get("stock_code") != self.code or publication_date(record) is None or publication_date(record) > self.cutoff:
                        continue
                    key = "cninfo:" + record["announcement_id"]
                    self.related_records[key] = record
                    items.append({"report_id": key, "title": record["title"], "published_date": publication_date(record).isoformat(),
                                  "source_url": record["source_url"], "linkage": "candidate_only"})
            except Exception:
                return {"status": "found" if items else "unavailable", "items": items[:5],
                        "reason": "公开披露目录查询失败，本地候选保留；不能据此断言没有后续进展。"}
        return {"status": "found" if items else "no_match", "items": items[:5], "online_enabled": self.online,
                "local_results_scanned": scanned, "as_of": self.as_of,
                "limitation": "只搜索有限本地结果及可选官方目录最近一年；候选需核对是否同一事项，无命中不能证明无公告。"}

    def load_related_announcement(self, report_id):
        if report_id not in self.related_records:
            raise GroundingError("ANNOUNCEMENT_MUST_BE_SEARCHED_FIRST")
        if report_id in self.loaded:
            return self.loaded[report_id]
        if self.related_loads >= 1:
            return {"status": "unavailable", "reason": "本次最多读取一份相关公告，请聚焦一个候选。"}
        record = self.related_records[report_id]
        if report_id.startswith("local-ann:"):
            task = self.core.task_by_id(record["task_id"])
            path = self.core.evidence_file_path(task) if task else None
            saved = record["saved"]
            expected = (saved.get("document") or {}).get("file_sha256")
            if not path or file_hash(path) != expected:
                return {"status": "unavailable", "reason": "相关公告原 PDF 缺失或哈希不符，不能仅凭旧模型报告补证。"}
            source = {"source_id": report_id, "kind": "task_upload", "task_id": record["task_id"], "file_sha256": expected, "file_name": path.name}
        else:
            if not self.online:
                return {"status": "unavailable", "reason": "在线公告下载未启用。"}
            from financial_engine.disclosure import download_announcement
            destination = self.core.CACHE_DIR / "announcement_context"
            destination.mkdir(parents=True, exist_ok=True)
            downloaded = download_announcement(record, destination)
            path = Path(downloaded["local_path"])
            expected = file_hash(path)
            source = {"source_id": report_id, "kind": "announcement_cache", "cache_name": path.name,
                      "file_sha256": expected, "file_name": path.name, "source_url": record["source_url"]}
        from financial_engine import analyze_announcement
        self.related_loads += 1
        parsed = analyze_announcement(path, enable_ocr=False)
        identity = (parsed.get("event") or {}).get("reported_identity") or {}
        parsed_date = date.fromisoformat(identity.get("announcement_date") or "")
        if identity.get("security_code") != self.code or parsed_date > self.cutoff or parsed["document"]["file_sha256"] != expected:
            raise GroundingError("RELATED_ANNOUNCEMENT_IDENTITY_OR_DATE_MISMATCH")
        blocks = [{**block, "source_id": report_id, "source_type": "announcement_pdf"} for block in parsed.get("evidence_blocks", [])]
        output = {"status": "loaded", "title": parsed["title"], "evidence_blocks": blocks, "sources": [source],
                  "linkage": "candidate_only", "limitation": "同一公司或词语匹配不证明同一事项，必须核查主体、项目和协议。"}
        self.loaded[report_id] = output
        return output
