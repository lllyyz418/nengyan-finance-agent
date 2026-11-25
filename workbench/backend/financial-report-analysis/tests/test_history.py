import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from financial_engine import analyze_parsed_documents, analyze_company_history, build_annual_history
from financial_engine.disclosure import (
    DisclosureError, discover_annual_reports, download_report,
    latest_reports_by_year, resolve_security,
)
from financial_engine.pdf_adapter import _later_global_statement_unit


def report_row(code, current, comparative, document_id):
    return {
        "kind": "annual",
        "metric_code": code,
        "label": code,
        "unit": "元",
        "values": [current, comparative],
        "source": {"document_id": document_id, "page_start": 2,
                   "page_end": 2, "section": "annual_summary"},
    }


def annual_document(year, revenue, cost, previous_revenue, previous_cost):
    doc_id = f"demo{year}"
    return {
        "document_id": doc_id,
        "company_name": "示例股份有限公司",
        "security_code": "002594",
        "report_type": "annual",
        "report_year": year,
        "rows": [report_row("revenue", revenue, previous_revenue, doc_id),
                 report_row("operating_cost", cost, previous_cost, doc_id)],
    }


class DisclosureTests(unittest.TestCase):
    def test_security_lookup_rejects_h_share_and_ambiguous_name(self):
        candidates = [
            {"code": "002594", "orgId": "gshk0001211", "zwjc": "比亚迪", "category": "A股"},
            {"code": "01211", "orgId": "gshk0001211", "zwjc": "比亚迪股份", "category": "港股"},
        ]
        with patch("financial_engine.disclosure._post_form", return_value=candidates):
            self.assertEqual(resolve_security("002594")["exchange_column"], "szse")
            self.assertEqual(resolve_security("比亚迪股份有限公司")["stock_code"], "002594")
        with patch("financial_engine.disclosure._post_form", return_value=candidates + [
            {"code": "300750", "orgId": "g1", "zwjc": "另一家公司", "category": "A股"}
        ]):
            with self.assertRaises(DisclosureError):
                resolve_security("模糊公司名")

    def test_full_reports_and_latest_version_are_recorded(self):
        security = {"stock_code": "002594", "org_id": "gshk0001211", "exchange_column": "szse"}
        def record(report_id, title, timestamp):
            return {"secCode": "002594", "secName": "比亚迪", "announcementId": report_id,
                    "announcementTitle": title, "announcementTime": timestamp,
                    "adjunctUrl": f"finalpage/2025-03-25/{report_id}.PDF"}
        payload = {"announcements": [
            record("1", "2024年年度报告", 1742832000000),
            record("2", "2024年年度报告摘要", 1742832000000),
            record("3", "2024年年度报告（修订版）", 1743436800000),
        ], "hasMore": False}
        with patch("financial_engine.disclosure._post_form", return_value=payload):
            reports = discover_annual_reports(security, [2024])
        selected, alternatives = latest_reports_by_year(reports, [2024])
        self.assertEqual(len(reports), 2)
        self.assertEqual(selected[2024]["announcement_id"], "3")
        self.assertEqual(selected[2024]["version_label"], "revised")
        self.assertEqual(alternatives[0]["announcement_id"], "1")

    def test_download_checks_pdf_and_reuses_manifest(self):
        record = {"stock_code": "002594", "report_year": 2024,
                  "announcement_id": "42", "source_url":
                  "https://static.cninfo.com.cn/finalpage/2025-03-25/42.PDF",
                  "published_at": "2025-03-25T00:00:00+00:00", "title": "2024年年度报告"}
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(dir=project) as directory:
            with patch("financial_engine.disclosure.urlopen", return_value=io.BytesIO(b"%PDF-1.7\nhello")) as fetch:
                first = download_report(record, directory)
                second = download_report(record, directory)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(first["cache_status"], "downloaded")
            self.assertEqual(second["cache_status"], "reused")
            self.assertEqual(json.loads(Path(first["manifest_path"]).read_text(encoding="utf-8"))["sha256"], first["sha256"])
        with tempfile.TemporaryDirectory(dir=project) as directory:
            with patch("financial_engine.disclosure.urlopen", return_value=io.BytesIO(b"not a pdf")):
                with self.assertRaises(DisclosureError):
                    download_report(record, directory)
            self.assertFalse(list(Path(directory).glob("*.pdf")))


