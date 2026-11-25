import concurrent.futures
import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from financial_engine.scan_review import ScanReviewError, create_scan_review_template
from scan_review_extension.api import register_scan_review_routes
from scan_review_extension.store import ReviewConflict, ReviewStore

ROOT = Path(__file__).resolve().parents[1]


def fixture(path):
    path.write_bytes(b"%PDF-1.7\nunit test only, not a renderable PDF")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    def source(text, x):
        return {"document_id": sha[:16], "file_sha256": sha, "page": 1, "bbox": [x,100,x+60,115],
                "method": "ocr", "confidence": .99, "text": text}
    return {"schema_version": "0.16", "document": {"document_id": sha[:16], "file_sha256": sha,
            "file_name": path.name, "page_count": 1}, "identity_claim": {"company_name": "测试股份有限公司",
            "report_year": 2025, "verified_from_pdf": True}, "pages_read": [1], "warnings": [],
            "candidates": [{"candidate_id": "row-1", "metric_code": "revenue", "period": "2025", "previous_period": "2024",
            "unit": "CNY", "raw_unit": "万元", "value": "2000000.01", "previous_value": "1000000",
            "statement_scope": "summary_disclosure", "status": "needs_review", "checks": {},
            "sources": {"label": source("营业收入",20), "current": source("200.000001",200), "previous": source("100",300)}}]}


class FixtureBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "verification")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = self.root / "source.pdf"
        self.scan = fixture(self.path); self.store = ReviewStore(self.root / "review.sqlite3")
        self.store.retain("task1", self.scan, self.path)
        self.actor = {"id": "reviewer", "identity_basis": "local_self_declared"}

    def review(self, key="request-001", revision=0):
        body = create_scan_review_template(self.scan)
        body.update(expected_revision=revision, request_id=key)
        body["identity"].update(decision="confirm", statement_scope="consolidated", comparative_restated=False)
        body["decisions"][0].update(decision="confirm", evidence_confirmed=True, note="对照原文核对两列和单位")
        return body


class StoreTests(FixtureBase):
    def test_pending_never_calculated(self):
        body = create_scan_review_template(self.scan); body.update(expected_revision=0, request_id="pending-001")
        result = self.store.submit("task1", body, self.actor)
        self.assertEqual(result["status"], "blocked"); self.assertEqual(result["analysis"]["facts"], [])

    def test_exact_decimal_and_server_identity(self):
        body = self.review(); body.update(reviewed_at="forged", reviewer={"id":"forged","kind":"machine"})
        result = self.store.submit("task1", body, self.actor)
        self.assertEqual(result["analysis"]["facts"][0]["value"], "2000000.010000")
        self.assertEqual(result["review"]["review_record"]["reviewer"]["id"], "reviewer")
        self.assertNotEqual(result["review"]["review_record"]["reviewed_at"], "forged")

    def test_idempotent_retry(self):
        body=self.review(); one=self.store.submit("task1",body,self.actor); two=self.store.submit("task1",body,self.actor)
        self.assertEqual(one,two);self.assertEqual(len(self.store.history("task1")),1)

    def test_same_key_different_body_conflicts(self):
        body=self.review();self.store.submit("task1",body,self.actor);body["decisions"][0]["note"]="modified"
        with self.assertRaises(ReviewConflict):self.store.submit("task1",body,self.actor)

    def test_stale_revision_rejected(self):
        self.store.submit("task1",self.review(),self.actor)
        with self.assertRaises(ReviewConflict):self.store.submit("task1",self.review("request-002"),self.actor)

    def test_older_retry_does_not_roll_back(self):
        body=self.review();self.store.submit("task1",body,self.actor)
        self.store.submit("task1",self.review("request-002",1),self.actor)
        with self.assertRaises(ReviewConflict):self.store.submit("task1",body,self.actor)

    def test_snapshot_cannot_be_replaced(self):
        other=deepcopy(self.scan);other["warnings"].append({"code":"changed"})
        with self.assertRaises(ReviewConflict):self.store.retain("task1",other,self.path)

    def test_restart_restores_original_and_decisions(self):
        self.store.submit("task1",self.review(),self.actor);new=ReviewStore(self.store.path).state("task1")
        self.assertEqual(new["revision"],1);self.assertEqual(new["scan"],self.scan)
        self.assertEqual(new["template"]["decisions"][0]["decision"],"confirm")

    def test_failed_validation_does_not_advance_revision(self):
        body=self.review();body["decisions"][0]["values"]["current"]["raw_value"]=0.1
        with self.assertRaises(ScanReviewError):self.store.submit("task1",body,self.actor)
        self.assertEqual(self.store.state("task1")["revision"],0)

    def test_source_change_blocked(self):
        self.path.write_bytes(b"%PDF-replaced")
        with self.assertRaises(ScanReviewError):self.store.submit("task1",self.review(),self.actor)

    def test_idempotent_retry_still_verifies_original_file(self):
        body=self.review();self.store.submit("task1",body,self.actor)
        self.path.write_bytes(b"%PDF-replaced")
        with self.assertRaises(ScanReviewError):self.store.submit("task1",body,self.actor)

    def test_two_concurrent_submissions_only_one_commits(self):
        def submit(key):
            try:return self.store.submit("task1",self.review(key),self.actor)["review_revision"]
            except ReviewConflict:return "conflict"
        with concurrent.futures.ThreadPoolExecutor(2) as executor:values=list(executor.map(submit,["request-001","request-002"]))
        self.assertCountEqual(values,[1,"conflict"])


