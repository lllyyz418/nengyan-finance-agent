import io
import json
import shutil
import uuid
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
import app as core
import company_directory as directory


class CompanyDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.test_root=core.BASE_DIR/'.history-tests'
        self.path=self.test_root/uuid.uuid4().hex;self.path.mkdir(parents=True)
        paths={key:self.path/key for key in ('TASK_DIR','RESULT_DIR','UPLOAD_DIR','CACHE_DIR','LOG_DIR','COMPANY_DIR')}
        for path in paths.values():path.mkdir()
        self.patch=patch.multiple(core,**paths,TASKS={});self.patch.start()
        self.cache=patch.multiple(directory,DATA_CACHE={},MASTER_CACHE={'time':0,'items':[]});self.cache.start()
        self.client=TestClient(core.app)
    def tearDown(self):
        self.cache.stop();self.patch.stop()
        assert self.path.resolve().is_relative_to(self.test_root.resolve())
        shutil.rmtree(self.path)
    def test_catalog_has_55_verified_unique_companies_and_preserves_old_choices(self):
        response=self.client.get('/api/companies',params={'limit':50}).json()
        self.assertEqual(response['catalog_size'],55);self.assertEqual(response['max_peers'],5)
        self.assertEqual(len({item['code'] for item in core.COMPANIES}),55)
        self.assertTrue({'300750','300014','002074','002594','601868','601012','300274','600438','002466','002460'}<={item['code'] for item in core.COMPANIES})
        self.assertTrue(all(item['identity_status']=='catalog_verified' for item in response['items']))
        self.assertTrue(response['has_more'])
    def test_prefix_partial_code_pinyin_alias_exclusions_and_paging(self):
        for query,code in [('宁德','300750'),('nd','300750'),('3007','300750'),('中伟股份','300919')]:
            items=self.client.get('/api/companies',params={'q':query}).json()['items']
            self.assertIn(code,[item['code'] for item in items])
        filtered=self.client.get('/api/companies',params={'segment':'动力及储能电池','exclude':'300750,300014'}).json()
        self.assertEqual(filtered['total'],3)
        self.assertFalse(any(item['code'] in {'300750','300014'} for item in filtered['items']))
        first=self.client.get('/api/companies',params={'limit':5}).json()
        second=self.client.get('/api/companies',params={'limit':5,'offset':5}).json()
        self.assertFalse({item['code'] for item in first['items']}&{item['code'] for item in second['items']})
    def test_identity_verification_never_implies_data_availability(self):
        item=self.client.get('/api/companies',params={'q':'300207','report_year':2025}).json()['items'][0]
        self.assertEqual(item['identity_status'],'catalog_verified')
        self.assertEqual(item['data_status']['state'],'unknown')
        task=core.create_task('cninfo','test',{'query':'300014','report_year':2025})
        analysis={'documents':[{'document_id':'test','security_code':'300014','report_year':2025,'report_type':'annual'}],
            'metrics':[{'document_id':'test','period':'2025','status':'calculated'}]}
        core.update_task(task['id'],status='completed',result={'analysis':analysis})
        directory.DATA_CACHE.clear()
        saved=self.client.get('/api/companies',params={'q':'300014','report_year':2025}).json()['items'][0]
        self.assertEqual(saved['data_status'],{'state':'analysis_saved','metrics':1,'year':2025})
        earlier=self.client.get('/api/companies',params={'q':'300014','report_year':2024}).json()['items'][0]
        self.assertEqual(earlier['data_status']['state'],'unknown')
    def test_unknown_code_requires_online_verification_and_persists_identity_only(self):
        master={'stockList':[{'code':'601611','category':'A股','zwjc':'中国核建','pinyin':'zghj'}]}
        with patch.object(directory,'urlopen',return_value=io.BytesIO(json.dumps(master).encode())):
            response=self.client.get('/api/companies/lookup',params={'code':'601611','report_year':2025})
        self.assertEqual(response.status_code,200)
        item=response.json()['item'];self.assertEqual(item['name'],'中国核建')
        self.assertEqual(item['identity_status'],'online_verified');self.assertEqual(item['data_status']['state'],'unknown')
        self.assertTrue((core.COMPANY_DIR/'verified.json').is_file())
        self.assertEqual(self.client.get('/api/companies',params={'q':'601611'}).json()['items'][0]['code'],'601611')
        self.assertFalse(list(core.TASK_DIR.glob('*.json')),'Lookup must not download/analyze any report')
        self.assertEqual(self.client.get('/api/companies/lookup',params={'code':'123'}).status_code,422)
    def test_no_match_or_network_failure_does_not_make_a_fake_company(self):
        with patch.object(directory,'urlopen',return_value=io.BytesIO(b'{"stockList":[]}')):
            self.assertEqual(self.client.get('/api/companies/lookup',params={'code':'999999'}).status_code,404)
        directory.MASTER_CACHE['time']=0
        with patch.object(directory,'urlopen',side_effect=OSError('test offline')),patch.object(core,'_online_resolve_security',side_effect=RuntimeError('test offline')):
            self.assertEqual(self.client.get('/api/companies/lookup',params={'code':'601611'}).status_code,503)
        self.assertFalse((core.COMPANY_DIR/'verified.json').exists())
    def test_registered_names_and_parentheses_match_without_network(self):
        with patch.object(directory,'urlopen',side_effect=AssertionError('local lookup contacted network')):
            for query,code in [('中国三峡新能源（集团）股份有限公司','600905'),('中国三峡新能源(集团)','600905'),('宁德时代新能源科技股份有限公司','300750')]:
                response=self.client.get('/api/companies',params={'q':query}).json()
                self.assertIn(code,[item['code'] for item in response['items']])
            self.assertEqual(core.resolve_security('中国三峡新能源（集团）股份有限公司')['stock_code'],'600905')

    def test_completed_verified_report_adds_full_name_but_conflict_does_not(self):
        for name,code,identity in [('测试注册公司股份有限公司','300014',{'stock_code':'300014','status':'pdf_code_matches_manifest'}),
                                   ('不应被采用的冲突名称股份有限公司','300014',{'stock_code':'002074','status':'conflict'})]:
            task=core.create_task('upload','test',{})
            core.update_task(task['id'],status='completed',result={'analysis':{'documents':[{'security_code':code,'company_name':name}]},'payload':{'identity':identity}})
        self.assertEqual(self.client.get('/api/companies',params={'q':'测试注册公司股份有限公司'}).json()['items'][0]['code'],'300014')
        self.assertEqual(self.client.get('/api/companies',params={'q':'不应被采用的冲突名称股份有限公司'}).json()['total'],0)

    def test_online_search_has_pagination_pinyin_full_names_and_never_starts_analysis(self):
        stocks=[{'code':f'600{i:03}','category':'A股','zwjc':f'测试公司{i:02}','pinyin':f'csgs{i:02}'} for i in range(25)]
        stocks.extend([{'code':'600905','category':'A股','zwjc':'三峡能源','pinyin':'sxny'},
                       {'code':'600999','category':'A股','zwjc':'冲突一'}, {'code':'600999','category':'A股','zwjc':'冲突二'},
                       {'code':'00700','category':'港股','zwjc':'测试港股'}, {'code':'bad','category':'A股','zwjc':'测试无效'}])
        with patch.object(directory,'urlopen',return_value=io.BytesIO(json.dumps({'stockList':stocks}).encode())) as remote:
            first=self.client.get('/api/companies/online',params={'q':'测试','limit':20}).json()
            second=self.client.get('/api/companies/online',params={'q':'测试','limit':20,'offset':20}).json()
            self.assertEqual(first['total'],25);self.assertTrue(first['has_more']);self.assertEqual(len(second['items']),5)
            self.assertFalse({item['code'] for item in first['items']}&{item['code'] for item in second['items']})
            self.assertEqual(self.client.get('/api/companies/online',params={'q':'csgs00'}).json()['items'][0]['code'],'600000')
            self.assertEqual(self.client.get('/api/companies/online',params={'q':'中国三峡新能源（集团）股份有限公司'}).json()['items'][0]['code'],'600905')
            self.assertEqual(self.client.get('/api/companies/online',params={'q':'冲突'}).json()['items'],[])
            self.assertEqual(remote.call_count,1,'official directory should be cached')
        self.assertFalse(list(core.TASK_DIR.glob('*.json')))
        self.assertFalse((core.COMPANY_DIR/'verified.json').exists(),'search must not persist the whole market as local choices')

    def test_online_empty_is_different_from_network_failure(self):
        with patch.object(directory,'urlopen',return_value=io.BytesIO(b'{"stockList":[]}')):
            response=self.client.get('/api/companies/online',params={'q':'未收录'})
            self.assertEqual(response.status_code,200);self.assertEqual(response.json()['total'],0)
        directory.MASTER_CACHE['time']=0
        with patch.object(directory,'urlopen',side_effect=OSError('offline')):
            response=self.client.get('/api/companies/online',params={'q':'测试'})
            self.assertEqual(response.status_code,503);self.assertIn('联网公司查询失败',response.json()['detail'])
        self.assertEqual(self.client.get('/api/companies/online',params={'q':' '}).status_code,422)
        self.assertEqual(self.client.get('/api/companies',params={'q':'三峡'}).json()['items'][0]['code'],'600905')
    def test_peer_limit_duplicates_and_minimum_are_enforced_before_any_job(self):
        request={'query':'300750','report_year':2025,'peer_group':'电池','selection_note':'按主要产品进行人工比较','peer_codes':['300014','002074','300207','300438','002594']}
        with patch.object(core,'run_online_task') as worker:
            self.assertEqual(self.client.post('/api/tasks/online',json=request).status_code,202)
            worker.assert_called_once()
            for codes in [request['peer_codes']+['300037'],['300014','300014'],['300014'],['bad','300014']]:
                self.assertEqual(self.client.post('/api/tasks/online',json={**request,'peer_codes':codes}).status_code,422)
            self.assertEqual(worker.call_count,1)
        self.assertEqual(self.client.post('/api/tasks/online',json={**request,'peer_codes':['300750','300014']}).status_code,400)


if __name__=='__main__':unittest.main()
