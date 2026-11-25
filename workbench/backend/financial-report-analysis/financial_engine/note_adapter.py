"""Conservative extraction of two consolidated financial-note tables.

Only emit values when the table header, unit, column count, and arithmetic
reconciliation all agree. These are reported amounts, not inferred estimates.
"""

from __future__ import annotations

import re
from decimal import Decimal, localcontext
from typing import Any


_AMOUNT = re.compile(r"(?<!\d)(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\d)")
_AGING_CODES = {
    "within_1y": "accounts_receivable_aging_within_1y",
    "1_to_2y": "accounts_receivable_aging_1_to_2y",
    "2_to_3y": "accounts_receivable_aging_2_to_3y",
    "over_3y": "accounts_receivable_aging_over_3y",
}


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _numbers(text: str) -> list[str]:
    return _AMOUNT.findall(text)


def _value(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


def _exact_difference(add: list[Decimal], subtract: list[Decimal]) -> Decimal:
    values = [*add, *subtract]
    with localcontext() as context:
        context.prec = max(50, sum(len(value.as_tuple().digits) for value in values) + 16)
        return sum(add, Decimal(0)) - sum(subtract, Decimal(0))


def _bbox(line: dict) -> list[float]:
    return [round(line[key], 2) for key in ("x0", "top", "x1", "bottom")]


def _source(document_id: str, page_number: int, section: str, line: dict) -> dict:
    return {
        "document_id": document_id,
        "page_start": page_number,
        "page_end": page_number,
        "section": section,
        "row_bbox": _bbox(line),
        "raw_text": line["text"],
        "extractor": "pdf_text_lines",
    }


def _header_order(text: str, year: int, page_text: str) -> bool | None:
    flat = _compact(text)
    current, previous = flat.find(f"{year}年"), flat.find(f"{year - 1}年")
    if current >= 0 and previous >= 0:
        return current < previous
    if f"{year}年" not in _compact(page_text[:120]):
        return None
    if "本期发生额" in flat and "上期发生额" in flat:
        return flat.index("本期发生额") < flat.index("上期发生额")
    for ending, opening in (("期末账面余额", "期初账面余额"), ("期末余额", "期初余额")):
        if ending in flat and opening in flat:
            return flat.index(ending) < flat.index(opening)
    return None


def _aging_bucket(text: str) -> str | None:
    flat = _compact(text)
    if flat.startswith("1年以内") and "小计" not in flat:
        return "within_1y"
    if re.match(r"^1年?至2年", flat):
        return "1_to_2y"
    if re.match(r"^2年?至3年", flat):
        return "2_to_3y"
    if flat.startswith("3年以上"):
        return "over_3y"
    return None


def _aging_rows(lines: list[dict], page_text: str, page_number: int, document_id: str, year: int, unit: str, *, continuation: bool = False) -> tuple[list[dict], str | None]:
    start = -1 if continuation else next((i for i, line in enumerate(lines) if "按账龄披露" in _compact(line["text"])), None)
    if start is None:
        return [], "未定位账龄小节"
    header_order: bool | None = None
    buckets: dict[str, tuple[list[str], dict]] = {}
    gross: tuple[list[str], dict] | None = None
    for line in lines[start + 1:]:
        flat = _compact(line["text"])
        order = _header_order(line["text"], year, page_text)
        if order is not None and ("账龄" in flat or "期末" in flat or "期初" in flat or f"{year}年" in flat):
            header_order = order
            continue
        if "按坏账计提方法" in flat or "坏账准备" in flat:
            break
        bucket = _aging_bucket(line["text"])
        nums = _numbers(line["text"])
        if bucket and len(nums) >= 2 and bucket not in buckets:
            buckets[bucket] = (nums[-2:], line)
        elif len(buckets) == 4 and gross is None and len(nums) == 2 and (flat.startswith("合计") or re.fullmatch(r"[\d,\.]+[\d,\.]+", flat)):
            gross = (nums, line)
            break
    if header_order is not True:
        return [], "账龄表本期与上期列顺序未确认"
    if len(buckets) != 4 or gross is None:
        return [], "账龄分组或账面余额合计不完整"
    differences = [
        _exact_difference([_value(pair[0][column]) for pair in buckets.values()],
                          [_value(gross[0][column])])
        for column in (0, 1)
    ]
    if any(abs(difference) > 1 for difference in differences):
        return [], "账龄分组与账面余额合计不一致"
    rows = [
        {"kind": "note", "metric_code": _AGING_CODES[code], "label": line["text"], "values": values, "unit": unit,
         "source": _source(document_id, page_number, "accounts_receivable_aging_note", line)}
        for code, (values, line) in buckets.items()
    ]
    total_source = _source(document_id, page_number, "accounts_receivable_aging_note", gross[1])
    total_source["reconciliation_difference_raw_unit"] = [str(value) for value in differences]
    rows.append({"kind": "note", "metric_code": "accounts_receivable_gross", "label": "应收账款账龄合计", "values": gross[0], "unit": unit,
                 "source": total_source})
    return rows, None


def _inventory_rows(lines: list[dict], page_text: str, page_number: int, document_id: str, year: int, unit: str) -> tuple[list[dict], str | None]:
    start = next((i for i, line in enumerate(lines) if "存货分类" in _compact(line["text"])), None)
    if start is None:
        return [], "未定位存货分类小节"
    header_order: bool | None = None
    total: tuple[list[str], dict] | None = None
    for line in lines[start + 1:]:
        flat = _compact(line["text"])
        order = _header_order(line["text"], year, page_text)
        if order is not None:
            header_order = order
        if re.match(r"^[（(]2[）)]", flat) or "存货跌价准备和合同履约成本减值准备" in flat:
            break
        nums = _numbers(line["text"])
        if flat.startswith("合计") and len(nums) == 6:
            total = (nums, line)
            break
    if header_order is not True:
        return [], "存货分类表本期与上期列顺序未确认"
    if total is None:
        return [], "存货分类表六列合计不完整"
    values, line = total
    differences = [_exact_difference([_value(values[i])],
                                     [_value(values[i + 1]), _value(values[i + 2])]) for i in (0, 3)]
    if any(abs(difference) > 1 for difference in differences):
        return [], "存货分类表账面余额减跌价准备不等于账面价值"
    records = []
    for code, pair in (
        ("inventory_gross", [values[0], values[3]]),
        ("inventory_impairment_allowance", [values[1], values[4]]),
        ("inventory", [values[2], values[5]]),
    ):
        source = _source(document_id, page_number, "inventory_classification_note", line)
        source["reconciliation_difference_raw_unit"] = [str(value) for value in differences]
        records.append({"kind": "note", "metric_code": code, "label": "存货分类合计", "values": pair, "unit": unit,
                        "source": source})
    return records, None


def _inventory_table_rows(page: Any, lines: list[dict], page_number: int,
                          document_id: str, unit: str, continuation_page: Any = None) -> tuple[list[dict], str | None]:
    """Recover six-column inventory totals from a scoped PDF table grid."""
    marker = next((line for line in lines if "存货分类" in _compact(line["text"])), None)
    finder = getattr(page, "find_tables", None)
    if marker is None or not callable(finder):
        return [], "存货分类表格网格不可用"
    next_section = next((line for line in lines if line["top"] > marker["top"]
                         and (re.match(r"^[（(]2[）)]", _compact(line["text"]))
                              or "存货跌价准备和合同履约成本减值准备" in _compact(line["text"]))), None)
    lower = marker["top"]
    upper = next_section["top"] if next_section else float("inf")

    def amount(cell: Any, allow_blank: bool = False) -> str | None:
        text = _compact(str(cell or ""))
        if not text and allow_blank:
            return "0"
        return text if _AMOUNT.fullmatch(text) else None

    for table_index, table in enumerate(finder()):
        if not (lower < table.bbox[1] < upper and table.bbox[3] <= upper):
            continue
        grid = table.extract()
        if len(grid) < 2 or any(len(cells) < 7 for cells in grid[:2]):
            continue
        source_page = page_number
        source_table = table
        heading_page = None
        if len(grid) == 2 and continuation_page is not None:
            # Only a verified header-only grid may continue on the next page.
            from .parse_runtime import cached_page_text
            following_text = cached_page_text(continuation_page)
            following_unit = re.search(r'单位\s*[:：]\s*(千元|万元|亿元|元)(?![/／股])', following_text)
            if following_unit and following_unit.group(1) != unit:
                return [], '跨页存货分类表金额单位发生变化'
            next_lines = continuation_page.extract_text_lines()
            if any(_major_heading(line['text']) for line in next_lines[:5]):
                return [], '跨页存货分类表所属报表范围中断'
            boundary = next((line['top'] for line in next_lines
                             if re.match(r'^[（(]2[）)]', _compact(line['text']))), float('inf'))
            candidates = [candidate for candidate in continuation_page.find_tables()
                          if abs(candidate.bbox[0]-table.bbox[0]) < 2
                          and abs(candidate.bbox[2]-table.bbox[2]) < 2
                          and candidate.bbox[1] < 130 and candidate.bbox[3] <= boundary
                          and all(len(cells) == 7 for cells in candidate.extract())]
            if len(candidates) != 1:
                return [], '跨页存货分类表续表位置或列数未确认'
            source_table = candidates[0]
            grid = [*grid, *source_table.extract()]
            source_page = page_number + 1
            heading_page = page_number
        first = [_compact(str(cell or "")) for cell in grid[0][:7]]
        second = [_compact(str(cell or "")) for cell in grid[1][:7]]
        if (first[1] != "期末余额" or first[4] != "期初余额"
                or "账面余额" not in second[1] or "账面价值" not in second[3]
                or "账面余额" not in second[4] or "账面价值" not in second[6]
                or "跌价准备" not in second[2] or "跌价准备" not in second[5]):
            continue
        totals = [(i, cells) for i, cells in enumerate(grid[2:], 2)
                  if _compact(str(cells[0] or "")) == "合计"]
        if len(totals) != 1 or totals[0][0] <= 2:
            return [], "存货分类表格合计行或类别行不完整"
        total_index, total_cells = totals[0]
        total_raw = [amount(cell) for cell in total_cells[1:7]]
        if any(value is None for value in total_raw):
            return [], "存货分类表格六列合计无法解析"
        category_rows: list[list[Decimal]] = []
        for cells in grid[2:total_index]:
            if len(cells) < 7 or not _compact(str(cells[0] or "")):
                return [], "存货分类表格类别行不完整"
            parsed = [amount(cell, allow_blank=i in (1, 4))
                      for i, cell in enumerate(cells[1:7])]
            if any(value is None for value in parsed):
                return [], "存货分类表格类别金额无法解析"
            numbers = [_value(value) for value in parsed]
            if any(abs(_exact_difference([numbers[i]], [numbers[i + 1], numbers[i + 2]])) > 1
                   for i in (0, 3)):
                return [], "存货分类表格类别金额勾稽不一致"
            category_rows.append(numbers)
        total = [_value(value) for value in total_raw]
        differences = [_exact_difference([total[i]], [total[i + 1], total[i + 2]])
                       for i in (0, 3)]
        category_differences = [_exact_difference([row[i] for row in category_rows], [total[i]])
                                for i in range(6)]
        if any(abs(value) > 1 for value in [*differences, *category_differences]):
            return [], "存货分类表格合计与类别或账面价值不一致"
        source = {
            "document_id": document_id, "page_start": source_page, "page_end": source_page,
            "section": "inventory_classification_note", "table_index": table_index,
            "table_bbox": [round(x, 2) for x in source_table.bbox],
            "row_bbox": [round(x, 2) for x in source_table.rows[total_index-(2 if heading_page else 0)].bbox],
            "raw_text": "合计 " + " ".join(total_raw), "extractor": "pdf_table",
            "reconciliation_difference_raw_unit": [str(value) for value in differences],
            "category_sum_difference_raw_unit": [str(value) for value in category_differences],
        }
        if heading_page:
            source['heading_page'] = heading_page
            source['header_bbox'] = [round(x, 2) for x in table.bbox]
        return [
            {"kind": "note", "metric_code": code, "label": "存货分类合计",
             "values": pair, "unit": unit, "source": dict(source)}
            for code, pair in (
                ("inventory_gross", [total_raw[0], total_raw[3]]),
                ("inventory_impairment_allowance", [total_raw[1], total_raw[4]]),
                ("inventory", [total_raw[2], total_raw[5]]),
            )
        ], None
    return [], "未发现位置和六列表头均符合的存货分类表格"


def _aging_table_rows(page: Any, lines: list[dict], page_number: int, document_id: str,
                      year: int, unit: str) -> tuple[list[dict], str | None]:
    """Handle detailed over-three-year buckets only after column/sum checks."""
    marker = next((line for line in lines if '按账龄披露' in _compact(line['text'])), None)
    if marker is None or not callable(getattr(page, 'find_tables', None)):
        return [], '账龄表格网格不可用'
    boundary = next((line['top'] for line in lines if line['top'] > marker['top']
                     and '按坏账计提方法' in _compact(line['text'])), float('inf'))
    for table in page.find_tables():
        if not (marker['top'] < table.bbox[1] < boundary and table.bbox[3] <= boundary):
            continue
        grid = table.extract()
        if not grid or any(len(cells) != 3 for cells in grid):
            continue
        from .parse_runtime import cached_page_text
        if _header_order(' '.join(str(cell or '') for cell in grid[0]), year, cached_page_text(page)) is not True:
            continue
        values = {}
        over = []
        total = None
        over_started = False
        for index, cells in enumerate(grid[1:], 1):
            label = _compact(str(cells[0] or ''))
            raw = [_compact(str(cell or '')) for cell in cells[1:]]
            if any(value and not _AMOUNT.fullmatch(value) for value in raw):
                return [], '账龄金额格式未确认'
            if label == '合计':
                if not all(raw):
                    return [], '账龄合计不完整'
                total = (raw, index)
                break
            if label == '3年以上':
                over_started = True
                if all(raw):
                    values['over_3y'] = (raw, index)
                elif any(raw):
                    return [], '三年以上账龄合计不完整'
                continue
            bucket = _aging_bucket(label)
            if bucket and any(raw):
                if not all(raw) or bucket in values:
                    return [], '账龄分组金额不完整或重复'
                values[bucket] = (raw, index)
            elif over_started and re.fullmatch(r'(?:[3-9]|\d{2,})(?:年?至(?:[4-9]|\d{2,})年|年以上)', label):
                over.append((raw, index))
            elif any(raw):
                return [], '存在未识别的账龄分组'
        if 'over_3y' not in values and over:
            # Blank detailed cells are accepted only if both full column totals reconcile.
            values['over_3y'] = ([str(_exact_difference([_value(raw[col]) for raw, _ in over if raw[col]], []))
                                  for col in (0, 1)], over[0][1])
        if set(values) != set(_AGING_CODES) or total is None:
            return [], '账龄分组或合计不完整'
        differences = [_exact_difference([_value(raw[col]) for raw, _ in values.values()], [_value(total[0][col])])
                       for col in (0, 1)]
        if any(abs(value) > 1 for value in differences):
            return [], '账龄分组与合计不一致'
        records = []
        for code, (raw, index) in [*values.items(), ('gross', total)]:
            source = {'document_id':document_id, 'page_start':page_number, 'page_end':page_number,
                      'section':'accounts_receivable_aging_note', 'extractor':'pdf_table',
                      'table_bbox':list(table.bbox), 'row_bbox':list(table.rows[index].bbox),
                      'raw_text':' '.join(str(cell or '') for cell in grid[index]),
                      'reconciliation_difference_raw_unit':[str(value) for value in differences]}
            if code == 'over_3y' and over:
                source['parts'] = [{'page':page_number, 'row_bbox':list(table.rows[i].bbox),
                                    'text':' '.join(str(cell or '') for cell in grid[i])} for _, i in over]
                source['raw_text'] = '三年以上明细合计 ' + ' '.join(raw)
            records.append({'kind':'note', 'statement_scope':'consolidated',
                            'metric_code':'accounts_receivable_gross' if code == 'gross' else _AGING_CODES[code],
                            'label':'应收账款账龄合计' if code == 'gross' else code,
                            'values':raw, 'unit':unit, 'source':source})
        return records, None
    return [], '未确认账龄表列口径与合计'


def _other_income_rows(lines: list[dict], page_text: str, page_number: int, document_id: str, year: int, unit: str) -> tuple[list[dict], bool, str | None]:
    start = next((i for i, line in enumerate(lines) if re.match(r"^\d+[、.．]其他收益$", _compact(line["text"]))), None)
    if start is None:
        return [], False, None
    order: bool | None = None
    rows: list[dict] = []
    codes = {
        "与日常活动相关的政府补助": "government_grant_other_income_daily",
        "与资产相关的政府补助": "government_grant_other_income_asset_related",
        "与收益相关的政府补助": "government_grant_other_income_income_related",
    }
    for line in lines[start + 1:]:
        flat = _compact(line["text"])
        if re.match(r"^\d+[、.．]", flat):
            break
        header = _header_order(line["text"], year, page_text)
        if header is not None:
            order = header
        code = next((metric for label, metric in codes.items() if flat.startswith(label)), None)
        if code:
            values = _numbers(line["text"])
            if len(values) != 2:
                return [], True, "政府补助行的本期与上期金额不完整"
            rows.append({"kind": "note", "metric_code": code, "label": line["text"], "values": values,
                         "unit": unit, "source": _source(document_id, page_number, "other_income_grants_note", line)})
    if rows and order is not True:
        return [], True, "其他收益表本期与上期列顺序未确认"
    return rows, True, None


def _chinese_section_number(text: str) -> int | None:
    digits = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
              "七": 7, "八": 8, "九": 9}
    if text in digits:
        return digits[text]
    if text == "十":
        return 10
    if "十" in text:
        tens, ones = text.split("十", 1)
        if tens in ("", *digits) and ones in ("", *digits):
            return digits.get(tens, 1) * 10 + digits.get(ones, 0)
    return None


