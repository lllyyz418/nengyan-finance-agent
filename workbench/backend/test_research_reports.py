"""Offline acceptance for evidence-linked, independent research versions."""
import copy
import hashlib
import json
import os
import shutil
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
import app as core
import research_reports as jobs
from research_context import build_context
from research_writer import validate_draft,generate,markdown


def analysis(year=2025,code='300750',company='测试新能源股份有限公司',kind='annual',quarter=None):
    identifier=f'{code}-{year}-{kind}'
    doc={'document_id':identifier,'security_code':code,'company_name':company,'report_year':year,
         'report_type':kind,'report_quarter':quarter,'file_sha256':hashlib.sha256(identifier.encode()).hexdigest(),'file_name':identifier+'.pdf'}
    period=str(year) if kind=='annual' else f'{year}H1' if kind=='semiannual' else f'{year}YTD9'
    facts=[{'fact_id':identifier+':'+key,'document_id':identifier,'metric_code':key,
            'value':value,'unit':'CNY','period':period,'statement_scope':'consolidated',
            'sources':[{'page_start':2,'page_end':2,'raw_text':key,'row_bbox':[10,20,300,40]}]}
           for key,value in [('revenue','100000000'),('operating_cost','75000000')]]
    metrics=[{'metric_id':identifier+':margin','document_id':identifier,'metric_code':'gross_margin',
              'value':'25','unit':'percent','period':period,'status':'calculated',
              'input_fact_ids':[f['fact_id'] for f in facts]}]
    return {'documents':[doc],'facts':facts,'metrics':metrics,'risk_assessments':[],
            'warnings':[],'unavailable_metrics':[]}


