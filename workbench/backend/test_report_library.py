"""Isolated local-library integration tests: no production tasks or network."""
import copy
import hashlib
import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import app as core
import backfill_api
import company_directory
from report_library import LibraryError, ReportLibrary
from financial_engine.pdf_adapter import _summary_monetary_unit


class ReportLibraryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.root = core.BASE_DIR / '.history-tests'
        self.path = self.root / uuid.uuid4().hex
        self.path.mkdir(parents=True)
        paths = {name:self.path/name for name in ('TASK_DIR','RESULT_DIR','UPLOAD_DIR','CACHE_DIR','LOG_DIR','BACKFILL_DIR','PARSED_DIR','COMPANY_DIR')}
        for path in paths.values():
            path.mkdir()
        self.settings = patch.multiple(core, **paths, TASKS={}, _CANCELLED_TASKS=set())
        self.settings.start()
        self.environment = patch.dict(os.environ, {'FINANCE_LIBRARY_DIR':str(self.path/'report_library'), 'FINANCE_LIBRARY_ALLOW_ONLINE':'1'})
        self.environment.start()
        self.library = self.path / 'report_library'
        self.library.mkdir()
        self.records, self.parsed, self.identities = [], {}, {}
        for code, year in [('300750',2025),('300750',2024),('300750',2023),('002074',2025),('300207',2025)]:
            self.add_report(code,year)
        companies = [{'code':code,'name':f'测试公司{code}','aliases':[f'库内别名{code}'],
                      'segment':'测试研究分组','identityVerified':True} for code in ['300750','002074','300207','999999']]
        (self.library/'companies.json').write_text(json.dumps({'items':companies}),encoding='utf-8')
        self.write_index()
        self.patches = [
            patch('financial_engine.mining.parse_pdf', side_effect=lambda path:copy.deepcopy(self.parsed[Path(path)])),
            patch('financial_engine.mining._pdf_security_identity',side_effect=lambda path:copy.deepcopy(self.identities[Path(path)])),
            patch.object(backfill_api,'parse_pdf',side_effect=lambda path:copy.deepcopy(self.parsed[Path(path)])),
            patch.object(backfill_api,'_pdf_security_identity',side_effect=lambda path:copy.deepcopy(self.identities[Path(path)])),
            patch.object(core,'generate_report',return_value=('# 本地测试报告','deterministic_template')),
            patch('financial_engine.mining.resolve_security',side_effect=AssertionError('unexpected network')),
            patch('financial_engine.mining.discover_annual_reports',side_effect=AssertionError('unexpected network')),
            patch('financial_engine.mining.download_report',side_effect=AssertionError('unexpected download')),
            patch.object(backfill_api,'discover_annual_reports',side_effect=AssertionError('unexpected backfill network')),
            patch.object(core,'_online_resolve_security',side_effect=AssertionError('unexpected identity network')),
        ]
        self.mocks = [p.start() for p in self.patches]
        self.client = TestClient(core.app)
        company_directory.DATA_CACHE.clear()

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.environment.stop();self.settings.stop()
        assert self.path.resolve().is_relative_to(self.root.resolve())
        shutil.rmtree(self.path)

    def add_report(self,code,year):
        relative = f'reports/{code}/{year}.pdf'
        path = self.library / relative
        path.parent.mkdir(parents=True,exist_ok=True)
        body = f'%PDF-1.7 local-library-test {code} {year}'.encode()
        path.write_bytes(body)
        sha = hashlib.sha256(body).hexdigest()
        doc_id = sha[:16]
        self.records.append({'stock_code':code,'company_name':f'测试公司{code}','report_year':year,
            'report_type':'annual','relative_path':relative,'sha256':sha,'bytes':len(body),
            'selected_for_comparison':True,'source_kind':'test_fixture','version_label':'original'})
        rows = [{'kind':'annual','metric_code':key,'label':key,'unit':'元','values':[value,'80'],
                 'source':{'document_id':doc_id,'page_start':2,'page_end':2,'section':'annual_summary'}}
                for key,value in [('revenue','100'),('operating_cost','60' if code=='300750' else '80')]]
        self.parsed[path] = {'document_id':doc_id,'company_name':f'测试公司{code}','report_year':year,
            'report_type':'annual','currency':'CNY','file_sha256':sha,'parser':'fixture','rows':rows,'parser_warnings':[]}
        self.identities[path] = {'stock_code':code,'status':'pdf_code_reported','sources':[]}

    def write_index(self):
        (self.library/'index.json').write_text(json.dumps({'schema_version':'1.0','snapshot_version':'test',
            'reports':self.records}),encoding='utf-8')

    def online(self, **config):
        response = self.client.post('/api/tasks/online',json={'query':'300750','report_year':2025,'lookback_years':0,**config})
        self.assertEqual(response.status_code,202)
        task = core.task_by_id(response.json()['id'])
        self.assertEqual(task['status'],'completed',task.get('error'))
        return task

    def test_online_current_history_and_peers_use_library_without_network(self):
        task = self.online(lookback_years=2,peer_codes=['002074','300207'],peer_group='人工研究',selection_note='测试人工业务选择')
        payload = task['result']['payload']
        self.assertEqual(payload['history']['status'],'not_requested')
        self.assertEqual(payload['peers']['status'],'complete')
        child=core.task_by_id(task['latest_backfill_task_id'])
        self.assertEqual(child['status'],'completed',child.get('error'))
        payload=child['result']['payload']
        self.assertEqual(payload['history']['status'],'complete')
        self.assertEqual(payload['history']['comparison']['report_years'],[2023,2024,2025])
        self.assertEqual(payload['peers']['status'],'complete')
        files = [r for r in payload['retrieval']['files'] if r['status']=='accepted']
        self.assertEqual(len(files),5)
        self.assertTrue(all(r['cache_status']=='verified_local_library' for r in files if r['role']!='history'))
        self.assertTrue(all(row['source']=='本地财报库' for row in child['backfill_rows'] if row['year']!=2025))
        self.assertEqual(self.client.get(f"/api/tasks/{task['id']}/file").status_code,200)
        for mock in self.mocks[5:]:mock.assert_not_called()

    def test_separate_backfill_peer_jobs_and_evidence_preserve_prior_results(self):
        base = self.online()
        original = copy.deepcopy(base['result'])
        response = self.client.post(f"/api/tasks/{base['id']}/backfill",json={'lookback_years':2,'online':False})
        self.assertEqual(response.status_code,202)
        history = core.task_by_id(response.json()['id'])
        self.assertEqual(history['result']['payload']['history']['status'],'complete')
        self.assertEqual(history['backfill_rows'][1]['source'],'本地财报库')
        self.assertEqual(self.client.get(f"/api/tasks/{history['id']}/backfill/files/2024").status_code,200)
        history_original = copy.deepcopy(history['result'])
        response = self.client.post(f"/api/tasks/{history['id']}/peers",json={
            'peer_codes':['002074','300207'],'peer_group':'人工研究','selection_note':'测试人工选择依据'})
        self.assertEqual(response.status_code,202)
        peer = core.task_by_id(response.json()['id'])
        self.assertEqual(peer['status'],'completed',peer.get('error'))
        self.assertEqual(peer['result']['payload']['history'],history_original['payload']['history'])
        for task_id,saved in [(base['id'],original),(history['id'],history_original)]:
            response=core.get_result(task_id)
            self.assertIn('财务分析摘要',response.pop('report_summary_markdown'))
            parameters=response.pop('analysis_parameters',None)
            if parameters is not None:
                self.assertEqual(parameters['minimum_negative_cash_quarters'],2)
            self.assertEqual(response,saved)
            self.assertEqual(core.task_by_id(task_id)['result'],saved)
        # Both peer and historical facts open their own source, not the target.
        for entry in peer['result']['evidence'].values():
            response = self.client.get(f"/api/tasks/{peer['id']}/file",params={'fact_id':entry['fact_id']})
            self.assertEqual(response.status_code,200)
            self.assertEqual(hashlib.sha256(response.content).hexdigest(),entry['file_hash'])
        self.assertEqual(self.client.get(f"/api/tasks/{peer['id']}/file",params={'document_id':'unknown'}).status_code,404)

    def test_upload_automatic_history_uses_same_library(self):
        parsed = self.parsed[self.library/'reports/300750/2025.pdf']
        from financial_engine import analyze_parsed_documents
        analysis = analyze_parsed_documents([parsed])
        analysis['documents'][0]['security_code']='300750'
        path = core.UPLOAD_DIR/'uploaded.pdf'
        path.write_bytes((self.library/'reports/300750/2025.pdf').read_bytes())
        self.parsed[path] = copy.deepcopy(parsed)
        self.identities[path] = {'stock_code':'300750','status':'pdf_code_reported','sources':[]}
        task = core.create_task('upload','uploaded.pdf',{'lookback_years':2})
        core.update_task(task['id'],file_path=str(path))
        with patch.object(core,'analyze_uploaded_document',return_value={'analysis':analysis}):
            core.run_upload_task(task['id'],path)
        history = core.task_by_id(task['latest_backfill_task_id'])
        self.assertEqual(history['result']['payload']['history']['available_years'],[2023,2024,2025])
        self.assertEqual(task['result']['analysis'],analysis)

    def test_search_keeps_existing_catalog_and_adds_pdf_availability_only(self):
        response = self.client.get('/api/companies',params={'q':'300750','report_year':2025}).json()
        self.assertGreaterEqual(response['catalog_size'],55)
        self.assertEqual(response['items'][0]['data_status']['state'],'pdf_saved')
        self.assertEqual(response['items'][0]['data_status']['metrics'],0)
        self.assertEqual(core.resolve_security('库内别名300750')['stock_code'],'300750')
        new = self.client.get('/api/companies',params={'q':'999999','report_year':2025}).json()['items'][0]
        self.assertEqual(new['data_status']['state'],'unknown')
        self.assertFalse(list(core.TASK_DIR.glob('*.json')))

    def test_missing_year_offline_is_partial_not_fabricated(self):
        with patch.dict(os.environ,{'FINANCE_LIBRARY_ALLOW_ONLINE':'0'}):
            task = self.online(lookback_years=5)
        self.assertEqual(task['result']['payload']['history']['status'],'not_requested')
        history = core.task_by_id(task['latest_backfill_task_id'])['result']['payload']['history']
        self.assertEqual(history['status'],'partial')
        self.assertEqual(history['missing_years'],[2020,2021,2022])
        self.assertEqual(history['available_years'],[2023,2024,2025])
        for mock in self.mocks[5:]:mock.assert_not_called()

    def test_missing_library_retains_online_fallback(self):
        with patch.object(core,'get_report_library',return_value=None),patch.object(core,'_engine_disclosure_context',return_value={}) as engine:
            core.analyze_disclosure_context(stock_code='300750',report_year=2025,fetch_online=True)
        self.assertTrue(engine.call_args.kwargs['fetch_online'])
        self.assertNotIn('local_report_provider',engine.call_args.kwargs)

    def test_mixed_local_and_missing_peer_only_fetches_missing_peer(self):
        self.add_report('300014',2025)
        record = self.records.pop()
        source = self.library/record['relative_path']
        downloaded = core.CACHE_DIR/'300014-2025.pdf'
        shutil.copyfile(source,downloaded)
        self.parsed[downloaded] = self.parsed[source]
        self.identities[downloaded] = self.identities[source]
        record.update(published_at='2026-03-01',announcement_id='123',local_path=str(downloaded))
        with patch('financial_engine.mining.resolve_security',return_value={'stock_code':'300014'}), \
             patch('financial_engine.mining.discover_annual_reports',return_value=[record]) as discover, \
             patch('financial_engine.mining.download_report',return_value=record) as download:
            task = self.online(peer_codes=['002074','300014'],peer_group='人工分组',selection_note='混合来源接口验证')
        self.assertEqual(task['result']['payload']['peers']['status'],'complete')
        self.assertEqual(discover.call_count,1)
        self.assertEqual(download.call_count,1)
        self.assertEqual(download.call_args.args[0]['stock_code'],'300014')

    def test_online_stop_after_current_parse_does_not_parse_history_or_generate_report(self):
        task = core.create_task('cninfo','stop-test')
        def stop(path):
            core.cancel_task(task['id'])
            return copy.deepcopy(self.parsed[Path(path)])
        with patch('financial_engine.mining.parse_pdf',side_effect=stop) as parse, patch.object(core,'generate_report') as model:
            core.run_online_task(task['id'],core.OnlineAnalysisRequest(query='300750',report_year=2025,lookback_years=2))
        self.assertEqual(parse.call_count,1)
        model.assert_not_called()
        self.assertEqual(task['status'],'interrupted')
        self.assertIsNone(task['result'])

    def test_health_and_invalid_provider_arguments(self):
        health = self.client.get('/api/health').json()
        self.assertEqual(health['report_library']['pdf_count'],5)
        self.assertTrue(health['report_library']['available'])
        for code,year in [('300750',True),(300750,2025),('750',2025),('300750','2025')]:
            with self.assertRaises(LibraryError):core.get_report_library().provide(code,year)

    def test_tamper_wrong_identity_and_wrong_year_are_rejected(self):
        path = self.library/'reports/300750/2025.pdf'
        self.identities[path]['stock_code']='002074'
        response = core.analyze_disclosure_context(stock_code='300750',report_year=2025,lookback_years=0,fetch_online=False)
        self.assertIsNone(response['current'])
        self.identities[path]['stock_code']='300750'
        self.parsed[path]['report_year']=2024
        response = core.analyze_disclosure_context(stock_code='300750',report_year=2025,lookback_years=0,fetch_online=False)
        self.assertIsNone(response['current'])
        self.parsed[path]['report_year']=2025
        task = self.online()
        path.write_bytes(b'%PDF-tampered')
        self.assertEqual(self.client.get(f"/api/tasks/{task['id']}/file").status_code,404)
        with self.assertRaises(LibraryError):core.get_report_library().provide('300750',2025)

    def test_history_checks_metadata_but_file_open_and_analysis_still_hash(self):
        task=self.online()
        with patch('report_library.digest',side_effect=AssertionError('history must not hash PDFs')):
            response=self.client.get('/api/history')
        self.assertEqual(response.status_code,200)
        self.assertTrue(response.json()['items'][0]['source_available'])
        path=self.library/'reports/300750/2025.pdf'
        path.write_bytes(b'%PDF-tampered')
        # A visible source link is an availability hint, never a SHA certificate.
        self.assertEqual(self.client.get(f"/api/tasks/{task['id']}/file").status_code,404)
        with self.assertRaises(LibraryError):core.get_report_library().provide('300750',2025)

    def test_index_cannot_escape_and_multiple_versions_are_not_silently_selected(self):
        record = self.records[0]
        record['relative_path']='../outside.pdf'
        self.write_index()
        with self.assertRaises(LibraryError):ReportLibrary(self.library)
        record['relative_path']='reports/300750/2025.pdf'
        self.add_report('300750',2025)
        self.records[-1]['relative_path']='reports/300750/2025-copy.pdf'
        shutil.copyfile(self.library/'reports/300750/2025.pdf',self.library/'reports/300750/2025-copy.pdf')
        self.write_index()
        with self.assertRaises(LibraryError):ReportLibrary(self.library).provide('300750',2025)

    def test_cancelled_backfill_does_not_read_or_publish(self):
        task = self.online()
        history = core.create_backfill_task(task['id'],{'lookback_years':2})
        core.cancel_task(history['id'])
        with patch.object(backfill_api,'report_analysis') as parser:
            core.run_backfill_task(history['id'])
        parser.assert_not_called()
        self.assertEqual(history['status'],'interrupted')
        self.assertIsNone(history['result'])

    def test_explicit_wrapped_row_unit_is_used_without_guessing(self):
        self.assertEqual(_summary_monetary_unit('营业收入（万\n元）',None),'万元')
        self.assertEqual(_summary_monetary_unit('营业收入（万元）','元'),'万元')
        self.assertIsNone(_summary_monetary_unit('营业收入',None))
        with self.assertRaises(ValueError):_summary_monetary_unit('收入（元）（万元）',None)


if __name__=='__main__':unittest.main()
