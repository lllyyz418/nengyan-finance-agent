"""Consolidated liabilities: extraction, exact formula and sourced history."""
import unittest
from decimal import Decimal

from financial_engine.core import analyze_parsed_documents, metric_code
from financial_engine.history import build_annual_history
from financial_engine.pdf_adapter import _statement_code, _statement_rows, _statement_column_order


def row(code, value, prior=None, unit='元', **extra):
    return {'kind':'balance_sheet', 'metric_code':code, 'label':code, 'unit':unit,
            'values':[value] if prior is None else [value,prior],
            'source':{'page_start':2,'page_end':2,'section':'consolidated_balance_sheet'}, **extra}


def report(year, assets, liabilities, prior_assets=None, prior_liabilities=None):
    return {'document_id':f'leverage-{year}', 'company_name':'测试股份有限公司',
            'security_code':'300750','report_type':'annual','report_year':year,
            'rows':[row('total_assets',assets,prior_assets),row('total_liabilities',liabilities,prior_liabilities)]}


class FakePage:
    width=600
    def __init__(self, texts):
        self.texts=texts
    def extract_text(self):
        return '\n'.join(self.texts)
    def extract_text_lines(self):
        return [{'text':text,'x0':20,'x1':580,'top':index*25+20,'bottom':index*25+35} for index,text in enumerate(self.texts)]
    def close(self):
        pass


