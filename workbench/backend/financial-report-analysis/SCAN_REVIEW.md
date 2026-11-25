# 扫描候选确认与财务分析（v0.17）

本模块把 v0.16 的扫描摘要候选接入已有公式和风险规则。成员 1 的后台应保存原始候选、鉴别实际确认人员，并把确认记录交给本模块。它没有网页复核界面，也没有 Agent 编排。OCR 置信度或语言模型的判断不会自动生成确认决定。

## 三个函数

```python
import json
from pathlib import Path
from financial_engine import (
    create_scan_review_template, prepare_reviewed_scan, analyze_reviewed_scan,
)

scan = json.loads(Path(r"D:\ai_finance\skill\scan_byd_p11_v016.json").read_text(encoding="utf-8-sig"))
template = create_scan_review_template(scan)
# 模板里公司身份和全部字段均为 pending；保存后由人查看原 PDF，填写决定。
# 成员 1 应从已鉴别的用户身份产生 reviewer.id，不能让模型自行填写确认身份。

review = json.loads(Path(r"D:\ai_finance\skill\scan_review_completed.json").read_text(encoding="utf-8-sig"))
result = analyze_reviewed_scan(
    scan, review,
    source_path=r"D:\ai_finance\skill\ocr_runtime\byd_p11_scan.pdf",
)
# result['review']：决定、原值、修改值、来源和核验事件
# result['analysis']：沿用现有 facts / metrics / unavailable_metrics / risk_assessments 等
```

`prepare_reviewed_scan` 只核验并返回 `parsed_document`，方便成员 1 先显示核验结果；`analyze_reviewed_scan` 在同样的核验后调用现有 `analyze_parsed_documents`。两者每次都重新比对原 PDF 的 SHA-256，均不写文件、不联网、不运行 OCR。它们只用 Python 标准库。

## 填写确认记录

模板绑定 `file_sha256` 和整个原始候选快照的 `candidate_snapshot_sha256`。填写模板时保留这两个字段。候选文件只要被修改，即使修改的是核对状态，也要重新生成并复核模板。

至少有一项实际确认或拒绝时，填写唯一 `review_id`、`reviewer={"id":"实际人员标识","kind":"human"}` 和带时区的 ISO-8601 `reviewed_at`。结果把时间规范化为 UTC。`kind="human"` 只是数据声明，**无法替代成员 1 后台的身份鉴别**；快照哈希同样不提供签名或防伪。后台应保管原候选和确认记录，不能把“确认工具”作为模型可自主调用的工具。

公司层面 `identity.decision` 必须为 `confirm`，公司和年度须与候选一致；还要核对 `currency="CNY"`、`statement_scope="consolidated"` 及布尔值 `comparative_restated`。该布尔值代表所选摘要表的上年比较列是否重述，不代表整份年报所有比较列的状态。公司或年度识别有误时，重新提取；母公司口径、外币或半年报不能沿此年度合并口径入口计算。

每个 `decisions` 元素处理同一字段的**本期和上期两个金额**：

| decision | 含义与必填内容 |
| --- | --- |
| `pending` | 尚未确认。缺少决定的字段也按此处理，不进入计算。 |
| `confirm` | 原文两期金额及单位无误。`evidence_confirmed=true`；`values.current/previous` 保留原 OCR 文本及单位。若原候选是 `needs_review`，还须填写复核说明 `note`。 |
| `correct` | 纠正金额或单位。两期均填写字符串 `raw_value` 和 `raw_unit`，至少一项确有修改；`evidence_confirmed=true` 并写明 `note`。 |
| `reject` | 不采用该字段，须填写原因 `note`。 |

示例：某人核对营业收入后，把当前决定改为 `correct`，在 `values.current` 写 `{"raw_value":"250","raw_unit":"万元"}`，上期保留或填写其确认后的值；`note` 写明具体误识别原因。金额支持普通十进制、合法千分位及括号负数，禁止浮点数、科学记数法、`NaN` 和无限值。单位只接受元、千元、万元、亿元；本期和上期可以分别使用不同单位，进入公式前用 `Decimal` 精确换算。

