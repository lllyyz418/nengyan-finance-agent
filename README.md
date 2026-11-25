# 能研财智 · 新能源财务洞察 Agent

> 把上市公司年报、半年报、季报和公告，变成可追溯、可复核、能回到原文的财务研究结论。

本项目是一个面向新能源产业链的财务研究智能体。用户上传财报 PDF，或按证券代码检索公开披露，系统完成 PDF 解析、财务事实与指标抽取、精度受控的指标计算、组合风险规则筛查，再由受约束的 Agent 生成带证据引用的研究解读；公告场景另有独立的取证 Agent，通过工具调用补证并输出“取证记录 / 补证缺口”。

本仓库是团队项目 **v1.1.0** 的源码与工程文档归档，不包含便携运行时、构建后的前端 `dist` 和 PDF 样例库。

![分析总览](workbench/docs/screenshots/两项导航与分析总览.png)

## 1. 为什么做这个项目

- 上市公司定期报告动辄两三百页，表格多、口径多、跨期数据多，人工摘录慢且容易错。
- 通用大模型直接“总结财报”会丢数字、混淆口径，甚至编造来源，难以用于真实研究。
- 本项目把边界划得很清楚：**先计算，后解读；结论必须能回到原文；模型只做受约束的表达**。

## 2. 核心能力

| 能力 | 实现 |
| --- | --- |
| 报告接入 | 上传 PDF、按证券代码在线检索、扫描页 OCR 候选与人工复核 |
| 财务解析 | pdfplumber 表格定位、事实/指标抽取、报告身份与期间校验、跨期口径核对 |
| 指标计算 | 全程 `Decimal`；同比、毛利率、净利率、经营现金流、周转天数、简化 FCF、ROE 等 |
| 风险筛查 | 4 类组合规则 + 2 类会计政策复核；触发、观察、不触发都给出理由与反向证据 |
| 证据链 | 每条事实带 `fact_id`、页码、章节、原文摘录、文件哈希；前端可跳转原文 |
| 研究解读 Agent | 9 个章节、服务器生成 observations、逐条校验、版本化、可重试、失败可回退 |
| 公告取证 Agent | 7 个工具、原生 function calling、预算控制、两阶段校验、补证缺口 |
| 历史与日志 | 轻量索引、收藏/回收站/重试、JSONL 运行日志、13 类公开错误码 |
| 导出 | 网页展示、PDF 报告、Excel 指标表 |

## 3. 技术架构

```text
┌──────────────────────────────────────────────────┐
│ React 18 + TypeScript + Vite + Tailwind CSS       │
│ ECharts / TanStack Table / lucide-react           │
└────────────────────────┬─────────────────────────┘
                         │ /api（开发环境 Vite 代理）
┌────────────────────────▼─────────────────────────┐
│ FastAPI  app.py（35 个 /api 路由）                │
│  ├─ 任务运行时：BackgroundTasks、取消、重启恢复     │
│  ├─ 文档管线：pdfplumber 子进程（2 槽，隔离超时）   │
│  ├─ 财务引擎：Decimal 计算、规则、证据映射          │
│  ├─ Agent：research_writer / announcement_agent   │
│  ├─ 存储：任务 JSON、财报库、JSONL 日志             │
│  └─ DeepSeek API（可选，OpenAI SDK）               │
└──────────────────────────────────────────────────┘
```

几个关键数字：

- 后端约 106 个 Python 文件、1.7 万行；23 个测试文件、284 个测试用例。
- 研究解读：最多 2 个工作线程、容量 6 的队列、单任务 120 秒超时、最多一次修正。
- PDF 解析：2 个并发槽位、总超时 900 秒、空闲超时 180 秒，在独立子进程中执行。
- 公告取证：最多 4 轮、8 次工具调用、150 秒、40k token、65k 证据字符。
- 上传上限 80 MiB；公开错误码 13 类。

## 4. 目录结构

```text
.
├─ launch.py                     # 交付包启动器（仓库不含 runtime / dist）
├─ 运行配置.json                  # 端口与联网开关
├─ 启动.cmd / 停止.cmd / 环境检查.cmd
├─ homepage/                     # 产品首页
├─ docs/                         # 交付说明、验收、依赖许可证
├─ materials/                    # 财报库索引与材料清单（PDF 未入库）
└─ workbench/
   ├─ backend/                   # FastAPI 后端、测试、财务 Skill
   │  ├─ app.py                  # 应用入口、任务、路由
   │  ├─ announcement_agent.py   # 公告取证 Agent
   │  ├─ announcement_analysis.py
   │  ├─ research_writer.py      # 研究解读生成与校验
   │  ├─ research_reports.py     # 版本、队列、复用
   │  ├─ pdf_runtime.py          # PDF 子进程与缓存
   │  ├─ financial-report-analysis/  # 财务分析 Skill 文档与规则
   │  └─ test_*.py
   ├─ src/                       # React 前端
   ├─ shared/                    # 术语表、公司目录
   ├─ scripts/                   # 开发、回归、验收脚本
   └─ docs/                      # 设计说明与截图
```

