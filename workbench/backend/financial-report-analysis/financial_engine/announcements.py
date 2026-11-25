"""Conservative, sourced extraction of bid and investment announcement events.

Amounts here are disclosure-event candidates, not audited statement facts.
Foreign-currency amounts retain their currency; no exchange-rate conversion is
attempted. Nothing in this module is fed to the annual financial formulas.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

from .document_reader import classify_disclosure, read_pdf_document


_ROLES = (
    ("bid_amount", re.compile(r"中标(?:金额|价|合同金额)|中标价款")),
    ("contract_amount", re.compile(r"合同(?:总)?金额|合同价款")),
    ("fund_target_size", re.compile(r"基金(?:目标|募集)?规模")),
    ("company_commitment", re.compile(r"(?:拟|计划|将)?认缴出资额|(?:拟|计划)出资(?:额|金额)|公司(?:拟|计划)投资金额")),
    ("project_total_investment", re.compile(r"项目(?:总)?投资(?:金额|总额)|项目总投资|投资总额")),
)
_AMOUNT = re.compile(
    r"(?P<qualifier>不超过|不高于|不少于|约为|约|预计|拟)?\s*"
    r"(?P<prefix_currency>人民币|美元|欧元|港元|港币)?\s*"
    r"(?P<number>(?:\d{1,3}(?:[,，]\d{3})+|\d+)(?:[.．]\d+)?)\s*"
    r"(?P<scale>亿|万|千)?\s*(?P<currency>元人民币|美元|欧元|港元|港币|人民币|元)?"
)
_CURRENCIES = {"人民币": "CNY", "元人民币": "CNY", "元": "CNY", "美元": "USD", "欧元": "EUR", "港元": "HKD", "港币": "HKD"}
_FACTORS = {None: 1, "千": 1000, "万": 10000, "亿": 100000000}
_RISK_TERMS = ("尚未正式签署合同", "尚未签署合同", "存在不确定", "尚需", "未获批准",
               "投资周期长", "流动性较低", "不能实现预期", "风险。", "风险，", "风险；", "无法保证")
_HEADING = re.compile(r"^(?:[一二三四五六七八九十]+[、.．]|[（(][一二三四五六七八九十]+[）)]|\d+[、．]|\d+\.(?!\d))")
_DETAIL_PATTERNS = {
    "funding_source": r"资金来源|自有资金|自筹资金",
    "time_horizon": r"存续期限|存续期|投资期|退出期|建设期|工期",
    "exit_arrangements": r"退出机制|份额转让|退伙|清算方式",
    "consolidation_scope": r"合并报表|合并范围",
    "approval_conditions": r"审议通过|尚需|审批权限|无须提交|项目.{0,12}(?:核准|备案)|(?:须|需经).{0,12}(?:审批|核准|备案)",
    "event_progress": r"尚未.*签署|已签署|拟签署|拟通过|拟认缴|中标通知",
}


def _headline(document: dict, supplied: str | None) -> str:
    if supplied and supplied.strip():
        return supplied.strip()
    for page in document["pages"][:2]:
        for index, line in enumerate(page["lines"][:18]):
            if re.sub(r"\s+", "", line["text"]).startswith("关于"):
                parts = []
                for continuation in page["lines"][index:index + 5]:
                    parts.append(continuation["text"])
                    if "公告" in continuation["text"]:
                        return "".join(parts)
        for line in page["lines"][:18]:
            text = line["text"]
            if "公告" in text and "公告编号" not in text and "保证" not in text:
                return text
    return ""


def _event_type(title: str, document: dict) -> str | None:
    compact = re.sub(r"\s+", "", title)
    if "中标" in compact:
        return "bid_award"
    if "投资" in compact and "基金" in compact:
        return "fund_investment"
    if any(term in compact for term in ("扩产", "扩建", "投资建设", "项目投资", "对外投资")):
        return "investment_expansion"
    if "投资" in compact:
        return "investment_other"
    if "合同" in compact and any(term in compact for term in ("签署", "签订", "重大", "日常经营")):
        return "bid_award"
    return None


def _line_source(line: dict, document: dict) -> dict:
    return {"document_id": document["document_id"], "file_sha256": document["file_sha256"],
            "page": line["page"], "bbox": line["bbox"],
            "method": line["method"], "confidence": line["confidence"]}


def _amount_from_match(match: re.Match[str]) -> tuple[str, str, str]:
    raw_number = match.group("number").replace(",", "").replace("，", "").replace("．", ".")
    try:
        with localcontext() as context:
            context.prec = max(28, len(raw_number.replace(".", "")) + 10)
            value = Decimal(raw_number) * _FACTORS[match.group("scale")]
    except InvalidOperation as exc:
        raise ValueError(f"Invalid disclosed amount: {raw_number}") from exc
    currency = _CURRENCIES[match.group("prefix_currency") or match.group("currency")]
    amount = format(value, "f")
    if "." in amount:
        amount = amount.rstrip("0").rstrip(".")
    return amount, currency, currency


def _find_amount(text: str):
    for match in _AMOUNT.finditer(text):
        prefix, suffix = match.group("prefix_currency"), match.group("currency")
        if not prefix and not suffix:
            continue
        if prefix and suffix and suffix != "元" and _CURRENCIES[prefix] != _CURRENCIES[suffix]:
            continue
        return match
    return None


def _candidate_lines(document: dict):
    for page in document["pages"]:
        lines = page["lines"]
        for index, line in enumerate(lines):
            previous = lines[index - 1] if index else None
            prefix = ""
            if (previous and line["bbox"][1] - previous["bbox"][3] < 32
                    and not previous["text"].endswith(("。", "；", ";"))):
                prefix = previous["text"][-4:]
            # A label may end a physical line and its number start the next.
            following = lines[index + 1] if index + 1 < len(lines) else None
            continuation = ""
            if following and following["bbox"][1] - line["bbox"][3] < 32:
                continuation = following["text"]
            yield line, prefix + line["text"], continuation, following, previous, len(prefix)


def _extract_amounts(document: dict, event_type: str) -> list[dict]:
    grouped: dict[tuple[str, str, str], dict] = {}
    for line, text, continuation, following, previous, prefix_length in _candidate_lines(document):
        for role, label_pattern in _ROLES:
            if role in ("bid_amount", "contract_amount") and event_type != "bid_award":
                continue
            if role in ("fund_target_size", "company_commitment", "project_total_investment") and event_type == "bid_award":
                continue
            for label in label_pattern.finditer(text):
                tail = text[label.end():]
                # Do not jump across a sentence or a second field label.
                tail = re.split(r"[。；;]", tail, maxsplit=1)[0][:45]
                search_text = tail
                match = _find_amount(search_text)
                source_line = line
                if match is None and following and not re.search(r"[。；;]", tail):
                    search_text = tail + continuation[:36]
                    match = _find_amount(search_text)
                    if match is not None:
                        source_line = following
                if match is None:
                    continue
                # Avoid matching a later amount if another relevant label intervenes.
                before = search_text[:match.start()]
                if any(pattern.search(before) for _, pattern in _ROLES):
                    continue
                amount, currency, unit = _amount_from_match(match)
                key = role, amount, currency
                qualifier = (match.group("qualifier") or "").strip()
                if "拟" in text[:label.end()] or "计划" in text[:label.end()]:
                    qualifier = ("拟" + qualifier).strip()
                if key not in grouped:
                    grouped[key] = {"role": role, "raw_amount": match.group(0),
                                    "amount": amount, "currency": currency, "base_unit": unit,
                                    "qualifier": qualifier or None, "sources": [], "label_sources": [],
                                    "context": text[:240], "review_status": "candidate"}
                source = _line_source(source_line, document)
                if source not in grouped[key]["sources"]:
                    grouped[key]["sources"].append(source)
                label_lines = [previous, line] if previous and label.start() < prefix_length else [line]
                for label_line in label_lines:
                    label_source = _line_source(label_line, document)
                    if label_source not in grouped[key]["label_sources"]:
                        grouped[key]["label_sources"].append(label_source)
                if source["method"] == "ocr" or amount == "0" or qualifier in ("约", "约为", "预计", "不高于", "不超过", "拟不高于", "拟不超过"):
                    grouped[key]["review_status"] = "requires_human_review"
    return list(grouped.values())


def _paragraphs(document: dict) -> list[dict]:
    """Join physical lines, retaining every source box, including page breaks.

    Section headings are boundaries, never a substitute for their body. Only
    standalone page numbers are discarded. Short documents remain inexpensive
    enough to provide all paragraphs to the semantic analysis layer.
    """
    result, pending = [], []

    def flush():
        if not pending:
            return
        text = "".join(item["text"] for item in pending)
        result.append({"evidence_id": f"ann:{document['document_id']}:p{len(result) + 1:04d}",
                       "text": text, "sources": [_line_source(item, document) for item in pending],
                       "is_heading": bool(_HEADING.match(text) and len(text) <= 85)})
        pending.clear()

    for page in document["pages"]:
        for line in page["lines"]:
            text = line["text"].strip()
            if not text or re.fullmatch(r"(?:第\s*)?\d+(?:\s*页)?", text):
                continue
            heading = bool(_HEADING.match(text))
            if heading:
                flush()
            pending.append({**line, "text": text})
            # A standalone heading is flushed before the first body line.
            if (heading and len(text) <= 35 and not re.search(r"[。；;：:]", text)
                    or text.endswith(("。", "；", ";", "！", "？"))):
                flush()
    flush()
    return result


def _risk_clauses(document: dict, paragraphs: list[dict] | None = None) -> list[dict]:
    result = []
    in_risk_section = False
    for block in paragraphs if paragraphs is not None else _paragraphs(document):
        text = block["text"]
        if re.match(r"^(?:特此公告|[\u4e00-\u9fff]{4,40}股份有限公司董事会|20\d{2}\s*年\s*\d{1,2}\s*月)", text):
            in_risk_section = False
            continue
        heading = block["is_heading"]
        risk_heading = bool(heading and re.search(r"风险(?:提示|因素|分析)?", text))
        if heading:
            in_risk_section = risk_heading
            if risk_heading and len(text) < 40 and not re.search(r"[。；]", text):
                continue
        if in_risk_section or any(term in text for term in _RISK_TERMS):
            if "保证信息披露" in text or "保证公告内容" in text:
                continue
            result.append({"text": text, "source": block["sources"][0],
                           "sources": block["sources"], "evidence_id": block["evidence_id"],
                           "kind": "disclosed_clause", "is_risk_conclusion": False})
    return result


def _reported_identity(document: dict) -> dict:
    """Read printed identity as evidence, independently of catalogue metadata."""
    result: dict = {"security_code": None, "security_code_source": None,
                    "company_name": None, "company_name_source": None,
                    "announcement_date": None, "announcement_date_source": None}
    for page in document["pages"][:2]:
        for line in page["lines"][:15]:
            compact = re.sub(r"\s+", "", line["text"])
            code = re.search(r"(?:A股代码|证券代码)[:：]?(\d{6})(?!\d)", compact)
            if code and result["security_code"] is None:
                result["security_code"] = code.group(1)
                result["security_code_source"] = _line_source(line, document)
            company = re.fullmatch(r"([\u4e00-\u9fff]{4,40}股份有限公司)", compact)
            if company and result["company_name"] is None:
                result["company_name"] = company.group(1)
                result["company_name_source"] = _line_source(line, document)
    for page in reversed(document["pages"]):
        for line in reversed(page["lines"][-8:]):
            compact = re.sub(r"\s+", "", line["text"])
            date = re.fullmatch(r"(20\d{2})年(\d{1,2})月(\d{1,2})日", compact)
            if date:
                try:
                    from datetime import date as date_type
                    result["announcement_date"] = date_type(*map(int, date.groups())).isoformat()
                    result["announcement_date_source"] = _line_source(line, document)
                    return result
                except ValueError:
                    continue
    return result


def analyze_announcement(path: str | Path, *, metadata: dict | None = None,
                         ocr_engine=None, enable_ocr: bool = True,
                         force_ocr: bool = False,
                         max_pages: int = 20) -> dict:
    """Extract announcement event candidates with source pages and boxes.

    `metadata` can be a `discover_event_announcements` record. The API does
    not certify company identity, legal effectiveness, or project completion.
    """
    metadata = metadata or {}
    document = read_pdf_document(path, ocr_engine=ocr_engine,
                                 enable_ocr=enable_ocr, force_ocr=force_ocr,
                                 max_pages=max_pages)
    title = _headline(document, metadata.get("title"))
    routing = classify_disclosure(document, title=title)
    warnings = list(document["warnings"])
    if routing["document_type"] != "announcement":
        warnings.append({"code": "NOT_AN_ANNOUNCEMENT", "detected_type": routing["document_type"]})
        return {"schema_version": "1.0", "document": {key: value for key, value in document.items() if key != "pages"},
                "routing": routing, "title": title, "event": None,
                "warnings": warnings, "review_status": "unsupported"}
    event_type = _event_type(title, document)
    if event_type is None:
        warnings.append({"code": "EVENT_TYPE_UNSUPPORTED", "title": title})
    amounts = _extract_amounts(document, event_type) if event_type else []
    paragraphs = _paragraphs(document)
    for amount in amounts:
        matching = [block for block in paragraphs if any(source in block["sources"] for source in amount["sources"])]
        amount["evidence_ids"] = [block["evidence_id"] for block in matching]
        if matching:
            amount["context"] = matching[0]["text"]
    clauses = _risk_clauses(document, paragraphs)
    details = [{"field": field, "text": block["text"],
                "evidence_ids": [block["evidence_id"]], "method": "rule_candidate"}
               for field, pattern in _DETAIL_PATTERNS.items() for block in paragraphs
               if re.search(pattern, block["text"])]
    reported = _reported_identity(document)
    catalogue_code = metadata.get("stock_code")
    printed_code = reported["security_code"]
    identity_conflict = bool(catalogue_code and printed_code and catalogue_code != printed_code)
    if identity_conflict:
        warnings.append({"code": "SECURITY_CODE_CONFLICT", "catalogue": catalogue_code,
                         "printed": printed_code})
    identity_quality = ("conflict" if identity_conflict else
                        "catalogue_and_pdf_code_match" if catalogue_code and printed_code else
                        "catalogue_only" if catalogue_code else
                        "pdf_only_unverified" if printed_code else "unknown")
    if event_type and not amounts:
        warnings.append({"code": "EVENT_AMOUNT_NOT_FOUND"})
    if any(candidate["amount"] == "0" for candidate in amounts):
        warnings.append({"code": "ZERO_EVENT_AMOUNT", "detail": "Verify whether zero is an actual disclosed consideration"})
    role_values: dict[str, set[tuple[str, str]]] = {}
    for candidate in amounts:
        role_values.setdefault(candidate["role"], set()).add((candidate["amount"], candidate["currency"]))
    ambiguous_roles = sorted(role for role, values in role_values.items() if len(values) > 1)
    if ambiguous_roles:
        warnings.append({"code": "MULTIPLE_AMOUNTS_SAME_ROLE", "roles": ambiguous_roles})
    all_text = "".join(line["text"] for page in document["pages"] for line in page["lines"])
    if event_type == "bid_award":
        event_status = "contract_unsigned" if "尚未正式签署合同" in all_text or "尚未签署合同" in all_text else "award_announced_contract_status_unknown"
        if "中标" not in title and event_status != "contract_unsigned":
            event_status = "contract_disclosed_signing_and_performance_unverified"
    elif event_type:
        event_status = "proposed_or_announced_actual_payment_unverified"
    else:
        event_status = "unclassified"
    review = "requires_human_review" if (document["status"] != "text_extracted" or
                                          ambiguous_roles or identity_conflict or not amounts or
                                          any(item["review_status"] != "candidate" for item in amounts)) else "candidates_extracted"
    review_reasons = []
    if document["status"] != "text_extracted":
        review_reasons.append({"code": "DOCUMENT_REVIEW", "message": "含扫描、稀疏文字或未读完页面，需要核对识别结果与覆盖范围。"})
    if identity_conflict:
        review_reasons.append({"code": "SECURITY_CODE_CONFLICT", "message": "目录与原文证券代码不一致。"})
    if ambiguous_roles:
        review_reasons.append({"code": "MULTIPLE_AMOUNTS_SAME_ROLE", "message": "同一金额口径出现不同数值，需要核对版本、主体和阶段。"})
    if not amounts:
        review_reasons.append({"code": "EVENT_AMOUNT_NOT_FOUND", "message": "未提取到支持的金额口径，不表示事件金额为零。"})
    if any(item["review_status"] == "requires_human_review" for item in amounts):
        review_reasons.append({"code": "QUALIFIED_OR_OCR_AMOUNT", "message": "金额含拟、约、上限等限制，或来自OCR/零金额，需要核对条件；不代表已发生支出。"})
    event = {"event_type": event_type, "event_status": event_status,
             "published_at": metadata.get("published_at"),
             "published_date_cn": metadata.get("published_date_cn"),
             "stock_code": None if identity_conflict else catalogue_code or printed_code,
             "identity_quality": identity_quality,
             "reported_identity": reported,
             "company_short_name": metadata.get("company_short_name"),
             "amount_candidates": amounts, "risk_clauses": clauses,
             "detail_candidates": details,
             "source_url": metadata.get("source_url"),
             "catalogue_url": metadata.get("catalogue_url"),
             "amounts_are_audited_financial_facts": False}
    return {"schema_version": "1.1",
            "document": {key: value for key, value in document.items() if key != "pages"},
            "routing": routing, "title": title, "event": event,
            "warnings": warnings, "review_status": review,
            "review_reasons": review_reasons, "evidence_blocks": paragraphs,
            "analysis_version": "announcement-1.0.0"}
