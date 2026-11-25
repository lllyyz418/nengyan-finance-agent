import shutil
import unittest
import uuid
from unittest.mock import patch
from fastapi.testclient import TestClient
import app as core


class TaskCancelTests(unittest.TestCase):
    def setUp(self):
        self.root = core.BASE_DIR / '.history-tests'
        self.path = self.root / uuid.uuid4().hex
        self.path.mkdir(parents=True)
        paths = {key: self.path / key for key in ('TASK_DIR', 'LOG_DIR', 'RESULT_DIR')}
        for path in paths.values(): path.mkdir()
        self.patches = patch.multiple(core, **paths, TASKS={}, _CANCELLED_TASKS=set())
        self.patches.start()
        self.client = TestClient(core.app)

    def tearDown(self):
        self.patches.stop()
        assert self.path.resolve().is_relative_to(self.root.resolve())
        shutil.rmtree(self.path)

    def test_peer_task_cancel_is_idempotent_and_cannot_be_revived(self):
        task = core.create_task('cninfo', request_data={'peer_codes':['300014', '002074']})
        core.update_task(task['id'], status='processing', progress=32)
        for _ in range(2):
            response = self.client.post(f"/api/tasks/{task['id']}/cancel")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['status'], 'interrupted')
        core.update_task(task['id'], status='completed', progress=100, result={'late': True})
        current = self.client.get(f"/api/tasks/{task['id']}").json()
        self.assertEqual(current['status'], 'interrupted')
        self.assertEqual(current['progress'], 32)
        self.assertIsNone(core.TASKS[task['id']]['result'])

    def test_cancel_during_summary_render_does_not_publish_result(self):
        task = core.create_task('cninfo')
        def model(_analysis):
            self.client.post(f"/api/tasks/{task['id']}/cancel")
            return '# late response'
        with patch.object(core, 'normalize_analysis', return_value={}), patch.object(core, 'deterministic_report', side_effect=model):
            with self.assertRaises(core.TaskCancelled):
                core.finish_task(task['id'], {})
        self.assertFalse((core.RESULT_DIR / f"{task['id']}.json").exists())
        self.assertEqual(core.TASKS[task['id']]['status'], 'interrupted')

    def test_finished_task_is_preserved_when_cancel_arrives_late(self):
        task = core.create_task('cninfo')
        core.update_task(task['id'], status='completed', result={'saved':True})
        response = self.client.post(f"/api/tasks/{task['id']}/cancel")
        self.assertEqual(response.json()['status'], 'completed')
        self.assertEqual(core.TASKS[task['id']]['result'], {'saved':True})

    def test_unknown_task_is_not_reported_as_stopped(self):
        self.assertEqual(self.client.post('/api/tasks/' + 'a'*32 + '/cancel').status_code, 404)


if __name__ == '__main__': unittest.main()
