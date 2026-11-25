import copy,unittest
from decimal import Decimal
import app
from financial_engine.policy_adapter import extract_policy_pages
from financial_engine.policy_review import evaluate_policy_reviews,refresh_policy_reviews
from financial_engine.core import analyze_parsed_documents


def rd_page(cap=('20','10'), header=None, unit='元'):
    grid=[header or ['项目','2025年','2024年','同比增减'],
          [f'研发投入合计（{unit}）','100','100','0%'],
          [f'资本化研发投入（{unit}）',*cap,'100%']]
    return {'page':8,'text':'研发投入\n'+'\n'.join(' '.join(str(c or '') for c in row) for row in grid),
            'tables':[{'rows':grid}]}


def dep_page(life='3-10', change='none'):
    grid=[['类别','折旧方法','折旧年限（年）','残值率（%）','年折旧率（%）'],
          ['机器设备','年限平均法',life,'5','9.5-31.67']]
    extra={'none':'重要会计估计变更\n□适用 √不适用',
           'changed':'重要会计估计变更\n√适用 □不适用\n本期机器设备折旧年限调整，自一月起执行。',
           'boilerplate':'每年复核固定资产折旧方法，若有变化则调整。','unknown':''}[change]
    return {'page':80,'text':'15、固定资产\n'+extra,'lines':[{'text':'15、固定资产'},
            *[{'text':line} for line in extra.splitlines()]],'tables':[{'rows':grid}]}


def document(year=2025, pages=None, did='d', company='测试股份有限公司', code='300750'):
    rows,policy=extract_policy_pages(pages or [],did,year)
    return {'document_id':did,'company_name':company,'security_code':code,'report_year':year,
            'report_type':'annual','rows':rows,'policy_review':policy}


def reviews(doc,other=None):
    result=analyze_parsed_documents([doc,*([other] if other else [])])
    return {r['rule_id']:r for r in result['risk_assessments'] if r['document_id']==doc['document_id']},result


