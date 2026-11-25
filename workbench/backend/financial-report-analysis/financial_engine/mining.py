"""Public-disclosure acquisition and annual comparison without model calls."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .core import analyze_parsed_documents
from .disclosure import (DisclosureError, discover_annual_reports, download_report,
                         latest_reports_by_year, resolve_security)
from .document_reader import classify_disclosure, read_pdf_document
from .history import build_annual_history
from .pdf_adapter import parse_pdf
from .peer import _validate_group, compare_peer_group
from .context_risk import evaluate_context_risks
from .semiannual import analyze_semiannual_reports
from .security_identity import identify_security


class MiningError(RuntimeError):
    """A document cannot enter the requested comparison."""


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _pdf_security_identity(path: Path) -> dict:
    """Read issuer fields, excluding glossary mentions of other companies."""
    from .parse_runtime import RUNNER
    runner = RUNNER.get()
    if runner:
        return runner('identity', path)
    import pdfplumber
    pages = []
    with pdfplumber.open(path) as pdf:
        for number, page in enumerate(pdf.pages[:12], 1):
            pages.append((number, page.extract_text() or ''))
            page.close()
    return identify_security(pages)


def _cached_records(directory: Path | None, warnings: list[dict], report_type='annual', report_quarter=None) -> list[dict]:
    if directory is None or not directory.is_dir():
        return []
    records = []
    for path in sorted(directory.glob("*.json")):
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                raise ValueError("Manifest exceeds size limit")
            item = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(item, dict) or item.get("report_type") != report_type or report_type=='quarterly' and item.get('report_quarter')!=report_quarter:
                continue
            if not re.fullmatch(r"\d{6}", str(item.get("stock_code", ""))):
                raise ValueError("Invalid stock code")
            year = item.get("report_year")
            if isinstance(year, bool) or not isinstance(year, int) or not 1990 <= year <= 2100:
                raise ValueError("Invalid report year")
            if not re.fullmatch(r"\d+", str(item.get("announcement_id", ""))):
                raise ValueError("Invalid announcement id")
            if not re.fullmatch(r"[a-f0-9]{64}", str(item.get("sha256", ""))):
                raise ValueError("Missing SHA-256")
            url = urlsplit(item.get("source_url", ""))
            if (url.scheme != "https" or url.netloc != "static.cninfo.com.cn"
                    or not re.fullmatch(r"/finalpage/[A-Za-z0-9/_-]+\.PDF", url.path, re.I)
                    or url.query or url.fragment):
                raise ValueError("Unsupported disclosure source")
            title = re.sub(r"\s+", "", str(item.get("title", "")))
            from .disclosure import periodic_title_year
            if ((not re.search(fr"(?<!\d){year}年?年度报告", title) if report_type=='annual' else periodic_title_year(title,{year},report_type,report_quarter) is None)
                    or any(term in title for term in ("摘要", "英文", "取消", "提示性", "关于"))):
                raise ValueError("Manifest does not identify a full annual report")
            timestamp = datetime.fromisoformat(item["published_at"])
            if timestamp.tzinfo is None:
                raise ValueError("Disclosure time requires a timezone")
            # Ignore stored local_path so relocated caches cannot redirect reads.
            pdf = path.with_suffix(".pdf").resolve()
            if pdf.parent != directory or not pdf.is_file():
                raise ValueError("Matching PDF missing or outside cache directory")
            records.append({**item, "published_at": timestamp.astimezone(timezone.utc).isoformat(),
                            "local_path": str(pdf), "manifest_path": str(path)})
        except (OSError, UnicodeError, ValueError, TypeError, KeyError) as exc:
            warnings.append({"code": "CACHE_MANIFEST_INVALID", "file_name": path.name,
                             "message": str(exc)})
    return records


def _verify_cache(record: dict) -> dict:
    path = Path(record["local_path"])
    with path.open("rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise MiningError("Cached content has no PDF signature")
    if _hash_file(path) != record["sha256"]:
        raise MiningError("Cached PDF SHA-256 differs from catalogue manifest")
    return {**record, "cache_status": "verified_cache"}


def _analysis(parsed: list[dict], options: dict | None) -> dict:
    result = analyze_parsed_documents(parsed, options)
    for document in parsed:
        result["warnings"].extend(document.get("parser_warnings", []))
        result["audit"].append({"event": "pdf_parsed", "document_id": document["document_id"],
                                "file_sha256": document["file_sha256"], "parser": document.get("parser"),
                                "extracted_rows": len(document["rows"])})
    return result


def analyze_disclosure_context(
    input_pdf: str | Path | None = None, *, stock_code: str | None = None,
    report_year: int | None = None, lookback_years: int = 2,
    peer_codes: list[str] | None = None, peer_group: str = "", selection_note: str = "",
    cache_dir: str | Path | None = None, fetch_online: bool = False,
    local_history_paths: list[str | Path] | None = None,
    metric_codes: list[str] | None = None, options: dict | None = None,
    local_report_provider=None,
    checkpoint=None,
    progress_callback=None,
) -> dict:
    """Bundle current, history and peers using one parsed copy per annual PDF.

    Supply a PDF or both code and fiscal year. Online lookup is explicit and
    requires cache_dir. H1 uploads retain current analysis but withhold annual
    cross-report comparisons. Complete refers to requested file coverage.
    """
    if local_report_provider is not None and not callable(local_report_provider):
        raise ValueError('local_report_provider must be callable')
    if checkpoint is not None and not callable(checkpoint):
        raise ValueError('checkpoint must be callable')
    if progress_callback is not None and not callable(progress_callback):
        raise ValueError('progress_callback must be callable')
    if input_pdf is not None and (not isinstance(input_pdf, (str, Path)) or not str(input_pdf).strip()):
        raise ValueError("input_pdf must be a nonempty path")
    if options is not None and not isinstance(options, dict):
        raise ValueError("options must be a dictionary")
    if stock_code is not None and (not isinstance(stock_code, str) or not re.fullmatch(r"\d{6}", stock_code)):
        raise ValueError("stock_code must be a six-digit string")
    if report_year is not None and (isinstance(report_year, bool) or not isinstance(report_year, int)
                                    or not 1990 <= report_year <= 2100):
        raise ValueError("report_year must be an integer fiscal year")
    if isinstance(lookback_years, bool) or not isinstance(lookback_years, int) or not 0 <= lookback_years <= 5:
        raise ValueError("lookback_years must be an integer from 0 through 5")
    if not isinstance(fetch_online, bool):
        raise ValueError("fetch_online must be a boolean")
    if input_pdf is None and (stock_code is None or report_year is None):
        raise ValueError("Supply input_pdf, or both stock_code and report_year")
    if fetch_online and cache_dir is None:
        raise ValueError("cache_dir is required for online retrieval")
    if local_history_paths is not None and (not isinstance(local_history_paths, list) or
            any(not isinstance(path, (str, Path)) or not str(path).strip() for path in local_history_paths)):
        raise ValueError("local_history_paths must be a list")
    peers = [] if peer_codes is None else peer_codes
    if not isinstance(peers, list):
        raise ValueError("peer_codes must be a list")
    if peers:
        _validate_group(stock_code or "000000", peers, peer_group, selection_note)
    if metric_codes is not None and (not isinstance(metric_codes, list) or not metric_codes
                                    or any(not isinstance(code, str) or not code for code in metric_codes)
                                    or len(set(metric_codes)) != len(metric_codes)):
        raise ValueError("metric_codes must be a nonempty list of unique names")
    directory = Path(cache_dir).resolve() if cache_dir is not None else None
    warnings: list[dict] = []
    output = {"schema_version": "0.15", "status": "failed",
              "request": {"input_pdf": str(Path(input_pdf).resolve()) if input_pdf is not None else None,
                          "stock_code": stock_code, "report_year": report_year,
                          "lookback_years": lookback_years, "peer_codes": list(peers),
                          "fetch_online": fetch_online},
              "identity": {"stock_code": None, "status": "unverified", "sources": []},
              "retrieval": {"mode": "local_library_first" if local_report_provider else "online_with_cache" if fetch_online else "offline",
                            "files": [], "alternatives": [], "warnings": [],
                            "catalogue_page_limit_per_security": 5, "catalogue_completeness_guaranteed": False},
              "current": None,
              "history": {"status": "not_requested" if not lookback_years else "unavailable",
                          "analysis": None, "comparison": None},
              "peers": {"status": "not_requested" if not peers else "unavailable",
                        "reports": {}, "comparison": None},
              "coverage": {"current_available": False, "annual_comparisons_supported": False,
                           "limitations": ["Selected rows and notes only; no complete-report coverage.",
                                           "Peer membership is caller configured, not automatically validated.",
                                           "Reported codes and catalogue provenance do not prove upload authenticity."]},
              "warnings": warnings, "audit": []}
    output["risk_review"] = evaluate_context_risks(output)
    cache_records = _cached_records(directory, output["retrieval"]["warnings"])
    parsed_by_path: dict[Path, dict] = {}
    identity_by_path: dict[Path, dict] = {}
    catalogues: dict[str, list[dict]] = {}
    securities: dict[str, dict] = {}
    fallback_codes: set[str] = set()

    def progress(stage):
        if checkpoint:checkpoint()
        if progress_callback:progress_callback(stage)

    def load(path: str | Path, label='上传报告') -> tuple[dict, dict]:
        if checkpoint:
            checkpoint()
        resolved = Path(path).resolve(strict=True)
        if resolved not in parsed_by_path:
            progress(f'正在解析 PDF：{label}（首次解析可能需要数分钟）')
            document = parse_pdf(resolved)
            if checkpoint:
                checkpoint()
            progress(f'核验 PDF 公司身份：{label}')
            identity = _pdf_security_identity(resolved)
            parsed_by_path[resolved] = document
            identity_by_path[resolved] = identity
        return parsed_by_path[resolved], identity_by_path[resolved]

    def acquire(code: str, year: int, role: str, years: list[int]) -> tuple[dict, dict]:
        if checkpoint:
            checkpoint()
        label=f'{code} · {year} 年'
        progress(f'查找本地年报：{label}')
        local_record = local_report_provider(code, year) if local_report_provider else None
        if local_record is not None:
            record = local_record
            progress(f'校验本地年报文件：{label}')
            file_info = _verify_cache(record)
            file_info['cache_status'] = 'verified_local_library'
        else:
            record, file_info = acquire_remote_or_cache(code, year, role, years)
        parsed, identity = load(file_info["local_path"],label)
        if parsed.get("report_type") != "annual" or parsed.get("report_year") != year:
            raise MiningError("PDF 内部报告类型或会计年度与所选报告不一致，请核对是否为对应年度的完整年报")
        if identity['status'] == 'ambiguous':
            observed = '、'.join(sorted({item['stock_code'] for item in identity.get('sources',[])})) or '多个代码'
            raise MiningError(f'PDF 中存在多个本公司证券代码（{observed}），无法唯一确认报告主体；请核对封面与公司基本情况')
        if identity['stock_code'] and identity['stock_code'] != code:
            raise MiningError(f"PDF 识别的本公司证券代码为 {identity['stock_code']}，与所选公司代码 {code} 不一致；已停止采用该文件，请核对公司和原文")
        if parsed.get("file_sha256") != file_info["sha256"]:
            raise MiningError("Parsed PDF hash differs from selected file manifest")
        identity_status = "pdf_code_matches_manifest"
        if not identity["stock_code"] and (local_record is not None or not fetch_online or code in fallback_codes):
            raise MiningError("PDF 中未找到可唯一识别的本公司证券代码，暂不能用于离线比较；请核对原文或补传清晰的完整年报")
        if fetch_online and local_record is None and code not in fallback_codes:
            if identity['stock_code']:
                # The issuer code already matches the selected official record.
                # Do not make acceptance depend on a second full-name search.
                identity_status = 'pdf_code_matches_catalogue'
            else:
                identified = resolve_security(parsed.get('company_name') or '')
                if identified['stock_code'] != code:
                    raise MiningError('PDF 公司名称核验结果与所选证券代码不一致，已停止采用该文件')
                identity_status = 'pdf_company_matches_catalogue'
        parsed["security_code"] = code
        parsed["disclosure"] = {key: file_info.get(key) for key in
                                ("announcement_id", "title", "published_at", "source_url",
                                 "catalogue_url", "version_label")}
        output["retrieval"]["files"].append({**file_info, "role": role, "status": "accepted",
                    "identity_status": identity_status, "document_id": parsed["document_id"],
                    "version_selection": "selected_local_snapshot_version" if local_record is not None else "latest_returned_catalogue_version" if "manifest_path" not in record
                    else "latest_available_cache_only"})
        return parsed, {**identity, "stock_code": code, "status": identity_status}

    def acquire_remote_or_cache(code, year, role, years):
        if code not in catalogues:
            candidates = [record for record in cache_records if record["stock_code"] == code]
            if fetch_online:
                try:
                    progress(f'联网检索正式年度报告：{code} · {year} 年')
                    securities[code] = securities.get(code) or resolve_security(code)
                    candidates = discover_annual_reports(securities[code], years)
                except (DisclosureError, OSError, ValueError) as exc:
                    fallback_codes.add(code)
                    warning = {"code": "CATALOGUE_UNAVAILABLE_CACHE_FALLBACK", "stock_code": code,
                               "message": str(exc), "version_selection": "latest_available_cache_only"}
                    warnings.append(warning)
                    output["retrieval"]["warnings"].append(warning)
            catalogues[code] = candidates
        selected, alternatives = latest_reports_by_year(catalogues[code], [year])
        output["retrieval"]["alternatives"].extend({"role": role, **item} for item in alternatives)
        record = selected.get(year)
        if record is None:
            if code in fallback_codes:
                raise MiningError(f'公开报告目录查询失败，且本机没有 {code} 的 {year} 年完整年报；请检查网络后重试，或上传本地年报')
            raise MiningError(f'未找到 {code} 的 {year} 年完整年度报告；请核对报告年度，或上传本地年报')
        progress(f'{"校验缓存年报" if "manifest_path" in record else "正在下载年度报告"}：{code} · {year} 年')
        file_info = _verify_cache(record) if "manifest_path" in record else download_report(record, directory)
        return record, file_info

    def failed(role: str, code: str | None, year: int | None, exc: Exception) -> None:
        warning = {"code": "DISCLOSURE_FILE_UNAVAILABLE", "role": role, "stock_code": code,
                   "report_year": year, "message": str(exc)}
        warnings.append(warning)
        output["retrieval"]["warnings"].append(warning)
        output["retrieval"]["files"].append({"role": role, "stock_code": code, "report_year": year,
                                             "status": "rejected_or_missing", "message": str(exc)})

    expansion_allowed = True
    peer_configuration_invalid = False
    try:
        if input_pdf is not None:
            inspection = read_pdf_document(input_pdf, max_pages=2, enable_ocr=False)
            kind = classify_disclosure(inspection)["document_type"]
            if kind == "half_year_report":
                current = analyze_semiannual_reports([input_pdf])
                if report_year is not None and current["documents"] and current["documents"][0]["report_year"] != report_year:
                    raise MiningError("Requested fiscal year differs from uploaded H1 report")
                if options:
                    warnings.append({"code": "H1_OPTIONS_NOT_APPLIED", "message": "options configures annual rules only"})
                if current["documents"]:
                    half_year_identity = _pdf_security_identity(Path(input_pdf).resolve())
                    output["identity"] = {**half_year_identity,
                        "company_name": current["documents"][0].get("company_name"),
                        "report_year": current["documents"][0]["report_year"], "report_type": "semiannual"}
                    if stock_code and half_year_identity["stock_code"] != stock_code:
                        warnings.append({"code": "INPUT_SECURITY_CODE_CONFLICT", "requested_code": stock_code,
                                         "reported_code": half_year_identity["stock_code"]})
                output["current"] = current
                output["coverage"]["current_available"] = bool(current["documents"])
                output["history"]["status"] = "unsupported" if lookback_years else "not_requested"
                output["peers"]["status"] = "unsupported" if peers else "not_requested"
                warnings.extend(current["warnings"])
                if lookback_years or peers:
                    warnings.append({"code": "H1_CROSS_REPORT_COMPARISON_UNSUPPORTED",
                                     "message": "H1 current analysis available; history and peers are annual-only"})
                output["coverage"].update({"history_status": output["history"]["status"],
                    "peers_status": output["peers"]["status"]})
                warnings.extend(output["retrieval"]["warnings"])
                output["audit"].extend(current["audit"])
                output["status"] = "failed" if not current["documents"] else "partial" if warnings else "complete"
                return output
            if kind != "annual_report":
                raise MiningError("This acquisition/comparison entry requires a text annual report")
            target, identity = load(input_pdf)
            if report_year is not None and target["report_year"] != report_year:
                raise MiningError("Requested fiscal year differs from uploaded report")
            actual_code = identity["stock_code"]
            if identity["status"] == "ambiguous" or (stock_code and actual_code and actual_code != stock_code):
                expansion_allowed = False
                warnings.append({"code": "INPUT_SECURITY_CODE_CONFLICT", "requested_code": stock_code,
                                 "reported_code": actual_code,
                                 "message": "Current analysis retained; no history or peer grouping performed"})
            if fetch_online and expansion_allowed:
                try:
                    identified = resolve_security(actual_code or target.get("company_name") or "")
                    if (stock_code and identified["stock_code"] != stock_code) or (
                            actual_code and identified["stock_code"] != actual_code):
                        raise MiningError('公司身份核验结果与请求代码或 PDF 本公司代码不一致')
                    actual_code = identified["stock_code"]
                    securities[actual_code] = identified
                    identity["status"] = "pdf_company_matches_catalogue"
                except (DisclosureError, OSError, ValueError, MiningError) as exc:
                    expansion_allowed = False
                    warnings.append({"code": "INPUT_IDENTITY_UNVERIFIED", "message": str(exc)})
            if actual_code is None:
                expansion_allowed = False
                warnings.append({"code": "INPUT_IDENTITY_UNVERIFIED",
                                 "message": "No unambiguous PDF code or successful company-name lookup"})
            stock_code = actual_code
            output["retrieval"]["files"].append({"role": "input", "status": "accepted",
                "stock_code": stock_code, "report_year": target["report_year"],
                "local_path": str(Path(input_pdf).resolve()), "sha256": target["file_sha256"],
                "document_id": target["document_id"], "source_status": "user_supplied",
                "identity_status": identity["status"], "version_selection": "uploaded_version_not_compared"})
        else:
            years = list(range(report_year - lookback_years, report_year + 1))
            target, identity = acquire(stock_code, report_year, "target", years)
        report_year = target["report_year"]
        if peers and stock_code:
            try:
                _validate_group(stock_code, peers, peer_group, selection_note)
            except ValueError as exc:
                peer_configuration_invalid = True
                warnings.append({"code": "PEER_CONFIGURATION_INVALID", "message": str(exc)})
                output["peers"] = {"status": "invalid_configuration", "reports": {},
                                   "comparison": None, "missing_codes": list(peers)}
                peers = []
        if stock_code and expansion_allowed:
            target["security_code"] = stock_code
        output["identity"] = {"stock_code": stock_code, "report_year": report_year, "report_type": "annual",
                              "status": identity["status"] if expansion_allowed else "conflict_or_unverified",
                              "sources": identity["sources"], "company_name": target.get("company_name")}
        progress('正在计算本期财务指标与风险规则')
        output["current"] = _analysis([target], options)
        output["coverage"]["current_available"] = True
        output["coverage"]["annual_comparisons_supported"] = True
    except (DisclosureError, MiningError, OSError, ValueError, RuntimeError) as exc:
        failed("target", stock_code, report_year, exc)
        output["warnings"] = list({json.dumps(item, sort_keys=True, ensure_ascii=False): item
            for item in [*warnings, *output["retrieval"]["warnings"]]}.values())
        return output

    wanted = list(range(report_year - lookback_years, report_year))
    history_documents = [target]
    local_years = set()
    local_candidates: dict[int, list[tuple[Path, dict, dict]]] = {}
    blocked_years = set()
    if expansion_allowed:
        for path in local_history_paths or []:
            try:
                parsed, identity = load(path)
                year = parsed["report_year"]
                if year not in wanted:
                    raise MiningError("Local history year is outside requested lookback")
                if identity["status"] == "ambiguous" or (identity["stock_code"] and identity["stock_code"] != stock_code):
                    raise MiningError("Local history PDF has a different stock code")
                if not identity["stock_code"] and parsed.get("company_name") != target.get("company_name"):
                    raise MiningError("Local history identity does not match uploaded company")
                local_candidates.setdefault(year, []).append((Path(path).resolve(), parsed, identity))
            except (MiningError, OSError, ValueError, RuntimeError) as exc:
                failed("local_history", stock_code, None, exc)
        for year, candidates in local_candidates.items():
            # Exact duplicate paths do not create a second version.
            candidates = list({path: (path, parsed, identity) for path, parsed, identity in candidates}.values())
            if len(candidates) != 1:
                blocked_years.add(year)
                failed("local_history", stock_code, year, MiningError(
                    "Multiple local reports for one year require explicit version selection"))
                continue
            path, parsed, identity = candidates[0]
            parsed["security_code"] = stock_code
            history_documents.append(parsed)
            local_years.add(year)
            output["retrieval"]["files"].append({"role": "history", "status": "accepted",
                "stock_code": stock_code, "report_year": year,
                "local_path": str(path), "sha256": parsed["file_sha256"],
                "document_id": parsed["document_id"], "source_status": "user_supplied",
                "identity_status": "pdf_code_matches_target" if identity["stock_code"] else "exact_company_name",
                "version_selection": "caller_selected_local_version"})
        for year in wanted:
            if year in local_years or year in blocked_years:
                continue
            try:
                parsed, _ = acquire(stock_code, year, "history", wanted or [report_year])
                history_documents.append(parsed)
            except (DisclosureError, MiningError, OSError, ValueError, RuntimeError) as exc:
                failed("history", stock_code, year, exc)
    if lookback_years:
        history_analysis = _analysis(history_documents, options)
        history = build_annual_history(history_analysis, stock_code if expansion_allowed else None)
        available = [year for year in wanted if year in history["report_years"]]
        output["history"] = {"status": "complete" if len(available) == len(wanted) else "partial",
                             "requested_years": wanted, "available_years": available,
                             "missing_years": [year for year in wanted if year not in available],
                             "analysis": history_analysis, "comparison": history}
    if peers:
        analyses = {stock_code: output["current"]} if expansion_allowed else {}
        if expansion_allowed:
            for code in peers:
                try:
                    parsed, _ = acquire(code, report_year, "peer", [report_year])
                    analyses[code] = _analysis([parsed], options)
                except (DisclosureError, MiningError, OSError, ValueError, RuntimeError) as exc:
                    failed("peer", code, report_year, exc)
        output["peers"] = {"status": "complete" if all(code in analyses for code in peers) else "partial",
                           "reports": analyses, "missing_codes": [code for code in peers if code not in analyses],
                           "comparison": compare_peer_group(analyses, target_code=stock_code,
                               peer_codes=peers, report_year=report_year, peer_group=peer_group,
                               selection_note=selection_note, metric_codes=metric_codes)
                           if expansion_allowed else None}
    warnings.extend(output["retrieval"]["warnings"])
    for role, analysis in [("current", output["current"]), ("history", output["history"].get("analysis")),
                            *[(f"peer:{code}", result) for code, result in output["peers"]["reports"].items()
                              if code != stock_code]]:
        if analysis:
            warnings.extend({"analysis_scope": role, **item} for item in analysis["warnings"])
    if output["history"].get("comparison"):
        warnings.extend(output["history"]["comparison"]["warnings"])
    # The target also appears in history.analysis; show the same document warning once.
    unique = {}
    for item in warnings:
        key = json.dumps({name: value for name, value in item.items() if name != "analysis_scope"},
                         sort_keys=True, ensure_ascii=False)
        unique.setdefault(key, item)
    output["warnings"] = list(unique.values())
    output["audit"] = [{"event": "pdf_parsed_once", "document_id": parsed["document_id"],
                        "file_sha256": parsed["file_sha256"], "local_path": str(path)}
                       for path, parsed in parsed_by_path.items()]
    output["audit"].extend({"event": "disclosure_file_accepted", "role": item["role"],
                            "stock_code": item.get("stock_code"), "report_year": item.get("report_year"),
                            "document_id": item.get("document_id"), "sha256": item.get("sha256")}
                           for item in output["retrieval"]["files"] if item["status"] == "accepted")
    output["coverage"].update({"history_status": output["history"]["status"],
                               "peers_status": output["peers"]["status"],
                               "current_metric_count": len(output["current"]["metrics"]),
                               "current_unavailable_metric_count": len(output["current"]["unavailable_metrics"])})
    output["risk_review"] = evaluate_context_risks(output)
    output["status"] = "partial" if (not expansion_allowed or peer_configuration_invalid
                                      or output["history"]["status"] == "partial"
                                      or output["peers"]["status"] == "partial"
                                      or output["retrieval"]["warnings"]) else "complete"
    return output
