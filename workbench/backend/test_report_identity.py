"""Identity regression cases, isolated from production tasks and models."""
import copy
import hashlib
import json
import shutil
import unittest
import uuid
from unittest.mock import patch

from fastapi.testclient import TestClient
import app as core
import backfill_api
import peer_api
from report_identity import resolve_identity
from test_peer_api import financial


class ReportIdentityTests(unittest.TestCase):
    def setUp(self):
        self.root=core.BASE_DIR/'.history-tests'
        self.path=self.root/uuid.uuid4().hex
        self.path.mkdir(parents=True)
        paths={name:self.path/name for name in ('TASK_DIR','RESULT_DIR','LOG_DIR','UPLOAD_DIR','CACHE_DIR','BACKFILL_DIR','PARSED_DIR')}
        for path in paths.values():path.mkdir()
        self.settings=patch.multiple(core,**paths,TASKS={},_CANCELLED_TASKS=set())
        self.settings.start()
        self.client=TestClient(core.app)
        self.base=core.create_task('upload','本期.pdf')
        self.analysis=financial('300750','未登记的测试公司',2025,'60')
        self.analysis['documents'][0].pop('security_code')
        self.analysis['documents'][0]['file_sha256']='a'*64
        self.original={'engine_version':'test','analysis':copy.deepcopy(self.analysis),'payload':{},'evidence':{},'report_markdown':'原报告','report_generator':'test'}
        self.file=core.UPLOAD_DIR/'original.pdf'
        self.file.write_bytes(b'%PDF-1.4 identity fixture')
        core.update_task(self.base['id'],status='completed',result=copy.deepcopy(self.original),file_path=str(self.file))

    def tearDown(self):
        self.settings.stop()
        assert self.path.resolve().is_relative_to(self.root.resolve())
        shutil.rmtree(self.path)

    def history(self, code='300750', **doc_updates):
        task=core.create_task('history_backfill','历史.pdf')
        result=copy.deepcopy(self.original)
        result['analysis']['documents'][0].update(doc_updates)
        core.update_task(task['id'],status='completed',parent_task_id=self.base['id'],verified_stock_code=code,result=result)
        core.update_task(self.base['id'],latest_backfill_task_id=task['id'])
        return task

    def test_old_upload_uses_verified_history_without_rewriting_snapshot(self):
        self.history()
        files={p:p.read_bytes() for p in core.TASK_DIR.glob('*.json')}
        response=self.client.get(f"/api/tasks/{self.base['id']}/result")
        result=response.json()
        self.assertEqual(result['analysis']['documents'][0]['security_code'],'300750')
        self.assertEqual(result['payload']['identity']['stock_code'],'300750')
        for key in ('metrics','facts','unavailable_metrics'):
            self.assertEqual(result['analysis'][key],self.original['analysis'][key])
        self.assertEqual(self.base['result'],self.original)
        self.assertTrue(all(p.read_bytes()==body for p,body in files.items()))
        _,_,_,_,code=peer_api.report_context(core,self.base['id'])
        self.assertEqual(code,'300750')
        records=self.client.get('/api/history').json()['items']
        self.assertEqual(next(r for r in records if r['id']==self.base['id'])['stock_code'],'300750')

    def test_top_level_history_metadata_alone_is_recovered(self):
        self.base['result']['history_backfill']={'stock_code':'300750'}
        self.assertEqual(core.get_result(self.base['id'])['analysis']['documents'][0]['security_code'],'300750')

    def test_other_pdf_of_same_company_and_year_is_not_reused(self):
        self.history(file_sha256='b'*64)
        self.assertIsNone(resolve_identity(core,self.base)['stock_code'])
        self.assertNotIn('security_code',core.get_result(self.base['id'])['analysis']['documents'][0])

    def test_trashed_or_unfinished_related_task_is_not_reused(self):
        child=self.history()
        for changes in ({'trashed':True},{'trashed':False,'status':'processing'}):
            child.update(changes)
            self.assertIsNone(resolve_identity(core,self.base)['stock_code'])

    def test_conflicting_codes_cannot_be_overridden_or_used_for_peers(self):
        self.history()
        self.base['result']['payload']['identity']={'stock_code':'002074','status':'pdf_code_reported'}
        result=self.client.get(f"/api/tasks/{self.base['id']}/result").json()
        self.assertEqual(result['report_identity']['status'],'conflict')
        response=self.client.post(f"/api/tasks/{self.base['id']}/peers",json={
            'peer_codes':['002074','300207'],'peer_group':'测试分组','selection_note':'测试不能覆盖冲突',
            'target_code':'300750','confirm_identity':True})
        self.assertEqual(response.status_code,409)

    def test_exact_catalog_full_name_is_automatic_but_partial_name_is_not(self):
        doc=self.base['result']['analysis']['documents'][0]
        doc['company_name']='宁德时代新能源科技股份有限公司'
        with patch.object(core,'resolve_security',side_effect=AssertionError('GET must not access network')):
            self.assertEqual(core.get_result(self.base['id'])['analysis']['documents'][0]['security_code'],'300750')
        doc['company_name']='宁德时代新能源科技'
        self.assertIsNone(resolve_identity(core,self.base)['stock_code'])

    def test_ambiguous_catalog_or_pdf_never_guesses(self):
        items=[{'name':'未登记的测试公司','code':code,'identity_status':'catalog_verified'} for code in ['300750','002074']]
        with patch('company_directory.directory',return_value=items):
            self.assertEqual(resolve_identity(core,self.base)['status'],'conflict')
        self.base['result']['analysis']['documents'][0]['company_name']='宁德时代新能源科技股份有限公司'
        identity=resolve_identity(core,self.base,pdf_identity={'status':'ambiguous','stock_code':None})
        self.assertEqual(identity['status'],'conflict')

    def test_new_upload_records_pdf_code_without_model_or_duplicate_prompt(self):
        task=core.create_task('upload','新上传.pdf',{'lookback_years':0})
        core.update_task(task['id'],file_path=str(self.file))
        with patch.object(core,'analyze_uploaded_document',return_value={'analysis':copy.deepcopy(self.analysis)}), \
             patch('financial_engine.mining._pdf_security_identity',return_value={'stock_code':'300750','status':'pdf_code_reported','sources':[]}) as reader, \
             patch.object(core,'generate_report',return_value=('测试报告','deterministic_template')):
            core.run_upload_task(task['id'],self.file)
        self.assertEqual(task['status'],'completed',task.get('error'))
        self.assertEqual(task['result']['analysis']['documents'][0]['security_code'],'300750')
        self.assertEqual(task['result']['payload']['identity']['stock_code'],'300750')
        reader.assert_called_once()

    def test_backfill_saves_identity_to_current_result_as_well_as_task_metadata(self):
        child=core.create_backfill_task(self.base['id'],{'lookback_years':1,'online':False})
        with patch.object(backfill_api,'identity_for',return_value={'stock_code':'300750','status':'pdf_code_reported','sources':[]}):
            core.run_backfill_task(child['id'])
        self.assertEqual(child['status'],'completed',child.get('error'))
        self.assertEqual(child['result']['analysis']['documents'][0]['security_code'],'300750')
        self.assertEqual(child['result']['payload']['identity']['stock_code'],'300750')
        self.assertEqual(self.base['result'],self.original)

    def test_peer_calculation_uses_recovered_identity_without_manual_input(self):
        self.history()
        def acquire(**kwargs):
            return {'current':financial(kwargs['stock_code'],'测试同行',2025,'80'),'retrieval':{'files':[]}}
        with patch.object(core,'analyze_disclosure_context',side_effect=acquire) as reports,patch.object(core,'generate_report') as model:
            response=self.client.post(f"/api/tasks/{self.base['id']}/peers",json={
                'peer_codes':['002074','300207'],'peer_group':'测试分组','selection_note':'测试自动恢复证券代码'})
        self.assertEqual(response.status_code,202)
        child=core.task_by_id(response.json()['id'])
        self.assertEqual(child['status'],'completed',child.get('error'))
        self.assertEqual(child['request']['target_code'],'300750')
        self.assertEqual(reports.call_count,2)
        model.assert_not_called()

    def test_new_issuer_reader_does_not_reuse_old_ambiguous_identity_cache(self):
        old_key=hashlib.sha256(('annual-history-3-local-library-units'+backfill_api.digest(self.file)).encode()).hexdigest()
        old_path=core.PARSED_DIR/f'i-{old_key[:40]}.json'
        old_text=json.dumps({'stock_code':None,'status':'ambiguous'})
        old_path.write_text(old_text,encoding='utf-8')
        with patch.object(backfill_api,'_pdf_security_identity',return_value={'stock_code':'600905','status':'pdf_code_reported','sources':[]}) as reader:
            self.assertEqual(backfill_api.identity_for(core,self.file)['stock_code'],'600905')
            self.assertEqual(backfill_api.identity_for(core,self.file)['stock_code'],'600905')
            reader.assert_called_once()
        self.assertEqual(old_path.read_text(encoding='utf-8'),old_text)


if __name__=='__main__':unittest.main()
