import copy,json,sys,unittest
from pathlib import Path
from decimal import Decimal,localcontext
from unittest.mock import patch
import app
import tempfile,shutil
from fastapi.testclient import TestClient
from financial_engine.interim import analyze_parsed_interim,parse_quarterly_pdf,analyze_interim_pdf
from financial_engine.periods import period_key,period_spec
from financial_engine.disclosure import discover_annual_reports,periodic_title_year
from financial_engine.document_reader import classify_disclosure
from periodic_reports import build_period_history,compare_period_peers,project_period
from backfill_api import context

def fixture(year=2025,kind='quarterly',quarter=3,doc_id=None):
    doc_id=doc_id or f'd{year}-{kind}-{quarter}'
    flows={'revenue':('300','200'),'operating_cost':('180','120'),'net_profit':('30','20'),
           'attributable_net_profit':('25','15'),'attributable_net_profit_ex_nonrecurring':('20','10'),
           'operating_cash_flow':('60','40'),'capital_expenditure_cash_paid':('15','10'),'r_and_d_expense':('12','10')}
    stocks={'total_assets':('1000','800'),'total_liabilities':('600','500'),'attributable_equity':('300','250'),
            'inventory':('60','40'),'accounts_receivable':('80','60'),'fixed_assets':('200','180'),'construction_in_progress':('40','20')}
    return {'document_id':doc_id,'security_code':'300750','company_name':'示例股份有限公司','report_year':year,'report_type':kind,
            'report_quarter':quarter if kind=='quarterly' else None,'currency':'CNY',
            'rows':[{'kind':'income_statement' if code in flows else 'balance_sheet','metric_code':code,'values':list(values),'unit':'元','label':code,
                    'source':{'page_start':1,'page_end':1,'document_id':doc_id,'row_bbox':[1,2,3,4]}} for code,values in {**flows,**stocks}.items()]}

