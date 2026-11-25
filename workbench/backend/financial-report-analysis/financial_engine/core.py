"""Dependency-free normalization, calculations, and review signals.

Input is the parsed-document contract in SKILL.md. This module never reads PDFs
or calls a language model. All numeric calculations use Decimal.
"""

from __future__ import annotations

import re
from calendar import monthrange
from datetime import date
from decimal import Decimal, DivisionByZero, InvalidOperation, ROUND_HALF_UP, localcontext
from typing import Any

from .risk import evaluate_document_risks


SCHEMA_VERSION = "0.6"
UNIT_MULTIPLIER = {
    "元": Decimal("1"),
    "千元": Decimal("1000"),
    "万元": Decimal("10000"),
    "亿元": Decimal("100000000"),
}
FLOW_CODES = (
    "revenue",
    "operating_cost",
    "r_and_d_expense",
    "net_profit",
    "attributable_net_profit",
    "attributable_net_profit_ex_nonrecurring",
    "operating_cash_flow",
    "total_profit",
)
STOCK_CODES = {
    "total_assets", "total_liabilities", "attributable_equity", "accounts_receivable",
    "inventory", "fixed_assets", "construction_in_progress", "inventory_gross",
    "inventory_impairment_allowance", "accounts_receivable_gross",
    "accounts_receivable_aging_within_1y", "accounts_receivable_aging_1_to_2y",
    "accounts_receivable_aging_2_to_3y", "accounts_receivable_aging_over_3y",
}


def _compact(value: str) -> str:
    return re.sub(r"[\s（）()：:／/、，,。·]", "", value or "")


def metric_code(label: str, kind: str) -> str | None:
    s = _compact(label)
    if kind == "balance_sheet":
        return {
            "应收账款": "accounts_receivable", "存货": "inventory",
            "固定资产": "fixed_assets", "在建工程": "construction_in_progress",
            "资产总计": "total_assets", "总资产": "total_assets",
            "负债合计": "total_liabilities", "负债总计": "total_liabilities", "负债总额": "total_liabilities",
        }.get(s)
    if kind == "income_statement":
        if s.startswith("研发费用"):
            return "r_and_d_expense"
        if s.startswith("营业成本") or s.startswith("减营业成本") or s.startswith("其中营业成本"):
            return "operating_cost"
        if s.startswith("净利润"):
            return "net_profit"
    if kind == "cash_flow_statement" and s.startswith("购建固定资产"):
        return "capital_expenditure_cash_paid"
    if kind == "nonrecurring":
        if "计入当期损益的政府补助" in s:
            return "government_grants_nonrecurring"
        if s == "合计":
            return "nonrecurring_total"
        return None
    if "扣除非经常性损益" in s and "净利润" in s:
        return "attributable_net_profit_ex_nonrecurring"
    if "归属于上市公司股东" in s and "净利润" in s:
        return "attributable_net_profit"
    if "经营活动产生的现金流量净额" in s:
        return "operating_cash_flow"
    if s.startswith("营业收入"):
        return "revenue"
    if s.startswith("利润总额"):
        return "total_profit"
    if s.startswith("总资产") or s.startswith("资产总额"):
        return "total_assets"
    if "归属于上市公司股东的净资产" in s:
        return "attributable_equity"
    return None


def parse_amount(raw: Any, unit: str) -> Decimal | None:
    """Return yuan, or None for blank/percentage/invalid cells."""
    # A float may already have lost digits before reaching this function.
    if raw is None or isinstance(raw, (float, bool)) or unit not in UNIT_MULTIPLIER:
        return None
    try:
        if isinstance(raw, Decimal):
            amount = raw
        else:
            s = str(raw).strip().replace("，", ",").replace(" ", "").replace("\u00a0", "")
            s = s.replace("−", "-").replace("－", "-").replace("（", "(").replace("）", ")")
            if not s or s in {"-", "--"} or "%" in s:
                return None
            if s.startswith("(") and s.endswith(")"):
                s = "-" + s[1:-1]
            # Removing separators before validation would silently turn "1,2" into 12.
            if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", s):
                return None
            amount = Decimal(s.replace(",", ""))
        if not amount.is_finite():
            return None
        with localcontext() as context:
            context.prec = max(50, len(amount.as_tuple().digits) + 12)
            return amount * UNIT_MULTIPLIER[unit]
    except InvalidOperation:
        return None