class ApiTests(FixtureBase):
    def setUp(self):
        super().setUp()
        self.tasks={"task1":{"id":"task1","result":{"scan":self.scan,"source_path":str(self.path)}}}
        self.host=SimpleNamespace(app=FastAPI(),BASE_DIR=self.root,UPLOAD_DIR=self.root,MAX_FILE_SIZE=80*1024*1024)
        self.host.get_result=lambda tid:self.tasks[tid]["result"]
        self.host.update_task=lambda tid,**kwargs:self.tasks[tid].update(kwargs)
        self.host.deterministic_report=lambda analysis:"# 扫描复核结果"
        self.host.log_event=lambda *args:None
        register_scan_review_routes(self.host)
        self.client=TestClient(self.host.app,client=("127.0.0.1",12345))

    def test_roundtrip_and_revision_history(self):
        state=self.client.get('/api/scan/task1/state').json();self.assertEqual(state['revision'],0)
        body=state['template'];body.update(self.review());body['reviewer']={'id':'zqj','kind':'human'}
        response=self.client.post('/api/scan/task1/review',json=body);self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['review_revision'],1);self.assertIn('scan',response.json())
        self.assertEqual(self.client.get('/api/scan/task1/reviews').json()['reviews'][0]['review']['reviewer']['id'],'zqj')
        self.assertEqual(self.client.get('/api/scan/task1/state').json()['revision'],1)

    def test_missing_human_name_is_rejected(self):
        self.client.get('/api/scan/task1/state')
        self.assertEqual(self.client.post('/api/scan/task1/review',json=self.review()).status_code,400)

    def test_remote_requires_host_identity_hook(self):
        remote=TestClient(self.host.app,client=('10.0.0.2',2345));body=self.review();body['reviewer']={'id':'claimed'}
        self.assertEqual(remote.post('/api/scan/task1/review',json=body).status_code,403)
        self.host.scan_review_actor=lambda request:{'id':'logged-in-reviewer','kind':'human'}
        response=remote.post('/api/scan/task1/review',json=body)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['payload']['reviewer_identity_basis'],'host_authenticated')

    def test_preview_verifies_source_and_renders(self):
        with patch('scan_review_extension.api.render_page',return_value=(b'png',600,800)):
            response=self.client.get('/api/scan/task1/pages/1');self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['x-pdf-width'],'600')
        self.path.write_bytes(b'%PDF-replaced')
        self.assertEqual(self.client.get('/api/scan/task1/pages/1').status_code,400)

    def test_bad_upload_pages_do_not_create_task(self):
        response=self.client.post('/api/scan/tasks?page_numbers=0&company_name=test&report_year=2025',content=b'%PDF-')
        self.assertEqual(response.status_code,400)

    def test_registration_is_idempotent(self):
        count=len(self.host.app.routes);register_scan_review_routes(self.host);self.assertEqual(len(self.host.app.routes),count)

    def test_state_recovers_commit_before_host_sync_failure(self):
        self.client.get('/api/scan/task1/state')
        body=self.review();body['reviewer']={'id':'zqj'}
        real_update=self.host.update_task
        self.host.update_task=lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('simulated host write failure'))
        with self.assertRaises(RuntimeError):self.client.post('/api/scan/task1/review',json=body)
        self.host.update_task=real_update
        response=self.client.get('/api/scan/task1/state')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.tasks['task1']['result']['review_revision'],1)


if __name__=='__main__':unittest.main()
