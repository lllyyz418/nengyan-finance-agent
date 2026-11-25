import copy
import hashlib
import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import app as host
from fastapi.testclient import TestClient
from announcement_agent import (AgentReply, Budget, DeepSeekToolModel, EvidenceTools, run_evidence_agent, model_from_environment)
from announcement_context import HostAnnouncementContext, publication_date
from test_announcement_analysis import fixture, valid_output, block


def tool(name, arguments, call_id="tool1"):
    return AgentReply({"role": "assistant", "content": None, "reasoning_content": "private-reasoning-fixture",
                       "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}]},
                      "offline-native-tool-fixture", usage={"total_tokens": 50}, finish_reason="tool_calls")


def final(analysis=None):
    return AgentReply({"role": "assistant", "content": json.dumps(valid_output(analysis or fixture())[1], ensure_ascii=False)},
                      "offline-native-tool-fixture", "mock-id", {"total_tokens": 100})


class AgentTests(unittest.TestCase):
    def test_major_contract_without_bid_word_is_routed_conservatively(self):
        from financial_engine.announcements import _event_type
        self.assertEqual(_event_type("关于签署重大销售合同的公告", {}), "bid_award")
        self.assertIsNone(_event_type("关于修改公司章程的公告", {}))
    def test_model_selects_tool_then_returns_grounded_report(self):
        calls = []
        def model(messages, tools, timeout):
            calls.append(copy.deepcopy(messages))
            return tool("search_evidence", {"query": "不纳入"}) if len(calls) == 1 else final()
        result, report, generator = run_evidence_agent(fixture(), model=model)
        self.assertEqual(generator, "deepseek")
        self.assertEqual(result["llm_metadata"]["tool_trace"][0]["status"], "found")
        self.assertEqual(calls[1][-2]["reasoning_content"], "private-reasoning-fixture")
        self.assertEqual(calls[1][-1]["role"], "tool")
        self.assertNotIn("private-reasoning-fixture", str(result) + report)

    def test_repeated_tool_does_not_repeat_work(self):
        sequence = iter([tool("list_financial_reports", {}), tool("list_financial_reports", {}, "tool2"), final()])
        context = SimpleNamespace(as_of="2025-05-16", as_of_mode="announcement", list_financial_reports=lambda: {"status": "not_found", "reason": "missing"})
        result, _, _ = run_evidence_agent(fixture(), model=lambda *_: next(sequence), context=context)
        self.assertEqual(result["llm_metadata"]["tool_trace"][1]["error_code"], "DUPLICATE_TOOL_REQUEST")

    def test_unknown_tools_cannot_execute(self):
        tools = EvidenceTools(fixture())
        self.assertEqual(tools.execute("run_shell", {"command": "anything"})["error_code"], "TOOL_NOT_ALLOWED")

    def test_tool_schema_rejects_paths_and_floats(self):
        tools = EvidenceTools(fixture())
        self.assertEqual(tools.execute("load_financial_report", {"report_id": "x", "path": "C:/secret"})["status"], "rejected")
        self.assertEqual(tools.execute("calculate_scale_reference", {"amount_index": 0.0, "financial_evidence_id": "x"})["status"], "rejected")

    def test_bad_final_can_be_repaired_once(self):
        bad = valid_output(fixture())[1]
        bad["summary"][0]["evidence_ids"] = ["nonexistent"]
        sequence = iter([AgentReply({"role": "assistant", "content": json.dumps(bad)}), final()])
        result, _, generator = run_evidence_agent(fixture(), model=lambda *_: next(sequence))
        self.assertEqual(generator, "deepseek")
        self.assertEqual([r["status"] for r in result["llm_metadata"]["calls"]], ["rejected", "accepted"])

    def test_no_unbounded_repair(self):
        calls = []
        def model(*_):
            calls.append(1)
            return AgentReply({"role": "assistant", "content": "invalid-json"})
        result, _, generator = run_evidence_agent(fixture(), model=model)
        self.assertEqual(len(calls), 2)
        self.assertEqual(generator, "deterministic_template_fallback")
        self.assertEqual(result["llm_metadata"]["status"], "failed")

    def test_round_and_token_budget_stop(self):
        result, _, _ = run_evidence_agent(fixture(), model=lambda *_: tool("search_evidence", {"query": "风险"}), budget=Budget(max_rounds=1))
        self.assertEqual(result["llm_metadata"]["status"], "budget_exhausted")
        excessive = final(); excessive.usage = {"total_tokens": 101}
        result, _, generator = run_evidence_agent(fixture(), model=lambda *_: excessive, budget=Budget(max_total_tokens=100))
        self.assertEqual(generator, "deterministic_template_fallback")

    def test_no_key_does_not_call_sdk(self):
        with patch("openai.OpenAI") as client:
            self.assertEqual(model_from_environment({})[1], "not_configured")
            result, _, generator = run_evidence_agent(fixture())
        client.assert_not_called()
        self.assertEqual(generator, "deterministic_template")
        self.assertEqual(result["llm_metadata"]["status"], "not_configured")

    def test_future_evidence_from_previous_question_is_not_inherited(self):
        analysis = fixture()
        future = {"evidence_id": "future", "text": "未来数据", "source_type": "announcement_pdf", "sources": [{"file_sha256": "b" * 64, "page": 1}]}
        analysis["evidence_blocks"].append(future)
        result, _, _ = run_evidence_agent(analysis)
        self.assertNotIn("future", [b["evidence_id"] for b in result["evidence_blocks"]])

    def test_native_sdk_keeps_thinking_and_tool_messages(self):
        message = {"role": "assistant", "content": "{}", "reasoning_content": "private"}
        response = SimpleNamespace(id="mock-request", usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 5}),
                                   choices=[SimpleNamespace(message=SimpleNamespace(model_dump=lambda **_: message), finish_reason="stop")])
        with patch("openai.OpenAI") as client:
            client.return_value.chat.completions.create.return_value = response
            model = DeepSeekToolModel("fake-key", thinking=True)
            reply = model([{"role": "user", "content": "data"}], [], 10)
            args = client.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(args["extra_body"]["thinking"]["type"], "enabled")
        self.assertEqual(args["extra_body"]["reasoning_effort"], "high")
        self.assertNotIn("temperature", args)
        self.assertEqual(reply.message["reasoning_content"], "private")

    def test_real_sdk_serializes_thinking_history_and_native_tool_results(self):
        import httpx
        from openai import OpenAI
        requests = []
        def transport(request):
            payload = json.loads(request.content)
            requests.append(payload)
            message = tool("search_evidence", {"query": "不纳入"}).message if len(requests) == 1 else final().message
            return httpx.Response(200, json={"id": "mock-http", "object": "chat.completion", "created": 1,
                "model": "deepseek-flash", "choices": [{"index": 0, "message": message,
                "finish_reason": "tool_calls" if len(requests) == 1 else "stop"}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 20, "total_tokens": 40}})
        client = httpx.Client(transport=httpx.MockTransport(transport), trust_env=False)
        model = DeepSeekToolModel.__new__(DeepSeekToolModel)
        model.client = OpenAI(api_key="mock-http-no-network", base_url="https://api.deepseek.com", http_client=client)
        model.model, model.thinking, model.effort = "deepseek-flash", True, "high"
        try:
            result, _, generator = run_evidence_agent(fixture(), model=model)
        finally:
            model.client.close()
        self.assertEqual(generator, "deepseek")
        self.assertEqual(requests[0]["thinking"], {"type": "enabled"})
        self.assertEqual(requests[0]["reasoning_effort"], "high")
        self.assertEqual(requests[1]["messages"][-2]["reasoning_content"], "private-reasoning-fixture")
        self.assertEqual(requests[1]["messages"][-1]["role"], "tool")
        self.assertNotIn("private-reasoning-fixture", str(result))

    def test_scale_reference_is_decimal_and_not_materiality_rating(self):
        analysis = fixture()
        analysis["event"]["amount_candidates"][1].update(currency="CNY", amount="1")
        tools = EvidenceTools(analysis)
        key = "fin:test"
        tools.financial[key] = {"evidence_id": key, "metric_code": "total_assets", "value": "3", "currency": "CNY", "period": "2024", "source_id": "report:x"}
        tools.add_blocks([{"evidence_id": key, "text": "总资产3元", "sources": [{"page": 1}], "source_type": "normalized_financial_fact"}])
        output = tools.ratio(1, key)
        self.assertEqual(output["value"], "33.33333333")
        self.assertFalse(output["materiality_assessed"])
        self.assertTrue(output["reference_only"])

    def test_scale_reference_currency_and_zero_denominator_blocked(self):
        tools = EvidenceTools(fixture())
        tools.financial["fin:test"] = {"metric_code": "total_assets", "value": "0", "currency": "CNY", "period": "2024"}
        self.assertEqual(tools.ratio(1, "fin:test")["status"], "not_comparable")
        tools.financial["fin:test"]["currency"] = "USD"
        self.assertEqual(tools.ratio(1, "fin:test")["status"], "not_comparable")
        self.assertEqual(tools.ratio(0, "fin:test")["status"], "not_comparable")

    def test_float_money_is_never_used_for_calculation(self):
        analysis = fixture()
        analysis["event"]["amount_candidates"][1].update(currency="CNY", amount=1.1)
        tools = EvidenceTools(analysis)
        tools.financial["fin:test"] = {"metric_code": "total_assets", "value": "3", "currency": "CNY", "period": "2024"}
        with self.assertRaisesRegex(Exception, "MONEY_MUST_BE_DECIMAL_STRING"):
            tools.ratio(1, "fin:test")


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=host.BASE_DIR)
        self.root = Path(self.temporary.name)
        self.core = SimpleNamespace(RESULT_DIR=self.root, get_report_library=lambda: self.library)
        self.library = SimpleNamespace(records=[])

    def tearDown(self):
        assert self.root.resolve().is_relative_to(host.BASE_DIR.resolve())
        self.temporary.cleanup()

    def record(self, year, published, sha):
        return {"stock_code": "300750", "report_year": year, "published_at": published, "sha256": sha * 64, "title": "年报"}

    def test_announcement_date_excludes_future_and_unknown_reports(self):
        self.library.records = [self.record(2024, "2025-03-15", "a"), self.record(2025, "2026-03-10", "b"), self.record(2023, None, "c")]
        context = HostAnnouncementContext(self.core, fixture())
        output = context.list_financial_reports()
        self.assertEqual([r["report_year"] for r in output["items"]], [2024])
        self.assertEqual(output["excluded"]["future_or_unknown_publication"], 2)

    def test_today_mode_is_explicit_and_can_see_later_reports(self):
        self.library.records = [self.record(2025, "2026-03-10", "a")]
        context = HostAnnouncementContext(self.core, fixture(), as_of_mode="today", today=date(2026, 10, 17))
        self.assertEqual(context.list_financial_reports()["items"][0]["report_year"], 2025)
        self.assertEqual(context.as_of, "2026-10-17")

    def test_multiple_historical_versions_are_withheld(self):
        self.library.records = [self.record(2024, "2025-03-15", "a"), self.record(2024, "2025-04-15", "b")]
        output = HostAnnouncementContext(self.core, fixture()).list_financial_reports()
        self.assertEqual(output["items"], [])
        self.assertEqual(output["excluded"]["version_ambiguous"], 2)

    def test_missing_identity_or_date_blocks_external_context(self):
        analysis = fixture()
        analysis["event"]["reported_identity"]["announcement_date"] = None
        self.assertEqual(HostAnnouncementContext(self.core, analysis).list_financial_reports()["status"], "unavailable")

    def test_publication_timestamp_uses_china_date_and_rejects_unknown_timezone(self):
        self.assertEqual(publication_date({"published_at": "2025-05-15T16:00:00Z"}), date(2025, 5, 16))
        self.assertIsNone(publication_date({"published_at": "2025-05-15T16:00:00"}))

    def test_unlisted_record_cannot_be_opened(self):
        context = HostAnnouncementContext(self.core, fixture())
        with self.assertRaises(Exception):
            context.load_financial_report("../../private")
        with self.assertRaises(Exception):
            context.load_related_announcement("https://arbitrary.example")

    def test_financial_conflict_is_withheld_even_if_quality_says_cross_checked(self):
        record = self.record(2024, "2025-03-15", "a")
        record["relative_path"] = "reports/300750/test.pdf"
        self.library.records = [record]
        self.library.path_for = lambda _: self.root / "test.pdf"
        self.library.verified_path = lambda *_: self.root / "test.pdf"
        source = {"page_start": 2, "row_bbox": [1, 2, 3, 4]}
        shared = {"document_id": "doc", "period": "2024", "unit": "CNY", "statement_scope": "consolidated", "sources": [source], "quality": "cross_checked"}
        parsed = {"documents": [{"document_id": "doc", "file_sha256": "a" * 64, "report_year": 2024}],
                  "facts": [{**shared, "fact_id": "assets", "metric_code": "total_assets", "value": "100"},
                            {**shared, "fact_id": "revenue", "metric_code": "revenue", "value": "80", "conflicting_values": [{"value": "90"}]}]}
        context = HostAnnouncementContext(self.core, fixture())
        report_id = context.list_financial_reports()["items"][0]["report_id"]
        with patch.object(context, "_parsed_report", return_value=parsed):
            output = context.load_financial_report(report_id)
        self.assertEqual([item["metric_code"] for item in output["financial_facts"]], ["total_assets"])
        self.assertIn("unverified_or_conflicting", output["withheld"][0]["reasons"])
        self.assertEqual(output["sources"][0]["published_date"], "2025-03-15")

    def test_local_related_report_needs_publication_date_not_just_printed_date(self):
        saved = fixture()
        saved["document"]["file_sha256"] = "b" * 64
        saved["title"] = "Lochpine进展公告"
        (self.root / ("a" * 32 + ".json")).write_text(json.dumps({"analysis": saved}, ensure_ascii=False), encoding="utf-8")
        context = HostAnnouncementContext(self.core, fixture())
        self.assertEqual(context.search_related_announcements("Lochpine")["items"], [])
        saved["event"]["published_date_cn"] = "2025-05-15"
        (self.root / ("a" * 32 + ".json")).write_text(json.dumps({"analysis": saved}, ensure_ascii=False), encoding="utf-8")
        self.assertEqual(len(context.search_related_announcements("Lochpine")["items"]), 1)


class QuestionApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=host.BASE_DIR)
        self.root = Path(self.temporary.name)
        paths = {name: self.root / name for name in ("TASK_DIR", "LOG_DIR", "RESULT_DIR", "UPLOAD_DIR")}
        for path in paths.values():
            path.mkdir()
        self.patches = patch.multiple(host, **paths, TASKS={}, _CANCELLED_TASKS=set())
        self.patches.start()
        self.client = TestClient(host.app)

    def tearDown(self):
        self.patches.stop()
        assert self.root.resolve().is_relative_to(host.BASE_DIR.resolve())
        self.temporary.cleanup()

    def test_question_creates_new_task_and_preserves_original(self):
        original = host.create_task("upload", file_name="fixture.pdf")
        value = fixture()
        source=host.UPLOAD_DIR/'fixture.pdf'
        source.write_bytes(b'%PDF-offline-question-fixture')
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        value['document']['file_sha256']=digest
        for evidence in value['evidence_blocks']:
            for item in evidence['sources']:item['file_sha256']=digest
        host.update_task(original["id"], status="completed", file_path=str(source), result={"analysis": value})
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            response = self.client.post(f"/api/tasks/{original['id']}/announcement-question", json={"question": "有哪些退出限制？", "as_of_mode": "announcement"})
        self.assertEqual(response.status_code, 202)
        child = host.TASKS[response.json()["id"]]
        self.assertEqual(child["status"], "completed")
        self.assertEqual(child["result"]["analysis"]["event_analysis"]["user_question"], "有哪些退出限制？")
        self.assertEqual(host.TASKS[original["id"]]["result"]["analysis"], value)

    def test_question_requires_unchanged_original_and_retry_keeps_question(self):
        source=host.UPLOAD_DIR/'fixture.pdf'
        source.write_bytes(b'%PDF-offline-question-fixture')
        digest=hashlib.sha256(source.read_bytes()).hexdigest()
        value=fixture();value['document']['file_sha256']=digest
        for evidence in value['evidence_blocks']:
            for item in evidence['sources']:item['file_sha256']=digest
        original=host.create_task('upload',file_name='fixture.pdf')
        host.update_task(original['id'],status='completed',file_path=str(source),result={'analysis':value})
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':''}):
            child=self.client.post(f"/api/tasks/{original['id']}/announcement-question",json={'question':'实际出资条件是什么？'})
            self.assertEqual(child.status_code,202)
            retry=self.client.post(f"/api/history/{child.json()['id']}/retry")
            self.assertEqual(retry.status_code,202)
        repeated=host.TASKS[retry.json()['id']]
        self.assertEqual(repeated['source'],'announcement_question')
        self.assertEqual(repeated['result']['analysis']['event_analysis']['user_question'],'实际出资条件是什么？')
        source.write_bytes(b'%PDF-changed')
        self.assertEqual(self.client.post(f"/api/tasks/{original['id']}/announcement-question",json={'question':'实际出资条件是什么？'}).status_code,409)

    def test_non_announcement_and_invalid_time_mode_rejected(self):
        task = host.create_task("upload")
        host.update_task(task["id"], status="completed", result={"analysis": {}})
        url = f"/api/tasks/{task['id']}/announcement-question"
        self.assertEqual(self.client.post(url, json={"question": "测试问题"}).status_code, 400)
        self.assertEqual(self.client.post(url, json={"question": "测试问题", "as_of_mode": "future"}).status_code, 422)

    def test_missing_or_tampered_evidence_is_not_served(self):
        task = host.create_task("upload")
        host.update_task(task["id"], status="completed", result={"analysis": fixture()})
        evidence_id = fixture()["evidence_blocks"][0]["evidence_id"]
        response = self.client.get(f"/api/tasks/{task['id']}/announcement-evidence/{evidence_id}")
        self.assertEqual(response.status_code, 409)

    def test_financial_evidence_opens_its_own_pdf_and_detects_change(self):
        path = self.root / "financial.pdf"
        body = b"%PDF-financial-test-only"
        path.write_bytes(body)
        digest = hashlib.sha256(body).hexdigest()
        analysis = fixture()
        analysis["evidence_blocks"].append({"evidence_id": "fin:test", "source_id": "report:test", "text": "总资产",
                                             "sources": [{"page": 2, "file_sha256": digest}]})
        analysis["agent_sources"] = [{"source_id": "report:test", "kind": "report_library", "relative_path": "reports/test.pdf", "file_sha256": digest}]
        task = host.create_task("upload")
        host.update_task(task["id"], status="completed", result={"analysis": analysis})
        library = SimpleNamespace(root=self.root, verified_path=lambda *_: path)
        url = f"/api/tasks/{task['id']}/announcement-evidence/fin:test"
        with patch.object(host, "get_report_library", return_value=library):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.content, body)
            path.write_bytes(b"%PDF-modified")
            self.assertEqual(self.client.get(url).status_code, 409)


if __name__ == "__main__":
    unittest.main()