def _number(value: Decimal) -> str:
    return format(value, "f")


def _exact_sum(values: list[Decimal]) -> Decimal:
    """Add monetary amounts without the default 28-digit context rounding."""
    with localcontext() as context:
        context.prec = max(50, sum(len(value.as_tuple().digits) for value in values) + 16)
        return sum(values, Decimal("0"))


def _warning(code: str, message: str, document_id: str | None = None) -> dict:
    return {"code": code, "message": message, "document_id": document_id}


def _period_metadata(period: str, code: str) -> dict:
    year = int(period[:4])
    quarter = int(period[-1]) if re.fullmatch(r"\d{4}Q[1-4]", period) else None
    if quarter is None:
        end = f"{year}-12-31"
        start = f"{year}-01-01"
    else:
        end_month = quarter * 3
        end = f"{year}-{end_month:02d}-{monthrange(year, end_month)[1]:02d}"
        start = f"{year}-{end_month - 2:02d}-01"
    if code in STOCK_CODES:
        return {"period_type": "instant", "period_start": None, "period_end": end}
    return {"period_type": "duration", "period_start": start, "period_end": end}


def _fact(document: dict, row: dict, code: str, period: str, raw: Any) -> dict | None:
    # Reviewed OCR columns can have distinct units and exact cell anchors.
    # Legacy adapters continue to use the shared row unit/source.
    if code in {"total_assets", "total_liabilities"} and row.get("statement_scope", document.get("statement_scope", "consolidated")) != "consolidated":
        return None
    unit = row.get("value_units", {}).get(period, row.get("unit", ""))
    value = parse_amount(raw, unit)
    if value is None:
        return None
    doc_id = document["document_id"]
    source = dict(row.get("value_sources", {}).get(period, row["source"]))
    source.update({"raw_value": str(raw), "raw_unit": unit, "raw_label": row["label"]})
    restated = bool(row.get("restated_comparative") and period == str(int(document["report_year"]) - 1))
    if restated:
        source["comparative_restated"] = True
    return {
        "fact_id": f"{doc_id}:{period}:{code}",
        "document_id": doc_id,
        "company_name": document.get("company_name"),
        "metric_code": code,
        "period": period,
        "statement_scope": "consolidated",
        **_period_metadata(period, code),
        "comparative_restated": restated,
        "value": _number(value),
        "unit": "CNY",
        "raw_label": row["label"],
        "raw_value": str(raw),
        "raw_unit": unit,
        "sources": [source],
        "quality": "extracted",
    }


