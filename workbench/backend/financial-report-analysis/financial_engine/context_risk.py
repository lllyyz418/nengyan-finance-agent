"""Evidence-gated screens over sourced annual history and curated peers.

These project rules produce review prompts, never a fraud diagnosis, rating or
statistical probability. Numeric comparisons accept Decimal strings only.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from .basis_check import SAFE_STATUSES

RULE_VERSION = "1.3"
AGING_CODES = (
    "accounts_receivable_gross", "revenue",
    "accounts_receivable_aging_1_to_2y",
    "accounts_receivable_aging_2_to_3y",
    "accounts_receivable_aging_over_3y",
)


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, (float, bool)) or value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _annual_aging_conditions_valid(assessment: dict) -> bool:
    conditions = assessment.get("conditions")
    if not isinstance(conditions, list) or len(conditions) != 2:
        return False
    if not isinstance(assessment.get("assessment_id"), str) or not assessment["assessment_id"]:
        return False
    if not isinstance(assessment.get("evidence_fact_ids"), list) or not assessment["evidence_fact_ids"]:
        return False
    expected = {"gross_receivables_growth_above_revenue_growth", "over_1y_share_increased"}
    if {item.get("condition_code") for item in conditions if isinstance(item, dict)} != expected:
        return False
    for item in conditions:
        if not isinstance(item.get("passed"), bool):
            return False
        field = ("growth_gap_percentage_points" if item["condition_code"]
                 == "gross_receivables_growth_above_revenue_growth" else "share_change_percentage_points")
        observed = item.get("observed")
        gap = _decimal(observed.get(field)) if isinstance(observed, dict) else None
        if gap is None or item["passed"] != (gap > 0):
            return False
    triggered = all(item["passed"] for item in conditions)
    return assessment.get("status") == ("triggered" if triggered else "not_triggered")


def _annual_aging_observed(assessment: dict) -> dict:
    observed = {"annual_rule_status": assessment["status"],
                "assessment_id": assessment["assessment_id"]}
    fields = {
        "gross_receivables_growth_above_revenue_growth": ("growth_gap_percentage_points", "growth_gap_percentage_points"),
        "over_1y_share_increased": ("share_change_percentage_points", "over_1y_share_change_percentage_points"),
    }
    for condition in assessment["conditions"]:
        source_key, output_key = fields[condition["condition_code"]]
        value = _decimal(condition.get("observed", {}).get(source_key))
        if value is not None:
            observed[output_key] = format(value, "f")
    return observed


def _annual(analysis: dict | None, year: int) -> dict | None:
    if not isinstance(analysis, dict):
        return None
    matches = [doc for doc in analysis.get("documents", [])
               if doc.get("report_type") == "annual" and doc.get("report_year") == year]
    return matches[0] if len(matches) == 1 else None


def _assessment(ident: str, year: int, rule: str, summary: str, limitations: str,
                *, status: str, conditions: list[dict] | None = None,
                missing: list[str] | None = None, facts: list[str] | None = None,
                metrics: list[str] | None = None, documents: list[str] | None = None) -> dict:
    return {
        "assessment_id": f"{ident}:{year}:{rule}", "period": str(year),
        "rule_id": rule, "rule_version": RULE_VERSION, "status": status,
        "screen_type": "cross_report_directional", "materiality_assessed": False,
        "summary": summary, "conditions": conditions or [],
        "unmet_condition_codes": [item["condition_code"] for item in conditions or [] if not item["passed"]],
        "missing_or_invalid": _unique(missing or []),
        "evidence_fact_ids": _unique(facts or []), "evidence_metric_ids": _unique(metrics or []),
        "evidence_document_ids": _unique(documents or []), "limitations": limitations,
    }


def evaluate_context_risks(context: dict) -> dict:
    """Review cross-report consistency, repeated aging pressure, and margin pressure.

    A requested but unavailable comparison is insufficient_data. A comparison
    not requested by the caller is not_evaluated. No source fact is invented.
    """
    if not isinstance(context, dict):
        raise ValueError("context must be a disclosure-context dictionary")
    current = context.get("current")
    annual_docs = [doc for doc in (current or {}).get("documents", [])
                   if doc.get("report_type") == "annual"]
    target_doc = annual_docs[0] if len(annual_docs) == 1 else None
    output = {"schema_version": "0.1", "rule_version": RULE_VERSION,
              "status": "not_evaluated", "assessments": [], "findings": [],
              "data_quality": {"status": "not_evaluated", "comparative_differences": []},
              "limitations": ["Directional screens are not materiality-calibrated or fraud findings.",
                              "Peer groups are caller selected; product mix is not adjusted."]}
    if target_doc is None:
        return output
    year = target_doc["report_year"]
    stock_code = context.get("identity", {}).get("stock_code") or target_doc.get("security_code") or "unknown"
    history = context.get("history") or {}
    peer = context.get("peers") or {}
    comparison = history.get("comparison") or {}
    history_analysis = history.get("analysis")
    prior_doc = _annual(history_analysis, year - 1)
    history_requested = history.get("status") != "not_requested"
    peer_requested = peer.get("status") != "not_requested"
    year_checks = [check for check in comparison.get("restatement_checks", [])
                   if check.get("year") == year - 1]
    differences = [check for check in year_checks if check.get("status") in {'difference','adjusted'}]
    precision_checks=[check for check in year_checks if check.get('status')=='rounding_compatible']
    verified_checks = [check for check in year_checks if check.get("status") in SAFE_STATUSES|{'difference','adjusted'}]
    if not history_requested:
        output["data_quality"]["status"] = "not_requested"
    elif prior_doc is None or not verified_checks:
        output["data_quality"]["status"] = "insufficient_data"
    else:
        output["data_quality"] = {"status": "review_required" if differences else 'precision_compatible' if precision_checks else "no_difference_detected",
            "checked_count": len(verified_checks), "unchecked_count": len(year_checks) - len(verified_checks),
            "precision_compatible_count":len(precision_checks),
            "comparative_differences": [{"year": year - 1, "metric_code": check["metric_code"],
                "original_fact_id": check["original_fact_id"],
                "later_comparative_fact_id": check["later_comparative_fact_id"]}
                for check in differences],
            "scope": "Only extracted facts with available comparative columns were checked; differences may be restatements, reclassification, or extraction errors."}

    aging_rule = "persistent_receivables_aging_pressure"
    aging_missing = []
    aging_facts: list[str] = []
    aging_docs = [target_doc["document_id"]]
    aging_conditions: list[dict] = []
    if not history_requested:
        aging_status = "not_evaluated"
        aging_missing.append("HISTORY_NOT_REQUESTED")
    elif prior_doc is None:
        aging_status = "insufficient_data"
        aging_missing.append("PREVIOUS_ANNUAL_REPORT_UNAVAILABLE")
    else:
        aging_docs.append(prior_doc["document_id"])
        assessments = (history_analysis or {}).get("risk_assessments", [])
        relevant = {}
        for doc, period in ((prior_doc, year - 1), (target_doc, year)):
            found = [a for a in assessments if a.get("document_id") == doc["document_id"]
                     and a.get("period") == str(period) and a.get("rule_id") == "receivables_aging_pressure"]
            if len(found) != 1:
                aging_missing.append(f"AGING_ASSESSMENT_UNAVAILABLE_{period}")
            else:
                relevant[period] = found[0]
                aging_facts.extend(found[0].get("evidence_fact_ids", []))
        checks = {check.get("metric_code"): check for check in comparison.get("restatement_checks", [])
                  if check.get("year") == year - 1}
        for code in AGING_CODES:
            status = checks.get(code, {}).get("status", "unavailable")
            if status not in SAFE_STATUSES:
                aging_missing.append(f"COMPARATIVE_{status.upper()}:{code}")
                check = checks.get(code)
                if check:
                    aging_facts.extend(value for value in
                        (check.get("original_fact_id"), check.get("later_comparative_fact_id")) if value)
        for period in (year - 1, year):
            annual = relevant.get(period)
            if annual is not None and annual.get("status") not in ("triggered", "not_triggered"):
                aging_missing.append(f"ANNUAL_RULE_DATA_INSUFFICIENT_{period}")
            elif annual is not None and not _annual_aging_conditions_valid(annual):
                aging_missing.append(f"ANNUAL_RULE_CONDITIONS_INCONSISTENT_{period}")
        aging_status = "insufficient_data" if aging_missing else "not_triggered"
        if not aging_missing:
            aging_conditions = [{"condition_code": f"annual_aging_pressure_{period}",
                "passed": relevant[period]["status"] == "triggered",
                "observed": _annual_aging_observed(relevant[period])}
                for period in (year - 1, year)]
            if all(item["passed"] for item in aging_conditions):
                aging_status = "triggered"
    output["assessments"].append(_assessment(stock_code, year, aging_rule,
        "连续两份年报的应收增速及账龄占比方向性信号需复核",
        "仅核对前一年原报告与后一年比较列的关键输入；前一年报告所用的更早期比较列未由第三份原报告独立核验。只比较变化方向，未评估经济重要性；账龄不等于逾期、坏账或造假。",
        status=aging_status, conditions=aging_conditions, missing=aging_missing,
        facts=aging_facts, documents=aging_docs))

    margin_rule = "relative_gross_margin_pressure"
    margin_missing = []
    margin_conditions: list[dict] = []
    margin_metrics: list[str] = []
    margin_facts: list[str] = []
    margin_docs = [target_doc["document_id"]]
    if not history_requested or not peer_requested:
        margin_status = "not_evaluated"
        if not history_requested:
            margin_missing.append("HISTORY_NOT_REQUESTED")
        if not peer_requested:
            margin_missing.append("PEERS_NOT_REQUESTED")
    else:
        if prior_doc is not None:
            margin_docs.append(prior_doc["document_id"])
        change = [item for item in comparison.get("changes", [])
                  if item.get("metric_code") == "gross_margin"
                  and item.get("from_year") == year - 1 and item.get("to_year") == year]
        item = change[0] if len(change) == 1 else None
        if item is None or item.get("status") != "calculated":
            margin_missing.append("GROSS_MARGIN_HISTORY_UNAVAILABLE")
        elif item.get("basis_check") not in {'matched_comparatives','precision_compatible_comparatives'}:
            margin_missing.append("GROSS_MARGIN_COMPARATIVE_UNVERIFIED")
        group = peer.get("comparison") or {}
        peer_items = [entry for entry in group.get("metric_comparisons", [])
                      if entry.get("metric_code") == "gross_margin" and entry.get("report_year") == year]
        peer_item = peer_items[0] if len(peer_items) == 1 else None
        if peer_item is None or peer_item.get("valid_peer_count", 0) < 2:
            margin_missing.append("TWO_VALID_PEERS_REQUIRED")
        elif (peer_item.get("target", {}).get("status") != "available"
              or peer_item.get("peer_median", {}).get("status") != "calculated"
              or peer_item.get("target", {}).get("document_id") != target_doc["document_id"]
              or peer_item.get("unit") != "percent"):
            margin_missing.append("PEER_GROSS_MARGIN_INCOMPARABLE")
        delta = _decimal(item.get("absolute_change")) if item else None
        relative = _decimal(peer_item.get("target_minus_peer_median")) if peer_item else None
        if item is not None and delta is None:
            margin_missing.append("INVALID_GROSS_MARGIN_CHANGE")
        if peer_item is not None and relative is None:
            margin_missing.append("INVALID_PEER_MARGIN_DIFFERENCE")
        margin_status = "insufficient_data" if margin_missing else "not_triggered"
        if not margin_missing:
            margin_conditions = [
                {"condition_code": "gross_margin_declined_from_prior_year",
                 "passed": delta < 0, "observed": {"change_percentage_points": format(delta, "f")}},
                {"condition_code": "gross_margin_below_peer_median",
                 "passed": relative < 0,
                 "observed": {"target_minus_peer_median_percentage_points": format(relative, "f"),
                              "valid_peer_count": peer_item["valid_peer_count"]}},
            ]
            if all(condition["passed"] for condition in margin_conditions):
                margin_status = "triggered"
            margin_metrics = [item["from_metric_id"], item["to_metric_id"],
                              peer_item["target"]["metric_id"]]
            margin_metrics.extend(peer_item["peer_median"].get("input_metric_ids", []))
            margin_facts.extend(peer_item["target"].get("input_fact_ids", []))
            margin_facts.extend(fact for point in peer_item["peers"] if point.get("status") == "available"
                                for fact in point.get("input_fact_ids", []))
            for point in peer_item["peers"]:
                if point.get("status") == "available":
                    margin_docs.append(point["document_id"])
    output["assessments"].append(_assessment(stock_code, year, margin_rule,
        "毛利率较上年下降且低于指定同行中位数，需核查经营解释",
        "同行业务结构及收入构成未调整；方向变化尚未评估经济重要性。",
        status=margin_status, conditions=margin_conditions, missing=margin_missing,
        facts=margin_facts, metrics=margin_metrics, documents=margin_docs))

    for item in output["assessments"]:
        if item["status"] == "triggered":
            output["findings"].append({"finding_id": item["assessment_id"],
                "rule_id": item["rule_id"], "severity": "watch",
                "summary": item["summary"], "materiality_assessed": False,
                "evidence_fact_ids": item["evidence_fact_ids"],
                "evidence_metric_ids": item["evidence_metric_ids"],
                "evidence_document_ids": item["evidence_document_ids"],
                "limitations": item["limitations"]})
    output["status"] = "complete" if all(a["status"] in ("triggered", "not_triggered")
        for a in output["assessments"]) else "partial"
    return output
