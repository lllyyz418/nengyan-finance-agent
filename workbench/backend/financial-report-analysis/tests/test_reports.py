import os
import unittest
from pathlib import Path

from financial_engine import analyze_reports
from benchmarks.metric_check import evaluate_metrics


class AnnualReportIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sample_dir = Path(os.environ.get("AI_FINANCE_SAMPLE_DIR", Path(__file__).resolve().parents[3]))
        cls.paths = [sample_dir / name for name in ("byd.pdf", "宁德时代.pdf", "中国能建.pdf")]
        if not all(path.is_file() for path in cls.paths):
            raise unittest.SkipTest("Set AI_FINANCE_SAMPLE_DIR to the directory containing three sample PDFs")
        cls.result = analyze_reports(cls.paths)

    def fact(self, company_fragment, code, period):
        return next(
            item for item in self.result["facts"]
            if company_fragment in (item["company_name"] or "")
            and item["metric_code"] == code
            and item["period"] == period
        )

    def test_annual_values_and_units(self):
        self.assertEqual(self.fact("比亚迪", "revenue", "2025")["value"], "803964958000.00")
        self.assertEqual(self.fact("宁德时代", "revenue", "2025")["value"], "423701834000")
        self.assertEqual(self.fact("中国能源建设", "revenue", "2025")["value"], "452929608000")
        self.assertEqual(self.fact("宁德时代", "revenue", "2025")["raw_unit"], "千元")
        self.assertEqual(self.fact("比亚迪", "revenue", "2025")["raw_unit"], "元")

    def test_cross_page_quarter_row_and_cash_signal(self):
        byd_profit = self.fact("比亚迪", "attributable_net_profit", "2025Q1")
        self.assertIn("净利润", byd_profit["raw_label"])
        self.assertEqual(byd_profit["sources"][0]["page_start"], 11)
        self.assertEqual(byd_profit["sources"][0]["page_end"], 12)
        self.assertEqual(self.fact("中国能源建设", "operating_cash_flow", "2025Q1")["value"], "-14509148000")

    def test_reconciliations_and_no_parse_failure(self):
        codes = {item["code"] for item in self.result["warnings"]}
        self.assertNotIn("PDF_PARSE_FAILED", codes)
        self.assertNotIn("QUARTER_SUM_MISMATCH", codes)
        self.assertNotIn("NONRECURRING_RECONCILIATION", codes)
        self.assertEqual(len(self.result["documents"]), 3)

    def test_nonrecurring_totals_do_not_bleed_into_later_tables(self):
        for company in ("比亚迪", "宁德时代", "中国能源建设"):
            self.assertEqual(self.fact(company, "nonrecurring_total", "2025")["raw_label"], "合计")
        self.assertEqual(
            self.fact("中国能源建设", "nonrecurring_total", "2025")["value"],
            "1211855000",
        )

    def test_consolidated_statements_and_evidence(self):
        byd_revenue = self.fact("比亚迪", "revenue", "2025")
        self.assertEqual(byd_revenue["quality"], "cross_checked")
        self.assertEqual({s["raw_unit"] for s in byd_revenue["sources"]}, {"元", "千元"})
        self.assertEqual({s["page_start"] for s in byd_revenue["sources"]}, {11, 126})
        self.assertEqual(byd_revenue["period_type"], "duration")
        self.assertEqual(byd_revenue["period_start"], "2025-01-01")
        self.assertEqual(byd_revenue["period_end"], "2025-12-31")

        inventory = self.fact("宁德时代", "inventory", "2025")
        self.assertEqual(inventory["statement_scope"], "consolidated")
        self.assertEqual(inventory["period_type"], "instant")
        self.assertIsNone(inventory["period_start"])
        self.assertEqual(inventory["period_end"], "2025-12-31")
        source = inventory["sources"][0]
        self.assertEqual(source["section"], "consolidated_balance_sheet")
        self.assertEqual(source["page_start"], 111)
        self.assertEqual(len(source["row_bbox"]), 4)
        self.assertLess(source["row_bbox"][0], source["row_bbox"][2])

        self.assertEqual(self.fact("中国能源建设", "capital_expenditure_cash_paid", "2025")["value"], "41681060000")
        self.assertTrue(self.fact("比亚迪", "operating_cost", "2024")["comparative_restated"])

    def test_note_reconciliations_and_gross_vs_net_amounts(self):
        for company in ("比亚迪", "宁德时代", "中国能源建设"):
            inventory = self.fact(company, "inventory", "2025")
            self.assertEqual(inventory["quality"], "cross_checked")
            self.assertIn("inventory_classification_note", {s["section"] for s in inventory["sources"]})
            self.assertGreater(
                int(self.fact(company, "accounts_receivable_gross", "2025")["value"]),
                int(self.fact(company, "accounts_receivable", "2025")["value"]),
            )
            for code in ("accounts_receivable_aging_within_1y", "accounts_receivable_aging_1_to_2y",
                         "accounts_receivable_aging_2_to_3y", "accounts_receivable_aging_over_3y"):
                self.assertEqual(self.fact(company, code, "2025")["period_type"], "instant")
        self.assertIn("NOTE_ROUNDING_DIFFERENCE", {w["code"] for w in self.result["warnings"]})

    def test_grant_breakdown_keeps_reported_categories_separate(self):
        self.assertEqual(self.fact("比亚迪", "government_grant_other_income_daily", "2025")["value"], "12468373000")
        self.assertEqual(self.fact("中国能源建设", "government_grant_other_income_asset_related", "2025")["value"], "24013000")
        self.assertEqual(self.fact("中国能源建设", "government_grant_other_income_income_related", "2025")["value"], "1059634000")
        self.assertFalse(any(
            "宁德时代" in (f["company_name"] or "") and f["metric_code"].startswith("government_grant_other_income_")
            for f in self.result["facts"]
        ))

    def test_formula_coverage_on_three_original_reports(self):
        required = {
            "gross_margin", "consolidated_net_margin", "adjusted_attributable_net_margin",
            "cash_to_consolidated_profit", "simplified_free_cash_flow", "inventory_days",
            "receivables_days_approx", "return_on_assets", "return_on_attributable_equity",
            "fixed_assets_cip_to_assets", "r_and_d_expense_to_revenue",
            "inventory_impairment_to_gross", "receivables_over_1y_to_gross",
        }
        for document in self.result["documents"]:
            doc_id = document["document_id"]
            actual = {m["metric_code"] for m in self.result["metrics"] if m["document_id"] == doc_id}
            with self.subTest(company=document["company_name"]):
                self.assertFalse(required - actual, required - actual)
                self.assertFalse(any(
                    item["metric_code"] in required and item["document_id"] == doc_id
                    for item in self.result["unavailable_metrics"]
                ))

    def test_39_metrics_against_independent_pdf_gold(self):
        comparison = evaluate_metrics(self.result)
        self.assertEqual(comparison["checked"], 39)
        self.assertEqual(comparison["errors"], 0, comparison["outcomes"])


if __name__ == "__main__":
    unittest.main()
