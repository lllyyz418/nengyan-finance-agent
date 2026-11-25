# v0.13 交付说明

## 接口与结果

- `analyze_disclosure_context(...)` 和 `evaluate_context_risks(...)` 的调用方式保持不变；统一入口顶层 `schema_version` 从 `0.12` 更新为 `0.13`。
- 年报应收账款账龄来源限定在合并财务报表附注的“应收账款”项目。若标题在前一页、表格在后一页，`sources[].heading_page` 记录标题页，`page_start` 和 `row_bbox` 记录金额所在页。其余来源字段保持原有结构。
- 宁德时代 2024 年报原版第 167 页与 2025 年报第 165 页上年比较列的 5 项账龄数据一致；v0.12 的差异来自误取第 223 页母公司其他应收款，详见 [SOURCE_AUDIT_CATL_2024.md](SOURCE_AUDIT_CATL_2024.md)。
- 重新计算后的样本中，22 项可核对比较字段均 `matched`；连续应收账龄压力筛查为 `triggered`，相对毛利率压力筛查为 `not_triggered`。`triggered` 仅表示需要复核，不是造假或违约认定。样本 JSON 位于包外的 `D:\ai_finance\skill\context_battery_2025_v013.json`。

## 成员 1 接入提醒

旧版 v0.12 的宁德时代跨期 JSON 含误提取结果，应由 v0.13 重新生成。前端与 Agent 展示 `risk_review.data_quality`、每条规则的 `status`、`conditions`、`limitations`，并通过 `evidence_fact_ids`、`evidence_document_ids` 查到对应报告和 PDF 页码。不要把筛查结果写成已证实的经营风险。

安装与运行步骤见 [QUICK_START.md](QUICK_START.md)，统一入口与 JSON 字段见 [DATA_MINING.md](DATA_MINING.md)、[INTEGRATION.md](INTEGRATION.md)。
