# 跨报告风险复核（v0.15）

`financial_engine.evaluate_context_risks(context)` 读取 `analyze_disclosure_context(...)` 的汇总结果。统一入口也会自动填充顶层 `risk_review`。这是成员 2 的确定性筛查代码，不调用语言模型，不给出造假判断、信用评级或投资建议。顶层文件覆盖状态与 `risk_review.status` 独立，文件获取完整也可能因口径冲突而无法完成某条规则。`status=complete` 只表示已请求的两条跨报告规则有足够输入，不能替代财报覆盖率或实质性判断。

设计参考 [CFA Institute 关于财报质量的分析框架](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/evaluating-quality-financial-reports)：结合业务、跨期、同行、会计政策及现金流证据。[FinanceToolkit](https://github.com/JerBouma/FinanceToolkit) 的透明公式和指标分层方式仅供结构设计参考；本模块未复制其源码、计算结果或美股数据口径。以下触发条件是**项目自定义的待复核筛查条件**，不是 CFA、证监会或财政部规定的阈值。

## 调用

```python
from financial_engine import analyze_disclosure_context, evaluate_context_risks

context = analyze_disclosure_context(
    r"D:\ai_finance\宁德时代.pdf", stock_code="300750", lookback_years=1,
    peer_codes=["300014", "002074"], peer_group="动力及储能电池相关企业",
    selection_note="人工选择，业务结构未调整",
    cache_dir=r"D:\ai_finance\skill\report_cache", fetch_online=False,
)
review = context["risk_review"]  # 统一入口已自动计算
# 对旧版或外部构造的同结构结果，也可单独调用 evaluate_context_risks(context)。
```

## 两条规则

| 规则 ID | 触发条件 | 必须满足的证据门槛 |
| --- | --- | --- |
| `persistent_receivables_aging_pressure` | 前后两份独立年报各自的 `receivables_aging_pressure` 均为 `triggered`。 | 必须取得相邻年份原年报；前一年原报告与后一年比较列的应收账款原值、营收和三个一年以上账龄桶都须 `matched`。任何差异或缺比较列均为 `insufficient_data`。 |
| `relative_gross_margin_pressure` | 毛利率较前一年下降，且低于至少两家指定同行的当年毛利率中位数。 | 年度毛利率变化须为 `calculated` 且 `basis_check=matched_comparatives`；同行比较必须同年、同公式、同单位，目标文件相同且至少两家有效同行。 |

两条规则都只有方向条件，没有经过整车、电池、工程建设等子行业样本的误报率或经济重要性校准，所以输出 `materiality_assessed=false`、`severity=watch`。同行业务结构、产品组合和并购影响没有调整。

## 与数据质量分开

`risk_review.data_quality.comparative_differences` 列出上年原报告与本年报告中上年比较列不同的事实 ID。`review_required` 只表示**比较口径需人工核查**，可能是重述、重分类或提取错误，不等同公司经营风险。无比较列可核时为 `insufficient_data`。跨报告风险规则遇到相关比较数不同会停止判断；不能将两个单份年报信号直接拼成“连续风险”。

每条 `assessment` 返回 `triggered`、`not_triggered`、`insufficient_data` 或 `not_evaluated`，以及 `conditions`、`missing_or_invalid`、`evidence_fact_ids`、`evidence_metric_ids`、`evidence_document_ids`。只把 `triggered` 放入 `findings`。事实和指标 ID 分别在 `history.analysis`、`peers.reports[代码]`、`current` 中查找，再从 `sources` 取得 PDF 页码与行坐标。未请求往期或同行时，依赖它们的规则返回 `not_evaluated`；请求但文件缺失时返回 `insufficient_data`。半年报没有年报跨报告判断。

v0.14 的连续账龄条件 `observed` 还提供每一年的 `growth_gap_percentage_points`、`over_1y_share_change_percentage_points`，两者都是保留十进制字符串的百分点差值。若单年规则状态与其两项条件不一致，跨报告规则返回 `insufficient_data`，而不是相信外部传入的状态。没有按行业样本校准的经济重要性阈值，方向性触发不能被解释为严重风险。

## 实测边界

在宁德时代 2025 年报、2024 年报及两家指定同行的离线样本上，v0.15 对可核对的 22 项上年比较字段均返回 `matched`，`data_quality.status=no_difference_detected`。此前 v0.12 的 5 项应收账款账龄差异是本项目把母公司“其他应收款”误读为合并“应收账款”所致，核查见 [SOURCE_AUDIT_CATL_2024.md](SOURCE_AUDIT_CATL_2024.md)；旧结果不得用于公司重述判断。修正后，连续应收账龄规则为 `triggered`，表示两份独立年报的方向性筛查信号均需复核，**不表示坏账、造假或经营风险已经被证实**。毛利率规则为 `not_triggered`，因为当年毛利率既未同比下降，也未低于指定同行中位数。具体幅度及原文来源见 [RISK_SIGNAL_AUDIT.md](RISK_SIGNAL_AUDIT.md)，两家同行字段的修复见 [PEER_EXTRACTION_AUDIT.md](PEER_EXTRACTION_AUDIT.md)；可复现汇总 JSON 位于上级目录 `context_battery_2025_v015.json`。这不是对企业风险水平的总体结论。

实现和测试见 `financial_engine/context_risk.py`、`tests/test_context_risk.py`。如需估计风险概率、严重程度或统一阈值，先收集足够的按行业分组、经过独立核验的正负样本，保留原文证据并避免使用事后修订数据做历史时点预测。
