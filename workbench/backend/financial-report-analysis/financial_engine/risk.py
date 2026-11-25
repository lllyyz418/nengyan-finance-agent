"""Evidence-gated financial review screens for annual reports.

These project screening rules are not accounting standards, fraud diagnoses,
credit ratings, or investment recommendations. Amounts and comparisons use
Decimal throughout; every evaluated condition retains its source identifiers.
"""

from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Callable


RULE_VERSION = "1.2"
RULES = {
    "cash_collection_pressure": (
        "盈利、连续季度现金流和应收增长联合筛查",
        "应收账款账面余额不含应收票据；季节性、结算方式和并购会影响比较。信号不能证明造假。",
    ),
    "inventory_margin_pressure": (
        "存货增长、跌价准备及毛利率联合筛查",
        "准备比例受存货结构和可变现净值估计影响；需要回看产品价格及存货附注。",
    ),
    "receivables_aging_pressure": (
        "应收增速和一年以上账龄占比的方向性联合筛查",
        "只比较变化方向，未评估经济重要性；账龄不等于逾期或预期信用损失，应结合客户、合同资产、应收票据、实际回款和损失准备复核。",
    ),
    "capital_expansion_cash_pressure": (
        "扩产资产比重上升且简化自由现金流为负",
        "简化自由现金流不涵盖租赁及非现金购建；扩产可以是正常投资，不能单独推断偿债风险。",
    ),
}


def _context_precision(values: list[Decimal]) -> int:
    return max(50, sum(len(value.as_tuple().digits) for value in values) + 16)


def _growth_percent(current: Decimal, previous: Decimal) -> str:
    with localcontext() as context:
        context.prec = _context_precision([current, previous])
        return format((current - previous) / previous * Decimal("100"), "f")


def _share_percent(numerator: Decimal, denominator: Decimal) -> str:
    with localcontext() as context:
        context.prec = _context_precision([numerator, denominator])
        return format(numerator / denominator * Decimal("100"), "f")


def _ratio_gap_percentage_points(left_num: Decimal, left_den: Decimal,
                                 right_num: Decimal, right_den: Decimal) -> str:
    """Exact-sign ratio difference, expressed as percentage points, with Decimal."""
    with localcontext() as context:
        context.prec = _context_precision([left_num, left_den, right_num, right_den])
        cross_difference = left_num * right_den - right_num * left_den
        return format(cross_difference / (left_den * right_den) * Decimal("100"), "f")


def _greater_fraction(left_num: Decimal, left_den: Decimal,
                      right_num: Decimal, right_den: Decimal) -> bool:
    """Compare two positive-denominator ratios without division rounding."""
    with localcontext() as context:
        context.prec = _context_precision([left_num, left_den, right_num, right_den])
        return left_num * right_den > right_num * left_den


def _condition(code: str, passed: bool, observed: dict) -> dict:
    return {"condition_code": code, "passed": passed, "observed": observed}


def _negative_run(values: list[Decimal]) -> int:
    longest = current = 0
    for value in values:
        current = current + 1 if value < 0 else 0
        longest = max(longest, current)
    return longest


def _cash_collection(v: dict[tuple[str, str], Decimal], year: int,
                     minimum_quarters: int) -> list[dict]:
    current, previous = str(year), str(year - 1)
    profit = v[(current, "net_profit")]
    quarters = [v[(f"{year}Q{i}", "operating_cash_flow")] for i in range(1, 5)]
    ar_current, ar_previous = v[(current, "accounts_receivable_gross")], v[(previous, "accounts_receivable_gross")]
    revenue_current, revenue_previous = v[(current, "revenue")], v[(previous, "revenue")]
    if ar_current < 0 or ar_previous <= 0 or revenue_current <= 0 or revenue_previous <= 0:
        raise ValueError("应收账款上期余额及两期营收须为正，应收本期余额不得为负")
    run = _negative_run(quarters)
    return [
        _condition("positive_consolidated_profit", profit > 0, {"net_profit_cny": format(profit, "f")}),
        _condition("consecutive_negative_quarterly_cfo", run >= minimum_quarters,
                   {"longest_run": run, "minimum_required": minimum_quarters,
                    "quarterly_cfo_cny": [format(q, "f") for q in quarters]}),
        _condition("gross_receivables_growth_above_revenue_growth",
                   _greater_fraction(ar_current, ar_previous, revenue_current, revenue_previous),
                   {"receivables_growth_percent": _growth_percent(ar_current, ar_previous),
                    "revenue_growth_percent": _growth_percent(revenue_current, revenue_previous),
                    "growth_gap_percentage_points": _ratio_gap_percentage_points(
                        ar_current, ar_previous, revenue_current, revenue_previous)}),
    ]


