import hashlib
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from financial_engine import analyze_disclosure_context
from financial_engine.disclosure import DisclosureError

PROJECT = Path(__file__).resolve().parents[1]


class DisclosureContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=PROJECT)
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name)
        self.parsed = {}
        self.identities = {}
        self.counter = 100
        self.target = self.report('300750', 2025, '100', '60', '80', '56', cached=False)
        for name, value in [('parse_pdf', self.fake_parse), ('_pdf_security_identity', self.fake_identity),
                            ('read_pdf_document', {'pages': [{'lines': [{'text': '2025年年度报告'}]}]})]:
            obj = patch('financial_engine.mining.' + name,
                        side_effect=value if callable(value) else None,
                        return_value=value if not callable(value) else None)
            mocked = obj.start()
            self.addCleanup(obj.stop)
            if name == 'parse_pdf':
                self.parser = mocked
            if name == 'read_pdf_document':
                self.inspection = mocked
        self.network = patch('financial_engine.mining.resolve_security', side_effect=AssertionError('offline contacted network')).start()
        self.addCleanup(patch.stopall)

    def report(self, code, year, revenue='100', cost='60', prev_revenue='80', prev_cost='56',
               *, cached=True, published='2026-01-01T00:00:00+00:00'):
        self.counter += 1
        path = self.cache / f'{code}_{year}_{self.counter}.pdf'
        data = f'%PDF-1.7\nfixture {code} {year} {self.counter}'.encode()
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        doc_id = digest[:16]
        rows = [{'kind': 'annual', 'metric_code': metric, 'label': metric, 'unit': '元',
                 'values': [now, prior], 'source': {'document_id': doc_id, 'page_start': 3,
                   'page_end': 3, 'section': 'annual_summary', 'row_bbox': [1, 2, 3, 4]}}
                for metric, now, prior in [('revenue', revenue, prev_revenue),
                                            ('operating_cost', cost, prev_cost)]]
        self.parsed[path] = {'document_id': doc_id, 'company_name': f'Company {code}',
            'report_year': year, 'report_type': 'annual', 'currency': 'CNY',
            'file_sha256': digest, 'parser': 'fixture', 'rows': rows, 'parser_warnings': []}
        self.identities[path] = {'stock_code': code, 'status': 'pdf_code_reported',
                                  'sources': [{'stock_code': code, 'page': 1, 'text': code}]}
        if cached:
            record = {'stock_code': code, 'report_year': year, 'report_type': 'annual',
                'announcement_id': str(self.counter), 'published_at': published,
                'source_url': f'https://static.cninfo.com.cn/finalpage/2026-01-01/{self.counter}.PDF',
                'title': f'{year}年年度报告', 'version_label': 'original', 'sha256': digest,
                'local_path': 'Z:/untrusted/redirect.pdf', 'bytes': len(data)}
            path.with_suffix('.json').write_text(json.dumps(record), encoding='utf-8')
        return path

    def fake_parse(self, path):
        # Match the real parser: callers receive a fresh metadata dictionary.
        return dict(self.parsed[Path(path)])

    def fake_identity(self, path):
        return dict(self.identities[Path(path)])

    def run_context(self, **kwargs):
        return analyze_disclosure_context(self.target, stock_code='300750', cache_dir=self.cache,
            **{'lookback_years': 1, **kwargs})

    def test_one_parse_per_file_complete_history_and_exact_peer_comparison(self):
        self.report('300750', 2024, '80', '56')
        self.report('300014', 2025, '100', '70')
        self.report('002074', 2025, '100', '80')
        result = self.run_context(peer_codes=['300014', '002074'], peer_group='battery',
                                  selection_note='caller selected product-related peers', metric_codes=['gross_margin'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['schema_version'], '0.15')
        self.assertEqual(result['risk_review']['assessments'][1]['rule_id'], 'relative_gross_margin_pressure')
        self.assertEqual(self.parser.call_count, 4)
        self.network.assert_not_called()
        self.assertEqual(result['history']['comparison']['report_years'], [2024, 2025])
        change = next(x for x in result['history']['comparison']['changes'] if x['metric_code']=='gross_margin')
        self.assertEqual(Decimal(change['absolute_change']), Decimal('10'))
        peer = result['peers']['comparison']['metric_comparisons'][0]
        self.assertEqual(Decimal(peer['peer_median']['value']), Decimal('25'))
        self.assertEqual(Decimal(peer['target_minus_peer_median']), Decimal('15'))
        analysis = result['peers']['reports']['300014']
        metric = next(x for x in analysis['metrics'] if x['metric_id']==peer['peers'][0]['metric_id'])
        fact_ids = {x['fact_id'] for x in analysis['facts']}
        self.assertTrue(set(metric['input_fact_ids']).issubset(fact_ids))

    def test_missing_files_preserve_current_and_explicit_missing_lists(self):
        result = self.run_context(peer_codes=['300014'], peer_group='battery', selection_note='configured')
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['current']['facts'])
        self.assertEqual(result['history']['missing_years'], [2024])
        self.assertEqual(result['peers']['missing_codes'], ['300014'])
        self.assertEqual(result['peers']['comparison']['metric_comparisons'][0]['peers'][0]['reason_code'], 'REPORT_NOT_AVAILABLE')

    def test_cache_revision_selection_and_relocated_path(self):
        old = self.report('300750', 2024, published='2025-01-01T00:00:00+00:00')
        new = self.report('300750', 2024, published='2025-02-01T00:00:00+00:00')
        result = self.run_context()
        accepted = next(x for x in result['retrieval']['files'] if x['role']=='history')
        self.assertEqual(accepted['local_path'], str(new))
        self.assertEqual(result['retrieval']['alternatives'][0]['local_path'], str(old))
        self.assertEqual(accepted['version_selection'], 'latest_available_cache_only')
        self.assertEqual(self.parser.call_count, 2)

    def test_tampered_latest_cache_is_withheld_without_using_older_version(self):
        self.report('300750', 2024, published='2025-01-01T00:00:00+00:00')
        new = self.report('300750', 2024, published='2025-02-01T00:00:00+00:00')
        new.write_bytes(b'%PDF-1.7\ntampered')
        result = self.run_context()
        self.assertEqual(result['history']['missing_years'], [2024])
        self.assertEqual(self.parser.call_count, 1)
        self.assertTrue(any('SHA-256' in x.get('message','') for x in result['warnings']))

    def test_wrong_company_pdf_cannot_enter_history_or_peers(self):
        h = self.report('300750', 2024)
        p = self.report('300014', 2025)
        self.identities[h]['stock_code']='601868'
        self.identities[p]['stock_code']='601868'
        result = self.run_context(peer_codes=['300014'],peer_group='battery',selection_note='configured')
        self.assertEqual(result['history']['missing_years'], [2024])
        self.assertNotIn('300014',result['peers']['reports'])
        self.assertTrue(all(x['report_year']==2025 for x in result['history']['analysis']['documents']))

    def test_wrong_requested_code_stops_all_expansion_and_retains_analysis(self):
        result = analyze_disclosure_context(self.target,stock_code='601868',lookback_years=1,cache_dir=self.cache)
        self.assertEqual(result['identity']['status'], 'conflict_or_unverified')
        self.assertTrue(result['current']['facts'])
        self.assertEqual(self.parser.call_count, 1)
        self.assertIn('INPUT_SECURITY_CODE_CONFLICT',[x['code'] for x in result['warnings']])

    def test_offline_company_code_and_year_can_select_verified_cached_target(self):
        path = self.report('300750',2024)
        result = analyze_disclosure_context(stock_code='300750',report_year=2024,lookback_years=0,cache_dir=self.cache)
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['current']['documents'][0]['file_sha256'],self.parsed[path]['file_sha256'])
        self.network.assert_not_called()

    def test_online_code_mode_queries_target_and_history_together(self):
        target=self.report('300750',2025,cached=False)
        history=self.report('300750',2024,cached=False)
        records=[]
        by_year={2025:target,2024:history}
        for year,path in by_year.items():
            records.append({'stock_code':'300750','report_year':year,'announcement_id':str(year),
                'published_at':f'{year+1}-01-01T00:00:00+00:00','source_url':'https://static.cninfo.com.cn/finalpage/2026-01-01/1.PDF',
                'title':f'{year}年年度报告','version_label':'original'})
        self.network.side_effect=None
        self.network.return_value={'stock_code':'300750'}
        def downloaded(record,directory):
            path=by_year[record['report_year']]
            return {**record,'local_path':str(path),'sha256':self.parsed[path]['file_sha256'],'cache_status':'downloaded'}
        with patch('financial_engine.mining.discover_annual_reports',return_value=records) as discover, \
             patch('financial_engine.mining.download_report',side_effect=downloaded) as download:
            result=analyze_disclosure_context(stock_code='300750',report_year=2025,lookback_years=1,cache_dir=self.cache,fetch_online=True)
        self.assertEqual(result['status'],'complete')
        self.assertEqual(discover.call_count,1)
        self.assertEqual(discover.call_args.args[1],[2024,2025])
        self.assertEqual(download.call_count,2)
        self.assertEqual(self.parser.call_count,2)

    def test_online_catalogue_failure_can_use_verified_cache_with_warning(self):
        self.report('300750',2024)
        self.network.side_effect=DisclosureError('network unavailable')
        result=analyze_disclosure_context(stock_code='300750',report_year=2024,lookback_years=0,cache_dir=self.cache,fetch_online=True)
        self.assertEqual(result['status'],'partial')
        self.assertTrue(result['current']['facts'])
        self.assertIn('CATALOGUE_UNAVAILABLE_CACHE_FALLBACK',[x['code'] for x in result['warnings']])

    def test_online_report_with_verified_pdf_code_does_not_repeat_full_name_lookup(self):
        record={'stock_code':'300750','report_year':2025,'announcement_id':'123','published_at':'2026-03-01',
                'local_path':str(self.target),'sha256':self.parsed[self.target]['file_sha256']}
        self.parsed[self.target]['company_name']='测试能源（集团）股份有限公司'
        def code_only(query):
            if query!='300750':raise DisclosureError('full-name lookup returned no matches')
            return {'stock_code':'300750'}
        self.network.side_effect=code_only
        with patch('financial_engine.mining.discover_annual_reports',return_value=[record]),patch('financial_engine.mining.download_report',return_value=record):
            result=analyze_disclosure_context(stock_code='300750',report_year=2025,lookback_years=0,cache_dir=self.cache,fetch_online=True)
        self.assertTrue(result['current'])
        self.assertEqual([call.args[0] for call in self.network.call_args_list],['300750'])
        self.assertEqual(result['identity']['status'],'pdf_code_matches_catalogue')

    def test_online_upload_reuses_reported_code_instead_of_company_name(self):
        self.parsed[self.target]['company_name']='测试能源（集团）股份有限公司'
        self.network.side_effect=lambda query: {'stock_code':query} if query=='300750' else (_ for _ in ()).throw(DisclosureError('name unavailable'))
        result=self.run_context(lookback_years=0,fetch_online=True)
        self.assertTrue(result['current'])
        self.assertEqual([call.args[0] for call in self.network.call_args_list],['300750'])

    def test_online_report_without_pdf_code_still_requires_matching_name_lookup(self):
        record={'stock_code':'300750','report_year':2025,'announcement_id':'123','published_at':'2026-03-01',
                'local_path':str(self.target),'sha256':self.parsed[self.target]['file_sha256']}
        self.identities[self.target]={'stock_code':None,'status':'absent','sources':[]}
        self.network.side_effect=lambda query: {'stock_code':'300750' if query=='300750' else '002074'}
        with patch('financial_engine.mining.discover_annual_reports',return_value=[record]),patch('financial_engine.mining.download_report',return_value=record):
            result=analyze_disclosure_context(stock_code='300750',report_year=2025,lookback_years=0,cache_dir=self.cache,fetch_online=True)
        self.assertIsNone(result['current'])
        self.assertTrue(any('PDF 公司名称核验结果' in item.get('message','') for item in result['warnings']))

    def test_duplicate_local_versions_are_not_arbitrarily_selected(self):
        a=self.report('300750',2024,cached=False)
        b=self.report('300750',2024,cached=False)
        result=self.run_context(local_history_paths=[a,b])
        self.assertEqual(result['history']['missing_years'],[2024])
        self.assertTrue(any('Multiple local' in x.get('message','') for x in result['warnings']))

    def test_restatement_suppresses_cross_report_difference(self):
        self.report('300750',2024,'90','56')
        result=self.run_context()
        item=next(x for x in result['history']['comparison']['changes'] if x['metric_code']=='gross_margin')
        self.assertEqual(item['status'],'unavailable')
        self.assertEqual(item['reason_code'],'RESTATED_COMPARATIVE_CONFLICT')

    def test_h1_current_is_retained_and_annual_expansion_is_explicitly_unsupported(self):
        self.inspection.return_value={'pages':[{'lines':[{'text':'2025年半年度报告'}]}]}
        current={'documents':[{'report_year':2025}], 'warnings':[], 'audit':[]}
        with patch('financial_engine.mining.analyze_semiannual_reports',return_value=current):
            result=self.run_context()
        self.assertEqual(result['current'],current)
        self.assertEqual(result['history']['status'],'unsupported')
        self.assertEqual(result['status'],'partial')
        self.parser.assert_not_called()

    def test_offline_peer_without_pdf_code_is_not_included(self):
        peer = self.report('300014', 2025)
        self.identities[peer] = {'stock_code': None, 'status': 'absent', 'sources': []}
        result = self.run_context(lookback_years=0, peer_codes=['300014'], peer_group='battery', selection_note='configured')
        self.assertEqual(result['peers']['missing_codes'], ['300014'])
        self.assertTrue(any('未找到可唯一识别的本公司证券代码' in x.get('message','') for x in result['warnings']))

    def test_peer_list_containing_inferred_target_keeps_current_analysis(self):
        result = analyze_disclosure_context(self.target, lookback_years=0,
            peer_codes=['300750'], peer_group='battery', selection_note='configured')
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['current']['facts'])
        self.assertEqual(result['peers']['status'], 'invalid_configuration')
        self.assertIn('PEER_CONFIGURATION_INVALID', [x['code'] for x in result['warnings']])

    def test_target_warning_is_not_repeated_when_history_includes_current(self):
        self.report('300750', 2024)
        original = self.parsed[self.target]
        original['parser_warnings'] = [{'code': 'SAMPLE_WARNING',
            'document_id': original['document_id'], 'message': 'same target row'}]
        result = self.run_context()
        self.assertEqual(len([x for x in result['warnings'] if x['code'] == 'SAMPLE_WARNING']), 1)
        self.assertEqual(result['status'], 'complete')  # file coverage is separate from parser warnings

    def test_h1_wrong_requested_year_does_not_return_wrong_period(self):
        self.inspection.return_value = {'pages': [{'lines': [{'text': '2025年半年度报告'}]}]}
        current = {'documents': [{'report_year': 2025}], 'warnings': [], 'audit': []}
        with patch('financial_engine.mining.analyze_semiannual_reports', return_value=current):
            result = self.run_context(report_year=2024)
        self.assertEqual(result['status'], 'failed')
        self.assertIsNone(result['current'])

    def test_cache_summary_record_is_excluded(self):
        path = self.report('300750', 2024)
        manifest = path.with_suffix('.json')
        record = json.loads(manifest.read_text(encoding='utf-8'))
        record['title'] = '2024年年度报告摘要'
        manifest.write_text(json.dumps(record), encoding='utf-8')
        result = self.run_context()
        self.assertEqual(result['history']['missing_years'], [2024])
        self.assertIn('CACHE_MANIFEST_INVALID', [x['code'] for x in result['warnings']])

    def test_bad_parameters_and_unsupported_input(self):
        for kwargs in [{'lookback_years':True},{'stock_code':300750},{'fetch_online':True},
                       {'peer_codes':['300014'],'peer_group':'battery'}, {'metric_codes':['x','x']},
                       {'options': []}, {'local_history_paths': [42]}]:
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):
                analyze_disclosure_context(self.target,**kwargs)
        self.inspection.return_value={'pages':[{'lines':[{'text':'项目中标公告'}]}]}
        result=self.run_context()
        self.assertEqual(result['status'],'failed')
        self.parser.assert_not_called()

    def test_genuine_wrong_issuer_error_is_chinese_and_names_both_codes(self):
        path=self.report('300750',2024)
        self.identities[path]['stock_code']='002202'
        result=analyze_disclosure_context(stock_code='300750',report_year=2024,lookback_years=0,cache_dir=self.cache)
        self.assertIsNone(result['current'])
        message=' '.join(item.get('message','') for item in result['warnings'])
        for text in ['本公司证券代码','002202','300750','不一致']:
            self.assertIn(text,message)

    def test_genuine_multiple_issuer_codes_remain_ambiguous_with_clear_error(self):
        path=self.report('300750',2024)
        self.identities[path]={'stock_code':None,'status':'ambiguous','sources':[{'stock_code':'300750'},{'stock_code':'002202'}]}
        result=analyze_disclosure_context(stock_code='300750',report_year=2024,lookback_years=0,cache_dir=self.cache)
        self.assertIsNone(result['current'])
        self.assertTrue(any('多个本公司证券代码' in item.get('message','') for item in result['warnings']))

    def test_progress_separates_pdf_parsing_identity_and_calculation(self):
        self.report('300750',2025)
        events=[]
        result=analyze_disclosure_context(stock_code='300750',report_year=2025,lookback_years=0,cache_dir=self.cache,progress_callback=events.append)
        self.assertIsNotNone(result['current'])
        self.assertTrue(any('正在解析 PDF：300750 · 2025 年' in event for event in events))
        self.assertTrue(any('核验 PDF 公司身份' in event for event in events))
        self.assertIn('正在计算本期财务指标与风险规则',events)
        self.assertLess(next(i for i,event in enumerate(events) if '正在解析 PDF' in event),events.index('正在计算本期财务指标与风险规则'))

    def test_no_annual_report_and_offline_directory_have_distinct_chinese_errors(self):
        missing=analyze_disclosure_context(stock_code='300750',report_year=2020,lookback_years=0,cache_dir=self.cache)
        self.assertTrue(any('未找到 300750 的 2020 年完整年度报告' in item.get('message','') for item in missing['warnings']))
        self.network.side_effect=DisclosureError('目录不可用')
        failed=analyze_disclosure_context(stock_code='300750',report_year=2020,lookback_years=0,cache_dir=self.cache,fetch_online=True)
        self.assertTrue(any('公开报告目录查询失败，且本机没有' in item.get('message','') for item in failed['warnings']))


if __name__=='__main__':
    unittest.main()
