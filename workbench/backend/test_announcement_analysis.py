import copy
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app as host
from announcement_analysis import (ModelReply, DeepSeekProvider, _load_json, analyze_event,
                                   format_event_amount, provider_from_environment)
from financial_engine.announcements import analyze_announcement


def line(text, y, page=1):
    return {"text": text, "page": page, "bbox": [70, y, 520, y + 15], "method": "pdf_text", "confidence": None}


def fixture(cross_page=False):
    lines = [line("证券代码：300750 证券简称：宁德时代", 10), line("宁德时代新能源科技股份有限公司", 30),
             line("关于全资子公司参与投资产业投资基金的公告", 50),
             line("基金目标规模为15亿美元，香港时代拟认缴出资额不高于2.25亿美元。", 80),
             line("资金来源全部为境外自有资金。", 110), line("基本存续期限为10年。", 140),
             line("基金不纳入公司合并报表范围。", 170), line("五、风险提示", 200),
             line("基金投资具有投资周期长、流动性较低等特点，在后续存续过程中", 230)]
    continuation = [line("存在因行业环境变化导致投资项目不能实现预期收益的风险。", 20 if cross_page else 255, 2 if cross_page else 1),
                    line("六、其他相关事项说明", 50 if cross_page else 285, 2 if cross_page else 1),
                    line("本节为其他事项，不属于风险段落。", 80 if cross_page else 310, 2 if cross_page else 1),
                    line("2025年5月16日", 110 if cross_page else 335, 2 if cross_page else 1)]
    pages = [{"page": 1, "lines": lines}]
    if cross_page:
        pages[0]["lines"].append(line("1", 750))
        pages.append({"page": 2, "lines": continuation})
    else:
        pages[0]["lines"].extend(continuation)
    document = {"document_id": "a" * 16, "file_sha256": "a" * 64, "file_name": "fixture.pdf",
                "page_count": len(pages), "pages_read": len(pages), "status": "text_extracted", "warnings": [], "pages": pages}
    with patch("financial_engine.announcements.read_pdf_document", return_value=document):
        return analyze_announcement("unused.pdf")


def block(result, substring):
    return next(item for item in result["evidence_blocks"] if substring in item["text"])


def valid_output(result):
    funding = block(result, "资金来源")
    horizon = block(result, "存续期限")
    risk = block(result, "投资周期长")
    amounts = block(result, "基金目标规模")
    extraction = {"facts": [{"field": "funding_source", "text": funding["text"], "evidence_ids": [funding["evidence_id"]]}]}
    interpretation = {
        "summary": [{"text": amounts["text"], "evidence_ids": [amounts["evidence_id"]], "kind": "source_fact"}],
        "impacts": [{"text": "较长的投资期限可能增加资金安排和退出管理要求，需结合实际缴款进度核查。",
                     "evidence_ids": [horizon["evidence_id"], funding["evidence_id"]], "kind": "interpretation"}],
        "risks": [{"text": risk["text"], "evidence_ids": [risk["evidence_id"]], "kind": "source_fact"}],
        "follow_up": ["实际缴款安排是否已经明确？"]}
    return extraction, interpretation