def _inventory_margin(v: dict[tuple[str, str], Decimal], year: int) -> list[dict]:
    current, previous = str(year), str(year - 1)
    inv_c, inv_p = v[(current, "inventory_gross")], v[(previous, "inventory_gross")]
    allow_c, allow_p = v[(current, "inventory_impairment_allowance")], v[(previous, "inventory_impairment_allowance")]
    rev_c, rev_p = v[(current, "revenue")], v[(previous, "revenue")]
    cost_c, cost_p = v[(current, "operating_cost")], v[(previous, "operating_cost")]
    if min(inv_c, inv_p, rev_c, rev_p) <= 0 or min(allow_c, allow_p, cost_c, cost_p) < 0:
        raise ValueError("两期存货原值和营收须为正，跌价准备及营业成本不得为负")
    if allow_c > inv_c or allow_p > inv_p:
        raise ValueError("跌价准备超过对应存货账面余额")
    return [
        _condition("gross_inventory_growth_above_revenue_growth",
                   _greater_fraction(inv_c, inv_p, rev_c, rev_p),
                   {"inventory_growth_percent": _growth_percent(inv_c, inv_p),
                    "revenue_growth_percent": _growth_percent(rev_c, rev_p),
                    "growth_gap_percentage_points": _ratio_gap_percentage_points(
                        inv_c, inv_p, rev_c, rev_p)}),
        _condition("inventory_allowance_share_increased",
                   _greater_fraction(allow_c, inv_c, allow_p, inv_p),
                   {"current_percent": _share_percent(allow_c, inv_c),
                    "previous_percent": _share_percent(allow_p, inv_p),
                    "share_change_percentage_points": _ratio_gap_percentage_points(
                        allow_c, inv_c, allow_p, inv_p)}),
        _condition("gross_margin_declined",
                   _greater_fraction(rev_p - cost_p, rev_p, rev_c - cost_c, rev_c),
                   {"current_percent": _share_percent(rev_c - cost_c, rev_c),
                    "previous_percent": _share_percent(rev_p - cost_p, rev_p)}),
    ]


def _receivables_aging(v: dict[tuple[str, str], Decimal], year: int) -> list[dict]:
    current, previous = str(year), str(year - 1)
    gross_c, gross_p = v[(current, "accounts_receivable_gross")], v[(previous, "accounts_receivable_gross")]
    rev_c, rev_p = v[(current, "revenue")], v[(previous, "revenue")]
    if min(gross_c, gross_p, rev_c, rev_p) <= 0:
        raise ValueError("两期应收账款账面余额及营收须为正")
    aged = []
    for period in (current, previous):
        parts = [v[(period, code)] for code in (
            "accounts_receivable_aging_1_to_2y",
            "accounts_receivable_aging_2_to_3y",
            "accounts_receivable_aging_over_3y",
        )]
        if min(parts) < 0:
            raise ValueError("账龄桶出现负余额")
        with localcontext() as context:
            context.prec = _context_precision(parts)
            aged.append(sum(parts, Decimal("0")))
    if aged[0] > gross_c or aged[1] > gross_p:
        raise ValueError("一年以上账龄桶合计超过应收账款账面余额")
    return [
        _condition("gross_receivables_growth_above_revenue_growth",
                   _greater_fraction(gross_c, gross_p, rev_c, rev_p),
                   {"receivables_growth_percent": _growth_percent(gross_c, gross_p),
                    "revenue_growth_percent": _growth_percent(rev_c, rev_p),
                    "growth_gap_percentage_points": _ratio_gap_percentage_points(
                        gross_c, gross_p, rev_c, rev_p)}),
        _condition("over_1y_share_increased",
                   _greater_fraction(aged[0], gross_c, aged[1], gross_p),
                   {"current_percent": _share_percent(aged[0], gross_c),
                    "previous_percent": _share_percent(aged[1], gross_p),
                    "share_change_percentage_points": _ratio_gap_percentage_points(
                        aged[0], gross_c, aged[1], gross_p)}),
    ]


