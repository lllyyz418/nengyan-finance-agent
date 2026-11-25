# 财务指标口径与异常规则

本文件约定 `financial_engine` v0.6 的计算口径。源文件仍是 PDF；每个计算值通过 `input_fact_ids` 追溯到原文页码、原值、单位和行坐标。`metrics` 只放可计算的结果；`unavailable_metrics` 放明确未计算的指标及原因。金额统一为人民币元的十进制字符串。

本页下方的 `D=完整报告年度天数` 和相关平均余额公式针对**年报**。半年报的同期间比较、181/182 天周转计算及不年化规则见 [SEMIANNUAL.md](SEMIANNUAL.md)，不能直接把半年报数据代入年度公式。

## 依据与边界

- [CFA Institute 财务比率表](https://www.cfainstitute.org/sites/default/files/-/media/documents/support/programs/cfa/cfa_program_level_ii_financial_ratio_list.pdf)：毛利率、净利率、ROA、ROE、存货周转和应收周转的基础定义。该表明确提示，实践中同名比率可能有不同定义。
- [证监会非经常性损益规则（2023 年修订）](https://www.csrc.gov.cn/csrc/c101954/c7451398/content.shtml)：本项目使用财报披露的扣非归母净利润，不在其上机械重复扣减政府补助。
- [IFRS Foundation 对自由现金流的说明](https://www.ifrs.org/content/dam/ifrs/resources-for/investors/the-essentials/the-essentials-feb-2019.pdf)：自由现金流没有统一的 IFRS 定义；经营现金流减资本开支是常见简化口径，租赁会影响可比性。
- [财政部对购建长期资产现金支出项目的解释](https://www.mof.gov.cn/bd/mhwzzxly/hjs/202310/t20231026_3913199.htm)：本项目用现金流量表该行作简化资本开支现金额，不将其等同于所有资本开支。
- [FinanceToolkit 利润率与现金利润比实现](https://github.com/JerBouma/FinanceToolkit/blob/main/financetoolkit/ratios/profitability_model.py)：作为公开代码的交叉核对材料，不直接复用其数据来源或浮点数计算。

## 公式

`R` = 合并营业收入，`C` = 合并营业成本，`NP` = 合并净利润，`ANP` = 归母净利润，`AANP` = 扣非归母净利润，`CFO` = 合并经营活动现金流量净额，`CAPEX_CASH` = 购建固定资产、无形资产和其他长期资产支付的现金。`D` 是当前完整报告年度的实际天数，闰年为 366，否则为 365。期初余额取上年末，期末余额取本年末。

| `metric_code` | 公式；百分比结果乘 100 | 性质及限制 |
| --- | --- | --- |
| `revenue_yoy` 等 `*_yoy` | `(本年值－上年值)／上年值` | 仅在上年同期间基数为正时给出百分比。季度 `*_q4_qoq` 使用独立单季 Q4、Q3。 |
| `gross_margin` | `(R－C)／R` | CFA 毛利率；负毛利率是有效结果。 |
| `consolidated_net_margin` | `NP／R` | CFA 净利率，使用合并净利润。 |
| `adjusted_attributable_net_margin` | `AANP／R` | 项目定义的“扣非归母净利率”；不称主营净利率。 |
| `cash_to_consolidated_profit` | `CFO／NP` | 现金利润比；`NP≤0` 时不给倍数，负 CFO 可如实输出。 |
| `simplified_free_cash_flow` | `CFO－CAPEX_CASH` | 单位元。简化 FCF，不是 FCFF 或 FCFE。 |
| `inventory_days` | `D×(期初存货净额＋期末存货净额)／(2×C)` | CFA 总体存货周转天数；没有分品类成本流转额时不计算原材料等分品类天数。 |
| `receivables_days_approx` | `D×(期初应收账款净额＋期末应收账款净额)／(2×R)` | CFA DSO 的项目近似值。营业收入未必全为赊销，净额不能与附注账面余额混算。 |
| `return_on_assets` | `NP／[(期初总资产＋期末总资产)／2]` | CFA ROA，合并口径。 |
| `return_on_attributable_equity` | `ANP／[(期初归母权益＋期末归母权益)／2]` | 简单平均归母 ROE，不是监管披露的加权平均净资产收益率。 |
| `fixed_assets_cip_to_assets` | `(固定资产＋在建工程)／总资产` | 项目自定义的重资产扩产比重。 |
| `r_and_d_expense_to_revenue` | `合并利润表研发费用／R` | 研发费用率，不是研发投入占比或研发资本化率。 |
| `inventory_impairment_to_gross` | `存货跌价准备／存货账面余额` | 项目自定义的准备覆盖比例，不能按固定百分比判断舞弊。 |
| `receivables_over_1y_to_gross` | `(1～2 年＋2～3 年＋3 年以上账龄余额)／应收账款账面余额` | 项目自定义的账龄结构指标。 |
| `nonrecurring_to_attributable_profit` | `非经常性损益归母净额／ANP` | 仅正归母利润时解释为占比；默认 30% 只是项目可调筛查阈值，不是监管红线。 |

以上涉及平均余额的公式遵循 [CFA 财务比率表](https://www.cfainstitute.org/sites/default/files/-/media/documents/support/programs/cfa/cfa_program_level_ii_financial_ratio_list.pdf)。简化 FCF 的现金支出项目遵循[财政部解释](https://www.mof.gov.cn/bd/mhwzzxly/hjs/202310/t20231026_3913199.htm)。

## 精度及不可计算情况

1. 原始金额由字符串直接构造 `Decimal`；浮点输入、`NaN`、`Infinity`、百分数字符串和未知单位都不作为金额。输入精度保留到原文披露位数，单位换算、金额加减与勾稽在足够高的局部十进制精度下执行。
2. 比率在至少 50 位有效数字的十进制上下文中运算；非终止小数仍受有限精度约束。JSON 用十进制字符串保存结果，前端可按其展示规则舍入，但不得先转为二进制浮点再声称金额精确。
3. 缺失字段为 `MISSING_INPUT`；同一字段披露冲突为 `CONFLICTING_INPUT`；分母为零为 `DIVISION_BY_ZERO`；需要正基数但为负为 `NONPOSITIVE_INPUT`；余额或现金支出项为负为 `NEGATIVE_BALANCE`；明细超过总额为 `INCONSISTENT_COMPONENTS`。这些情况写入 `unavailable_metrics`，其 `value` 为 `null`，不会用 0 代替。
4. 利润、经营现金流、自由现金流和毛利率本身可以为负。负值单独不代表违规。涉及同比的上期值须大于零；`CFO／NP` 的 `NP` 须大于零。
5. 先核对公司、期间、单位、合并范围和重述标记。解析或披露冲突的事实不得进入公式。指标是分析结果，风险规则是待复核信号；二者都不是审计意见。
