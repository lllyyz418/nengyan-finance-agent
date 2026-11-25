"""Create unchecked review forms or analyze host-confirmed scan data."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .scan_review import analyze_reviewed_scan, create_scan_review_template


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    template = commands.add_parser("template", help="Create a form with all decisions pending")
    template.add_argument("candidates", type=Path)
    template.add_argument("--output", type=Path, required=True)
    analyze = commands.add_parser("analyze", help="Validate explicit human decisions and run existing formulas")
    analyze.add_argument("candidates", type=Path)
    analyze.add_argument("review", type=Path)
    analyze.add_argument("--source-pdf", type=Path, required=True)
    analyze.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = [args.candidates]
    if args.command == "analyze":
        inputs.extend([args.review, args.source_pdf])
    if args.output.resolve() in {path.resolve() for path in inputs}:
        parser.error("Output must not overwrite a source, candidate bundle, or review form")
    if args.output.exists():
        parser.error("Output already exists; choose a new file to preserve previous review records")
    try:
        scan = json.loads(args.candidates.read_text(encoding="utf-8-sig"))
        if args.command == "template":
            result = create_scan_review_template(scan)
        else:
            review = json.loads(args.review.read_text(encoding="utf-8-sig"))
            result = analyze_reviewed_scan(scan, review, source_path=args.source_pdf)
        encoded = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded + "\n")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(str(args.output))


if __name__ == "__main__":
    main()
