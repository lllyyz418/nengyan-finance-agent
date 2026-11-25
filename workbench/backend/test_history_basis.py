"""Offline precision, provenance, saved-version and downstream acceptance."""
import copy
import json
import unittest
from decimal import Decimal
from unittest.mock import patch
from pathlib import Path
from fastapi.testclient import TestClient
import app as core
from financial_engine.basis_check import compare_facts,precision,VERSION
from financial_engine import analyze_parsed_documents,build_annual_history,evaluate_context_risks
from history_basis_view import enrich_history
from test_history import annual_document
from test_context_risk import context_fixture


def fact(raw='3,829.02',raw_unit='万元',**changes):
    scale={'元':1,'万元':10000,'千元':1000}.get(raw_unit,1)
    return {'fact_id':'f','document_id':'d','metric_code':'accounts_receivable_aging_2_to_3y',
            'value':str(Decimal(raw.replace(',',''))*scale),'unit':'CNY','period':'2023',
            'statement_scope':'consolidated','period_type':'instant','period_end':'2023-12-31',
            'raw_value':raw,'raw_unit':raw_unit,'raw_label':'两至三年应收账款',
            'quality':'extracted','sources':[{'page_start':166,'raw_text':'两至三年应收账款'}],**changes}


def history_fixture():
    older=annual_document(2023,'3,829.02','2,000.01','3,000.00','1,500.00')
    newer=annual_document(2024,'50,000','20,000','38,290','20,000')
    for row in older['rows']:row['unit']='万元'
    for row in newer['rows']:row['unit']='千元'
    analysis=analyze_parsed_documents([older,newer])
    return analysis


