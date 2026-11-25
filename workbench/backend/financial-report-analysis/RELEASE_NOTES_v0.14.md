# v0.14 交付说明

## 本版变化

- `analyze_disclosure_context(...)` 的参数和调用方式不变；顶层汇总 `schema_version` 由 `0.13` 改为 `0.14`。单份年报分析结构仍为 `0.6`。
- 年报规则 `cash_collection_pressure`、`inventory_margin_pressure`、`receivables_aging_pressure` 的 `conditions[].observed` 增加十进制字符串形式的增长差距或占比变化（单位：百分点）。`rule_version` 为 `1.1`。
- 跨报告规则 `persistent_receivables_aging_pressure` 的各年度条件也带有 `growth_gap_percentage_points`、`over_1y_share_change_percentage_points`；单年状态与条件相矛盾时返回 `insufficient_data`。其 `rule_version` 为 `1.2`。
- 风险判断仍是**方向性筛查**，没有训练得到的概率、行业重要性阈值或坏账判断。解释时必须同时显示差值、原文页码、缺失信息和 `materiality_assessed=false`。

## 已核验样本与后续解析

宁德时代 2024、2025 年报的应收账龄筛查触发；2025 年应收增速比营收高约 **1.5891 个百分点**，一年以上账龄占比上升约 **0.3822 个百分点**。具体原文与限制见 [RISK_SIGNAL_AUDIT.md](RISK_SIGNAL_AUDIT.md)。两个同行样本暴露的解析缺口：亿纬锂能第 9 页四个单季经营现金流值、国轩高科第 193 页存货原值及跌价准备。它们在原文中存在，但当前结构化结果缺失；相关规则保持 `insufficient_data`。

成员 1 接入时不需要更换函数名。展示跨报告风险条件时读取 `risk_review.assessments[].conditions[].observed`，证据按 `evidence_fact_ids` → `history.analysis.facts` → `sources` → `documents.file_sha256` 解析。旧版 JSON 和 ZIP 可保留作回归对照，不能与 v0.14 混用为同一次分析结论。完整调用见 [DATA_MINING.md](DATA_MINING.md) 和 [QUICK_START.md](QUICK_START.md)。
