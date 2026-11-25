# v0.15 交付说明

- `analyze_disclosure_context(...)` 等函数名和参数保持不变；顶层 `schema_version` 从 `0.14` 更新为 `0.15`。单份年报分析结构仍为 `0.6`。
- 年报 PDF 解析器标识更新为 `pdfplumber_annual_adapter_v0.6`。分裂在相邻单元格的单字行标签可受控合并；合并附注的六列存货表新增表格网格回退、类别总额和“原值－准备＝净额”校验。
- 亿纬锂能 2025 年报第 9 页四个单季经营现金流量净额、国轩高科 2025 年报第 193 页两期存货原值与跌价准备已提取。原先这两家公司的相应单份年报风险规则为 `insufficient_data`，本版实测均为 `not_triggered`；详情见 [PEER_EXTRACTION_AUDIT.md](PEER_EXTRACTION_AUDIT.md)。
- 规则状态仍是带证据的筛查，不是信用评级或投资结论。缺少其他报告、扫描财务表格或未覆盖附注时继续显示 `insufficient_data` 或未覆盖范围。

成员 1 可沿用原调用代码，只需检查结果版本、展示 `risk_assessments` 的条件与来源，保留 `warnings`。旧版 JSON 不能用于代表 v0.15 对这两家同行的判断。使用示例见 [QUICK_START.md](QUICK_START.md)、[DATA_MINING.md](DATA_MINING.md)。
