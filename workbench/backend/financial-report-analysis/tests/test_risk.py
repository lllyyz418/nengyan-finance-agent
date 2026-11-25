"""Review-rule tests use sourced synthetic facts with known outcomes."""

import unittest
from decimal import Decimal

from financial_engine.core import analyze_parsed_documents


def row(kind, code, current, previous=None):
    return {
        "kind": kind,
        "metric_code": code,
        "label": code,
        "values": [current] if previous is None else [current, previous],
        "unit": "元",
        "source": {"page_start": 1, "page_end": 1, "section": kind},
    }


def complete_document():
    return {
        "document_id": "synthetic-risk",
        "company_name": "测试公司",
        "report_year": 2025,
        "rows": [
            row("income_statement", "revenue", "110", "100"),
            row("income_statement", "operating_cost", "90", "80"),
            row("income_statement", "net_profit", "10", "9"),
            row("annual", "operating_cash_flow", "4", "5"),
            {**row("quarterly", "operating_cash_flow", "-1"), "values": ["-1", "-2", "3", "4"]},
            row("note", "accounts_receivable_gross", "30", "20"),
            row("note", "accounts_receivable_aging_1_to_2y", "3", "1"),
            row("note", "accounts_receivable_aging_2_to_3y", "2", "1"),
            row("note", "accounts_receivable_aging_over_3y", "1", "0"),
            row("note", "inventory_gross", "50", "40"),
            row("note", "inventory_impairment_allowance", "5", "2"),
            row("cash_flow_statement", "capital_expenditure_cash_paid", "9", "7"),
            row("balance_sheet", "fixed_assets", "25", "20"),
            row("balance_sheet", "construction_in_progress", "15", "10"),
            row("balance_sheet", "total_assets", "110", "100"),
        ],
    }


class RiskTests(unittest.TestCase):
    def test_four_joint_screens_trigger_with_traceable_conditions(self):
        result = analyze_parsed_documents([complete_document()])
        assessments = {item["rule_id"]: item for item in result["risk_assessments"]}
        expected = {
            "cash_collection_pressure", "inventory_margin_pressure",
            "receivables_aging_pressure", "capital_expansion_cash_pressure",
        }
        self.assertEqual({code for code in expected if assessments[code]["status"] == "triggered"}, expected)
        for code in expected:
            with self.subTest(rule=code):
                self.assertTrue(assessments[code]["conditions"])
                self.assertTrue(all(condition["passed"] for condition in assessments[code]["conditions"]))
                self.assertTrue(assessments[code]["evidence_fact_ids"])
        self.assertEqual(assessments["cash_collection_pressure"]["conditions"][1]["observed"]["longest_run"], 2)
        cash_gap = assessments["cash_collection_pressure"]["conditions"][2]["observed"]["growth_gap_percentage_points"]
        inventory_gap = assessments["inventory_margin_pressure"]["conditions"][0]["observed"]["growth_gap_percentage_points"]
        allowance_change = assessments["inventory_margin_pressure"]["conditions"][1]["observed"]["share_change_percentage_points"]
        aging = assessments["receivables_aging_pressure"]
        self.assertEqual(Decimal(cash_gap), Decimal("40"))
        self.assertEqual(Decimal(inventory_gap), Decimal("15"))
        self.assertEqual(Decimal(allowance_change), Decimal("5"))
        self.assertEqual(Decimal(aging["conditions"][1]["observed"]["share_change_percentage_points"]), Decimal("10"))
        self.assertFalse(aging["materiality_assessed"])
        self.assertTrue(assessments["capital_expansion_cash_pressure"]["evidence_metric_ids"])
        self.assertEqual({item["category"] for item in result["findings"]}, expected)
        self.assertEqual(assessments["r_and_d_capitalization_policy"]["status"], "insufficient_data")
        self.assertEqual(assessments["depreciation_policy_change"]["status"], "insufficient_data")
        self.assertTrue(assessments["depreciation_policy_change"]["missing_or_invalid"])

    def test_unmet_conditions_and_missing_inputs_are_distinguished(self):
        document = complete_document()
        quarterly = next(row for row in document["rows"] if row["kind"] == "quarterly")
        quarterly["values"] = ["-1", "3", "-1", "3"]
        annual_cash = next(row for row in document["rows"] if row["kind"] == "annual" and row["metric_code"] == "operating_cash_flow")
        annual_cash["values"][0] = "4"
        result = analyze_parsed_documents([document])
        cash = next(item for item in result["risk_assessments"] if item["rule_id"] == "cash_collection_pressure")
        self.assertEqual(cash["status"], "not_triggered")
        self.assertEqual(cash["unmet_condition_codes"], ["consecutive_negative_quarterly_cfo"])
        self.assertNotIn("cash_collection_pressure", {item["category"] for item in result["findings"]})

        quarterly["values"] = ["-1", "-2"]
        result = analyze_parsed_documents([document])
        cash = next(item for item in result["risk_assessments"] if item["rule_id"] == "cash_collection_pressure")
        self.assertEqual(cash["status"], "insufficient_data")
        self.assertTrue(any(item["code"] == "operating_cash_flow" and item["period"] == "2025Q3"
                            for item in cash["missing_or_invalid"]))

    def test_quarter_reconciliation_and_configuration_gate_cash_rule(self):
        document = complete_document()
        annual_cash = next(row for row in document["rows"] if row["kind"] == "annual" and row["metric_code"] == "operating_cash_flow")
        annual_cash["values"][0] = "8"
        result = analyze_parsed_documents([document])
        cash = next(item for item in result["risk_assessments"] if item["rule_id"] == "cash_collection_pressure")
        self.assertEqual(cash["status"], "insufficient_data")
        self.assertIn("QUARTER_SUM_MISMATCH", {item["reason"] for item in cash["missing_or_invalid"]})
        with self.assertRaises(ValueError):
            analyze_parsed_documents([document], {"minimum_negative_cash_quarters": 1})
        with self.assertRaises(ValueError):
            analyze_parsed_documents([document], {"minimum_negative_cash_quarters": 2.0})

    def test_tiny_directional_aging_signal_preserves_decimal_gap_and_no_materiality_claim(self):
        document = {
            "document_id": "tiny-aging", "company_name": "测试公司", "report_year": 2025,
            "rows": [
                row("income_statement", "revenue", "100000000000000000000", "100000000000000000000"),
                row("note", "accounts_receivable_gross", "100000000000000000001", "100000000000000000000"),
                row("note", "accounts_receivable_aging_1_to_2y", "10000000000000000001", "10000000000000000000"),
                row("note", "accounts_receivable_aging_2_to_3y", "0", "0"),
                row("note", "accounts_receivable_aging_over_3y", "0", "0"),
            ],
        }
        result = analyze_parsed_documents([document])
        aging = next(item for item in result["risk_assessments"]
                     if item["rule_id"] == "receivables_aging_pressure")
        self.assertEqual(aging["status"], "triggered")
        self.assertFalse(aging["materiality_assessed"])
        for condition in aging["conditions"]:
            key = ("growth_gap_percentage_points" if condition["condition_code"]
                   == "gross_receivables_growth_above_revenue_growth" else "share_change_percentage_points")
            gap = Decimal(condition["observed"][key])
            self.assertGreater(gap, 0)
            self.assertLess(gap, Decimal("0.000001"))


if __name__ == "__main__":
    unittest.main()
