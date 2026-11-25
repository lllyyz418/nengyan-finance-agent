"""Page-level PDF routing with optional CPU OCR and PDF-point provenance.

OCR is deliberately a separate evidence path. Its output is never passed to
the deterministic annual-report financial parser without human verification.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable


class DocumentReadError(RuntimeError):
    """The input is invalid or cannot be safely read."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _round_box(box: list[float]) -> list[float]:
    return [round(float(value), 2) for value in box]


def _text_lines(page: Any, page_number: int) -> list[dict]:
    lines = []
    for line in page.extract_text_lines() or []:
        content = re.sub(r"\s+", " ", line.get("text", "")).strip()
        if content:
            lines.append({"text": content, "page": page_number,
                          "bbox": _round_box([line["x0"], line["top"], line["x1"], line["bottom"]]),
                          "method": "pdf_text", "confidence": None})
    return lines


@lru_cache(maxsize=1)
def _rapid_engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise DocumentReadError("OCR_UNAVAILABLE: install requirements-ocr.txt to read scanned pages") from exc
    return RapidOCR()


def _default_ocr(image: Any) -> list[dict]:
    result = _rapid_engine()(image)
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is None or texts is None or scores is None:
        raise DocumentReadError("OCR_OUTPUT_UNEXPECTED: RapidOCR response has no boxes/txts/scores")
    return [{"box": box, "text": text, "confidence": score}
            for box, text, score in zip(boxes, texts, scores)]


def _ocr_lines(path: Path, page_index: int, width: float, height: float,
               ocr_engine: Callable[[Any], list[dict]], dpi: int) -> list[dict]:
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        raise DocumentReadError("OCR_RENDERER_UNAVAILABLE: install pypdfium2") from exc
    with pdfium.PdfDocument(str(path)) as document:
        page = document.get_page(page_index)
        try:
            image = page.render(scale=dpi / 72).to_pil()
        finally:
            page.close()
    x_factor, y_factor = width / image.width, height / image.height
    try:
        raw = ocr_engine(image)
    finally:
        image.close()
    lines = []
    for item in raw:
        try:
            text = re.sub(r"\s+", " ", str(item["text"])).strip()
            points = item["box"]
            confidence = float(item["confidence"])
            xs, ys = [float(point[0]) for point in points], [float(point[1]) for point in points]
            if not text or len(xs) < 4 or not 0 <= confidence <= 1:
                continue
            box = _round_box([max(0, min(xs) * x_factor), max(0, min(ys) * y_factor),
                              min(width, max(xs) * x_factor), min(height, max(ys) * y_factor)])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        lines.append({"text": text, "page": page_index + 1, "bbox": box,
                      "method": "ocr", "confidence": round(confidence, 4)})
    return sorted(lines, key=lambda line: (line["bbox"][1], line["bbox"][0]))


def read_pdf_document(path: str | Path, *, ocr_engine: Callable[[Any], list[dict]] | None = None,
                      enable_ocr: bool = True, force_ocr: bool = False,
                      max_pages: int = 20, dpi: int = 200,
                      max_bytes: int = 80 * 1024 * 1024,
                      page_numbers: list[int] | tuple[int, ...] | None = None) -> dict:
    from .parse_runtime import RUNNER
    runner = RUNNER.get()
    if runner and ocr_engine is None:
        return runner('document', path, enable_ocr=enable_ocr, force_ocr=force_ocr,
                      max_pages=max_pages, dpi=dpi, max_bytes=max_bytes, page_numbers=page_numbers)
    return _read_pdf_local(path, ocr_engine=ocr_engine, enable_ocr=enable_ocr,
                          force_ocr=force_ocr, max_pages=max_pages, dpi=dpi,
                          max_bytes=max_bytes, page_numbers=page_numbers)