def _capital_expansion(v: dict[tuple[str, str], Decimal], year: int,
                       free_cash_flow: Decimal) -> list[dict]:
    current, previous = str(year), str(year - 1)
    assets = []
    shares = []
    for period in (current, previous):
        fixed = v[(period, "fixed_assets")]
        construction = v[(period, "construction_in_progress")]
        total = v[(period, "total_assets")]
        if fixed < 0 or construction < 0 or total <= 0:
            raise ValueError("固定资产和在建工程不得为负，总资产须为正")
        with localcontext() as context:
            context.prec = _context_precision([fixed, construction, total])
            combined = fixed + construction
        if combined > total:
            raise ValueError("固定资产加在建工程超过总资产")
        assets.append(combined)
        shares.append(total)
    return [
        _condition("simplified_free_cash_flow_negative", free_cash_flow < 0,
                   {"free_cash_flow_cny": format(free_cash_flow, "f")}),
        _condition("fixed_assets_cip_share_increased",
                   _greater_fraction(assets[0], shares[0], assets[1], shares[1]),
                   {"current_percent": _share_percent(assets[0], shares[0]),
                    "previous_percent": _share_percent(assets[1], shares[1])}),
    ]


def evaluate_document_risks(document: dict, facts: list[dict], metrics: list[dict],
                            options: dict | None = None, warnings: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Return risk assessments and triggered review findings for one annual PDF."""
    options = options or {}
    minimum_quarters = options.get("minimum_negative_cash_quarters", 2)
    if isinstance(minimum_quarters, bool) or not isinstance(minimum_quarters, int) or not 2 <= minimum_quarters <= 4:
        raise ValueError("minimum_negative_cash_quarters must be an integer from 2 to 4")
    doc_id, year = document["document_id"], int(document["report_year"])
    current, previous = str(year), str(year - 1)
    fact_index = {(item["period"], item["metric_code"]): item
                  for item in facts if item["document_id"] == doc_id}
    metric_index = {(item["period"], item["metric_code"]): item
                    for item in metrics if item["document_id"] == doc_id}
    cash_quarter_mismatch = any(
        item.get("document_id") == doc_id
        and item.get("code") == "QUARTER_SUM_MISMATCH"
        and item.get("message", "").startswith("operating_cash_flow ")
        for item in (warnings or [])
    )
    assessments: list[dict] = []
    findings: list[dict] = []

    def assess(rule_id: str, required: list[tuple[str, str]],
               evaluator: Callable, metric_code: str | None = None) -> None:
        values = {}
        evidence_facts = []
        missing = []
        for key in required:
            item = fact_index.get(key)
            if item is None:
                reason = document.get("withheld_inputs", {}).get(f"{key[0]}:{key[1]}", {}).get("reason", "MISSING_INPUT")
                missing.append({"period": key[0], "code": key[1], "reason": reason})
            elif item["quality"] == "conflict":
                missing.append({"period": key[0], "code": key[1], "reason": "CONFLICTING_INPUT"})
            else:
                values[key] = Decimal(item["value"])
                evidence_facts.append(item["fact_id"])
        evidence_metrics = []
        metric_value = None
        if metric_code:
            metric = metric_index.get((current, metric_code))
            if metric is None:
                missing.append({"period": current, "code": metric_code, "reason": "METRIC_UNAVAILABLE"})
            else:
                metric_value = Decimal(metric["value"])
                evidence_metrics.append(metric["metric_id"])
                evidence_facts.extend(metric["input_fact_ids"])
        if rule_id == "cash_collection_pressure" and cash_quarter_mismatch:
            missing.append({"period": current, "code": "operating_cash_flow", "reason": "QUARTER_SUM_MISMATCH"})
        conditions = []
        if missing:
            status = "insufficient_data"
        else:
            try:
                conditions = evaluator(values, year, minimum_quarters) if rule_id == "cash_collection_pressure" else (
                    evaluator(values, year, metric_value) if metric_code else evaluator(values, year)
                )
                status = "triggered" if all(condition["passed"] for condition in conditions) else "not_triggered"
            except ValueError as exc:
                status = "insufficient_data"
                missing.append({"period": current, "code": "INPUT_VALIDATION", "reason": str(exc)})
        summary, limitations = RULES[rule_id]
        assessment = {
            "assessment_id": f"{doc_id}:{year}:{rule_id}",
            "document_id": doc_id,
            "company_name": document.get("company_name"),
            "period": current,
            "rule_id": rule_id,
            "rule_version": RULE_VERSION,
            "status": status,
            "screen_type": "directional_combination",
            "materiality_assessed": False,
            "summary": summary,
            "conditions": conditions,
            "unmet_condition_codes": [condition["condition_code"] for condition in conditions if not condition["passed"]],
            "missing_or_invalid": missing,
            "evidence_fact_ids": list(dict.fromkeys(evidence_facts)),
            "evidence_metric_ids": evidence_metrics,
            "limitations": limitations,
        }
        assessments.append(assessment)
        if status == "triggered":
            findings.append({
                "finding_id": assessment["assessment_id"],
                "document_id": doc_id,
                "category": rule_id,
                "severity": "watch",
                "screen_type": "directional_combination",
                "materiality_assessed": False,
                "summary": summary + "；建议结合管理层讨论及相关附注复核。",
                "evidence_fact_ids": assessment["evidence_fact_ids"],
                "evidence_metric_ids": evidence_metrics,
                "rule_id": rule_id,
                "limitations": limitations,
            })

    assess("cash_collection_pressure", [
        (current, "net_profit"),
        *[(f"{year}Q{i}", "operating_cash_flow") for i in range(1, 5)],
        (current, "accounts_receivable_gross"), (previous, "accounts_receivable_gross"),
        (current, "revenue"), (previous, "revenue"),
    ], _cash_collection)
    assess("inventory_margin_pressure", [
        (current, "inventory_gross"), (previous, "inventory_gross"),
        (current, "inventory_impairment_allowance"), (previous, "inventory_impairment_allowance"),
        (current, "revenue"), (previous, "revenue"),
        (current, "operating_cost"), (previous, "operating_cost"),
    ], _inventory_margin)
    assess("receivables_aging_pressure", [
        (current, "accounts_receivable_gross"), (previous, "accounts_receivable_gross"),
        (current, "revenue"), (previous, "revenue"),
        *[(period, code) for period in (current, previous) for code in (
            "accounts_receivable_aging_1_to_2y",
            "accounts_receivable_aging_2_to_3y",
            "accounts_receivable_aging_over_3y",
        )],
    ], _receivables_aging)
    assess("capital_expansion_cash_pressure", [
        *[(period, code) for period in (current, previous) for code in (
            "fixed_assets", "construction_in_progress", "total_assets",
        )],
    ], _capital_expansion, metric_code="simplified_free_cash_flow")

    from .policy_review import evaluate_policy_reviews
    assessments.extend(evaluate_policy_reviews(document, facts))
    return assessments, findings