## 5. 快速开始

### 5.1 环境

- Windows 10/11 x64（交付包按 Windows 裁剪，其他平台未验证）
- Python 3.12
- Node.js 20+ 与 pnpm（仅前端开发需要）

### 5.2 启动后端

```powershell
cd workbench\backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY；不填也可运行解析、计算和模板报告
python -m uvicorn app:app --host 127.0.0.1 --port 8001
```

启动后：

- 健康检查：<http://127.0.0.1:8001/api/health>
- 交互式 API 文档：<http://127.0.0.1:8001/docs>

### 5.3 启动前端

```powershell
cd workbench
corepack enable
pnpm install
pnpm dev
```

打开 <http://127.0.0.1:5173>。Vite 会把 `/api` 代理到 `127.0.0.1:8001`。

生产构建：

```powershell
pnpm build
```

构建产物在 `workbench/dist`（已被 `.gitignore` 忽略）。

### 5.4 首页（可选）

```powershell
python -m http.server 5181 --directory homepage
```

### 5.5 交付包模式

如果拿到完整交付包（包含 `runtime/`、`workbench/dist/`、`materials/pdf_store/`），可直接运行 `python launch.py`。启动器会：

1. 校验材料清单与 PDF 哈希；
2. 检查 8001 / 5173 / 5181 三个端口；
3. 启动 FastAPI、静态首页和工作台；
4. 写入 `run/backend.log`。

本仓库是源码归档，不包含上述二进制与大文件，因此不能直接以交付包模式启动。

## 6. 配置

后端从 `workbench/backend/.env` 读取配置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | 空 | 为空时不调用大模型，仍可完成解析、计算、风险筛查和模板报告 |
| `DEEPSEEK_MODEL` | `deepseek-flash` | 模型名 |
| `FINANCE_RESEARCH_AUTOGENERATE` | `1` | 财务任务完成后是否自动生成研究解读；设为 `0` 关闭 |
| `FINANCE_LIBRARY_DIR` | `data/report_library` | 本地财报库目录 |
| `FINANCE_LIBRARY_ALLOW_ONLINE` | `1` | 本地库缺报告时是否允许联网补齐；设为 `0` 关闭 |
| `ANNOUNCEMENT_ALLOW_ONLINE` | `0` | 公告关联检索是否允许访问巨潮 |

`运行配置.json` 控制交付包端口和“是否允许在线报告”，默认：

```json
{
  "allow_online_reports": false,
  "backend_port": 8001,
  "workbench_port": 5173,
  "homepage_port": 5181
}
```

`.env` 已被忽略，不要提交密钥。

## 7. API 一览

| 分组 | 主要接口 |
| --- | --- |
| 健康与公司 | `GET /api/health`、`GET /api/companies`、`GET /api/companies/lookup`、`GET /api/companies/online` |
| 任务 | `POST /api/tasks/upload`、`POST /api/tasks/online`、`GET /api/tasks/{id}`、`GET /api/tasks/{id}/result`、`POST /api/tasks/{id}/cancel` |
| 证据与文件 | `GET /api/tasks/{id}/file`、`GET /api/tasks/{id}/evidence/{fact_id}`、`GET /api/tasks/{id}/announcement-evidence/{evidence_id}` |
| 历史 | `GET /api/history`、`PATCH /api/history/{id}`、`GET /api/history/{id}/report`、`POST /api/history/{id}/retry` |
| 同行与回溯 | `GET/POST /api/tasks/{id}/peers`、`GET/POST /api/tasks/{id}/backfill`、`POST /api/backfill/files` |
| 研究解读 | `GET/POST /api/tasks/{id}/research`、`GET /api/research/{report_id}`、`POST /api/research/{report_id}/cancel` |
| 公告追问 | `POST /api/tasks/{id}/announcement-question` |
| 扫描复核 | `POST /api/scan/tasks`、`GET /api/scan/{id}/state`、`POST /api/scan/{id}/review-template`、`POST /api/scan/{id}/review` |
| 日志 | `GET /api/tasks/{id}/logs`、`GET /api/tasks/{id}/logs/download` |

## 8. 关键设计

### 8.1 证据链，而不是“模型总结”