def _read_pdf_local(path, *, ocr_engine=None, enable_ocr=True, force_ocr=False,
                    max_pages=20, dpi=200, max_bytes=80*1024*1024, page_numbers=None):
    """Read up to max_pages with page coordinates in PDF points, top-left origin.

    `page_numbers` selects physical PDF pages (1-based), including pages past
    the default first-page window. `ocr_engine` accepts a PIL image and returns dictionaries with polygon
    `box` (pixel coordinates), `text`, and [0,1] `confidence`. If no engine is
    supplied, RapidOCR is loaded only when a scanned page is encountered.
    """
    if not isinstance(max_pages, int) or not 1 <= max_pages <= 200:
        raise ValueError("max_pages must be an integer from 1 to 200")
    if not isinstance(dpi, int) or not 72 <= dpi <= 400:
        raise ValueError("dpi must be an integer from 72 to 400")
    if page_numbers is not None:
        if not isinstance(page_numbers, (list, tuple)) or not page_numbers or len(page_numbers) > max_pages:
            raise ValueError("page_numbers must contain 1 to max_pages PDF page numbers")
        if any(type(number) is not int or number < 1 for number in page_numbers):
            raise ValueError("page_numbers must be positive integers")
        if len(set(page_numbers)) != len(page_numbers):
            raise ValueError("page_numbers must not contain duplicates")
    source = Path(path).resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise DocumentReadError("Input must be an existing PDF file")
    if source.stat().st_size > max_bytes:
        raise DocumentReadError("PDF exceeds the configured size limit")
    with source.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise DocumentReadError("Input has no PDF signature")
    try:
        import pdfplumber
    except ImportError as exc:
        raise DocumentReadError("PDF_TEXT_BACKEND_UNAVAILABLE: install requirements.txt") from exc
    pages = []
    warnings = []
    try:
        with pdfplumber.open(source) as pdf:
            total_pages = len(pdf.pages)
            if page_numbers is not None and any(number > total_pages for number in page_numbers):
                raise ValueError("page_numbers contains a page beyond the end of the PDF")
            indices = ([number - 1 for number in page_numbers] if page_numbers is not None
                       else range(min(total_pages, max_pages)))
            for index in indices:
                from .parse_runtime import report_progress
                report_progress('识别报告类型与文本',index+1,total_pages)
                page = pdf.pages[index]
                text_lines = _text_lines(page, index + 1)
                visible_chars = sum(len(line["text"]) for line in text_lines)
                image_count = len(page.images or [])
                sparse = visible_chars < 40
                page_type = "scan_candidate" if sparse and image_count else (
                    "sparse_text" if sparse else "text")
                method = "pdf_text"
                if enable_ocr and (force_ocr or page_type == "scan_candidate"):
                    try:
                        text_lines = _ocr_lines(source, index, float(page.width), float(page.height),
                                                ocr_engine or _default_ocr, dpi)
                        method = "ocr" if text_lines else "ocr_empty"
                    except (DocumentReadError, OSError, RuntimeError) as exc:
                        warnings.append({"code": "OCR_FAILED", "page": index + 1, "detail": str(exc)})
                        method = "ocr_unavailable"
                elif page_type == "scan_candidate":
                    warnings.append({"code": "OCR_DISABLED", "page": index + 1})
                    method = "ocr_disabled"
                pages.append({"page": index + 1, "width": round(float(page.width), 2),
                              "height": round(float(page.height), 2), "page_type": page_type,
                              "read_method": method, "image_count": image_count,
                              "lines": text_lines})
                page.close()
    except (OSError, ValueError) as exc:
        raise DocumentReadError(f"PDF_READ_FAILED: {exc}") from exc
    if page_numbers is None and total_pages > max_pages:
        warnings.append({"code": "PAGE_LIMIT", "pages_read": max_pages, "pages_total": total_pages})
    methods = {page["read_method"] for page in pages}
    status = "needs_review" if ((page_numbers is None and total_pages > max_pages) or
                                any(page["page_type"] != "text" for page in pages) or
                                any(method in methods for method in ("ocr", "ocr_empty", "ocr_unavailable", "ocr_disabled"))) else "text_extracted"
    if any(page["page_type"] == "sparse_text" for page in pages):
        warnings.append({"code": "SPARSE_TEXT_PAGE", "pages": [page["page"] for page in pages
                                                                  if page["page_type"] == "sparse_text"]})
    digest = _sha256(source)
    return {"document_id": digest[:16], "file_sha256": digest,
            "file_name": source.name, "page_count": total_pages,
            "pages_read": len(pages), "status": status, "pages": pages, "warnings": warnings}


def classify_disclosure(document: dict, *, title: str | None = None) -> dict:
    """Route to an adapter; never silently send an announcement to annual math."""
    heading = re.sub(r"\s+", "", title or "")
    if not heading:
        heading = "".join(line["text"] for page in document["pages"][:2]
                          for line in page["lines"][:12])
    if re.search(r"(?:20\d{2}年?)?半年度报告", heading):
        kind = "half_year_report"
    elif re.search(r'(?:20\d{2}年?)?第[一三13]季度报告',heading):
        kind = 'quarterly_report'
    elif re.search(r"(?:20\d{2}年?)?年度报告", heading):
        kind = "annual_report"
    elif "公告" in heading:
        kind = "announcement"
    else:
        kind = "unknown"
    return {"document_type": kind, "route": {"annual_report": "annual_parser",
                                               "announcement": "announcement_parser"}.get(kind, "manual_review")}
