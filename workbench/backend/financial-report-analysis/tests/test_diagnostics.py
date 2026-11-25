import copy
import unittest
from decimal import Decimal

from financial_engine.core import analyze_parsed_documents
from financial_engine.note_adapter import _aging_table_rows, _inventory_table_rows
from financial_engine.pdf_adapter import _statement_rows
from test_leverage import FakePage
from test_note_adapter import Page, Table
from test_risk import complete_document


class Grid(Page):
    def __init__(self, lines, grid, bbox=(10,50,400,90)):
        super().__init__(*lines)
        self.table=Table(grid)
        self.table.bbox=bbox
    def find_tables(self):
        return [self.table]


class DiagnosticsTests(unittest.TestCase):
    def test_prior_same_metric_and_percentage_point_delta_use_same_formula(self):
        result=analyze_parsed_documents([complete_document()])
        margin=next(item for item in result['metrics'] if item['metric_code']=='gross_margin')
        self.assertEqual(Decimal(margin['comparison']['value']),20)
        self.assertEqual(margin['comparison']['period'],'2024')
        self.assertEqual(margin['comparison']['change_unit'],'percentage_points')
        self.assertEqual(margin['comparison']['document_id'],margin['document_id'])
        growth=next(item for item in result['metrics'] if item['metric_code']=='revenue_yoy')
        self.assertEqual(growth['comparison']['status'],'unavailable')
        self.assertEqual(growth['comparison']['missing_inputs'],[{'period':'2023','code':'revenue'}])
        self.assertEqual(len(result['metrics']),len({item['metric_id'] for item in result['metrics']}))

    def test_quarter_comparison_is_q3_growth_not_last_year_annual_growth(self):
        doc=complete_document()
        doc['rows'].append({'kind':'quarterly','label':'营业收入','metric_code':'revenue','unit':'元','values':['10','20','30','50'],'source':{'page_start':1}})
        result=analyze_parsed_documents([doc])
        metric=next(item for item in result['metrics'] if item['metric_code']=='revenue_q4_qoq')
        self.assertEqual(metric['comparison']['period'],'2025Q3')
        self.assertEqual(Decimal(metric['comparison']['value']),50)
        self.assertEqual(metric['comparison']['input_fact_ids'],['synthetic-risk:2025Q3:revenue','synthetic-risk:2025Q2:revenue'])

    def test_negative_base_retains_amount_change_without_inventing_growth_percent(self):
        doc=complete_document()
        doc['rows'].append({'kind':'quarterly','label':'归母净利润','metric_code':'attributable_net_profit','unit':'元','values':['1','2','-4','-7'],'source':{'page_start':2}})
        result=analyze_parsed_documents([doc])
        unavailable=next(item for item in result['unavailable_metrics'] if item['metric_code']=='attributable_net_profit_q4_qoq')
        self.assertIsNone(unavailable['value'])
        self.assertEqual(unavailable['reason_code'],'NONPOSITIVE_INPUT')
        self.assertEqual(Decimal(unavailable['amount_comparison']['absolute_change']),-3)
        self.assertEqual(unavailable['amount_comparison']['previous_period'],'2025Q3')

    def test_current_config_changes_rule_and_is_recorded_without_mutating_input(self):
        doc=complete_document();original=copy.deepcopy(doc)
        for threshold,status in ((2,'triggered'),(3,'not_triggered'),(4,'not_triggered')):
            result=analyze_parsed_documents([doc],{'minimum_negative_cash_quarters':threshold})
            cash=next(item for item in result['risk_assessments'] if item['rule_id']=='cash_collection_pressure')
            self.assertEqual(cash['status'],status)
            self.assertEqual(result['analysis_options']['minimum_negative_cash_quarters'],threshold)
            self.assertEqual(cash['conditions'][1]['observed']['longest_run'],2)
        self.assertEqual(doc,original)

    def test_multiline_consolidated_profit_not_parent_or_component_profit(self):
        class StatementPage(FakePage):
            def extract_text_lines(self):
                return [{**line,'x0':350 if line['text'].startswith('-100.00') else 20} for line in super().extract_text_lines()]
        pdf=type('PDF',(),{'pages':[StatementPage(['合并利润表','单位：元','项目 2025年度 2024年度',
            '五、净利润（净亏损以负号填列）','-100.00 200.00','1.持续经营净利润 -99 199',
            '母公司利润表','项目 2025年度 2024年度','四、净利润 -500 600'])]})()
        rows,_,_,_=_statement_rows(pdf,0,'doc',2025)
        profits=[row for row in rows if row['metric_code']=='net_profit']
        self.assertEqual(len(profits),1)
        self.assertEqual(profits[0]['values'],['-100.00','200.00'])
        self.assertIn('-100.00',profits[0]['source']['raw_text'])

    def test_detailed_aging_reconciles_both_columns_and_preserves_parts(self):
        grid=[['账龄','期末账面余额','期初账面余额'],['1年以内（含1年）','',''],
              ['1年以内','40','30'],['1至2年','20','10'],['2至3年','5','2'],['3年以上','',''],
              ['3至4年','3','3'],['4至5年','2',''],['5年以上','',''],['合计','70','45']]
        lines=['2025年年度报告','（1）按账龄披露','单位：元']
        page=Grid(lines,grid)
        rows,problem=_aging_table_rows(page,page.extract_text_lines(),1,'doc',2025,'元')
        self.assertIsNone(problem)
        over=next(row for row in rows if row['metric_code']=='accounts_receivable_aging_over_3y')
        self.assertEqual([Decimal(value) for value in over['values']],[5,3])
        self.assertEqual(len(over['source']['parts']),3)
        grid[-1][1]='71'
        rows,_=_aging_table_rows(page,page.extract_text_lines(),1,'doc',2025,'元')
        # A one-unit rounding tolerance is accepted; larger mismatches never are.
        grid[-1][1]='73'
        rows,_=_aging_table_rows(page,page.extract_text_lines(),1,'doc',2025,'元')
        self.assertFalse(rows)
        grid[-1][1]='70';grid[0][1],grid[0][2]=grid[0][2],grid[0][1]
        self.assertFalse(_aging_table_rows(page,page.extract_text_lines(),1,'doc',2025,'元')[0])

    def test_header_only_inventory_continuation_requires_geometry_scope_units_and_sums(self):
        header=[['项目','期末余额',None,None,'期初余额',None,None],
                [None,'账面余额','存货跌价准备','账面价值','账面余额','存货跌价准备','账面价值']]
        first=Grid(['2025年年度报告','（1）存货分类','单位：元'],header)
        second=Grid(['2025年年度报告','单位：元'],[
            ['原材料','70','5','65','60','','60'],['库存商品','30','5','25','40','2','38'],['合计','100','10','90','100','2','98']])
        def parse():return _inventory_table_rows(first,first.extract_text_lines(),1,'doc','元',second)
        rows,problem=parse();self.assertIsNone(problem)
        self.assertEqual(rows[0]['source']['page_start'],2)
        self.assertEqual(rows[0]['source']['heading_page'],1)
        second.table.bbox=(40,50,430,90);self.assertFalse(parse()[0])
        second.table.bbox=(10,50,400,90);second.texts=['2025年年度报告','十八、母公司财务报表主要项目注释']
        self.assertFalse(parse()[0])
        second.texts=['2025年年度报告','单位：千元'];self.assertFalse(parse()[0])


if __name__=='__main__':unittest.main()
