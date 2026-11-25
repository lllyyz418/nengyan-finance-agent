"""Scope and cross-page note tests for the 2024 CATL misclassification."""
import unittest

from financial_engine.note_adapter import parse_selected_notes


class Page:
    def __init__(self, *texts):
        self.texts = list(texts)

    def extract_text(self):
        return '\n'.join(self.texts)

    def extract_text_lines(self):
        return [{'text': text, 'x0': 10, 'top': i * 10, 'x1': 400, 'bottom': i * 10 + 8}
                for i, text in enumerate(self.texts)]

    def close(self):
        pass


class Pdf:
    def __init__(self, pages):
        self.pages = pages


class Table:
    def __init__(self, grid):
        self.grid = grid
        self.bbox = (10, 25, 400, 45)
        self.rows = [type('Row', (), {'bbox': (10, 25 + i, 400, 26 + i)})()
                     for i in range(len(grid))]

    def extract(self):
        return self.grid


class GridPage(Page):
    def __init__(self, grid):
        super().__init__('2025年年度报告', '七、合并财务报表项目注释',
                         '9、存货', '（1）存货分类', '单位：元',
                         '项目 期末余额 期初余额', '（2）存货跌价准备和合同履约成本减值准备',
                         '10、一年内到期的非流动资产')
        self.tables = [Table(grid)]
        self.tables[0].bbox = (10, 35, 400, 55)

    def find_tables(self):
        return self.tables


ROWS = ('账龄 期末账面余额 期初账面余额',
        '1年以内（含1年） 40 30', '1至2年 20 10', '2至3年 5 2',
        '3年以上 5 3', '合计 70 45', '（2）按坏账计提方法分类披露')


