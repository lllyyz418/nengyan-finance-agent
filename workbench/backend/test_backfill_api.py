"""Isolated regression fixtures: synthetic reports, mocked parser and network."""
import copy
import json
import shutil
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
import app as core
import backfill_api as backfill


def analysis(year=2025, code='300750', name='测试公司'):
    document_id=f'{code}-{year}'
    return {'documents':[{'document_id':document_id,'company_name':name,'security_code':code,
                           'report_year':year,'report_type':'annual'}],
            'facts':[], 'metrics':[{'document_id':document_id,'metric_id':f'{document_id}-margin',
                                    'metric_code':'gross_margin','period':str(year),'value':'25.5',
                                    'unit':'percent','status':'calculated','input_fact_ids':[]}],
            'unavailable_metrics':[],'risk_assessments':[],'warnings':[]}


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.test_root=core.BASE_DIR/'.history-tests'
        self.directory=self.test_root/uuid.uuid4().hex
        self.directory.mkdir(parents=True)
        paths={key:self.directory/key for key in ('TASK_DIR','RESULT_DIR','UPLOAD_DIR','CACHE_DIR','LOG_DIR','BACKFILL_DIR','PARSED_DIR')}
        for path in paths.values():path.mkdir()
        self.patch=patch.multiple(core,**paths,TASKS={});self.patch.start()
        self.client=TestClient(core.app)
        self.base=core.create_task('upload','测试公司2025.pdf',{'lookback_years':0,'nonrecurring_ratio_threshold':'0.45'})
        self.path=core.UPLOAD_DIR/f'{self.base["id"]}.pdf';self.path.write_bytes(b'%PDF-1.4 2025 current')
        result={'engine_version':'test','analysis':analysis(),'payload':{},'evidence':{},'report_markdown':'# 本期原报告','report_generator':'test'}
        core.update_task(self.base['id'],status='completed',file_path=str(self.path),result=result)
        self.original=copy.deepcopy(result)
        self.identity=patch.object(backfill,'_pdf_security_identity',return_value={'stock_code':'300750','status':'pdf_code_reported'})
        self.identity.start()
        self.discovery=patch.object(backfill,'discover_annual_reports',return_value=[])
        self.remote=self.discovery.start()
        self.security=patch.object(backfill,'resolve_security',return_value={'stock_code':'300750'})
        self.security.start()

    def tearDown(self):
        self.identity.stop();self.discovery.stop();self.security.stop();self.patch.stop()
        assert self.directory.resolve().is_relative_to(self.test_root.resolve())
        shutil.rmtree(self.directory)

    def create(self, years=2, **config):
        return backfill.create_job(core,self.base['id'],{'lookback_years':years,**config})

    def stage(self,year=2024,tag=''):
        response=self.client.post(f'/api/tasks/{self.base["id"]}/backfill/files',
            content=f'%PDF-1.4 {year} {tag}'.encode(),headers={'X-Filename':f'year-{year}.pdf'})
        self.assertEqual(response.status_code,201)
        return response.json()['token']

    @staticmethod
    def fake_parse(path):
        year=int(path.read_text().split()[1])
        return {'report_year':year,'report_type':'annual','company_name':'测试公司','document_id':f'doc-{year}'}

    @staticmethod
    def fake_engine(documents,options):
        return analysis(documents[0]['report_year'])

    def test_missing_years_partial_preserves_original_and_no_fake_years(self):
        job=self.create()
        backfill.run_job(core,job['id'])
        result=core.get_result(job['id'])
        self.assertEqual(result['payload']['history']['requested_years'],[2023,2024,2025])
        self.assertEqual(result['payload']['history']['available_years'],[2025])
        self.assertEqual(result['payload']['history']['status'],'partial')
        response=core.get_result(self.base['id'])
        self.assertIn('财务分析摘要',response.pop('report_summary_markdown'))
        self.assertEqual(response,self.original)
        self.assertEqual(core.task_by_id(self.base['id'])['result'],self.original)
        self.assertEqual(self.remote.call_count,1)
        self.assertEqual(core.task_by_id(job['id'])['status'],'completed')

    def test_legacy_current_and_local_history_get_liabilities_without_overwriting_original(self):
        token=self.stage(2024)
        def parse(path):
            doc=self.fake_parse(path)
            doc['rows']=[{'kind':'balance_sheet','label':label,'metric_code':code,'unit':'万元','values':[value],
                          'source':{'page_start':2,'page_end':2,'section':'consolidated_balance_sheet'}}
                         for label,code,value in [('资产总计','total_assets','1000'),('负债合计','total_liabilities','600' if doc['report_year']==2025 else '500')]]
            return doc
        with patch.object(backfill,'parse_pdf',side_effect=parse) as parser,patch.object(core,'generate_report') as model:
            job=self.create(1,file_tokens=[token],online=False)
            backfill.run_job(core,job['id'])
        self.assertEqual(parser.call_count,2)
        model.assert_not_called()
        self.remote.assert_not_called()
        result=core.get_result(job['id'])
        series=result['payload']['history']['comparison']['series']['liabilities_to_assets']
        self.assertEqual([(item['year'],item['value']) for item in series],[(2024,'50.0'),(2025,'60.0')])
        self.assertEqual(result['analysis'],self.original['analysis'])
        self.assertEqual(result['report_markdown'],self.original['report_markdown'])
        self.assertEqual(core.TASKS[self.base['id']]['result'],self.original)
        self.assertEqual(result['payload']['history']['calculation_version'],backfill.CACHE_VERSION)

    def test_policy_comparison_and_prior_source_join_only_new_history_version(self):
        from test_policy_reviews import document,dep_page
        from financial_engine.core import analyze_parsed_documents
        current=document(pages=[dep_page(change='unknown')],did='doc-2025',company='测试公司')
        original={**self.original,'analysis':analyze_parsed_documents([current])}
        original['evidence']=core.evidence_index(original['analysis'])
        core.update_task(self.base['id'],result=original)
        token=self.stage(2024)
        def parse(path):
            year=int(path.read_text().split()[1])
            return document(year,[dep_page(change='unknown')],did=f'doc-{year}',company='测试公司')
        with patch.object(backfill,'parse_pdf',side_effect=parse),patch.object(core,'generate_report') as model:
            job=self.create(1,file_tokens=[token],online=False)
            backfill.run_job(core,job['id'])
        result=core.get_result(job['id'])
        self.assertEqual(core.task_by_id(job['id'])['status'],'completed')
        dep=next(r for r in result['analysis']['risk_assessments'] if r['rule_id']=='depreciation_policy_change')
        self.assertEqual(dep['status'],'reviewed')
        eid=next(e for e in dep['evidence_policy_ids'] if e.startswith('doc-2024:'))
        response=self.client.get(f'/api/tasks/{job["id"]}/evidence/{eid}')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['document_id'],'doc-2024')
        self.assertTrue(response.json()['source_available'])
        self.assertEqual(core.task_by_id(self.base['id'])['result'],original)
        self.assertEqual(result['report_markdown'],original['report_markdown'])
        self.assertIn('会计政策与估计披露复核',result['report_summary_markdown'])
        model.assert_not_called()

    def test_new_cache_version_reparses_old_cache_and_then_reuses_new_result(self):
        import hashlib
        options={}
        old_key=hashlib.sha256((backfill.digest(self.path)+'300750'+'测试公司'+'annual-history-5-cover-company-name'+json.dumps(options,sort_keys=True)).encode()).hexdigest()
        (core.PARSED_DIR/f'{old_key[:40]}.json').write_text(json.dumps(analysis()),encoding='utf-8')
        with patch.object(backfill,'parse_pdf',side_effect=self.fake_parse) as parser,patch.object(backfill,'analyze_parsed_documents',side_effect=self.fake_engine):
            _,_,first_reused=backfill.report_analysis(core,self.path,'300750','测试公司',options)
            _,_,second_reused=backfill.report_analysis(core,self.path,'300750','测试公司',options)
        self.assertFalse(first_reused)
        self.assertTrue(second_reused)
        self.assertEqual(parser.call_count,1)

    def test_manual_merge_parse_cache_range_extension_and_reduction(self):
        token=self.stage()
        with patch.object(backfill,'parse_pdf',side_effect=self.fake_parse) as parser, patch.object(backfill,'analyze_parsed_documents',side_effect=self.fake_engine):
            job=self.create(1,file_tokens=[token],online=False);backfill.run_job(core,job['id'])
            self.assertEqual(core.get_result(job['id'])['payload']['history']['comparison']['report_years'],[2024,2025])
            self.assertEqual(parser.call_count,2,'Legacy current report and one historical report are refreshed locally')
            extended=backfill.create_job(core,job['id'],{'lookback_years':2,'online':False})
            backfill.run_job(core,extended['id'])
            self.assertEqual(parser.call_count,2,'Unchanged files/options must reuse parsed results')
            self.assertEqual(extended['request']['options']['nonrecurring_ratio_threshold'],'0.45')
            reduced=backfill.create_job(core,extended['id'],{'lookback_years':0})
            backfill.run_job(core,reduced['id'])
            self.assertEqual(core.get_result(reduced['id'])['payload']['history']['requested_years'],[2025])
            self.assertEqual(core.get_result(reduced['id'])['payload']['history']['comparison']['report_years'],[2025])
            saved=self.client.get(f'/api/tasks/{job["id"]}/backfill').json()
            self.assertEqual(saved['lookback_years'],1,'Opening an older version must keep its original scope')
            self.assertEqual(len(saved['rows']),2)
        self.remote.assert_not_called()
        self.assertTrue((core.BACKFILL_DIR/f'{token}.pdf').exists())
        response=core.get_result(self.base['id'])
        self.assertIn('财务分析摘要',response.pop('report_summary_markdown'))
        self.assertEqual(response,self.original)
        self.assertEqual(core.task_by_id(self.base['id'])['result'],self.original)

    def test_identity_missing_requires_confirmation_before_any_network(self):
        self.base['result']['analysis']['documents'][0].pop('security_code')
        with patch.object(backfill,'identity_for',return_value={'stock_code':None,'status':'absent'}):
            job=self.create();backfill.run_job(core,job['id'])
            self.assertEqual(job['status'],'review_required')
            self.remote.assert_not_called()
            confirmed=self.create(1,stock_code='300750',confirm_identity=True)
            backfill.run_job(core,confirmed['id'])
            self.assertEqual(confirmed['status'],'completed')
            self.assertEqual(confirmed['verified_stock_code'],'300750')

    def test_explicit_code_conflict_cannot_be_overridden(self):
        job=self.create(stock_code='601012',confirm_identity=True)
        backfill.run_job(core,job['id'])
        self.assertEqual(job['status'],'failed');self.remote.assert_not_called()

    def test_manual_wrong_year_and_duplicate_versions_rejected(self):
        outside=self.stage(2022)
        a=self.stage(2024,'first');b=self.stage(2024,'second')
        with patch.object(backfill,'parse_pdf',side_effect=self.fake_parse),patch.object(backfill,'analyze_parsed_documents',side_effect=self.fake_engine):
            job=self.create(1,file_tokens=[outside,a,b],online=False)
            backfill.run_job(core,job['id'])
        self.assertEqual(len(job['backfill_rejected']),1)
        self.assertIn('多个不同版本',job['backfill_rows'][1]['message'])
        self.assertEqual(core.get_result(job['id'])['payload']['history']['available_years'],[2025])

    def test_manual_identity_type_and_company_validation(self):
        token=self.stage()
        path=core.BACKFILL_DIR/f'{token}.pdf'
        for document,identity in [
            ({'report_type':'half_year'},{'stock_code':'300750'}),
            ({'report_type':'annual','company_name':'另一家公司'},{'stock_code':None}),
            ({'report_type':'annual','company_name':'测试公司'},{'stock_code':'601012'}),
        ]:
            with patch.object(backfill,'parse_pdf',return_value=document),patch.object(backfill,'identity_for',return_value=identity):
                with self.assertRaises(ValueError):backfill.report_analysis(core,path,'300750','测试公司',{})

    def test_route_validation_file_binding_and_concurrent_launch(self):
        url=f'/api/tasks/{self.base["id"]}/backfill'
        self.assertEqual(self.client.post(url,json={'lookback_years':6}).status_code,422)
        self.assertEqual(self.client.post(url+'/files',content=b'not pdf',headers={'X-Filename':'a.pdf'}).status_code,400)
        token=self.stage()
        manifest=core.BACKFILL_DIR/f'{token}.json'
        manifest.write_text(json.dumps({'root_task_id':'different','file_name':'wrong.pdf'}))
        self.assertEqual(self.client.post(url,json={'lookback_years':1,'file_tokens':[token]}).status_code,400)
        with patch.object(backfill,'run_job'):
            response=self.client.post(url,json={'lookback_years':1})
        self.assertEqual(response.status_code,202)
        self.assertNotIn('backfill_files',response.json())
        self.assertEqual(self.client.post(url,json={'lookback_years':1}).status_code,409)
        summary=self.client.get(url).json()
        self.assertEqual(len(summary['rows']),2)
        self.assertEqual(self.client.get(f'/api/tasks/{response.json()["id"]}/backfill/files/2025').status_code,200)
        self.assertEqual(self.client.get(f'/api/tasks/{response.json()["id"]}/backfill/files/2024').status_code,404)

    def test_offline_upload_default_and_current_completion_before_backfill(self):
        task=core.create_task('upload','test.pdf',{'lookback_years':2})
        core.update_task(task['id'],file_path=str(self.path))
        def child_worker(task_id):
            self.assertEqual(core.task_by_id(task['id'])['status'],'completed')
            self.assertTrue(core.get_result(task['id'])['analysis']['documents'])
        with patch.object(core,'analyze_uploaded_document',return_value={'analysis':analysis()}),patch.object(core,'generate_report',return_value=('# test','deterministic_template')),patch.object(core,'run_backfill_task',side_effect=child_worker) as worker:
            core.run_upload_task(task['id'],self.path)
        worker.assert_called_once()
        with patch.object(core,'run_upload_task'):
            uploaded=self.client.post('/api/tasks/upload',content=b'%PDF-1.4',headers={'X-Filename':'a.pdf'})
        self.assertEqual(uploaded.status_code,202)
        self.assertEqual(uploaded.json()['request']['lookback_years'],0,'Announcement callers must not start history')

    def test_retry_backfill_preserves_source_type(self):
        job=self.create(0);backfill.run_job(core,job['id'])
        with patch.object(core,'run_backfill_task') as worker:
            response=self.client.post(f'/api/history/{job["id"]}/retry')
        self.assertEqual(response.status_code,202)
        self.assertEqual(response.json()['source'],'history_backfill')
        worker.assert_called_once()
        self.assertEqual(job['status'],'completed')

    def test_online_download_then_retry_reuses_selected_file(self):
        path=core.CACHE_DIR/'annual-2024.pdf';path.write_bytes(b'%PDF-1.4 2024')
        record={'stock_code':'300750','report_year':2024,'published_at':'2025-03-01','announcement_id':'123',
                'local_path':str(path),'title':'2024年年度报告','version_label':'original'}
        self.remote.return_value=[record]
        with patch.object(backfill,'download_report',return_value=record) as download,patch.object(backfill,'parse_pdf',side_effect=self.fake_parse),patch.object(backfill,'analyze_parsed_documents',side_effect=self.fake_engine):
            job=self.create(1);backfill.run_job(core,job['id'])
            self.assertEqual(job['status'],'completed')
            self.assertEqual(core.get_result(job['id'])['payload']['history']['status'],'complete')
            retry=backfill.create_job(core,job['id'],{'lookback_years':1})
            backfill.run_job(core,retry['id'])
            self.assertEqual(download.call_count,1)
            self.assertEqual(self.remote.call_count,1)
            response=self.client.get(f'/api/tasks/{retry["id"]}/backfill/files/2024')
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.content.startswith(b'%PDF-'))

    def test_foreign_evidence_never_falls_back_to_current_pdf(self):
        self.assertIsNone(core.evidence_file_path(self.base,{'document_id':'missing-history-document'}))
        self.assertEqual(core.evidence_file_path(self.base,{'document_id':'300750-2025'}),self.path)


if __name__=='__main__':unittest.main()
