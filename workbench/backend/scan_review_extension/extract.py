"""Conservative OCR extraction of selected annual-summary financial rows.

This is an OCR candidate path, not a substitute for the text-PDF annual parser.
Only explicitly selected pages are read. All amounts and checks use Decimal.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path
from typing import Callable, Any

from financial_engine.core import parse_amount
from financial_engine.document_reader import read_pdf_document


_LABELS = {
    "营业收入": "revenue",
    "经营活动产生的现金流量净额": "operating_cash_flow",
    "归属于上市公司股东的净利润": "attributable_net_profit",
    "归属于母公司股东的净利润": "attributable_net_profit",
    "归属于母公司所有者的净利润": "attributable_net_profit",
    "总资产": "total_assets",
    "资产总额": "total_assets",
    "资产总计": "total_assets",
    "归属于上市公司股东的净资产": "attributable_equity",
    "归属于母公司股东的净资产": "attributable_equity",
}
_DIGITS = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_AMOUNT = re.compile(rf"(?:[−－-]?{_DIGITS}|\({_DIGITS}\)|（{_DIGITS}）)")
_YEAR = re.compile(r"(?<!\d)(20\d{2})年(?:末|度)?(?!\d)")
_PERCENT = re.compile(r"[−－-]?\d+(?:\.\d+)?%")


def _center(line: dict) -> tuple[float, float]:
    x0, y0, x1, y1 = line["bbox"]
    return ((x0 + x1) / 2, (y0 + y1) / 2)


def _rows(lines: list[dict]) -> list[list[dict]]:
    """Join OCR fragments that share one printed table row."""
    result: list[list[dict]] = []
    for line in sorted(lines, key=lambda item: (_center(item)[1], _center(item)[0])):
        cy = _center(line)[1]
        # The tolerance is below the usual 16-point financial-table row pitch.
        match = next((row for row in reversed(result[-3:])
                      if abs(cy - sum(_center(item)[1] for item in row) / len(row)) <= 5.5), None)
        if match is None:
            result.append([line])
        else:
            match.append(line)
    return [sorted(row, key=lambda item: _center(item)[0]) for row in result]


def _source(document: dict, page: dict, line: dict) -> dict:
    return {"document_id": document["document_id"], "file_sha256": document["file_sha256"],
            "page": page["page"], "bbox": line["bbox"], "method": line["method"],
            "confidence": line["confidence"], "text": line["text"]}


def _code(label: str) -> str | None:
    flat = re.sub(r"[\s（）()·、]", "", label).replace("元", "")
    return _LABELS.get(flat)


def _join_wrapped_labels(lines: list[dict], year: int) -> list[dict]:
    """Join label fragments only when bounded by two adjacent numeric row centers.

    No amount text is joined or repaired. Exact financial label matching remains
    mandatory, and the union box preserves all printed label fragments.
    """
    rows = _rows(lines)
    output = list(lines)
    for index, row in enumerate(rows):
        positions = {int(m.group(1)): _center(line)[0] for line in row
                     if (m := _YEAR.search(line["text"]))}
        if year not in positions or year - 1 not in positions:
            continue
        current_x, previous_x = positions[year], positions[year - 1]
        if current_x >= previous_x:
            continue
        header_y = sum(_center(line)[1] for line in row) / len(row)
        next_header_y = min((_center(line)[1] for later in rows[index+1:] for line in later
                             if _YEAR.search(line["text"]) or line["text"] == "第一季度"), default=10**9)
        anchors = []
        for later in rows[index+1:]:
            cy = sum(_center(line)[1] for line in later) / len(later)
            if cy >= next_header_y:
                break
            nums = [line for line in later if _AMOUNT.fullmatch(line["text"])]
            a = min(nums, key=lambda line: abs(_center(line)[0]-current_x), default=None)
            b = min(nums, key=lambda line: abs(_center(line)[0]-previous_x), default=None)
            if a is not None and b is not None and a is not b and abs(_center(a)[0]-current_x)<=40 and abs(_center(b)[0]-previous_x)<=40:
                anchors.append((_center(a)[1]+_center(b)[1])/2)
        for anchor in anchors:
            labels = [line for line in lines if _center(line)[0] < current_x-30
                      and header_y < _center(line)[1] < next_header_y
                      and abs(_center(line)[1]-anchor)<=20
                      and min(anchors,key=lambda y:abs(_center(line)[1]-y)) == anchor]
            labels.sort(key=lambda line: (line["bbox"][1],line["bbox"][0]))
            if len(labels)<2 or not _code("".join(line["text"] for line in labels)):
                continue
            if any(line not in output for line in labels):
                continue
            merged = {**labels[0], "text": "".join(line["text"] for line in labels),
                      "bbox": [min(l["bbox"][0] for l in labels),min(l["bbox"][1] for l in labels),
                               max(l["bbox"][2] for l in labels),max(l["bbox"][3] for l in labels)],
                      "confidence": min(l["confidence"] for l in labels)}
            for line in labels:
                output.remove(line)
            output.append(merged)
    return output


def _has_restatement_columns(lines: list[dict], year: int) -> bool:
    """Recognize pre/post labels in the table header, not explanatory footnotes."""
    rows = _rows(lines)
    for index, row in enumerate(rows):
        years = {int(m.group(1)) for line in row if (m := _YEAR.search(line["text"]))}
        if not {year, year - 1}.issubset(years):
            continue
        header_y = sum(_center(line)[1] for line in row) / len(row)
        first_amount_y = next((sum(_center(line)[1] for line in later)/len(later)
                               for later in rows[index+1:]
                               if sum(bool(_AMOUNT.fullmatch(line["text"])) for line in later) >= 2), header_y+35)
        if any(header_y-8 <= _center(line)[1] < first_amount_y-4
               and any(word in line["text"] for word in ("调整前", "调整后", "重述前", "重述后"))
               for line in lines):
            return True
    return False


def _percent_checked(current: Decimal, previous: Decimal, disclosed: str) -> bool:
    if previous <= 0:
        return False
    with localcontext() as ctx:
        ctx.prec = max(60, len(current.as_tuple().digits) + len(previous.as_tuple().digits) + 16)
        calculated = (current - previous) / previous * 100
        stated = Decimal(disclosed[:-1].replace("−", "-").replace("－", "-"))
        # Reported percentages are normally rounded to two decimal places.
        return abs(calculated - stated) <= Decimal("0.011")


def analyze_scanned_financial_pages(path: str | Path, *, page_numbers: list[int],
                                    company_name: str, report_year: int,
                                    ocr_engine: Callable[[Any], list[dict]] | None = None,
                                    dpi: int = 200, min_confidence: str = "0.85") -> dict:
    """Return sourced annual-summary OCR candidates and internal checks.

    The caller supplies company and report year as *claims*, not verified PDF
    identity. Unchecked OCR never enters the deterministic financial engine.
    A candidate is machine_checked only if year columns, unit, confidence, and
    the printed year-on-year percentage all pass. Missing checks are explicit.
    """
    if not isinstance(company_name, str) or not company_name.strip():
        raise ValueError("company_name is required for OCR identity review")
    if type(report_year) is not int or not 2000 <= report_year <= 2100:
        raise ValueError("report_year must be a four-digit integer")
    if isinstance(min_confidence, (float, bool)):
        raise ValueError("min_confidence must be a decimal string")
    try:
        threshold = Decimal(str(min_confidence))
    except InvalidOperation as exc:
        raise ValueError("min_confidence is not a decimal number") from exc
    if not threshold.is_finite() or not Decimal("0") <= threshold <= Decimal("1"):
        raise ValueError("min_confidence must be between 0 and 1")
    document = read_pdf_document(path, page_numbers=page_numbers, max_pages=len(page_numbers),
                                 ocr_engine=ocr_engine, dpi=dpi, enable_ocr=True, force_ocr=True)
    warnings = list(document["warnings"])
    title_lines = [line["text"] for page in document["pages"] for line in page["lines"][:12]]
    compact_title = re.sub(r"\s+", "", "".join(title_lines))
    compact_company = re.sub(r"\s+", "", company_name)
    identity_verified = compact_company in compact_title and f"{report_year}年年度报告" in compact_title
    if not identity_verified:
        warnings.append({"code": "OCR_IDENTITY_UNVERIFIED",
                         "detail": "Selected OCR pages did not independently confirm company and annual-report year"})
    candidates: list[dict] = []
    quarterly: dict[str, dict] = {}
    for page in document["pages"]:
        if page["read_method"] != "ocr":
            warnings.append({"code": "NOT_SCANNED_PAGE", "page": page["page"],
                             "detail": "Use analyze_reports for a text PDF page"})
            continue
        section_titles = [line for line in page["lines"]
                          if "主要会计数据和财务指标" in line["text"]]
        if not section_titles:
            warnings.append({"code": "OCR_SECTION_UNVERIFIED", "page": page["page"]})
            continue
        if _has_restatement_columns(page["lines"], report_year):
            warnings.append({"code": "OCR_RESTATEMENT_COLUMNS_UNSUPPORTED", "page": page["page"],
                             "detail": "Multiple pre/post-restatement columns require a dedicated table adapter; no automatic column choice"})
            continue
        section_y = min(_center(line)[1] for line in section_titles)
        rows = _rows(_join_wrapped_labels(page["lines"], report_year))
        numeric_growth_header = any(re.search(r"增减\s*[(（]%[)）]", line["text"])
                                    for line in page["lines"])
        annual_header: tuple[float, float] | None = None
        annual_header_y: float | None = None
        quarter_x: list[float] | None = None
        unit: str | None = None
        for row in rows:
            row_y = sum(_center(item)[1] for item in row) / len(row)
            if row_y < section_y:
                continue
            joined = "".join(item["text"] for item in row)
            if "单位" in joined:
                match = re.search(r"单位\s*[:：]\s*(千元|万元|亿元|元)", joined)
                if match:
                    unit = match.group(1)
            year_items = [(int(match.group(1)), _center(item)[0])
                          for item in row for match in [_YEAR.search(item["text"])] if match]
            years = {year: x for year, x in year_items}
            if report_year in years and report_year - 1 in years and years[report_year] < years[report_year - 1]:
                annual_header = (years[report_year], years[report_year - 1])
                annual_header_y = row_y
                quarter_x = None
                continue
            quarter_words = {"第一季度": 1, "第二季度": 2, "第三季度": 3, "第四季度": 4}
            q_positions = {quarter_words[item["text"]]: _center(item)[0]
                           for item in row if item["text"] in quarter_words}
            if len(q_positions) == 4:
                quarter_x = [q_positions[q] for q in range(1, 5)]
                annual_header = None
                continue
            label_limit = annual_header[0] - 30 if annual_header else (quarter_x[0] - 20 if quarter_x else 0)
            labels = [item for item in row if _center(item)[0] < label_limit]
            label_line = next((item for item in labels if _code(item["text"])), None)
            if label_line is None:
                continue
            code = _code(label_line["text"])
            if quarter_x and code == "revenue":
                values = []
                for x in quarter_x:
                    item = min((entry for entry in row if _AMOUNT.fullmatch(entry["text"])),
                               key=lambda entry: abs(_center(entry)[0] - x), default=None)
                    if item is None or abs(_center(item)[0] - x) > 38 or item in values:
                        values = []
                        break
                    values.append(item)
                if len(values) == 4 and unit:
                    amounts = [parse_amount(item["text"], unit) for item in values]
                    if all(amount is not None for amount in amounts):
                        with localcontext() as ctx:
                            ctx.prec = max(60, sum(len(amount.as_tuple().digits) for amount in amounts) + 16)
                            total = sum(amounts, Decimal(0))
                        quarterly[code] = {"sum": total,
                                           "confidence_ok": all(Decimal(str(item["confidence"])) >= threshold
                                                                for item in values),
                                           "sources": [_source(document, page, item) for item in values]}
                continue
            if annual_header is None or annual_header_y is None or row_y <= annual_header_y:
                continue
            x_current, x_previous = annual_header
            amounts = [item for item in row if _AMOUNT.fullmatch(item["text"])]
            current = min(amounts, key=lambda item: abs(_center(item)[0] - x_current), default=None)
            previous = min((item for item in amounts if item is not current),
                           key=lambda item: abs(_center(item)[0] - x_previous), default=None)
            if current is None or previous is None or any((abs(_center(item)[0] - x) > 40)
                                                        for item, x in ((current, x_current), (previous, x_previous))):
                continue
            row_unit = "元" if re.search(r"[（(]元[）)]", label_line["text"]) else unit
            if row_unit is None:
                warnings.append({"code": "OCR_UNIT_MISSING", "page": page["page"], "metric_code": code})
                continue
            value, prior = parse_amount(current["text"], row_unit), parse_amount(previous["text"], row_unit)
            if value is None or prior is None:
                continue
            pct = next((item for item in row if _PERCENT.fullmatch(item["text"])), None)
            if pct is None and numeric_growth_header:
                pct = next((item for item in row if _AMOUNT.fullmatch(item["text"])
                            and x_previous + 50 < _center(item)[0] < x_previous + 140), None)
            confidence_ok = all(Decimal(str(item["confidence"])) >= threshold
                                for item in (label_line, current, previous, *([pct] if pct else [])))
            pct_raw = pct["text"] if pct else None
            pct_ok = bool(pct and _percent_checked(value, prior,
                                                   pct_raw if pct_raw.endswith("%") else pct_raw + "%"))
            if not confidence_ok:
                warnings.append({"code": "OCR_LOW_CONFIDENCE", "page": page["page"], "metric_code": code})
            if pct is None:
                warnings.append({"code": "OCR_YOY_UNVERIFIED", "page": page["page"], "metric_code": code})
            elif prior <= 0:
                warnings.append({"code": "OCR_YOY_BASE_NONPOSITIVE", "page": page["page"], "metric_code": code})
            elif not pct_ok:
                warnings.append({"code": "OCR_YOY_MISMATCH", "page": page["page"], "metric_code": code})
            with localcontext() as ctx:
                ctx.prec = max(60, len(value.as_tuple().digits) + len(prior.as_tuple().digits) + 16)
                growth = (value - prior) / prior * 100 if prior > 0 else None
                growth_text = (format(growth.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
                               if growth is not None else None)
            checks = {"year_columns": True, "unit": True, "ocr_confidence": confidence_ok,
                      "reported_yoy_percentage": pct_ok, "quarterly_sum": None}
            sources = {"label": _source(document, page, label_line),
                       "current": _source(document, page, current),
                       "previous": _source(document, page, previous),
                       "percentage": _source(document, page, pct) if pct else None}
            candidates.append({"candidate_id": f"{document['document_id']}:{page['page']}:{report_year}:{code}:{len(candidates)+1}",
                               "metric_code": code, "period": str(report_year),
                               "previous_period": str(report_year - 1), "statement_scope": "summary_disclosure",
                               "value": format(value, "f"), "previous_value": format(prior, "f"),
                               "calculated_yoy_percent": growth_text,
                               "unit": "CNY", "raw_unit": row_unit, "sources": sources,
                               "checks": checks, "status": "machine_checked" if confidence_ok and pct_ok and identity_verified else "needs_review"})
    for item in candidates:
        if item["metric_code"] in quarterly:
            total = quarterly[item["metric_code"]]["sum"]
            good = total == Decimal(item["value"]) and quarterly[item["metric_code"]]["confidence_ok"]
            item["checks"]["quarterly_sum"] = good
            item["quarterly_sources"] = quarterly[item["metric_code"]]["sources"]
            if not good:
                item["status"] = "needs_review"
                warnings.append({"code": "OCR_QUARTERLY_SUM_MISMATCH", "candidate_id": item["candidate_id"]})
    if not candidates:
        warnings.append({"code": "NO_VERIFIED_FINANCIAL_ROWS", "detail": "No annual summary rows met layout and unit requirements"})
    return {"schema_version": "0.16", "document": {key: document[key] for key in
                                                   ("document_id", "file_sha256", "file_name", "page_count")},
            "identity_claim": {"company_name": company_name.strip(), "report_year": report_year,
                               "verified_from_pdf": identity_verified}, "pages_read": [p["page"] for p in document["pages"]],
            "page_geometries": [{key: page[key] for key in ("page", "width", "height", "read_method")}
                                for page in document["pages"]],
            "candidates": candidates, "warnings": warnings,
            "review_status": "requires_human_review"}
