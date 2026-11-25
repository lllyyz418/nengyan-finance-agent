"""Regression for simultaneous history reads, PDF work and current-first saves."""
import shutil
import threading
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import app as core
import history_index
import history_api
import pdf_runtime
import test_history_api


class ResponsivenessTests(unittest.TestCase):
    setUp=test_history_api.HistoryApiTests.setUp
    tearDown=test_history_api.HistoryApiTests.tearDown
    task=test_history_api.HistoryApiTests.task

    def test_history_does_not_wait_for_task_write_lock(self):
        self.task()
        with core.TASK_LOCK,ThreadPoolExecutor(max_workers=1) as executor:
            response=executor.submit(self.client.get,'/api/history').result(timeout=3)
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['total'],1)

    def test_persistent_summary_survives_memory_cache_reset(self):
        task=self.task();history_index.CACHE.clear();core.TASKS.clear()
        original=Path.read_text
        def read(path,*args,**kwargs):
            if path.parent==core.TASK_DIR:
                raise AssertionError('must not read the full result on restart')
            return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',read):
            row=self.client.get('/api/history').json()['items'][0]
        self.assertEqual(row['id'],task['id'])

    def test_corrupt_summary_rebuilds_without_modifying_original(self):
        task=self.task();path=core.TASK_DIR/f"{task['id']}.json";before=path.read_bytes()
        (core.TASK_DIR/'.summaries'/path.name).write_text('broken')
        history_index.CACHE.clear()
        self.assertEqual(self.client.get('/api/history').json()['total'],1)
        self.assertEqual(path.read_bytes(),before)

    def test_name_compatibility_lookup_uses_summaries_not_full_results(self):
        import company_directory
        task=self.task()
        core.TASKS.clear();history_index.CACHE.clear()
        company_directory.NAME_CACHE.update(key=None,items=())
        original=Path.read_text
        def read(path,*args,**kwargs):
            if path.parent==core.TASK_DIR:raise AssertionError('name lookup reread full results')
            return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',read):
            names=company_directory.saved_names(core)
        self.assertIn(('300750','宁德时代'),names)

    def test_slow_history_has_server_deadline_not_infinite_spinner(self):
        self.task();entered=threading.Event();release=threading.Event()
        original=history_index.HistoryReader.ids
        def slow(reader):
            entered.set();release.wait(3);return original(reader)
        try:
            with patch.object(history_api,'HISTORY_TIMEOUT_SECONDS',.1),patch.object(history_index.HistoryReader,'ids',slow):
                response=self.client.get('/api/history')
                self.assertTrue(entered.is_set());self.assertEqual(response.status_code,503)
                self.assertIn('记录仍保留',response.json()['detail'])
                self.assertEqual(self.client.get('/api/health').status_code,200)
        finally:release.set()

    def test_current_is_readable_while_history_is_still_running(self):
        task=core.create_task('cninfo','300750-2025',{'query':'300750','report_year':2025,'lookback_years':2})
        payload={'current':{'documents':[{'document_id':'current','company_name':'测试公司','security_code':'300750','report_year':2025,'report_type':'annual'}],
                            'facts':[],'metrics':[],'warnings':[],'unavailable_metrics':[]},
                 'history':{'status':'not_requested'},'retrieval':{'files':[]}}
        started=threading.Event();release=threading.Event()
        def backfill(_identifier):started.set();release.wait(3)
        with patch.object(core,'resolve_security',return_value={'stock_code':'300750'}),\
             patch.object(core,'analyze_disclosure_context',return_value=payload) as engine,\
             patch.object(core,'generate_report',return_value=('# 本期报告','deterministic_template')),\
             patch.object(core,'create_backfill_task',return_value={'id':'b'*32}),\
             patch.object(core,'run_backfill_task',side_effect=backfill),ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(core.run_online_task,task['id'],core.OnlineAnalysisRequest(query='300750',report_year=2025,lookback_years=2))
            try:
                self.assertTrue(started.wait(2))
                self.assertEqual(self.client.get(f"/api/tasks/{task['id']}/result").status_code,200)
                self.assertEqual(self.client.get('/api/history').json()['total'],1)
                self.assertFalse(future.done())
            finally:release.set()
            future.result(timeout=2)
        self.assertEqual(engine.call_args.kwargs['lookback_years'],0)
        self.assertEqual(task['request']['lookback_years'],2)

    def test_legacy_and_restarted_results_do_not_wait_for_nonexistent_backfill(self):
        import backfill_api
        task=self.task(request={'lookback_years':2})
        task['result']['analysis']['documents'][0]['report_type']='annual'
        core.persist_task(task)
        self.assertFalse(backfill_api.job_summary(core,task)['pending'])
        core.update_task(task['id'],auto_backfill_pending=True)
        self.assertTrue(backfill_api.job_summary(core,task)['pending'])
        core.update_task(task['id'],session_id='previous-server')
        self.assertFalse(backfill_api.job_summary(core,task)['pending'])


class PdfRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.root=core.BASE_DIR/'.history-tests'
        self.directory=self.root/('runtime-'+uuid.uuid4().hex);self.directory.mkdir(parents=True)
        self.path=self.directory/'input.pdf';self.path.write_text('success')
        self.worker=patch.object(pdf_runtime,'WORKER',core.BASE_DIR.parent/'scripts/fixtures/pdf-runtime-worker.py')
        self.worker.start();pdf_runtime.parser_version.cache_clear()

    def tearDown(self):
        self.worker.stop();pdf_runtime.parser_version.cache_clear()
        assert self.directory.resolve().is_relative_to(self.root.resolve())
        shutil.rmtree(self.directory)

    def extract(self,**kwargs):
        return pdf_runtime.extract('annual',self.path,self.directory,**kwargs)

    def test_cache_reuse_hash_version_and_corruption(self):
        self.assertEqual(self.extract(),{'fixture':'success'})
        with patch.object(pdf_runtime.subprocess,'Popen',side_effect=AssertionError('cache must avoid worker')):
            self.assertEqual(self.extract(),{'fixture':'success'})
        self.path.write_text('changed');self.assertEqual(self.extract(),{'fixture':'changed'})
        with patch.object(pdf_runtime,'parser_version',return_value='next-version'):
            self.assertEqual(self.extract(),{'fixture':'changed'})
        for file in (self.directory/'pdf-runtime').glob('*.json'):file.write_text('broken')
        self.assertEqual(self.extract(),{'fixture':'changed'})

    def test_timeout_and_worker_crash_release_slots(self):
        self.path.write_text('timeout')
        with self.assertRaisesRegex(TimeoutError,'解析超时'):self.extract(max_seconds=.2)
        self.path.write_text('crash')
        with self.assertRaisesRegex(RuntimeError,'异常退出'):self.extract()
        self.path.write_text('error')
        with self.assertRaisesRegex(ValueError,'fixture failure'):self.extract()
        self.path.write_text('success');self.assertEqual(self.extract(),{'fixture':'success'})

    def test_cancel_terminates_child_without_waiting_for_pdf(self):
        self.path.write_text('cancel');started=time.monotonic()
        def checkpoint():
            if time.monotonic()-started>.4:raise core.TaskCancelled()
        with self.assertRaises(core.TaskCancelled):self.extract(checkpoint=checkpoint)
        self.assertLess(time.monotonic()-started,3)
        self.assertFalse(list((self.directory/'pdf-runtime').glob('*.json')))

    def test_busy_pdf_does_not_block_health_or_history(self):
        self.path.write_text('busy')
        with patch.multiple(core,TASK_DIR=self.directory,TASKS={}),ThreadPoolExecutor(max_workers=1) as executor:
            future=executor.submit(self.extract)
            client=TestClient(core.app)
            for _ in range(3):
                started=time.monotonic()
                self.assertEqual(client.get('/api/health').status_code,200)
                self.assertEqual(client.get('/api/history').status_code,200)
                self.assertLess(time.monotonic()-started,1.5)
            self.assertEqual(future.result(timeout=5),{'fixture':'busy'})

    def test_simultaneous_same_pdf_uses_one_worker(self):
        self.path.write_text('busy')
        with patch.object(pdf_runtime.subprocess,'Popen',wraps=pdf_runtime.subprocess.Popen) as launch,ThreadPoolExecutor(max_workers=2) as executor:
            jobs=[executor.submit(self.extract) for _ in range(2)]
            self.assertEqual([job.result(timeout=6) for job in jobs],[{'fixture':'busy'}]*2)
            self.assertEqual(launch.call_count,1)

    def test_page_text_cache_is_scoped_and_does_not_change_text(self):
        from financial_engine.parse_runtime import cached_page_text,page_text_cache
        class Page:
            calls=0
            def extract_text(self):self.calls+=1;return 'unchanged 0 -100'
        page=Page()
        with page_text_cache():
            self.assertEqual(cached_page_text(page),cached_page_text(page))
            self.assertEqual(page.calls,1)
        with page_text_cache():self.assertEqual(cached_page_text(page),'unchanged 0 -100')
        self.assertEqual(page.calls,2)


if __name__=='__main__':unittest.main()
