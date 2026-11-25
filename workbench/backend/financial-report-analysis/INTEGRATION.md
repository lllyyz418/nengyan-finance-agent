# 成员 1 接口说明

需要一次取得当前财报、年度往期和指定同行的汇总结果时，调用新增的 `analyze_disclosure_context(...)`，见 [DATA_MINING.md](DATA_MINING.md)。它支持上传 PDF 或明确的公司代码与财年，默认离线；联网请显式设置 `fetch_online=True` 并提供缓存目录。返回汇总格式 `0.15`，既有年度/H1接口仍可单独调用。顶层 `risk_review` 是跨报告风险复核；字段及边界见 [CONTEXT_RISK.md](CONTEXT_RISK.md)。

需要“上传一份年报后获取往期报告并做纵向比较”时，调用 `analyze_company_history`，详见 [HISTORY.md](HISTORY.md)。原有 `analyze_reports` 接口与本页以下说明保持兼容。

需要同年度人工指定同行的横向比较时，调用 `analyze_peer_group`；如果成员 1 已取得各公司分析结果，可调用不联网的 `compare_peer_group`。参数、证据链及中位数门槛见 [PEERS.md](PEERS.md)。

需要公告事件提取或扫描公告 OCR 时，调用 `analyze_announcement`；巨潮资讯公告目录发现与下载见 [ANNOUNCEMENTS.md](ANNOUNCEMENTS.md)。公告结果独立于本页的年报 `facts` 和 `metrics`，不能拿事件金额直接计算财务比率。

选定扫描年报摘要页的 OCR 候选提取调用 `analyze_scanned_financial_pages`；参数、返回状态和三家公司模拟扫描页检验见 [SCANNED_FINANCIAL.md](SCANNED_FINANCIAL.md)。它不自动产出年报 `facts` 或风险结论。

v0.17 新增扫描候选确认入口：`create_scan_review_template` 生成全待确认模板，`prepare_reviewed_scan` 验证真实人员的确认记录，`analyze_reviewed_scan` 将通过核验的字段送入已有公式和风险规则。调用方须保存原候选、核对原文件并鉴别人类确认人员；调用细节见 [SCAN_REVIEW.md](SCAN_REVIEW.md)。该入口返回 `0.17` 包装结构，财务结果在 `analysis`，复核决定与原值/改值在 `review`。原汇总入口的 `0.15` 结构和其余既有入口保持原格式。

半年报使用 `analyze_semiannual_reports`；与年度口径、不可计算原因及验收样本的区别见 [SEMIANNUAL.md](SEMIANNUAL.md)。单份上传文件可先调用 `analyze_uploaded_document(path)` 自动路由，结果在 `analysis` 字段；历史和同行仍需调用对应的专用函数。

本目录提供年报与半年报 PDF 结构化提取、跨章节核对和可复核的财务计算。年报适配器覆盖有文本层的年度报告前部“主要会计数据和财务指标”“分季度主要财务数据”“非经常性损益”表，合并资产负债表、合并利润表、合并现金流量表中的选定字段，以及应收账款账龄、存货分类和“其他收益”政府补助的选定附注行。应收账款账龄支持标题与表格相邻跨页，并限定合并报表附注范围；核查示例见 [SOURCE_AUDIT_CATL_2024.md](SOURCE_AUDIT_CATL_2024.md)。分裂在相邻单元格的季度行标签，以及同页多列表头的存货分类合计，已在两份同行样本上补充校验；见 [PEER_EXTRACTION_AUDIT.md](PEER_EXTRACTION_AUDIT.md)。半年报适配器覆盖摘要表和选定合并报表行，尚未系统解析附注。扫描年度摘要页现有独立 OCR 候选接口，其他扫描财务表格、单独季度报告和其余财务附注仍未覆盖；扫描公告由独立 OCR 路径提取候选事件。

## 调用

运行环境建议 Python 3.10 或更新版本。PDF 入口安装 `requirements.txt`；如果成员 1 已将 PDF 解析为结构化表格，可直接调用无第三方依赖的核心函数。

```python
from financial_engine import analyze_reports, analyze_parsed_documents

result = analyze_reports([r"D:\ai_finance\byd.pdf", r"D:\ai_finance\宁德时代.pdf"])
# result 是可 JSON 序列化的 dict。成员 1 负责 API、上传文件和日志持久化。
```

命令行检查：

```powershell
python -B -m financial_engine D:\ai_finance\byd.pdf
```

## 返回值