class GroundedAnalysisTests(unittest.TestCase):
    def run_provider(self, result=None, change=None):
        result = result or fixture()
        outputs = dict(zip(("extract", "interpret"), valid_output(result)))
        if change:
            change(outputs)
        calls = []
        def provider(stage, payload):
            calls.append((stage, payload))
            return ModelReply(outputs[stage], "offline-fixture", "mock-request", {"total_tokens": 20})
        analyzed, report, generator = analyze_event(result, provider=provider)
        return analyzed, report, generator, calls

    def test_complete_risk_body_is_not_heading(self):
        result = fixture()
        risk = result["event"]["risk_clauses"]
        self.assertTrue(any("不能实现预期收益" in item["text"] for item in risk))
        self.assertFalse(any(item["text"] == "五、风险提示" for item in risk))
        self.assertEqual(result["event"]["event_type"], "fund_investment")
        self.assertTrue(result["review_reasons"])

    def test_cross_page_risk_has_both_boxes(self):
        result = fixture(cross_page=True)
        risk = next(item for item in result["event"]["risk_clauses"] if "投资周期长" in item["text"])
        self.assertEqual({source["page"] for source in risk["sources"]}, {1, 2})
        self.assertIn("不能实现预期收益", risk["text"])

    def test_next_section_not_included_in_risk_body(self):
        risks = fixture()["event"]["risk_clauses"]
        self.assertFalse(any("本节为其他事项" in risk["text"] for risk in risks))

    def test_offline_mode_keeps_original_event_and_explicit_model_state(self):
        original = fixture()
        before = copy.deepcopy(original)
        result, report, generator = analyze_event(original)
        self.assertEqual(original, before)
        self.assertEqual(result["event"], original["event"])
        self.assertEqual(generator, "deterministic_template")
        self.assertEqual(result["llm_metadata"]["status"], "not_configured")
        self.assertIn("15亿美元", report)
        self.assertNotIn("财务分析结果", report)

    def test_two_calls_receive_full_evidence_and_render_grounded_analysis(self):
        result, report, generator, calls = self.run_provider()
        self.assertEqual([stage for stage, _ in calls], ["extract", "interpret"])
        self.assertTrue(any("不能实现预期收益" in item["text"] for item in calls[0][1]["evidence"]))
        self.assertTrue(calls[1][1]["verified_extraction"])
        self.assertEqual(generator, "deepseek")
        self.assertEqual(result["llm_metadata"]["status"], "success")
        self.assertIn("较长的投资期限", report)
        self.assertIn("PDF第", report)
        self.assertFalse(result["event_analysis"]["materiality_assessed"])
        self.assertEqual(result["llm_metadata"]["calls"][0]["usage"]["total_tokens"], 20)

    def test_unknown_evidence_rejects_extraction_without_second_call(self):
        result, _, generator, calls = self.run_provider(change=lambda out: out["extract"]["facts"][0].update(evidence_ids=["invented"]))
        self.assertEqual(result["llm_metadata"]["status"], "failed")
        self.assertEqual(len(calls), 1)
        self.assertEqual(generator, "deterministic_template_fallback")

    def test_paraphrased_extraction_is_rejected(self):
        result, _, _, _ = self.run_provider(change=lambda out: out["extract"]["facts"][0].update(text="公司已支付全部投资。"))
        self.assertEqual(result["llm_metadata"]["calls"][0]["error_code"], "EXTRACTION_NOT_VERBATIM")

    def assert_bad_claim(self, text, source_text, error):
        base = fixture()
        evidence_id = block(base, source_text)["evidence_id"]
        def mutate(outputs):
            outputs["interpret"]["impacts"] = [{"text": text, "evidence_ids": [evidence_id], "kind": "interpretation"}]
        result, report, generator, _ = self.run_provider(base, mutate)
        self.assertEqual(result["llm_metadata"]["status"], "partial")
        self.assertEqual(result["llm_metadata"]["calls"][-1]["error_code"], error)
        self.assertEqual(generator, "deterministic_template_fallback")
        self.assertEqual(result["event_analysis"]["impacts"], [])
        return report

    def test_invented_amount_and_currency_rejected(self):
        self.assert_bad_claim("拟认缴上限为3亿美元。", "基金目标规模", "UNGROUNDED_NUMBER_OR_UNIT")
        self.assert_bad_claim("拟认缴上限为2.25亿元。", "基金目标规模", "UNGROUNDED_NUMBER_OR_UNIT")

    def test_amount_upper_bound_cannot_disappear(self):
        self.assert_bad_claim("公司拟认缴2.25亿美元。", "基金目标规模", "UPPER_BOUND_LOST")

    def test_payment_status_cannot_change(self):
        self.assert_bad_claim("公司已支付不高于2.25亿美元。", "基金目标规模", "PAYMENT_STATUS_CHANGED")

    def test_consolidation_negation_cannot_change(self):
        self.assert_bad_claim("基金纳入公司合并报表。", "不纳入", "CONSOLIDATION_NEGATION_CHANGED")

    def test_fraud_certainty_rejected(self):
        self.assert_bad_claim("已证实公司财务造假。", "投资周期长", "UNSUPPORTED_CERTAINTY")

    def test_followup_cannot_introduce_numeric_predictions(self):
        result, _, _, _ = self.run_provider(change=lambda out: out["interpret"].update(follow_up=["公司利润将增长50%吗？"]))
        self.assertEqual(result["llm_metadata"]["calls"][-1]["error_code"], "FOLLOW_UP_MUST_NOT_INTRODUCE_NUMERIC_CLAIMS")

    def test_provider_failure_is_sanitized_and_keeps_rule_data(self):
        def provider(*_):
            raise RuntimeError("secret-key-must-not-be-persisted")
        result, report, generator = analyze_event(fixture(), provider=provider)
        self.assertEqual(generator, "deterministic_template_fallback")
        self.assertNotIn("secret-key", str(result) + report)
        self.assertTrue(result["event"]["amount_candidates"])

    def test_cancellation_between_stages_prevents_next_call(self):
        count = []
        def checkpoint():
            count.append(1)
            if len(count) == 2:
                raise RuntimeError("cancelled")
        def provider(*_):
            return ModelReply(valid_output(fixture())[0])
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            analyze_event(fixture(), provider=provider, checkpoint=checkpoint)

    def test_unsupported_type_does_not_call_model(self):
        original = fixture()
        original["event"]["event_type"] = None
        with patch("openai.OpenAI") as client:
            result, _, _ = analyze_event(original)
        self.assertEqual(result["llm_metadata"]["status"], "unsupported_or_no_evidence")
        client.assert_not_called()

    def test_source_window_is_disclosed(self):
        result, _, _ = analyze_event(fixture(), max_source_chars=120)
        self.assertFalse(result["llm_metadata"]["all_extracted_blocks_provided"])

    def test_prefix_currency_and_wrapped_upper_bound_are_preserved(self):
        from financial_engine.announcements import _extract_amounts
        source = {"document_id": "test", "file_sha256": "a" * 64, "pages": [{"page": 1, "lines": [
            line("项目总投资不超过人民币", 50), line("140亿元。", 76)]}]}
        amounts = _extract_amounts(source, "investment_expansion")
        self.assertEqual((amounts[0]["amount"], amounts[0]["currency"], amounts[0]["qualifier"]), ("14000000000", "CNY", "不超过"))

    def test_footer_is_not_a_risk_clause(self):
        from financial_engine.announcements import _risk_clauses
        result = fixture()
        blocks = copy.deepcopy(result["evidence_blocks"])
        blocks.append({"evidence_id": "footer", "text": "特此公告。", "sources": [blocks[0]["sources"][0]], "is_heading": False})
        self.assertFalse(any(item["text"] == "特此公告。" for item in _risk_clauses({}, blocks)))

    def test_deepseek_transport_uses_json_and_retains_audit_metadata(self):
        reply = SimpleNamespace(id="request-fixture", usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 42}),
                                choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content='{"facts":[]}'))])
        with patch("openai.OpenAI") as client:
            client.return_value.chat.completions.create.return_value = reply
            provider = DeepSeekProvider("test-only-key", "test-model")
            result = provider("extract", {"title": "fixture", "evidence": []})
            kwargs = client.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})
        self.assertEqual(kwargs["model"], "test-model")
        self.assertEqual(result.request_id, "request-fixture")
        self.assertEqual(result.usage["total_tokens"], 42)
        self.assertNotIn("test-only-key", str(kwargs))

    def test_deepseek_truncated_json_is_never_accepted(self):
        from announcement_analysis import ModelCallError
        reply = SimpleNamespace(choices=[SimpleNamespace(finish_reason="length", message=SimpleNamespace(content='{"facts":['))])
        with patch("openai.OpenAI") as client:
            client.return_value.chat.completions.create.return_value = reply
            with self.assertRaises(ModelCallError):
                DeepSeekProvider("test-only-key")("extract", {})

    def test_decimal_display_preserves_more_than_float_precision(self):
        self.assertEqual(format_event_amount("225000000", "USD"), "2.25亿美元")
        self.assertEqual(format_event_amount("123456789012345678901234567890.12", "CNY"), "1234567890123456789012.3456789012亿元")
        self.assertEqual(format_event_amount("0", "CNY"), "0元")

    def test_bad_json_duplicate_and_non_finite_values_rejected(self):
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '[]'):
            with self.assertRaises(ValueError):
                _load_json(text)

    def test_environment_switch_no_key_never_constructs_client(self):
        with patch("openai.OpenAI") as client:
            self.assertEqual(provider_from_environment({})[1], "not_configured")
            self.assertEqual(provider_from_environment({"DEEPSEEK_API_KEY": "fake", "ANNOUNCEMENT_LLM_ENABLED": "0"})[1], "disabled")
        client.assert_not_called()


