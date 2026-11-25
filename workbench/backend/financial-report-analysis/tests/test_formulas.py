"""Financial formula checks use explicit statement amounts and known answers."""

import unittest
from decimal import Decimal

from financial_engine.core import analyze_parsed_documents, parse_amount


def row(kind, code, current, previous=None, unit="万元"):
    values = [current] if previous is None else [current, previous]
    return {
        "kind": kind,
        "metric_code": code,
        "label": code,
        "values": values,
        "unit": unit,
        "source": {"page_start": 2, "page_end": 2, "section": kind},
    }


def report(year, rows):
    return {"document_id": f"formula-{year}", "report_year": year, "rows": rows}


class FormulaTests(unittest.TestCase):
    def test_annual_profit_cash_turnover_and_custom_metrics(self):
        rows = [
            row("income_statement", "revenue", "200", "100"),
            row("income_statement", "operating_cost", "120", "80"),
            row("income_statement", "net_profit", "20", "10"),
            row("income_statement", "r_and_d_expense", "10", "8"),
            row("annual", "attributable_net_profit", "18", "9"),
            row("annual", "attributable_net_profit_ex_nonrecurring", "15", "8"),
            row("cash_flow_statement", "operating_cash_flow", "-10", "5"),
            row("cash_flow_statement", "capital_expenditure_cash_paid", "5", "4"),
            row("balance_sheet", "inventory", "30", "20"),
            row("balance_sheet", "accounts_receivable", "20", "10"),
            row("balance_sheet", "total_assets", "600", "500"),
            row("annual", "attributable_equity", "400", "300"),
            row("balance_sheet", "fixed_assets", "100", "90"),
            row("balance_sheet", "construction_in_progress", "50", "30"),
            row("note", "inventory_gross", "40", "30"),
            row("note", "inventory_impairment_allowance", "10", "10"),
            row("note", "accounts_receivable_gross", "25", "15"),
            row("note", "accounts_receivable_aging_1_to_2y", "1", "1"),
            row("note", "accounts_receivable_aging_2_to_3y", "2", "1"),
            row("note", "accounts_receivable_aging_over_3y", "3", "1"),
        ]
        result = analyze_parsed_documents([report(2025, rows)])
        metrics = {item["metric_code"]: item for item in result["metrics"]}
        expected = {
            "gross_margin": Decimal("40"),
            "consolidated_net_margin": Decimal("10"),
            "adjusted_attributable_net_margin": Decimal("7.5"),
            "cash_to_consolidated_profit": Decimal("-0.5"),
            "simplified_free_cash_flow": Decimal("-150000"),
            "inventory_days": Decimal("365") * Decimal("25") / Decimal("120"),
            "receivables_days_approx": Decimal("27.375"),
            "return_on_assets": Decimal("20") / Decimal("550") * Decimal("100"),
            "return_on_attributable_equity": Decimal("18") / Decimal("350") * Decimal("100"),
            "fixed_assets_cip_to_assets": Decimal("25"),
            "r_and_d_expense_to_revenue": Decimal("5"),
            "inventory_impairment_to_gross": Decimal("25"),
            "receivables_over_1y_to_gross": Decimal("24"),
        }
        for code, answer in expected.items():
            with self.subTest(code=code):
                self.assertIn(code, metrics)
                self.assertLess(abs(Decimal(metrics[code]["value"]) - answer), Decimal("1e-25"))
                self.assertEqual(metrics[code]["status"], "calculated")
                self.assertTrue(metrics[code]["input_fact_ids"])
        self.assertEqual(metrics["simplified_free_cash_flow"]["unit"], "CNY")
        self.assertEqual(len(metrics["inventory_days"]["input_fact_ids"]), 3)

    def test_zero_negative_missing_and_conflicting_inputs_are_explained(self):
        rows = [
            row("income_statement", "revenue", "0", "0"),
            row("income_statement", "operating_cost", "0", "10"),
            row("income_statement", "net_profit", "0", "1"),
            row("cash_flow_statement", "operating_cash_flow", "-4", "2"),
            row("cash_flow_statement", "capital_expenditure_cash_paid", "5", "1"),
            row("balance_sheet", "inventory", "-1", "2"),
            row("balance_sheet", "accounts_receivable", "2", "1"),
        ]
        result = analyze_parsed_documents([report(2025, rows)])
        unavailable = {item["metric_code"]: item for item in result["unavailable_metrics"]}
        self.assertEqual(unavailable["revenue_yoy"]["reason_code"], "DIVISION_BY_ZERO")
        self.assertEqual(unavailable["gross_margin"]["reason_code"], "DIVISION_BY_ZERO")
        self.assertEqual(unavailable["cash_to_consolidated_profit"]["reason_code"], "DIVISION_BY_ZERO")
        self.assertEqual(unavailable["inventory_days"]["reason_code"], "DIVISION_BY_ZERO")
        self.assertEqual(unavailable["adjusted_attributable_net_margin"]["reason_code"], "MISSING_INPUT")
        metrics = {item["metric_code"]: item for item in result["metrics"]}
        self.assertEqual(Decimal(metrics["simplified_free_cash_flow"]["value"]), Decimal("-90000"))
        self.assertNotIn("gross_margin", metrics)
        self.assertNotIn("cash_to_consolidated_profit", metrics)

        conflict = analyze_parsed_documents([report(2025, [
            row("income_statement", "revenue", "200", "100"),
            row("income_statement", "revenue", "201", "100"),
            row("income_statement", "operating_cost", "120", "80"),
        ])])
        unavailable = {item["metric_code"]: item for item in conflict["unavailable_metrics"]}
        self.assertEqual(unavailable["gross_margin"]["reason_code"], "CONFLICTING_INPUT")

    def test_leap_year_uses_actual_days_and_float_amount_is_rejected(self):
        result = analyze_parsed_documents([report(2024, [
            row("balance_sheet", "inventory", "10", "10"),
            row("income_statement", "operating_cost", "366", "300"),
        ])])
        inventory_days = next(item for item in result["metrics"] if item["metric_code"] == "inventory_days")
        self.assertEqual(Decimal(inventory_days["value"]), Decimal("10"))
        self.assertIsNone(parse_amount(0.1, "元"))
        self.assertIsNone(parse_amount("1,2", "元"))
        self.assertIsNone(parse_amount("NaN", "元"))
        self.assertIsNone(parse_amount("Infinity", "元"))
        self.assertEqual(parse_amount(Decimal("1E+3"), "元"), Decimal("1000"))
        self.assertEqual(parse_amount("123456789012345678901234567890.12", "万元"),
                         Decimal("1234567890123456789012345678901200.00"))

    def test_negative_balances_and_inconsistent_components_are_withheld(self):
        result = analyze_parsed_documents([report(2025, [
            row("income_statement", "revenue", "100", "90"),
            row("income_statement", "operating_cost", "60", "55"),
            row("balance_sheet", "inventory", "-1", "2"),
            row("balance_sheet", "fixed_assets", "80", "70"),
            row("balance_sheet", "construction_in_progress", "30", "20"),
            row("balance_sheet", "total_assets", "100", "90"),
            row("cash_flow_statement", "operating_cash_flow", "10", "9"),
            row("cash_flow_statement", "capital_expenditure_cash_paid", "-2", "1"),
        ])])
        unavailable = {item["metric_code"]: item for item in result["unavailable_metrics"]}
        self.assertEqual(unavailable["inventory_days"]["reason_code"], "NEGATIVE_BALANCE")
        self.assertEqual(unavailable["simplified_free_cash_flow"]["reason_code"], "NEGATIVE_BALANCE")
        self.assertEqual(unavailable["fixed_assets_cip_to_assets"]["reason_code"], "INCONSISTENT_COMPONENTS")
        self.assertEqual(unavailable["inventory_days"]["value"], None)


if __name__ == "__main__":
    unittest.main()
