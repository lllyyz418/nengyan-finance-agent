"""CLI for one uploaded report or one explicit company/year context."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .mining import analyze_disclosure_context


def main() -> None:
    parser = argparse.ArgumentParser(description="Retrieve and compare sourced annual disclosures")
    parser.add_argument("pdf", nargs="?", help="Uploaded text PDF; omit with --stock-code and --year")
    parser.add_argument("--stock-code")
    parser.add_argument("--year", type=int, help="Explicit fiscal year for company-code input")
    parser.add_argument("--lookback-years", type=int, default=2)
    parser.add_argument("--peer-code", action="append", default=[])
    parser.add_argument("--peer-group", default="")
    parser.add_argument("--selection-note", default="")
    parser.add_argument("--cache-dir")
    parser.add_argument("--online", action="store_true", help="Enable catalogue queries and downloads")
    parser.add_argument("--history-pdf", action="append", default=[])
    parser.add_argument("--metric", action="append")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        result = analyze_disclosure_context(args.pdf, stock_code=args.stock_code, report_year=args.year,
            lookback_years=args.lookback_years, peer_codes=args.peer_code, peer_group=args.peer_group,
            selection_note=args.selection_note, cache_dir=args.cache_dir, fetch_online=args.online,
            local_history_paths=args.history_pdf, metric_codes=args.metric)
    except ValueError as exc:
        parser.error(str(exc))
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        path = Path(args.output).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
        print(path)
        print(f"status={result['status']} history={result['history']['status']} peers={result['peers']['status']}")
    else:
        print(payload)
    if result["status"] == "failed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
