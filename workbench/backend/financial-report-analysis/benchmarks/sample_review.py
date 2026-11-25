"""Regenerate fact, metric, and risk evidence reports with one PDF parse."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from financial_engine import analyze_reports

from .check import evaluate, to_markdown
from .metric_check import GOLD_FILE, evaluate_metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Review three original annual reports against manual benchmarks")
    parser.add_argument("--sample-dir", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    gold = json.loads(GOLD_FILE.read_text(encoding="utf-8"))
    paths = [args.sample_dir / item["file_name"] for item in gold["documents"]]
    analysis = analyze_reports(paths)
    fact_result = evaluate(args.sample_dir, analysis)
    metric_result = evaluate_metrics(analysis, gold)
    risk_result = {
        "schema_version": analysis["schema_version"],
        "documents": [{"document_id": d["document_id"], "company_name": d["company_name"],
                       "report_year": d["report_year"], "file_sha256": d["file_sha256"]}
                      for d in analysis["documents"]],
        "risk_assessments": analysis["risk_assessments"],
        "risk_findings": [f for f in analysis["findings"] if f["category"] in {
            "cash_collection_pressure", "inventory_margin_pressure",
            "receivables_aging_pressure", "capital_expansion_cash_pressure",
        }],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "benchmark_report.md").write_text(to_markdown(fact_result), encoding="utf-8")
    (args.output_dir / "metric_report.json").write_text(json.dumps(metric_result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "risk_review.json").write_text(json.dumps(risk_result, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = fact_result["counts"]
    print(f"Facts: {counts['pass']} pass, {counts['gap']} gap, {counts['error']} error")
    print(f"Metrics: {metric_result['passed']} pass, {metric_result['errors']} error")
    for document in risk_result["documents"]:
        assessments = [item for item in risk_result["risk_assessments"] if item["document_id"] == document["document_id"]]
        statuses = ", ".join(f"{item['rule_id']}={item['status']}" for item in assessments)
        print(f"{document['company_name']}: {statuses}")
    if counts["gap"] or counts["error"] or metric_result["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
