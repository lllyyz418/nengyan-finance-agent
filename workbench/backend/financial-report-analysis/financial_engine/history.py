"""Sourced annual-report history built on the existing deterministic engine."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, localcontext
from pathlib import Path

from .core import analyze_parsed_documents
from .disclosure import (DisclosureError, discover_annual_reports, download_report,
                         latest_reports_by_year, resolve_security)
from .pdf_adapter import parse_pdf
from .basis_check import compare_facts, SAFE_STATUSES, VERSION as BASIS_VERSION


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def build_annual_history(analysis: dict, stock_code: str | None = None) -> dict:
    """Compare current-period metrics from distinct annual reports.

    A report's comparative column is used only to check whether the earlier
    report's input facts were later restated. It never silently replaces the
    earlier report's own current-period facts.
    """
    documents = [item for item in analysis.get("documents", [])
                 if item.get("report_type") == "annual"
                 and (stock_code is None or item.get("security_code") == stock_code)]
    warnings: list[dict] = []
    by_year: dict[int, list[dict]] = {}
    for document in documents:
        try:
            year = int(document["report_year"])
        except (KeyError, TypeError, ValueError):
            warnings.append({"code": "INVALID_REPORT_YEAR", "document_id": document.get("document_id")})
            continue
        by_year.setdefault(year, []).append(document)
    selected: dict[int, dict] = {}
    for year, versions in sorted(by_year.items()):
        if len(versions) != 1:
            warnings.append({"code": "MULTIPLE_REPORTS_FOR_YEAR", "year": year,
                             "document_ids": [item["document_id"] for item in versions],
                             "message": "Select one annual-report version before comparing this year"})
            continue
        selected[year] = versions[0]
    if stock_code is None:
        names = {item.get("company_name") for item in selected.values() if item.get("company_name")}
        if len(names) > 1:
            warnings.append({"code": "COMPANY_IDENTITY_UNVERIFIED",
                             "message": "Multiple company names require an explicit security code"})
            selected.clear()

    facts = {(item.get("document_id"), item.get("period"), item.get("metric_code")): item
             for item in analysis.get("facts", [])}
    facts_by_id = {item.get("fact_id"): item for item in analysis.get("facts", [])}
    metrics: dict[tuple[str, int, str], dict] = {}
    for metric in analysis.get("metrics", []):
        if metric.get("status") != "calculated":
            continue
        try:
            period_year = int(metric.get("period", ""))
        except (TypeError, ValueError):
            continue  # Exclude quarterly metrics from annual comparisons.
        document = selected.get(period_year)
        if document is None or metric.get("document_id") != document["document_id"]:
            continue
        metrics[(document["document_id"], period_year, metric["metric_code"])] = metric

    fact_series: dict[str, list[dict]] = {}
    for (doc_id, period, code), fact in sorted(facts.items(), key=lambda pair: (pair[0][2], pair[0][1])):
        try:
            period_year = int(period)
        except (TypeError, ValueError):
            continue
        document = selected.get(period_year)
        if document is None or doc_id != document["document_id"]:
            continue
        fact_series.setdefault(code, []).append({
            "year": period_year,
            "value": None if fact.get("quality") == "conflict" else fact["value"],
            "unit": fact["unit"],
            "quality": fact.get("quality"),
            "fact_id": fact["fact_id"],
            "document_id": doc_id,
        })

    series: dict[str, list[dict]] = {}
    for (_, year, code), item in sorted(metrics.items(), key=lambda pair: (pair[0][2], pair[0][1])):
        series.setdefault(code, []).append({
            "year": year,
            "value": item["value"],
            "unit": item["unit"],
            "metric_id": item["metric_id"],
            "document_id": item["document_id"],
            "input_fact_ids": list(item.get("input_fact_ids", [])),
        })

    changes: list[dict] = []
    restatement_checks: list[dict] = []
    years = sorted(selected)
    for earlier_year, later_year in zip(years, years[1:]):
        older_doc, newer_doc = selected[earlier_year], selected[later_year]
        for code in sorted(fact_series):
            older_fact = facts.get((older_doc["document_id"], str(earlier_year), code))
            if older_fact is None:
                continue
            newer_comparative = facts.get((newer_doc["document_id"], str(earlier_year), code))
            restatement_checks.append({
                "year": earlier_year, "later_report_year": later_year, "metric_code": code,
                **compare_facts(older_fact,newer_comparative),
                "original_fact_id": older_fact["fact_id"],
                "later_comparative_fact_id": newer_comparative["fact_id"] if newer_comparative else None,
                "original_document_id":older_doc['document_id'],"later_document_id":newer_doc['document_id'],
                "affected_metrics":[{'metric_code':m['metric_code'],'period':m.get('period'),
                    'metric_id':m.get('metric_id'),'document_id':m.get('document_id')}
                    for m in analysis.get('metrics',[]) if set(m.get('input_fact_ids') or []) &
                    {older_fact['fact_id'],(newer_comparative or {}).get('fact_id')}],
                "affected_rules":sorted({r['rule_id'] for r in analysis.get('risk_assessments',[])
                    if set(r.get('evidence_fact_ids') or []) & {older_fact['fact_id'],(newer_comparative or {}).get('fact_id')}}),
            })
        pair_checks={c['metric_code']:c for c in restatement_checks if c['year']==earlier_year}
        for code in sorted(series):
            older = metrics.get((older_doc["document_id"], earlier_year, code))
            newer = metrics.get((newer_doc["document_id"], later_year, code))
            change = {
                "metric_code": code,
                "from_year": earlier_year,
                "to_year": later_year,
                "from_metric_id": older["metric_id"] if older else None,
                "to_metric_id": newer["metric_id"] if newer else None,
                "status": "unavailable",
            }
            if later_year != earlier_year + 1:
                change["reason_code"] = "NONADJACENT_YEARS"
            elif not older or not newer:
                change["reason_code"] = "MISSING_METRIC"
            elif older["unit"] != newer["unit"]:
                change["reason_code"] = "UNIT_MISMATCH"
            else:
                conflicts: list[dict] = []
                required_codes: set[str] = set()
                checked_codes: set[str] = set()
                for fact_id in older.get("input_fact_ids", []):
                    earlier_fact = facts_by_id.get(fact_id)
                    if not earlier_fact or earlier_fact.get("period") != str(earlier_year):
                        continue
                    required_codes.add(earlier_fact["metric_code"])
                    key = (newer_doc["document_id"], str(earlier_year), earlier_fact["metric_code"])
                    later_comparative = facts.get(key)
                    check=pair_checks.get(earlier_fact['metric_code'],{})
                    if check.get('status')=='unavailable' or not later_comparative:
                        continue
                    checked_codes.add(earlier_fact["metric_code"])
                    if check.get('status') not in SAFE_STATUSES:
                        conflicts.append({"metric_code": earlier_fact["metric_code"],
                                          "earlier_fact_id": earlier_fact["fact_id"],
                                          "later_comparative_fact_id": later_comparative["fact_id"]})
                if conflicts:
                    change["reason_code"] = "RESTATED_COMPARATIVE_CONFLICT"
                    change["conflicting_facts"] = conflicts
                else:
                    previous, current = Decimal(older["value"]), Decimal(newer["value"])
                    with localcontext() as context:
                        context.prec = max(50, len(previous.as_tuple().digits) + len(current.as_tuple().digits) + 16)
                        context.rounding = ROUND_HALF_UP
                        difference = current - previous
                        change.update({
                            "status": "calculated",
                            "unit": "percentage_points" if older["unit"] == "percent" else older["unit"],
                            "absolute_change": _decimal_text(difference),
                            "basis_check": (('precision_compatible_comparatives' if any(pair_checks[c]['status']=='rounding_compatible' for c in checked_codes) else 'matched_comparatives') if required_codes and checked_codes == required_codes
                                            else "partial_comparatives" if checked_codes
                                            else "comparatives_unavailable"),
                            "basis_checked_codes": sorted(checked_codes),
                            "basis_unchecked_codes": sorted(required_codes - checked_codes),
                            "precision_compatible_codes":sorted(c for c in checked_codes if pair_checks[c]['status']=='rounding_compatible'),
                        })
                        if older["unit"] != "percent" and previous > 0:
                            change["relative_change_percent"] = _decimal_text(
                                difference / previous * Decimal("100"))
                        elif older["unit"] != "percent":
                            change["relative_change_reason"] = "NONPOSITIVE_BASE"
            changes.append(change)

    for check in restatement_checks:
        check['withheld_changes']=[{'metric_code':change['metric_code'],'from_year':change['from_year'],'to_year':change['to_year']}
            for change in changes if change.get('status')=='unavailable' and any(
                fact.get('earlier_fact_id')==check['original_fact_id'] for fact in change.get('conflicting_facts') or [])]

    return {
        "schema_version": "0.1",
        "stock_code": stock_code,
        "report_years": years,
        "document_ids": [selected[year]["document_id"] for year in years],
        "fact_series": fact_series,
        "series": series,
        "changes": changes,
        "restatement_checks": restatement_checks,
        "basis_check_version": BASIS_VERSION,
        "warnings": warnings,
    }


def analyze_company_history(input_pdf: str | Path, *, stock_code: str | None = None,
                            lookback_years: int = 2, cache_dir: str | Path | None = None,
                            fetch_online: bool = True,
                            extra_report_paths: list[str | Path] | None = None,
                            options: dict | None = None) -> dict:
    """Analyze one uploaded annual report and its prior official annual reports.

    `cache_dir` is required for online retrieval and should be placed on the
    desired drive by the host application. Local prior reports work offline.
    Network failure yields a partial analysis with explicit warnings.
    """
    if not isinstance(lookback_years, int) or isinstance(lookback_years, bool) or not 0 <= lookback_years <= 5:
        raise ValueError("lookback_years must be an integer from 0 through 5")
    if fetch_online and cache_dir is None:
        raise ValueError("cache_dir is required when fetch_online=True")
    warnings: list[dict] = []
    retrieval: dict = {"status": "offline" if not fetch_online else "partial",
                       "requested_years": [], "files": [], "alternatives": [], "warnings": []}
    try:
        uploaded = parse_pdf(input_pdf)
    except (OSError, ValueError, RuntimeError) as exc:
        result = analyze_parsed_documents([], options)
        result["warnings"].append({"code": "PDF_PARSE_FAILED", "file_name": Path(input_pdf).name,
                                   "message": str(exc)})
        result["history"] = build_annual_history(result, stock_code)
        retrieval["status"] = "failed"
        result["retrieval"] = retrieval
        return result
    year = int(uploaded["report_year"])
    wanted = list(range(year - lookback_years, year))
    retrieval["requested_years"] = wanted
    parsed = [uploaded]
    seen_hashes = {uploaded["file_sha256"]}
    occupied_years = {year}
    retrieval["files"].append({"role": "input", "report_year": year,
                                "local_path": str(Path(input_pdf).resolve()),
                                "sha256": uploaded["file_sha256"], "source_status": "user_supplied"})
    security = None
    if fetch_online:
        try:
            company_name = uploaded.get("company_name")
            if not company_name:
                raise DisclosureError("Uploaded report company name could not be verified")
            security = resolve_security(company_name)
            if stock_code is not None and security["stock_code"] != stock_code:
                raise DisclosureError("Requested security code does not match the uploaded report company name")
            stock_code = security["stock_code"]
            retrieval["identity_status"] = "company_name_matched_catalogue"
            records = discover_annual_reports(security, wanted) if wanted else []
            selected, alternatives = latest_reports_by_year(records, wanted)
            retrieval["alternatives"] = alternatives
            for prior_year in wanted:
                record = selected.get(prior_year)
                if record is None:
                    warnings.append({"code": "REPORT_NOT_FOUND", "report_year": prior_year,
                                     "message": "No full annual report was found in the public catalogue"})
                    continue
                try:
                    file_info = download_report(record, cache_dir)
                    prior = parse_pdf(file_info["local_path"])
                    if prior["report_year"] != prior_year:
                        raise ValueError("PDF cover year differs from catalogue report year")
                    prior["security_code"] = stock_code
                    prior["disclosure"] = {key: record[key] for key in
                                           ("announcement_id", "title", "published_at", "source_url",
                                            "catalogue_url", "version_label")}
                    if prior["file_sha256"] not in seen_hashes and prior_year not in occupied_years:
                        parsed.append(prior)
                        seen_hashes.add(prior["file_sha256"])
                        occupied_years.add(prior_year)
                    retrieval["files"].append({"role": "history", **file_info})
                except (DisclosureError, OSError, ValueError, RuntimeError) as exc:
                    warnings.append({"code": "HISTORY_REPORT_FAILED", "report_year": prior_year,
                                     "message": str(exc)})
        except (DisclosureError, OSError, ValueError) as exc:
            warnings.append({"code": "DISCLOSURE_DISCOVERY_FAILED", "message": str(exc)})
            if retrieval.get("identity_status") != "company_name_matched_catalogue":
                stock_code = None
                retrieval["identity_status"] = "unverified"
    if stock_code:
        uploaded["security_code"] = stock_code
    for path in extra_report_paths or []:
        try:
            prior = parse_pdf(path)
            prior_year = int(prior["report_year"])
            if prior["file_sha256"] in seen_hashes:
                continue
            if prior_year in occupied_years:
                warnings.append({"code": "MULTIPLE_REPORTS_FOR_YEAR", "report_year": prior_year,
                                 "file_name": Path(path).name})
                continue
            prior["security_code"] = stock_code
            parsed.append(prior)
            seen_hashes.add(prior["file_sha256"])
            occupied_years.add(prior_year)
            retrieval["files"].append({"role": "local_history", "report_year": prior_year,
                                        "local_path": str(Path(path).resolve()),
                                        "sha256": prior["file_sha256"], "source_status": "user_supplied"})
        except (OSError, ValueError, RuntimeError) as exc:
            warnings.append({"code": "LOCAL_HISTORY_PARSE_FAILED", "file_name": Path(path).name,
                             "message": str(exc)})
    result = analyze_parsed_documents(parsed, options)
    for document in parsed:
        result["warnings"].extend(document.get("parser_warnings", []))
        result["audit"].append({"event": "pdf_parsed", "document_id": document["document_id"],
                                "file_sha256": document["file_sha256"], "parser": document["parser"],
                                "extracted_rows": len(document["rows"])})
    result["warnings"].extend(warnings)
    retrieval["warnings"] = warnings
    if fetch_online:
        retrieval["status"] = "complete" if all(prior_year in occupied_years for prior_year in wanted) else "partial"
    result["retrieval"] = retrieval
    result["history"] = build_annual_history(result, stock_code)
    return result
