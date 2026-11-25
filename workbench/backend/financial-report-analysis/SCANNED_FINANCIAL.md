# 扫描财务表格候选提取（v0.16）

## 接口

```python
from financial_engine import analyze_scanned_financial_pages

result = analyze_scanned_financial_pages(
    r"D:\path\scanned_annual_report.pdf",
    page_numbers=[11],  # PDF 物理页码，从 1 开始；先选择“主要会计数据和财务指标”所在页
    company_name="比亚迪股份有限公司",
    report_year=2025,
)
```

需要安装 `requirements.txt` 和可选的 `requirements-ocr.txt`。`RapidOCR`、`onnxruntime`、`pypdfium2` 只在扫描页进入 OCR 路径时加载。调用方选择的页数最多为 200，`dpi` 允许 72–400，默认 200。`page_numbers` 可跳过年报前面的页，但返回的来源页码仍是**原 PDF 物理页码**。OCR 不读取整份年报来猜测目标表格；成员 1 应根据上传后的页面预览或章节定位结果提供页码。

`read_pdf_document(path, page_numbers=[...])` 也可单独用于扫描页与文本页的逐页路由。文本页继续调用 `analyze_reports`，扫描财务页使用本接口。`analyze_uploaded_document` 尚未自动把扫描年报送入本接口。

## 返回契约

- `document`：文件名、页数和完整 SHA-256；`identity_claim`：调用方声明的公司及年度。只有 OCR 页眉同时出现完整公司名和“年度报告”年份时，`verified_from_pdf` 才为 `true`。这只是页面内部身份核对，**不能证明文件来自交易所**。
- `candidates`：可定位的年度摘要表字段。目前只识别营业收入、经营活动现金流量净额、归母净利润、总资产及归母净资产中的**单行清晰字段**；跨行标签不猜测。`value`、`previous_value` 已按明确的“元/千元/万元/亿元”换算为人民币元的十进制字符串。`calculated_yoy_percent` 仅在上年金额大于零时返回。
- `sources`：标签、本期金额、上期金额和披露同比的 OCR 原文、PDF 页码、左上角原点的 PDF point 坐标及识别置信度；营业收入若有完整四季度行，另提供 `quarterly_sources`。
- `checks`：本期/上期列顺序、单位、OCR 置信度、披露同比复算、四季度合计。若同比列通过表头“同期增减(%)”声明为百分数，单元格本身可省略 `%`。同比按 `(本期−上期)/上期×100` 复算；上年金额非正时不通过。披露值与复算值允许最多 0.011 个百分点的两位小数舍入差。四季度金额之和必须与年度金额精确相等。
- `status="machine_checked"` 表示页面内部检查通过；否则为 `needs_review`。**顶层 `review_status` 始终是 `requires_human_review`**。同一 OCR 页中的同比与季度合计能发现部分读数错误，但不能替代对原披露文件、合并范围及审计意见的人工或独立来源核对。这些候选值不自动进入既有 `facts`、`metrics`、`risk_assessments`。
- `warnings` 包含 OCR 缺失、无可用表格、无法确认单位/身份、季度金额不符等原因。空 `candidates` 不能解释为财报未披露该字段。

## 样本检验与边界

已将三份**有文本层**的 2025 年报摘要页以 200 DPI 渲染成单页图片 PDF，作为模拟扫描页，用 RapidOCR 3.9.2 + ONNX Runtime 1.30.0 测试；结果与原文本 PDF 对照。比亚迪第 11 页识别 5 个核对通过字段，其中营收通过四季度合计；宁德时代第 11 页识别 2 个核对通过字段；中国能建第 13 页识别 3 个核对通过字段，其同比列仅在表头标 `%`。10 个输出金额及其上年值均与原文一致。这是**定向页面样本**，不是整份财报召回率、真实扫描质量或手写体识别率。多页大表、附注、手写材料、图片破损与低清扫描仍要人工处理或另做基准。

此接口的主要目的，是给成员 1 一个带证据和复核状态的扫描候选入口。待真实扫描件标注集充分后，再评估 PP-StructureV3 或 Docling 的表格识别增益；当前交付没有引入这两套大型运行依赖。

v0.17 已补充候选经真实人员确认后进入公式与风险规则的接口，见 [SCAN_REVIEW.md](SCAN_REVIEW.md)。v0.16 候选返回结构仍保持原样；机器核对本身不会放行金额。
