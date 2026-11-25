"""Deterministic file dispatcher for member 1; no agent/model orchestration."""

from __future__ import annotations

from pathlib import Path

from .announcements import analyze_announcement
from .document_reader import classify_disclosure, read_pdf_document
from .pdf_adapter import analyze_reports
from .semiannual import analyze_semiannual_reports


def _unsupported_upload(inspection: dict, analysis: dict | None = None) -> dict:
    # A short unrelated PDF is not an annual-report result. In particular,
    # announcement rejection returns "document", not "documents".
    unreadable = any(page.get("page_type") == "scan_candidate" for page in inspection.get("pages", []))
    no_text = not any(page.get("lines") for page in inspection.get("pages", []))
    code = "PDF_TEXT_UNREADABLE" if unreadable or no_text else "DOCUMENT_TYPE_UNRECOGNIZED"
    return {"schema_version": "0.10", "document_type": "unknown", "status": "unsupported",
            "input": {key: inspection.get(key) for key in
                      ("document_id", "file_sha256", "file_name", "page_count")},
            "analysis": None, "coverage": {"supported": False, "sections": []},
            "warnings": [*(analysis or inspection).get("warnings", []), {"code": code}]}


def analyze_uploaded_document(path: str | Path, *, metadata: dict | None = None,
                              enable_ocr: bool | None = None,
                              ocr_mode: str | None = None, options: dict | None = None) -> dict:
    """Route one PDF to annual, H1, or announcement analysis.

    Existing specialist APIs remain available for history, peer groups, and
    multiple files. This dispatcher neither crawls nor merges unlike periods.
    """
    if ocr_mode is None:
        ocr_mode = "auto" if enable_ocr is not False else "off"
    if ocr_mode not in {"auto", "force", "off"}:
        raise ValueError("ocr_mode must be auto, force, or off")
    inspection = read_pdf_document(path, enable_ocr=False, max_pages=2)
    route = classify_disclosure(inspection)
    if route["document_type"] == "unknown" and (metadata or {}).get("title"):
        route = classify_disclosure(inspection, title=metadata["title"])
    kind = route["document_type"]
    if kind == "annual_report":
        analysis = analyze_reports([path], options=options)
    elif kind == "half_year_report":
        from .interim import analyze_interim_pdf
        analysis = analyze_interim_pdf(path,'semiannual',options)
    elif kind == 'quarterly_report':
        from .interim import analyze_interim_pdf
        analysis = analyze_interim_pdf(path,'quarterly',options)
    elif kind == "announcement" or inspection["page_count"] <= 20:
        analysis = analyze_announcement(path, metadata=metadata,
                                        enable_ocr=ocr_mode != "off",
                                        force_ocr=ocr_mode == "force")
        kind = analysis["routing"]["document_type"]
        if kind != "announcement":
            return _unsupported_upload(inspection, analysis)
    else:
        return _unsupported_upload(inspection)
    if kind == "announcement":
        sections = [analysis["event"]["event_type"]] if analysis.get("event") and analysis["event"]["event_type"] else []
        warnings = analysis["warnings"]
        status = analysis["review_status"]
    else:
        sections = [section for document in analysis["documents"]
                    for section in document.get("sections_found", [])]
        warnings = analysis["warnings"]
        status = "analyzed_with_warnings" if warnings else "analyzed"
        if not analysis["documents"]:
            status = "failed"
    return {"schema_version": "0.10", "document_type": kind, "status": status,
            "input": {key: inspection[key] for key in
                      ("document_id", "file_sha256", "file_name", "page_count")},
            "analysis": analysis, "coverage": {"supported": bool(sections),
                                                "sections": sorted(set(sections))},
            "warnings": warnings}