class PeriodicTests(unittest.TestCase):
    def test_type_and_period_validation(self):
        self.assertEqual(period_key(2025,'quarterly',3),'2025YTD9')
        self.assertEqual(period_key(2025,'quarterly',3,'single'),'2025Q3')
        self.assertEqual(period_spec(fixture())['end'],'2025-09-30')
        with self.assertRaises(ValueError):period_key(2025,'quarterly',2)
        for title in ('2025年第一季度报告','2025年第三季度报告'):
            self.assertEqual(classify_disclosure({'pages':[{'lines':[{'text':title}]}]})['document_type'],'quarterly_report')

    def test_quarter_explicit_reversed_year_columns_override_generic_labels(self):
        from financial_engine.semiannual import _statement_rows
        texts=['合并利润表','单位：元','项目 2024年1-9月 2025年1-9月 本期发生额 上期发生额','营业收入 100 200']
        class Page:
            width=600;page_number=1
            def extract_text(self):return '\n'.join(texts)
            def extract_text_lines(self):return [{'text':t,'top':i*15,'bottom':i*15+10,'x0':10,'x1':500} for i,t in enumerate(texts)]
            def close(self):pass
        class Pdf:pages=[Page()]
        rows,_,_=_statement_rows(Pdf(),0,'d',2025,9)
        self.assertEqual(rows,[])

    def test_cumulative_flows_not_annual_or_single(self):
        result=analyze_parsed_interim(fixture())
        facts={(f['period'],f['metric_code']):f for f in result['facts']}
        self.assertEqual(facts['2025YTD9','revenue']['value'],'300')
        self.assertNotIn(('2025','revenue'),facts)
        self.assertNotIn(('2025Q3','revenue'),facts)
        metric=next(m for m in result['metrics'] if m['metric_code']=='revenue_yoy')
        self.assertEqual(Decimal(metric['value']),50)
        self.assertEqual(metric['comparison']['period'],'2024YTD9')

    def test_actual_days_and_no_annualization(self):
        result=analyze_parsed_interim(fixture(2024,'semiannual'))
        metrics={m['metric_code']:m for m in result['metrics']}
        with localcontext() as ctx:
            ctx.prec=60
            self.assertEqual(Decimal(metrics['inventory_days']['value']),Decimal(182)*50/180)
            self.assertEqual(Decimal(metrics['return_on_assets']['value']),Decimal(30)/900*100)
        self.assertNotIn('365',metrics['inventory_days']['formula'])

    def test_single_quarter_derivation_and_source_links(self):
        h1=analyze_parsed_interim(fixture(kind='semiannual'))
        current=fixture()
        for row in current['rows']:
            if row['kind']=='income_statement':row['values']=[str(Decimal(x)*2) for x in row['values']]
        result=analyze_parsed_interim(current,support=[h1])
        derived=next(f for f in result['facts'] if f['period']=='2025Q3' and f['metric_code']=='revenue')
        self.assertEqual(derived['value'],'300');self.assertEqual(derived['quality'],'derived')
        self.assertEqual(len(derived['input_fact_ids']),2)
        self.assertEqual({s['document_id'] for s in derived['sources']},{current['document_id'],h1['documents'][0]['document_id']})
        self.assertTrue(any(m.get('derived_from_cumulative') for m in result['single_quarter']['metrics']))
        original=analyze_parsed_interim(current)
        self.assertFalse(any(m['metric_code']=='gross_margin' for m in original['single_quarter']['metrics']))

    def test_other_company_and_basis_conflicts_cannot_derive(self):
        h1=analyze_parsed_interim(fixture(kind='semiannual'))
        h1['documents'][0]['security_code']='002594'
        result=analyze_parsed_interim(fixture(),support=[h1])
        self.assertFalse(any(f.get('quality')=='derived' for f in result['facts']))
        h1=analyze_parsed_interim(fixture(kind='semiannual'))
        next(f for f in h1['facts'] if f['period']=='2024-12-31' and f['metric_code']=='total_assets')['value']='999'
        result=analyze_parsed_interim(fixture(),support=[h1])
        self.assertFalse(any(f.get('quality')=='derived' for f in result['facts']))

    def test_negative_base_and_duplicate_facts_withheld(self):
        parsed=fixture();parsed['rows'][0]['values'][1]='-100'
        result=analyze_parsed_interim(parsed)
        item=next(m for m in result['unavailable_metrics'] if m['metric_code']=='revenue_yoy')
        self.assertEqual(item['reason_code'],'NONPOSITIVE_INPUT');self.assertEqual(item['amount_comparison']['absolute_change'],'400')
        parsed=fixture();parsed['rows'].append({**parsed['rows'][0],'values':['999','200']})
        result=analyze_parsed_interim(parsed)
        self.assertFalse(any(m['metric_code']=='gross_margin' for m in result['metrics']))

    def test_same_period_history_and_immutable_inputs(self):
        reports=[analyze_parsed_interim(fixture(year)) for year in (2024,2025)]
        merged={key:[item for result in reports for item in result.get(key,[])] for key in ('documents','facts','metrics','unavailable_metrics')}
        before=copy.deepcopy(merged)
        result=build_period_history(merged,'300750','quarterly',3)
        self.assertEqual(result['report_years'],[2024,2025]);self.assertEqual(result['period_label'],'前三季度')
        self.assertEqual(len(result['series']['gross_margin']),2);self.assertEqual(merged,before)
        self.assertEqual(build_period_history(merged,'300750','semiannual')['report_years'],[])

    def test_peer_median_only_same_type_quarter_and_basis(self):
        result=analyze_parsed_interim(fixture());h1=analyze_parsed_interim(fixture(kind='semiannual'))
        peers={'300750':result,'002594':copy.deepcopy(result),'300014':h1}
        comparison=compare_period_peers(peers,kind='quarterly',quarter=3,target_code='300750',peer_codes=['002594','300014'],report_year=2025,peer_group='组',selection_note='相同业务人工指定')
        row=next(x for x in comparison['metric_comparisons'] if x['metric_code']=='gross_margin')
        self.assertEqual(row['valid_peer_count'],1);self.assertEqual(row['peer_median']['status'],'unavailable')

    def test_single_qoq_and_verified_adjacent_stock_inputs_are_comparable(self):
        def scaled(kind,quarter,factor):
            value=fixture(kind=kind,quarter=quarter)
            for row in value['rows']:
                if row['kind']=='income_statement':row['values']=[str(Decimal(x)*factor) for x in row['values']]
            return value
        h1=analyze_parsed_interim(scaled('semiannual',None,2))
        q1=analyze_parsed_interim(scaled('quarterly',1,1))
        target=analyze_parsed_interim(scaled('quarterly',3,3),support=[h1,q1])
        qoq=next(m for m in target['single_quarter']['metrics'] if m['metric_code']=='revenue_qoq')
        self.assertEqual(Decimal(qoq['value']),0)
        self.assertEqual(qoq['comparison']['period'],'2024Q3')
        comparison=compare_period_peers({'300750':target,'300014':copy.deepcopy(target),'002594':copy.deepcopy(target)},kind='quarterly',quarter=3,basis='single',target_code='300750',peer_codes=['300014','002594'],report_year=2025,peer_group='同业务',selection_note='测试已核验同期报表')
        row=next(m for m in comparison['metric_comparisons'] if m['metric_code']=='return_on_assets')
        self.assertEqual(row['valid_peer_count'],2)
        self.assertEqual(Decimal(row['peer_median']['value']),3)

    def test_catalogue_filters_type_quarter_summary_and_year(self):
        self.assertEqual(periodic_title_year('公司：2025年第三季度报告',{2025},'quarterly',3),2025)
        self.assertEqual(periodic_title_year('宁德时代：2025年三季度报告',{2025},'quarterly',3),2025)
        for title in ('2025年年度报告','2025年第三季度报告摘要','2024年第三季度报告','2025年第一季度报告'):
            self.assertIsNone(periodic_title_year(title,{2025},'quarterly',3))
        with patch('financial_engine.disclosure._post_form',return_value={'announcements':[]}) as post:
            discover_annual_reports({'stock_code':'300750','exchange_column':'szse','org_id':'x'},[2025],report_type='quarterly',report_quarter=3)
        self.assertEqual(post.call_args.args[1]['category'],'category_sjdbg_szsh')
        self.assertTrue(post.call_args.args[1]['seDate'].startswith('2025-'))

    def test_api_request_and_backfill_eligibility(self):
        request=app.OnlineAnalysisRequest(query='300750',report_year=2025,report_type='quarterly',report_quarter=3)
        self.assertEqual(request.model_dump()['report_type'],'quarterly')
        class Core:
            def task_by_id(self,_):return {'result':{'analysis':{'documents':[fixture()]}}}
        self.assertTrue(context(Core(),'test')[3])

    def test_cash_rule_never_requests_future_quarters(self):
        result=analyze_parsed_interim(fixture(2025,'quarterly',1))
        cash=result['risk_assessments'][0]
        missing=[m['period'] for m in cash['missing_or_invalid'] if m['code']=='operating_cash_flow']
        self.assertEqual(missing,['2024Q2','2024Q3','2024Q4'])

    def test_official_quarter_samples(self):
        root=Path(__file__).resolve().parent.parent/'artifacts/periodic-reports'
        for quarter,revenue,profit,cash in [(1,'84704589000','14861634000','32868257000'),(3,'283071987000','52296866000','80660430000')]:
            path=root/f'catl-2025-q{quarter}.pdf'
            if not path.exists():self.skipTest('Official test PDFs not present')
            result=analyze_interim_pdf(path,'quarterly',security_code='300750')
            key=period_key(2025,'quarterly',quarter)
            facts={(f['period'],f['metric_code']):f for f in result['facts']}
            for code,value in [('revenue',revenue),('net_profit',profit),('operating_cash_flow',cash)]:
                self.assertEqual(Decimal(facts[key,code]['value']),Decimal(value))
                self.assertGreater(facts[key,code]['sources'][0]['page_start'],0)