def analyze_parsed_documents(documents: list[dict], options: dict | None = None) -> dict:
    """Analyze parsed annual-report tables without third-party dependencies.

    Each input document has metadata plus sourced rows. Supported kinds are
    annual, quarterly, nonrecurring, selected consolidated statements and notes.
    """
    options = options or {}
    raw_threshold = options.get("nonrecurring_ratio_threshold", "0.30")
    if isinstance(raw_threshold, (float, bool)):
        raise ValueError("nonrecurring_ratio_threshold must be a decimal string, int, or Decimal")
    try:
        threshold = Decimal(str(raw_threshold))
    except InvalidOperation as exc:
        raise ValueError("nonrecurring_ratio_threshold is not a decimal number") from exc
    if not threshold.is_finite() or threshold < 0:
        raise ValueError("nonrecurring_ratio_threshold must be finite and nonnegative")
    result: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "documents": [],
        "facts": [],
        "metrics": [],
        "unavailable_metrics": [],
        "comparison_metrics": [],
        "comparison_unavailable_metrics": [],
        "analysis_options": {"minimum_negative_cash_quarters": options.get("minimum_negative_cash_quarters", 2)},
        "risk_assessments": [],
        "findings": [],
        "warnings": [],
        "audit": [],
    }
    fact_map: dict[tuple[str, str, str], dict] = {}

    for document in documents:
        doc_id = document["document_id"]
        year = int(document["report_year"])
        result["documents"].append({k: v for k, v in document.items() if k != "rows"})
        result["audit"].append({"event": "document_analyzed", "document_id": doc_id, "rows": len(document.get("rows", []))})
        for row in document.get("rows", []):
            kind = row.get("kind")
            code = row.get("metric_code") or metric_code(row.get("label", ""), kind)
            if not code:
                continue
            values = row.get("values", [])
            if kind == "quarterly":
                pairs = [(f"{year}Q{i}", values[i - 1]) for i in range(1, min(4, len(values)) + 1)]
            elif kind in {"annual", "nonrecurring", "balance_sheet", "income_statement", "cash_flow_statement", "note"}:
                pairs = [(str(year - i), values[i]) for i in range(min(2, len(values)))]
            else:
                continue
            for period, raw in pairs:
                if raw is None and row.get('allow_missing_values'):
                    continue
                fact = _fact(document, row, code, period, raw)
                if fact is None:
                    result["warnings"].append(_warning("INVALID_AMOUNT", f"{code} {period} 的数值或单位无法解析", doc_id))
                    continue
                key = (doc_id, period, code)
                old = fact_map.get(key)
                if old is None:
                    fact_map[key] = fact
                    result["facts"].append(fact)
                elif Decimal(old["value"]) == Decimal(fact["value"]):
                    if fact["sources"][0] not in old["sources"]:
                        old["sources"].extend(fact["sources"])
                        if len({source.get("section") for source in old["sources"] if source.get("section")}) >= 2:
                            old["quality"] = "cross_checked"
                            result["audit"].append({"event": "fact_cross_checked", "fact_id": old["fact_id"], "source_count": len(old["sources"])})
                    old["comparative_restated"] |= fact["comparative_restated"]
                else:
                    old["quality"] = "conflict"
                    old["sources"].extend(fact["sources"])
                    old.setdefault("conflicting_values", []).append({"value": fact["value"], "source": fact["sources"][0]})
                    result["warnings"].append(_warning("CONFLICTING_VALUES", f"{code} {period} 存在互相冲突的披露值，相关计算已跳过", doc_id))
                    result["audit"].append({"event": "fact_conflict", "fact_id": old["fact_id"], "alternative_value": fact["value"]})

        def get(period: str, code: str) -> tuple[Decimal, dict] | None:
            item = fact_map.get((doc_id, period, code))
            if not item or item["quality"] == "conflict":
                return None
            return Decimal(item["value"]), item

        comparison_mode = False

        def add_metric(code: str, period: str, value: Decimal, unit: str, formula: str, inputs: list[dict]) -> dict:
            item = {
                "metric_id": f"{doc_id}:{period}:{code}",
                "document_id": doc_id,
                "metric_code": code,
                "period": period,
                "status": "calculated",
                "value": _number(value),
                "unit": unit,
                "formula": formula,
                "input_fact_ids": [x["fact_id"] for x in inputs],
            }
            result["comparison_metrics" if comparison_mode else "metrics"].append(item)
            result["audit"].append({"event": "comparison_metric_calculated" if comparison_mode else "metric_calculated", "metric_id": item["metric_id"], "formula": formula, "inputs": item["input_fact_ids"]})
            return item

        def unavailable(code: str, period: str, reason_code: str, detail: str,
                        inputs: list[dict] | None = None) -> dict:
            item = {
                "metric_id": f"{doc_id}:{period}:{code}",
                "document_id": doc_id,
                "metric_code": code,
                "period": period,
                "status": "unavailable",
                "value": None,
                "reason_code": reason_code,
                "detail": detail,
                "input_fact_ids": [fact["fact_id"] for fact in (inputs or [])],
            }
            result["comparison_unavailable_metrics" if comparison_mode else "unavailable_metrics"].append(item)
            result["audit"].append({"event": "comparison_metric_unavailable" if comparison_mode else "metric_unavailable", "metric_id": item["metric_id"], "reason_code": reason_code})
            return item

        def calculate_one(code: str, period: str, required: list[tuple[str, str]],
                      unit: str, formula: str, operation: Any,
                      positive: tuple[int, ...] = (), nonnegative: tuple[int, ...] = (),
                      bounded: tuple[int, tuple[int, ...]] | None = None) -> None:
            """Evaluate a sourced Decimal formula or explain why it was withheld."""
            resolved = [get(p, field) for p, field in required]
            if any(item is None for item in resolved):
                missing = [f"{p}:{field}" for (p, field), item in zip(required, resolved) if item is None]
                has_conflict = any(
                    fact_map.get((doc_id, p, field), {}).get("quality") == "conflict"
                    or document.get("withheld_inputs", {}).get(f"{p}:{field}", {}).get("reason") == "CONFLICTING_INPUT"
                    for (p, field), item in zip(required, resolved) if item is None
                )
                withheld_reasons = {document.get("withheld_inputs", {}).get(f"{p}:{field}", {}).get("reason")
                                    for (p, field), item in zip(required, resolved) if item is None}
                reason = ("CONFLICTING_INPUT" if has_conflict else
                          "UNCONFIRMED_INPUT" if "UNCONFIRMED_INPUT" in withheld_reasons else
                          "REJECTED_INPUT" if "REJECTED_INPUT" in withheld_reasons else "MISSING_INPUT")
                item = unavailable(code, period, reason,
                            ", ".join(missing), [item[1] for item in resolved if item])
                item['missing_inputs'] = [{'period':p, 'code':field} for (p, field), value in zip(required, resolved) if value is None]
                return
            values = [item[0] for item in resolved if item]
            facts = [item[1] for item in resolved if item]
            for index in positive:
                if values[index] <= 0:
                    reason = "DIVISION_BY_ZERO" if values[index] == 0 else "NONPOSITIVE_INPUT"
                    item = unavailable(code, period, reason, f"{required[index][0]}:{required[index][1]} must be positive", facts)
                    if (code.endswith('_yoy') or code.endswith('_q4_qoq')) and len(values) == 2:
                        with localcontext() as context:
                            context.prec = max(50, sum(len(value.as_tuple().digits) for value in values)+16)
                            item['amount_comparison'] = {'current_period':required[0][0], 'previous_period':required[1][0],
                                'current':_number(values[0]), 'previous':_number(values[1]),
                                'absolute_change':_number(values[0]-values[1]), 'unit':'CNY'}
                    return
            for index in nonnegative:
                if values[index] < 0:
                    unavailable(code, period, "NEGATIVE_BALANCE", f"{required[index][0]}:{required[index][1]} is negative", facts)
                    return
            if bounded is not None:
                denominator_index, numerator_indexes = bounded
                if _exact_sum([values[i] for i in numerator_indexes]) > values[denominator_index]:
                    unavailable(code, period, "INCONSISTENT_COMPONENTS", "component sum exceeds its reported total", facts)
                    return
            try:
                with localcontext() as context:
                    context.prec = max(50, sum(len(value.as_tuple().digits) for value in values) + 16)
                    context.rounding = ROUND_HALF_UP
                    value = operation(values)
            except (DivisionByZero, InvalidOperation, ZeroDivisionError):
                unavailable(code, period, "DIVISION_BY_ZERO", "formula denominator is zero", facts)
                return
            if not value.is_finite():
                unavailable(code, period, "NONFINITE_RESULT", "formula produced a nonfinite value", facts)
                return
            add_metric(code, period, value, unit, formula, facts)

        def calculate(code: str, period: str, required: list[tuple[str, str]],
                      unit: str, formula: str, operation: Any, **checks: Any) -> None:
            nonlocal comparison_mode
            before = len(result['metrics'])
            calculate_one(code, period, required, unit, formula, operation, **checks)
            current_item = result['metrics'][-1] if len(result['metrics']) > before else result['unavailable_metrics'][-1]
            current_item.setdefault('unit', unit)
            def previous(value: str) -> str:
                if 'Q' in value:
                    y, q = value.split('Q')
                    return f'{y}Q{int(q)-1}' if int(q)>1 else f'{int(y)-1}Q4'
                return str(int(value)-1)
            comparison_mode = True
            before_comparison = len(result['comparison_metrics'])
            try:
                previous_operation = operation
                if code in {'inventory_days','receivables_days_approx'}:
                    previous_year = int(previous(period))
                    old_days = Decimal((date(previous_year+1,1,1)-date(previous_year,1,1)).days)
                    current_days = Decimal((date(int(period)+1,1,1)-date(int(period),1,1)).days)
                    previous_operation = lambda values: operation(values) * old_days / current_days
                calculate_one(code, previous(period), [(previous(p), field) for p, field in required],
                              unit, formula, previous_operation, **checks)
            finally:
                comparison_mode = False
            prior = (result['comparison_metrics'][-1] if len(result['comparison_metrics']) > before_comparison
                     else result['comparison_unavailable_metrics'][-1])
            comparison = {'period':previous(period), 'status':prior['status'], 'unit':unit,
                          'value':prior['value'], 'input_fact_ids':prior['input_fact_ids'],
                          'source_kind':'same_report_comparative', 'document_id':doc_id,
                          'reason_code':prior.get('reason_code'), 'detail':prior.get('detail'),
                          'missing_inputs':prior.get('missing_inputs',[])}
            if current_item['status'] == prior['status'] == 'calculated':
                with localcontext() as context:
                    context.prec = max(50, len(current_item['value']) + len(prior['value']) + 16)
                    comparison['absolute_change'] = _number(Decimal(current_item['value'])-Decimal(prior['value']))
                    comparison['change_unit'] = 'percentage_points' if unit=='percent' else unit
            current_item['comparison'] = comparison

        for code in FLOW_CODES:
            cur, prev = get(str(year), code), get(str(year - 1), code)
            calculate(f"{code}_yoy", str(year), [(str(year), code), (str(year - 1), code)],
                      "percent", "(current - previous) / previous * 100",
                      lambda v: (v[0] - v[1]) / v[1] * Decimal("100"), positive=(1,))
            if cur and prev and prev[0] <= 0:
                result["warnings"].append(_warning("NONPOSITIVE_BASE", f"{code} 上年基数非正，未计算同比百分比", doc_id))
            q4, q3 = get(f"{year}Q4", code), get(f"{year}Q3", code)
            if q4 or q3:
                calculate(f"{code}_q4_qoq", f"{year}Q4", [(f"{year}Q4", code), (f"{year}Q3", code)],
                          "percent", "(Q4 - Q3) / Q3 * 100",
                          lambda v: (v[0] - v[1]) / v[1] * Decimal("100"), positive=(1,))
                if q4 and q3 and q3[0] <= 0:
                    result["warnings"].append(_warning("NONPOSITIVE_BASE", f"{code} 第三季度基数非正，未计算环比百分比", doc_id))
            quarters = [get(f"{year}Q{i}", code) for i in range(1, 5)]
            if cur and all(quarters):
                quarter_total = _exact_sum([x[0] for x in quarters if x])
                difference = _exact_sum([quarter_total, -cur[0]])
                # Disclosed values may be rounded to the table's displayed unit.
                # Treat a one-unit difference as rounding, not a mismatch.
                precision = max(UNIT_MULTIPLIER[x[1]["raw_unit"]] for x in quarters if x)
                if abs(difference) > precision:
                    result["warnings"].append(_warning("QUARTER_SUM_MISMATCH", f"{code} 四季度合计与年度值相差 {_number(difference)} 元", doc_id))

        current, previous = str(year), str(year - 1)
        days = Decimal(str((date(year + 1, 1, 1) - date(year, 1, 1)).days))
        pct = Decimal("100")
        calculate("gross_margin", current, [(current, "revenue"), (current, "operating_cost")],
                  "percent", "(revenue - operating_cost) / revenue * 100",
                  lambda v: (v[0] - v[1]) / v[0] * pct, positive=(0,), nonnegative=(1,))
        calculate("consolidated_net_margin", current, [(current, "net_profit"), (current, "revenue")],
                  "percent", "net_profit / revenue * 100",
                  lambda v: v[0] / v[1] * pct, positive=(1,))
        calculate("adjusted_attributable_net_margin", current,
                  [(current, "attributable_net_profit_ex_nonrecurring"), (current, "revenue")],
                  "percent", "attributable_net_profit_ex_nonrecurring / revenue * 100",
                  lambda v: v[0] / v[1] * pct, positive=(1,))
        calculate("cash_to_consolidated_profit", current,
                  [(current, "operating_cash_flow"), (current, "net_profit")],
                  "times", "operating_cash_flow / net_profit",
                  lambda v: v[0] / v[1], positive=(1,))
        calculate("simplified_free_cash_flow", current,
                  [(current, "operating_cash_flow"), (current, "capital_expenditure_cash_paid")],
                  "CNY", "operating_cash_flow - capital_expenditure_cash_paid",
                  lambda v: v[0] - v[1], nonnegative=(1,))
        calculate("inventory_days", current,
                  [(previous, "inventory"), (current, "inventory"), (current, "operating_cost")],
                  "days", "period_days * (opening_inventory + closing_inventory) / (2 * operating_cost)",
                  lambda v: days * (v[0] + v[1]) / (Decimal("2") * v[2]),
                  positive=(2,), nonnegative=(0, 1))
        calculate("receivables_days_approx", current,
                  [(previous, "accounts_receivable"), (current, "accounts_receivable"), (current, "revenue")],
                  "days", "period_days * (opening_net_receivables + closing_net_receivables) / (2 * revenue)",
                  lambda v: days * (v[0] + v[1]) / (Decimal("2") * v[2]),
                  positive=(2,), nonnegative=(0, 1))
        calculate("return_on_assets", current,
                  [(current, "net_profit"), (previous, "total_assets"), (current, "total_assets")],
                  "percent", "net_profit / ((opening_total_assets + closing_total_assets) / 2) * 100",
                  lambda v: v[0] / ((v[1] + v[2]) / Decimal("2")) * pct,
                  positive=(1, 2))
        calculate("return_on_attributable_equity", current,
                  [(current, "attributable_net_profit"), (previous, "attributable_equity"), (current, "attributable_equity")],
                  "percent", "attributable_net_profit / ((opening_attributable_equity + closing_attributable_equity) / 2) * 100",
                  lambda v: v[0] / ((v[1] + v[2]) / Decimal("2")) * pct,
                  positive=(1, 2))
        calculate("fixed_assets_cip_to_assets", current,
                  [(current, "fixed_assets"), (current, "construction_in_progress"), (current, "total_assets")],
                  "percent", "(fixed_assets + construction_in_progress) / total_assets * 100",
                  lambda v: (v[0] + v[1]) / v[2] * pct,
                  positive=(2,), nonnegative=(0, 1), bounded=(2, (0, 1)))
        calculate("r_and_d_expense_to_revenue", current,
                  [(current, "r_and_d_expense"), (current, "revenue")],
                  "percent", "r_and_d_expense / revenue * 100",
                  lambda v: v[0] / v[1] * pct, positive=(1,), nonnegative=(0,))
        calculate("inventory_impairment_to_gross", current,
                  [(current, "inventory_impairment_allowance"), (current, "inventory_gross")],
                  "percent", "inventory_impairment_allowance / inventory_gross * 100",
                  lambda v: v[0] / v[1] * pct,
                  positive=(1,), nonnegative=(0,), bounded=(1, (0,)))
        # Total liabilities are not just borrowing. Negative equity can
        # legitimately produce a ratio above 100%, so do not cap this metric.
        calculate("liabilities_to_assets", current,
                  [(current, "total_liabilities"), (current, "total_assets")],
                  "percent", "total_liabilities / total_assets * 100",
                  lambda v: v[0] / v[1] * pct, positive=(1,), nonnegative=(0,))
        calculate("receivables_over_1y_to_gross", current,
                  [(current, "accounts_receivable_aging_1_to_2y"),
                   (current, "accounts_receivable_aging_2_to_3y"),
                   (current, "accounts_receivable_aging_over_3y"),
                   (current, "accounts_receivable_gross")],
                  "percent", "(aging_1_to_2y + aging_2_to_3y + aging_over_3y) / gross_receivables * 100",
                  lambda v: (v[0] + v[1] + v[2]) / v[3] * pct,
                  positive=(3,), nonnegative=(0, 1, 2), bounded=(3, (0, 1, 2)))

        profit = get(str(year), "attributable_net_profit")
        adjusted_profit = get(str(year), "attributable_net_profit_ex_nonrecurring")
        cash = get(str(year), "operating_cash_flow")
        nonrecurring = get(str(year), "nonrecurring_total")
        if profit and adjusted_profit and nonrecurring:
            difference = _exact_sum([profit[0], -adjusted_profit[0], -nonrecurring[0]])
            precision = max(
                UNIT_MULTIPLIER[item[1]["raw_unit"]]
                for item in (profit, adjusted_profit, nonrecurring)
            )
            if abs(difference) > precision:
                result["warnings"].append(_warning(
                    "NONRECURRING_RECONCILIATION",
                    f"归母净利润减扣非归母净利润与非经常性损益合计相差 {_number(difference)} 元",
                    doc_id,
                ))
        if profit and cash and profit[0] > 0 and cash[0] < 0:
            result["findings"].append({
                "finding_id": f"{doc_id}:{year}:cash_profit_divergence",
                "document_id": doc_id,
                "category": "cash_profit_divergence",
                "severity": "review",
                "summary": "归母净利润为正而经营活动现金流量净额为负，建议核查应收款、存货及预收款变动。",
                "evidence_fact_ids": [profit[1]["fact_id"], cash[1]["fact_id"]],
                "rule_id": "positive_profit_negative_operating_cash",
                "limitations": "此信号本身不能证明财务造假；归母利润与合并现金流的会计口径也不完全相同。",
            })
        if nonrecurring and profit and profit[0] > 0:
            with localcontext() as context:
                context.prec = max(50, len(nonrecurring[0].as_tuple().digits) + len(profit[0].as_tuple().digits) + 16)
                context.rounding = ROUND_HALF_UP
                ratio = nonrecurring[0] / profit[0]
            calculate("nonrecurring_to_attributable_profit", str(year),
                      [(str(year),'nonrecurring_total'),(str(year),'attributable_net_profit')],
                      'percent','nonrecurring_total / attributable_net_profit * 100',lambda values:values[0]/values[1]*Decimal('100'),positive=(1,))
            if ratio > threshold:
                result["findings"].append({
                    "finding_id": f"{doc_id}:{year}:nonrecurring_share",
                    "document_id": doc_id,
                    "category": "nonrecurring_share",
                    "severity": "review",
                    "summary": "非经常性损益占归母净利润比例超过设定阈值，建议核查利润的持续性。",
                    "evidence_fact_ids": [nonrecurring[1]["fact_id"], profit[1]["fact_id"]],
                    "rule_id": f"nonrecurring_ratio_gt_{threshold}",
                    "limitations": "阈值是项目可调筛查规则，不是会计准则或投资评级。",
                })
        elif nonrecurring or profit:
            calculate("nonrecurring_to_attributable_profit", str(year),
                      [(str(year),'nonrecurring_total'),(str(year),'attributable_net_profit')],
                      'percent','nonrecurring_total / attributable_net_profit * 100',lambda values:values[0]/values[1]*Decimal('100'),positive=(1,))
        if not profit or not cash:
            result["warnings"].append(_warning("INCOMPLETE_CASH_PROFIT_CHECK", "缺少归母净利润或经营现金流，未执行年度背离检查", doc_id))

        assessments, risk_findings = evaluate_document_risks(
            document, result["facts"], result["metrics"], options, result["warnings"]
        )
        result["risk_assessments"].extend(assessments)
        result["findings"].extend(risk_findings)
        result["audit"].extend({
            "event": "risk_rule_evaluated",
            "assessment_id": item["assessment_id"],
            "status": item["status"],
            "evidence_fact_ids": item["evidence_fact_ids"],
        } for item in assessments)

    from .policy_review import refresh_policy_reviews
    refresh_policy_reviews(result)
    return result