## 结果与限制

- 顶层 `status` 为 `ready`（选中候选的决定已处理且存在可用数据）、`partial`（有未确认或冲突字段）、`no_confirmed_data`（身份已确认但没有可用字段）或 `blocked`（身份、期间、口径尚不满足条件）。这些状态只针对所选摘要候选，不表示整份财报分析完整。
- `review.decisions` 保存完整原始候选、每期确认后的原文值及人民币金额、决定和结果状态。相同指标确认后仍有不同金额时，两期均标为 `withheld_conflict`。金额相同的重复来源可以合并，但不因人工确认或重复 OCR 自动标为 `cross_checked`。
- `analysis.facts[*].sources` 分别指向本期或上期**对应的单元格**，保留 `ocr_original`、`reviewed_disclosure` 和人员/时间/决定记录。`raw_value` 与 `raw_unit` 为确认后的披露值；`verification_status="human_reviewed"` 与 `quality="extracted"` 并存，均不表示财报经过审计或来自官方渠道。
- `unavailable_metrics.reason_code` 能区分 `UNCONFIRMED_INPUT`、`REJECTED_INPUT`、`CONFLICTING_INPUT` 和普通 `MISSING_INPUT`；规则的 `missing_or_invalid` 也保留这些原因。零分母、负基数等继续按 [FORMULAS.md](FORMULAS.md) 处理。
- 纠正后重算与 OCR 同比的差异会给出 `REVIEWED_YOY_DISCREPANCY`。人确认了金额，并不同时确认 OCR 百分数；输出保留此差异，便于再核对百分数原文。原始 OCR 警告也会保留。
- 当前扫描入口只有五类摘要字段。因此可能算出营业收入/归母利润/现金流同比及具备输入时的简化归母权益回报率；毛利率、周转天数、自由现金流以及大部分组合风险规则仍可能缺数据。归母净利润不会替代合并净利润；缺季度现金流和应收附注时，风险规则返回 `insufficient_data`，不能描述为“无风险”。

确认接口不会把未经确认的四季度 OCR 值带入公式，也不接收自由添加的字段或改变指标名、期间的记录。仍需扩展的跨页表格、财务附注和真实低清扫描测试见 [SCANNED_FINANCIAL.md](SCANNED_FINANCIAL.md)。

## 本地命令行

在项目目录运行，先生成待填写模板：

```powershell
python -B -m financial_engine.scan_review_cli template 'D:\ai_finance\skill\scan_byd_p11_v016.json' --output 'D:\ai_finance\skill\scan_review_pending_v017.json'
```

人工核对后把填写后的文件保存为 `scan_review_completed.json`，再运行：

```powershell
python -B -m financial_engine.scan_review_cli analyze 'D:\ai_finance\skill\scan_byd_p11_v016.json' 'D:\ai_finance\skill\scan_review_completed.json' --source-pdf 'D:\ai_finance\skill\ocr_runtime\byd_p11_scan.pdf' --output 'D:\ai_finance\skill\scan_review_analysis_v017.json'
```

命令行支持带 BOM 的 UTF-8 JSON，拒绝覆盖已有输出或输入文件。源文件不匹配、快照过期、未知/重复决定、伪装成确认的改值等会报 `ScanReviewError`，不生成分析输出。三个函数也提供同样的异常，后台应呈现具体错误。

## 本次验证

新增测试覆盖确认、纠正、拒绝、未确认、重复数值冲突、源文件变化、候选快照变化、不同列单位及坐标、重述、浮点数、超长金额和零/负基数。已有三份模拟扫描结果可用于回放测试，回放中的人类身份仅为**测试桩**；它验证代码路径，不产生真实人员的确认记录。真实扫描件和真实确认界面的体验仍需后续验收。