class HistoryBasisTests(unittest.TestCase):
    def test_real_example_precision_not_fixed_currency_cutoff(self):
        a,b=fact(),fact('38,290','千元')
        result=compare_facts(a,b)
        self.assertEqual(result['status'],'rounding_compatible')
        self.assertEqual(Decimal(result['difference']),-200)
        self.assertEqual(result['precision']['original']['step'],'100.00')
        self.assertEqual(result['precision']['later']['step'],'1000')
        self.assertEqual(Decimal(result['precision']['combined_half_step']),550)
        self.assertFalse(result['materiality_assessed'])
        self.assertEqual(compare_facts(fact('38290200','元'),fact('38290000','元'))['status'],'difference')

    def test_exact_values_unit_scope_period_and_zero(self):
        zero=fact('0.00','元');self.assertEqual(compare_facts(zero,zero)['status'],'matched')
        delta=compare_facts(zero,fact('1.00','元'))
        self.assertIsNone(delta['relative_difference_percent'])
        for changes in ({'unit':'USD'},{'statement_scope':'parent'},{'period':'2024'},{'period_type':'duration'},{'period_end':'2023-06-30'}):
            with self.subTest(changes=changes):self.assertEqual(compare_facts(fact(),fact(**changes))['status'],'unavailable')

    def test_same_precision_touching_boundary_not_waived(self):
        self.assertEqual(compare_facts(fact('100','元'),fact('101','元'))['status'],'difference')
        self.assertEqual(compare_facts(fact('0.95','元'),fact('1.0','元'))['status'],'rounding_compatible')
        self.assertEqual(compare_facts(fact('0.94','元'),fact('1.0','元'))['status'],'difference')

    def test_invalid_or_unverified_raw_precision_never_inferred(self):
        for changes in ({'raw_value':None},{'raw_unit':'未知单位'},{'raw_value':'1e3'},{'raw_value':'约3,829.02'},{'raw_value':'3,829.01'}):
            self.assertIsNone(precision(fact(**changes)))
        for value in (None,'NaN','Infinity',True,1.25):
            self.assertEqual(compare_facts(fact(),fact(value=value))['status'],'unavailable')
        for quality in ('conflict','unconfirmed','rejected'):
            self.assertEqual(compare_facts(fact(),fact(quality=quality))['reason_code'],'FACT_CONFLICT')
        self.assertEqual(compare_facts(fact(raw_value=None),fact('38,290','千元'))['reason_code'],'PRECISION_UNVERIFIED')

    def test_negative_and_large_decimal_values_are_exact_and_immutable(self):
        a=fact('-3,829.02');b=fact('-38,290','千元');before=copy.deepcopy([a,b])
        self.assertEqual(compare_facts(a,b)['status'],'rounding_compatible');self.assertEqual([a,b],before)
        result=compare_facts(fact('9007199254740993.01','元'),fact('9007199254740993.02','元'))
        self.assertEqual(Decimal(result['difference']),Decimal('.01'))
        self.assertEqual(result['status'],'difference')

    def test_large_difference_or_page_marker_does_not_claim_confirmed_adjustment(self):
        a=fact('30,907,043.40');b=fact('323,982,130','千元')
        result=compare_facts(a,b)
        self.assertEqual(result['reason_code'],'OUTSIDE_PRECISION_RANGE')
        self.assertEqual(Decimal(result['difference']),Decimal('14911696000'))
        b['comparative_restated']=True
        self.assertEqual(compare_facts(a,b)['reason_code'],'ADJUSTMENT_MARKED')
        self.assertNotEqual(compare_facts(a,b)['status'],'adjusted')

    def test_confirmed_adjustment_requires_bound_original_excerpt(self):
        a,b=fact('100.00','元'),fact('120.00','元')
        excerpt='2023年 两至三年应收账款，前期差错更正，调整前：100.00 元；调整后：120.00 元。'
        b['sources']=[{'page_start':12,'raw_text':excerpt}]
        self.assertEqual(compare_facts(a,b)['status'],'adjusted')
        for text in (excerpt.replace('120.00','121.00'),excerpt.replace('2023','2022'),excerpt.replace('两至三年应收账款','其他项目'),excerpt.replace('前期差错更正','未发生前期差错更正')):
            b['sources'][0]['raw_text']=text
            self.assertNotEqual(compare_facts(a,b)['status'],'adjusted')

    def test_rounding_no_longer_blocks_delta_and_keeps_original_series(self):
        analysis=history_fixture();before=copy.deepcopy(analysis)
        comparison=build_annual_history(analysis,'002594')
        self.assertEqual(comparison['basis_check_version'],VERSION)
        self.assertTrue(all(c['status']=='rounding_compatible' for c in comparison['restatement_checks']))
        change=next(c for c in comparison['changes'] if c['metric_code']=='gross_margin')
        self.assertEqual(change['status'],'calculated')
        self.assertEqual(change['basis_check'],'precision_compatible_comparatives')
        self.assertTrue(change['precision_compatible_codes'])
        self.assertEqual(Decimal(comparison['fact_series']['revenue'][0]['value']),Decimal('38290200'))
        self.assertEqual(analysis,before)
        self.assertTrue(all(c['affected_metrics'] for c in comparison['restatement_checks']))

    def test_true_difference_still_blocks_only_dependent_changes(self):
        analysis=history_fixture()
        later=next(f for f in analysis['facts'] if f['document_id']=='demo2024' and f['metric_code']=='operating_cost' and f['period']=='2023')
        later.update(value='90000000',raw_value='90,000')
        comparison=build_annual_history(analysis,'002594')
        gross=next(c for c in comparison['changes'] if c['metric_code']=='gross_margin')
        self.assertEqual(gross['reason_code'],'RESTATED_COMPARATIVE_CONFLICT')
        self.assertNotIn('absolute_change',gross)
        cost=next(c for c in comparison['restatement_checks'] if c['metric_code']=='operating_cost')
        self.assertIn({'metric_code':'gross_margin','from_year':2023,'to_year':2024},cost['withheld_changes'])
        revenue=next(c for c in comparison['changes'] if c['metric_code']=='revenue_yoy')
        self.assertEqual(revenue['status'],'calculated')

    def test_context_risk_accepts_precision_but_still_requires_real_conditions(self):
        sample=context_fixture()
        sample['history']['comparison']['restatement_checks'][0]['status']='rounding_compatible'
        sample['history']['comparison']['changes'][0]['basis_check']='precision_compatible_comparatives'
        result=evaluate_context_risks(sample)
        self.assertEqual(result['data_quality']['status'],'precision_compatible')
        self.assertEqual(result['data_quality']['precision_compatible_count'],1)
        self.assertTrue(all(a['status']=='triggered' for a in result['assessments']))
        sample['history']['comparison']['restatement_checks'][0]['status']='adjusted'
        self.assertEqual(evaluate_context_risks(sample)['assessments'][0]['status'],'insufficient_data')

    def test_old_result_is_read_only_upgraded_without_model_or_reparse(self):
        analysis=history_fixture()
        result={'analysis':{'documents':[analysis['documents'][-1]]},'report_markdown':'原报告不覆盖',
                'payload':{'history':{'status':'complete','analysis':analysis,'comparison':{'restatement_checks':[]}}}}
        before=copy.deepcopy(result)
        task={'id':'a'*32,'status':'completed','result':result,'request':{}}
        with patch.object(core,'task_by_id',return_value=task),patch.object(core,'generate_report',side_effect=AssertionError('no model')),patch('financial_engine.pdf_adapter.parse_pdf',side_effect=AssertionError('no reparse')):
            response=TestClient(core.app).get('/api/tasks/'+task['id']+'/result')
        self.assertEqual(response.status_code,200)
        upgraded=response.json()
        self.assertEqual(upgraded['payload']['history']['comparison']['basis_check_version'],VERSION)
        self.assertTrue(upgraded['payload']['history']['basis_view']['read_only'])
        self.assertEqual(result,before);self.assertEqual(upgraded['report_markdown'],'原报告不覆盖')

    def test_saved_catl_snapshot_expected_breakdown(self):
        path=core.TASK_DIR/'c21dec043a6748c2adfc38c40c050b61.json'
        if not path.is_file():self.skipTest('Public acceptance snapshot absent')
        before=path.read_bytes();original=json.loads(before);result=enrich_history(original['result'])
        checks=result['payload']['history']['comparison']['restatement_checks']
        counts={status:sum(c['status']==status for c in checks) for status in ('matched','rounding_compatible','difference','unavailable','adjusted')}
        self.assertEqual(counts,{'matched':25,'rounding_compatible':22,'difference':1,'unavailable':1,'adjusted':0})
        large=next(c for c in checks if c['status']=='difference')
        self.assertEqual(large['metric_code'],'operating_cost')
        self.assertEqual(Decimal(large['difference']),Decimal('14911696000'))
        self.assertEqual(path.read_bytes(),before)


if __name__=='__main__':unittest.main()
