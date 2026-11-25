# 财务风险筛查规则 v1.0

这些规则将多个已经核对的公开披露事实合并为**方向性需复核信号**。`risk_assessments` 对每份报告输出每条规则的 `triggered`、`not_triggered`、`insufficient_data` 或 `not_evaluated` 状态。只有 `triggered` 同时进入 `findings`，其严重程度为 `watch`；`materiality_assessed=false` 提醒调用方：目前尚未按子行业校准差异幅度，轻微变化也可能触发。它不是审计结论、造假判断、信用评级或投资建议。所有比较使用 `Decimal`，保存 `evidence_fact_ids`、`evidence_metric_ids`、每项条件的观察值与未满足条件。

规则设计参考 [CFA Institute 财务报告质量分析](https://www.cfainstitute.org/insights/professional-learning/refresher-readings/2026/evaluating-quality-financial-reports)：结合业务背景、跨期项目、财务比率和净利润与经营现金流差异来评估，不能由单一数值推断报告质量。存货准备复核还应遵循[财政部《企业会计准则第1号——存货》](https://kjs.mof.gov.cn/zt/kjzzss/kuaijizhunzeshishi/200806/t20080618_46216.htm)关于成本与可变现净值孰低的规定。以下组合条件是**本项目的筛查定义**，没有声称是 CFA、财政部或监管部门规定的预警阈值。

| 规则 ID | 必须同时满足的条件 | 数据条件及限制 |
| --- | --- | --- |
| `cash_collection_pressure` | 本年**合并净利润**为正；四个独立季度中至少连续 2 季经营现金流为负；应收账款**账面余额**同比增速高于合并营业收入增速。 | 可把 `minimum_negative_cash_quarters` 设为整数 2–4，默认 2；四季现金流合计若与全年数明显不符，状态为 `insufficient_data`。不含应收票据、合同资产。 |
| `inventory_margin_pressure` | 存货账面余额同比增速高于营收；存货跌价准备／存货账面余额上升；合并毛利率下降。 | 需要连续两年的附注存货原值、准备及利润表成本；仅提示核查价格、产品结构和可变现净值。 |
| `receivables_aging_pressure` | 应收账款账面余额同比增速高于营收；一年以上账龄金额占应收账款账面余额的比例上升。 | 账龄不能直接当作坏账损失，需结合信用损失准备、客户与合同条款。 |
| `capital_expansion_cash_pressure` | 简化自由现金流为负；`(固定资产＋在建工程)／总资产` 高于上年末。 | 扩产可以是正常投资。简化自由现金流不含租赁及非现金购建，不能直接当成偿债能力判定。 |

两项会计政策与估计披露复核已接通（`annual-policy-1`，年报解析器 v0.9）：

- `r_and_d_capitalization_policy`：从具有明确本期、上期列及金额单位的研发投入表提取总投入和当期资本化金额；以 Decimal 计算两期资本化比例。比例上升输出 `review_required`，仅观察方向，不声称已校准经济重要性；比例未上升输出 `reviewed`。显式零有效，空白、横线和“不适用”不按零处理；不由总投入减费用化金额倒推，不以利润表研发费用或开发支出期末余额替代。
- `depreciation_policy_change`：提取固定资产各类别折旧方法、年限、残值率、年折旧率，并定位“重要会计估计变更”小节的明确披露。有同公司上期年报时按相同类别比较；不同类别不强行映射。参数不同或定位到涉及折旧的实际变更说明输出 `review_required`；披露矛盾、原表未通过校验输出 `manual_review`。仅有本期表且明确勾选重要会计估计变更不适用时，可完成有限范围的本期披露复核，必须说明未独立核对上期表。没有变更段落不等于没有变更，常规“每年复核、如有变化则调整”不算实际变更。

这两项使用独立的 `policy_observation`，不是已证实风险，不进入红色已触发规则或 findings 计数。状态为已完成复核、发现变化需复核、数据不足、需人工确认。金额事实仍在 facts 中；文字/表格证据保留在 document.policy_review.evidence，并由 evidence_index 提供原 PDF 页码与定位。历史补齐只在新版本中更新政策观察，不覆盖原任务或原报告正文；旧记录需要重新分析才能使用新提取能力。中期报告不直接套用这套年度规则。

旧记录的 `not_evaluated` 继续如实展示，不在读取时假装完成评估。对已实现规则，缺少输入、数值冲突、非法余额或现金流季度勾稽失败则返回 `insufficient_data`，并列出 `missing_or_invalid`。`not_triggered` 可在 `unmet_condition_codes` 中查看具体未满足条件。

v0.14 在应收、存货和现金流相关条件的 `observed` 中补充 `growth_gap_percentage_points` 或 `share_change_percentage_points`，用 `Decimal` 计算。它们用于展示方向差距，**不构成已校准的显著性或经济重要性阈值**。应收账龄不是逾期天数；样本核验见 [RISK_SIGNAL_AUDIT.md](RISK_SIGNAL_AUDIT.md)。

当前样本为比亚迪、宁德时代和中国能建三份 2025 年报，行业跨度大，不能用这三个样本估计统一的异常分位数。后续若需要“显著高于”等具有经济重要性的定量阈值，应按整车、电池、工程建设分别收集跨年样本，验证误报和漏报后再设定。

## 跨报告规则

v0.15 汇总入口返回 `risk_review`，对连续应收账龄压力和同比加同行毛利率压力进行复核。前一年原报告与后一年比较列有差异时会停止相关判断；详见 [CONTEXT_RISK.md](CONTEXT_RISK.md)。
