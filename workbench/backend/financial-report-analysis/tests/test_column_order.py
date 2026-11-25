import unittest

from financial_engine.pdf_adapter import _row_parts, _statement_column_order


class StatementColumnOrderTests(unittest.TestCase):
    def test_adjacent_one_character_label_cell_is_rejoined_before_quarter_amounts(self):
        label, values = _row_parts([
            '经', '营活动产生的现金流量净额', '',
            '892,143,948.57', None, '1,480,421,763.04', None,
            '2,531,259,756.21', None, '2,588,306,198.97',
        ])
        self.assertEqual(label, '经营活动产生的现金流量净额')
        self.assertEqual(values, ['892,143,948.57', '1,480,421,763.04',
                                  '2,531,259,756.21', '2,588,306,198.97'])

    def test_current_and_prior_year_headers(self):
        self.assertTrue(_statement_column_order("附注七 2025年 2024年", "income_statement", 2025, ""))
        self.assertTrue(_statement_column_order("项目 2025年度 2024年度", "cash_flow_statement", 2025, ""))
        self.assertTrue(_statement_column_order("项目 期末余额 期初余额", "balance_sheet", 2025, "2025年12月31日"))

    def test_reversed_or_unidentified_headers_cannot_be_assumed(self):
        self.assertFalse(_statement_column_order("项目 2024年度 2025年度", "income_statement", 2025, ""))
        self.assertFalse(_statement_column_order("项目 期初余额 期末余额", "balance_sheet", 2025, "2025年12月31日"))
        self.assertIsNone(_statement_column_order("项目 期末余额 期初余额", "balance_sheet", 2025, ""))


if __name__ == "__main__":
    unittest.main()
