"""Run offline upload regressions with workspace-scoped Windows temp folders.

Some desktop sandboxes cannot reopen tempfile's restrictive Windows folders.
Use normal inherited workspace permissions for test fixtures only.
"""
import shutil
import sys
import tempfile
import unittest
import uuid
import os
from pathlib import Path
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'backend'))
sys.path.insert(0, str(PROJECT / 'backend' / 'financial-report-analysis'))
sys.path.insert(0, str(PROJECT / 'backend' / 'financial-report-analysis' / 'tests'))


class WorkspaceTemp:
    def __init__(self, *args, **kwargs):
        root = PROJECT / 'backend' / '.history-tests'
        self.path = root / ('regression-' + uuid.uuid4().hex)
        self.path.mkdir(parents=True)
        self.name = str(self.path)

    def __enter__(self):
        return self.name

    def __exit__(self, *args):
        self.cleanup()

    def cleanup(self):
        assert self.path.resolve().is_relative_to((PROJECT / 'backend' / '.history-tests').resolve())
        if self.path.exists():
            shutil.rmtree(self.path)


with patch.object(tempfile, 'TemporaryDirectory', WorkspaceTemp),patch.dict(os.environ,{'FINANCE_RESEARCH_AUTOGENERATE':'0'}):
    suite = unittest.defaultTestLoader.loadTestsFromNames(sys.argv[1:] or [
        'test_upload_errors', 'test_task_cancel', 'test_report_generation', 'test_report_summary',
        'test_announcements', 'test_semiannual',
    ])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
