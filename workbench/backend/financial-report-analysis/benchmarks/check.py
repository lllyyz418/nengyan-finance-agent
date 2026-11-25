"""Compare extracted facts with values independently transcribed from PDFs.

Run from the project directory:
    python -B -m benchmarks.check --output benchmarks/benchmark_report.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

from financial_engine import analyze_reports


GOLD_FILE = Path(__file__).with_name("annual_2025_gold.json")
UNIT_MULTIPLIER = {"元": Decimal(1), "千元": Decimal(1000), "万元": Decimal(10000)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _amount(raw: str, unit: str) -> Decimal:
    return Decimal(raw.replace(",", "")) * UNIT_MULTIPLIER[unit]


def evaluate(sample_dir: str | Path, analysis: dict | None = None) -> dict:
    """Return detailed outcomes; a known coverage gap is distinct from an error."""
    gold = json.loads(GOLD_FILE.read_text(encoding="utf-8"))
    sample_dir = Path(sample_dir)
    outcomes: list[dict] = []
    paths: list[Path] = []
    available: set[str] = set()

    for document in gold["documents"]:
        path = sample_dir / document["file_name"]
        if not path.is_file():
            outcomes.append({"status": "error", "company": document["company_name"], "code": "FILE_MISSING", "period": "", "detail": str(path)})
            continue
        if _sha256(path) != document["sha256"]:
            outcomes.append({"status": "error", "company": document["company_name"], "code": "FILE_HASH_MISMATCH", "period": "", "detail": str(path)})
            continue
        paths.append(path)
        available.add(document["file_name"])

    analysis = analysis if analysis is not None else (
        analyze_reports(paths) if paths else {"documents": [], "facts": [], "warnings": []}
    )
    extracted_docs = {doc["file_sha256"]: doc for doc in analysis["documents"]}
    facts = {(f["document_id"], f["metric_code"], f["period"]): f for f in analysis["facts"]}

    for document in gold["documents"]:
        if document["file_name"] not in available:
            continue
        company = document["company_name"]
        parsed_doc = extracted_docs.get(document["sha256"])
        if parsed_doc is None:
            outcomes.append({"status": "error", "company": company, "code": "DOCUMENT_NOT_PARSED", "period": "", "detail": document["file_name"]})
            continue
        if parsed_doc.get("report_year") != 2025 or parsed_doc.get("company_name") != company:
            outcomes.append({"status": "error", "company": company, "code": "DOCUMENT_METADATA_MISMATCH", "period": "", "detail": str(parsed_doc.get("company_name"))})
        for item in document["facts"]:
            code, period = item["code"], item["period"]
            expected = _amount(item["raw"], item["unit"])
            fact = facts.get((parsed_doc["document_id"], code, period))
            base = {
                "company": company,
                "code": code,
                "period": period,
                "expected_raw": item["raw"],
                "expected_unit": item["unit"],
                "expected_yuan": str(expected),
                "gold_page": item["page"],
                "section": item["section"],
            }
            if fact is None:
                status = "error" if item["current_scope"] else "gap"
                outcomes.append({**base, "status": status, "detail": "字段缺失"})
                continue
            try:
                actual = Decimal(fact["value"])
                raw = _amount(fact["raw_value"], fact["raw_unit"])
            except (KeyError, InvalidOperation, ValueError) as exc:
                outcomes.append({**base, "status": "error", "detail": f"数值或单位无法验证: {exc}"})
                continue
            sources = fact.get("sources", [])
            page_ok = any(s.get("page_start", 0) <= item["page"] <= s.get("page_end", 0) for s in sources)
            errors = []
            if actual != expected or raw != expected:
                errors.append(f"金额不符：实际 {fact['value']} 元")
            if fact.get("raw_unit") != item["unit"]:
                errors.append(f"原始单位不符：实际 {fact.get('raw_unit')}")
            if not page_ok:
                errors.append(f"来源页不符：实际 {[(s.get('page_start'), s.get('page_end')) for s in sources]}")
            if fact.get("quality") == "conflict":
                errors.append("存在冲突值")
            outcomes.append({**base, "status": "error" if errors else "pass", "detail": "；".join(errors) if errors else "数值、单位、期间和页码均匹配"})

    counts = Counter(item["status"] for item in outcomes)
    return {
        "benchmark_version": gold["benchmark_version"],
        "sample_dir": str(sample_dir.resolve()),
        "counts": {key: counts[key] for key in ("pass", "gap", "error")},
        "outcomes": outcomes,
        "parser_warnings": analysis["warnings"],
        "observations": {doc["company_name"]: doc.get("observations", []) for doc in gold["documents"]},
    }


def to_markdown(result: dict) -> str:
    counts = result["counts"]
    lines = [
        "# 三份年报人工核对结果",
        "",
        f"样本目录：`{result['sample_dir']}`",
        f"核对通过：**{counts['pass']}**；当前未覆盖：**{counts['gap']}**；错误：**{counts['error']}**。",
        "",
        "`gap` 表示标准答案已从原 PDF 人工核对，但当前提取器还未覆盖该字段；`error` 表示已覆盖字段缺失或值、单位、期间、来源页不符。",
        "",
        "| 公司 | 期间 | 字段 | PDF 原值 | 原单位 | 标准值（元） | PDF 页 | 章节 | 状态 | 说明 |",
        "| --- | --- | --- | ---: | --- | ---: | ---: | --- | --- | --- |",
    ]
    for item in result["outcomes"]:
        clean = lambda value: str(value).replace("|", "\\|").replace("\n", " ")
        lines.append("| " + " | ".join(clean(item.get(key, "")) for key in ("company", "period", "code", "expected_raw", "expected_unit", "expected_yuan", "gold_page", "section", "status", "detail")) + " |")
    lines.extend(["", "## 人工核对时发现的口径事项", ""])
    for company, notes in result["observations"].items():
        for note in notes:
            lines.append(f"- **{company}**：{note}")
    if result["parser_warnings"]:
        lines.extend(["", "## 解析器警告", ""])
        for warning in result["parser_warnings"]:
            lines.append(f"- `{warning['code']}`：{warning['message']}")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check parsed annual-report facts against manually verified PDF values")
    parser.add_argument("--sample-dir", default=os.environ.get("AI_FINANCE_SAMPLE_DIR", str(Path(__file__).resolve().parents[3])))
    parser.add_argument("--output", type=Path, help="Optional UTF-8 Markdown report path")
    parser.add_argument("--strict", action="store_true", help="Treat known coverage gaps as failures")
    args = parser.parse_args()
    result = evaluate(args.sample_dir)
    report = to_markdown(result)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
        print(f"Report: {args.output.resolve()}")
    else:
        print(report)
    if result["counts"]["error"] or (args.strict and result["counts"]["gap"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
