import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from decimal import Decimal, localcontext
from pathlib import Path
from io import StringIO
from unittest.mock import patch

from financial_engine import (ScanReviewError, analyze_reviewed_scan,
                              create_scan_review_template, prepare_reviewed_scan)
from financial_engine.scan_review_cli import main as review_cli


class ScanReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "scan.pdf"
        self.path.write_bytes(b"%PDF-1.7\nreview boundary fixture")
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.scan = {"schema_version": "0.16", "document": {"document_id": digest[:16],
                      "file_sha256": digest, "file_name": "scan.pdf", "page_count": 2},
                     "identity_claim": {"company_name": "测试股份有限公司", "report_year": 2025,
                                        "verified_from_pdf": True},
                     "pages_read": [1, 2], "candidates": [], "warnings": [],
                     "review_status": "requires_human_review"}

    def candidate(self, code="revenue", current="200", previous="100", unit="元", page=1):
        document = self.scan["document"]
        def source(text, x):
            return {"document_id": document["document_id"], "file_sha256": document["file_sha256"],
                    "page": page, "bbox": [x, 100, x + 50, 115], "method": "ocr",
                    "text": text, "confidence": 0.99}
        multiplier = {"元": 1, "千元": 1000, "万元": 10000}[unit]
        with localcontext() as context:
            context.prec = 150
            values = [format(Decimal(raw.replace(",", "")) * multiplier, "f") for raw in (current, previous)]
        candidate = {"candidate_id": f"{document['document_id']}:{page}:2025:{code}",
                     "metric_code": code, "period": "2025", "previous_period": "2024",
                     "value": values[0], "previous_value": values[1], "unit": "CNY", "raw_unit": unit,
                     "statement_scope": "summary_disclosure", "status": "machine_checked",
                     "checks": {"year_columns": True, "ocr_confidence": True, "unit": True,
                                "reported_yoy_percentage": True, "quarterly_sum": None},
                     "sources": {"label": source(code, 20), "current": source(current, 200),
                                 "previous": source(previous, 300), "percentage": None}}
        self.scan["candidates"].append(candidate)
        return candidate

    def reviewed(self, actions=None):
        review = create_scan_review_template(self.scan)
        review.update({"review_id": "review-001", "reviewer": {"id": "member2", "kind": "human"},
                       "reviewed_at": "2026-10-07T12:00:00+08:00"})
        review["identity"].update({"decision": "confirm", "statement_scope": "consolidated",
                                    "comparative_restated": False})
        for item in review["decisions"]:
            item.update({"decision": (actions or {}).get(item["candidate_id"], "confirm"),
                         "evidence_confirmed": True, "note": "Checked both printed cells against source"})
        return review

    def analyze(self, review):
        return analyze_reviewed_scan(self.scan, review, source_path=self.path)

    def test_unchecked_template_never_admits_machine_checked_values(self):
        self.candidate()
        original = deepcopy(self.scan)
        result = self.analyze(create_scan_review_template(self.scan))
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["analysis"]["facts"], [])
        self.assertEqual(result["analysis"]["metrics"], [])
        self.assertEqual(self.scan, original)

    def test_confirmed_values_use_correct_period_cell_anchors_and_existing_formula(self):
        self.candidate(unit="万元")
        review = self.reviewed()
        result = self.analyze(review)
        facts = {fact["period"]: fact for fact in result["analysis"]["facts"]}
        self.assertEqual(facts["2025"]["value"], "2000000")
        self.assertEqual(facts["2024"]["sources"][0]["row_bbox"], [300, 100, 350, 115])
        self.assertEqual(facts["2025"]["sources"][0]["row_bbox"], [200, 100, 250, 115])
        metric = next(item for item in result["analysis"]["metrics"] if item["metric_code"] == "revenue_yoy")
        self.assertEqual(Decimal(metric["value"]), Decimal("100"))
        self.assertEqual(result["review"]["review_record"]["reviewed_at"], "2026-10-07T04:00:00Z")
        self.assertEqual(facts["2025"]["verification_status"], "human_reviewed")
        self.assertEqual(facts["2025"]["quality"], "extracted")
        json.dumps(result, allow_nan=False)

    def test_correction_keeps_original_and_accepts_distinct_cell_units(self):
        self.candidate(unit="万元")
        review = self.reviewed()
        decision = review["decisions"][0]
        decision["decision"] = "correct"
        decision["values"] = {"current": {"raw_value": "250", "raw_unit": "万元"},
                              "previous": {"raw_value": "1000000", "raw_unit": "元"}}
        result = self.analyze(review)
        facts = {fact["period"]: fact for fact in result["analysis"]["facts"]}
        self.assertEqual(facts["2024"]["value"], "1000000")
        self.assertEqual(facts["2024"]["raw_unit"], "元")
        self.assertEqual(facts["2024"]["sources"][0]["ocr_original"]["raw_unit"], "万元")
        self.assertEqual(facts["2024"]["sources"][0]["ocr_original"]["text"], "100")
        self.assertEqual(facts["2025"]["sources"][0]["reviewed_disclosure"]["raw_value"], "250")
        growth = next(item for item in result["analysis"]["metrics"] if item["metric_code"] == "revenue_yoy")
        self.assertEqual(Decimal(growth["value"]), Decimal("150"))

    def test_pending_and_rejected_fields_cannot_support_formulas_or_risk(self):
        revenue = self.candidate()
        profit = self.candidate("attributable_net_profit", "20", "10")
        self.candidate("operating_cash_flow", "-10", "-20")
        review = self.reviewed({revenue["candidate_id"]: "pending", profit["candidate_id"]: "reject"})
        result = self.analyze(review)
        codes = {fact["metric_code"] for fact in result["analysis"]["facts"]}
        self.assertEqual(codes, {"operating_cash_flow"})
        self.assertEqual(result["status"], "partial")
        unavailable = {item["metric_code"]: item for item in result["analysis"]["unavailable_metrics"]}
        self.assertEqual(unavailable["revenue_yoy"]["reason_code"], "UNCONFIRMED_INPUT")
        self.assertEqual(unavailable["attributable_net_profit_yoy"]["reason_code"], "REJECTED_INPUT")
        self.assertFalse(result["analysis"]["findings"])
        self.assertTrue(all(item["status"] in ("insufficient_data", "not_evaluated")
                            for item in result["analysis"]["risk_assessments"]))
        missing = next(item for item in result["analysis"]["unavailable_metrics"]
                       if item["metric_code"] == "cash_to_consolidated_profit")
        self.assertEqual(missing["reason_code"], "MISSING_INPUT")

    def test_conflicting_confirmed_duplicates_are_withheld_without_blocking_other_fields(self):
        self.candidate(current="200", page=1)
        self.candidate(current="201", page=2)
        self.candidate("operating_cash_flow", "50", "25")
        result = self.analyze(self.reviewed())
        self.assertEqual({fact["metric_code"] for fact in result["analysis"]["facts"]}, {"operating_cash_flow"})
        self.assertIn("REVIEWED_VALUE_CONFLICT", {warning["code"] for warning in result["analysis"]["warnings"]})
        self.assertEqual([item["status"] for item in result["review"]["decisions"][:2]],
                         ["withheld_conflict", "withheld_conflict"])
        self.assertFalse(any(item["metric_code"] == "revenue_yoy" for item in result["analysis"]["metrics"]))
        unavailable = next(item for item in result["analysis"]["unavailable_metrics"] if item["metric_code"] == "revenue_yoy")
        self.assertEqual(unavailable["reason_code"], "CONFLICTING_INPUT")
        self.assertTrue(any(item["reason"] == "CONFLICTING_INPUT" for rule in result["analysis"]["risk_assessments"]
                            for item in rule["missing_or_invalid"]))

    def test_matching_duplicate_sources_do_not_become_independent_cross_checks(self):
        self.candidate(page=1)
        self.candidate(page=2)
        result = self.analyze(self.reviewed())
        self.assertEqual(len(result["analysis"]["facts"]), 2)
        self.assertEqual(result["analysis"]["facts"][0]["quality"], "extracted")
        self.assertEqual(len(result["analysis"]["facts"][0]["sources"]), 2)

    def test_file_change_or_candidate_change_invalidates_review(self):
        self.candidate()
        review = self.reviewed()
        self.scan["candidates"][0]["checks"]["quarterly_sum"] = False
        with self.assertRaisesRegex(ScanReviewError, "STALE_REVIEW"):
            self.analyze(review)
        self.scan["candidates"][0]["checks"]["quarterly_sum"] = None
        self.path.write_bytes(b"%PDF-1.7\nchanged")
        with self.assertRaisesRegex(ScanReviewError, "SOURCE_HASH_MISMATCH"):
            self.analyze(review)

    def test_model_declared_review_and_edited_confirmation_are_rejected(self):
        self.candidate()
        review = self.reviewed()
        review["reviewer"]["kind"] = "model"
        with self.assertRaises(ScanReviewError):
            self.analyze(review)
        review["reviewer"]["kind"] = "human"
        review["decisions"][0]["values"]["current"]["raw_value"] = "300"
        with self.assertRaisesRegex(ScanReviewError, "Use correct"):
            self.analyze(review)

    def test_float_invalid_grouping_and_missing_evidence_are_rejected(self):
        self.candidate()
        for raw in (0.1, "1,2", "NaN", "Infinity", "1e3"):
            with self.subTest(raw=raw):
                review = self.reviewed()
                review["decisions"][0]["decision"] = "correct"
                review["decisions"][0]["values"]["current"]["raw_value"] = raw
                with self.assertRaises(ScanReviewError):
                    self.analyze(review)
        review = self.reviewed()
        review["decisions"][0]["evidence_confirmed"] = False
        with self.assertRaises(ScanReviewError):
            self.analyze(review)

    def test_identity_scope_and_restated_period_are_checked(self):
        self.candidate()
        for key, value in (("company_name", "其他公司"), ("report_year", 2024),
                           ("statement_scope", "parent"), ("currency", "USD"),
                           ("comparative_restated", None)):
            with self.subTest(key=key):
                review = self.reviewed()
                review["identity"][key] = value
                result = self.analyze(review)
                self.assertEqual(result["status"], "blocked")
                self.assertFalse(result["analysis"]["facts"])
        review = self.reviewed()
        review["identity"]["comparative_restated"] = True
        facts = {fact["period"]: fact for fact in self.analyze(review)["analysis"]["facts"]}
        self.assertTrue(facts["2024"]["comparative_restated"])
        self.assertFalse(facts["2025"]["comparative_restated"])

    def test_huge_amounts_remain_exact_and_zero_negative_bases_stay_unavailable(self):
        huge = "1234567890123456789012345678901234567890.12"
        self.candidate(current=huge, previous="100")
        result = self.analyze(self.reviewed())
        self.assertEqual(result["analysis"]["facts"][0]["value"], huge)
        for prior, reason in (("0", "DIVISION_BY_ZERO"), ("-100", "NONPOSITIVE_INPUT")):
            with self.subTest(prior=prior):
                self.scan["candidates"] = []
                self.candidate(previous=prior)
                result = self.analyze(self.reviewed())
                item = next(metric for metric in result["analysis"]["unavailable_metrics"]
                            if metric["metric_code"] == "revenue_yoy")
                self.assertEqual(item["reason_code"], reason)

    def test_unknown_duplicate_review_ids_and_foreign_source_are_rejected(self):
        self.candidate()
        review = self.reviewed()
        review["decisions"].append(deepcopy(review["decisions"][0]))
        with self.assertRaises(ScanReviewError):
            self.analyze(review)
        review = self.reviewed()
        review["decisions"][0]["candidate_id"] = "unrecognized"
        with self.assertRaises(ScanReviewError):
            self.analyze(review)
        self.scan["candidates"][0]["sources"]["current"]["file_sha256"] = "0" * 64
        with self.assertRaises(ScanReviewError):
            create_scan_review_template(self.scan)

    def test_cli_preserves_existing_forms_and_writes_nothing_on_source_mismatch(self):
        self.candidate()
        folder = Path(self.tmp.name)
        bundle = folder / "candidates.json"
        bundle.write_text(json.dumps(self.scan), encoding="utf-8-sig")
        form_path = folder / "pending.json"
        template_args = ["review", "template", str(bundle), "--output", str(form_path)]
        with patch("sys.argv", template_args), redirect_stdout(StringIO()):
            review_cli()
        original_form = form_path.read_bytes()
        with patch("sys.argv", template_args), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            review_cli()
        self.assertEqual(form_path.read_bytes(), original_form)
        review_path = folder / "completed.json"
        review_path.write_text(json.dumps(self.reviewed()), encoding="utf-8")
        self.path.write_bytes(b"%PDF-1.7\nchanged after review")
        output = folder / "result.json"
        args = ["review", "analyze", str(bundle), str(review_path), "--source-pdf", str(self.path), "--output", str(output)]
        with patch("sys.argv", args), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            review_cli()
        self.assertFalse(output.exists())


