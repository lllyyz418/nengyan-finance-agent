# 成员 1 交接速览

## 拿走什么

复制整个 `financial-report-analysis` 文件夹。`SKILL.md` 是 Agent 的调用与解释规则；`financial_engine/` 是实际执行的 Python 代码。只复制函数名无法运行。详细字段和限制见 `INTEGRATION.md`。

新增的自动往期年报检索与纵向分析调用见 [HISTORY.md](HISTORY.md)。在线运行时另需指定一个可写缓存目录；离线演示时可同时交付缓存中的往期 PDF，或由成员 1 提供本地历史 PDF 路径。

新增的同行横向比较调用见 [PEERS.md](PEERS.md)。成员 1 需要传入同行证券代码、业务分组及选择理由；`compare_peer_group` 可直接复用已解析结果，无需再次请求网站。

公告事件候选、巨潮资讯检索及可选扫描页 OCR 见 [ANNOUNCEMENTS.md](ANNOUNCEMENTS.md)。公告入口 `analyze_announcement` 与年报入口 `analyze_reports` 分开调用。

选定扫描年报摘要页可调用 `analyze_scanned_financial_pages`，详情见 [SCANNED_FINANCIAL.md](SCANNED_FINANCIAL.md)。输出带页内坐标和核对状态，仍需人工复核，不自动进入 `analyze_reports` 的财务事实。

扫描金额获得真实人员确认后，用 `analyze_reviewed_scan` 接入原公式与风险规则；模板、JSON 字段和命令行用法见 [SCAN_REVIEW.md](SCAN_REVIEW.md)。此确认与分析模块只用标准库，重用已有 OCR 候选时无需再运行 OCR。

半年报入口为 `analyze_semiannual_reports`，期间规则和样本见 [SEMIANNUAL.md](SEMIANNUAL.md)。若成员 1 先只想处理一份上传文件，可调用 `analyze_uploaded_document(path)` 自动路由，专业结果在返回值的 `analysis` 中。

如需一次获取当前报告、年度历史和指定同行，可用 `analyze_disclosure_context`，调用和离线/联网示例见 [DATA_MINING.md](DATA_MINING.md)。新增的接口不改变 v0.10 已有函数的参数或结果格式。v0.15 汇总输出包含 `risk_review`；见 [CONTEXT_RISK.md](CONTEXT_RISK.md)。

## 安装与调用

建议 Python 3.10+。在本文件所在目录运行：

```powershell
$env:TEMP = 'D:\ai_finance\skill\_tmp'
$env:TMP = 'D:\ai_finance\skill\_tmp'
python -m pip install --target 'D:\ai_finance\skill\_runtime_deps' -r .\requirements.txt
$env:PYTHONPATH = 'D:\ai_finance\skill\_runtime_deps'
```

如需处理扫描公告或扫描年报摘要页，再用同一 `--target` 安装 `requirements-ocr.txt`。此安装目录与缓存均在 D 盘；成员 1 的实际部署环境也可以使用自己的 Python 虚拟环境。

在同一目录运行 Python，或把该目录加入成员 1 项目的 Python 模块路径：

```python
from financial_engine import analyze_reports

result = analyze_reports([
    r"D:\ai_finance\byd.pdf",
    r"D:\ai_finance\宁德时代.pdf",
    r"D:\ai_finance\中国能建.pdf",
])

# result 可直接交给 JSON API；金额以字符串表示，避免浮点误差。
print(len(result["facts"]), len(result["warnings"]))
```

成员 1 将用户上传的 PDF 路径传给 `analyze_reports`。返回值包含 `documents`、`facts`、`metrics`、`unavailable_metrics`、`risk_assessments`、`findings`、`warnings`、`audit`。向用户展示结论前检查 `warnings` 和每个事实的 `quality`；用 `facts[*].sources` 展示页码、原值、单位和行坐标。`metrics[*].value` 是十进制字符串；`unavailable_metrics[*].value` 是 `null`，要显示具体原因。公式口径见 [FORMULAS.md](FORMULAS.md)，风险规则见 [RISK_RULES.md](RISK_RULES.md)。`findings` 只是需要复核的信号，不是违规或投资结论。成员 1 负责 API、文件上传、Agent 编排和日志保存。

`parse_pdf(path)` 可单独返回一份 PDF 的中间解析结果；`analyze_parsed_documents(documents)` 接受这类结果的列表，供已有解析流程对接。首次集成建议使用上面的 `analyze_reports` 入口。PDF 入口依赖 `pdfplumber`；核心计算模块只使用 Python 标准库。Skill 结构校验用的 PyYAML **不是运行依赖**。

## 验收

三份示例 PDF 默认放在 `D:\ai_finance`。在项目目录运行：

```powershell
python -B -m benchmarks.sample_review
python -B -m unittest discover -s tests -v
```

原始字段人工标注基准为 **135 项**，公式独立复算为 **39 项**；`sample_review` 一次解析三份 PDF，生成 `benchmarks/benchmark_report.md`、`benchmarks/metric_report.json` 和 `benchmarks/risk_review.json`。宁德时代两处附注存在 1 千元的原表勾稽差异，会显示 `NOTE_ROUNDING_DIFFERENCE`。

## 当前边界

已覆盖三份文本型年报的摘要表、选定合并报表行，以及应收账款账龄、存货分类和明确单列的政府补助附注；三份 2025 半年报的摘要表和选定合并报表行也已验证。另可将投资/扩产/中标公告提取为带页码和坐标的事件候选，扫描公告走可选 OCR 并需复核。扫描年度摘要页目前只输出 OCR 候选；其他扫描财务表格、其他公告类型和其余附注尚未验证；系统应明确提示未覆盖，不能把缺失字段解释为零或“无风险”。
