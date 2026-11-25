from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from task_logs import append_event, register_log_routes
from report_summary import build_summary
from upload_errors import classify_upload_exception, public_failure, validate_upload_result
from pdf_runtime import task_runtime
from announcement_agent import run_evidence_agent, model_from_environment
from announcement_context import HostAnnouncementContext
from announcement_api import register_announcement_routes


BASE_DIR = Path(__file__).resolve().parent
ENGINE_DIR = BASE_DIR / "financial-report-analysis"
sys.path.insert(0, str(ENGINE_DIR))

from financial_engine import (  # noqa: E402
    ScanReviewError,
    analyze_disclosure_context as _engine_disclosure_context,
    analyze_reviewed_scan,
    analyze_scanned_financial_pages,
    analyze_uploaded_document,
    create_scan_review_template,
    resolve_security as _online_resolve_security,
)


load_dotenv(BASE_DIR / ".env", override=False)
UPLOAD_DIR = BASE_DIR / "data" / "uploads"
CACHE_DIR = BASE_DIR / "data" / "report_cache"
RESULT_DIR = BASE_DIR / "data" / "results"
TASK_DIR = BASE_DIR / "data" / "tasks"
LOG_DIR = BASE_DIR / "data" / "logs"
BACKFILL_DIR = BASE_DIR / "data" / "history_uploads"
PARSED_DIR = BASE_DIR / "data" / "history_parsed"
COMPANY_DIR = BASE_DIR / 'data' / 'companies'
BACKFILL_DIR.mkdir(parents=True, exist_ok=True)
PARSED_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)
TASK_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = 80 * 1024 * 1024
ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]

app = FastAPI(
    title="新能源财务洞察 Agent API",
    version="0.17.0",
    description="财报上传、公开披露检索、确定性财务分析、证据追溯与扫描件复核接口",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


from company_directory import COMPANIES, register_company_routes
from report_library import load_library, LibraryError
from report_identity import result_with_identity as _result_with_identity


def result_with_identity(task, **options):
    return _result_with_identity(sys.modules[__name__], task, **options)


def get_report_library():
    location = Path(os.getenv('FINANCE_LIBRARY_DIR') or CACHE_DIR.parent / 'report_library')
    return load_library(location if location.is_absolute() else BASE_DIR / location)


def library_online_enabled():
    # Preserve the existing online workflow unless the operator opts out.
    return os.getenv('FINANCE_LIBRARY_ALLOW_ONLINE', '1') == '1'


def resolve_security(query):
    from company_directory import directory, normalized
    key = normalized(query)
    key = re.sub(r'(sh|sz|bj)$', '', key) if re.fullmatch(r'\d{6}(sh|sz|bj)', key) else key
    matches = [item for item in directory(sys.modules[__name__])
               if key in {normalized(value) for value in [item['code'], item['name'], item.get('full_name',''), *item.get('aliases', [])]}]
    if len(matches) == 1:
        return {'stock_code':matches[0]['code'], 'company_short_name':matches[0]['name'], 'source':'local_company_directory'}
    if len(matches) > 1:
        raise LibraryError('公司名称匹配多个代码，请使用六位证券代码')
    if not library_online_enabled():
        raise LibraryError('本地目录未找到公司，且联网补齐已关闭，请使用已收录代码')
    return _online_resolve_security(query)


def analyze_disclosure_context(*args, **kwargs):
    task_id = kwargs.pop('_task_id', None)
    report_type=kwargs.pop('report_type','annual')
    report_quarter=kwargs.pop('report_quarter',None)
    if report_type!='annual':
        from periodic_reports import analyze_context
        return analyze_context(sys.modules[__name__],report_type=report_type,report_quarter=report_quarter,
            checkpoint=(lambda:ensure_task_running(task_id)) if task_id else (lambda:None),**kwargs)
    if task_id:
        kwargs['checkpoint'] = lambda: ensure_task_running(task_id)
        def report_progress(stage):
            ensure_task_running(task_id)
            if stage.startswith('正在解析 PDF：'):
                update_task(task_id, parse_report_label=stage.split('：',1)[1].split('（',1)[0])
            update_task(task_id,stage=stage)
        kwargs['progress_callback'] = report_progress
    library = get_report_library()
    if library:
        kwargs['local_report_provider'] = library.provide
    kwargs['fetch_online'] = bool(kwargs.get('fetch_online', False) and library_online_enabled())
    return _engine_disclosure_context(*args, **kwargs)

# 引擎内部继续使用稳定的英文代码；报告和网页只展示中文名称。
METRIC_LABELS = {
    'capital_expenditure_cash_paid_yoy':'购建长期资产支付现金同比增长率',
    'revenue_qoq':'营业收入单季度环比增长率','attributable_net_profit_qoq':'归母净利润单季度环比增长率',
    'attributable_net_profit_ex_nonrecurring_qoq':'扣非归母净利润单季度环比增长率','operating_cash_flow_qoq':'经营现金流单季度环比增长率',
    "revenue_yoy": "营业收入同比增长率",
    "revenue_q4_qoq": "营业收入第四季度环比增长率",
    "operating_cost_yoy": "营业成本同比增长率",
    "r_and_d_expense_yoy": "研发费用同比增长率",
    "net_profit_yoy": "合并净利润同比增长率",
    "attributable_net_profit_yoy": "归母净利润同比增长率",
    "attributable_net_profit_q4_qoq": "归母净利润第四季度环比增长率",
    "attributable_net_profit_ex_nonrecurring_yoy": "扣非归母净利润同比增长率",
    "attributable_net_profit_ex_nonrecurring_q4_qoq": "扣非归母净利润第四季度环比增长率",
    "operating_cash_flow_yoy": "经营活动现金流净额同比增长率",
    "operating_cash_flow_q4_qoq": "经营活动现金流净额第四季度环比增长率",
    "total_profit_yoy": "利润总额同比增长率",
    "total_profit_q4_qoq": "利润总额第四季度环比增长率",
    "gross_margin": "合并毛利率",
    "consolidated_net_margin": "合并净利率",
    "adjusted_attributable_net_margin": "扣非归母净利率",
    "cash_to_consolidated_profit": "现金利润比",
    "simplified_free_cash_flow": "简化自由现金流",
    "inventory_days": "总体存货周转天数",
    "receivables_days_approx": "应收账款周转天数（近似）",
    "return_on_assets": "总资产回报率",
    "return_on_attributable_equity": "归母权益回报率",
    "fixed_assets_cip_to_assets": "固定资产与在建工程占总资产比例",
    "r_and_d_expense_to_revenue": "研发费用率",
    "liabilities_to_assets": "资产负债率",
    "inventory_impairment_to_gross": "存货跌价准备覆盖比例",
    "receivables_over_1y_to_gross": "一年以上应收账款占比",
    "nonrecurring_to_attributable_profit": "非经常性损益占归母净利润比例",
}
RISK_LABELS = {
    "cash_collection_pressure": "现金回款压力筛查",
    "inventory_margin_pressure": "存货与毛利压力筛查",
    "receivables_aging_pressure": "应收账款账龄压力筛查",
    "capital_expansion_cash_pressure": "扩产与现金流压力筛查",
    "r_and_d_capitalization_policy": "研发资本化政策复核",
    "depreciation_policy_change": "固定资产折旧政策变化复核",
    "persistent_receivables_aging_pressure": "连续应收账款账龄压力筛查",
    "relative_gross_margin_pressure": "同行相对毛利率压力筛查",
}
RISK_STATUS_LABELS = {
    "reviewed": "已完成复核",
    "review_required": "发现变化，需复核",
    "manual_review": "需人工确认",
    "triggered": "已触发，建议复核",
    "not_triggered": "未触发",
    "insufficient_data": "数据不足",
    "not_evaluated": "尚未评估",
}

TASKS: dict[str, dict[str, Any]] = {}
_CANCELLED_TASKS: set[str] = set()
TASK_LOCK = threading.Lock()
SESSION_ID = uuid.uuid4().hex


def persist_task(task: dict[str, Any]) -> None:
    """Persist local task metadata/results so a backend restart does not erase them."""
    target = TASK_DIR / f"{task['id']}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows indexers/antivirus may briefly hold a just-written JSON file.
    # Keep the atomic replacement, retry only transient access-denied errors.
    for attempt in range(5):
        try:
            temporary.replace(target)
            from history_index import save_projection
            try:
                save_projection(TASK_DIR, task)
            except OSError:
                pass  # Derived index can be rebuilt; original task is authoritative.
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.02 * (attempt + 1))