class NoteScopeTests(unittest.TestCase):
    def test_multiline_six_column_inventory_grid_recovers_sourced_totals(self):
        grid = [
            ['项目', '期末余额', None, None, '期初余额', None, None],
            [None, '账面余额', '存货跌价准备', '账面价值', '账面余额', '存货跌价准备', '账面价值'],
            ['原材料', '70', '5', '65', '60', '', '60'],
            ['库存商品', '30', '5', '25', '40', '2', '38'],
            ['合计', '1\n00', '10', '90', '100', '2', '98'],
        ]
        # The table bbox lies between the classification heading (top=30)
        # and the next note subsection (top=60).
        page = GridPage(grid)
        rows, warnings, found = parse_selected_notes(Pdf([page]), 0, 'doc', 2025, '元')
        self.assertIn('inventory', found)
        gross = next(item for item in rows if item['metric_code'] == 'inventory_gross')
        allowance = next(item for item in rows if item['metric_code'] == 'inventory_impairment_allowance')
        self.assertEqual(gross['values'], ['100', '100'])
        self.assertEqual(allowance['values'], ['10', '2'])
        self.assertEqual(gross['source']['extractor'], 'pdf_table')
        self.assertEqual(gross['source']['page_start'], 1)
        self.assertEqual(gross['source']['category_sum_difference_raw_unit'], ['0'] * 6)
        self.assertFalse(any(item['code'] == 'NOTE_TABLE_UNVERIFIED' for item in warnings))

    def test_inventory_grid_with_wrong_category_sum_is_not_used(self):
        grid = [
            ['项目', '期末余额', None, None, '期初余额', None, None],
            [None, '账面余额', '存货跌价准备', '账面价值', '账面余额', '存货跌价准备', '账面价值'],
            ['原材料', '70', '5', '65', '60', '', '60'],
            ['库存商品', '30', '5', '25', '40', '2', '38'],
            ['合计', '102', '10', '90', '100', '2', '98'],
        ]
        rows, warnings, found = parse_selected_notes(Pdf([GridPage(grid)]), 0, 'doc', 2025, '元')
        self.assertNotIn('inventory', found)
        self.assertFalse(any(item['metric_code'] == 'inventory_gross' for item in rows))
        self.assertTrue(any(item['code'] == 'NOTE_TABLE_UNVERIFIED' for item in warnings))

    def test_inventory_grid_with_reversed_period_columns_is_not_used(self):
        grid = [
            ['项目', '期初余额', None, None, '期末余额', None, None],
            [None, '账面余额', '存货跌价准备', '账面价值', '账面余额', '存货跌价准备', '账面价值'],
            ['原材料', '70', '5', '65', '60', '', '60'],
            ['库存商品', '30', '5', '25', '40', '2', '38'],
            ['合计', '100', '10', '90', '100', '2', '98'],
        ]
        rows, warnings, found = parse_selected_notes(Pdf([GridPage(grid)]), 0, 'doc', 2025, '元')
        self.assertNotIn('inventory', found)
        self.assertFalse(any(item['metric_code'] == 'inventory_gross' for item in rows))
        self.assertTrue(any(item['code'] == 'NOTE_TABLE_UNVERIFIED' for item in warnings))

    def test_consolidated_aging_continues_to_next_page(self):
        pdf = Pdf([Page('2025年年度报告', '七、合并财务报表项目注释',
                        '4、应收账款', '（1）按账龄披露', '单位：千元'),
                   Page('2025年年度报告', *ROWS),
                   Page('2025年年度报告', '十八、母公司财务报表主要项目注释',
                        '1、应收账款', '（1）按账龄披露', '单位：千元', *ROWS)])
        rows, warnings, found = parse_selected_notes(pdf, 0, 'doc', 2025, '千元')
        self.assertIn('aging', found)
        self.assertEqual(len([r for r in rows if r['metric_code'].startswith('accounts_receivable')]), 5)
        gross = next(r for r in rows if r['metric_code'] == 'accounts_receivable_gross')
        self.assertEqual(gross['values'], ['70', '45'])
        self.assertEqual(gross['source']['page_start'], 2)
        self.assertEqual(gross['source']['heading_page'], 1)

    def test_other_receivables_and_parent_tables_cannot_become_consolidated_ar(self):
        pdf = Pdf([Page('2025年年度报告', '七、合并财务报表项目注释',
                        '7、其他应收款', '（2）其他应收款', '2）按账龄披露', '单位：千元', *ROWS),
                   Page('2025年年度报告', '十八、母公司财务报表主要项目注释',
                        '1、应收账款', '（1）按账龄披露', '单位：千元', *ROWS)])
        rows, warnings, found = parse_selected_notes(pdf, 0, 'doc', 2025, '千元')
        self.assertNotIn('aging', found)
        self.assertFalse(any(r['metric_code'] == 'accounts_receivable_gross' for r in rows))
        self.assertTrue(any(w['code'] == 'NOTE_TABLE_MISSING' for w in warnings))

    def test_same_page_consolidated_ar_is_preserved(self):
        pdf = Pdf([Page('2025年年度报告', '七、合并财务报表主要项目注释',
                        '3、应收账款', '（1）按账龄披露', '单位：千元', *ROWS)])
        rows, _, found = parse_selected_notes(pdf, 0, 'doc', 2025, '千元')
        self.assertIn('aging', found)
        self.assertEqual(next(r for r in rows if r['metric_code']=='accounts_receivable_gross')['source']['page_start'], 1)

    def test_lower_numbered_subtable_does_not_end_consolidated_notes(self):
        pdf = Pdf([Page('2025年年度报告', '七、合并财务报表项目注释'),
                   Page('2025年年度报告', '一、合营企业'),
                   Page('2025年年度报告', '57、其他收益',
                        '项目 本期发生额 上期发生额',
                        '与资产相关的政府补助 10 8')])
        rows, _, found = parse_selected_notes(pdf, 0, 'doc', 2025, '千元')
        self.assertIn('government_grant_breakdown', found)
        grant = next(r for r in rows if r['metric_code']=='government_grant_other_income_asset_related')
        self.assertEqual(grant['values'], ['10','8'])

    def test_missing_consolidated_heading_never_uses_parent_note(self):
        pdf = Pdf([Page('2025年年度报告', '十八、母公司财务报表主要项目注释',
                        '1、应收账款', '（1）按账龄披露', '单位：千元', *ROWS)])
        rows, warnings, found = parse_selected_notes(pdf, 0, 'doc', 2025, '千元')
        self.assertFalse(rows)
        self.assertFalse(found)
        self.assertTrue(any(w['code']=='NOTE_SCOPE_UNVERIFIED' for w in warnings))


if __name__ == '__main__':
    unittest.main()