if __name__=='__main__':unittest.main()

class PeriodicApiTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        paths={key:self.root/key for key in ('TASK_DIR','RESULT_DIR','UPLOAD_DIR','CACHE_DIR','LOG_DIR','BACKFILL_DIR','PARSED_DIR','COMPANY_DIR')}
        for path in paths.values():path.mkdir()
        self.patch=patch.multiple(app,**paths,TASKS={},_CANCELLED_TASKS=set());self.patch.start()
        self.library=patch.object(app,'get_report_library',return_value=None);self.library.start()
        self.offline=patch.object(app,'library_online_enabled',return_value=False);self.offline.start()
        self.model=patch.object(app,'generate_report',side_effect=lambda _:('# 本地核验报告','deterministic_template'));self.model.start()
        self.client=TestClient(app.app)
        source=Path(__file__).resolve().parent.parent/'artifacts/periodic-reports'
        for name,kind,quarter,year,report_id,day in [('q3','quarterly',3,2025,'1224721971','2025-10-21'),('h1','semiannual',None,2025,'1224342874','2025-07-31'),('q3','quarterly',3,2024,'1221432744','2024-10-19')]:
            original=source/f'catl-{year}-{name}.pdf'
            if not original.exists():continue
            path=app.CACHE_DIR/f'300750_{year}_{report_id}.pdf';shutil.copy2(original,path)
            title=f'{year}年'+('半年度报告' if kind=='semiannual' else '第三季度报告')
            import hashlib
            record={'stock_code':'300750','report_type':kind,'report_quarter':quarter,'report_year':year,'announcement_id':report_id,
                'title':title,'source_url':f'https://static.cninfo.com.cn/finalpage/{day}/{report_id}.PDF',
                'published_at':day+'T00:00:00+00:00','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
            path.with_suffix('.json').write_text(json.dumps(record),encoding='utf8')
    def tearDown(self):
        self.model.stop();self.offline.stop();self.library.stop();self.patch.stop();self.temp.cleanup()
    def test_online_search_uses_verified_matching_cache_and_returns_q3(self):
        if not list(app.CACHE_DIR.glob('*.pdf')):self.skipTest('Official fixtures absent')
        with patch('periodic_reports.resolve_security',side_effect=AssertionError('must remain offline')):
            response=self.client.post('/api/tasks/online',json={'query':'300750','report_year':2025,'report_type':'quarterly','report_quarter':3,'lookback_years':0})
        self.assertEqual(response.status_code,202)
        task=app.task_by_id(response.json()['id']);self.assertEqual(task['status'],'completed',task.get('error'))
        result=self.client.get(f'/api/tasks/{task["id"]}/result').json()
        self.assertEqual(result['analysis']['documents'][0]['report_quarter'],3)
        self.assertIn('第三季度报告',result['report_summary_markdown'])
        self.assertTrue(result['analysis']['single_quarter']['metrics'])
        cash=next(f for f in result['analysis']['facts'] if f['period']=='2025Q3' and f['metric_code']=='operating_cash_flow')
        self.assertEqual(cash['value'],'21973364000')
        # Evidence for adjacent filings resolves to that filing, not the target PDF.
        supporting=next(f for f in result['analysis']['facts'] if f['period']=='2025H1' and f['metric_code']=='revenue')
        self.assertEqual(self.client.get(f'/api/tasks/{task["id"]}/file',params={'fact_id':supporting['fact_id']}).status_code,200)
    def test_q3_history_does_not_pick_h1_same_year(self):
        paths=list(app.CACHE_DIR.glob('*2025_1224721971.pdf'))
        if not paths:self.skipTest('Official fixtures absent')
        analysis=analyze_interim_pdf(paths[0],'quarterly',security_code='300750')
        task=app.create_task('upload','核验三季报.pdf',{'lookback_years':0})
        app.update_task(task['id'],file_path=str(paths[0]),status='completed',result={'analysis':analysis,'payload':{},'evidence':app.evidence_index(analysis),'report_markdown':'原文保留'})
        before=copy.deepcopy(app.task_by_id(task['id'])['result'])
        response=self.client.post(f'/api/tasks/{task["id"]}/backfill',json={'lookback_years':1,'online':False})
        self.assertEqual(response.status_code,202)
        result=app.task_by_id(response.json()['id'])['result'];history=result['payload']['history']
        self.assertEqual(history['available_years'],[2024,2025]);self.assertEqual(history['comparison']['period_label'],'前三季度')
        self.assertTrue(all(d['report_type']=='quarterly' and d['report_quarter']==3 for d in history['analysis']['documents']))
        self.assertEqual(before,app.task_by_id(task['id'])['result'])
    def test_quarter_selection_required_and_annual_quarter_rejected(self):
        for body in ({'report_type':'quarterly'},{'report_type':'annual','report_quarter':3}):
            response=self.client.post('/api/tasks/online',json={'query':'300750','report_year':2025,**body})
            self.assertEqual(response.status_code,422)
