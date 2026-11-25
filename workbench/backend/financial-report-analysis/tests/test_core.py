import unittest

from financial_engine.core import analyze_parsed_documents, parse_amount


def row(kind, label, values, unit="万元", page=1):
    return {
        "kind": kind,
        "label": label,
        "values": values,
        "unit": unit,
        "source": {"document_id": "synthetic", "page_start": page, "page_end": page, "table_index": 0},
    }


class CoreTests(unittest.TestCase):
    def test_amounts_are_exact_and_missing_is_not_zero(self):
        self.assertEqual(str(parse_amount("1,234.50", "千元")), "1234500.00")
        self.assertEqual(str(parse_amount("(12.5)", "万元")), "-125000.0")
        self.assertIsNone(parse_amount("-", "元"))
        self.assertIsNone(parse_amount("12%", "元"))
        self.assertIsNone(parse_amount("12", "unknown"))

    def test_growth_reconciliation_and_review_signals(self):
        document = {
            "document_id": "synthetic",
            "company_name": "示例公司",
            "report_year": 2025,
            "rows": [
                row("annual", "营业收入", ["200", "100"]),
                row("annual", "归属于上市公司股东的净利润", ["100", "80"]),
                row("annual", "归属于上市公司股东的扣除非经常性损益的净利润", ["50", "60"]),
                row("annual", "经营活动产生的现金流量净额", ["-10", "5"]),
                row("quarterly", "营业收入", ["20", "30", "40", "110"]),
                row("nonrecurring", "合计", ["50", "20"]),
            ],
        }
        result = analyze_parsed_documents([document])
        metrics = {item["metric_code"]: item for item in result["metrics"]}
        self.assertEqual(metrics["revenue_yoy"]["value"], "100")
        self.assertEqual(metrics["revenue_q4_qoq"]["value"], "175.00")
        self.assertEqual(metrics["nonrecurring_to_attributable_profit"]["value"], "50.0")
        self.assertEqual({item["category"] for item in result["findings"]}, {"cash_profit_divergence", "nonrecurring_share"})
        self.assertFalse([w for w in result["warnings"] if "MISMATCH" in w["code"] or "RECONCILIATION" in w["code"]])
        self.assertTrue(all(item["input_fact_ids"] for item in result["metrics"]))

    def test_conflicting_facts_cannot_support_growth(self):
        document = {
            "document_id": "synthetic",
            "report_year": 2025,
            "rows": [
                row("annual", "营业收入", ["200", "100"], page=1),
                row("annual", "营业收入", ["201", "100"], page=2),
            ],
        }
        result = analyze_parsed_documents([document])
        self.assertIn("CONFLICTING_VALUES", {w["code"] for w in result["warnings"]})
        self.assertNotIn("revenue_yoy", {m["metric_code"] for m in result["metrics"]})

    def test_equivalent_amounts_in_different_units_are_cross_checked(self):
        document = {
            "document_id": "synthetic",
            "report_year": 2025,
            "rows": [
                {**row("annual", "营业收入", ["1000.00", "900.00"], unit="元", page=1),
                 "source": {"page_start": 1, "page_end": 1, "section": "annual_summary"}},
                {**row("income_statement", "营业收入", ["1", "0.9"], unit="千元", page=2),
                 "source": {"page_start": 2, "page_end": 2, "section": "consolidated_income_statement"}},
            ],
        }
        result = analyze_parsed_documents([document])
        revenue = next(f for f in result["facts"] if f["metric_code"] == "revenue" and f["period"] == "2025")
        self.assertEqual(revenue["quality"], "cross_checked")
        self.assertEqual(len(revenue["sources"]), 2)
        self.assertNotIn("CONFLICTING_VALUES", {w["code"] for w in result["warnings"]})


if __name__ == "__main__":
    unittest.main()