def task_by_id(task_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[a-f0-9]{32}", task_id):
        return None
    task = TASKS.get(task_id)
    if task:
        return task
    path = TASK_DIR / f"{task_id}.json"
    if not path.is_file():
        return None
    try:
        task = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(task, dict) or task.get("id") != task_id:
        return None
    if task.get("status") in {"queued", "processing"} and task.get("session_id") != SESSION_ID:
        task.update(status="interrupted", stage="服务重启，任务已中断",
                    error="任务未完成时后端已停止，请重新分析。原文件及已有结果仍保留。", updated_at=utc_now())
        if task.get("runtime_log_version"):
            log_event(task_id, "warning", "任务中断", "恢复任务时发现后端曾停止，任务已标记中断。",
                      "原文件与已有日志仍保留，请从历史记录重新分析；不会自动续跑。")
        persist_task(task)
    TASKS[task_id] = task
    return task


def public_task(task: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in task.items() if key not in {"result", "file_path", "backfill_files", "backfill_rows"}}


class OnlineAnalysisRequest(BaseModel):
    query: str = Field(min_length=1)
    report_year: int = Field(ge=1990, le=2100)
    report_type: Literal['annual','semiannual','quarterly'] = 'annual'
    report_quarter: Literal[1,3] | None = None
    lookback_years: int = Field(default=0, ge=0, le=5)
    peer_codes: list[str] = Field(default_factory=list, max_length=5)
    peer_group: str | None = None
    selection_note: str | None = None
    nonrecurring_ratio_threshold: str = "0.30"
    minimum_negative_cash_quarters: int = Field(default=2, ge=2, le=4)

    @model_validator(mode='after')
    def valid_period(self):
        if self.report_type=='quarterly' and self.report_quarter not in (1,3):raise ValueError('季报请选择第一季度或第三季度')
        if self.report_type!='quarterly' and self.report_quarter is not None:raise ValueError('年报和半年报无需选择季度')
        return self

    @field_validator('peer_codes')
    @classmethod
    def validate_peer_codes(cls,codes):
        if codes and len(codes)<2:raise ValueError('同行比较至少需要两家，默认三家，最多五家')
        if len(set(codes))!=len(codes):raise ValueError('同行证券代码不能重复')
        if any(not re.fullmatch(r'\d{6}',code) for code in codes):raise ValueError('请使用六位 A 股证券代码')
        return codes


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_event(task_id: str, level: str, stage: str, message: str, details: str = "") -> None:
    append_event(LOG_DIR, task_id, level, stage, message, details)


def update_task(task_id: str, **changes: Any) -> None:
    with TASK_LOCK:
        if task_id in TASKS:
            # A late download/model response must never revive a stopped task.
            if task_id in _CANCELLED_TASKS and changes.get('status') != 'interrupted':
                return
            previous = (TASKS[task_id].get("stage"), TASKS[task_id].get("status"))
            TASKS[task_id].update(changes)
            TASKS[task_id]["updated_at"] = utc_now()
            task = TASKS[task_id]
            if changes.get("status") in {"completed", "failed", "interrupted", "review_required"}:
                task["finished_at"] = task["updated_at"]
            if task.get("runtime_log_version") and previous != (task.get("stage"), task.get("status")):
                level = {"completed": "success", "failed": "error", "interrupted": "warning", "review_required": "warning"}.get(task["status"], "info")
                details = task.get("error") or (("请对照本期 PDF 确认公司证券代码，确认前不下载历史年报。" if task.get('source') == 'history_backfill' else "候选需对照 PDF 原文人工确认，不会自动进入正式指标。") if task["status"] == "review_required" else "")
                log_event(task_id, level, task["stage"], task["stage"], details)
            persist_task(TASKS[task_id])


def task_cancelled(task_id: str) -> bool:
    with TASK_LOCK:
        return task_id in _CANCELLED_TASKS or (TASKS.get(task_id) or {}).get('status') == 'interrupted'


class TaskCancelled(Exception):
    pass


def ensure_task_running(task_id: str) -> None:
    if task_cancelled(task_id):
        raise TaskCancelled('任务已由用户停止')


def create_task(source: str, file_name: str | None = None,
                request_data: dict[str, Any] | None = None) -> dict[str, Any]:
    task_id = uuid.uuid4().hex
    task = {
        "id": task_id,
        "source": source,
        "file_name": file_name,
        "status": "queued",
        "stage": "等待处理",
        "progress": 0,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "error": None,
        "result": None,
        "request": request_data,
        "session_id": SESSION_ID,
        "favorite": False,
        "note": "",
        "trashed": False,
        "runtime_log_version": 1,
    }
    with TASK_LOCK:
        TASKS[task_id] = task
        log_event(task_id, "info", "任务创建", "任务已创建，等待处理。")
        persist_task(task)
    return task


def normalize_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else payload
    if "current" in analysis and isinstance(analysis.get("current"), dict):
        analysis = analysis["current"]
    return analysis


def evidence_index(analysis: dict[str, Any]) -> dict[str, Any]:
    documents = {item.get("document_id"): item for item in [*analysis.get("documents", []),*analysis.get('support_documents',[])] if isinstance(item, dict)}
    index: dict[str, Any] = {}
    for fact in analysis.get("facts", []):
        if not isinstance(fact, dict):
            continue
        fact_id = str(fact.get("fact_id") or "")
        if not fact_id:
            continue
        sources = fact.get("sources") or []
        source = sources[0] if sources and isinstance(sources[0], dict) else {}
        document = documents.get(fact.get("document_id"), {})
        index[fact_id] = {
            "id": fact_id,
            "document_id": fact.get("document_id"),
            "fact_id": fact_id,
            "metric_code": fact.get("metric_code"),
            "file": document.get("file_name") or source.get("file_name") or "财务报告.pdf",
            "file_hash": document.get("file_sha256") or "",
            "parser_version": document.get("parser_version") or document.get('parser') or "financial-engine v0.17",
            "page": source.get("page_start") or source.get("page") or 0,
            "heading_page": source.get("heading_page"),
            "section": source.get("section") or source.get("heading") or fact.get("label") or "财务披露",
            "excerpt": source.get("text") or source.get("raw_text") or fact.get("label") or "",
            "location": source.get("row_bbox") or source.get("bbox"),
            "original_field": source.get("label") or fact.get("label") or fact.get("metric_code"),
            "original_value": source.get("raw_value") or fact.get("raw_value") or fact.get("value"),
            "original_unit": source.get("raw_unit") or fact.get("raw_unit") or fact.get("unit"),
            "normalized_value": fact.get("value"),
            "period": fact.get("period"),
            "period_type": fact.get("period_type"),
            "statement_scope": fact.get("statement_scope"),
            "comparative_restated": fact.get("comparative_restated", False),
            "quality": fact.get("quality") or "extracted",
            "verification_status": source.get("verification_status") or fact.get("verification_status"),
        }
        if fact.get('quality')=='derived':
            parts=[f"{part.get('raw_value','')} {part.get('raw_unit','')}（PDF 第 {part.get('page_start','?')} 页）" for part in sources]
            index[fact_id].update(excerpt='累计相减推导，非独立披露值：'+' − '.join(parts)+f"；来源事实：{'、'.join(fact.get('input_fact_ids',[]))}",
                original_value='非独立披露值，详见上方双份来源',original_unit='',
                normalized_value=f"{fact.get('value')} 元（单季度推导值）",period_type='单季度期间 · 累计相减推导')
    for document in documents.values():
        for item in (document.get('policy_review') or {}).get('evidence', []):
            source=item.get('source') or {};eid=item.get('evidence_id')
            if not eid:
                continue
            index[eid]={
                'id':eid,'fact_id':eid,'document_id':document.get('document_id'),'evidence_kind':'policy',
                'metric_code':'会计政策披露','file':document.get('file_name') or '财务报告.pdf',
                'file_hash':document.get('file_sha256') or '',
                'parser_version':document.get('parser') or 'annual-policy-1',
                'page':source.get('page_start',0),'section':source.get('section','会计政策披露'),
                'excerpt':source.get('raw_text',''),'location':source.get('row_bbox'),
                'original_field':item.get('label','政策原文'),'original_value':'文字披露，详见原文摘录',
                'original_unit':'文字或表格披露','normalized_value':'原文披露，非计算值',
                'period':str(document.get('report_year','')),'period_type':'年度政策披露',
                'statement_scope':'consolidated','comparative_restated':False,'quality':'extracted',
            }
    return index


def format_metric_value(value: Any, unit: str | None) -> str:
    """Format engine Decimal strings for display without changing stored values."""
    if value is None:
        return "不可计算"
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return f"{value} {unit or ''}".strip()
    if unit == "percent":
        return f"{number.quantize(Decimal('0.01'))}%"
    if unit in {"times", "ratio"}:
        return f"{number.quantize(Decimal('0.01'))} 倍"
    if unit in {"CNY", "yuan"}:
        return f"{(number / Decimal('100000000')).quantize(Decimal('0.01'))} 亿元"
    if unit == "days":
        return f"{number.quantize(Decimal('0.01'))} 天"
    return f"{number.quantize(Decimal('0.01'))} {unit or ''}".strip()


def deterministic_report(analysis: dict[str, Any]) -> str:
    documents = analysis.get("documents") or []
    if documents:
        return build_summary(analysis,METRIC_LABELS,RISK_LABELS,RISK_STATUS_LABELS)
    document = documents[0] if documents and isinstance(documents[0], dict) else {}
    company = document.get("company_name") or document.get("company_short_name") or "目标公司"
    metrics = analysis.get("metrics") or []
    risks = analysis.get("risk_assessments") or []
    warnings = analysis.get("warnings") or []
    unavailable = analysis.get("unavailable_metrics") or []
    lines = [
        f"# {company}财务分析结果",
        "",
        "> 本报告由确定性财务引擎根据上传文件生成，未使用语言模型扩写，不构成投资建议。",
        "",
        "## 一、数据范围",
        "",
        f"- 文件：{document.get('file_name', '未识别')} ",
        f"- 报告年度：{document.get('report_year', '未识别')}",
        f"- 已提取事实：{len(analysis.get('facts') or [])} 项",
        f"- 已计算指标：{len(metrics)} 项；不可计算指标：{len(unavailable)} 项",
        "",
        "## 二、核心指标",
        "",
    ]
    for metric in metrics[:16]:
        code = metric.get("metric_code", "metric")
        label = METRIC_LABELS.get(code, code.replace("_", " "))
        lines.append(f"- {label}：{format_metric_value(metric.get('value'), metric.get('unit'))}")
    lines.extend(["", "## 三、风险筛查", ""])
    if risks:
        for risk in risks:
            rule_id = risk.get("rule_id", "规则")
            status = risk.get("status", "unknown")
            lines.append(
                f"- {RISK_LABELS.get(rule_id, rule_id)}："
                f"{RISK_STATUS_LABELS.get(status, status)}。{risk.get('summary', '')}"
            )
    else:
        lines.append("- 当前结果没有可展示的规则评估。")
    lines.extend(["", "## 四、数据限制", ""])
    lines.append(f"- 系统警告 {len(warnings)} 项；不可计算指标 {len(unavailable)} 项。")
    lines.append("- 未提取到的数据不能解释为零；筛查信号不是审计结论、信用评级或违规认定。")
    return "\n".join(lines)


def generate_announcement_report(analysis: dict[str, Any], task_id: str | None = None, *, question=None, as_of_mode="announcement") -> tuple[str, str]:
    """Announcement-only model workflow; retain the independent annual-report pipeline."""
    try:
        model, reason = model_from_environment()
    except Exception:
        model, reason = None, "provider_initialization_failed"

    def progress(stage):
        if task_id:
            ensure_task_running(task_id)
            update_task(task_id, stage=stage, progress=90)
            log_event(task_id, "info", stage, "正在核查公告原文及本轮可用背景资料。")

    context = HostAnnouncementContext(sys.modules[__name__], analysis, as_of_mode=as_of_mode)
    enriched, report, generator = run_evidence_agent(
        analysis, model=model, context=context, unavailable_reason=reason, question=question,
        checkpoint=(lambda: ensure_task_running(task_id)) if task_id else None, progress=progress,
    )
    analysis.update(enriched)
    if task_id:
        ensure_task_running(task_id)
        for call in enriched["llm_metadata"]["calls"]:
            log_event(task_id, "info" if call["status"] in {"accepted", "tools_executed"} else "warning",
                      "公告分析检查", f"第 {call.get('round', '?')} 轮：{call['status']}",
                      json.dumps(call, ensure_ascii=False))
        for action in enriched["llm_metadata"].get("tool_trace", []):
            # Keep logs compact: do not duplicate complete PDF excerpts/tool results.
            log_event(task_id, "warning" if action["status"] in {"rejected", "unavailable"} else "info",
                      action["label"], action["status"],
                      json.dumps({key:action[key] for key in ("name","status","error_code","elapsed_ms","output_sha256") if key in action},ensure_ascii=False))
    return report, generator


def generate_report(analysis: dict[str, Any]) -> tuple[str, str]:
    if analysis.get("event") is not None or (analysis.get("routing") or {}).get("document_type") == "announcement":
        return generate_announcement_report(analysis)
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        return deterministic_report(analysis), "deterministic_template"
    try:
        from openai import OpenAI

        compact = {
            "documents": analysis.get("documents"),
            "metrics": analysis.get("metrics"),
            "unavailable_metrics": analysis.get("unavailable_metrics"),
            "risk_assessments": analysis.get("risk_assessments"),
            "findings": analysis.get("findings"),
            "warnings": analysis.get("warnings"),
            "中文术语表": {**json.loads((BASE_DIR.parent / 'shared' / 'reportTerms.json').read_text(encoding='utf-8')),
                          **METRIC_LABELS, **RISK_LABELS, **RISK_STATUS_LABELS},
        }
        client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=120, max_retries=0)
        response = client.chat.completions.create(
            model=os.getenv("DEEPSEEK_MODEL", "").strip() or "deepseek-flash",
            temperature=0.2,
            max_tokens=4096,
            extra_body={"thinking": {"type": "disabled"}},
            messages=[{
                "role": "user",
                "content": "你是新能源行业财务分析助手。只使用下列JSON中的事实和指标，使用Markdown输出财务分析摘要。固定采用旧版结构：一、数据范围（项目/内容表）；二、核心指标，依次分为2.1增长与盈利、2.2利润率、2.3现金流、2.4营运效率与回报、2.5资产质量，各组使用指标/数值/单位表格，注明年度或季度，完整覆盖已有指标，不任意截取前若干项；三、风险复核，分3.1已触发信号、3.2未触发信号（规则/状态/关键观察表）、3.3未评估项（规则/原因表），必要时单列数据不足；四、证据限制，列明真实解析警告、不可用指标及缺失输入、口径限制。已触发项结合输入中实际条件和观测值说明；未触发项精炼列出关键观察，不重复展开大段模板。正文不堆放完整文件SHA-256或大量事实编号，证据明细保留在系统证据链；如引用事实编号、SHA-256、原文链接和文件名保持原样。保持旧版摘要的简明表格风格，不自行改变分组或新增大量技术元数据。不得补造数字，不得把watch信号写成已证实风险。正文中的指标、财务字段、表名及状态必须使用中文术语表中的中文名称，不直接展示英文内部代码；原因码应写成中文解释。materiality_assessed为false时应表述为尚未评估变化的重要性，不能据此判断风险严重程度。\n" + json.dumps(compact, ensure_ascii=False),
            }],
        )
        content = (response.choices[0].message.content or '').strip()
        if not content or response.choices[0].finish_reason != 'stop':
            raise ValueError('模型未返回完整文字报告，使用模板保留计算结果')
        return content, "deepseek"
    except Exception:
        return deterministic_report(analysis), "deterministic_template_fallback"


