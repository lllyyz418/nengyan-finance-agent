import unittest
from decimal import Decimal
from unittest.mock import patch

from financial_engine import analyze_parsed_documents, analyze_peer_group, compare_peer_group
from financial_engine.disclosure import DisclosureError


def financial_result(year, name, revenue, cost):
    doc_id = f"{name}-{year}"
    rows = []
    for code, value in (("revenue", revenue), ("operating_cost", cost)):
        rows.append({"kind": "annual", "metric_code": code, "label": code,
                     "unit": "元", "values": [value, "80"],
                     "source": {"document_id": doc_id, "page_start": 2,
                                "page_end": 2, "section": "annual_summary"}})
    document = {"document_id": doc_id, "company_name": name, "report_year": year,
                "report_type": "annual", "currency": "CNY", "file_sha256": doc_id,
                "rows": rows}
    return analyze_parsed_documents([document])


class PeerComparisonTests(unittest.TestCase):
    def setUp(self):
        self.analyses = {
            "300750": financial_result(2025, "目标公司", "100", "60"),
            "300014": financial_result(2025, "同行甲", "100", "70"),
            "002074": financial_result(2025, "同行乙", "100", "80"),
        }
        self.kwargs = {"target_code": "300750", "peer_codes": ["300014", "002074"],
                       "report_year": 2025, "peer_group": "电池系统",
                       "selection_note": "人工选择业务相关企业，未调整产品结构"}

    def test_two_peers_have_exact_decimal_median_and_sources(self):
        result = compare_peer_group(self.analyses, **self.kwargs)
        item = next(entry for entry in result["metric_comparisons"]
                    if entry["metric_code"] == "gross_margin")
        self.assertEqual(item["valid_peer_count"], 2)
        self.assertEqual(Decimal(item["peer_median"]["value"]), Decimal("25"))
        self.assertEqual(Decimal(item["target_minus_peer_median"]), Decimal("15"))
        self.assertEqual(item["difference_unit"], "percentage_points")
        self.assertEqual(len(item["peer_median"]["input_metric_ids"]), 2)
        self.assertTrue(all(point["input_fact_ids"] for point in item["peers"]))

    def test_missing_or_mismatched_peer_is_excluded_from_median(self):
        self.analyses["002074"]["metrics"] = [
            {**item, "formula": "different formula"} if item["metric_code"] == "gross_margin" else item
            for item in self.analyses["002074"]["metrics"]
        ]
        result = compare_peer_group(self.analyses, **self.kwargs)
        item = next(entry for entry in result["metric_comparisons"]
                    if entry["metric_code"] == "gross_margin")
        self.assertEqual(item["valid_peer_count"], 1)
        self.assertEqual(item["peers"][1]["reason_code"], "INCOMPARABLE_FORMULA_OR_UNIT")
        self.assertEqual(item["peer_median"]["status"], "unavailable")
        self.assertIsNone(item["target_minus_peer_median"])

    def test_single_peer_still_has_side_by_side_value(self):
        result = compare_peer_group(self.analyses, **{
            **self.kwargs, "peer_codes": ["300014"], "metric_codes": ["gross_margin"]})
        item = result["metric_comparisons"][0]
        self.assertEqual(item["peers"][0]["status"], "available")
        self.assertEqual(item["peer_median"]["reason_code"], "INSUFFICIENT_PEER_COVERAGE")

    def test_group_selection_must_be_explicit(self):
        with self.assertRaises(ValueError):
            compare_peer_group(self.analyses, **{**self.kwargs, "selection_note": ""})
        with self.assertRaises(ValueError):
            compare_peer_group(self.analyses, **{**self.kwargs, "peer_codes": ["300750"]})

    def test_float_metric_is_withheld(self):
        for metric in self.analyses["300750"]["metrics"]:
            if metric["metric_code"] == "gross_margin":
                metric["value"] = 40.0
        result = compare_peer_group(self.analyses, **self.kwargs)
        item = next(entry for entry in result["metric_comparisons"]
                    if entry["metric_code"] == "gross_margin")
        self.assertEqual(item["target"]["reason_code"], "FLOAT_VALUE_NOT_ALLOWED")
        self.assertIsNone(item["target_minus_peer_median"])

    def test_online_failure_preserves_target_and_peer_warning(self):
        target = self.analyses["300750"]
        target["documents"][0]["company_name"] = "目标公司"
        target["documents"][0]["file_sha256"] = "abc"
        with patch("financial_engine.peer.analyze_reports", return_value=target), \
             patch("financial_engine.peer.resolve_security", side_effect=[
                 {"stock_code": "300750"}, {"stock_code": "300014"}]), \
             patch("financial_engine.peer.discover_annual_reports", side_effect=DisclosureError("offline")):
            result = analyze_peer_group(
                "target.pdf", peer_codes=["300014"], peer_group="电池系统",
                selection_note="人工选择相关电池业务", cache_dir="D:/ai_finance/skill/report_cache")
        self.assertEqual(result["status"], "partial")
        self.assertIn("300750", result["reports"])
        self.assertEqual(result["warnings"][0]["code"], "PEER_REPORT_FAILED")
        item = next(entry for entry in result["comparison"]["metric_comparisons"]
                    if entry["metric_code"] == "gross_margin")
        self.assertEqual(item["peers"][0]["reason_code"], "REPORT_NOT_AVAILABLE")

    def test_peer_pdf_company_mismatch_is_excluded(self):
        target, wrong_peer = self.analyses["300750"], self.analyses["300014"]
        with patch("financial_engine.peer.analyze_reports", side_effect=[target, wrong_peer]), \
             patch("financial_engine.peer.resolve_security", side_effect=[
                 {"stock_code": "300750"}, {"stock_code": "300014"},
                 {"stock_code": "601868"}]), \
             patch("financial_engine.peer.discover_annual_reports", return_value=[{"report_year": 2025}]), \
             patch("financial_engine.peer.latest_reports_by_year", return_value=({2025: {}}, [])), \
             patch("financial_engine.peer.download_report", return_value={"local_path": "peer.pdf"}):
            result = analyze_peer_group(
                "target.pdf", peer_codes=["300014"], peer_group="电池系统",
                selection_note="人工选择相关电池业务", cache_dir="D:/ai_finance/skill/report_cache")
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("300014", result["reports"])
        self.assertEqual(result["warnings"][0]["code"], "PEER_REPORT_FAILED")


if __name__ == "__main__":
    unittest.main()
