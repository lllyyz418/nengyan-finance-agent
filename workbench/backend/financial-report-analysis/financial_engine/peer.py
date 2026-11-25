"""Evidence-linked, same-year comparisons for a user-curated peer group."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from pathlib import Path

from .disclosure import (DisclosureError, discover_annual_reports, download_report,
                         latest_reports_by_year, resolve_security)
from .pdf_adapter import analyze_reports


DEFAULT_PEER_METRICS = (
    "revenue_yoy",
    "gross_margin",
    "consolidated_net_margin",
    "adjusted_attributable_net_margin",
    "cash_to_consolidated_profit",
    "inventory_days",
    "receivables_days_approx",
    "return_on_assets",
    "return_on_attributable_equity",
    "fixed_assets_cip_to_assets",
    "r_and_d_expense_to_revenue",
    "inventory_impairment_to_gross",
    "receivables_over_1y_to_gross",
)


def _validate_group(target_code: str, peer_codes: list[str], peer_group: str,
                    selection_note: str) -> None:
    if not isinstance(target_code, str) or not re.fullmatch(r"\d{6}", target_code):
        raise ValueError("target_code must be a six-digit security code")
    if not isinstance(peer_codes, list) or not 1 <= len(peer_codes) <= 5:
        raise ValueError("peer_codes must contain one to five codes")
    if any(not isinstance(code, str) or not re.fullmatch(r"\d{6}", code) for code in peer_codes):
        raise ValueError("every peer code must have six digits")
    if len(set(peer_codes)) != len(peer_codes) or target_code in peer_codes:
        raise ValueError("peer codes must be unique and exclude the target")
    if not isinstance(peer_group, str) or not peer_group.strip():
        raise ValueError("peer_group must describe the business segment")
    if not isinstance(selection_note, str) or not selection_note.strip():
        raise ValueError("selection_note must explain why these peers were chosen")


def _annual_document(analysis: dict, year: int) -> tuple[dict | None, str | None]:
    documents = [item for item in analysis.get("documents", [])
                 if item.get("report_type") == "annual" and item.get("report_year") == year]
    if len(documents) != 1:
        return None, "MISSING_OR_DUPLICATE_ANNUAL_REPORT"
    if documents[0].get("currency") != "CNY":
        return None, "UNSUPPORTED_CURRENCY"
    return documents[0], None


def _metric_point(analysis: dict | None, year: int, code: str) -> dict:
    if analysis is None:
        return {"status": "unavailable", "reason_code": "REPORT_NOT_AVAILABLE"}
    document, reason = _annual_document(analysis, year)
    if document is None:
        return {"status": "unavailable", "reason_code": reason}
    doc_id = document["document_id"]
    metrics = [item for item in analysis.get("metrics", [])
               if item.get("document_id") == doc_id and item.get("period") == str(year)
               and item.get("metric_code") == code and item.get("status") == "calculated"]
    if len(metrics) != 1:
        unavailable = next((item for item in analysis.get("unavailable_metrics", [])
                            if item.get("document_id") == doc_id and item.get("period") == str(year)
                            and item.get("metric_code") == code), None)
        return {"status": "unavailable", "company_name": document.get("company_name"),
                "document_id": doc_id,
                "reason_code": unavailable.get("reason_code") if unavailable else "MISSING_METRIC"}
    metric = metrics[0]
    fact_index = {item.get("fact_id"): item for item in analysis.get("facts", [])}
    inputs = metric.get("input_fact_ids") or []
    valid_document_ids={doc_id}
    if analysis.get('period_basis') in ('single','cumulative'):
        valid_document_ids.update(doc['document_id'] for doc in analysis.get('support_documents',[])
                                  if doc.get('security_code') and doc.get('security_code')==document.get('security_code')
                                  and doc.get('currency')==document.get('currency'))
    if not inputs or any(
        fact_index.get(fact_id, {}).get("document_id") not in valid_document_ids
        or fact_index.get(fact_id, {}).get("quality") == "conflict"
        or fact_index.get(fact_id, {}).get("statement_scope") != "consolidated"
        for fact_id in inputs
    ):
        return {"status": "unavailable", "company_name": document.get("company_name"),
                "document_id": doc_id, "reason_code": "UNVERIFIED_METRIC_INPUTS"}
    if isinstance(metric.get("value"), (float, bool)):
        return {"status": "unavailable", "company_name": document.get("company_name"),
                "document_id": doc_id, "reason_code": "FLOAT_VALUE_NOT_ALLOWED"}
    try:
        value = Decimal(str(metric["value"]))
    except (KeyError, InvalidOperation, ValueError):
        return {"status": "unavailable", "company_name": document.get("company_name"),
                "document_id": doc_id, "reason_code": "INVALID_METRIC_VALUE"}
    if not value.is_finite() or not isinstance(metric.get("unit"), str) or not metric.get("formula"):
        return {"status": "unavailable", "company_name": document.get("company_name"),
                "document_id": doc_id, "reason_code": "INVALID_METRIC_VALUE"}
    return {"status": "available", "company_name": document.get("company_name"),
            "document_id": doc_id, "metric_id": metric["metric_id"],
            "value": format(value, "f"), "unit": metric["unit"],
            "formula": metric["formula"], "input_fact_ids": list(inputs)}


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[midpoint]
    with localcontext() as context:
        context.prec = max(50, sum(len(item.as_tuple().digits) for item in ordered) + 16)
        return (ordered[midpoint - 1] + ordered[midpoint]) / Decimal("2")


def compare_peer_group(analyses_by_code: dict[str, dict], *, target_code: str,
                       peer_codes: list[str], report_year: int, peer_group: str,
                       selection_note: str,
                       metric_codes: list[str] | None = None) -> dict:
    """Compare sourced indicators from annual reports without network access.

    Peer membership is supplied by the caller. A median needs at least two
    valid peer reports; one peer is shown only as a side-by-side value.
    """
    _validate_group(target_code, peer_codes, peer_group, selection_note)
    if not isinstance(report_year, int) or isinstance(report_year, bool) or not 1990 <= report_year <= 2100:
        raise ValueError("report_year must be a fiscal year")
    codes = list(DEFAULT_PEER_METRICS) if metric_codes is None else metric_codes
    if not isinstance(codes, list) or not codes or any(not isinstance(code, str) or not code for code in codes):
        raise ValueError("metric_codes must be a nonempty list of metric names")
    if len(set(codes)) != len(codes):
        raise ValueError("metric_codes must be unique")
    comparisons: list[dict] = []
    target_analysis = analyses_by_code.get(target_code)
    for metric_code in codes:
        target = _metric_point(target_analysis, report_year, metric_code)
        peer_points = []
        for code in peer_codes:
            peer_points.append({"stock_code": code,
                                **_metric_point(analyses_by_code.get(code), report_year, metric_code)})
        available = [point for point in [target, *peer_points] if point["status"] == "available"]
        reference = target if target["status"] == "available" else (available[0] if available else None)
        if reference is not None:
            for point in [target, *peer_points]:
                if point["status"] == "available" and (point["unit"] != reference["unit"]
                                                        or point["formula"] != reference["formula"]):
                    point["status"] = "unavailable"
                    point["reason_code"] = "INCOMPARABLE_FORMULA_OR_UNIT"
                    point.pop("value", None)
        valid_peers = [point for point in peer_points if point["status"] == "available"]
        item = {"metric_code": metric_code, "report_year": report_year,
                "unit": reference["unit"] if reference else None,
                "formula": reference["formula"] if reference else None,
                "target": {"stock_code": target_code, **target},
                "peers": peer_points,
                "valid_peer_count": len(valid_peers), "configured_peer_count": len(peer_codes),
                "peer_median": {"status": "unavailable", "value": None,
                                "reason_code": "INSUFFICIENT_PEER_COVERAGE"},
                "target_minus_peer_median": None,
                "difference_unit": None}
        if len(valid_peers) >= 2:
            median = _median([Decimal(point["value"]) for point in valid_peers])
            item["peer_median"] = {"status": "calculated", "value": format(median, "f"),
                                   "unit": reference["unit"],
                                   "input_metric_ids": [point["metric_id"] for point in valid_peers]}
            if target["status"] == "available":
                with localcontext() as context:
                    context.prec = max(50, len(Decimal(target["value"]).as_tuple().digits)
                                       + len(median.as_tuple().digits) + 16)
                    context.rounding = ROUND_HALF_UP
                    difference = Decimal(target["value"]) - median
                item["target_minus_peer_median"] = format(difference, "f")
                item["difference_unit"] = ("percentage_points" if reference["unit"] == "percent"
                                           else reference["unit"])
        comparisons.append(item)
    return {"schema_version": "0.1", "selection_basis": "user_configured",
            "peer_group": peer_group.strip(), "selection_note": selection_note.strip(),
            "target_code": target_code, "peer_codes": list(peer_codes),
            "report_year": report_year, "metric_comparisons": comparisons,
            "limitations": [
                "Consolidated-company ratios may contain different product mixes and accounting policies.",
                "A configured peer group is not a statistical industry sample or investment ranking.",
            ]}


def analyze_peer_group(target_pdf: str | Path, *, peer_codes: list[str], peer_group: str,
                       selection_note: str, cache_dir: str | Path,
                       target_code: str | None = None,
                       metric_codes: list[str] | None = None,
                       options: dict | None = None) -> dict:
    """Fetch same-year peer reports and return their analyses and comparison."""
    if cache_dir is None:
        raise ValueError("cache_dir must be provided")
    target_result = analyze_reports([target_pdf], options)
    documents = target_result.get("documents", [])
    output: dict = {"schema_version": "0.1", "status": "partial", "reports": {},
                    "retrieval": [], "comparison": None, "warnings": [],
                    "report_warnings": {}}
    if len(documents) != 1 or documents[0].get("report_type") != "annual":
        output["status"] = "failed"
        output["warnings"].append({"code": "TARGET_REPORT_NOT_PARSED",
                                   "message": "A single text-layer annual report is required"})
        output["reports"]["target"] = target_result
        output["report_warnings"]["target"] = target_result.get("warnings", [])
        return output
    year = int(documents[0]["report_year"])
    try:
        identified = resolve_security(documents[0].get("company_name") or "")
        if target_code is not None and target_code != identified["stock_code"]:
            raise DisclosureError("Target code does not match the uploaded report company name")
        target_code = identified["stock_code"]
    except (DisclosureError, ValueError) as exc:
        output["warnings"].append({"code": "TARGET_IDENTITY_UNVERIFIED", "message": str(exc)})
        output["reports"]["target"] = target_result
        output["report_warnings"]["target"] = target_result.get("warnings", [])
        return output
    _validate_group(target_code, peer_codes, peer_group, selection_note)
    documents[0]["security_code"] = target_code
    output["reports"][target_code] = target_result
    output["report_warnings"][target_code] = target_result.get("warnings", [])
    output["retrieval"].append({"stock_code": target_code, "status": "user_supplied",
                                "local_path": str(Path(target_pdf).resolve()),
                                "sha256": documents[0].get("file_sha256")})
    for code in peer_codes:
        try:
            security = resolve_security(code)
            candidates = discover_annual_reports(security, [year])
            selected, alternatives = latest_reports_by_year(candidates, [year])
            record = selected.get(year)
            if record is None:
                raise DisclosureError(f"No full annual report found for {code} in {year}")
            file_info = download_report(record, cache_dir)
            analysis = analyze_reports([file_info["local_path"]], options)
            document, reason = _annual_document(analysis, year)
            if document is None:
                raise DisclosureError(f"Peer report failed validation: {reason}")
            pdf_identity = resolve_security(document.get("company_name") or "")
            if pdf_identity["stock_code"] != code:
                raise DisclosureError("Peer PDF company name does not match the requested security code")
            document["security_code"] = code
            document["disclosure"] = {key: record[key] for key in
                                       ("announcement_id", "title", "published_at", "source_url",
                                        "catalogue_url", "version_label")}
            output["reports"][code] = analysis
            output["report_warnings"][code] = analysis.get("warnings", [])
            output["retrieval"].append({"stock_code": code, "status": "analyzed", **file_info,
                                        "alternative_versions": alternatives})
        except (DisclosureError, OSError, ValueError, RuntimeError) as exc:
            warning = {"code": "PEER_REPORT_FAILED", "stock_code": code, "message": str(exc)}
            output["warnings"].append(warning)
            output["retrieval"].append({"stock_code": code, "status": "failed", "message": str(exc)})
    output["comparison"] = compare_peer_group(
        output["reports"], target_code=target_code, peer_codes=peer_codes,
        report_year=year, peer_group=peer_group, selection_note=selection_note,
        metric_codes=metric_codes)
    output["status"] = "complete" if not output["warnings"] else "partial"
    return output