def finish_task(task_id: str, payload: dict[str, Any]) -> None:
    ensure_task_running(task_id)
    analysis = normalize_analysis(payload)
    if analysis.get("warnings") or analysis.get("unavailable_metrics"):
        log_event(task_id, "warning", "数据质量核对",
                  f"系统提示 {len(analysis.get('warnings') or [])} 项，不可计算指标 {len(analysis.get('unavailable_metrics') or [])} 项。",
                  "这是数据质量提示，不是财务风险认定。缺失数据不会以零替代，详情见指标可用性与报告。")
    log_event(task_id, "success", "引擎结果", f"已返回 {len(analysis.get('facts') or [])} 项财务事实及 {len(analysis.get('metrics') or [])} 项指标。")
    is_announcement = payload.get("document_type") == "announcement" or (analysis.get("routing") or {}).get("document_type") == "announcement"
    update_task(task_id, stage="公告证据与影响分析" if is_announcement else "保存财务摘要与证据", progress=90)
    if is_announcement:
        report, generator = generate_announcement_report(analysis, task_id)
    else:
        report, generator = deterministic_report(analysis), 'deterministic_template'
    ensure_task_running(task_id)
    log_event(task_id, "warning" if generator.endswith("fallback") else "info", "报告生成",
              {"deepseek": "AI 分析报告已生成。", "deterministic_template": "使用确定性模板生成报告。",
               "deterministic_template_fallback": "AI 报告生成未成功，已回退到确定性模板；财务计算结果保留。"}[generator])
    result = {
        "engine_version": "0.17",
        "payload": payload,
        "analysis": analysis,
        "report_markdown": report,
        "report_generator": generator,
        "evidence": evidence_index(analysis),
    }
    (RESULT_DIR / f"{task_id}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    update_task(task_id, status="completed", stage="分析完成", progress=100, result=result)
    from research_reports import automatic
    automatic(sys.modules[__name__],task_id)


def record_upload_failure(task_id: str, exc: Exception) -> None:
    if task_cancelled(task_id):
        return
    current = task_by_id(task_id) or {}
    failure = classify_upload_exception(exc, current.get('stage') or '文件处理')
    update_task(task_id, status="failed", stage="分析已结束",
                error=f"{failure['title']}：{failure['message']}", error_info=failure)
    log_event(task_id, "error", failure['stage'], failure['title'],
              f"错误编号：{failure['code']}；异常类型：{type(exc).__name__}；任务编号：{task_id}")


@task_runtime
def run_upload_task(task_id: str, path: Path, ocr_mode: Literal["auto", "force", "off"] = "off") -> None:
    try:
        ensure_task_running(task_id)
        update_task(task_id, status="processing", stage="报告类型识别", progress=15)
        log_event(task_id, "info", "文件解析", "开始读取已保存的 PDF。",
                  f"OCR 方式：{ {'auto': '自动识别', 'force': '强制识别', 'off': '关闭'}[ocr_mode]}。引擎返回前不提供逐页进度。")
        update_task(task_id, stage="财务事实提取", progress=35)
        config = (task_by_id(task_id) or {}).get('request') or {}
        options = {key: config[key] for key in ('nonrecurring_ratio_threshold', 'minimum_negative_cash_quarters') if key in config}
        payload = analyze_uploaded_document(path, ocr_mode=ocr_mode, options=options)
        ensure_task_running(task_id)
        validate_upload_result(payload)
        identified = result_with_identity({**(task_by_id(task_id) or {}),
            'result':{'analysis':normalize_analysis(payload),'payload':payload}}, inspect_pdf=True)
        if identified.get('report_identity'):
            payload = identified['payload']
            payload['analysis'] = identified['analysis']
            payload['identity'] = identified['report_identity']
            ensure_task_running(task_id)
        update_task(task_id, stage="一致性核对与指标计算", progress=72)
        from periodic_reports import enrich_upload
        payload=enrich_upload(sys.modules[__name__],payload,path,options,task_id)
        update_task(task_id,auto_backfill_pending=bool(config.get('lookback_years',0)))
        finish_task(task_id, payload)
    except TaskCancelled:
        return
    except Exception as exc:
        record_upload_failure(task_id, exc)
        return
    # The original result is already complete; a backfill failure must not undo it.
    if config.get('lookback_years', 0):
        try:
            from backfill_api import BackfillRequest, context
            if context(sys.modules[__name__], task_id)[3]:
                child = create_backfill_task(task_id, BackfillRequest(lookback_years=config['lookback_years']).model_dump())
                run_backfill_task(child['id'])
            else:
                update_task(task_id, auto_backfill_pending=False,auto_backfill_error='未识别到可回溯的定期报告期间，请检查原文件')
        except Exception as exc:
            update_task(task_id, auto_backfill_pending=False,auto_backfill_error=str(exc))
            log_event(task_id, 'warning', '历史回溯未启动', '本期分析已保存，可在历史趋势区重试。', str(exc))


@task_runtime
def run_online_task(task_id: str, request: OnlineAnalysisRequest) -> None:
    try:
        ensure_task_running(task_id)
        update_task(task_id, status="processing", stage="查询公开披露", progress=12)
        security = resolve_security(request.query)
        log_event(task_id, "success", "公司身份核验", f"已识别公司：{security.get('company_short_name') or request.query}（{security['stock_code']}）。")
        update_task(task_id, company_name=security.get("company_short_name") or request.query,
                    stock_code=security["stock_code"])
        update_task(task_id, stage="优先读取本地财报，缺失时检索公开报告", progress=32)
        log_event(task_id, "info", "分析范围", f"报告年度 {request.report_year}，历史回溯 {request.lookback_years} 年，指定同行 {len(request.peer_codes)} 家。",
                  "优先使用已校验的本地财报库；缺失时按联网配置获取。初次解析可能需要数分钟。")
        payload = analyze_disclosure_context(
            _task_id=task_id,
            stock_code=security["stock_code"],
            report_year=request.report_year,
            report_type=request.report_type,
            report_quarter=request.report_quarter,
            lookback_years=0,
            peer_codes=request.peer_codes or None,
            peer_group=request.peer_group,
            selection_note=request.selection_note,
            cache_dir=CACHE_DIR,
            fetch_online=True,
            options={"nonrecurring_ratio_threshold": request.nonrecurring_ratio_threshold,
                     "minimum_negative_cash_quarters": request.minimum_negative_cash_quarters},
        )
        ensure_task_running(task_id)
        if payload.get("status") == "failed" or not payload.get("current"):
            warnings = payload.get("warnings") or payload.get("retrieval", {}).get("warnings") or []
            details = [str(item.get("message") or item.get("detail") or item.get("code"))
                       for item in warnings if isinstance(item, dict)]
            reason = "；".join(dict.fromkeys(details)) or "未找到符合条件的正式年度报告"
            raise RuntimeError(reason)
        update_task(task_id, stage="财务计算与风险筛查", progress=76)
        update_task(task_id,auto_backfill_pending=bool(request.lookback_years))
        finish_task(task_id, payload)
        if request.lookback_years:
            from backfill_api import BackfillRequest
            try:
                child=create_backfill_task(task_id,BackfillRequest(lookback_years=request.lookback_years).model_dump())
                run_backfill_task(child['id'])
            except TaskCancelled:raise
            except Exception as exc:
                update_task(task_id,auto_backfill_pending=False,auto_backfill_error=str(exc))
                log_event(task_id,'warning','历史回溯未启动','本期结果已保存，可在历史趋势区重试。',str(exc))
    except TaskCancelled:
        return
    except Exception as exc:
        update_task(task_id, status="failed", stage="公开报告分析失败", progress=100, error=str(exc))


@task_runtime
def run_scan_task(task_id: str, path: Path, pages: list[int], company: str, year: int) -> None:
    try:
        update_task(task_id, status="processing", stage="扫描页OCR识别", progress=30)
        log_event(task_id, "info", "扫描范围", f"对 PDF 物理页 {', '.join(map(str, pages))} 执行 OCR。")
        scan = analyze_scanned_financial_pages(path, page_numbers=pages, company_name=company, report_year=year)
        log_event(task_id, "info", "候选提取", f"已返回 {len(scan.get('candidates') or [])} 个候选、{len(scan.get('warnings') or [])} 条提示。")
        update_task(task_id, status="review_required", stage="等待人工复核", progress=65,
                    result={"engine_version": "0.17", "scan": scan, "source_path": str(path)})
    except Exception as exc:
        update_task(task_id, status="failed", stage="扫描页识别失败", progress=100, error=str(exc))


@app.get("/")
def root() -> dict[str, Any]:
    return {"name": app.title, "version": app.version, "docs": "/docs"}


@app.get("/api/health")
def health() -> dict[str, Any]:
    try:
        library = get_report_library()
        library_status = {'available':bool(library), 'pdf_count':len(library.records) if library else 0,
                          'companies_with_reports':len({r['stock_code'] for r in library.records}) if library else 0,
                          'snapshot':library.index.get('snapshot_version') if library else None}
    except (OSError, ValueError):
        library_status = {'available':False, 'error':'本地财报库索引不可用，请检查后端配置'}
    return {
        "status": "ok",
        "engine_version": "0.17",
        "llm_configured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
        "api_revision": "upload-errors-1",
        "runtime_revision": "responsive-analysis-1",
        "announcement_revision": "evidence-agent-1.1.0",
        'report_types':['annual','semiannual','quarterly'],
        "online_search": "available_when_network_is_reachable" if library_online_enabled() else "disabled_by_configuration",
        "report_library": library_status,
    }


@app.post("/api/tasks/upload", status_code=202)
async def upload_report(request: Request, background_tasks: BackgroundTasks,
                        ocr_mode: Literal["auto", "force", "off"] = "off",
                        lookback_years: int = Query(default=0, ge=0, le=5),
                        nonrecurring_ratio_threshold: float = Query(default=0.30, ge=0, le=1),
                        minimum_negative_cash_quarters: int = Query(default=3, ge=2, le=4)) -> dict[str, Any]:
    original_name = Path(unquote(request.headers.get("x-filename") or "report.pdf")).name
    if Path(original_name).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail=public_failure("FILE_TYPE_UNSUPPORTED"))
    body = await request.body()
    if len(body) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=public_failure("FILE_TOO_LARGE"))
    if body[:5] != b"%PDF-":
        raise HTTPException(status_code=400, detail=public_failure("INVALID_PDF"))
    task = create_task("upload", original_name, {"ocr_mode": ocr_mode, 'lookback_years':lookback_years,
        'nonrecurring_ratio_threshold':str(nonrecurring_ratio_threshold), 'minimum_negative_cash_quarters':minimum_negative_cash_quarters})
    path = UPLOAD_DIR / f"{task['id']}.pdf"
    try:
        path.write_bytes(body)
    except OSError as exc:
        record_upload_failure(task['id'], exc)
        return public_task(task)
    update_task(task["id"], file_path=str(path))
    log_event(task["id"], "success", "文件保存", f"PDF 已保存至本地，大小 {len(body) / 1024 / 1024:.2f} MB。")
    background_tasks.add_task(run_upload_task, task["id"], path, ocr_mode)
    return public_task(task)


