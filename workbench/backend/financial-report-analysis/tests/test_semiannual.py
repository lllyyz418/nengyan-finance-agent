import json
import unittest
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

from financial_engine import (analyze_parsed_semiannual, analyze_semiannual_reports,
                              analyze_uploaded_document)
from financial_engine.semiannual import _header_order


PROJECT = Path(__file__).resolve().parents[1]
GOLD = json.loads((PROJECT / "benchmarks" / "semiannual_2025_gold.json").read_text(encoding="utf-8"))
SAMPLE_DIR = PROJECT.parent / "semiannual_cache"


def row(kind, code, current, previous, *, section=None, unit="元"):
    return {"kind": kind, "metric_code": code, "label": code,
            "values": [current, previous], "unit": unit,
            "source": {"document_id": "synthetic", "page_start": 2, "page_end": 2,
                       "section": section or kind, "row_bbox": [1, 2, 3, 4]}}


def parsed(rows, year=2025):
    return {"document_id": "synthetic", "report_type": "semiannual", "report_year": year,
            "company_name": "示例股份有限公司", "file_sha256": "f" * 64,
            "currency": "CNY", "rows": rows}


class HalfYearCoreTests(unittest.TestCase):
    def test_reversed_statement_columns_are_not_accepted_from_running_header(self):
        header = "公司2025年半年度报告全文\n项目 2024年半年度 2025年半年度"
        self.assertFalse(_header_order(header, "income_statement", 2025))
        self.assertTrue(_header_order("项目 2025年半年度 2024年半年度", "income_statement", 2025))

    def test_distinct_h1_and_year_end_periods_and_no_annual_return(self):
        result = analyze_parsed_semiannual([parsed([
            row("summary_flow", "revenue", "200", "100"),
            row("summary_stock", "inventory", "80", "40"),
            row("income_statement", "operating_cost", "100", "50"),
        ])])
        facts = {(fact["period"], fact["metric_code"]): fact for fact in result["facts"]}
        self.assertEqual(facts[("2025H1", "revenue")]["period_start"], "2025-01-01")
        self.assertEqual(facts[("2024H1", "revenue")]["period_end"], "2024-06-30")
        self.assertEqual(facts[("2024-12-31", "inventory")]["period_type"], "instant")
        metrics = {metric["metric_code"]: metric for metric in result["metrics"]}
        self.assertEqual(Decimal(metrics["revenue_yoy"]["value"]), Decimal("100"))
        self.assertEqual(Decimal(metrics["inventory_days"]["value"]), Decimal("108.6"))
        unavailable = {metric["metric_code"]: metric for metric in result["unavailable_metrics"]}
        self.assertEqual(unavailable["return_on_assets"]["reason_code"], "ANNUAL_METRIC_NOT_APPLICABLE")

    def test_conflict_float_and_zero_denominator_withhold_metrics(self):
        source = parsed([
            row("summary_flow", "revenue", "100", "0"),
            row("income_statement", "revenue", "101", "0", section="consolidated_income_statement"),
            row("income_statement", "operating_cost", 10.1, "8"),
        ])
        result = analyze_parsed_semiannual([source])
        facts = {(fact["period"], fact["metric_code"]): fact for fact in result["facts"]}
        self.assertEqual(facts[("2025H1", "revenue")]["quality"], "conflict")
        self.assertTrue(any(warning["code"] == "INVALID_AMOUNT" for warning in result["warnings"]))
        unavailable = {metric["metric_code"]: metric for metric in result["unavailable_metrics"]}
        self.assertEqual(unavailable["revenue_yoy"]["reason_code"], "CONFLICTING_INPUT")
        self.assertEqual(unavailable["gross_margin"]["reason_code"], "CONFLICTING_INPUT")
        self.assertFalse(any(metric["metric_code"] in {"revenue_yoy", "gross_margin"}
                             for metric in result["metrics"]))

    def test_leap_year_h1_uses_182_days_and_negative_base_is_not_growth(self):
        result = analyze_parsed_semiannual([parsed([
            row("summary_stock", "inventory", "50", "50"),
            row("income_statement", "operating_cost", "91", "80"),
            row("summary_flow", "operating_cash_flow", "-20", "-10"),
        ], year=2024)])
        metrics = {metric["metric_code"]: metric for metric in result["metrics"]}
        self.assertEqual(Decimal(metrics["inventory_days"]["value"]), Decimal("100"))
        unavailable = {metric["metric_code"]: metric for metric in result["unavailable_metrics"]}
        self.assertEqual(unavailable["operating_cash_flow_yoy"]["reason_code"], "NONPOSITIVE_INPUT")


