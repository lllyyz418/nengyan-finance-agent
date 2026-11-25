# 数据获取与纵横比较统一入口（v0.15）

`analyze_disclosure_context` 是现有提取、计算、历史比较和同行比较工具的统一调用入口。它不调用语言模型。每个年度 PDF 的财务提取只运行一次，解析结果复用于当前、历史及同行分析；公式和风险规则沿用现有引擎。

## 输入与调用

上传文本型年报可省略公司代码：从报告前 12 页明确的证券代码标签读取六位 A 股代码。显式代码若与 PDF 冲突，保留当前财务分析，停止纵横归组。联网时另外核对公司名称与证券目录。

```python
from financial_engine import analyze_disclosure_context

result = analyze_disclosure_context(
    r"D:\ai_finance\宁德时代.pdf",
    stock_code="300750",
    lookback_years=1,
    peer_codes=["300014", "002074"],
    peer_group="动力及储能电池相关企业",
    selection_note="人工选择电池业务相关公司，合并报表业务结构未调整",
    cache_dir=r"D:\ai_finance\skill\report_cache",
    fetch_online=False,
)
```

默认 `fetch_online=False`：使用输入报告、本地往期文件或缓存中相邻的 PDF/清单 JSON；不发起网络请求。首次在线检索请显式设置 `fetch_online=True` 并提供可写的 `cache_dir`。网络由已有巨潮资讯工具执行，不需要新增第三方库。

仅输入公司代码时，必须同时给出**报告年度**，不按当前日历年份猜测最新报告：

```python
result = analyze_disclosure_context(
    stock_code="300750", report_year=2024, lookback_years=0,
    cache_dir=r"D:\ai_finance\skill\report_cache",
    fetch_online=True,
)
```

以上也能在 `fetch_online=False` 时运行，前提是缓存中有对应完整年报及清单。

## 参数约定

| 参数 | 含义 |
| --- | --- |
| `input_pdf` | 可选上传路径；未提供时要求 `stock_code`、`report_year`。 |
| `stock_code` | 六位字符串；保留前导零，拒绝整数代码。 |
| `report_year` | 1990–2100 的整数财年；上传路径模式可省略，给出时必须匹配 PDF。 |
| `lookback_years` | 0–5 的整数，默认 2；0 不请求纵向分析。 |
| `peer_codes` | 1–8 个不重复的同行代码，不能包含目标；不提供则不请求横向分析。 |
| `peer_group`、`selection_note` | 配置同行时必须说明业务分组和选择理由。不自动证明业务可比。 |
| `cache_dir` | PDF 与清单缓存目录；联网必填，离线读取缓存时也需提供。 |
| `fetch_online` | 默认 False；True 查询公开目录并下载，缓存可复用。 |
| `local_history_paths` | 可选本地往期年报路径列表；必须在请求的往期年份内、属于同一公司。同年多个不同路径不任意选取，要求调用方先确定版本。 |
| `metric_codes` | 横向指标名称列表；省略则复用现有 13 项同行比较指标。 |
| `options` | 原年度公式和风险引擎配置，规则见 `INTEGRATION.md`。半年报不应用这些年度规则配置。 |

错误参数抛出 `ValueError`；文件损坏、报告缺失、身份冲突等运行结果写入状态与警告。金额计算仍由 `Decimal` 引擎完成，数值返回字符串。

## 文件取得与版本

1. 通过已有证券目录匹配目标与同行，发现请求财年的完整年报；摘要、英文版、提示性公告不参与年度比较。
2. 联网时选择目录查询返回候选中最新正式披露的完整版本；离线时仅选择缓存已有的最新版本。`retrieval.alternatives` 保留其余候选，`version_selection` 明确区别两者。
3. 缓存清单必须有官方 PDF 来源、年度、代码、披露时间、公告编号和 SHA-256。读取 PDF 前核对文件头与实际哈希；缓存里的 `local_path` 不用于跳转读取，始终读清单旁的同名 PDF，允许整体搬迁缓存。
4. 入组前核对 PDF 年份和代码。离线缓存缺少可读的 PDF 代码时不进入比较；联网则可用明确的公司名称目录匹配补充核对。原文代码核对不等于鉴定上传文件真实性。
5. 同年最新版缓存损坏时，不默默改用较旧版本。若在线目录查询失败，可使用已校验缓存，但返回 `CATALOGUE_UNAVAILABLE_CACHE_FALLBACK` 和 `partial`，不声称已核验网站最新版本。

年度目录查询沿用现有每个证券最多 5 页、每页 30 条的上限；`catalogue_completeness_guaranteed=False`，不能声称枚举了网站的所有版本。

上传年报自身保留为 `uploaded_version_not_compared`：不会偷偷用网站文件替换它，也没有宣称该上传文件就是网站最新版本。当前版本不提供历史时点回测的数据截断；默认最新披露版本不能直接用于无前视偏差的模型训练。