@app.post("/api/tasks/online", status_code=202)
def online_report(request: OnlineAnalysisRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    if request.peer_codes and (not (request.peer_group or '').strip() or len((request.selection_note or '').strip())<6):
        raise HTTPException(status_code=400, detail="添加同行时必须填写业务分组和不少于六个字的选择理由")
    if request.query.strip() in request.peer_codes:
        raise HTTPException(status_code=400, detail="目标公司不能同时作为同行")
    task = create_task("cninfo", f"{request.query}-{request.report_year}", request.model_dump())
    background_tasks.add_task(run_online_task, task["id"], request)
    return public_task(task)


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str) -> dict[str, Any]:
    with TASK_LOCK:
        task = task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="分析任务不存在")
        return public_task(task)


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str) -> dict[str, Any]:
    with TASK_LOCK:
        task = task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="分析任务不存在")
        if task.get('status') in {'completed', 'failed', 'interrupted', 'review_required'}:
            return public_task(task)
        _CANCELLED_TASKS.add(task_id)
        progress = task.get('progress', 0)
    update_task(task_id, status='interrupted', stage='已由用户停止', progress=progress,
                error='用户主动停止任务；已保留原文件和已有日志。')
    log_event(task_id, 'warning', '任务停止', '用户主动停止了当前分析任务。', '已停止后续阶段，不会继续生成报告。')
    return public_task(task_by_id(task_id) or {'id': task_id, 'status': 'interrupted', 'stage': '已由用户停止', 'progress': progress})


