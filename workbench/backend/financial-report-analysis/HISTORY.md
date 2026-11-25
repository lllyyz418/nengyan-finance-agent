# 往期年报检索与纵向比较（初稿）

本模块把现有的年报 PDF 提取、Decimal 公式及风险规则接到公开披露文件检索上。输入一份文本型年报，可检索同一 A 股公司往期年报，保存来源与文件哈希，生成逐年事实、指标和跨年差异。它没有实现同行自动选择、扫描件 OCR、公告事件识别或 Agent 编排。

## 成员 1 调用

```python
from financial_engine import analyze_company_history

result = analyze_company_history(
    r"D:\ai_finance\byd.pdf",
    stock_code="002594",  # 可省略；会根据封面公司名称查询，并核对显式代码
    lookback_years=2,
    cache_dir=r"D:\ai_finance\skill\report_cache",
)

retrieval = result["retrieval"]
history = result["history"]
print(retrieval["status"], history["report_years"])
print(history["fact_series"].get("revenue", []))
print([item for item in history["changes"] if item["metric_code"] == "gross_margin"])
```

安装 `requirements.txt` 中的 PDF 依赖。`disclosure.py` 的网络与缓存代码只使用 Python 标准库；`build_annual_history(analysis, stock_code)` 也不发起网络请求。线上获取失败时，输入报告仍会分析，`retrieval.status="partial"`，原因写入 `retrieval.warnings` 和顶层 `warnings`。若输入 PDF 无法解析，返回 `PDF_PARSE_FAILED`，不会伪造跨年数据。

PowerShell 命令行示例（完整结果写入 D 盘，终端会显示文件路径和已分析年份）：

```powershell
Set-Location 'D:\ai_finance\skill\financial-report-analysis'
python -B -m financial_engine.history_cli 'D:\ai_finance\byd.pdf' --stock-code 002594 --lookback-years 2 --cache-dir 'D:\ai_finance\skill\report_cache' --output 'D:\ai_finance\skill\history_result.json'
```

离线演示或网站暂时无法访问时：

```powershell
python -B -m financial_engine.history_cli 'D:\ai_finance\byd.pdf' --offline --stock-code 002594 --extra-pdf 'D:\ai_finance\skill\report_cache\002594_2024_1222881496.pdf' --output 'D:\ai_finance\skill\history_offline.json'
```

## 返回数据及口径

新接口保留原有 `documents`、`facts`、`metrics`、`unavailable_metrics`、`risk_assessments`、`findings`、`warnings`、`audit`，增加：

主分析结果的 `schema_version` 仍为 `0.6`，保持原接口兼容；新增的 `history.schema_version` 为 `0.1`。

- `retrieval.files`：上传文件和往期文件的本地路径、披露来源、公告编号、发布日期、SHA-256、缓存状态。每个下载的 PDF 旁边有同名 `.json` 清单。
- `retrieval.alternatives`：同一年较早披露的完整版本；默认选择最新正式披露版本，选择过程可复核。摘要、英文版和提示性公告不作为完整年报。
- `history.fact_series`：各年度正式报告的当期原始金额，`value` 为人民币元的十进制字符串。`fact_id` 可在顶层 `facts` 中查到原文页码、行坐标和单位。
- `history.series`：各年度正式报告的当期已计算指标，`metric_id` 指向顶层 `metrics`，再通过 `input_fact_ids` 回溯原文。
- `history.changes`：相邻年度同一指标的差异。百分比指标的差异单位为 `percentage_points`；金额等其他指标只有上期值大于零时才给出相对变动百分比。缺值、单位不同或年份不连续时保留 `status="unavailable"` 与 `reason_code`。
- `history.restatement_checks`：核对上一年度原报告的当期事实，与下一年度报告对同一年的比较列。若有不同，相关指标的跨年差异标记 `RESTATED_COMPARATIVE_CONFLICT`，并给出两份事实的 ID；不会悄悄替换上一年数值。

`retrieval.status="complete"` 只表示请求的往期文件已找到并解析，不代表所有指标都有值。展示前仍要检查 `unavailable_metrics`、`history.changes[*].status`、`history.warnings` 和 `facts[*].quality`。风险规则仍以各报告自身证据为基础；跨年比较本身不构成违规或投资结论。

## 来源与边界

在线检索使用[巨潮资讯网公开披露页面](https://www.cninfo.com.cn/new/commonUrl?url=disclosure%2Flist%2Fnotice)背后的查询接口，下载其公开 PDF。查询前会用财报封面公司名称匹配证券代码；用户提供的代码若不一致，就停止在线扩展。该网站接口没有稳定性承诺；网络失败、页面结构变化、公告缺失都可能使 `retrieval` 变为 `partial`。缓存文件会校验 PDF 文件头和 SHA-256，限制下载大小，并保留来源 URL 与公告版本。开发时已在比亚迪 2023–2025、宁德时代 2024–2025、中国能建 2024–2025 年报上跑通获取与纵向解析；这些是当前样本验证范围，尚非任意公司和年份的准确率证明。

解析器仍以有文本层的年报为对象。遇到扫描件、半年报、临时公告或无法判定单位的表格，会保留失败或缺失状态。比亚迪 2023 年报的合并报表单位来自该报告第 153 页的明确声明，相关报表行的来源记录含 `unit_source_page`，没有根据数字量级猜测单位。

离线模式下，显式传入的证券代码只用于归组，不进行联网身份核对；调用方应确认所有本地年报属于同一家公司。
