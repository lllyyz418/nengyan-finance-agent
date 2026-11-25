"""Optional pdfplumber adapter for sourced annual-report facts.

The analysis core does not import pdfplumber. This adapter deliberately limits
its extraction to summary tables, selected consolidated statement rows, and
three narrowly defined note tables. It never implies all notes were checked.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .core import analyze_parsed_documents
from .note_adapter import parse_selected_notes
from .policy_adapter import parse_annual_policies
from .parse_runtime import cached_page_text, page_text_cache


def _cell_text(cell: Any) -> str:
    return re.sub(r"\s+", "", str(cell or ""))


def _table_kind(row: list[Any], year: int) -> str | None:
    joined = "".join(_cell_text(cell) for cell in row)
    if "第一季度" in joined and "第四季度" in joined:
        return "quarterly"
    if (f"{year}年金额" in joined and f"{year - 1}年金额" in joined) or "非经常性损益项目" in joined:
        return "nonrecurring"
    if f"{year}年" in joined and f"{year - 1}年" in joined:
        return "annual"
    return None


def _numeric_cell(cell: Any) -> bool:
    s = _cell_text(cell).replace(",", "").replace("−", "-").replace("－", "-")
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?%?", s))


def _row_parts(row: list[Any]) -> tuple[str, list[str]]:
    entries = [_cell_text(cell) for cell in row if _cell_text(cell)]
    label = next((part for part in entries if re.search(r"[\u4e00-\u9fff]", part) and not re.fullmatch(r"20\d{2}年(?:末|金额)?", part)), "")
    if len(label) == 1:
        # Some PDFs split the first character (e.g. 经 | 营活动产生的现金流量净额)
        # into an adjacent cell. Rejoin label cells only before the first amount.
        first_amount = next((i for i, cell in enumerate(row) if _numeric_cell(cell)), len(row))
        fragments = [_cell_text(cell) for cell in row[:first_amount]
                     if re.search(r"[\u4e00-\u9fff]", _cell_text(cell))]
        if fragments and fragments[0] == label:
            label = "".join(fragments)
    values = [part for part in entries if _numeric_cell(part)]
    return label, values


def _summary_monetary_unit(label: str, fallback: str | None) -> str | None:
    """Respect explicit row units, including wrapped 万\n元; never infer scale."""
    flat = re.sub(r"\s+", "", label)
    units = set(re.findall(r"[（(](千元|万元|亿元|元)[）)]", flat))
    if len(units) > 1:
        raise ValueError("Conflicting explicit monetary units in summary row")
    return next(iter(units)) if units else fallback


def _pdf_metadata(first_page_text: str) -> tuple[str | None, int | None]:
    flat = re.sub(r"\s+", "", first_page_text)
    year_match = re.search(r"(20\d{2})年年度报告", flat)
    lines = [re.sub(r'\s+', '', line) for line in first_page_text.splitlines() if line.strip()]
    title = r'20\d{2}年年度报告'
    name_pattern = r'[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9（）()·&.\-]{1,100}'
    generic = {'公司','股份有限公司','有限公司','有限责任公司','集团有限公司','年度报告'}

    def valid_name(value):
        folded = value.translate(str.maketrans('（）','()'))
        return bool(re.fullmatch(name_pattern,value) and value not in generic
                    and folded.count('(') == folded.count(')'))

    # Keep line boundaries: flattening the entire cover can join a securities
    # code/header to the name. Parentheses are part of the issuer's legal name.
    for index, line in enumerate(lines):
        match = re.match(fr'({name_pattern}){title}',line)
        if match and valid_name(match[1]):
            return match[1], int(year_match[1]) if year_match else None
        if re.match(title,line):
            for count in range(1,min(3,index)+1):
                candidate=''.join(lines[index-count:index])
                if valid_name(candidate) and candidate.endswith(('有限公司','有限责任公司')):
                    return candidate, int(year_match[1]) if year_match else None
    return None, int(year_match[1]) if year_match else None


_STATEMENT_CODES = {
    "balance_sheet": {"accounts_receivable", "inventory", "fixed_assets", "construction_in_progress", "total_assets", "total_liabilities"},
    "income_statement": {"revenue", "operating_cost", "r_and_d_expense", "net_profit"},
    "cash_flow_statement": {"operating_cash_flow", "capital_expenditure_cash_paid"},
}
_REQUIRED_STATEMENT_CODES = {
    "balance_sheet": {"accounts_receivable", "inventory", "fixed_assets", "construction_in_progress", "total_assets", "total_liabilities"},
    "income_statement": {"operating_cost", "r_and_d_expense", "net_profit"},
    "cash_flow_statement": {"operating_cash_flow", "capital_expenditure_cash_paid"},
}


def _unit_from_text(text: str) -> str | None:
    match = re.search(r"(?:财务附注中报表的单位为|单位)\s*[:：]\s*(千元|万元|亿元|元)(?![/／股])", text)
    return match.group(1) if match else None


def _statement_title(text: str) -> tuple[str, str] | None:
    flat = re.sub(r"\s+", "", text)
    match = re.fullmatch(r"(?:\d+[、.．])?(合并|母公司)(资产负债表|利润表|现金流量表)", flat)
    if not match:
        return None
    kind = {"资产负债表": "balance_sheet", "利润表": "income_statement", "现金流量表": "cash_flow_statement"}[match.group(2)]
    return match.group(1), kind


def _statement_code(text: str, kind: str) -> str | None:
    flat = re.sub(r"\s+", "", text)
    if kind == "balance_sheet":
        for label, code in (
            ("应收账款", "accounts_receivable"),
            ("存货", "inventory"),
            ("固定资产", "fixed_assets"),
            ("在建工程", "construction_in_progress"),
            ("资产总计", "total_assets"),
            ("负债合计", "total_liabilities"),
            ("负债总计", "total_liabilities"),
            ("负债总额", "total_liabilities"),
        ):
            if flat.startswith(label):
                return code
    elif kind == "income_statement":
        if re.match(r"^(?:一、|其中[:：])?营业收入", flat):
            return "revenue"
        if re.match(r"^(?:减[:：]|其中[:：])?营业成本", flat):
            return "operating_cost"
        if flat.startswith("研发费用"):
            return "r_and_d_expense"
        if re.match(r"^[四五]、净利润", flat):
            return "net_profit"
    elif kind == "cash_flow_statement":
        if flat.startswith("经营活动产生的现金流"):
            return "operating_cash_flow"
        if flat.startswith("购建固定资产"):
            return "capital_expenditure_cash_paid"
    return None


def _amount_tokens(text: str) -> list[str]:
    """Find amount-shaped tokens; note references may precede the last two."""
    return re.findall(r"(?<!\d)[−－-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\d)", text)


def _bbox(lines: list[dict]) -> list[float]:
    return [round(min(x["x0"] for x in lines), 2), round(min(x["top"] for x in lines), 2),
            round(max(x["x1"] for x in lines), 2), round(max(x["bottom"] for x in lines), 2)]


def _statement_column_order(text: str, kind: str, year: int, page_text: str) -> bool | None:
    """Confirm that the first amount column is current year, not comparative.

    Only table header lines are considered. Some balance sheets label their
    columns as ending and opening balances rather than printing both years.
    """
    flat = re.sub(r"\s+", "", text)
    current, previous = str(year), str(year - 1)
    if kind == "balance_sheet":
        closing = re.search(rf"(?<!\d){year}年12月31日", flat)
        opening = re.search(rf"(?<!\d){year}年0?1月0?1日", flat)
        if closing and opening:
            return closing.start() < opening.start()
    if kind == "balance_sheet" and "期末余额" in flat and "期初余额" in flat:
        if re.search(rf"{year}年12月31日", page_text):
            return flat.index("期末余额") < flat.index("期初余额")
    if not any(marker in flat for marker in ("项目", "附注", "资产", "年度", "12月31日")):
        return None
    current_match = re.search(rf"(?<!\d){current}年(?:度|12月31日)?", flat)
    previous_match = re.search(rf"(?<!\d){previous}年(?:度|12月31日)?", flat)
    if current_match and previous_match:
        return current_match.start() < previous_match.start()
    return None


def _later_global_statement_unit(pdf: Any, start_index: int) -> tuple[str, int] | None:
    """Find an explicit financial-statement unit declaration in nearby notes."""
    for index in range(start_index, min(start_index + 30, len(pdf.pages))):
        page = pdf.pages[index]
        text = re.sub(r"\s+", "", cached_page_text(page))
        page.close()
        match = re.search(r"本财务报表.{0,100}以人民币(千元|万元|亿元|元)为单位", text)
        if match:
            return match.group(1), index + 1
    return None


def _statement_rows(pdf: Any, start_index: int, document_id: str, year: int) -> tuple[list[dict], list[dict], list[str], str | None]:
    """Read selected rows only while inside a consolidated statement.

    The first two monetary columns are the report year and prior year only
    after the statement header has confirmed their order. A row
    split across text lines is accepted only when both monetary values appear
    together in a right-hand numeric line; ambiguous rows are left unparsed.
    """
    rows: list[dict] = []
    warnings: list[dict] = []
    found_sections: set[str] = set()
    found_codes: dict[str, set[str]] = {kind: set() for kind in _STATEMENT_CODES}
    active: str | None = None
    unit: str | None = None
    unit_source_page: int | None = None
    global_unit_checked = False
    comparative_restated = False
    column_order_verified = False
    invalid_order_sections: set[str] = set()
    cash_section_seen = False
    finished = False
    for index in range(start_index, len(pdf.pages)):
        from .parse_runtime import report_progress
        report_progress('定位合并财务报表', index+1, len(pdf.pages))
        page = pdf.pages[index]
        page_text = cached_page_text(page)
        if active is None and not any(
            title in page_text
            for title in ("合并资产负债表", "合并利润表", "合并现金流量表")
        ):
            page.close()
            continue
        page_unit = _unit_from_text(page_text)
        if page_unit:
            unit = page_unit
            unit_source_page = index + 1
        lines = page.extract_text_lines()
        for line_index, line in enumerate(lines):
            title = _statement_title(line["text"])
            if title:
                owner, kind = title
                if owner == "母公司":
                    if cash_section_seen:
                        finished = True
                        break
                    active = None
                else:
                    active = kind
                    column_order_verified = False
                    found_sections.add(kind)
                    cash_section_seen |= kind == "cash_flow_statement"
                    comparative_restated = "经重述" in page_text or "經重述" in page_text
                continue
            if active is None:
                continue
            order = _statement_column_order(line["text"], active, year, page_text)
            if order is not None:
                if not order:
                    invalid_order_sections.add(active)
                    warnings.append({"code": "STATEMENT_COLUMN_ORDER_INVALID", "document_id": document_id, "message": f"PDF 第 {index + 1} 页 {active} 的本期与上期列顺序不符合预期，已跳过该章节"})
                elif active not in invalid_order_sections:
                    column_order_verified = True
            if active in invalid_order_sections or not column_order_verified:
                continue
            code = _statement_code(line["text"], active)
            if code is None or code in found_codes[active]:
                continue
            used = [line]
            tokens = _amount_tokens(line["text"])
            if len(tokens) < 2 and active in {"cash_flow_statement", "income_statement"}:
                # In some cash-flow tables the label occupies two lines and
                # both amounts are printed on an intervening numeric line.
                for following in lines[line_index + 1:]:
                    if following["top"] - line["top"] > 40:
                        break
                    text = following["text"]
                    if _statement_title(text) or _statement_code(text, active):
                        break
                    numeric = _amount_tokens(text)
                    if len(numeric) >= 2 and following["x0"] > page.width * 0.45:
                        tokens = numeric
                        used.append(following)
                        break
            if len(tokens) < 2:
                continue
            if unit is None and not global_unit_checked:
                global_unit_checked = True
                later_unit = _later_global_statement_unit(pdf, index + 1)
                if later_unit:
                    unit, unit_source_page = later_unit
            if unit is None:
                warnings.append({"code": "STATEMENT_UNIT_MISSING", "document_id": document_id, "message": f"PDF 第 {index + 1} 页 {active} 未确认金额单位"})
                continue
            record = {
                "kind": active,
                "statement_scope": "consolidated",
                "metric_code": code,
                "label": line["text"],
                "values": tokens[-2:],
                "unit": unit,
                "restated_comparative": comparative_restated,
                "source": {
                    "document_id": document_id,
                    "page_start": index + 1,
                    "page_end": index + 1,
                    "section": f"consolidated_{active}",
                    "row_bbox": _bbox(used),
                    "raw_text": " ".join(x["text"] for x in used),
                    "unit_source_page": unit_source_page,
                    "extractor": "pdf_text_lines",
                },
            }
            rows.append(record)
            found_codes[active].add(code)
        page.close()
        if finished or all(_REQUIRED_STATEMENT_CODES[kind] <= found_codes[kind] for kind in _REQUIRED_STATEMENT_CODES):
            break

    for kind, required in _REQUIRED_STATEMENT_CODES.items():
        if kind not in found_sections:
            warnings.append({"code": "STATEMENT_SECTION_MISSING", "document_id": document_id, "message": f"未定位到合并{kind}章节"})
        elif not found_codes[kind] and kind not in invalid_order_sections:
            warnings.append({"code": "STATEMENT_COLUMN_ORDER_UNVERIFIED", "document_id": document_id, "message": f"合并{kind}未确认本期与上期金额列顺序，已跳过该章节"})
        for code in sorted(required - found_codes[kind]):
            warnings.append({"code": "STATEMENT_FIELD_MISSING", "document_id": document_id, "message": f"合并{kind}中未确认 {code}"})
    return rows, warnings, sorted(found_sections), unit


def parse_pdf(path: str | Path) -> dict:
    from .parse_runtime import RUNNER
    runner = RUNNER.get()
    if runner:
        return runner('annual', path)
    with page_text_cache():
        return _parse_pdf_local(path)


def _parse_pdf_local(path: str | Path) -> dict:
    """Parse selected annual-report tables, statements, and notes from a text PDF.

    Raises ValueError when report metadata or an extractable summary is absent.
    OCR and arbitrary announcements are outside this adapter's current scope.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("PDF adapter requires pdfplumber; the core module does not") from exc

    source_path = Path(path).resolve(strict=True)
    digest = hashlib.sha256()
    with source_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    file_hash = digest.hexdigest()

    with pdfplumber.open(str(source_path)) as pdf:
        first_text = cached_page_text(pdf.pages[0])
        company, year = _pdf_metadata(first_text)
        if year is None:
            raise ValueError("The PDF cover does not identify an annual-report year")
        scan_limit = min(len(pdf.pages), 35)
        start: int | None = None
        for index in range(scan_limit):
            text = cached_page_text(pdf.pages[index])
            if "主要会计数据和财务指标" in text or "近三年主要会计数据和财务指标" in text:
                start = index
                break
        if start is None:
            raise ValueError("No annual-report summary section was found in the first 35 pages")

        rows: list[dict] = []
        active_kind: str | None = None
        active_unit: str | None = None
        last_row: dict | None = None
        summary_complete = False
        for index in range(start, min(start + 7, len(pdf.pages))):
            page = pdf.pages[index]
            page_text = cached_page_text(page)
            if "单位：千元" in page_text or "单位:千元" in page_text:
                active_unit = "千元"
            elif "单位：万元" in page_text or "单位:万元" in page_text:
                active_unit = "万元"
            elif "单位：元" in page_text or "单位:元" in page_text:
                active_unit = "元"
            for table_index, table in enumerate(page.find_tables()):
                for row_index, cells in enumerate(table.extract()):
                    if not cells:
                        continue
                    kind = _table_kind(cells, year)
                    if kind:
                        active_kind = kind
                        last_row = None
                        continue
                    if active_kind is None:
                        continue
                    label, values = _row_parts(cells)
                    if not label:
                        continue
                    if len(values) >= 2:
                        row_unit = _summary_monetary_unit(label, active_unit)
                        if row_unit is None:
                            raise ValueError(f"Unit not found for table on PDF page {index + 1}")
                        if active_unit is None:
                            active_unit = row_unit
                        record = {
                            "kind": active_kind,
                            "label": label,
                            "values": values,
                            "unit": row_unit,
                            "source": {
                                "document_id": file_hash[:16],
                                "page_start": index + 1,
                                "page_end": index + 1,
                                "table_index": table_index,
                                "section": {"annual": "annual_summary", "quarterly": "quarterly_summary", "nonrecurring": "nonrecurring_summary"}[active_kind],
                                "table_bbox": [round(x, 2) for x in table.bbox],
                                "row_bbox": [round(x, 2) for x in table.rows[row_index].bbox],
                                "parts": [{"page": index + 1, "row_bbox": [round(x, 2) for x in table.rows[row_index].bbox], "text": label}],
                                "extractor": "pdf_table",
                            },
                        }
                        rows.append(record)
                        last_row = record
                        if active_kind == "nonrecurring" and label == "合计":
                            summary_complete = True
                            break
                    elif last_row is not None and last_row["kind"] == active_kind and last_row["label"] != "合计":
                        last_row["label"] += label
                        last_row["source"]["page_end"] = index + 1
                        last_row["source"]["parts"].append({"page": index + 1, "row_bbox": [round(x, 2) for x in table.rows[row_index].bbox], "text": label})
                if summary_complete:
                    break
            page.close()
            if summary_complete:
                break

        if not rows:
            raise ValueError("No structured summary table rows were extracted; OCR may be required")
        from .parse_runtime import report_progress
        report_progress('提取合并财务报表')
        statement_rows, parser_warnings, sections, statement_unit = _statement_rows(pdf, start + 1, file_hash[:16], year)
        rows.extend(statement_rows)
        note_start = max((row["source"]["page_end"] - 1 for row in statement_rows), default=start + 1)
        report_progress('提取财务附注')
        note_rows, note_warnings, note_sections = parse_selected_notes(pdf, note_start, file_hash[:16], year, statement_unit)
        rows.extend(note_rows)
        parser_warnings.extend(note_warnings)
        try:
            policy_rows, policy_review = parse_annual_policies(pdf, file_hash[:16], year)
        except (ValueError, TypeError, KeyError, IndexError, RuntimeError) as exc:
            # Optional note coverage must not discard successfully parsed core
            # statements. Preserve a diagnostic instead of claiming no change.
            policy_rows, policy_review = [], {'version':'annual-policy-1','extraction_failed':True}
            parser_warnings.append({'code':'POLICY_EXTRACTION_FAILED','document_id':file_hash[:16],
                                    'message':'会计政策附注提取遇到格式问题，基础财务数据已保留；请对照原文人工复核。','diagnostic':type(exc).__name__})
        rows.extend(policy_rows)
        return {
            "document_id": file_hash[:16],
            "file_name": source_path.name,
            "file_sha256": file_hash,
            "company_name": company,
            "report_title": f"{year}年年度报告",
            "report_year": year,
            "report_type": "annual",
            "currency": "CNY",
            "pdf_pages": len(pdf.pages),
            "parser": "pdfplumber_annual_adapter_v0.9_policies",
            "policy_review": policy_review,
            "sections_found": ["annual_summary", "quarterly_summary", "nonrecurring_summary", *sections, *[f"{name}_note" for name in note_sections]],
            "parser_warnings": parser_warnings,
            "rows": rows,
        }


def analyze_reports(file_paths: list[str | Path], options: dict | None = None) -> dict:
    """Member-1 integration entrypoint: JSON-serializable result, partial failures."""
    parsed: list[dict] = []
    failures: list[dict] = []
    for path in file_paths:
        try:
            parsed.append(parse_pdf(path))
        except (OSError, ValueError, RuntimeError) as exc:
            failures.append({"code": "PDF_PARSE_FAILED", "file_name": Path(path).name, "message": str(exc)})
    result = analyze_parsed_documents(parsed, options)
    result["warnings"].extend(failures)
    for document in parsed:
        result["warnings"].extend(document.get("parser_warnings", []))
        result["audit"].append({
            "event": "pdf_parsed",
            "document_id": document["document_id"],
            "file_sha256": document["file_sha256"],
            "parser": document["parser"],
            "extracted_rows": len(document["rows"]),
        })
    return result