@app.get("/api/tasks/{task_id}/result")
def get_result(task_id: str) -> dict[str, Any]:
    with TASK_LOCK:
        task = task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="分析任务不存在")
        if task["status"] not in {"completed", "review_required"}:
            raise HTTPException(status_code=409, detail="分析任务尚未完成")
        snapshot=dict(task)
    result=result_with_identity(snapshot)
    from history_basis_view import enrich_history
    result=enrich_history(result)
    request_options = snapshot.get('request') or {}
    request_options = request_options.get('options') or request_options
    if request_options.get('minimum_negative_cash_quarters') is not None:
        result = {**result, 'analysis_parameters': {
            'minimum_negative_cash_quarters': request_options['minimum_negative_cash_quarters']
        }}
    analysis=result.get('analysis') or {}
    docs=analysis.get('documents') or []
    if docs and docs[0].get('report_type') in {'annual','annual_report','semiannual','half_year','half_year_report','quarterly'}:
        return {**result,'report_summary_markdown':build_summary(analysis,METRIC_LABELS,RISK_LABELS,RISK_STATUS_LABELS)}
    return result


def evidence_file_path(task: dict[str, Any], evidence: dict[str, Any] | None = None, *, verify: bool = True) -> Path | None:
    """Resolve upload or cached source only inside the local report storage roots."""
    result = task.get("result") or {}
    payload = result.get("payload") or {}
    document_id = (evidence or {}).get("document_id") or str((evidence or {}).get("fact_id", "")).split(":")[0]
    records = payload.get("retrieval", {}).get("files", [])
    candidates = []
    if evidence:
        candidates.extend(record.get("local_path") for record in records
                          if record.get("document_id") == document_id and record.get("status") == "accepted")
    else:
        candidates.extend(record.get("local_path") for record in records
                          if record.get("role") in {"target", "input"} and record.get("status") == "accepted")
    # Uploaded and reviewed scans each have one original file. Do not fall back
    # to a target PDF if a peer/history fact references a different document.
    own_documents = (result.get('analysis') or {}).get('documents') or []
    if task.get("file_path") and (not evidence or not document_id or any(doc.get('document_id') == document_id for doc in own_documents)):
        candidates.append(task["file_path"])
    analyses = [result.get('analysis') or {}, payload.get('current') or {}, (payload.get('history') or {}).get('analysis') or {}]
    analyses.extend(((payload.get('peers') or {}).get('reports') or {}).values())
    documents = [doc for analysis in analyses for doc in analysis.get('documents', [])]
    wanted_doc = next((doc for doc in documents if doc.get('document_id') == document_id), None) if evidence else next(iter(own_documents), None)
    library = get_report_library()
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).resolve()
        if path.is_file() and any(path.is_relative_to(root.resolve()) for root in (UPLOAD_DIR, CACHE_DIR, BACKFILL_DIR)):
            return path
        if library and path in library.by_path:
            expected = (wanted_doc or {}).get('file_sha256')
            # A library path alone is not authority: it must belong to an
            # accepted document of this task and match its saved file hash.
            accepted = next((record for record in records if record.get('status') == 'accepted'
                             and record.get('local_path') and Path(record['local_path']).resolve() == path
                             and record.get('sha256') == expected), None)
            if expected and accepted:
                try:
                    if not verify:
                        # History availability is not an integrity certification.
                        # Keep containment/manifest binding, defer hashing to use.
                        library.verified_metadata(path,expected)
                        return path
                    return library.verified_path(path, expected)
                except (OSError, ValueError):
                    continue
    return None


