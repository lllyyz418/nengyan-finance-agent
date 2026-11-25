"""Local UTF-8 JSON CLI for half-year reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .semiannual import analyze_semiannual_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze selected fields in text H1 reports")
    parser.add_argument("pdf", nargs="+", help="One or more half-year PDF files")
    parser.add_argument("--output", help="Write UTF-8 JSON to this path")
    args = parser.parse_args()
    result = analyze_semiannual_reports(args.pdf)
    payload = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(payload + "\n", encoding="utf-8")
        print(str(destination.resolve()))
    else:
        print(payload)


if __name__ == "__main__":
    main()
