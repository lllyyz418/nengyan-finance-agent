import copy
import shutil
import unittest
import uuid
from unittest.mock import patch
from fastapi.testclient import TestClient
import app as core
from report_summary import GROUPS,build_summary,number


def fixture():
    return {'documents':[{'document_id':'example','security_code':'300750','company_name':'示例公司',
            'report_year':2025,'report_type':'annual','currency':'CNY','pdf_pages':232,'sections_found':['annual_summary','aging_note']}],
        'metrics':[{'metric_code':code,'period':'2025Q4' if 'q4' in code else '2025','status':'calculated',
                    'value':'17.040646606968221328589615707','unit':'percent'} for _,codes in GROUPS for code in codes],
        'risk_assessments':[], 'unavailable_metrics':[], 'warnings':[]}


def summary(analysis):
    return build_summary(analysis,core.METRIC_LABELS,core.RISK_LABELS,core.RISK_STATUS_LABELS)


class ReportSummaryTests(unittest.TestCase):
    def test_fixed_old_sections_all_metrics_and_no_mutation(self):
        data=fixture();original=copy.deepcopy(data);text=summary(data)
        self.assertEqual(data,original)
        for heading in ['一、数据范围','2.1 增长与盈利','2.2 利润率','2.3 现金流','2.4 营运效率与回报','2.5 资产质量','三、风险复核','3.1 已触发','3.2 未触发','3.3 未评估','四、证据限制']:
            self.assertIn(heading,text)
        self.assertEqual(text.count('| 17.04 | % |'),len(data['metrics']),'no arbitrary 16-metric truncation')
        self.assertIn('2025Q4 营业收入环比',text)
        self.assertIn('一年以上应收/应收原值',text)
        self.assertNotIn('annual_summary',text)

    def test_missing_conflicting_zero_negative_and_units_are_not_fabricated(self):
        data=fixture()
        data['metrics']=[{'metric_code':'revenue_yoy','period':'2025','status':'calculated','value':'0','unit':'percent'},
            {'metric_code':'simplified_free_cash_flow','period':'2025','status':'calculated','value':'-97672309000','unit':'CNY'}]
        data['unavailable_metrics']=[{'metric_code':'total_profit_yoy','period':'2025','value':None,'status':'unavailable','reason_code':'MISSING_INPUT','detail':'2025:total_profit, 2024:total_profit'}]
        text=summary(data)
        self.assertIn('| 营业收入同比 | 0.00 | % |',text)
        self.assertIn('-97,672,309,000.00 | 元',text)
        self.assertIn('不可计算：缺少计算所需数据',text)
        self.assertIn('2025:利润总额',text)
        self.assertNotIn('total_profit',text)
        self.assertEqual(number('NaN'),'未提供')
        self.assertEqual(number('0'),'0.00')

    def test_triggered_observations_and_not_evaluated_limits_are_specific(self):
        data=fixture()
        data['risk_assessments']=[{'rule_id':'receivables_aging_pressure','status':'triggered','materiality_assessed':False,
            'conditions':[{'condition_code':'over_1y_share_increased','passed':True,'observed':{'current_percent':'3.6743','previous_percent':'3.2921','share_change_percentage_points':'0.3822'}}],
            'limitations':'账龄不等于逾期或预期信用损失。'},
            {'rule_id':'r_and_d_capitalization_policy','status':'not_evaluated','reason_code':'PARSER_NOT_COVERED','summary':'研发资本化未完整提取'},
            {'rule_id':'cash_collection_pressure','status':'insufficient_data','summary':'缺少季度现金流'}]
        data['warnings']=[{'message':'PDF 第165页 aging 表内勾稽相差最多1千元'}]
        text=summary(data)
        for expected in ['本期 3.67%，上期 3.29%','变化 0.38 个百分点','尚未评估变化的重要性','账龄不等于逾期','当前解析器暂未覆盖','3.4 数据不足','缺少季度现金流','第165页 应收账款账龄明细']:
            self.assertIn(expected,text)
        self.assertNotIn('PARSER_NOT_COVERED',text)

    def test_extra_metrics_are_not_silently_discarded_and_no_fake_company(self):
        self.assertEqual(summary({'documents':[]}),'')
        data=fixture();data['metrics'].append({'metric_code':'自定义指标','period':'2024','value':'42','unit':'倍'})
        text=summary(data)
        self.assertIn('2.6 其他指标',text);self.assertIn('自定义指标 | 2024 | 42.00 倍',text)
        self.assertNotIn('宁德时代',text)

    def test_fallback_has_same_complete_summary_tables(self):
        data=fixture()
        with patch.dict('os.environ',{'DEEPSEEK_API_KEY':''}),patch('openai.OpenAI') as model:
            text,generator=core.generate_report(data)
        self.assertIn('2.5 资产质量',text)
        self.assertEqual(generator,'deterministic_template');model.assert_not_called()


class SavedReportSummaryTests(unittest.TestCase):
    def setUp(self):
        self.root=core.BASE_DIR/'.history-tests';self.path=self.root/uuid.uuid4().hex;self.path.mkdir(parents=True)
        paths={key:self.path/key for key in ('TASK_DIR','RESULT_DIR','UPLOAD_DIR','CACHE_DIR','LOG_DIR')}
        for path in paths.values():path.mkdir()
        self.paths=patch.multiple(core,**paths,TASKS={});self.paths.start()
        self.client=TestClient(core.app)

    def tearDown(self):
        self.paths.stop()
        assert self.path.resolve().is_relative_to(self.root.resolve())
        shutil.rmtree(self.path)

    def test_viewing_existing_report_adds_summary_without_rewriting_or_model_calls(self):
        task=core.create_task('upload','example.pdf')
        saved={'analysis':fixture(),'payload':{},'report_markdown':'# 原始 DeepSeek 文字不得覆盖','report_generator':'deepseek','evidence':{}}
        core.update_task(task['id'],status='completed',result=copy.deepcopy(saved))
        path=core.TASK_DIR/f"{task['id']}.json";before=path.read_bytes()
        with patch.object(core,'generate_report',side_effect=AssertionError('view must not call model')):
            result=self.client.get(f"/api/tasks/{task['id']}/result").json()
        self.assertEqual(result['report_markdown'],saved['report_markdown'])
        self.assertIn('2.5 资产质量',result['report_summary_markdown'])
        self.assertEqual(result['analysis'],saved['analysis'])
        self.assertEqual(task['result'],saved);self.assertEqual(path.read_bytes(),before)
