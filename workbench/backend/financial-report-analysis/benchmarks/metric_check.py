"""Independent metric oracle using manually transcribed PDF facts.

The expected values are exact Fraction results derived from annual_2025_gold.json.
This module does not call financial_engine's formula functions to compute answers.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, localcontext
from fractions import Fraction
from pathlib import Path


GOLD_FILE = Path(__file__).with_name("annual_2025_gold.json")
UNIT_MULTIPLIER = {"元": 1, "千元": 1000, "万元": 10000, "亿元": 100000000}


def _gold_values(document: dict) -> dict[tuple[str, str], Fraction]:
    result = {}
    for fact in document["facts"]:
        key = (fact["period"], fact["code"])
        value = Fraction(fact["raw"].replace(",", "")) * UNIT_MULTIPLIER[fact["unit"]]
        if key in result and result[key] != value:
            raise ValueError(f"Conflicting manual gold values: {key}")
        result[key] = value
    return result


def expected_metrics(document: dict) -> dict[str, Fraction]:
    """Compute 13 metric answers from manually checked raw PDF amounts."""
    g = _gold_values(document)
    current, previous = "2025", "2024"

    def v(period: str, code: str) -> Fraction:
        return g[(period, code)]

    revenue = v(current, "revenue")
    cost = v(current, "operating_cost")
    net_profit = v(current, "net_profit")
    inventory_average = (v(previous, "inventory") + v(current, "inventory")) / 2
    receivables_average = (v(previous, "accounts_receivable") + v(current, "accounts_receivable")) / 2
    assets_average = (v(previous, "total_assets") + v(current, "total_assets")) / 2
    attributable_equity_average = (
        v(previous, "attributable_equity") + v(current, "attributable_equity")
    ) / 2
    days = (date(2026, 1, 1) - date(2025, 1, 1)).days
    aged = sum((v(current, code) for code in (
        "accounts_receivable_aging_1_to_2y",
        "accounts_receivable_aging_2_to_3y",
        "accounts_receivable_aging_over_3y",
    )), Fraction(0))
    return {
        "gross_margin": (revenue - cost) * 100 / revenue,
        "consolidated_net_margin": net_profit * 100 / revenue,
        "adjusted_attributable_net_margin": v(current, "attributable_net_profit_ex_nonrecurring") * 100 / revenue,
        "cash_to_consolidated_profit": v(current, "operating_cash_flow") / net_profit,
        "simplified_free_cash_flow": v(current, "operating_cash_flow") - v(current, "capital_expenditure_cash_paid"),
        "inventory_days": days * inventory_average / cost,
        "receivables_days_approx": days * receivables_average / revenue,
        "return_on_assets": net_profit * 100 / assets_average,
        "return_on_attributable_equity": v(current, "attributable_net_profit") * 100 / attributable_equity_average,
        "fixed_assets_cip_to_assets": (
            v(current, "fixed_assets") + v(current, "construction_in_progress")
        ) * 100 / v(current, "total_assets"),
        "r_and_d_expense_to_revenue": v(current, "r_and_d_expense") * 100 / revenue,
        "inventory_impairment_to_gross": v(current, "inventory_impairment_allowance") * 100 / v(current, "inventory_gross"),
        "receivables_over_1y_to_gross": aged * 100 / v(current, "accounts_receivable_gross"),
    }


def evaluate_metrics(analysis: dict, gold: dict | None = None) -> dict:
    """Compare engine output to exact-rational answers from the manual PDF gold."""
    if gold is None:
        gold = json.loads(GOLD_FILE.read_text(encoding="utf-8"))
    parsed = {item.get("file_sha256"): item for item in analysis["documents"]}
    metrics = {(item["document_id"], item["period"], item["metric_code"]): item
               for item in analysis["metrics"]}
    outcomes = []
    tolerance = Decimal("1e-40")
    for document in gold["documents"]:
        parsed_document = parsed.get(document["sha256"])
        if parsed_document is None:
            outcomes.append({"company": document["company_name"], "metric_code": "DOCUMENT", "status": "error", "detail": "PDF hash not present in analysis"})
            continue
        try:
            expected = expected_metrics(document)
        except (KeyError, ZeroDivisionError, ValueError) as exc:
            outcomes.append({"company": document["company_name"], "metric_code": "GOLD", "status": "error", "detail": str(exc)})
            continue
        for code, answer in expected.items():
            metric = metrics.get((parsed_document["document_id"], "2025", code))
            if metric is None:
                outcomes.append({"company": document["company_name"], "metric_code": code, "status": "error", "detail": "Metric missing"})
                continue
            with localcontext() as context:
                context.prec = 100
                context.rounding = ROUND_HALF_UP
                exact_decimal = Decimal(answer.numerator) / Decimal(answer.denominator)
                actual = Decimal(metric["value"])
                difference = abs(actual - exact_decimal)
                display = str(exact_decimal.quantize(Decimal("0.000001")))
            status = "pass" if difference <= tolerance else "error"
            outcomes.append({
                "company": document["company_name"],
                "metric_code": code,
                "status": status,
                "expected_6dp": display,
                "actual": metric["value"],
                "difference": str(difference),
                "detail": "exact Fraction from manually transcribed PDF facts",
            })
    return {
        "benchmark_version": gold["benchmark_version"],
        "checked": len(outcomes),
        "passed": sum(item["status"] == "pass" for item in outcomes),
        "errors": sum(item["status"] == "error" for item in outcomes),
        "outcomes": outcomes,
    }


def main() -> None:
    from financial_engine import analyze_reports

    parser = argparse.ArgumentParser(description="Recompute 39 financial metrics independently from manual PDF gold")
    parser.add_argument("--sample-dir", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    gold = json.loads(GOLD_FILE.read_text(encoding="utf-8"))
    paths = [args.sample_dir / document["file_name"] for document in gold["documents"]]
    result = evaluate_metrics(analyze_reports(paths), gold)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Report: {args.output.resolve()}")
    else:
        print(rendered)
    if result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
