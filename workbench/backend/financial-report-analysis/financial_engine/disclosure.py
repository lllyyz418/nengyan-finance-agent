"""Retrieve annual reports from the public CNINFO disclosure catalogue.

This module uses only the Python standard library. CNINFO's website endpoint is
not a documented stable API, so callers must handle DisclosureError and may
provide local reports to the history workflow instead.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


SEARCH_URL = "https://www.cninfo.com.cn/new/information/topSearch/query"
ANNOUNCEMENT_URL = "https://www.cninfo.com.cn/new/hisAnnouncement/query"
PDF_HOST = "static.cninfo.com.cn"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_PDF_BYTES = 80 * 1024 * 1024
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FinancialReportResearch/0.7)",
    "Referer": "https://www.cninfo.com.cn/",
    "Accept": "application/json, text/plain, */*",
}


class DisclosureError(RuntimeError):
    """A public disclosure could not be identified, obtained, or verified."""


def _post_form(url: str, form: dict[str, str]) -> object:
    data = urlencode(form).encode("utf-8")
    request = Request(url, data=data, headers=HEADERS, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except OSError as exc:
        raise DisclosureError('公开披露接口查询失败，请检查网络后重试') from exc
    if len(body) > MAX_RESPONSE_BYTES:
        raise DisclosureError('公开披露接口返回内容超过大小限制')
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DisclosureError('公开披露接口返回格式异常，请稍后重试') from exc


def _exchange_column(stock_code: str) -> str:
    if re.fullmatch(r"(?:60|68)\d{4}", stock_code):
        return "sse"
    if re.fullmatch(r"(?:00|30)\d{4}", stock_code):
        return "szse"
    if re.fullmatch(r"(?:4|8|9)\d{5}", stock_code):
        return "bj"
    raise DisclosureError(f"Unsupported A-share security code: {stock_code!r}")


def resolve_security(query: str) -> dict:
    """Resolve a six-digit A-share code or company name; reject ambiguity."""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a nonempty security code or company name")
    query = query.strip()
    data = _post_form(SEARCH_URL, {"keyWord": query, "maxNum": "20"})
    if not isinstance(data, list):
        raise DisclosureError('证券目录返回格式异常，请稍后重试')
    candidates = [item for item in data if isinstance(item, dict)
                  and item.get("category") == "A股"
                  and isinstance(item.get("code"), str)
                  and re.fullmatch(r"\d{6}", item["code"])
                  and isinstance(item.get("orgId"), str)]
    if re.fullmatch(r"\d{6}", query):
        candidates = [item for item in candidates if item["code"] == query]
    if len(candidates) != 1:
        codes = sorted({item["code"] for item in candidates})
        if not codes:
            raise DisclosureError(f'未在证券目录查到“{query}”对应的 A 股公司，请核对名称或六位证券代码后重试')
        raise DisclosureError(f'公司身份查询未得到唯一结果（候选代码：{"、".join(codes)}），请核对公司身份后重试')
    item = candidates[0]
    return {
        "stock_code": item["code"],
        "company_short_name": item.get("zwjc"),
        "org_id": item["orgId"],
        "exchange_column": _exchange_column(item["code"]),
        "lookup_query": query,
    }


def _pdf_url(relative_url: str) -> str:
    if not isinstance(relative_url, str):
        raise DisclosureError("Announcement has no PDF path")
    path = relative_url.lstrip("/")
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or parts.query or parts.fragment:
        raise DisclosureError("Announcement PDF path is not a relative path")
    if not re.fullmatch(r"finalpage/[A-Za-z0-9/_-]+\.PDF", path, re.IGNORECASE):
        raise DisclosureError("Announcement PDF path has an unexpected format")
    return f"https://{PDF_HOST}/{path}"


def _report_year(title: str, requested_years: set[int]) -> int | None:
    compact = re.sub(r"\s+", "", title)
    if "年度报告" not in compact or any(word in compact for word in
                               ("摘要", "英文", "取消", "提示性", "关于")):
        return None
    for year in requested_years:
        if re.search(fr"(?<!\d){year}年?年度报告", compact):
            return year
    return None


def discover_annual_reports(security: dict, years: list[int], *, report_type='annual', report_quarter=None) -> list[dict]:
    """Return all full-report versions; the caller chooses and records one."""
    if not years or any(not isinstance(year, int) or year < 1990 or year > 2100 for year in years):
        raise ValueError("years must contain valid fiscal years")
    from .periods import report_kind, report_label
    report_type=report_kind(report_type)
    if report_type not in ('annual','semiannual','quarterly') or report_type=='quarterly' and report_quarter not in (1,3):
        raise ValueError('报告类型或季度无效')
    code = security["stock_code"]
    column = security["exchange_column"]
    org_id = security["org_id"]
    wanted = set(years)
    found: dict[str, dict] = {}
    for page in range(1, 6):
        payload = _post_form(ANNOUNCEMENT_URL, {
            "stock": f"{code},{org_id}",
            "column": column,
            "tabName": "fulltext",
            "category": {'annual':'category_ndbg_szsh','semiannual':'category_bndbg_szsh','quarterly':'category_yjdbg_szsh' if report_quarter==1 else 'category_sjdbg_szsh'}[report_type],
            "seDate": f"{min(wanted) + (1 if report_type=='annual' else 0)}-01-01~{max(wanted) + 2}-12-31",
            "pageNum": str(page),
            "pageSize": "30",
            "isHLtitle": "true",
        })
        if not isinstance(payload, dict):
            raise DisclosureError("Unexpected announcement-search response")
        announcements = payload.get("announcements") or []
        if not isinstance(announcements, list):
            raise DisclosureError("Announcement list is malformed")
        for item in announcements:
            if not isinstance(item, dict) or item.get("secCode") != code:
                continue
            title = str(item.get("announcementTitle") or "")
            year = _report_year(title, wanted) if report_type=='annual' else periodic_title_year(title,wanted,report_type,report_quarter)
            if year is None:
                continue
            report_id = str(item.get("announcementId") or "")
            if not re.fullmatch(r"\d+", report_id):
                continue
            try:
                url = _pdf_url(item.get("adjunctUrl"))
                published_ms = int(item["announcementTime"])
                published = datetime.fromtimestamp(published_ms / 1000, timezone.utc).isoformat()
            except (DisclosureError, KeyError, TypeError, ValueError, OverflowError):
                continue
            found[report_id] = {
                "stock_code": code,
                "company_short_name": item.get("secName"),
                "report_year": year,
                "report_type": report_type,
                **({'report_quarter':report_quarter} if report_type=='quarterly' else {}),
                "announcement_id": report_id,
                "title": title,
                "published_at": published,
                "source_url": url,
                "catalogue_url": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
                "version_label": "revised" if any(word in title for word in ("修订", "更正", "补充")) else "original",
            }
        if not payload.get("hasMore") or not announcements:
            break
    return sorted(found.values(), key=lambda item: (item["report_year"], item["published_at"], item["announcement_id"]))


def periodic_title_year(title, years, report_type, report_quarter=None):
    compact=re.sub(r'\s+','',html.unescape(re.sub(r'<[^>]+>','',title)))
    if any(word in compact for word in ('摘要','英文','取消','提示性','关于')):return None
    suffix='半年度报告' if report_type=='semiannual' else ('(?:第)?[一1]季度报告' if report_quarter==1 else '(?:第)?[三3]季度报告')
    return next((year for year in years if re.search(fr'(?<!\d){year}年?{suffix}',compact)),None)


def latest_reports_by_year(records: list[dict], years: list[int]) -> tuple[dict[int, dict], list[dict]]:
    """Choose the latest disclosed full version and expose older candidates."""
    selected: dict[int, dict] = {}
    alternatives: list[dict] = []
    for year in years:
        versions = sorted((item for item in records if item["report_year"] == year),
                          key=lambda item: (item["published_at"], item["announcement_id"]), reverse=True)
        if versions:
            selected[year] = versions[0]
            alternatives.extend(versions[1:])
    return selected, alternatives


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_report(record: dict, destination: str | Path) -> dict:
    """Cache one verified PDF and its provenance beside it on the chosen drive."""
    code = str(record["stock_code"])
    is_announcement = record.get("report_type") == "announcement"
    year = int(record["report_year"]) if not is_announcement else None
    report_id = str(record["announcement_id"])
    if not re.fullmatch(r"\d{6}", code) or not re.fullmatch(r"\d+", report_id):
        raise DisclosureError("Invalid stock code or announcement id")
    url = str(record["source_url"])
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != PDF_HOST or not parts.path.startswith("/finalpage/"):
        raise DisclosureError("PDF source is outside the approved disclosure host")
    directory = Path(destination).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    pdf_path = directory / (f"{code}_announcement_{report_id}.pdf" if is_announcement
                            else f"{code}_{year}_{report_id}.pdf")
    manifest_path = pdf_path.with_suffix(".json")
    if pdf_path.exists():
        with pdf_path.open("rb") as stream:
            if stream.read(5) != b"%PDF-":
                raise DisclosureError(f"Cached file is not a PDF: {pdf_path}")
        sha256 = _sha256_file(pdf_path)
        if manifest_path.exists():
            try:
                old = json.loads(manifest_path.read_text(encoding="utf-8"))
                if old.get("sha256") != sha256 or old.get("source_url") != url:
                    raise DisclosureError("Cached PDF provenance does not match its manifest")
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise DisclosureError("Cached PDF manifest is unreadable") from exc
        cache_status = "reused"
    else:
        temp_path: Path | None = None
        try:
            request = Request(url, headers={"User-Agent": HEADERS["User-Agent"], "Referer": HEADERS["Referer"]})
            with urlopen(request, timeout=45) as response, tempfile.NamedTemporaryFile(
                    dir=directory, suffix=".part", delete=False) as temp:
                temp_path = Path(temp.name)
                digest = hashlib.sha256()
                total = 0
                header = b""
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_PDF_BYTES:
                        raise DisclosureError("PDF exceeds the download size limit")
                    if len(header) < 5:
                        header += chunk[:5 - len(header)]
                    digest.update(chunk)
                    temp.write(chunk)
                if header != b"%PDF-":
                    raise DisclosureError("Downloaded content is not a PDF")
                sha256 = digest.hexdigest()
            temp_path.replace(pdf_path)
            temp_path = None
        except OSError as exc:
            raise DisclosureError(f"PDF download failed: {exc}") from exc
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        cache_status = "downloaded"
    manifest = {**record, "local_path": str(pdf_path), "sha256": sha256,
                "bytes": pdf_path.stat().st_size}
    temp_manifest: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         suffix=".json.part", delete=False) as temp:
            temp_manifest = Path(temp.name)
            json.dump(manifest, temp, ensure_ascii=False, indent=2)
        temp_manifest.replace(manifest_path)
        temp_manifest = None
    finally:
        if temp_manifest is not None:
            temp_manifest.unlink(missing_ok=True)
    return {**manifest, "cache_status": cache_status, "manifest_path": str(manifest_path)}


def _event_title(title: str) -> bool:
    """Restrict broad catalogue keyword matches to likely company events."""
    if "公告" not in title or any(term in title for term in
                                  ("投资者", "管理制度", "核查意见", "核查报告", "调研活动")):
        return False
    return bool(re.search(r"中标|扩产|扩建|投资建设|项目投资|投资项目|对外投资|参与投资", title))


def discover_event_announcements(security: dict, start_date: str, end_date: str,
                                 *, keywords: tuple[str, ...] = ("中标", "投资", "扩产", "扩建"),
                                 max_pages_per_keyword: int = 3) -> list[dict]:
    """Find likely event notices in CNINFO's changeable public catalogue.

    This is a title-screened discovery list, not a completeness guarantee.
    Search terms, pagination limits, and the source URL are returned so the
    host can show how the list was obtained.
    """
    try:
        begin, end = date.fromisoformat(start_date), date.fromisoformat(end_date)
    except ValueError as exc:
        raise ValueError("start_date and end_date must be YYYY-MM-DD") from exc
    if begin > end:
        raise ValueError("start_date must not be after end_date")
    if not 1 <= max_pages_per_keyword <= 10:
        raise ValueError("max_pages_per_keyword must be between 1 and 10")
    if not keywords or len(keywords) > 8 or any(not isinstance(word, str) or not word.strip()
                                                for word in keywords):
        raise ValueError("keywords must contain 1-8 nonempty strings")
    code, org_id, column = (security[key] for key in ("stock_code", "org_id", "exchange_column"))
    if not re.fullmatch(r"\d{6}", code) or _exchange_column(code) != column or not isinstance(org_id, str):
        raise DisclosureError("Invalid resolved security")
    found: dict[str, dict] = {}
    for keyword in keywords:
        for page in range(1, max_pages_per_keyword + 1):
            payload = _post_form(ANNOUNCEMENT_URL, {
                "stock": f"{code},{org_id}", "column": column, "tabName": "fulltext",
                "category": "", "seDate": f"{begin.isoformat()}~{end.isoformat()}",
                "pageNum": str(page), "pageSize": "30", "searchkey": keyword,
                "isHLtitle": "true",
            })
            if not isinstance(payload, dict) or not isinstance(payload.get("announcements") or [], list):
                raise DisclosureError("Unexpected announcement-search response")
            announcements = payload.get("announcements") or []
            for item in announcements:
                if not isinstance(item, dict) or item.get("secCode") != code:
                    continue
                title = html.unescape(re.sub(r"</?em>", "", str(item.get("announcementTitle") or ""), flags=re.I))
                if not _event_title(title):
                    continue
                announcement_id = str(item.get("announcementId") or "")
                if not re.fullmatch(r"\d+", announcement_id):
                    continue
                try:
                    url = _pdf_url(item.get("adjunctUrl"))
                    published = datetime.fromtimestamp(int(item["announcementTime"]) / 1000, timezone.utc)
                except (DisclosureError, KeyError, TypeError, ValueError, OverflowError):
                    continue
                local_date = published.astimezone(timezone(timedelta(hours=8))).date()
                if not begin <= local_date <= end:
                    continue
                record = found.setdefault(announcement_id, {
                    "stock_code": code, "company_short_name": item.get("secName"),
                    "report_type": "announcement", "announcement_id": announcement_id,
                    "title": title, "published_at": published.isoformat(),
                    "published_date_cn": local_date.isoformat(),
                    "source_url": url,
                    "catalogue_url": "https://www.cninfo.com.cn/new/commonUrl?url=disclosure/list/notice",
                    "query_keywords": [], "discovery_limit_pages_per_keyword": max_pages_per_keyword,
                })
                if keyword not in record["query_keywords"]:
                    record["query_keywords"].append(keyword)
            if not payload.get("hasMore") or not announcements:
                break
    return sorted(found.values(), key=lambda item: (item["published_at"], item["announcement_id"]))


def download_announcement(record: dict, destination: str | Path) -> dict:
    """Cache an official event PDF and adjacent provenance manifest."""
    if record.get("report_type") != "announcement":
        raise DisclosureError("Expected an announcement discovery record")
    return download_report(record, destination)