class StoredScanReviewReplayTests(unittest.TestCase):
    def test_three_existing_simulated_scans_match_manual_gold_after_review_replay(self):
        # This is a test stub for a human confirmation record, never a real approval.
        project = Path(__file__).resolve().parents[1]
        cases = [("byd.pdf", "scan_byd_p11_v016.json", "byd_p11_scan.pdf"),
                 ("宁德时代.pdf", "scan_catl_p11_v016.json", "catl_p11_scan.pdf"),
                 ("中国能建.pdf", "scan_ceec_p13_v016.json", "ceec_p13_scan.pdf")]
        base = project.parent
        if not all((base / bundle).is_file() and (base / "ocr_runtime" / source).is_file()
                   for _, bundle, source in cases):
            self.skipTest("Optional v0.16 stored OCR snapshots and simulated scan PDFs are absent")
        gold = json.loads((project / "benchmarks" / "annual_2025_gold.json").read_text(encoding="utf-8"))
        gold_docs = {item["file_name"]: item for item in gold["documents"]}
        checked = 0
        for file_name, bundle_name, source_name in cases:
            with self.subTest(company=file_name):
                scan = json.loads((base / bundle_name).read_text(encoding="utf-8-sig"))
                review = create_scan_review_template(scan)
                review.update({"review_id": "REGRESSION_FIXTURE_ONLY", "reviewer": {"id": "SIMULATED_HUMAN_RECORD", "kind": "human"},
                               "reviewed_at": "2026-10-07T00:00:00Z"})
                review["identity"].update({"decision": "confirm", "statement_scope": "consolidated", "comparative_restated": False})
                for decision in review["decisions"]:
                    decision.update({"decision": "confirm", "evidence_confirmed": True,
                                     "note": "Test stub replay of independently labelled source cells; no new human approval"})
                result = analyze_reviewed_scan(scan, review, source_path=base / "ocr_runtime" / source_name)
                labelled = {(item["period"], item["code"]): item for item in gold_docs[file_name]["facts"]}
                for fact in result["analysis"]["facts"]:
                    answer = labelled[(fact["period"], fact["metric_code"])]
                    multiplier = {"元": 1, "千元": 1000, "万元": 10000, "亿元": 100000000}[answer["unit"]]
                    expected = Decimal(answer["raw"].replace(",", "")) * multiplier
                    self.assertEqual(Decimal(fact["value"]), expected)
                    role = "current" if fact["period"] == "2025" else "previous"
                    candidate = next(item for item in scan["candidates"] if item["metric_code"] == fact["metric_code"])
                    self.assertEqual(fact["sources"][0]["row_bbox"], candidate["sources"][role]["bbox"])
                    checked += 1
                self.assertFalse(result["analysis"]["findings"])
                self.assertTrue(result["analysis"]["unavailable_metrics"])
        self.assertEqual(checked, 20)


if __name__ == "__main__":
    unittest.main()