@app.get("/api/tasks/{task_id}/file")
def get_task_file(task_id: str, fact_id: str | None = None, document_id: str | None = None):
    with TASK_LOCK:
        task = task_by_id(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="分析任务不存在")
        evidence = None
        if fact_id:
            evidence = ((task.get("result") or {}).get("evidence") or {}).get(fact_id)
            if not evidence and fact_id.startswith('research:'):
                from research_reports import excerpt_evidence
                evidence=excerpt_evidence(sys.modules[__name__],task_id,fact_id)
            if not evidence:
                raise HTTPException(status_code=404, detail="未找到该事实的原文证据")
        elif document_id:
            evidence = {'document_id':document_id}
        snapshot=dict(task)
    if evidence and evidence.get('evidence_kind')=='research':
        from research_reports import excerpt_file
        file_path=excerpt_file(sys.modules[__name__],evidence)
    else:file_path = evidence_file_path(snapshot, evidence)
    if not file_path:
        raise HTTPException(status_code=404, detail="该任务没有可打开的本地原文件")
    return FileResponse(file_path, media_type="application/pdf",
                        filename=(evidence or {}).get("file") or file_path.name,
                        content_disposition_type="inline")


@app.get("/api/tasks/{task_id}/evidence/{fact_id}")
def get_evidence(task_id: str, fact_id: str) -> dict[str, Any]:
    if fact_id.startswith('research:'):
        from research_reports import excerpt_evidence,excerpt_file
        evidence=excerpt_evidence(sys.modules[__name__],task_id,fact_id)
        return {**evidence,'source_available':bool(excerpt_file(sys.modules[__name__],evidence))}
    result = get_result(task_id)
    evidence = (result.get("evidence") or {}).get(fact_id)
    if not evidence:
        raise HTTPException(status_code=404, detail="未找到该事实的原文证据")
    task = task_by_id(task_id)
    return {**evidence, "source_available": bool(task and evidence_file_path(task, evidence))}


