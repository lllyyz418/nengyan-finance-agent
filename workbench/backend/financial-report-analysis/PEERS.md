# 同年度同行比较（初稿）

该工具读取目标公司的文本型年度财报，根据**团队明确指定**的同行证券代码检索同年度正式年报，再用现有财务引擎计算相同指标。同行关系需要在 `peer_group` 和 `selection_note` 中写明；本版不自动判定公司业务同质性，也不把比较结果解释成投资排名。

## 成员 1 调用

```python
from financial_engine import analyze_peer_group

result = analyze_peer_group(
    r"D:\ai_finance\宁德时代.pdf",
    target_code="300750",  # 可省略；公司名称会用于核对代码
    peer_codes=["300014", "002074"],
    peer_group="动力及储能电池相关企业",
    selection_note="人工选择电池业务相关企业；合并报表产品结构未调整",
    cache_dir=r"D:\ai_finance\skill\report_cache",
)

comparison = result["comparison"]
for item in comparison["metric_comparisons"]:
    if item["metric_code"] == "gross_margin":
        print(item["target"], item["peers"], item["peer_median"])
```

若成员 1 已取得各公司的 `analyze_reports(...)` 结果，可直接使用**无需网络的纯比较函数**：

```python
from financial_engine import compare_peer_group

comparison = compare_peer_group(
    {"300750": target_analysis, "300014": peer1_analysis, "002074": peer2_analysis},
    target_code="300750",
    peer_codes=["300014", "002074"],
    report_year=2025,
    peer_group="动力及储能电池相关企业",
    selection_note="人工选择电池业务相关企业；合并报表产品结构未调整",
)
```

命令行演示（完整 JSON 写入 D 盘）：

```powershell
Set-Location 'D:\ai_finance\skill\financial-report-analysis'
python -B -m financial_engine.peer_cli 'D:\ai_finance\宁德时代.pdf' --target-code 300750 --peer-code 300014 --peer-code 002074 --peer-group '动力及储能电池相关企业' --selection-note '人工选择电池业务相关企业；合并报表产品结构未调整' --cache-dir 'D:\ai_finance\skill\report_cache' --output 'D:\ai_finance\skill\peer_battery_2025.json'
```

## 返回值与核对方法

- `reports`：按证券代码保存每家公司的完整 `analyze_reports` 结果，含文件哈希、原文页码、事实、公式结果、风险筛查和警告。上传的目标财报仍是用户提供的文件；代码核对仅确认封面公司名称对应证券代码，不能验证上传文件真伪。
- `retrieval`：目标文件和同行 PDF 的取得状态；同行记录有披露标题、时间、公告编号、官方 URL、缓存路径、哈希及其他版本。任一同行失败时，`status="partial"`，已成功的公司仍保留。
- `report_warnings`：按证券代码汇总各家财报解析与计算警告；`status="complete"` 仅表示请求的同行报告获取并解析成功，仍须阅读这些警告和各指标的状态。
- `comparison.metric_comparisons`：每个指标的目标值、同行值、有效同行家数、同行中位数和“目标值－同行中位数”。每个值有 `metric_id`，可在相应 `reports[代码].metrics` 中查到 `input_fact_ids`，再查 `facts[*].sources` 的页码和原值。
- 仅同一报告年度、人民币、合并口径、**完全相同公式与单位**且输入事实有效的结果参与比较。缺失和公式不一致时显示 `unavailable` 与 `reason_code`，不填零。
- 至少两家同行有有效值才计算中位数；只有一家时只做并排展示。百分比指标的差值单位为**百分点**。金额和比率由 `Decimal` 计算，输出为十进制字符串。

默认对比同比增速、毛利率、净利率、现金利润比、周转天数、资产回报、研发费用率等 13 个指标。调用方可传 `metric_codes=[...]` 限定项目。同行名单是人工配置的分析假设，样本很小时中位数仅是描述值；各公司的动力电池、储能、材料和其他业务占比可能不同，因此即便公式相同，也需结合分部披露解释。成员 1 应同时传给模型 `limitations`、各项 `status` 和原文证据，不应把“目标高于同行中位数”自动写成优劣判断。

开发样本使用[宁德时代 2025 年报](https://static.cninfo.com.cn/finalpage/2026-03-10/1225002214.PDF)以及巨潮资讯上亿纬锂能、国轩高科的 2025 年报，完成了同年度获取和解析验证。它只是一个人工选择的电池业务示例，未证明这三家公司在全部业务上严格可比。获取接口依赖[巨潮资讯公开披露页面](https://www.cninfo.com.cn/new/commonUrl?url=disclosure%2Flist%2Fnotice)，网站结构变化会通过 `retrieval` 和 `warnings` 告知调用方。