def draft(packet):
    observation=packet['observations'][0];reference=observation['evidence_ids'][0]
    def claim(title):
        return {'headline':title,'interpretation':'可核对的数据为 {{'+reference+'}}，需要结合现金回收与营运资金变化解读，当前材料并不足以确认具体经营成因。',
                'evidence_ids':list(observation['evidence_ids']),'observation_ids':[observation['id']],'alternatives':['结算时点可能影响观察，需结合原文和后续报告核实。'],
                'checks':['核对下一报告期的回款与存货变化。'],'limitation':'指标变化不是已证实的风险，也不是因果证明。'}
    return {'sections':[{'key':'overview','claims':[claim('将盈利表现与现金兑现结合观察')]},
                        {'key':'followup','claims':[claim('补齐经营变化的验证材料')]}]}


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.test_root=core.BASE_DIR/'.history-tests'
        self.directory=self.test_root/('research-'+uuid.uuid4().hex);self.directory.mkdir(parents=True)
        paths={name:self.directory/name for name in ('TASK_DIR','RESULT_DIR','UPLOAD_DIR','CACHE_DIR','LOG_DIR','BACKFILL_DIR','PARSED_DIR','COMPANY_DIR')}
        for path in paths.values():path.mkdir()
        self.settings=patch.multiple(core,**paths,TASKS={},_CANCELLED_TASKS=set());self.settings.start()
        self.env=patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-only-key','FINANCE_RESEARCH_AUTOGENERATE':'0'});self.env.start()
        self.futures=[];submit=jobs.EXECUTOR.submit
        def capture(*args,**kwargs):
            future=submit(*args,**kwargs);self.futures.append(future);return future
        self.capture=patch.object(jobs.EXECUTOR,'submit',side_effect=capture);self.capture.start()
        self.client=TestClient(core.app)
        self.base=self.task()

    def tearDown(self):
        for future in self.futures:future.result(timeout=5)
        self.capture.stop();self.env.stop();self.settings.stop()
        assert self.directory.resolve().is_relative_to(self.test_root.resolve())
        shutil.rmtree(self.directory)

    def task(self,data=None,source='upload',payload=None):
        data=data or analysis()
        task=core.create_task(source,'测试报告.pdf',{'lookback_years':0})
        path=core.UPLOAD_DIR/f"{task['id']}.pdf";path.write_bytes(b'%PDF-1.4 fixture')
        core.update_task(task['id'],status='completed',file_path=str(path),result={
            'engine_version':'test','analysis':data,'payload':payload or {},'evidence':core.evidence_index(data),
            'report_markdown':'# 原报告不覆盖','report_generator':'deterministic_template'})
        return task

    def packet(self,scope='available'):return build_context(core,self.base,scope)[0]

    def finish(self):
        for future in self.futures:future.result(timeout=5)

    def test_context_requires_real_stored_evidence_and_keeps_zero(self):
        data=self.base['result']['analysis'];data['metrics'][0]['value']='0'
        core.persist_task(self.base)
        packet=self.packet()
        point=next(p for p in packet['registry'].values() if p['kind']=='metric')
        self.assertEqual(point['display'],'0.00%')
        self.base['result']['evidence']={}
        with self.assertRaisesRegex(ValueError,'原文'):self.packet()

    def test_conflicting_values_and_identity_are_not_model_evidence(self):
        data=self.base['result']['analysis']
        data['facts'][0]['quality']='conflict'
        data['metrics'][0]['status']='unavailable'
        with self.assertRaisesRegex(ValueError,'研究观察'):self.packet()
        self.base['result']['report_identity']={'status':'conflict'}
        with self.assertRaisesRegex(ValueError,'身份'):self.packet()

    def test_same_report_saved_comparison_becomes_evidence_not_missing(self):
        metric=self.base['result']['analysis']['metrics'][0]
        metric['comparison']={'status':'calculated','value':'22','unit':'percent','period':'2024',
                              'input_fact_ids':list(metric['input_fact_ids'])}
        packet=self.packet()
        prior=next(p for p in packet['registry'].values() if p['code']=='gross_margin' and p['period']=='2024')
        self.assertEqual(prior['display'],'22.00%')
        observation=next(o for o in packet['observations'] if o['headline']=='毛利表现与经营结构')
        self.assertIn('高于同一报告',observation['fact'])
        self.assertIn(prior['id'],observation['evidence_ids'])

    def test_rule_observation_preserves_actual_conditions_not_metric_proxies(self):
        from research_observations import observations
        packet=self.packet()
        packet['registry']['m0999']={'id':'m0999','kind':'rule','scope':'本期','code':'receivables_aging_pressure',
            'label':'应收账款账龄压力筛查','display':'已触发，建议复核',
            'condition_text':'应收账款增速高于营业收入增速（满足）；一年以上账龄占比上升（满足）'}
        found=next(o for o in observations(packet) if o['evidence_ids']==['m0999'])
        self.assertIn('实际条件：应收账款增速',found['fact'])
        self.assertIn('一年以上账龄占比',found['fact'])
        self.assertNotIn('周转天数',found['fact'])

    def test_unresolved_history_is_a_check_not_an_operating_trend(self):
        from research_observations import observations
        packet=self.packet();current=next(p for p in packet['registry'].values() if p['kind']=='metric')
        packet['registry']['m0998']={**current,'id':'m0998','period':'2024','scope':'历年同期','comparison_blocked':True}
        packet['registry']['m0999']={**current,'id':'m0999','kind':'basis_check','scope':'历年同期',
            'display':'待核查','condition_text':'差额超出披露精度范围，原因尚未查明。'}
        found=observations(packet)
        self.assertFalse(any(o['headline'].startswith('同口径历史观察') for o in found))
        check=next(o for o in found if o['evidence_ids']==['m0999'])
        self.assertIn('不得编造重述理由',check['fact'])

    def test_history_is_same_issuer_type_and_quarter_and_changes_signature(self):
        _,before=build_context(core,self.base)
        prior=analysis(2024);foreign=analysis(2023,'999999','其他公司')
        merged={key:prior.get(key,[])+foreign.get(key,[]) for key in ('documents','facts','metrics')}
        child=self.task(data=analysis(),source='history_backfill',payload={'history':{'status':'partial','analysis':merged}})
        child['result']['evidence'].update(core.evidence_index(merged));core.persist_task(child)
        core.update_task(self.base['id'],latest_backfill_task_id=child['id'])
        packet,after=build_context(core,self.base)
        self.assertNotEqual(before,after)
        historical=[p for p in packet['registry'].values() if p['scope']=='历年同期']
        self.assertTrue(historical);self.assertTrue(all(p['security_code']=='300750' for p in historical))
        self.assertTrue(all(p['period']=='2024' for p in historical))
        self.assertFalse(any(c['role']=='历年同期' for c in self.packet('current')['coverage']))

    def test_new_peer_selection_does_not_merge_removed_companies(self):
        first=self.task(source='peer_compare',payload={'peers':{'status':'complete','reports':{'111111':analysis(code='111111',company='旧同行公司')}},'peer_binding':{'peer_codes':['111111']}})
        second=self.task(source='peer_compare',payload={'peers':{'status':'complete','reports':{'222222':analysis(code='222222',company='新同行公司')}},'peer_binding':{'peer_codes':['222222']}})
        for task in (first,second):
            for peer in task['result']['payload']['peers']['reports'].values():task['result']['evidence'].update(core.evidence_index(peer))
            core.persist_task(task)
        core.update_task(second['id'],parent_task_id=first['id'])
        core.update_task(self.base['id'],latest_peer_task_id=second['id'])
        companies={p['security_code'] for p in self.packet()['registry'].values() if p['scope']=='所选同行'}
        self.assertEqual(companies,{'222222'})

    def test_values_are_server_rendered_and_unknown_refs_are_rejected(self):
        packet=self.packet();raw=draft(packet);body=validate_draft(raw,packet)
        self.assertIn('25.00%',body['sections'][0]['claims'][0]['interpretation'])
        self.assertNotIn('{{',markdown(body))
        raw['sections'][0]['claims'][0]['evidence_ids']=['m9999']
        with self.assertRaisesRegex(ValueError,'证据'):validate_draft(raw,packet)

    def test_uncited_numbers_links_and_unsupported_peer_claims_fail_closed(self):
        packet=self.packet()
        for text in ['利润增长 99%。','估计未来增长十倍。','详情 https://untrusted.example。','建议买入以获取保证收益。']:
            raw=draft(packet);raw['sections'][0]['claims'][0]['interpretation']+=text
            with self.assertRaises(ValueError):validate_draft(raw,packet)
        raw=draft(packet);raw['sections'][1]['key']='peers'
        with self.assertRaisesRegex(ValueError,'比较资料'):validate_draft(raw,packet)

    def test_generation_uses_structured_output_and_rejects_truncation(self):
        packet=self.packet()
        response=SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=json.dumps(draft(packet))))])
        with patch('openai.OpenAI') as client:
            client.return_value.chat.completions.create.return_value=response
            body=generate(packet)
            kwargs=client.return_value.chat.completions.create.call_args.kwargs
            self.assertEqual(kwargs['response_format'],{'type':'json_object'})
            self.assertEqual(len(body['sections']),2)
            response.choices[0].finish_reason='length'
            with self.assertRaises(ValueError):generate(packet)

    def test_validation_feedback_repairs_once_and_never_loops(self):
        packet=self.packet();valid=draft(packet);invalid=copy.deepcopy(valid)
        invalid['sections'][0]['claims'][0]['interpretation']+='无依据的增长 99%。'
        def response(raw):return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=json.dumps(raw)))])
        with patch('openai.OpenAI') as client:
            method=client.return_value.chat.completions.create
            method.side_effect=[response(invalid),response(valid)]
            self.assertTrue(generate(packet)['sections']);self.assertEqual(method.call_count,2)
            method.reset_mock();method.side_effect=[response(invalid),response(invalid)]
            with self.assertRaises(ValueError):generate(packet)
            self.assertEqual(method.call_count,2)

    def test_claim_cannot_borrow_evidence_from_another_observation(self):
        packet=self.packet();raw=draft(packet)
        other=next(identifier for identifier in packet['registry'] if identifier not in raw['sections'][0]['claims'][0]['evidence_ids'])
        raw['sections'][0]['claims'][0]['evidence_ids'].append(other)
        with self.assertRaisesRegex(ValueError,'事实观察'):validate_draft(raw,packet)

    def test_cautious_negation_is_not_confused_with_a_guarantee(self):
        packet=self.packet();raw=draft(packet)
        raw['sections'][0]['claims'][0]['alternatives']=['不必然意味着回款恶化，需核对结算时点。']
        self.assertTrue(validate_draft(raw,packet)['sections'])

    def test_one_bad_detail_is_excluded_without_displaying_unsafe_text(self):
        packet=self.packet();raw=draft(packet)
        detail=copy.deepcopy(raw['sections'][1]);detail['key']='profit'
        raw['sections'].insert(1,detail)
        raw['sections'][-1]['claims'][0]['interpretation']+='建议买入。'
        body=validate_draft(raw,packet)
        self.assertEqual(body['excluded_findings'],1)
        self.assertNotIn('建议买入',json.dumps(body,ensure_ascii=False))
        self.assertIn('已排除',body['validation_note'])

    def test_reading_or_refreshing_never_calls_model_and_original_is_unchanged(self):
        path=core.TASK_DIR/f"{self.base['id']}.json";before=path.read_bytes()
        with patch.object(jobs,'generate',side_effect=AssertionError('GET must not call model')):
            self.assertEqual(self.client.get(f"/api/tasks/{self.base['id']}/research").status_code,200)
            self.assertEqual(self.client.get(f"/api/tasks/{self.base['id']}/research").status_code,200)
        self.assertEqual(path.read_bytes(),before)

    def test_generation_is_independent_versioned_and_deduplicated(self):
        path=core.TASK_DIR/f"{self.base['id']}.json";before=path.read_bytes()
        with patch.object(jobs,'generate',side_effect=lambda packet:validate_draft(draft(packet),packet)) as model:
            first=self.client.post(f"/api/tasks/{self.base['id']}/research",json={})
            self.assertEqual(first.status_code,202);self.finish()
            again=self.client.post(f"/api/tasks/{self.base['id']}/research",json={})
            self.assertEqual(first.json()['id'],again.json()['id']);self.assertEqual(model.call_count,1)
        value=self.client.get(f"/api/tasks/{self.base['id']}/research").json()
        self.assertEqual(value['report']['status'],'completed')
        self.assertFalse(value['new_data_available'])
        self.assertEqual(self.client.get(f"/api/tasks/{self.base['id']}/research/{first.json()['id']}").status_code,200)
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(self.client.get('/api/history').json()['total'],1)

    def test_model_failure_keeps_prior_version_and_financial_result(self):
        with patch.object(jobs,'generate',side_effect=ValueError('private material must not leak')):
            self.client.post(f"/api/tasks/{self.base['id']}/research",json={});self.finish()
        value=self.client.get(f"/api/tasks/{self.base['id']}/research").json()
        self.assertEqual(value['latest_job']['status'],'failed')
        self.assertNotIn('private material',json.dumps(value))
        self.assertEqual(self.client.get(f"/api/tasks/{self.base['id']}/result").status_code,200)

    def test_slow_model_does_not_block_history_or_financial_result_and_cancel_is_final(self):
        entered=threading.Event();release=threading.Event()
        def model(packet):
            entered.set();release.wait(4);return validate_draft(draft(packet),packet)
        with patch.object(jobs,'generate',side_effect=model):
            try:
                response=self.client.post(f"/api/tasks/{self.base['id']}/research",json={})
                self.assertTrue(entered.wait(2));identifier=response.json()['id']
                self.assertEqual(self.client.get('/api/history').status_code,200)
                self.assertEqual(self.client.get(f"/api/tasks/{self.base['id']}/result").status_code,200)
                stopped=self.client.post(f"/api/tasks/{self.base['id']}/research/{identifier}/cancel")
                self.assertEqual(stopped.json()['status'],'interrupted')
            finally:release.set()
            self.finish()
        self.assertIsNone(self.client.get(f"/api/tasks/{self.base['id']}/research").json()['report'])

    def test_missing_key_and_wrong_report_version_are_rejected(self):
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':''}):
            self.assertEqual(self.client.post(f"/api/tasks/{self.base['id']}/research",json={}).status_code,409)
        other=self.task(data=analysis(code='999999'))
        with patch.object(jobs,'generate',side_effect=lambda packet:validate_draft(draft(packet),packet)):
            response=self.client.post(f"/api/tasks/{self.base['id']}/research",json={});self.finish()
        self.assertEqual(self.client.get(f"/api/tasks/{other['id']}/research/{response.json()['id']}").status_code,404)

    def test_restart_marks_only_research_job_interrupted(self):
        root=jobs.report_root(core,self.base)
        identifier=uuid.uuid4().hex
        jobs.save(jobs.directory(core,root)/f'{identifier}.json',{'id':identifier,'root_task_id':root,
            'status':'processing','session_id':'old-session','created_at':jobs.now()})
        record=jobs.records(core,root)[0]
        self.assertEqual(record['status'],'interrupted')
        self.assertEqual(self.base['status'],'completed')

    def test_financial_save_never_waits_for_old_synchronous_generator(self):
        task=core.create_task('upload','新报告.pdf')
        with patch.object(core,'generate_report',side_effect=AssertionError('legacy model must not block')):
            core.finish_task(task['id'],analysis())
        self.assertEqual(task['status'],'completed')
        self.assertEqual(task['result']['report_generator'],'deterministic_template')


if __name__=='__main__':unittest.main()