- `documents`：公司、报告年度、原文件名、PDF 页数、SHA-256 和解析器版本。
- `facts`：标准化为人民币“元”的十进制字符串；保留原始值、原始单位、期间类型、起止日期、合并口径、原始字段名和证据页/行坐标。每个 `source` 各自保留原值及单位；跨章节相同数值标记为 `cross_checked`，相异则标记为 `conflict` 并禁止用于计算。2024 年比较列若注明“经重述”，另有 `comparative_restated` 标记。
- `metrics`：同比、第四季度相对第三季度的环比、盈利与现金质量、周转天数、资产回报及部分附注结构指标。每项有 `status="calculated"`、十进制字符串 `value`、单位、公式及输入 `fact_id`。完整口径见 [FORMULAS.md](FORMULAS.md)。
- `unavailable_metrics`：因缺失、冲突、零分母、负基数或异常余额而未计算的指标。其 `value=null`，并给出 `reason_code`、`detail` 和可用的输入 `fact_id`。前端应显示“不可计算”及原因，不能显示成 0。
  扫描确认入口还可返回 `UNCONFIRMED_INPUT` 或 `REJECTED_INPUT`，确认后相互冲突的字段继续使用 `CONFLICTING_INPUT`；复核记录说明对应候选为什么未进入公式。
- `risk_assessments`：每份报告的规则状态、条件观察值、未满足条件、缺失原因和证据 ID。`triggered` 才另入 `findings`；`not_evaluated` 表示解析器未覆盖有关附注。规则口径见 [RISK_RULES.md](RISK_RULES.md)。
- `findings`：包含原有现金与利润背离、非经常性损益占比筛查，以及四类条件全部满足时的方向性 `watch` 信号。此类信号的 `materiality_assessed=false`，展示时应同时呈现观察值及限制，不给违规或信用风险定论。
- `warnings`：解析失败、数据冲突、单位错误或财务勾稽问题。单个文件失败不会阻断其他文件。
- `audit`：提取和计算事件。成员 1 应将其与 Agent 的模型、工具调用和文件访问日志一起持久化。

`nonrecurring_ratio_threshold` 可通过 `options` 设置，默认字符串 `"0.30"`；它只是筛查阈值。请传字符串、整数或 `Decimal`，不要传 `float`。`minimum_negative_cash_quarters` 默认整数 `2`，仅接受 2–4。所有金额计算使用 `Decimal`；输入原值若已是 `float`，核心会拒绝该金额。前一年或上一季度基数非正时不计算增长百分比。跨公司对比需要成员 1 明确展示期间与口径，不应直接拼接不同公司的指标。

报表表头必须确认本期、上期金额列的顺序；若顺序异常或无法确认，`warnings` 会给出 `STATEMENT_COLUMN_ORDER_INVALID` 或 `STATEMENT_COLUMN_ORDER_UNVERIFIED`，对应字段不进入 `facts`。`source.document_id` 是文件哈希前缀，完整 SHA-256 在 `documents.file_sha256` 中。

已结构化字段：`accounts_receivable`、`inventory`、`fixed_assets`、`construction_in_progress`、`operating_cost`、`r_and_d_expense`、`net_profit`、`capital_expenditure_cash_paid`。附注另外输出 `accounts_receivable_gross`、四个 `accounts_receivable_aging_*` 桶、`inventory_gross`、`inventory_impairment_allowance`，以及有明确行项目时的 `government_grant_other_income_*`。这些字段中的可用事实支持毛利率、简化自由现金流、周转天数及其他公式。`capital_expenditure_cash_paid` 是现金流量表“购建固定资产、无形资产和其他长期资产支付的现金”的原始项目，不能直接等同于全部资本开支。

应收账款账龄和存货分类表必须通过合计勾稽；最大相差 1 个披露单位时保留原值，并给出 `NOTE_ROUNDING_DIFFERENCE`，超过则不输出表内字段。存货附注的“账面价值”还会与合并资产负债表的存货金额交叉核对。政府补助不设统一合计：比亚迪披露“与日常活动相关的政府补助”，中国能建按与资产/收益相关拆分，宁德时代“其他收益”附注未单列补助金额。不得把其他收益总额、递延收益余额和非经常性损益中的补助金额互换。

## 示例样本的主要核验点

| 文件 | PDF 页 | 必查事项 |
| --- | ---: | --- |
| 比亚迪 2025 年报 | 11–12 | 年度营收、跨页季度净利润、非经常性损益；金额单位“元”。 |
| 宁德时代 2025 年报 | 11–12 | 换行字段名、季度表跨页；金额单位“千元”。 |
| 中国能建 2025 年报 | 13、15–16 | 主要会计数据、季度现金流、非经常性损益；金额单位“千元”。 |

## 人工核对基准

`benchmarks/annual_2025_gold.json` 按原 PDF 指定页记录了 135 个独立核对值，并用 SHA-256 固定三份样本版本。运行 `python -B -m benchmarks.sample_review` 一次解析即可生成事实核对、39 项精确有理数独立复算和风险筛查报告。该统计仅针对列入标准答案的字段，不能代表整份年报所有章节均已解析。

## 本阶段限制

本代码尚未覆盖质押、股权变动公告字段，也未系统检查会计政策变更及其余财务报表附注。年报、半年报来源锚点精确到表格行；公告与扫描摘要候选精确到文本行或 OCR 框。半年报已有独立期间映射，但只验证了三份文本型样本；扫描摘要接口仅在三份模拟扫描页做过定向测试。PDF 与 OCR 组件的版权和来源见 `THIRD_PARTY.md`。
