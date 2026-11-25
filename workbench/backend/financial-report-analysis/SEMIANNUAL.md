# 半年报入口与期间口径：成员 1 接入说明

半年报使用独立的 `financial_engine.analyze_semiannual_reports`，结果顶层与年报保持 `documents`、`facts`、`metrics`、`unavailable_metrics`、`risk_assessments`、`findings`、`warnings`、`audit` 结构。`report_type="semiannual"`，当前适配器只覆盖文本型 PDF 的主要会计数据表和选定合并资产负债表、利润表、现金流量表字段；其余附注和管理层讨论没有系统解析。年报的历史比较与同行中位数函数仍只接受完整年度数据。

```python
from financial_engine import analyze_semiannual_reports, analyze_uploaded_document

path = r"D:\ai_finance\skill\semiannual_cache\002594_2025_1224626653.pdf"
result = analyze_semiannual_reports([path])
single = analyze_uploaded_document(path)  # 自动路由，分析结果在 single["analysis"]
```

命令行：

```powershell
python -B -m financial_engine.semiannual_cli 'D:\ai_finance\skill\semiannual_cache\002594_2025_1224626653.pdf' --output 'D:\ai_finance\skill\semiannual_byd_2025_result.json'
```

## 期间与公式

| 字段类别 | 本期 | 比较期 | 说明 |
| --- | --- | --- | --- |
| 收入、成本、利润、研发费用、经营现金流、购建长期资产现金支出 | `2025H1`，2025-01-01 至 2025-06-30 | `2024H1`，2024-01-01 至 2024-06-30 | 半年累计数；同比仅同为 H1 的流量相除。 |
| 应收账款、存货、固定资产、在建工程、总资产、归母权益 | `2025-06-30` | `2024-12-31` | 时点余额；上年末不是上年同期 H1。 |

`*_yoy = (本期 H1 流量－上年 H1 流量) / 上年 H1 流量 × 100%`，仅上年 H1 基数大于 0 时计算。毛利率、净利率、扣非归母净利率、经营现金流/合并净利润、简化自由现金流、研发费用率与年报定义相同，但分子分母**都取 H1**。总体存货周转天数和近似应收周转天数使用上年末与本年 6 月末的平均余额，乘 H1 实际天数（平年 181 天，闰年 182 天）；它们不能直接同全年天数指标作数值比较。固定资产加在建工程占总资产比重取本年 6 月末余额。不自动年化 ROA/ROE，这两个年度指标在 `unavailable_metrics` 中注明 `ANNUAL_METRIC_NOT_APPLICABLE`。

原值与单位保存在每个事实的 `sources`，归一化 CNY 金额是 `Decimal` 转成的字符串。摘要与合并报表相同值可标为 `cross_checked`；冲突值标 `conflict`，不得进入指标。缺失、冲突、非正分母、负余额和零分母分别有不可计算原因。`risk_assessments` 对年度风险规则明确返回 `not_evaluated/PERIOD_NOT_ANNUAL`；H1 负经营现金流本身不据此判定违规。`assurance` 区分 `unaudited`、`limited_review`、`unknown`；审阅不等于审计。

## 样本和验收

人工标注基准在 `benchmarks/semiannual_2025_gold.json`，固定了三份 PDF 的 SHA-256、33 个金额与原文页码；测试还用独立有理数复算同比、毛利率和存货周转天数。

- [比亚迪 2025 年半年报](https://static.cninfo.com.cn/finalpage/2025-08-30/1224626653.PDF)：摘要 PDF 第 7 页，合并报表从第 63 页开始；摘要用元，报表用千元。
- [宁德时代 2025 年半年报](https://static.cninfo.com.cn/finalpage/2025-07-31/1224342874.PDF)：摘要 PDF 第 7 页，合并报表从第 63 页开始；部分字段名和金额跨行。
- [中国能建 2025 年半年报](https://static.cninfo.com.cn/finalpage/2025-08-29/1224610873.PDF)：摘要 PDF 第 10 页，合并报表从第 96 页开始；第 95 页为审阅报告，因此标 `limited_review`。

本阶段基准只覆盖标注字段；不代表完整报告、所有附注或其他公司版式的准确率。半年报的前期 H1 值目前取本期报告的比较列，还没有下载上一期半年报独立核验重述。对这些事项，前端应显示覆盖范围与来源，不应生成未经证据支持的结论。
