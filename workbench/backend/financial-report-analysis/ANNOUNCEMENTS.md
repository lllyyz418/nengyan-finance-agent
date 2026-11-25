# 公告与扫描页入口：成员 1 接入说明

这部分只处理**投资、扩产、工程中标公告的事件候选**。公告金额不进入年报的 `facts`、`metrics`、`risk_assessments`。未覆盖股权质押、股权变动、合同履约及公告全文语义核查；半年报和扫描年报也尚未接入年度财务公式。

## 本地 PDF

```python
from financial_engine import analyze_announcement, read_pdf_document

result = analyze_announcement(r"D:\ai_finance\skill\announcement_cache\601868_bid_20250617.pdf")
print(result["event"]["amount_candidates"])

# 如需先看页面类型、OCR 文本和坐标：
pages = read_pdf_document(r"D:\ai_finance\skill\announcement_cache\601868_bid_20250617.pdf")
```

命令行可将结果保存为 UTF-8 JSON：

```powershell
python -B -m financial_engine.announcement_cli 'D:\ai_finance\skill\announcement_cache\601868_bid_20250617.pdf' --output 'D:\ai_finance\skill\announcement_result.json'
```

扫描页使用可选 CPU OCR。安装 `requirements.txt` 后，再安装 `requirements-ocr.txt`；OCR 缺失或失败时返回 `OCR_FAILED`，不会悄悄把空文本当作“没有公告金额”。这次本机把可选依赖放在 `D:\ai_finance\skill\_ocr_deps`，运行前可设置 `$env:PYTHONPATH='D:\ai_finance\skill\_ocr_deps'`。交付压缩包不含大型 OCR 模型和第三方依赖，成员 1 应自行安装。

## 巨潮资讯发现与下载

```python
from financial_engine import (resolve_security, discover_event_announcements,
                              download_announcement, analyze_announcement)

security = resolve_security("601868")
records = discover_event_announcements(security, "2025-06-01", "2025-06-30")
record = next(item for item in records if "中标" in item["title"])
cached = download_announcement(record, r"D:\ai_finance\skill\announcement_cache")
result = analyze_announcement(cached["local_path"], metadata=cached)
```

目录端点不是官方稳定 API；关键词、标题过滤和每关键词最多 3 页的默认上限意味着列表**不保证完整**。成员 1 应展示 `query_keywords`、`discovery_limit_pages_per_keyword`、`source_url`，并留存 PDF、清单 JSON 和 SHA-256。下载只允许巨潮资讯 `static.cninfo.com.cn/finalpage/...PDF`；公司简称来自目录，正文证券代码另行提取并核对。`published_at` 为 UTC 时刻，`published_date_cn` 为中国本地日期；`reported_identity.announcement_date` 是 PDF 落款日期，三者不应机械视为同一字段。

## JSON 约定

- `routing.document_type`：`announcement`、`annual_report`、`half_year_report` 或 `unknown`。非公告输入返回 `event=null`、`review_status=unsupported`。
- `document`：原文件名、完整 SHA-256、页数、读取状态及警告；`read_pdf_document` 另提供每页 `page_type`、`read_method`、文本行、置信度和坐标。
- `event.event_type`：`bid_award`、`fund_investment`、`investment_expansion`、`investment_other`。该分类仅为路由；不代表项目已完成。
- `event.event_status`：中标公告若明确写了“尚未正式签署合同”，输出 `contract_unsigned`；其他状态不推断合同生效或出资已支付。
- `event.amount_candidates`：按 `role` 区分 `bid_amount`、`contract_amount`、`fund_target_size`、`company_commitment`、`project_total_investment`。`amount` 为 `Decimal` 计算得到的基本币种十进制字符串；`currency` 为 CNY 或 USD。美元不换算为人民币，不与年报人民币数据直接求比值。
- `amount_candidates[*].sources`：金额所在文本行的 `file_sha256`、1 起始页码、PDF 点坐标 `[x0, top, x1, bottom]`、`method` 和 OCR `confidence`；`label_sources` 另外保存字段名所在行，跨行字段会保留两行。相同角色、金额重复披露时保留多个来源；同一角色出现不同金额时返回 `MULTIPLE_AMOUNTS_SAME_ROLE`，需要人工确定是变更、分期还是不同标的。
- `event.reported_identity`：PDF 自身的证券代码、公司名、落款日期及各自坐标。目录代码与 PDF 代码相同时 `identity_quality=catalogue_and_pdf_code_match`，这只核对了代码；冲突时 `stock_code=null`、`identity_quality=conflict`。
- `review_status`：`candidates_extracted` 或 `requires_human_review`。OCR、约数/上限、多金额冲突、缺金额等均进入复核。即使是 `candidates_extracted`，也仅是公告字段候选，不能称为经审计财务事实。

`read_pdf_document` 默认最多读取 20 页、80 MB，扫描页按 200 DPI 渲染；可在主程序内设置更严格的上传限额。手写、复杂印章遮挡、复杂表格的正确率未建立基准，出现 OCR 输出时请展示原页图片供人工复核。OCR 只服务公告候选；扫描年报的报表勾稽与公式映射尚未完成。

## 验证样本

已用巨潮资讯的[中国能建 2025-06-17 中标公告](https://static.cninfo.com.cn/finalpage/2025-06-17/1223887083.PDF)和[宁德时代 2025-05-16 基金投资公告](https://static.cninfo.com.cn/finalpage/2025-05-16/1223572286.PDF)核对。前者跨行披露约 50.0508 亿元人民币且注明合同尚未签署；后者将 15 亿美元基金目标规模与不高于 2.25 亿美元的公司认缴额分别标记。还将前者渲染为图片 PDF 验证 OCR、页码与坐标回映。样本只证明这些页面的字段可提取，不代表所有公告类型的准确率。