class LeverageTests(unittest.TestCase):
    def metric(self, result):
        return next((item for item in result['metrics'] if item['metric_code']=='liabilities_to_assets'),None)

    def test_exact_percent_and_instant_sourced_facts(self):
        result=analyze_parsed_documents([report(2025,'1000','600','900','450')])
        metric=self.metric(result)
        self.assertEqual(Decimal(metric['value']),Decimal('60'))
        self.assertEqual(metric['unit'],'percent')
        self.assertEqual(metric['period'],'2025')
        self.assertEqual(len(metric['input_fact_ids']),2)
        fact=next(item for item in result['facts'] if item['metric_code']=='total_liabilities' and item['period']=='2025')
        self.assertEqual(fact['statement_scope'],'consolidated')
        self.assertEqual(fact['period_type'],'instant')
        self.assertEqual(fact['period_end'],'2025-12-31')
        self.assertIsNone(fact['period_start'])
        self.assertEqual(fact['sources'][0]['page_start'],2)

    def test_different_explicit_units_and_zero_liabilities(self):
        doc=report(2025,'3','100000000')
        doc['rows'][0]['unit']='亿元'
        result=analyze_parsed_documents([doc])
        self.assertLess(abs(Decimal(self.metric(result)['value'])-Decimal(100)/Decimal(3)),Decimal('1e-25'))
        self.assertEqual(Decimal(self.metric(analyze_parsed_documents([report(2025,'100','0')]))['value']),0)

    def test_missing_negative_zero_and_conflicting_inputs_are_not_zero_filled(self):
        cases=[(report(2025,'0','10'),'DIVISION_BY_ZERO'),
               (report(2025,'-1','10'),'NONPOSITIVE_INPUT'),
               (report(2025,'100','-10'),'NEGATIVE_BALANCE'),
               (report(2025,'100',None),'MISSING_INPUT')]
        conflict=report(2025,'100','50')
        conflict['rows'].append(row('total_liabilities','51'))
        cases.append((conflict,'CONFLICTING_INPUT'))
        for doc, reason in cases:
            with self.subTest(reason=reason):
                result=analyze_parsed_documents([doc])
                self.assertIsNone(self.metric(result))
                unavailable=next(item for item in result['unavailable_metrics'] if item['metric_code']=='liabilities_to_assets')
                self.assertEqual(unavailable['reason_code'],reason)
                self.assertIsNone(unavailable['value'])

    def test_above_100_percent_is_retained_and_never_inferred_from_parent_equity(self):
        self.assertEqual(Decimal(self.metric(analyze_parsed_documents([report(2025,'100','120')]))['value']),120)
        doc=report(2025,'100',None)
        doc['rows'].append(row('attributable_equity','40'))
        self.assertIsNone(self.metric(analyze_parsed_documents([doc])))
        for index in (0,1):
            doc=report(2025,'100','60')
            doc['rows'][index]['statement_scope']='parent'
            self.assertIsNone(self.metric(analyze_parsed_documents([doc])))

    def test_liability_total_does_not_match_components_or_total_liabilities_and_equity(self):
        for label in ('负债合计','负债总计','负债总额'):
            self.assertEqual(_statement_code(label+' 600 450','balance_sheet'),'total_liabilities')
            self.assertEqual(metric_code(label,'balance_sheet'),'total_liabilities')
        for label in ('流动负债合计','非流动负债合计','负债和所有者权益总计','负债及股东权益总计'):
            self.assertIsNone(_statement_code(label+' 1000 900','balance_sheet'))
            self.assertIsNone(metric_code(label,'balance_sheet'))

    def test_consolidated_continuation_page_retains_units_and_ignores_parent(self):
        pdf=type('PDF',(),{'pages':[
            FakePage(['合并资产负债表','单位：万元','项目 2025年12月31日 2024年12月31日','资产总计 1,000.00 900.00']),
            FakePage(['负债合计 600.00 450.00','负债和所有者权益总计 1,000.00 900.00','母公司资产负债表','项目 2025年12月31日 2024年12月31日','负债合计 999.00 888.00'])]})()
        rows,_,_,_= _statement_rows(pdf,0,'example',2025)
        liabilities=[item for item in rows if item['metric_code']=='total_liabilities']
        self.assertEqual(len(liabilities),1)
        self.assertEqual(liabilities[0]['values'],['600.00','450.00'])
        self.assertEqual(liabilities[0]['unit'],'万元')
        self.assertEqual(liabilities[0]['source']['page_start'],2)
        self.assertEqual(liabilities[0]['statement_scope'],'consolidated')
        self.assertEqual(len(liabilities[0]['source']['row_bbox']),4)

    def test_unverified_or_reversed_columns_withhold_liability_values(self):
        for header in ('项目 本年 上年','项目 2024年12月31日 2025年12月31日'):
            pdf=type('PDF',(),{'pages':[FakePage(['合并资产负债表','单位：元',header,'负债合计 600 450'])]})()
            rows,_,_,_=_statement_rows(pdf,0,'example',2025)
            self.assertFalse(any(item['metric_code']=='total_liabilities' for item in rows))

    def test_explicit_year_end_and_same_year_opening_dates(self):
        for opening in ('2023年1月1日', '2023年01月01日'):
            header=f'项目 2023年12月31日 {opening}'
            self.assertTrue(_statement_column_order(header,'balance_sheet',2023,header))
            self.assertFalse(_statement_column_order(f'项目 {opening} 2023年12月31日','balance_sheet',2023,header))
            pdf=type('PDF',(),{'pages':[FakePage(['合并资产负债表','单位：万元',header,'资产总计 1000 900','负债合计 600 450'])]})()
            rows,_,_,_=_statement_rows(pdf,0,'example',2023)
            result=analyze_parsed_documents([{'document_id':'example','report_type':'annual','report_year':2023,'rows':rows}])
            self.assertEqual(Decimal(self.metric(result)['value']),60)

    def test_history_uses_original_years_and_percentage_points(self):
        result=analyze_parsed_documents([report(2024,'1000','500','900','450'),report(2025,'1000','600','1000','500')])
        history=build_annual_history(result,'300750')
        self.assertEqual([Decimal(item['value']) for item in history['series']['liabilities_to_assets']],[50,60])
        change=next(item for item in history['changes'] if item['metric_code']=='liabilities_to_assets')
        self.assertEqual(change['unit'],'percentage_points')
        self.assertEqual(Decimal(change['absolute_change']),10)
        self.assertNotIn('relative_change_percent',change)
        altered=report(2025,'1000','600','1000','510')
        history=build_annual_history(analyze_parsed_documents([report(2024,'1000','500'),altered]),'300750')
        change=next(item for item in history['changes'] if item['metric_code']=='liabilities_to_assets')
        self.assertEqual(change['reason_code'],'RESTATED_COMPARATIVE_CONFLICT')
        self.assertNotIn('absolute_change',change)


if __name__=='__main__':
    unittest.main()