class HistoryTests(unittest.TestCase):
    def test_mismatched_security_code_does_not_mix_company_reports(self):
        uploaded = {"document_id": "uploaded", "file_sha256": "abc", "report_year": 2025,
                    "report_type": "annual", "company_name": "示例股份有限公司",
                    "parser": "test", "parser_warnings": [], "rows": []}
        with patch("financial_engine.history.parse_pdf", return_value=uploaded), \
             patch("financial_engine.history.resolve_security", return_value={"stock_code": "300750"}), \
             patch("financial_engine.history.discover_annual_reports") as discover:
            result = analyze_company_history("uploaded.pdf", stock_code="002594",
                                             cache_dir="D:/ai_finance/skill/report_cache")
        discover.assert_not_called()
        self.assertEqual(result["retrieval"]["identity_status"], "unverified")
        self.assertEqual(result["retrieval"]["status"], "partial")
        self.assertIsNone(result["history"]["stock_code"])
        self.assertIn("DISCLOSURE_DISCOVERY_FAILED", {item["code"] for item in result["warnings"]})

    def test_statement_unit_requires_explicit_declaration(self):
        class Page:
            def __init__(self, text):
                self.text = text
            def extract_text(self):
                return self.text
            def close(self):
                pass
        class PDF:
            pages = [Page("资产总计 1,000 900"),
                     Page("本财务报表所采用的货币均为人民币，除有特别说明外，均以人民币千元为单位表示。")]
        self.assertEqual(_later_global_statement_unit(PDF(), 0), ("千元", 2))
        self.assertIsNone(_later_global_statement_unit(type("PDF", (), {"pages": [Page("资产总计 1,000 900")]})(), 0))

    def test_decimal_percentage_point_change_has_metric_evidence(self):
        documents = [annual_document(2024, "100.00", "60.00", "90", "50"),
                     annual_document(2025, "150.00", "75.00", "100.00", "60.00")]
        result = analyze_parsed_documents(documents)
        history = build_annual_history(result, "002594")
        self.assertEqual([(point["year"], point["value"]) for point in history["fact_series"]["revenue"]],
                         [(2024, "100.00"), (2025, "150.00")])
        self.assertIn("matched", {item["status"] for item in history["restatement_checks"]})
        item = next(change for change in history["changes"] if change["metric_code"] == "gross_margin")
        self.assertEqual(item["status"], "calculated")
        self.assertEqual(item["absolute_change"], "10.0")
        self.assertEqual(item["unit"], "percentage_points")
        self.assertEqual(item["basis_check"], "matched_comparatives")
        self.assertTrue(item["from_metric_id"] and item["to_metric_id"])

    def test_changed_comparative_suppresses_cross_report_delta(self):
        documents = [annual_document(2024, "100", "60", "90", "50"),
                     annual_document(2025, "150", "75", "120", "60")]
        result = analyze_parsed_documents(documents)
        history = build_annual_history(result, "002594")
        item = next(change for change in history["changes"] if change["metric_code"] == "gross_margin")
        self.assertEqual(item["status"], "unavailable")
        self.assertEqual(item["reason_code"], "RESTATED_COMPARATIVE_CONFLICT")
        self.assertEqual(item["conflicting_facts"][0]["metric_code"], "revenue")
        self.assertNotIn("absolute_change", item)
        self.assertIn("difference", {entry["status"] for entry in history["restatement_checks"]})


if __name__ == "__main__":
    unittest.main()
