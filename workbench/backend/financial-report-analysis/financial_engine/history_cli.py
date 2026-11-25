"""Command-line entrypoint for a sourced annual-report history JSON."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .history import analyze_company_history


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze one annual report and available prior annual reports")
    parser.add_argument("pdf", help="Input text-layer annual-report PDF")
    parser.add_argument("--stock-code", help="Six-digit A-share code; inferred from company name if omitted")
    parser.add_argument("--lookback-years", type=int, default=2)
    parser.add_argument("--cache-dir", help="Directory for downloaded reports; required for online mode")
    parser.add_argument("--offline", action="store_true", help="Skip disclosure website requests")
    parser.add_argument("--extra-pdf", action="append", default=[], help="Additional local annual report")
    parser.add_argument("--output", help="Write full JSON to this path; otherwise print JSON")
    args = parser.parse_args()
    if not args.offline and not args.cache_dir:
        parser.error("--cache-dir is required unless --offline is given")
    result = analyze_company_history(
        args.pdf, stock_code=args.stock_code, lookback_years=args.lookback_years,
        cache_dir=args.cache_dir, fetch_online=not args.offline,
        extra_report_paths=args.extra_pdf,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                             suffix=".json.part", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(rendered)
            temporary.replace(output)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print(f"Saved {len(result['documents'])} reports and {len(result['history']['changes'])} comparisons to {output}")
        print(f"Retrieval: {result['retrieval']['status']}; years: {result['history']['report_years']}")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
