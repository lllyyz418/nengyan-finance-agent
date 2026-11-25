import copy
import shutil
import unittest
import uuid
from unittest.mock import patch
from fastapi.testclient import TestClient
import app as core
from financial_engine import analyze_parsed_documents
import peer_api


def financial(code, name, year, cost):
    doc_id = f'{code}-{year}'
    rows = [{'kind':'annual','metric_code':key,'label':key,'unit':'元','values':[value,'80'],
        'source':{'document_id':doc_id,'page_start':2,'page_end':2,'section':'annual_summary'}}
        for key,value in [('revenue','100'),('operating_cost',cost)]]
    analysis = analyze_parsed_documents([{'document_id':doc_id,'company_name':name,'report_year':year,
        'report_type':'annual','currency':'CNY','file_sha256':doc_id,'rows':rows}])
    analysis['documents'][0]['security_code'] = code
    return analysis


class BoundPeerTests(unittest.TestCase):
    def setUp(self):
        self.root=core.BASE_DIR/'.history-tests';self.path=self.root/uuid.uuid4().hex;self.path.mkdir(parents=True)
        paths={name:self.path/name for name in ('TASK_DIR','LOG_DIR','RESULT_DIR','UPLOAD_DIR','CACHE_DIR')}
        for path in paths.values():path.mkdir()
        self.patches=patch.multiple(core,**paths,TASKS={},_CANCELLED_TASKS=set());self.patches.start()
        self.client=TestClient(core.app)
        self.base=core.create_task('upload','工程公司2024.pdf')
        self.original={'engine_version':'0.17','analysis':financial('601868','中国能源建设股份有限公司',2024,'60'),
            'payload':{'history':{'available_years':[2022,2023,2024]}},'report_markdown':'# 原报告','report_generator':'deepseek','evidence':{}}
        core.update_task(self.base['id'],status='completed',result=copy.deepcopy(self.original))
        self.config={'peer_codes':['601669','600900'],'peer_group':'人工研究分组','selection_note':'测试人工确认业务与口径'}

    def tearDown(self):
        self.patches.stop();assert self.path.resolve().is_relative_to(self.root.resolve());shutil.rmtree(self.path)

    def acquired(self, **kwargs):
        return {'current':financial(kwargs['stock_code'],'同行公司',kwargs['report_year'],'80'), 'retrieval':{'files':[]}}

    def test_reuses_loaded_report_and_year_without_downloading_target_or_regenerating_ai(self):
        with patch.object(core,'analyze_disclosure_context',side_effect=self.acquired) as acquire, patch.object(core,'generate_report') as model:
            response=self.client.post(f"/api/tasks/{self.base['id']}/peers",json={**self.config,'report_year':2025})
        self.assertEqual(response.status_code,202)
        result=core.TASKS[response.json()['id']]['result']
        self.assertEqual([call.kwargs['stock_code'] for call in acquire.call_args_list],self.config['peer_codes'])
        self.assertTrue(all(call.kwargs['report_year']==2024 and call.kwargs['lookback_years']==0 for call in acquire.call_args_list))
        model.assert_not_called()
        self.assertEqual(result['analysis'],self.original['analysis'])
        self.assertEqual(result['report_markdown'],'# 原报告')
        self.assertEqual(result['payload']['history'],self.original['payload']['history'])
        self.assertEqual(core.TASKS[self.base['id']]['result'],self.original)
        comparison=result['payload']['peers']['comparison']
        gross=next(item for item in comparison['metric_comparisons'] if item['metric_code']=='gross_margin')
        self.assertEqual(gross['target']['value'],'40.0')
        saved=self.client.get(f"/api/tasks/{self.base['id']}/peers").json()
        self.assertEqual(saved['task_id'],response.json()['id'])
        backfill=core.create_task('history_backfill')
        core.update_task(backfill['id'],status='completed',parent_task_id=self.base['id'],result=copy.deepcopy(self.original))
        self.assertEqual(self.client.get(f"/api/tasks/{backfill['id']}/peers").json()['task_id'],response.json()['id'])

    def test_target_cannot_be_changed_by_client(self):
        with patch.object(core,'analyze_disclosure_context') as acquire:
            response=self.client.post(f"/api/tasks/{self.base['id']}/peers",json={**self.config,'target_code':'300750','confirm_identity':True})
        self.assertEqual(response.status_code,409);acquire.assert_not_called()

    def test_interim_peer_requests_bind_same_type_and_quarter(self):
        from test_periodic_reports import fixture
        from financial_engine.interim import analyze_parsed_interim
        target=analyze_parsed_interim(fixture())
        core.TASKS[self.base['id']]['result']['analysis']=target
        def acquire(**kwargs):
            self.assertEqual(kwargs['report_type'],'quarterly');self.assertEqual(kwargs['report_quarter'],3)
            parsed=fixture(doc_id=kwargs['stock_code']);parsed['security_code']=kwargs['stock_code']
            return {'current':analyze_parsed_interim(parsed),'retrieval':{'files':[]}}
        with patch.object(core,'analyze_disclosure_context',side_effect=acquire),patch.object(core,'generate_report') as model:
            response=self.client.post(f'/api/tasks/{self.base["id"]}/peers',json=self.config)
        self.assertEqual(response.status_code,202)
        result=core.task_by_id(response.json()['id'])['result']
        self.assertEqual(result['payload']['peer_binding']['report_quarter'],3)
        margin=next(row for row in result['payload']['peers']['comparison']['metric_comparisons'] if row['metric_code']=='gross_margin')
        self.assertEqual(margin['valid_peer_count'],2);self.assertEqual(result['report_markdown'],'# 原报告');model.assert_not_called()

    def test_no_report_or_nonperiodic_report_is_blocked(self):
        self.assertEqual(self.client.post('/api/tasks/'+'f'*32+'/peers',json=self.config).status_code,404)
        core.TASKS[self.base['id']]['result']['analysis']['documents'][0]['report_type']='announcement'
        self.assertEqual(self.client.post(f"/api/tasks/{self.base['id']}/peers",json=self.config).status_code,409)

    def test_missing_identity_requires_confirmation_and_matching_company(self):
        core.TASKS[self.base['id']]['result']['analysis']['documents'][0].pop('security_code')
        core.TASKS[self.base['id']]['result']['analysis']['documents'][0]['company_name']='无目录映射的测试公司'
        core.update_task(self.base['id'],result=core.TASKS[self.base['id']]['result'])
        # This case specifically has no registered-name mapping. A known full
        # registered name can now legitimately recover its code without input.
        with patch.object(core,'analyze_disclosure_context',side_effect=AssertionError('unexpected acquisition')):
            self.assertEqual(self.client.post(f"/api/tasks/{self.base['id']}/peers",json=self.config).status_code,409)
        with patch.object(core,'resolve_security',return_value={'stock_code':'601868'}),patch.object(core,'analyze_disclosure_context',side_effect=self.acquired):
            wrong=self.client.post(f"/api/tasks/{self.base['id']}/peers",json={**self.config,'target_code':'300750','confirm_identity':True})
            self.assertEqual(wrong.status_code,409)
            good=self.client.post(f"/api/tasks/{self.base['id']}/peers",json={**self.config,'target_code':'601868','confirm_identity':True})
            self.assertEqual(good.status_code,202)
            self.assertEqual(core.TASKS[self.base['id']]['confirmed_peer_code'],'601868')

    def test_registered_full_name_recovers_code_without_manual_reentry(self):
        core.TASKS[self.base['id']]['result']['analysis']['documents'][0].pop('security_code')
        core.update_task(self.base['id'],result=core.TASKS[self.base['id']]['result'])
        with patch.object(core,'analyze_disclosure_context',side_effect=self.acquired),patch.object(core,'generate_report') as model:
            response=self.client.post(f"/api/tasks/{self.base['id']}/peers",json=self.config)
        self.assertEqual(response.status_code,202)
        model.assert_not_called()

    def test_stop_between_peers_does_not_request_remaining_reports(self):
        def cancel(**kwargs):
            child=next(task for task in core.TASKS.values() if task['source']=='peer_compare')
            core.cancel_task(child['id'])
            return self.acquired(**kwargs)
        with patch.object(core,'analyze_disclosure_context',side_effect=cancel) as acquire:
            response=self.client.post(f"/api/tasks/{self.base['id']}/peers",json=self.config)
        child=core.TASKS[response.json()['id']]
        self.assertEqual(child['status'],'interrupted');self.assertEqual(acquire.call_count,1)
        self.assertIsNone(child['result']);self.assertEqual(core.TASKS[self.base['id']]['result'],self.original)

    def test_duplicates_self_target_and_six_peers_are_blocked(self):
        for codes in [['601669','601669'],['601868','601669']]:
            self.assertEqual(self.client.post(f"/api/tasks/{self.base['id']}/peers",json={**self.config,'peer_codes':codes}).status_code,400)
        self.assertEqual(self.client.post(f"/api/tasks/{self.base['id']}/peers",json={**self.config,'peer_codes':['000001']*6}).status_code,422)

    def test_failed_peer_does_not_erase_target_history(self):
        with patch.object(core,'analyze_disclosure_context',side_effect=RuntimeError('测试网络失败')):
            response=self.client.post(f"/api/tasks/{self.base['id']}/peers",json=self.config)
        child=core.TASKS[response.json()['id']]
        self.assertEqual(child['result']['payload']['peers']['status'],'partial')
        self.assertEqual(child['result']['payload']['history'],self.original['payload']['history'])
        self.assertEqual(child['result']['payload']['peers']['missing_codes'],self.config['peer_codes'])


if __name__=='__main__':unittest.main()
