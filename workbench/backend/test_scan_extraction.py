import unittest
from scan_review_extension.extract import _join_wrapped_labels, _AMOUNT, _has_restatement_columns
from financial_engine.core import parse_amount


def line(text,x,y):
    return {'text':text,'bbox':[x,y,x+60,y+10],'confidence':.99,'method':'ocr','page':1}


class WrappedLabelTests(unittest.TestCase):
    def test_adjustment_footnote_does_not_block_normal_year_columns(self):
        lines=[line('2025年',200,30),line('2024年',300,30),line('100',200,60),line('80',300,60),
               line('调整后加权平均净资产收益率计算说明',20,100)]
        self.assertFalse(_has_restatement_columns(lines,2025))

    def test_pre_post_adjustment_header_is_ambiguous(self):
        lines=[line('2025年',200,30),line('2024年',300,30),line('调整前',270,45),line('调整后',330,45),
               line('100',200,70),line('80',300,70)]
        self.assertTrue(_has_restatement_columns(lines,2025))
    def test_negative_brackets_zero_and_grouping_are_preserved(self):
        for raw in ['(1,234.56)','（1,234.56）','-0.01','0']:
            self.assertIsNotNone(_AMOUNT.fullmatch(raw))
            self.assertIsNotNone(parse_amount(raw,'万元'))
        for raw in ['1,23.00','1e9','NaN']:
            self.assertIsNone(_AMOUNT.fullmatch(raw))
    def test_wrapped_label_join_preserves_union_anchor_and_amount_strings(self):
        lines=[line('2025年',200,30),line('2024年',300,30),
               line('归属于上市公司股东',20,54),line('的净利润',20,66),
               line('100.01',200,60),line('80.00',300,60)]
        result=_join_wrapped_labels(lines,2025)
        joined=next(l for l in result if l['text']=='归属于上市公司股东的净利润')
        self.assertEqual(joined['bbox'],[20,54,80,76])
        self.assertIn(lines[-1],result);self.assertIn(lines[-2],result)

    def test_adjacent_different_financial_rows_are_not_joined(self):
        lines=[line('2025年',200,30),line('2024年',300,30),
               line('营业收入',20,60),line('100',200,60),line('80',300,60),
               line('归属于上市公司股东的净利润',20,80),line('10',200,80),line('8',300,80)]
        self.assertEqual(_join_wrapped_labels(lines,2025),lines)

    def test_nonrecurring_profit_label_is_not_misclassified_as_net_profit(self):
        lines=[line('2025年',200,30),line('2024年',300,30),
               line('归属于上市公司股东',20,50),line('的扣除非经常性损益',20,60),line('的净利润',20,70),
               line('100',200,60),line('80',300,60)]
        self.assertEqual(_join_wrapped_labels(lines,2025),lines)