class PolicyReviewTests(unittest.TestCase):
    def test_amounts_normalize_and_ratio_rise_is_review_not_fraud_signal(self):
        risks,result=reviews(document(pages=[rd_page(unit='万元')]))
        rd=risks['r_and_d_capitalization_policy']
        self.assertEqual(rd['status'],'review_required')
        self.assertEqual(rd['conditions'][0]['observed']['current_percent'],'20.0')
        self.assertIn('资本化比例 10.00% → 20.00%',rd['summary'])
        self.assertTrue(all(Decimal(f['value'])>=100000 for f in result['facts']))
        self.assertFalse(rd['materiality_assessed']);self.assertEqual(result['findings'],[])
        self.assertEqual(len(rd['evidence_fact_ids']),4)

    def test_explicit_zero_is_valid_but_blank_dash_and_not_applicable_are_not_zero(self):
        risks,_=reviews(document(pages=[rd_page(('0','0'))]))
        self.assertEqual(risks['r_and_d_capitalization_policy']['status'],'reviewed')
        for bad in ('','—','不适用'):
            with self.subTest(bad=bad):
                risks,result=reviews(document(pages=[rd_page((bad,'0'))]))
                self.assertEqual(risks['r_and_d_capitalization_policy']['status'],'insufficient_data')
                self.assertFalse(any(f['metric_code']=='r_and_d_capitalized_expenditure' and f['period']=='2025' for f in result['facts']))
                self.assertTrue(any(f['metric_code']=='r_and_d_capitalized_expenditure' and f['period']=='2024' and f['value']=='0' for f in result['facts']))
                self.assertTrue(risks['r_and_d_capitalization_policy']['evidence_policy_ids'])

    def test_negative_zero_base_and_cap_exceeds_total_are_blocked(self):
        for pair in (('-1','0'),('101','0')):
            risks,_=reviews(document(pages=[rd_page(pair)]))
            self.assertEqual(risks['r_and_d_capitalization_policy']['status'],'insufficient_data')
        page=rd_page();page['tables'][0]['rows'][1][1]='0'
        risks,_=reviews(document(pages=[page]));self.assertEqual(risks['r_and_d_capitalization_policy']['status'],'insufficient_data')

    def test_reversed_years_missing_units_and_percentage_cells_rejected(self):
        for page in (rd_page(header=['项目','2024年','2025年','变动']),rd_page(unit='美元')):
            doc=document(pages=[page]);self.assertEqual(doc['rows'],[])
        doc=document(pages=[rd_page(('20%','10%'))])
        self.assertFalse(any(r['metric_code']=='r_and_d_capitalized_expenditure' for r in doc['rows']))

    def test_parent_company_and_development_balance_not_used(self):
        page=rd_page();page['lines']=[{'text':'母公司研发投入'}]
        self.assertEqual(document(pages=[page])['rows'],[])
        page=rd_page();page['tables'][0]['rows'][2][0]='开发支出期末余额'
        page['text']='研发投入\n'+'\n'.join(' '.join(str(c or '') for c in row) for row in page['tables'][0]['rows'])
        self.assertFalse(any(r['metric_code']=='r_and_d_capitalized_expenditure' for r in document(pages=[page])['rows']))

    def test_grid_geometry_preserves_blank_current_column(self):
        table={'rows':[['项目','2025年','2024年','变动'],['资本化研发投入（元）','','10','200']],
               'row_cells':[[(0,0,100,10),(100,0,200,10),(200,0,300,10),(300,0,400,10)],
                            [(0,10,100,20),(100,10,200,20),(200,10,300,20),(300,10,400,20)]]}
        page={'page':8,'text':'项目 2025年 2024年 变动\n资本化研发投入（元） 10 200','tables':[table]}
        risks,result=reviews(document(pages=[page]))
        self.assertFalse(any(f['metric_code']=='r_and_d_capitalized_expenditure' and f['period']=='2025' for f in result['facts']))
        self.assertTrue(any(f['period']=='2024' and f['value']=='10' for f in result['facts']))
        table['rows'][0]=['','本年度','上年度','变化幅度（%）']
        table['rows'][1]=['研发投入合计（元）','100','80','25']
        doc=document(pages=[page]);self.assertEqual(doc['rows'][0]['values'],['100','80'])

    def test_conflicting_monetary_disclosures_block_ratio(self):
        doc=document(pages=[rd_page(),rd_page(('30','10'))])
        risks,_=reviews(doc)
        self.assertEqual(risks['r_and_d_capitalization_policy']['status'],'insufficient_data')
        self.assertIn('CONFLICTING_INPUT',{m['reason'] for m in risks['r_and_d_capitalization_policy']['missing_or_invalid']})

    def test_current_table_and_explicit_no_important_change_has_limited_scope(self):
        doc=document(pages=[dep_page()]);risks,_=reviews(doc);dep=risks['depreciation_policy_change']
        self.assertEqual(dep['status'],'reviewed');self.assertIn('未独立对照上期',''.join(dep['review_details']))
        self.assertTrue(dep['evidence_policy_ids'])

    def test_boilerplate_or_absent_change_paragraph_is_not_no_change(self):
        for state in ('boilerplate','unknown'):
            dep=reviews(document(pages=[dep_page(change=state)]))[0]['depreciation_policy_change']
            self.assertEqual(dep['status'],'insufficient_data')
            self.assertIn('POLICY_PRIOR_REPORT_NEEDED',{m['reason'] for m in dep['missing_or_invalid']})

    def test_actual_depreciation_change_disclosure_needs_review(self):
        dep=reviews(document(pages=[dep_page(change='changed')]))[0]['depreciation_policy_change']
        self.assertEqual(dep['status'],'review_required')
        self.assertNotIn('违规',dep['summary'])

    def test_prior_comparison_scope_identity_and_format(self):
        doc=document(pages=[dep_page(life='3.00～10.0',change='unknown')])
        previous=document(2024,[dep_page(change='unknown')],did='old')
        dep=reviews(doc,previous)[0]['depreciation_policy_change']
        self.assertEqual(dep['status'],'reviewed');self.assertTrue(any(eid.startswith('old:policy:') for eid in dep['evidence_policy_ids']))
        previous['security_code']='002594'
        self.assertEqual(reviews(doc,previous)[0]['depreciation_policy_change']['status'],'insufficient_data')

    def test_prior_difference_and_contradiction_are_distinguished(self):
        previous=document(2024,[dep_page(life='3-5',change='unknown')],did='old')
        doc=document(pages=[dep_page(change='unknown')])
        dep=reviews(doc,previous)[0]['depreciation_policy_change'];self.assertEqual(dep['status'],'review_required')
        self.assertIn('3-5 → 3-10',''.join(dep['review_details']))
        doc=document(pages=[dep_page()]);self.assertEqual(reviews(doc,previous)[0]['depreciation_policy_change']['status'],'manual_review')

    def test_category_mismatch_is_not_silently_no_change(self):
        doc=document(pages=[dep_page(change='unknown')]);previous=document(2024,[dep_page(change='unknown')],did='old')
        previous['policy_review']['depreciation_tables'][0]['rows'][0]['category']='生产设备'
        self.assertEqual(reviews(doc,previous)[0]['depreciation_policy_change']['status'],'manual_review')

    def test_previous_report_can_fill_missing_rd_comparative_but_not_a_conflict(self):
        doc=document(pages=[rd_page()]);prior=document(2024,[],did='old')
        for row in doc['rows']:
            row['values'][1]=None
        prior['rows']=[{'kind':'note','metric_code':code,'label':'上期原报告研发投入',
                       'values':[value],'unit':'元','source':{'page_start':7,'page_end':7}}
                      for code,value in [('r_and_d_total_investment','100'),('r_and_d_capitalized_expenditure','10')]]
        rd=reviews(doc,prior)[0]['r_and_d_capitalization_policy']
        self.assertEqual(rd['status'],'review_required')
        self.assertTrue(any(e.startswith('old:2024:') for e in rd['evidence_fact_ids']))
        self.assertIn('来自同公司上期原报告',''.join(rd['review_details']))

    def test_partial_or_invalid_current_and_prior_tables_require_manual_confirmation(self):
        page=dep_page();page['tables'][0]['rows'][1][2]=''
        dep=reviews(document(pages=[page]))[0]['depreciation_policy_change']
        self.assertEqual(dep['status'],'manual_review');self.assertTrue(dep['evidence_policy_ids'])
        doc=document(pages=[dep_page(change='unknown')]);prior=document(2024,[dep_page(change='unknown')],did='old')
        prior['policy_review']['depreciation_unverified']=['old:policy:unverified']
        dep=reviews(doc,prior)[0]['depreciation_policy_change']
        self.assertEqual(dep['status'],'manual_review')

    def test_lease_depreciation_not_fixed_assets_and_text_is_not_numeric_fact(self):
        page=dep_page();page['lines'][0]['text']='使用权资产'
        self.assertEqual(document(pages=[page])['policy_review']['depreciation_tables'],[])
        risks,result=reviews(document(pages=[dep_page()]))
        self.assertEqual(result['facts'],[])
        index=app.evidence_index(result);eid=risks['depreciation_policy_change']['evidence_policy_ids'][0]
        self.assertIn(eid,index);self.assertEqual(index[eid]['page'],80)
        self.assertEqual(index[eid]['normalized_value'],'原文披露，非计算值')

    def test_history_refresh_updates_only_copy_and_retains_quantitative_rules(self):
        doc=document(pages=[dep_page(change='unknown')]);risks,analysis=reviews(doc)
        before=copy.deepcopy(analysis);updated=copy.deepcopy(analysis)
        prior=document(2024,[dep_page(change='unknown')],did='old')
        refresh_policy_reviews(updated,[*updated['documents'],prior])
        self.assertEqual(analysis,before)
        dep=next(r for r in updated['risk_assessments'] if r['rule_id']=='depreciation_policy_change')
        self.assertEqual(dep['status'],'reviewed')
        self.assertEqual([r for r in before['risk_assessments'] if r['screen_type']!='policy_observation'],[r for r in updated['risk_assessments'] if r['screen_type']!='policy_observation'])

    def test_summary_contains_chinese_observations_and_missing_reasons(self):
        _,analysis=reviews(document(pages=[rd_page(('', '0')),dep_page()]))
        text=app.deterministic_report(analysis)
        for value in ('会计政策与估计披露复核','已完成复核','不按零处理','研发总投入'):
            self.assertIn(value,text)
        self.assertNotIn('POLICY_',text);self.assertNotIn('policy_observation',text)

if __name__=='__main__':unittest.main()