class AnnouncementHostTests(unittest.TestCase):
    def test_announcement_report_routes_without_using_annual_prompt(self):
        analysis = fixture()
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "ANNOUNCEMENT_LLM_ENABLED": "1"}), patch("openai.OpenAI") as client:
            report, generator = host.generate_report(analysis)
        client.assert_not_called()
        self.assertEqual(generator, "deterministic_template")
        self.assertIn("event_analysis", analysis)
        self.assertIn("产业基金投资", report)

    def test_existing_upload_task_saves_enriched_result(self):
        with tempfile.TemporaryDirectory(dir=host.BASE_DIR) as temporary:
            root = Path(temporary)
            paths = {key: root / key for key in ("TASK_DIR", "LOG_DIR", "RESULT_DIR")}
            for path in paths.values():
                path.mkdir()
            with patch.multiple(host, **paths, TASKS={}, _CANCELLED_TASKS=set()), patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
                task = host.create_task("upload", file_name="fixture.pdf")
                payload = {"document_type": "announcement", "analysis": fixture()}
                with patch.object(host, "generate_report", side_effect=AssertionError("annual report path must not be called")):
                    host.finish_task(task["id"], payload)
                result = host.TASKS[task["id"]]["result"]
                self.assertEqual(host.TASKS[task["id"]]["status"], "completed")
                self.assertIn("event_analysis", result["analysis"])
                self.assertIn("llm_metadata", result["payload"]["analysis"])
                self.assertTrue((paths["RESULT_DIR"] / f"{task['id']}.json").is_file())


if __name__ == "__main__":
    unittest.main()
