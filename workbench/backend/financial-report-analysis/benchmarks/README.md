# 三份年报人工核对基准

`annual_2025_gold.json` 是对用户提供的三份 2025 年报指定页逐项核对后录入的标准答案，不是从程序生成的 JSON 反向复制。每项记录字段代码、期间、PDF 中的原始数字与单位、PDF 页码、所在章节及当前适配器是否承诺覆盖。三个 PDF 的 SHA-256 一并记录；文件版本不同会报告错误，避免拿另一版 PDF 的页码或数字比较。

标准答案共 135 项：原有 129 项加三家公司上期总资产和归母权益各 2 项。覆盖年报摘要、季度和非经常性损益表，合并三张报表的选定字段，以及应收账款账龄、存货分类和可单列的其他收益政府补助。`benchmark_report.md` 是运行核对程序生成的逐项结果；宁德时代上期账龄与存货表内各有 1 千元勾稽差异，程序保留原披露值并发出警告。

`metric_check.py` 从人工抄录的原始数字和披露单位出发，用 Python 标准库的 `Fraction` 独立复算三家公司各 13 项财务指标，合计 39 项，再与核心模块的 `Decimal` 结果对照。它不使用核心模块计算预期答案。运行：

```powershell
python -B -m benchmarks.metric_check --output benchmarks\metric_report.json
```

推荐直接运行 `python -B -m benchmarks.sample_review`；该命令只解析一次三份 PDF，并在本目录更新 `benchmark_report.md`、`metric_report.json` 和 `risk_review.json`。风险报告保留每条条件的观察值、证据 ID 与缺失原因。

在项目目录运行：

```powershell
python -B -m benchmarks.check --output benchmarks\benchmark_report.md
python -B -m unittest discover -s tests -v
```

默认从 `D:\ai_finance` 读取三份样本。样本在其他位置时加 `--sample-dir <目录>`，或设置 `AI_FINANCE_SAMPLE_DIR`。`--strict` 会让任何未覆盖字段也导致非零退出码，适合后续完整覆盖时作为交付门槛。

单元测试在缺少样本 PDF 时会跳过集成核验。正式核对请运行上面的 `benchmarks.check` 命令；它会把缺少样本或文件版本不符明确报告为错误。

状态说明：

- `pass`：归一化金额、原始单位、期间、来源页与标准答案一致，且没有冲突值。
- `gap`：标准答案存在，但当前适配器还未提取该字段。
- `error`：已覆盖字段缺失，或值、单位、期间、页码、文件版本不匹配。

核对范围是指定事实的抽取准确性，不等于对整份年报、所有会计政策或经营风险作过审计。比亚迪的研发投入与研发费用金额不同，当前统一核对的是合并利润表中的**研发费用**；第 39 页的研发投入会作为后续独立字段处理。比亚迪合并报表的“千元”单位写在第 123 页总说明中，对第 126 页合并利润表仍适用。
