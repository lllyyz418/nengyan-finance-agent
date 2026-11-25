# v0.16 交付说明

本版新增 `financial_engine.analyze_scanned_financial_pages`，针对**选定的扫描年度摘要页**提取带页码、坐标与 OCR 置信度的金额候选。按明确单位转换为人民币元，并用 `Decimal` 核对披露同比；若同页有完整四季度营业收入，复核季度合计。公司和年份由调用方提供，同时在 OCR 页眉做内部一致性检查。返回值始终保留人工复核状态，不直接接入既有财务事实、公式和风控链。

`read_pdf_document` 新增 `page_numbers` 参数，允许只读取原 PDF 的指定物理页，方便处理长年报中的扫描页。原有不传此参数时的行为不变。`SKILL.md`、`INTEGRATION.md` 和 `QUICK_START.md` 已补充路由与调用边界，完整接口说明见 [SCANNED_FINANCIAL.md](SCANNED_FINANCIAL.md)。现有 `analyze_reports`、`analyze_disclosure_context` 等接口未改签名或返回结构。

本机用三份年报的已知摘要页生成图片 PDF 进行 OCR 检验，10 个输出字段的本期与上期金额均与原始页一致；其中比亚迪 5 个、宁德时代 2 个、中国能建 3 个。三家原版报表的其他字段并未因此获得扫描识别能力。尚无真实低清扫描件或手写材料的独立标注集，不能把本次检验当作其准确率。

成员 1 应把此接口的候选结果放入复核界面，显示 `status`、`checks`、`sources` 与 `warnings`。机器核对通过不等于披露真实性或审计结论。确认扫描金额后若要纳入正式指标分析，应经人工确认并用现有结构化输入契约形成来源记录；本版没有自动放行开关。