@app.post("/api/scan/tasks", status_code=202)
async def scan_pages(request: Request, background_tasks: BackgroundTasks, page_numbers: str,
                     company_name: str, report_year: int) -> dict[str, Any]:
    try:
        pages = sorted({int(item.strip()) for item in page_numbers.split(",") if item.strip()})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="页码必须为逗号分隔的整数") from exc
    if not pages or any(page < 1 for page in pages):
        raise HTTPException(status_code=400, detail="至少提供一个有效PDF物理页码")
    body = await request.body()
    if len(body) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="文件超过80MB限制")
    if body[:5] != b"%PDF-":
        raise HTTPException(status_code=400, detail="文件内容不是有效PDF")
    task = create_task("scan", Path(unquote(request.headers.get("x-filename") or "scan.pdf")).name,
                       {"page_numbers": pages, "company_name": company_name, "report_year": report_year})
    path = UPLOAD_DIR / f"{task['id']}.pdf"
    path.write_bytes(body)
    update_task(task["id"], file_path=str(path))
    log_event(task["id"], "success", "文件保存", f"扫描件 PDF 已保存，大小 {len(body) / 1024 / 1024:.2f} MB。")
    background_tasks.add_task(run_scan_task, task["id"], path, pages, company_name, report_year)
    return public_task(task)


