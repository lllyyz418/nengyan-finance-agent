import copy
import json
import unittest
from unittest.mock import patch
import tempfile
from pathlib import Path
import app as core


class TaskDiagnosticsTests(unittest.TestCase):
    def test_get_exposes_saved_threshold_without_rewriting_stored_result(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            paths={name:root/name for name in ('TASK_DIR','RESULT_DIR','LOG_DIR')}
            for path in paths.values():path.mkdir()
            with patch.multiple(core,**paths,TASKS={}):
                task=core.create_task('upload','fixture.pdf',{'minimum_negative_cash_quarters':4})
                original={'analysis':{'documents':[],'metrics':[]},'payload':{},'report_markdown':'原报告'}
                core.update_task(task['id'],status='completed',result=copy.deepcopy(original))
                with patch.object(core,'result_with_identity',side_effect=lambda snapshot:copy.deepcopy(snapshot['result'])):
                    response=core.get_result(task['id'])
                self.assertEqual(response['analysis_parameters']['minimum_negative_cash_quarters'],4)
                self.assertEqual(task['result'],original)
                saved=json.loads((paths['TASK_DIR']/f"{task['id']}.json").read_text(encoding='utf-8'))
                self.assertEqual(saved['result'],original)


if __name__=='__main__':unittest.main()
