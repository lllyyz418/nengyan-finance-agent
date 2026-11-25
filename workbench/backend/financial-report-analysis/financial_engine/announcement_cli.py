"""Small local CLI for announcement extraction; output is UTF-8 JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .announcements import analyze_announcement


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract sourced announcement event candidates")
    parser.add_argument("pdf", help="Local announcement PDF")
    parser.add_argument("--metadata", help="JSON manifest returned by download_announcement")
    parser.add_argument("--output", help="Write JSON to this UTF-8 path; otherwise print it")
    parser.add_argument("--no-ocr", action="store_true", help="Leave scanned pages for review")
    parser.add_argument("--max-pages", type=int, default=20)
    args = parser.parse_args()
    metadata = json.loads(Path(args.metadata).read_text(encoding="utf-8")) if args.metadata else None
    result = analyze_announcement(args.pdf, metadata=metadata,
                                  enable_ocr=not args.no_ocr, max_pages=args.max_pages)
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