class HalfYearGoldTests(unittest.TestCase):
    def test_three_official_reports_match_manual_values_and_evidence(self):
        paths = [next(SAMPLE_DIR.glob(sample["stock_code"] + "*.pdf"), None) for sample in GOLD["samples"]]
        if any(path is None for path in paths):
            self.skipTest("Official half-year PDFs not installed in sibling semiannual_cache")
        analysis = analyze_semiannual_reports(paths)
        self.assertFalse(analysis["warnings"])
        self.assertEqual(len(analysis["documents"]), 3)
        docs = {document["file_sha256"]: document for document in analysis["documents"]}
        facts = {(fact["document_id"], fact["period"], fact["metric_code"]): fact
                 for fact in analysis["facts"]}
        metrics = {(metric["document_id"], metric["metric_code"]): metric
                   for metric in analysis["metrics"]}
        for sample in GOLD["samples"]:
            doc = docs[sample["sha256"]]
            self.assertEqual(doc["company_name"], sample["company_name"])
            self.assertEqual(doc["assurance"], sample["assurance"])
            for target in sample["facts"]:
                fact = facts[(doc["document_id"], target["period"], target["code"])]
                self.assertEqual(Decimal(fact["value"]), Decimal(target["value"]))
                self.assertIn(target["page"], [source["page_start"] for source in fact["sources"]])
                self.assertNotEqual(fact["quality"], "conflict")
            gold = {(target["period"], target["code"]): int(target["value"])
                    for target in sample["facts"]}
            revenue = gold[("2025H1", "revenue")]
            prior_revenue = gold[("2024H1", "revenue")]
            cost = gold[("2025H1", "operating_cost")]
            cfo = gold[("2025H1", "operating_cash_flow")]
            capex = gold[("2025H1", "capital_expenditure_cash_paid")]
            checks = {
                "revenue_yoy": Fraction(100 * (revenue - prior_revenue), prior_revenue),
                "gross_margin": Fraction(100 * (revenue - cost), revenue),
                "inventory_days": Fraction(181 * (gold[("2024-12-31", "inventory")] +
                                                   gold[("2025-06-30", "inventory")]), 2 * cost),
            }
            with localcontext() as context:
                context.prec = 60
                for code, expected in checks.items():
                    actual = Decimal(metrics[(doc["document_id"], code)]["value"])
                    independently_calculated = Decimal(expected.numerator) / Decimal(expected.denominator)
                    self.assertLess(abs(actual - independently_calculated), Decimal("1e-40"))
            self.assertEqual(Decimal(metrics[(doc["document_id"], "simplified_free_cash_flow")]["value"]),
                             Decimal(cfo - capex))
        china = docs[next(sample["sha256"] for sample in GOLD["samples"] if sample["stock_code"] == "601868")]
        self.assertIn("NONPOSITIVE_INPUT", [item["reason_code"] for item in analysis["unavailable_metrics"]
                                            if item["document_id"] == china["document_id"]
                                            and item["metric_code"] == "operating_cash_flow_yoy"])


class DispatcherTests(unittest.TestCase):
    def test_half_year_dispatch_keeps_existing_analysis_shape(self):
        inspection = {"document_id": "id", "file_sha256": "a" * 64, "file_name": "h1.pdf",
                      "page_count": 100, "pages": [{"lines": [{"text": "2025年半年度报告"}]}]}
        analysis = {"documents": [{"sections_found": ["h1_summary"]}], "warnings": []}
        with patch("financial_engine.pipeline.read_pdf_document", return_value=inspection), \
             patch("financial_engine.interim.analyze_interim_pdf", return_value=analysis) as parser:
            result = analyze_uploaded_document("h1.pdf", metadata={"title": "重大投资公告"})
        parser.assert_called_once_with('h1.pdf','semiannual',None)
        self.assertEqual(result["document_type"], "half_year_report")
        self.assertEqual(result["coverage"]["sections"], ["h1_summary"])


if __name__ == "__main__":
    unittest.main()
