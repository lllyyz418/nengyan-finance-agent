"""Print the analysis JSON for one or more PDF paths."""

import argparse
import json

from .pdf_adapter import analyze_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract and cross-check selected Chinese annual-report facts")
    parser.add_argument("pdfs", nargs="+", help="Local PDF files")
    args = parser.parse_args()
    print(json.dumps(analyze_reports(args.pdfs), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
