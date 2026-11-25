import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import app as core


class ReportGenerationTests(unittest.TestCase):
    analysis={'documents':[], 'metrics':[], 'risk_assessments':[], 'warnings':[]}

    def response(self, content='# 测试报告', reason='stop'):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),finish_reason=reason)])

    def test_missing_key_uses_template_without_network(self):
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY':''}),patch('openai.OpenAI') as client:
            _, generator=core.generate_report(self.analysis)
        self.assertEqual(generator,'deterministic_template');client.assert_not_called()

    def test_successful_model_response_is_marked_deepseek(self):
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-only-key','DEEPSEEK_MODEL':''}),patch('openai.OpenAI') as client:
            client.return_value.chat.completions.create.return_value=self.response()
            text,generator=core.generate_report(self.analysis)
        self.assertEqual(text,'# 测试报告');self.assertEqual(generator,'deepseek')
        kwargs=client.return_value.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs['model'],'deepseek-flash')
        self.assertEqual(kwargs['max_tokens'],4096)
        self.assertEqual(kwargs['extra_body']['thinking']['type'],'disabled')
        prompt=kwargs['messages'][0]['content']
        self.assertIn('正文中的指标、财务字段、表名及状态必须使用中文',prompt)
        self.assertIn('利润总额同比增长率',prompt)
        self.assertIn('当前解析器暂未覆盖',prompt)
        for section in ['2.1增长与盈利','2.2利润率','2.3现金流','2.4营运效率与回报','2.5资产质量','3.3未评估项']:
            self.assertIn(section,prompt)
        self.assertIn('正文不堆放完整文件SHA-256或大量事实编号',prompt)

    def test_empty_or_truncated_model_response_is_not_reported_as_ai_success(self):
        for content,reason in [('', 'stop'),('未完整输出','length')]:
            with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-only-key'}),patch('openai.OpenAI') as client:
                client.return_value.chat.completions.create.return_value=self.response(content,reason)
                _,generator=core.generate_report(self.analysis)
                self.assertEqual(generator,'deterministic_template_fallback')

    def test_network_failure_preserves_template(self):
        with patch.dict(os.environ,{'DEEPSEEK_API_KEY':'test-only-key'}),patch('openai.OpenAI') as client:
            client.return_value.chat.completions.create.side_effect=RuntimeError('test-only failure')
            text,generator=core.generate_report(self.analysis)
        self.assertEqual(generator,'deterministic_template_fallback')
        self.assertIn('财务分析结果',text)


if __name__=='__main__':unittest.main()
