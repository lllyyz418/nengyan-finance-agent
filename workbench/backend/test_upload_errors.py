import json
import shutil
import uuid
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import app as core
from financial_engine import pipeline
from financial_engine.document_reader import DocumentReadError
from upload_errors import UploadAnalysisError, classify_upload_exception, validate_upload_result


def unrelated_pdf() -> bytes:
    """Small real text PDF, intentionally unrelated; no network or PDF writer needed."""
    content = b'BT /F1 14 Tf 50 740 Td (Participation Commitment) Tj 0 -30 Td (This is a contest commitment, not a financial report.) Tj ET'
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream']
    body, offsets = b'%PDF-1.4\n', [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body += f'{index} 0 obj\n'.encode() + obj + b'\nendobj\n'
    start = len(body)
    body += b'xref\n0 6\n0000000000 65535 f \n'
    body += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return body + f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()


class UploadErrorTests(unittest.TestCase):
    def setUp(self):
        self.test_root = core.BASE_DIR / '.history-tests'
        self.root = self.test_root / ('upload-errors-' + uuid.uuid4().hex)
        self.root.mkdir(parents=True)
        paths = {key: self.root / key for key in ('TASK_DIR', 'UPLOAD_DIR', 'LOG_DIR', 'RESULT_DIR')}
        for path in paths.values():
            path.mkdir()
        self.settings = patch.multiple(core, **paths, TASKS={}, _CANCELLED_TASKS=set())
        self.settings.start()
        self.client = TestClient(core.app)
        self.model = patch.object(core, 'generate_report', return_value=('# test only', 'deterministic_template'))
        self.generator = self.model.start()

    def tearDown(self):
        self.model.stop()
        self.settings.stop()
        assert self.root.resolve().is_relative_to(self.test_root.resolve())
        shutil.rmtree(self.root)

    def upload(self, body=None, name='承诺书.pdf'):
        response = self.client.post('/api/tasks/upload?ocr_mode=off',
                                    content=body if body is not None else unrelated_pdf(),
                                    headers={'X-Filename': __import__('urllib.parse', fromlist=['quote']).quote(name)})
        self.assertEqual(response.status_code, 202)
        return self.client.get('/api/tasks/' + response.json()['id']).json()

    def test_unrelated_real_pdf_has_actionable_reason_and_never_calls_model(self):
        task = self.upload()
        self.assertEqual(task['status'], 'failed')
        self.assertEqual(task['error_info']['code'], 'DOCUMENT_TYPE_UNRECOGNIZED')
        self.assertIn('未识别', task['error_info']['title'])
        self.assertIn('重新选择', task['error_info']['suggestion'])
        self.assertLess(task['progress'], 100)
        self.assertNotIn("'documents'", json.dumps(task, ensure_ascii=False))
        self.assertEqual((core.UPLOAD_DIR / f"{task['id']}.pdf").read_bytes(), unrelated_pdf())
        self.assertFalse((core.RESULT_DIR / f"{task['id']}.json").exists())
        self.generator.assert_not_called()

    def test_unknown_system_error_is_not_claimed_to_be_an_invalid_report(self):
        with patch.object(core, 'analyze_uploaded_document', side_effect=KeyError('D:/private/token-secret')):
            task = self.upload()
        self.assertEqual(task['error_info']['code'], 'SYSTEM_ERROR')
        self.assertTrue(task['error_info']['retryable'])
        self.assertNotIn('token-secret', json.dumps(task))
        logs = ''.join(path.read_text(encoding='utf-8') for path in core.LOG_DIR.glob('*'))
        self.assertNotIn('token-secret', logs)
        self.generator.assert_not_called()

    def test_scan_and_unidentified_large_pdf_do_not_become_key_errors(self):
        inspection = {'document_id':'id', 'file_sha256':'a'*64, 'file_name':'annual.pdf',
                      'page_count':80, 'pages':[{'page_type':'scan_candidate', 'lines':[]}], 'warnings':[]}
        with patch('financial_engine.pipeline.read_pdf_document', return_value=inspection):
            result = pipeline.analyze_uploaded_document('unused.pdf', ocr_mode='off')
        self.assertEqual(result['warnings'][-1]['code'], 'PDF_TEXT_UNREADABLE')
        with self.assertRaises(UploadAnalysisError) as caught:
            validate_upload_result(result)
        self.assertEqual(caught.exception.code, 'PDF_TEXT_UNREADABLE')
        inspection['pages']=[{'page_type':'text','lines':[{'text':'Other document'}]}]
        with patch('financial_engine.pipeline.read_pdf_document', return_value=inspection):
            result = pipeline.analyze_uploaded_document('unused.pdf')
        self.assertEqual(result['warnings'][-1]['code'], 'DOCUMENT_TYPE_UNRECOGNIZED')

    def test_corrupt_pdf_has_safe_reason(self):
        task = self.upload(b'%PDF-1.4\nthis is not a valid PDF')
        self.assertEqual(task['error_info']['code'], 'PDF_DAMAGED')
        self.assertNotIn('Traceback', json.dumps(task))

    def test_http_preflight_errors_are_structured(self):
        for name, body, code in [('test.txt', b'text', 'FILE_TYPE_UNSUPPORTED'),
                                  ('test.pdf', b'not a PDF', 'INVALID_PDF')]:
            response = self.client.post('/api/tasks/upload', content=body, headers={'X-Filename':name})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['detail']['code'], code)
        with patch.object(core, 'MAX_FILE_SIZE', 5):
            response = self.client.post('/api/tasks/upload', content=unrelated_pdf())
            self.assertEqual(response.status_code, 413)
            self.assertEqual(response.json()['detail']['code'], 'FILE_TOO_LARGE')

    def test_known_parser_errors_are_distinct(self):
        password_error = type('PDFPasswordIncorrect', (Exception,), {})()
        wrapped = DocumentReadError('PDF_READ_FAILED')
        wrapped.__cause__ = password_error
        self.assertEqual(classify_upload_exception(wrapped, '文件读取')['code'], 'PDF_ENCRYPTED')
        self.assertEqual(classify_upload_exception(TimeoutError(), '文件读取')['code'], 'ANALYSIS_TIMEOUT')
        self.assertEqual(classify_upload_exception(PermissionError(), '文件保存')['code'], 'FILE_UNAVAILABLE')
        self.assertEqual(classify_upload_exception(DocumentReadError('Input has no PDF signature'), '文件读取')['code'], 'INVALID_PDF')

    def test_partial_reports_keep_calculations_and_missing_data(self):
        analysis = {'documents':[{'document_id':'doc', 'report_type':'annual'}], 'facts':[{'fact_id':'f'}],
                    'metrics':[], 'warnings':[{'code':'missing_note'}], 'unavailable_metrics':[{'metric_code':'revenue_yoy'}]}
        payload = {'document_type':'annual_report', 'status':'analyzed_with_warnings', 'analysis':analysis}
        with patch.object(core, 'analyze_uploaded_document', return_value=payload), \
             patch.object(core, 'result_with_identity', return_value={}):
            task = self.upload()
        self.assertEqual(task['status'], 'completed')
        self.assertEqual(core.TASKS[task['id']]['result']['analysis']['unavailable_metrics'], analysis['unavailable_metrics'])
        self.generator.assert_not_called()  # Financial persistence no longer waits for legacy model prose.

    def test_empty_financial_result_is_not_success_and_does_not_backfill(self):
        payload = {'document_type':'annual_report', 'status':'failed', 'analysis':{'documents':[], 'facts':[]}}
        with patch.object(core, 'analyze_uploaded_document', return_value=payload), patch.object(core, 'create_backfill_task') as backfill:
            task = self.upload()
        self.assertEqual(task['error_info']['code'], 'NO_FINANCIAL_DATA')
        backfill.assert_not_called()
        self.generator.assert_not_called()

    def test_supported_announcement_is_not_misread_as_financial_report(self):
        validate_upload_result({'document_type':'announcement', 'analysis':{'event':{'event_type':'bid_award'}}})
        with self.assertRaises(UploadAnalysisError) as caught:
            validate_upload_result({'document_type':'announcement', 'analysis':{'event':{'event_type':None}}})
        self.assertEqual(caught.exception.code, 'ANNOUNCEMENT_UNSUPPORTED')

    def test_cancellation_cannot_turn_into_failure(self):
        task = core.create_task('upload')
        self.client.post(f"/api/tasks/{task['id']}/cancel")
        core.record_upload_failure(task['id'], KeyError('documents'))
        self.assertEqual(core.TASKS[task['id']]['status'], 'interrupted')


if __name__ == '__main__':
    unittest.main()
