"""Structured extraction and deterministic analysis for Chinese annual reports."""

from .core import analyze_parsed_documents
from .disclosure import (discover_annual_reports, discover_event_announcements,
                         download_announcement, download_report, resolve_security)
from .document_reader import classify_disclosure, read_pdf_document
from .history import analyze_company_history, build_annual_history
from .pdf_adapter import analyze_reports, parse_pdf
from .peer import analyze_peer_group, compare_peer_group
from .announcements import analyze_announcement
from .semiannual import (analyze_parsed_semiannual, analyze_semiannual_reports,
                         parse_semiannual_pdf)
from .pipeline import analyze_uploaded_document
from .mining import analyze_disclosure_context
from .context_risk import evaluate_context_risks
from .scan_financial import analyze_scanned_financial_pages
from .scan_review import (ScanReviewError, analyze_reviewed_scan,
                          create_scan_review_template, prepare_reviewed_scan)

__all__ = [
    "analyze_parsed_documents", "analyze_reports", "parse_pdf",
    "resolve_security", "discover_annual_reports", "download_report",
    "discover_event_announcements", "download_announcement",
    "read_pdf_document", "classify_disclosure", "analyze_announcement",
    "parse_semiannual_pdf", "analyze_parsed_semiannual", "analyze_semiannual_reports",
    "analyze_uploaded_document", "analyze_disclosure_context", "evaluate_context_risks",
    "analyze_company_history", "build_annual_history",
    "analyze_peer_group", "compare_peer_group",
    "analyze_scanned_financial_pages",
    "create_scan_review_template", "prepare_reviewed_scan", "analyze_reviewed_scan", "ScanReviewError",
]
