"""Command-line entrypoint for a sourced, user-curated peer comparison."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from .peer import analyze_peer_group


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare annual-report metrics with selected peer companies")
    parser.add_argument("pdf", help="Target company's text-layer annual-report PDF")
    parser.add_argument("--target-code", help="Six-digit target A-share code; checked against the PDF company name")
    parser.add_argument("--peer-code", action="append", required=True, help="Six-digit peer A-share code; repeat for each peer")
    parser.add_argument("--peer-group", required=True, help="Business segment shared by the selected companies")
    parser.add_argument("--selection-note", required=True, help="Why this peer group was chosen and its limitations")
    parser.add_argument("--cache-dir", required=True, help="Directory for downloaded peer reports")
    parser.add_argument("--output", help="Write full JSON to this path; otherwise print JSON")
    args = parser.parse_args()
    result = analyze_peer_group(
        args.pdf, target_code=args.target_code, peer_codes=args.peer_code,
        peer_group=args.peer_group, selection_note=args.selection_note,
        cache_dir=args.cache_dir,
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if not args.output:
        print(rendered)
        return
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
    comparison = result.get("comparison") or {}
    counted = sum(item["peer_median"]["status"] == "calculated"
                  for item in comparison.get("metric_comparisons", []))
    print(f"Saved peer comparison to {output}")
    print(f"Status: {result['status']}; companies: {list(result['reports'])}; metrics with peer median: {counted}")


if __name__ == "__main__":
    main()