def _major_heading(text: str) -> tuple[str, int] | None:
    """Identify top-level section numbers, retaining lower numbered subheads."""
    flat = _compact(text)
    if len(flat) > 65:
        return None
    match = re.match(r"^([一二三四五六七八九十]+)[、.．](.+)$", flat)
    if not match:
        return None
    number = _chinese_section_number(match.group(1))
    if number is None:
        return None
    title = match.group(2)
    if re.match(r"^合并财务报表(?:主要)?项目(?:注释|附注)(?:（续）)?$", title):
        return "consolidated", number
    return "other", number


def _note_subject(text: str) -> str | None:
    flat = _compact(text)
    match = re.match(r"^\d+[、.．]([^（]+)(?:（续）)?$", flat)
    if not match:
        return None
    title = match.group(1)
    if title == "应收账款":
        return "accounts_receivable"
    if title == "其他应收款":
        return "other_receivables"
    if title == "存货":
        return "inventory"
    return "other"


def parse_selected_notes(pdf: Any, start_index: int, document_id: str, year: int, default_unit: str | None) -> tuple[list[dict], list[dict], list[str]]:
    """Read selected consolidated notes, including a one-page aging continuation.

    A generic aging heading alone cannot distinguish accounts receivable from
    other receivables. The numbered note subject and consolidated section are
    mandatory so a parent-company table cannot enter consolidated metrics.
    """
    rows: list[dict] = []
    warnings: list[dict] = []
    found: set[str] = set()
    grant_checked = False
    in_consolidated = False
    consolidated_section_number: int | None = None
    subject: str | None = None
    pending_aging: tuple[int, str] | None = None
    for index in range(start_index, len(pdf.pages)):
        from .parse_runtime import report_progress
        report_progress('核验财务附注', index+1, len(pdf.pages))
        page = pdf.pages[index]
        from .parse_runtime import cached_page_text
        page_text = cached_page_text(page)
        lines = page.extract_text_lines()
        begin = 0
        end = len(lines)
        for pos, line in enumerate(lines):
            heading = _major_heading(line["text"])
            if heading and heading[0] == "consolidated" and not in_consolidated:
                in_consolidated = True
                consolidated_section_number = heading[1]
                begin = pos
            elif (heading and heading[0] == "other" and in_consolidated
                  and consolidated_section_number is not None
                  and heading[1] > consolidated_section_number):
                end = pos
                break
        if not in_consolidated:
            page.close()
            continue
        scoped_lines = lines[begin:end]
        if not scoped_lines:
            page.close()
            if end < len(lines):
                break
            continue
        # A continuation must be checked before changing the subject on this page.
        if pending_aging and "aging" not in found:
            origin, origin_unit = pending_aging
            pending_aging = None
            parsed, problem = _aging_rows(scoped_lines, page_text, index + 1, document_id, year,
                                          origin_unit, continuation=True)
            if parsed:
                for row in parsed:
                    row["source"]["heading_page"] = origin
                rows.extend(parsed)
                found.add("aging")
                differences = parsed[-1]["source"].get("reconciliation_difference_raw_unit", [])
                if any(Decimal(value) != 0 for value in differences):
                    warnings.append({"code": "NOTE_ROUNDING_DIFFERENCE", "document_id": document_id,
                                     "message": f"PDF 第 {index + 1} 页 aging 表内勾稽相差最多 1 {origin_unit}，已保留原值和差异"})
            else:
                warnings.append({"code": "NOTE_TABLE_UNVERIFIED", "document_id": document_id,
                                 "message": f"PDF 第 {origin}-{index + 1} 页 aging 跨页表：{problem}"})
        aging_marker = False
        inventory_marker = False
        for line in scoped_lines:
            kind = _note_subject(line["text"])
            if kind is not None:
                subject = kind
            if "按账龄披露" in _compact(line["text"]) and subject == "accounts_receivable":
                aging_marker = True
            if "存货分类" in _compact(line["text"]) and subject == "inventory":
                inventory_marker = True
        unit_match = re.search(r"单位\s*[:：]\s*(千元|万元|亿元|元)(?![/／股])", page_text)
        unit = unit_match.group(1) if unit_match else default_unit
        if unit is None:
            if aging_marker or (inventory_marker and "inventory" not in found):
                warnings.append({"code": "NOTE_UNIT_MISSING", "document_id": document_id,
                                 "message": f"PDF 第 {index + 1} 页附注金额单位未确认"})
            page.close()
            if end < len(lines):
                break
            continue
        if aging_marker and "aging" not in found:
            parsed, problem = _aging_rows(scoped_lines, page_text, index + 1, document_id, year, unit)
            if not parsed and callable(getattr(page, 'find_tables', None)):
                grid_rows, _ = _aging_table_rows(page, scoped_lines, index + 1, document_id, year, unit)
                if grid_rows:
                    parsed, problem = grid_rows, None
            if parsed:
                rows.extend(parsed)
                found.add("aging")
                differences = parsed[-1]["source"].get("reconciliation_difference_raw_unit", [])
                if any(Decimal(value) != 0 for value in differences):
                    warnings.append({"code": "NOTE_ROUNDING_DIFFERENCE", "document_id": document_id,
                                     "message": f"PDF 第 {index + 1} 页 aging 表内勾稽相差最多 1 {unit}，已保留原值和差异"})
            elif problem in ("账龄分组或账面余额合计不完整", "账龄表本期与上期列顺序未确认"):
                pending_aging = (index + 1, unit)
            else:
                warnings.append({"code": "NOTE_TABLE_UNVERIFIED", "document_id": document_id,
                                 "message": f"PDF 第 {index + 1} 页 aging：{problem}"})
        if inventory_marker and "inventory" not in found:
            parsed, problem = _inventory_rows(scoped_lines, page_text, index + 1, document_id, year, unit)
            if not parsed:
                parsed, problem = _inventory_table_rows(page, scoped_lines, index + 1, document_id, unit,
                                                        pdf.pages[index + 1] if index + 1 < len(pdf.pages) and end == len(lines) else None)
            if parsed:
                rows.extend(parsed)
                found.add("inventory")
                differences = parsed[-1]["source"].get("reconciliation_difference_raw_unit", [])
                if any(Decimal(value) != 0 for value in differences):
                    warnings.append({"code": "NOTE_ROUNDING_DIFFERENCE", "document_id": document_id,
                                     "message": f"PDF 第 {index + 1} 页 inventory 表内勾稽相差最多 1 {unit}，已保留原值和差异"})
            elif problem != "未定位存货分类小节":
                warnings.append({"code": "NOTE_TABLE_UNVERIFIED", "document_id": document_id,
                                 "message": f"PDF 第 {index + 1} 页 inventory：{problem}"})
        if not grant_checked and "其他收益" in page_text:
            grant_rows, section_seen, problem = _other_income_rows(scoped_lines, page_text, index + 1, document_id, year, unit)
            if section_seen:
                grant_checked = True
                if grant_rows:
                    rows.extend(grant_rows)
                    found.add("government_grant_breakdown")
                elif problem:
                    warnings.append({"code": "NOTE_TABLE_UNVERIFIED", "document_id": document_id,
                                     "message": f"PDF 第 {index + 1} 页 grants：{problem}"})
        page.close()
        if end < len(lines):
            break
        if all(key in found for key in ("aging", "inventory")) and grant_checked:
            break
    if pending_aging:
        warnings.append({"code": "NOTE_TABLE_UNVERIFIED", "document_id": document_id,
                         "message": f"PDF 第 {pending_aging[0]} 页 aging 跨页表后续页未确认"})
    if not in_consolidated:
        warnings.append({"code": "NOTE_SCOPE_UNVERIFIED", "document_id": document_id,
                         "message": "未确认合并财务报表项目注释章节，附注字段已跳过"})
    for key in {"aging", "inventory"} - found:
        warnings.append({"code": "NOTE_TABLE_MISSING", "document_id": document_id,
                         "message": f"未确认 {key} 财务附注表"})
    return rows, warnings, sorted(found)
