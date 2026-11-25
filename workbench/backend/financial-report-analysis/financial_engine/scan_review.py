"""Explicit human-review boundary between OCR candidates and annual math.

The host authenticates the human and retains the original candidate bundle.
This module validates the supplied record; a JSON 'human' declaration itself
is not authentication. No model, network, or OCR call is made here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .core import UNIT_MULTIPLIER, analyze_parsed_documents, parse_amount
from .scan_financial import _percent_checked


class ScanReviewError(ValueError):
    """A review record, candidate snapshot, or source is inconsistent."""


_CODES = {"revenue", "operating_cash_flow", "attributable_net_profit",
          "total_assets", "attributable_equity"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ScanReviewError(message)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _snapshot(scan: dict) -> str:
    try:
        encoded = json.dumps(scan, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ScanReviewError("Candidate bundle must be finite, JSON-serializable data") from exc
    return hashlib.sha256(encoded).hexdigest()


def _check_source(source: dict, document: dict, pages: list[int]) -> None:
    _require(isinstance(source, dict), "Missing OCR cell source")
    _require(source.get("document_id") == document["document_id"] and
             source.get("file_sha256") == document["file_sha256"], "OCR source belongs to another document")
    page = source.get("page")
    _require(type(page) is int and page in pages, "OCR source page is not in pages_read")
    box = source.get("bbox")
    _require(isinstance(box, list) and len(box) == 4 and
             all(type(x) in (int, float) and math.isfinite(x) and x >= 0 for x in box),
             "OCR source bbox must contain four finite nonnegative coordinates")
    _require(box[2] > box[0] and box[3] > box[1], "OCR source bbox has no area")
    confidence = source.get("confidence")
    _require(type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1,
             "OCR source confidence must be finite and between 0 and 1")
    _require(source.get("method") == "ocr" and _text(source.get("text")), "OCR source text/method is invalid")


def _validate_scan(scan: dict) -> dict[str, dict]:
    _require(isinstance(scan, dict) and scan.get("schema_version") == "0.16", "Expected a v0.16 OCR candidate bundle")
    document = scan.get("document")
    _require(isinstance(document, dict), "Missing document metadata")
    digest = document.get("file_sha256")
    _require(isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
             "file_sha256 must be a lowercase SHA-256 digest")
    _require(document.get("document_id") == digest[:16], "document_id does not match file_sha256")
    count = document.get("page_count")
    _require(type(count) is int and count > 0 and _text(document.get("file_name")), "Invalid PDF metadata")
    pages = scan.get("pages_read")
    _require(isinstance(pages, list) and bool(pages) and
             all(type(p) is int and 1 <= p <= count for p in pages) and len(set(pages)) == len(pages),
             "pages_read must contain distinct physical PDF page numbers")
    identity = scan.get("identity_claim")
    _require(isinstance(identity, dict) and _text(identity.get("company_name")) and
             type(identity.get("report_year")) is int and 2000 <= identity["report_year"] <= 2100,
             "Invalid OCR identity claim")
    year = identity["report_year"]
    candidates = scan.get("candidates")
    _require(isinstance(candidates, list), "candidates must be a list")
    indexed: dict[str, dict] = {}
    for candidate in candidates:
        _require(isinstance(candidate, dict), "Each candidate must be a dictionary")
        cid = candidate.get("candidate_id")
        _require(_text(cid) and cid not in indexed, "Candidate IDs must be nonempty and unique")
        _require(isinstance(candidate.get("metric_code"), str) and candidate["metric_code"] in _CODES and candidate.get("period") == str(year) and
                 candidate.get("previous_period") == str(year - 1), "Unsupported field or mismatched annual periods")
        _require(candidate.get("unit") == "CNY" and isinstance(candidate.get("raw_unit"), str) and candidate["raw_unit"] in UNIT_MULTIPLIER and
                 candidate.get("statement_scope") == "summary_disclosure", "Invalid candidate currency/unit/scope")
        _require(candidate.get("status") in ("machine_checked", "needs_review") and
                 isinstance(candidate.get("checks"), dict), "Missing candidate check status")
        sources = candidate.get("sources")
        _require(isinstance(sources, dict), "Missing candidate sources")
        for role in ("label", "current", "previous"):
            _check_source(sources.get(role), document, pages)
        for role, key in (("current", "value"), ("previous", "previous_value")):
            normalized = candidate.get(key)
            _require(isinstance(normalized, str), "Normalized monetary values must be decimal strings")
            value = parse_amount(normalized, "元")
            original = parse_amount(sources[role]["text"], candidate["raw_unit"])
            _require(value is not None and original is not None and value == original,
                     "Normalized candidate amount does not match original OCR amount and unit")
        if sources.get("percentage") is not None:
            _check_source(sources["percentage"], document, pages)
        for source in candidate.get("quarterly_sources", []):
            _check_source(source, document, pages)
        indexed[cid] = candidate
    _snapshot(scan)
    return indexed


def create_scan_review_template(scan_result: dict) -> dict:
    """Produce an unchecked form; machine_checked never preselects confirmation."""
    candidates = _validate_scan(scan_result)
    identity = scan_result["identity_claim"]
    return {"schema_version": "0.17", "file_sha256": scan_result["document"]["file_sha256"],
            "candidate_snapshot_sha256": _snapshot(scan_result), "review_id": None,
            "reviewer": {"id": None, "kind": None}, "reviewed_at": None,
            "identity": {"decision": "pending", "company_name": identity["company_name"],
                         "report_year": identity["report_year"], "currency": "CNY",
                         "statement_scope": None, "comparative_restated": None},
            "decisions": [{"candidate_id": cid, "decision": "pending", "evidence_confirmed": False,
                           "note": None, "values": {
                               role: {"raw_value": candidate["sources"][role]["text"],
                                      "raw_unit": candidate["raw_unit"]}
                               for role in ("current", "previous")}}
                          for cid, candidate in candidates.items()]}


def _verify_pdf(path: str | Path, expected: str) -> None:
    source = Path(path)
    _require(source.is_file(), "Source PDF does not exist")
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        first = stream.read(5)
        _require(first == b"%PDF-", "Source does not have a PDF signature")
        digest.update(first)
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    _require(digest.hexdigest() == expected, "SOURCE_HASH_MISMATCH: source PDF changed after extraction")


def _review_header(review: dict, scan: dict, candidate_ids: set[str]) -> tuple[dict, dict]:
    _require(isinstance(review, dict) and review.get("schema_version") == "0.17", "Expected a v0.17 review form")
    _require(review.get("file_sha256") == scan["document"]["file_sha256"], "Review refers to another PDF")
    _require(review.get("candidate_snapshot_sha256") == _snapshot(scan),
             "STALE_REVIEW: candidate bundle changed after review form creation")
    identity = review.get("identity")
    _require(isinstance(identity, dict) and identity.get("decision") in ("pending", "confirm", "reject"),
             "Identity decision must be pending, confirm, or reject")
    decisions = review.get("decisions")
    _require(isinstance(decisions, list), "decisions must be a list")
    indexed = {}
    for decision in decisions:
        _require(isinstance(decision, dict), "Each review decision must be a dictionary")
        cid = decision.get("candidate_id")
        _require(isinstance(cid, str) and cid in candidate_ids and cid not in indexed,
                 "Review has unknown or duplicate candidate IDs")
        _require(decision.get("decision") in ("pending", "confirm", "correct", "reject"), "Unknown review action")
        indexed[cid] = decision
    record = {key: deepcopy(review.get(key)) for key in
              ("review_id", "reviewer", "reviewed_at", "file_sha256", "candidate_snapshot_sha256")}
    if identity["decision"] != "pending" or any(item["decision"] != "pending" for item in indexed.values()):
        reviewer = review.get("reviewer")
        _require(isinstance(reviewer, dict) and reviewer.get("kind") == "human" and _text(reviewer.get("id")),
                 "Explicit review requires a host-authenticated human reviewer")
        _require(_text(review.get("review_id")) and _text(review.get("reviewed_at")), "Review ID and timestamp are required")
        try:
            when = datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ScanReviewError("reviewed_at must be an ISO-8601 timestamp") from exc
        _require(when.tzinfo is not None and when.utcoffset() is not None, "reviewed_at must include a timezone")
        record["reviewed_at"] = when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return indexed, record


def _cell_source(scan: dict, candidate: dict, role: str, decision: dict, record: dict) -> dict:
    original = deepcopy(candidate["sources"][role])
    label = deepcopy(candidate["sources"]["label"])
    return {"document_id": scan["document"]["document_id"],
            "page_start": original["page"], "page_end": original["page"],
            "row_bbox": original["bbox"], "section": "annual_summary", "extractor": "ocr_human_reviewed",
            "file_sha256": scan["document"]["file_sha256"], "label_source": label,
            "parts": [{"page": original["page"], "row_bbox": original["bbox"], "text": original["text"]}],
            "ocr_original": {**original, "raw_unit": candidate["raw_unit"]},
            "reviewed_disclosure": deepcopy(decision["values"][role]),
            "review": {**deepcopy(record), "candidate_id": candidate["candidate_id"],
                       "decision": decision["decision"], "note": decision.get("note"), "evidence_confirmed": True}}


def prepare_reviewed_scan(scan_result: dict, review: dict, *, source_path: str | Path) -> dict:
    """Validate review and create a partial annual input from confirmed fields.

    Missing decisions remain pending. Confirm/correct covers both amount cells.
    Conflicting confirmed duplicates are withheld as an entire metric row.
    Company/year remapping and parent-company math are deliberately unsupported.
    """
    candidates = _validate_scan(scan_result)
    decisions, record = _review_header(review, scan_result, set(candidates))
    _verify_pdf(source_path, scan_result["document"]["file_sha256"])
    identity = review["identity"]
    claim = scan_result["identity_claim"]
    warnings = deepcopy(scan_result.get("warnings", []))
    warnings.append({"code": "PARTIAL_SCANNED_SUMMARY", "message": "Only explicitly reviewed summary fields are covered"})
    blocking = []
    if identity["decision"] != "confirm":
        blocking.append("IDENTITY_NOT_CONFIRMED")
    elif identity.get("company_name") != claim["company_name"] or type(identity.get("report_year")) is not int or identity["report_year"] != claim["report_year"]:
        blocking.append("REVIEW_IDENTITY_MISMATCH")
    if identity["decision"] == "confirm":
        if identity.get("currency") != "CNY" or identity.get("statement_scope") != "consolidated":
            blocking.append("UNSUPPORTED_REVIEW_SCOPE_OR_CURRENCY")
        if type(identity.get("comparative_restated")) is not bool:
            blocking.append("RESTATEMENT_STATUS_UNCONFIRMED")
    audit = [{"event": "scan_review_checked", **deepcopy(record)}]
    ledger = []
    admitted: list[tuple[dict, dict, dict]] = []
    for cid, candidate in candidates.items():
        decision = decisions.get(cid, {"candidate_id": cid, "decision": "pending"})
        action = decision["decision"]
        entry = {"candidate_id": cid, "metric_code": candidate["metric_code"], "decision": action,
                 "status": action, "original": deepcopy(candidate), "effective_values": None,
                 "note": decision.get("note"), "post_review_checks": {}}
        ledger.append(entry)
        if action == "pending":
            continue
        if action == "reject":
            _require(_text(decision.get("note")), "Rejected candidates require a reason")
            continue
        _require(decision.get("evidence_confirmed") is True, "Confirmed/corrected cells require explicit evidence confirmation")
        values = decision.get("values")
        _require(isinstance(values, dict) and set(values) == {"current", "previous"}, "Review must supply both current and previous cells")
        normalized = {}
        changed = False
        for role in ("current", "previous"):
            cell = values[role]
            _require(isinstance(cell, dict) and isinstance(cell.get("raw_value"), str) and
                     isinstance(cell.get("raw_unit"), str), "Reviewed amounts and units must be strings; float amounts are rejected")
            amount = parse_amount(cell["raw_value"], cell["raw_unit"])
            _require(amount is not None, "Invalid reviewed decimal amount, grouping, or monetary unit")
            normalized[role] = format(amount, "f")
            changed |= cell["raw_value"] != candidate["sources"][role]["text"] or cell["raw_unit"] != candidate["raw_unit"]
        _require(not changed if action == "confirm" else changed, "Use correct for edited cells; confirm must preserve original values and units")
        if action == "correct" or candidate["status"] == "needs_review":
            _require(_text(decision.get("note")), "Corrections and originally unchecked candidates require a review explanation")
        entry["effective_values"] = {role: {**deepcopy(values[role]), "value_cny": normalized[role]}
                                     for role in ("current", "previous")}
        pct = candidate["sources"].get("percentage")
        if pct:
            raw = pct["text"].replace("−", "-").replace("－", "-")
            if re.fullmatch(r"-?\d+(?:\.\d+)?%?", raw):
                good = _percent_checked(Decimal(normalized["current"]), Decimal(normalized["previous"]),
                                       raw if raw.endswith("%") else raw + "%")
                entry["post_review_checks"]["ocr_disclosed_yoy"] = good
                if not good:
                    warnings.append({"code": "REVIEWED_YOY_DISCREPANCY", "candidate_id": cid,
                                     "message": "Reviewed amounts disagree with the OCR percentage or its base is nonpositive; review percentage evidence separately"})
        if blocking:
            entry["status"] = "withheld_identity"
            continue
        year = claim["report_year"]
        periods = {"current": str(year), "previous": str(year - 1)}
        sources = {periods[role]: _cell_source(scan_result, candidate, role, decision, record)
                   for role in periods}
        row = {"kind": "annual", "metric_code": candidate["metric_code"],
               "label": candidate["sources"]["label"]["text"],
               "values": [values["current"]["raw_value"], values["previous"]["raw_value"]],
               "unit": values["current"]["raw_unit"],
               "value_units": {periods[role]: values[role]["raw_unit"] for role in periods},
               "source": sources[str(year)], "value_sources": sources,
               "restated_comparative": identity["comparative_restated"]}
        entry["status"] = "confirmed" if action == "confirm" else "corrected"
        admitted.append((candidate, row, entry))
    grouped: dict[str, list[tuple[dict, dict, dict]]] = {}
    for triple in admitted:
        grouped.setdefault(triple[0]["metric_code"], []).append(triple)
    rows = []
    for code, group in grouped.items():
        pairs = {(Decimal(entry["effective_values"]["current"]["value_cny"]),
                  Decimal(entry["effective_values"]["previous"]["value_cny"])) for _, _, entry in group}
        if len(pairs) > 1:
            for _, _, entry in group:
                entry["status"] = "withheld_conflict"
            warnings.append({"code": "REVIEWED_VALUE_CONFLICT", "metric_code": code,
                             "candidate_ids": [c["candidate_id"] for c, _, _ in group],
                             "message": "Confirmed duplicate values disagree; both periods of this field were withheld"})
        else:
            rows.extend(row for _, row, _ in group)
    warnings.extend({"code": code} for code in blocking)
    for entry in ledger:
        audit.append({"event": "scan_candidate_reviewed", "candidate_id": entry["candidate_id"],
                      "decision": entry["decision"], "status": entry["status"], "review_id": record["review_id"]})
    admitted_codes = {row["metric_code"] for row in rows}
    withheld = {}
    priority = {"REJECTED_INPUT": 1, "UNCONFIRMED_INPUT": 2, "CONFLICTING_INPUT": 3}
    reasons = {"pending": "UNCONFIRMED_INPUT", "reject": "REJECTED_INPUT", "withheld_conflict": "CONFLICTING_INPUT"}
    for entry in ledger:
        if entry["metric_code"] in admitted_codes or entry["status"] not in reasons:
            continue
        reason = reasons[entry["status"]]
        for period in (str(claim["report_year"]), str(claim["report_year"] - 1)):
            key = f"{period}:{entry['metric_code']}"
            item = withheld.setdefault(key, {"reason": reason, "candidate_ids": []})
            if priority[reason] > priority[item["reason"]]:
                item["reason"] = reason
            item["candidate_ids"].append(entry["candidate_id"])
    parsed = None
    if not blocking:
        parsed = {**deepcopy(scan_result["document"]), "company_name": claim["company_name"],
                  "report_year": claim["report_year"], "report_title": f"{claim['report_year']}年年度报告（扫描摘要复核）",
                  "report_type": "annual", "currency": "CNY", "pdf_pages": scan_result["document"]["page_count"],
                  "parser": "ocr_human_review_adapter_v0.17", "sections_found": ["annual_summary"],
                  "coverage": {"full_report": False, "pages_reviewed": deepcopy(scan_result["pages_read"])},
                  "withheld_inputs": withheld, "rows": rows}
    partial = any(entry["status"] in ("pending", "withheld_conflict") for entry in ledger)
    status = "blocked" if blocking else ("no_confirmed_data" if not rows else ("partial" if partial else "ready"))
    return {"schema_version": "0.17", "status": status, "review_record": record,
            "identity": deepcopy(identity), "decisions": ledger, "parsed_document": parsed,
            "warnings": warnings, "audit": audit}


def analyze_reviewed_scan(scan_result: dict, review: dict, *, source_path: str | Path,
                          options: dict | None = None) -> dict:
    """Run existing formulas/rules on the admitted subset; preserve missing inputs."""
    prepared = prepare_reviewed_scan(scan_result, review, source_path=source_path)
    document = prepared["parsed_document"]
    analysis = analyze_parsed_documents([document] if document else [], options)
    analysis["warnings"].extend(deepcopy(prepared["warnings"]))
    analysis["audit"].extend(deepcopy(prepared["audit"]))
    for fact in analysis["facts"]:
        fact["verification_status"] = "human_reviewed"
    return {"schema_version": "0.17", "status": prepared["status"], "review": prepared, "analysis": analysis}
