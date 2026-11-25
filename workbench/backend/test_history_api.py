import shutil
import uuid
import unittest
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import app as core
import history_index


class HistoryApiTests(unittest.TestCase):
    def setUp(self):
        self.test_root = core.BASE_DIR / '.history-tests'
        self.test_root.mkdir(exist_ok=True)
        self.directory = self.test_root / uuid.uuid4().hex
        self.directory.mkdir()
        root = self.directory
        self.paths = {key: root / key for key in ('TASK_DIR', 'RESULT_DIR', 'UPLOAD_DIR', 'CACHE_DIR', 'LOG_DIR')}
        for path in self.paths.values():
            path.mkdir()
        self.patches = patch.multiple(core, **self.paths, TASKS={})
        self.patches.start()
        self.client = TestClient(core.app)

    def tearDown(self):
        self.patches.stop()
        assert self.directory.resolve().is_relative_to(self.test_root.resolve())
        shutil.rmtree(self.directory)

    def task(self, source='upload', result=None, request=None, year=2025):
        task = core.create_task(source, '宁德时代.pdf', request)
        path = core.UPLOAD_DIR / f'{task["id"]}.pdf'
        path.write_bytes(b'%PDF-1.4\n% source retained for regression test')
        if result is None:
            result = {'engine_version':'0.17','payload':{}, 'analysis': {
                'documents':[{'company_name':'宁德时代','security_code':'300750','report_year':year}],
                'metrics':[{'metric_code':'revenue_yoy','value':'12','unit':'percent'}],
                'risk_assessments':[{'status':'triggered'}], 'warnings':[]},
                'report_markdown':'# 原分析报告\n- 保存值：12%', 'evidence':{}}
        core.update_task(task['id'], status='completed', file_path=str(path), result=result)
        return task

    def test_list_search_filter_pagination_and_saved_result(self):
        first = self.task(year=2024)
        self.task(year=2025)
        response = self.client.get('/api/history', params={'q':'300750','year':2024,'page_size':1}).json()
        self.assertEqual(response['total'], 1)
        self.assertEqual(response['items'][0]['id'], first['id'])
        self.assertEqual(response['items'][0]['risks'], 1)
        self.assertNotIn('result', response['items'][0])
        self.assertEqual(self.client.get(f'/api/tasks/{first["id"]}/result').json()['analysis']['metrics'][0]['value'], '12')
        self.assertEqual(self.client.get('/api/history',params={'page_size':0}).status_code, 400)

    def test_favorite_note_trash_restore_and_reload(self):
        task = self.task()
        url = f'/api/history/{task["id"]}'
        self.assertEqual(self.client.patch(url,json={'favorite':True,'note':'待核对存货'}).status_code, 200)
        self.assertEqual(self.client.get('/api/history',params={'q':'存货','favorites':True}).json()['total'],1)
        self.client.patch(url,json={'trashed':True})
        self.assertEqual(self.client.get('/api/history').json()['total'],0)
        core.TASKS.clear()  # Same disk archive, fresh process memory.
        stored = self.client.get('/api/history',params={'trashed':True}).json()['items'][0]
        self.assertTrue(stored['favorite'])
        self.assertEqual(stored['note'],'待核对存货')
        self.assertTrue(core.evidence_file_path(core.task_by_id(task['id'])).is_file())
        self.client.patch(url,json={'trashed':False})
        self.assertEqual(self.client.get('/api/history').json()['total'],1)

    def test_restart_marks_inflight_as_interrupted(self):
        task = core.create_task('cninfo','宁德时代-2025',{'query':'宁德时代','report_year':2025})
        core.update_task(task['id'],status='processing',session_id='previous-server')
        core.TASKS.clear()
        result = self.client.get('/api/history').json()
        self.assertEqual(result['items'][0]['status'],'interrupted')
        self.assertEqual(result['statistics']['failed'],1)
        self.assertEqual(self.client.get(f'/api/tasks/{task["id"]}').json()['status'],'interrupted')

    def test_retry_creates_new_version_and_preserves_original(self):
        task = self.task(request={'ocr_mode':'auto'})
        with patch.object(core,'run_upload_task') as worker:
            response = self.client.post(f'/api/history/{task["id"]}/retry')
        self.assertEqual(response.status_code,202)
        new = response.json()
        self.assertNotEqual(new['id'],task['id'])
        self.assertEqual(new['parent_task_id'],task['id'])
        self.assertEqual(worker.call_args.args[2],'auto')
        self.assertEqual(core.task_by_id(task['id'])['status'],'completed')
        legacy = self.task()
        self.assertEqual(self.client.post(f'/api/history/{legacy["id"]}/retry').status_code,409)

    def test_report_download_and_pending_protection(self):
        task = self.task()
        response = self.client.get(f'/api/history/{task["id"]}/report')
        self.assertEqual(response.status_code,200)
        self.assertIn('保存值：12%',response.text)
        self.assertTrue(response.headers['content-disposition'].startswith('attachment'))
        core.update_task(task['id'],status='processing')
        self.assertEqual(self.client.patch(f'/api/history/{task["id"]}',json={'trashed':True}).status_code,409)
        self.assertEqual(self.client.post(f'/api/history/{task["id"]}/retry').status_code,409)

    def test_dates_use_china_time_at_day_boundary(self):
        task = self.task()
        core.update_task(task['id'],created_at='2025-12-31T17:30:00+00:00')
        self.assertEqual(self.client.get('/api/history',params={'start_date':'2026-01-01','end_date':'2026-01-01'}).json()['total'],1)
        self.assertEqual(self.client.get('/api/history',params={'end_date':'2025-12-31'}).json()['total'],0)

    def test_slow_history_file_check_does_not_block_progress_cancel_or_new_analysis(self):
        self.task()
        running=core.create_task('cninfo','运行中',{'query':'300750','report_year':2025})
        entered,release=threading.Event(),threading.Event()
        def slow_check(task,*args,**kwargs):
            self.assertFalse(core.TASK_LOCK.locked(),'file I/O must be outside TASK_LOCK')
            self.assertFalse(kwargs['verify'],'listing must not hash source PDFs')
            entered.set();release.wait(4)
            return None
        with ThreadPoolExecutor(max_workers=3) as executor,patch.object(core,'evidence_file_path',side_effect=slow_check),patch.object(core,'run_online_task') as worker:
            history=executor.submit(self.client.get,'/api/history')
            try:
                self.assertTrue(entered.wait(2))
                progress=executor.submit(self.client.get,f"/api/tasks/{running['id']}").result(timeout=2)
                self.assertEqual(progress.status_code,200)
                stopped=executor.submit(self.client.post,f"/api/tasks/{running['id']}/cancel").result(timeout=2)
                self.assertEqual(stopped.json()['status'],'interrupted')
                created=executor.submit(self.client.post,'/api/tasks/online',json={'query':'300750','report_year':2025}).result(timeout=2)
                self.assertEqual(created.status_code,202);worker.assert_called_once()
            finally:release.set()
            self.assertEqual(history.result(timeout=2).status_code,200)

    def test_file_checks_are_page_scoped_and_logs_can_skip_them(self):
        for _ in range(5):self.task()
        with patch.object(core,'evidence_file_path',return_value=None) as check:
            response=self.client.get('/api/history',params={'page_size':2})
            self.assertEqual(response.json()['total'],5)
            self.assertEqual(check.call_count,2)
            check.reset_mock()
            self.client.get('/api/history',params={'include_source':False})
            check.assert_not_called()

    def test_cached_projections_do_not_reread_unchanged_full_task_json(self):
        task=self.task()
        core.TASKS.clear()
        with patch.object(history_index.json,'loads',wraps=json.loads) as decode:
            self.assertEqual(self.client.get('/api/history').json()['total'],1)
            first_count=decode.call_count
            self.assertGreater(first_count,0)
            self.client.get('/api/history')
            self.assertEqual(decode.call_count,first_count)
        task['note']='外部更新后应刷新摘要'
        core.persist_task(task)
        self.assertEqual(self.client.get('/api/history').json()['items'][0]['note'],task['note'])

    def test_history_projection_excludes_heavy_results_and_preserves_counts(self):
        task=self.task()
        result=task['result'];analysis=result['analysis']
        analysis['facts']=[{'text':'huge'*10000} for _ in range(20)]
        analysis['metrics']=[{'value':'1'} for _ in range(200)]
        analysis['warnings']=['原始警告']*3
        result['report_markdown']='long report '*10000
        small=history_index.compact_task(task)
        self.assertLess(len(json.dumps(small)),6000)
        self.assertNotIn('facts',small['result']['analysis'])
        self.assertNotIn('metrics',small['result']['analysis'])
        core.persist_task(task)
        row=self.client.get('/api/history').json()['items'][0]
        self.assertEqual(row['metrics'],200);self.assertEqual(row['warnings'],3)
        self.assertEqual(row['risks'],1);self.assertEqual(row['status'],'partial')

    def test_listing_does_not_rewrite_old_inflight_files(self):
        task=core.create_task('cninfo','previous process',{'query':'300750','report_year':2025})
        core.update_task(task['id'],status='processing',session_id='old-process')
        path=core.TASK_DIR/f"{task['id']}.json";before=path.read_bytes()
        core.TASKS.clear()
        row=self.client.get('/api/history').json()['items'][0]
        self.assertEqual(row['status'],'interrupted')
        self.assertEqual(path.read_bytes(),before)

    def test_result_enrichment_and_source_opening_do_not_hold_shared_lock(self):
        task=self.task();original=core.result_with_identity
        def enrich(value):
            self.assertFalse(core.TASK_LOCK.locked())
            return original(value)
        def source(value,*args,**kwargs):
            self.assertFalse(core.TASK_LOCK.locked())
            return Path(value['file_path'])
        with patch.object(core,'result_with_identity',side_effect=enrich):
            self.assertEqual(self.client.get(f"/api/tasks/{task['id']}/result").status_code,200)
        with patch.object(core,'evidence_file_path',side_effect=source):
            self.assertEqual(self.client.get(f"/api/tasks/{task['id']}/file").status_code,200)


if __name__ == '__main__':
    unittest.main()
