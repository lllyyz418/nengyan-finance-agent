import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import app as core
import test_history_api as history_tests
from task_logs import safe_text


class TaskLogTests(unittest.TestCase):
    setUp = history_tests.HistoryApiTests.setUp
    tearDown = history_tests.HistoryApiTests.tearDown

    def test_real_transitions_incremental_and_no_duplicate_poll_events(self):
        task = core.create_task('upload', '测试.pdf', {'ocr_mode': 'off'})
        core.update_task(task['id'], status='processing', stage='财务事实提取', progress=35)
        core.update_task(task['id'], stage='财务事实提取', progress=35)
        for _ in range(3):
            self.client.get(f'/api/tasks/{task["id"]}')
        response = self.client.get(f'/api/tasks/{task["id"]}/logs', params={'limit':1}).json()
        self.assertEqual(len(response['events']), 1)
        self.assertTrue(response['has_more'])
        next_page = self.client.get(f'/api/tasks/{task["id"]}/logs', params={'after_seq':response['next_seq']}).json()
        self.assertEqual([e['seq'] for e in next_page['events']], [2])
        self.assertFalse(next_page['has_more'])
        self.assertEqual(next_page['task']['status'], 'processing')
        self.assertNotIn('request', next_page['task'])
        self.assertNotIn('result', next_page['task'])

    def test_filter_and_utf8_text_download(self):
        task = core.create_task('upload', '测试.pdf')
        core.log_event(task['id'], 'warning', '数据质量', '字段缺失', '请查看原文')
        core.update_task(task['id'], status='failed', stage='分析失败', error='解析超时')
        response = self.client.get(f'/api/tasks/{task["id"]}/logs', params={'level':'warning','q':'字段'}).json()
        self.assertEqual(len(response['events']), 1)
        self.assertEqual(response['events'][0]['message'], '字段缺失')
        download = self.client.get(f'/api/tasks/{task["id"]}/logs/download', params={'level':'error'})
        self.assertEqual(download.status_code, 200)
        self.assertIn('北京时间', download.text)
        self.assertIn('解析超时', download.text)
        self.assertNotIn('字段缺失', download.text)
        self.assertIn('attachment', download.headers['content-disposition'])

    def test_legacy_not_fabricated_and_invalid_id_rejected(self):
        task = core.create_task('upload', '旧文件.pdf')
        (core.LOG_DIR / f'{task["id"]}.jsonl').unlink()
        task.pop('runtime_log_version')
        task.update(status='completed', stage='旧结果已保存')
        core.persist_task(task)
        response = self.client.get(f'/api/tasks/{task["id"]}/logs').json()
        self.assertFalse(response['has_logs'])
        self.assertEqual(response['events'], [])
        self.assertEqual(response['task']['stage'], '旧结果已保存')
        self.assertEqual(self.client.get('/api/tasks/not-a-task/logs').status_code, 404)
        self.assertEqual(self.client.get(f'/api/tasks/{task["id"]}/logs', params={'limit':501}).status_code, 400)

    def test_restart_records_detection_not_fake_history(self):
        task = core.create_task('cninfo', '测试-2025')
        core.update_task(task['id'], status='processing', stage='解析中', session_id='old-session')
        core.TASKS.clear()
        response = self.client.get(f'/api/tasks/{task["id"]}/logs').json()
        self.assertEqual(response['task']['status'], 'interrupted')
        self.assertEqual(response['events'][-1]['level'], 'warning')
        count = len(response['events'])
        self.assertEqual(len(self.client.get(f'/api/tasks/{task["id"]}/logs').json()['events']), count)

    def test_secret_redaction_and_concurrent_sequence(self):
        task = core.create_task('upload', '测试.pdf')
        core.log_event(task['id'], 'error', '测试', 'sk-demoSecret Bearer abc123 api_key=exampleSecret', r'C:\Users\private\report.pdf')
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(lambda number: core.log_event(task['id'], 'info', '并发', f'测试事件 {number}'), range(24)))
        response = self.client.get(f'/api/tasks/{task["id"]}/logs').json()
        self.assertEqual([e['seq'] for e in response['events']], list(range(1,27)))
        stored = (core.LOG_DIR / f'{task["id"]}.jsonl').read_text(encoding='utf-8')
        self.assertNotIn('demoSecret', stored)
        self.assertNotIn('abc123', stored)
        self.assertNotIn('exampleSecret', stored)
        self.assertNotIn('private', stored)
        self.assertIn('已隐藏', safe_text('password=hunter2'))

    def test_completion_and_quality_warning_retained_after_reload(self):
        task = core.create_task('upload', '测试.pdf')
        payload = {'facts':[], 'metrics':[], 'warnings':[{'message':'输入不足'}], 'unavailable_metrics':[{}]}
        with patch.object(core, 'generate_report', return_value=('# 模板报告', 'deterministic_template')):
            core.finish_task(task['id'], payload)
        core.TASKS.clear()
        response = self.client.get(f'/api/tasks/{task["id"]}/logs').json()
        self.assertEqual(response['task']['status'], 'completed')
        self.assertTrue(response['task']['finished_at'])
        self.assertTrue(any(e['stage'] == '数据质量核对' and e['level'] == 'warning' for e in response['events']))
        self.assertEqual(response['events'][-1]['level'], 'success')
        self.assertEqual(self.client.get(f'/api/tasks/{task["id"]}/logs',params={'after_seq':response['next_seq']}).json()['events'], [])

    def test_task_isolation_and_recent_sort_ignores_favorite(self):
        first = core.create_task('upload', '旧任务.pdf')
        first['favorite'] = True
        core.persist_task(first)
        second = core.create_task('upload', '新任务.pdf')
        core.log_event(second['id'], 'success', '文件', '新任务文件已保存')
        events = self.client.get(f'/api/tasks/{first["id"]}/logs').json()['events']
        self.assertFalse(any('新任务' in e['message'] for e in events))
        recent = self.client.get('/api/history',params={'sort':'recent','page_size':1}).json()['items']
        self.assertEqual(recent[0]['id'], second['id'])


if __name__ == '__main__':
    unittest.main()