## 返回结构与证据

顶层 `schema_version="0.15"` 是汇总格式；里面的年度分析仍采用现有 `0.6`，历史和同行比较子格式仍是 `0.1`。

- `current`：目标报告的原分析结果，包含 `documents/facts/metrics/unavailable_metrics/risk_assessments/findings/warnings/audit`。
- `identity`：实际识别的代码、公司、年度与识别状态；`sources` 保存代码标签所在页和原文行。
- `retrieval`：检索模式、接受或拒绝的文件、来源链接、披露日期、公告编号、文件哈希、版本选择及其他版本。出现拒绝文件时查看其 `role`、`stock_code`、`report_year` 和 `message`。
- `history.analysis`：目标和已取得往期报告的财务分析；`history.comparison` 提供逐年事实、指标、跨年变动及重述核查。对比中的事实和指标 ID 在 `history.analysis` 内解析。
- `history.requested_years/available_years/missing_years`：请求年份和实际覆盖；目标当年在 `comparison.report_years`，不列入往期 `available_years`。
- `peers.reports[代码]`：每家公司的分析；`peers.comparison` 提供同年度同公式指标、有效同行数量、中位数及差异。对比证据需进入对应代码的报告解析，不能只在 `current` 中查找。
- `risk_review`：跨报告风险复核的 `assessments/findings/data_quality`；比较口径冲突会阻止连续风险判断。规则见 [CONTEXT_RISK.md](CONTEXT_RISK.md)。
- `coverage/warnings/audit`：当前覆盖、所有分析及获取问题、每份 PDF 的解析与接受记录。计算事件仍保存在各子分析的 `audit` 中。

顶层文件覆盖 `status` 与 `risk_review.status` 是独立状态：例如文件齐全而比较口径冲突时，前者可为 `complete`，后者为 `partial`。

`status="complete"` 仅表示请求的目标、往期和同行文件覆盖完成；不代表所有指标可计算、完整附注都已解析，或网站全部公告已发现。缺年份、缺同行或网络降级时为 `partial`，目标报告不可处理时为 `failed`。不请求某项时子状态为 `not_requested`；上传半年报并请求跨报告分析时为 `unsupported`；同行列表包含目标公司时 `peers.status="invalid_configuration"` 且总体为 `partial`。前端应显示 `warnings`、指标不可计算原因、同行业务结构限制和重述核对范围。

每项比率的证据链：`metric.input_fact_ids` → 该子分析的 `facts` → `sources`（页码、原值、单位、行坐标）→ `documents.file_sha256`。跨年差异沿用 `build_annual_history` 的重述核查：有冲突则停止计算相关差异，不悄悄覆盖旧数；比较列核查不全时保留 `basis_check` 与未核对字段。

合并应收账款账龄表若在标题后一页，`sources[].heading_page` 记录标题页，`page_start` 和 `row_bbox` 仍记录实际数值页。提取前需确认合并报表附注及“应收账款”项目，不能将母公司“其他应收款”当作合并应收账款。宁德时代 2024 年报的逐页核查见 [SOURCE_AUDIT_CATL_2024.md](SOURCE_AUDIT_CATL_2024.md)。

## PowerShell 演示

在包目录运行。默认离线，使用现有宁德时代往期及同行缓存：

```powershell
python -B -m financial_engine.mining_cli 'D:\ai_finance\宁德时代.pdf' --stock-code 300750 --lookback-years 1 --peer-code 300014 --peer-code 002074 --peer-group '动力及储能电池相关企业' --selection-note '人工选择电池业务相关公司，合并报表业务结构未调整' --cache-dir 'D:\ai_finance\skill\report_cache' --output 'D:\ai_finance\skill\context_battery_2025.json'
```

要重新查询网站，在命令中增加 `--online`。公司代码模式用 `--stock-code 300750 --year 2024`，省略 PDF 参数。`--history-pdf` 可重复提供明确选定的本地往期文件。程序打印输出路径及总体、纵向、横向状态；`failed` 的命令退出码为 2。

## 当前边界与验收

支持选定文本年报字段的年度纵向和横向比较，不自动选择同行、不抓取网页传闻或推测缺失金额。半年报上传保留 `current`，但请求跨报告比较会返回 `unsupported` 和 `H1_CROSS_REPORT_COMPARISON_UNSUPPORTED`；扫描财务表格、手写件、公告事件汇总及机器学习仍由后续任务完善。公告入口继续独立，金额不参与此处财务比较。

`tests/test_mining.py` 验证一次解析复用、精确中位数、缺文件降级、错误身份、缓存哈希、版本选择、重述、公司代码输入和联网失败缓存回退。网络调用在测试中由固定响应替代，让逻辑测试不依赖网站可用性；真实网站查询应另记录实际成功或失败。此前 v0.10 的已验收功能无需重新人工标注。
