"""Sourced half-year financial facts and period-specific deterministic metrics.

The annual engine is intentionally unchanged: H1 flows compare with prior H1,
while balance-sheet stocks compare with the preceding December 31. Neither
half-year return ratios nor annualized figures are inferred here.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from decimal import Decimal, DivisionByZero, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

from .core import UNIT_MULTIPLIER, metric_code, parse_amount
from .pdf_adapter import _statement_code


SCHEMA_VERSION = "0.10-h1"
_NUMBERS = re.compile(r"(?<!\d)\(?[−－-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?(?!\d)")
_STATEMENT_TITLE = re.compile(r"(?:\d+[、.．])?(合并|母公司|公司)(资产负债表|利润表|现金流量表)")
_SECTIONS = {"资产负债表": "balance_sheet", "利润表": "income_statement", "现金流量表": "cash_flow_statement"}
_FLOW_FIELDS = {"revenue", "operating_cost", "r_and_d_expense", "net_profit",
                "attributable_net_profit", "attributable_net_profit_ex_nonrecurring",
                "operating_cash_flow", "capital_expenditure_cash_paid", "total_profit"}
_STOCK_FIELDS = {"total_assets", "attributable_equity", "accounts_receivable", "inventory",
                 "fixed_assets", "construction_in_progress", "total_liabilities"}


def _bbox(parts: list[dict]) -> list[float]:
    return [round(min(part["x0"] for part in parts), 2),
            round(min(part["top"] for part in parts), 2),
            round(max(part["x1"] for part in parts), 2),
            round(max(part["bottom"] for part in parts), 2)]


def _unit(text: str) -> str | None:
    match = re.search(r"单位(?:为)?\s*[:：]\s*(千元|万元|亿元|元)(?![/／股])", text)
    return match.group(1) if match else None


def _row_unit(label: str, page_unit: str | None) -> str | None:
    match = re.search(r"[（(](千元|万元|亿元|元)[）)]", label)
    return match.group(1) if match else page_unit


def _amount_cells(cells: list[Any]) -> list[str]:
    result = []
    for cell in cells:
        text = str(cell or "").strip().replace("−", "-").replace("－", "-")
        if re.fullmatch(r"-?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", text):
            result.append(text)
    return result


def _summary_rows(page: Any, document_id: str) -> list[dict]:
    page_text = page.extract_text() or ""
    page_unit = _unit(page_text)
    records = []
    for table_index, table in enumerate(page.find_tables()):
        table_rows = table.extract()
        if not table_rows:
            continue
        first = "".join(str(cell or "") for cell in table_rows[0])
        if "本报告期" not in first or "上年同期" not in first:
            continue
        mode = "summary_flow"
        for index, cells in enumerate(table_rows):
            row_text = "".join(str(cell or "") for cell in cells)
            if "本报告期末" in row_text and "上年度末" in row_text:
                mode = "summary_stock"
                continue
            if index == 0:
                continue
            values = _amount_cells(cells)
            # Labels can contain a unit and digits (e.g. "（千元）"); use the
            # first nonempty Chinese cell that is not a comparative header.
            label = next((str(cell).strip() for cell in cells if cell and
                          re.search(r"[\u4e00-\u9fff]", str(cell)) and
                          not any(term in str(cell) for term in ("本报告期", "上年同期", "上年度末"))), "")
            if len(values) >= 2 and label:
                record = {"kind": mode, "label": label, "values": values[:2],
                          "unit": _row_unit(label, page_unit), "source": {
                              "document_id": document_id, "page_start": page.page_number,
                              "page_end": page.page_number, "table_index": table_index,
                              "section": mode, "row_bbox": [round(x, 2) for x in table.rows[index].bbox],
                              "parts": [{"page": page.page_number,
                                         "row_bbox": [round(x, 2) for x in table.rows[index].bbox],
                                         "text": label}], "extractor": "pdf_table"}}
                records.append(record)
            elif label and records and len(values) == 0:
                last = records[-1]
                if last["kind"] == mode:
                    last["label"] += label
                    if last["unit"] is None:
                        last["unit"] = _row_unit(last["label"], page_unit)
                    current_box = table.rows[index].bbox
                    old_box = last["source"]["row_bbox"]
                    last["source"]["row_bbox"] = [round(min(old_box[0], current_box[0]), 2),
                                                    round(min(old_box[1], current_box[1]), 2),
                                                    round(max(old_box[2], current_box[2]), 2),
                                                    round(max(old_box[3], current_box[3]), 2)]
                    last["source"]["parts"].append({"page": page.page_number,
                                                      "row_bbox": [round(x, 2) for x in current_box],
                                                      "text": label})
    for record in records:
        record["metric_code"] = metric_code(record["label"], "annual")
    return [record for record in records if record["metric_code"] in _FLOW_FIELDS | _STOCK_FIELDS
            and record["unit"] in UNIT_MULTIPLIER]


def _header_order(header: str, kind: str, year: int) -> bool:
    current, previous = str(year), str(year - 1)
    lines = [re.sub(r"\s+", "", line) for line in header.splitlines()]
    for line in lines:
        if current in line and previous in line:
            return line.index(current) < line.index(previous)
    compact = "".join(lines)
    if kind == "balance_sheet" and (f"{year}年6月30日" in compact and
                                    "期末余额" in compact and "期初余额" in compact):
        return compact.index("期末余额") < compact.index("期初余额")
    return False


def _statement_rows(pdf: Any, start_index: int, doc_id: str, year: int, end_month: int = 6) -> tuple[list[dict], list[dict], list[str]]:
    rows, warnings = [], []
    found_sections: set[str] = set()
    verified_sections: set[str] = set()
    found_codes: dict[str, set[str]] = {kind: set() for kind in _SECTIONS.values()}
    active: str | None = None
    verified = False
    unit: str | None = None
    unit_page: int | None = None
    restated = False
    cash_seen = False
    header_start = 0
    header_page = -1
    for index in range(start_index, min(len(pdf.pages), start_index + 115)):
        page = pdf.pages[index]
        page_text = page.extract_text() or ""
        if active is None and "合并" not in page_text and not cash_seen:
            page.close()
            continue
        lines = page.extract_text_lines() or []
        for line_index, line in enumerate(lines):
            compact = re.sub(r"\s+", "", line["text"])
            title = _STATEMENT_TITLE.fullmatch(compact.replace('年初到报告期末', '').replace('年初至报告期末', ''))
            if title:
                if title.group(1) != "合并":
                    active = None
                    verified = False
                    continue
                active = _SECTIONS[title.group(2)]
                found_sections.add(active)
                verified = False
                header_start = line_index + 1
                header_page = index
                cash_seen |= active == "cash_flow_statement"
                restated = "经重述" in page_text or "經重述" in page_text
                page_unit = _unit(page_text)
                if page_unit:
                    unit, unit_page = page_unit, index + 1
                continue
            if active is None:
                continue
            page_unit = _unit(line["text"])
            if page_unit:
                unit, unit_page = page_unit, index + 1
            if not verified:
                # Include up to the first dozen physical lines after the title;
                # date columns often span two or three rows.
                begin = header_start if header_page == index else 0
                header = "\n".join(x["text"] for x in lines[begin:begin + 12])
                verified = _header_order(header, active, year)
                if end_month != 6 and not verified:
                    compact_header=re.sub(r'\s+','',header)
                    explicit_years=str(year) in compact_header and str(year-1) in compact_header
                    if explicit_years:
                        verified=False
                    elif active=='balance_sheet':
                        verified=bool(re.search(fr'{year}年0?{end_month}月',compact_header) and '期末余额' in compact_header and '期初余额' in compact_header and compact_header.index('期末余额')<compact_header.index('期初余额'))
                    else:
                        verified=('本期发生额' in compact_header and '上期发生额' in compact_header and compact_header.index('本期发生额')<compact_header.index('上期发生额'))
                if end_month != 6 and active != 'balance_sheet':
                    period_header = re.sub(r'\s+', '', header)
                    period_valid = bool(re.search(fr'1[—–－\-至~～]{1,2}{end_month}月', period_header)
                                        or ('第一季度' in page_text if end_month == 3 else '前三季度' in period_header)
                                        or (end_month == 9 and ('年初到报告期末' in page_text or '年初至报告期末' in page_text)))
                    verified = verified and period_valid
                if verified:
                    verified_sections.add(active)
            if not verified:
                continue
            code = _statement_code(line["text"], active)
            if active=='balance_sheet' and '归属于母公司所有者权益合计' in compact:
                code='attributable_equity'
            if active=='income_statement' and re.search(r'归属于母公司(?:股东|所有者)的净利润',compact):
                code='attributable_net_profit'
            if active == "income_statement" and re.match(r"^[四五]、?利润总额", compact):
                code = "total_profit"
            if code is None or code in found_codes[active]:
                continue
            used = [line]
            tokens = _NUMBERS.findall(line["text"])
            if len(tokens) < 2:
                for following in lines[line_index + 1:line_index + 4]:
                    if following["top"] - line["top"] > 25:
                        break
                    numeric = _NUMBERS.findall(following["text"])
                    if len(numeric) >= 2 and following["x0"] > float(page.width) * 0.4:
                        tokens = numeric
                        used.append(following)
                        break
            if len(tokens) < 2 or unit is None:
                continue
            values = [token.replace("−", "-").replace("－", "-") for token in tokens[-2:]]
            if any(parse_amount(value, unit) is None for value in values):
                continue
            rows.append({"kind": active, "metric_code": code, "label": line["text"],
                         "values": values, "unit": unit, "restated_comparative": restated,
                         "source": {"document_id": doc_id, "page_start": index + 1,
                                    "page_end": index + 1, "section": f"consolidated_{active}",
                                    "row_bbox": _bbox(used), "raw_text": " ".join(x["text"] for x in used),
                                    "unit_source_page": unit_page, "extractor": "pdf_text_lines"}})
            found_codes[active].add(code)
        page.close()
        if cash_seen and active is None:
            break
    required = {"balance_sheet": {"accounts_receivable", "inventory", "fixed_assets",
                                   "construction_in_progress", "total_assets"},
                "income_statement": {"revenue", "operating_cost", "r_and_d_expense", "net_profit"},
                "cash_flow_statement": {"operating_cash_flow", "capital_expenditure_cash_paid"}}
    for kind, codes in required.items():
        if kind not in found_sections:
            warnings.append({"code": "H1_STATEMENT_MISSING", "section": kind, "document_id": doc_id})
        elif kind not in verified_sections:
            warnings.append({"code": "H1_COLUMN_ORDER_UNVERIFIED", "section": kind,
                             "document_id": doc_id})
        for code in sorted(codes - found_codes[kind]):
            warnings.append({"code": "H1_STATEMENT_FIELD_MISSING", "section": kind,
                             "metric_code": code, "document_id": doc_id})
    return rows, warnings, sorted(found_sections)


def parse_semiannual_pdf(path: str | Path) -> dict:
    """Extract selected H1 summary and consolidated statement rows from text PDF."""
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError("Semiannual PDF adapter requires pdfplumber") from exc
    source = Path(path).resolve(strict=True)
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    sha = digest.hexdigest()
    with pdfplumber.open(source) as pdf:
        cover = pdf.pages[0].extract_text() or ""
        title = re.search(r"(20\d{2})\s*年\s*半年度报告", cover)
        if not title:
            raise ValueError("PDF cover does not identify a half-year report")
        year = int(title.group(1))
        company_match = re.search(r"([\u4e00-\u9fff]{2,40}股份有限公司)\s*20\d{2}\s*年\s*半年度报告", cover)
        company = company_match.group(1) if company_match else None
        summary_index = None
        for index in range(min(25, len(pdf.pages))):
            text = pdf.pages[index].extract_text() or ""
            if "主要会计数据和财务指标" in text and "营业收入" in text:
                summary_index = index
                break
            pdf.pages[index].close()
        if summary_index is None:
            raise ValueError("H1 summary table was not found in the first 25 pages")
        summary = _summary_rows(pdf.pages[summary_index], sha[:16])
        pdf.pages[summary_index].close()
        if not summary or not {"revenue", "total_assets"} <= {row["metric_code"] for row in summary}:
            raise ValueError("H1 summary table lacks revenue or total assets")
        financial_start = None
        for index in range(summary_index + 1, min(len(pdf.pages), 130)):
            page = pdf.pages[index]
            lines = page.extract_text_lines() or []
            if any(_STATEMENT_TITLE.fullmatch(re.sub(r"\s+", "", line["text"]))
                   and "合并资产负债表" in re.sub(r"\s+", "", line["text"]) for line in lines):
                financial_start = index
                break
            page.close()
        if financial_start is None:
            raise ValueError("Consolidated balance sheet not found")
        statement_rows, warnings, sections = _statement_rows(pdf, financial_start, sha[:16], year)
        intro = "\n".join((pdf.pages[index].extract_text() or "")[:1000]
                          for index in range(max(0, financial_start - 1), min(financial_start + 1, len(pdf.pages))))
        assurance = ("limited_review" if "审阅报告" in intro else
                     "unaudited" if "未经审计" in intro else "unknown")
        return {"document_id": sha[:16], "file_name": source.name, "file_sha256": sha,
                "company_name": company, "report_year": year, "report_type": "semiannual",
                "currency": "CNY", "pdf_pages": len(pdf.pages), "assurance": assurance,
                "report_title": f"{year}年半年度报告",
                "parser": "pdfplumber_h1_adapter_v0.1",
                "sections_found": ["h1_summary", *sections],
                "parser_warnings": warnings, "rows": [*summary, *statement_rows]}


def analyze_parsed_semiannual(documents: list[dict]) -> dict:
    """Normalize H1 rows and calculate only period-valid Decimal indicators."""
    result = {"schema_version": SCHEMA_VERSION, "documents": [], "facts": [], "metrics": [],
              "unavailable_metrics": [], "risk_assessments": [], "findings": [],
              "warnings": [], "audit": []}
    for document in documents:
        if document.get("report_type") != "semiannual":
            raise ValueError("analyze_parsed_semiannual accepts only semiannual documents")
        year = int(document["report_year"])
        doc_id = document["document_id"]
        h1, prior_h1 = f"{year}H1", f"{year - 1}H1"
        close, opening = f"{year}-06-30", f"{year - 1}-12-31"
        period_days = Decimal(str((date(year, 7, 1) - date(year, 1, 1)).days))
        result["documents"].append({key: value for key, value in document.items() if key != "rows"})
        result["audit"].append({"event": "h1_document_analyzed", "document_id": doc_id,
                                "row_count": len(document.get("rows", []))})
        facts: dict[tuple[str, str], dict] = {}
        for row in document.get("rows", []):
            kind = row.get("kind")
            code = row.get("metric_code")
            if kind in {"summary_flow", "income_statement", "cash_flow_statement"} and code in _FLOW_FIELDS:
                periods = (h1, prior_h1)
            elif kind in {"summary_stock", "balance_sheet"} and code in _STOCK_FIELDS:
                periods = (close, opening)
            else:
                continue
            for period, raw in zip(periods, row.get("values", [])[:2]):
                value = parse_amount(raw, row.get("unit", ""))
                if value is None:
                    result["warnings"].append({"code": "INVALID_AMOUNT", "document_id": doc_id,
                                               "metric_code": code, "period": period,
                                               "raw_value": str(raw)})
                    continue
                source = {**row["source"], "raw_value": str(raw),
                          "raw_unit": row["unit"], "raw_label": row["label"]}
                restated = bool(row.get("restated_comparative") and period == prior_h1)
                if restated:
                    source["comparative_restated"] = True
                period_meta = ({"period_type": "instant", "period_start": None,
                                "period_end": period} if kind in {"summary_stock", "balance_sheet"} else
                               {"period_type": "duration", "period_start": f"{period[:4]}-01-01",
                                "period_end": f"{period[:4]}-06-30"})
                fact = {"fact_id": f"{doc_id}:{period}:{code}", "document_id": doc_id,
                        "company_name": document.get("company_name"), "metric_code": code,
                        "period": period, "statement_scope": "consolidated", **period_meta,
                        "comparative_restated": restated, "value": format(value, "f"),
                        "unit": "CNY", "raw_label": row["label"], "raw_value": str(raw),
                        "raw_unit": row["unit"], "sources": [source], "quality": "extracted"}
                key = period, code
                old = facts.get(key)
                if old is None:
                    facts[key] = fact
                    result["facts"].append(fact)
                elif Decimal(old["value"]) == value:
                    if source not in old["sources"]:
                        old["sources"].append(source)
                        if len({item.get("section") for item in old["sources"]}) >= 2:
                            old["quality"] = "cross_checked"
                    old["comparative_restated"] |= restated
                else:
                    old["quality"] = "conflict"
                    old["sources"].append(source)
                    old.setdefault("conflicting_values", []).append({"value": fact["value"], "source": source})
                    result["warnings"].append({"code": "CONFLICTING_VALUES", "document_id": doc_id,
                                               "metric_code": code, "period": period})

        def get(period: str, code: str) -> tuple[Decimal, dict] | None:
            fact = facts.get((period, code))
            if fact is None or fact["quality"] == "conflict":
                return None
            return Decimal(fact["value"]), fact

        def unavailable(code: str, reason: str, detail: str, items: list[dict] | None = None) -> None:
            result["unavailable_metrics"].append({"metric_id": f"{doc_id}:{h1}:{code}",
                "document_id": doc_id, "metric_code": code, "period": h1,
                "status": "unavailable", "value": None, "reason_code": reason,
                "detail": detail, "input_fact_ids": [item["fact_id"] for item in (items or [])]})

        def calculate(code: str, required: list[tuple[str, str]], unit: str, formula: str,
                      operation, *, positive: tuple[int, ...] = (),
                      nonnegative: tuple[int, ...] = (),
                      bounded: tuple[int, tuple[int, ...]] | None = None) -> None:
            resolved = [get(period, field) for period, field in required]
            available = [item[1] for item in resolved if item]
            if any(item is None for item in resolved):
                conflict = any(facts.get(pair, {}).get("quality") == "conflict"
                               for pair, item in zip(required, resolved) if item is None)
                missing = [f"{p}:{c}" for (p, c), item in zip(required, resolved) if item is None]
                unavailable(code, "CONFLICTING_INPUT" if conflict else "MISSING_INPUT", ", ".join(missing), available)
                return
            values = [item[0] for item in resolved if item]
            for index in positive:
                if values[index] <= 0:
                    unavailable(code, "DIVISION_BY_ZERO" if values[index] == 0 else "NONPOSITIVE_INPUT",
                                f"{required[index][0]}:{required[index][1]} must be positive", available)
                    return
            for index in nonnegative:
                if values[index] < 0:
                    unavailable(code, "NEGATIVE_BALANCE", f"{required[index][0]}:{required[index][1]} is negative", available)
                    return
            if bounded:
                denominator, numerators = bounded
                with localcontext() as context:
                    context.prec = max(50, sum(len(value.as_tuple().digits) for value in values) + 16)
                    if sum((values[index] for index in numerators), Decimal("0")) > values[denominator]:
                        unavailable(code, "INCONSISTENT_COMPONENTS", "components exceed total", available)
                        return
            try:
                with localcontext() as context:
                    context.prec = max(50, sum(len(value.as_tuple().digits) for value in values) + 16)
                    value = operation(values)
            except (DivisionByZero, InvalidOperation, ZeroDivisionError):
                unavailable(code, "DIVISION_BY_ZERO", "formula denominator is zero", available)
                return
            if not value.is_finite():
                unavailable(code, "NONFINITE_RESULT", "formula produced a nonfinite value", available)
                return
            metric = {"metric_id": f"{doc_id}:{h1}:{code}", "document_id": doc_id,
                      "metric_code": code, "period": h1, "period_type": "duration",
                      "period_start": f"{year}-01-01", "period_end": f"{year}-06-30",
                      "status": "calculated", "value": format(value, "f"),
                      "unit": unit, "formula": formula,
                      "input_fact_ids": [item["fact_id"] for item in available]}
            result["metrics"].append(metric)
            result["audit"].append({"event": "h1_metric_calculated", "metric_id": metric["metric_id"],
                                    "input_fact_ids": metric["input_fact_ids"]})

        pct = Decimal("100")
        for code in sorted(_FLOW_FIELDS):
            calculate(f"{code}_yoy", [(h1, code), (prior_h1, code)], "percent",
                      "(current_H1 - prior_H1) / prior_H1 * 100",
                      lambda values: (values[0] - values[1]) / values[1] * pct, positive=(1,))
        calculate("gross_margin", [(h1, "revenue"), (h1, "operating_cost")], "percent",
                  "(H1_revenue - H1_operating_cost) / H1_revenue * 100",
                  lambda values: (values[0] - values[1]) / values[0] * pct,
                  positive=(0,), nonnegative=(1,))
        calculate("consolidated_net_margin", [(h1, "net_profit"), (h1, "revenue")], "percent",
                  "H1_consolidated_net_profit / H1_revenue * 100",
                  lambda values: values[0] / values[1] * pct, positive=(1,))
        calculate("adjusted_attributable_net_margin",
                  [(h1, "attributable_net_profit_ex_nonrecurring"), (h1, "revenue")], "percent",
                  "H1_adjusted_attributable_profit / H1_revenue * 100",
                  lambda values: values[0] / values[1] * pct, positive=(1,))
        calculate("cash_to_consolidated_profit", [(h1, "operating_cash_flow"), (h1, "net_profit")],
                  "times", "H1_operating_cash_flow / H1_consolidated_net_profit",
                  lambda values: values[0] / values[1], positive=(1,))
        calculate("simplified_free_cash_flow",
                  [(h1, "operating_cash_flow"), (h1, "capital_expenditure_cash_paid")], "CNY",
                  "H1_operating_cash_flow - H1_long_term_asset_cash_paid",
                  lambda values: values[0] - values[1], nonnegative=(1,))
        calculate("inventory_days", [(opening, "inventory"), (close, "inventory"), (h1, "operating_cost")],
                  "days", "H1_days * (opening_inventory + closing_inventory) / (2 * H1_operating_cost)",
                  lambda values: period_days * (values[0] + values[1]) / (Decimal("2") * values[2]),
                  positive=(2,), nonnegative=(0, 1))
        calculate("receivables_days_approx",
                  [(opening, "accounts_receivable"), (close, "accounts_receivable"), (h1, "revenue")],
                  "days", "H1_days * (opening_net_receivables + closing_net_receivables) / (2 * H1_revenue)",
                  lambda values: period_days * (values[0] + values[1]) / (Decimal("2") * values[2]),
                  positive=(2,), nonnegative=(0, 1))
        calculate("fixed_assets_cip_to_assets",
                  [(close, "fixed_assets"), (close, "construction_in_progress"), (close, "total_assets")],
                  "percent", "(H1_end_fixed_assets + H1_end_CIP) / H1_end_total_assets * 100",
                  lambda values: (values[0] + values[1]) / values[2] * pct,
                  positive=(2,), nonnegative=(0, 1), bounded=(2, (0, 1)))
        calculate("r_and_d_expense_to_revenue", [(h1, "r_and_d_expense"), (h1, "revenue")],
                  "percent", "H1_R&D_expense / H1_revenue * 100",
                  lambda values: values[0] / values[1] * pct, positive=(1,), nonnegative=(0,))
        for code in ("return_on_assets", "return_on_attributable_equity"):
            unavailable(code, "ANNUAL_METRIC_NOT_APPLICABLE",
                        "Annual-period return formula is not automatically annualized or reused for H1")
        result["risk_assessments"].append({"assessment_id": f"{doc_id}:{h1}:annual_risk_rules",
                                           "document_id": doc_id, "status": "not_evaluated",
                                           "reason_code": "PERIOD_NOT_ANNUAL",
                                           "evidence_fact_ids": []})
    return result


def analyze_semiannual_reports(file_paths: list[str | Path]) -> dict:
    """Member-1 H1 entrypoint with per-file parse failures and stable JSON keys."""
    parsed, failures = [], []
    for path in file_paths:
        try:
            parsed.append(parse_semiannual_pdf(path))
        except (OSError, ValueError, RuntimeError) as exc:
            failures.append({"code": "H1_PDF_PARSE_FAILED", "file_name": Path(path).name,
                             "message": str(exc)})
    result = analyze_parsed_semiannual(parsed)
    result["warnings"].extend(failures)
    for document in parsed:
        result["warnings"].extend(document.get("parser_warnings", []))
        result["audit"].append({"event": "h1_pdf_parsed", "document_id": document["document_id"],
                                "file_sha256": document["file_sha256"], "parser": document["parser"],
                                "extracted_rows": len(document["rows"])})
    return result