@app.post("/api/scan/{task_id}/review-template")
def scan_review_template(task_id: str) -> dict[str, Any]:
    result = get_result(task_id)
    scan = result.get("scan")
    if not scan:
        raise HTTPException(status_code=409, detail="该任务不是扫描候选任务")
    return create_scan_review_template(scan)


@app.post("/api/scan/{task_id}/review")
def submit_scan_review(task_id: str, review: dict[str, Any]) -> dict[str, Any]:
    result = get_result(task_id)
    scan = result.get("scan")
    source_path = result.get("source_path")
    if not scan or not source_path:
        raise HTTPException(status_code=409, detail="该任务不是扫描候选任务")
    try:
        log_event(task_id, "info", "人工复核", "已收到人工复核提交，正在校验确认内容。")
        reviewed = analyze_reviewed_scan(scan, review, source_path=source_path)
    except ScanReviewError as exc:
        log_event(task_id, "warning", "人工复核", "人工复核提交未通过校验。", str(exc))
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    analysis = normalize_analysis(reviewed)
    report, generator = deterministic_report(analysis),'deterministic_template'
    completed = {
        "engine_version": "0.17",
        "payload": reviewed,
        "analysis": analysis,
        "report_markdown": report,
        "report_generator": generator,
        "evidence": evidence_index(analysis),
    }
    update_task(task_id, status="completed", stage="人工复核完成", progress=100, result=completed)
    from research_reports import automatic
    automatic(sys.modules[__name__],task_id)
    return completed


from history_api import register_history_routes
from backfill_api import register_backfill_routes
from peer_api import register_peer_routes
from research_reports import register_research_routes

register_backfill_routes(app, sys.modules[__name__])
register_peer_routes(app, sys.modules[__name__])
register_company_routes(app, sys.modules[__name__])
register_history_routes(app, sys.modules[__name__])
register_log_routes(app, sys.modules[__name__])
register_research_routes(app, sys.modules[__name__])
register_announcement_routes(app, sys.modules[__name__])

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001)