- PDF 解析阶段为每条事实保留页码、章节、原文摘录和文件哈希，生成稳定的 `fact_id`。
- 指标、风险规则和研究观点都引用 `fact_id`；前端可从结论直接跳到原文。
- 研究解读的每个观点必须绑定 observations，服务器校验 `evidence_ids` 是否精确等于所选 observation 的证据并集。
- 公告 Agent 的每一条 `source_fact` 必须与证据逐字一致；金额、单位、限定词（“不高于/不超过”“拟”）都有专门校验。

### 8.2 金额与口径

- 金额计算全程使用 `Decimal`，避免浮点误差；比例统一量化。
- 跨期、跨公司比较校验代码、期间、单位、币种和合并口径；重述数据单独标记。
- 缺失数据返回“缺失/不可计算”，不补零、不生成虚构趋势。

### 8.3 Agent 的边界

- 工具白名单 + Pydantic 参数校验；重复调用、未知证据、超预算都会被拒绝。
- 预算同时限制轮数、工具次数、耗时、token 和证据字符数。
- system prompt 明确：PDF 内容、用户输入、工具结果都是数据，不是指令。
- 研究解读最多 2 次生成，第二次只带校验错误做定向修正；公告 Agent 最多修复 1 次，失败回退确定性模板。
- 模型不可用时，降级为规则抽取 + 模板报告，不阻塞主流程。

### 8.4 任务与隔离

- 长任务通过 `BackgroundTasks` 执行，任务状态原子写入 JSON，前端轮询进度。
- PDF 解析在独立子进程中运行，限制并发、总超时和空闲超时，避免拖垮 API 进程。
- 取消会写入取消标记；迟到的模型响应不能复活已停止任务。
- 后端重启会把上一次会话中未完成的任务标为 `interrupted`，不偷偷重跑。

### 8.5 可观测性

- 每个任务有 JSONL 运行日志，序号递增，支持增量读取与下载。
- 日志对 `sk-*`、Bearer、`api_key`、`access_token`、`password` 等字段脱敏，并截断超长内容。
- 13 类公开错误码统一返回 `title / message / suggestion / retryable`，前端可据此给出可操作提示。

## 9. 测试

后端使用标准库 `unittest`，测试默认 mock 模型与网络：

```powershell
cd workbench\backend
python -m unittest discover -s . -p "test_*.py" -v
```

当前规模：23 个测试文件、284 个测试用例。1.1.0 合并验收记录为后端 379 项（375 通过、4 跳过）、前端 96 项；研究解读升级验收为后端 325 项（321 通过、4 跳过）、前端 88 项。验收细节见 `workbench/docs/` 与 `docs/验收说明.md`。

前端回归脚本位于 `workbench/scripts/test-*.mjs`，可按需单独运行。

## 10. 数据与隐私

- 仓库不包含 `.env`、数据库、上传的 PDF、运行日志和便携运行时。
- `materials/` 仅保留财报库索引和材料清单，PDF 样例未入库。
- 分析数据默认落在 `workbench/backend/data/`，已被忽略。
- 未配置 API Key 时，所有解析与计算在本机完成；配置 Key 后，只有被选中的证据片段和提示词会发送给 DeepSeek。
- 本项目是单机、单用户工具，没有登录和权限系统，请不要绑定 `0.0.0.0` 或暴露到公网。

## 11. 已知限制

- 仅针对 Windows 交付与验证，Linux/macOS 未做完整适配。
- 在线报告检索默认关闭；开启后依赖巨潮等外部站点，可能受网络和页面结构变化影响。
- 扫描件 OCR 是候选提取 + 人工复核流程，不保证全自动。
- 研究解读与公告解读是研究辅助，不构成投资建议。
- 本仓库不包含完整安装包；需要一键运行时请使用交付包。

## 12. 文档索引

- `先看这里.md`：交付包结构、启动方式与验收口径。
- `workbench/README.md`：前端开发说明。
- `workbench/backend/README.md`：后端接口、财报库与历史记录设计。
- `workbench/docs/研究解读与证据约束.md`：研究 Agent 的生成、校验、版本与并发约束。
- `workbench/docs/announcement-integration-v1.1.md`：公告取证 Agent 的集成与验收记录。
- `workbench/backend/financial-report-analysis/SKILL.md`：财务分析 Skill 的入口、规则与数据口径。
- `docs/比赛要求对照.md`：比赛要求与代码落点的对应关系。

## 13. 团队与许可

本项目为团队协作完成，仓库由 [@lllyyz418](https://github.com/lllyyz418) 维护。

仓库未附开源许可证，默认保留所有权利；如需复用代码、文档或截图，请先联系维护者。第三方依赖与运行时组件的许可证见 `docs/实际依赖与许可证.json`、`docs/Conda组件许可证/` 和 `docs/frontend-licenses/`。
