# 新能源财务洞察 Agent

面向新能源行业上市公司的财务披露分析工作台。每次任务以一家目标公司为中心，可扩展历史纵向分析与人工指定同行比较。前端使用 React + TypeScript，后端使用 FastAPI，并已接入 `financial-engine v0.17`。

## 已实现

- 公司名称/证券代码搜索与已验证样本标记。
- 本地 PDF 上传、后台任务进度、结果读取和错误提示。
- 公开报告在线分析任务接口（巨潮资讯，需联网）。
- 年报、半年报和公告自动路由。
- 财务事实、指标、不可计算原因、风险筛查和原文证据映射。
- Agent Markdown 报告；未配置模型密钥时自动使用确定性模板报告。
- 扫描页 OCR、复核模板和提交人工确认的后端接口。
- 扫描件人工复核工作台界面。
- 演示数据与真实任务结果明确区分。

## 第一次安装

### 1. 后端

在 VS Code 的 Terminal 中执行：

```powershell
conda activate D:\marktext\dcf-agent-env
cd C:\Users\乐乐\Documents\Codex\2026-09-29\agent-web\outputs\new-energy-finance-agent
python -m pip install -r .\backend\requirements.txt
```

如果要识别扫描版 PDF，再安装 OCR 组件：

```powershell
python -m pip install -r .\backend\financial-report-analysis\requirements-ocr.txt
```

### 2. 前端

项目已经包含前端依赖。如果换到新电脑，执行：

```powershell
pnpm install
```

## 每次启动

需要两个 Terminal。

Terminal 1 启动后端：

```powershell
conda activate D:\marktext\dcf-agent-env
cd C:\Users\乐乐\Documents\Codex\2026-09-29\agent-web\outputs\new-energy-finance-agent\backend
python -m uvicorn app:app --host 127.0.0.1 --port 8001 --reload
```

Terminal 2 启动前端：

```powershell
cd C:\Users\乐乐\Documents\Codex\2026-09-29\agent-web\outputs\new-energy-finance-agent
pnpm dev
```

打开 `http://127.0.0.1:5173`。接口说明位于 `http://127.0.0.1:8001/docs`。

## 可选：启用 DeepSeek 报告

复制 `backend/.env.example` 为 `backend/.env`，在本机填写新的 `DEEPSEEK_API_KEY`。不要把 `.env` 上传或发给其他人。没有密钥时，PDF 解析、指标、风险和证据仍然正常运行，只使用模板报告。

## 主要目录

- `src/components`：工作台、图表、风险、证据、Agent 报告和扫描复核界面。
- `src/services/financeApi.ts`：真实后端请求与结果适配。
- `src/data/mockData.ts`：仅在主动点击“查看演示”时展示的模拟样例，不作为真实分析失败或缺失时的回退数据。
- `backend/app.py`：FastAPI、分析任务、搜索、上传、证据和复核接口。
- `backend/financial-report-analysis`：原始 v0.17 财务分析引擎及测试。
- `backend/data`：本机上传文件和公开报告缓存；不应提交到代码仓库。

### 本地数据实际保存位置

- 上传的 PDF：`backend/data/uploads/`
- “搜索公开报告”下载的公开披露文件：`backend/data/report_cache/`
- 完整分析结果 JSON：`backend/data/results/`
- 任务状态和任务结果备份：`backend/data/tasks/`

新任务不再只保存在运行内存中，后端重启后仍能从本地 JSON 恢复。左侧“披露与管理 → 历史记录”提供任务列表、搜索筛选、分页、恢复原分析结果、原文件入口、Excel/PDF和文字报告下载。

### 历史记录

- 收藏、研究备注、回收站状态随任务保存；移入回收站不删除PDF或分析结果。
- 新任务记录原请求和OCR/分析配置，可使用“重新分析”创建新版本；旧任务未记录配置时提示重新设置。
- 后端重启后，尚未完成的任务标记为“已中断”，不自动续跑；已完成任务仍可打开。
- 扫描候选和公告结果可重新打开；扫描候选仍需人工确认，历史记录不替代复核。
- “历史分析”展示跨年度财务数据，“历史记录”管理过去执行的任务；真实历史任务不使用演示趋势曲线。
- 新接口：`GET /api/history`、`PATCH /api/history/{id}`、`POST /api/history/{id}/retry`、`GET /api/history/{id}/report`。
- 后端修改后，在原后端终端按 Ctrl+C，再运行 `python -m uvicorn app:app --host 127.0.0.1 --port 8001`。前端仍使用原 Vite 地址。

## 当前边界

### 财务分析报告与旧版摘要

年报和半年报的财务分析报告默认显示“旧版财务摘要”：按增长与盈利、利润率、现金流、营运效率与回报、资产质量五组展示指标，再展示已触发、未触发和未评估的风险规则及证据限制。此视图由已保存的财务引擎结果排版，不会补造缺失数据，也不会重新请求模型或下载报告。

报告顶部可切换到“DeepSeek 生成原文”（未使用模型时为“原始模板报告”）。复制报告和导出 PDF 使用当前选择的视图；原有 AI 原文及历史结果文件不会被覆盖。已有历史报告可重新打开直接使用摘要，无需重新分析。新生成的 DeepSeek 原文也采用固定分组提示，实际措辞仍可能不同。

本次修改需重启后端并刷新网页；健康接口版本为 `report-summary-1`。PDF 保持白底黑字、表格和 A4 页面。

### 初次打开与演示模式

首次打开或刷新默认“等待分析”：指标显示“—”，不显示风险结论、趋势曲线或示例证据，指标导出禁用。点击“查看演示”才展示模拟样例；退出后回到等待状态，或返回原先载入的真实结果。模拟样例不会保存为真实任务。

打开真实历史任务或完成新的财报分析后，切换为真实结果。历史记录和原文件保留；日志不再自动选中旧任务，可手动选择。查看演示不覆盖真实结果，演示证据明确标注为模拟。

右上角“返回工作台”可从历史列表或已打开的报告返回总览，并恢复等待分析；只重置当前展示和所选日志，不删除保存的报告及原文件。手机端也保留完整文字按钮。

状态回归测试：`node scripts/test-analysis-state.mjs`。

### 运行日志

页面最底部及左侧“运行日志”入口可查看任务真实执行事件，按任务切换、按级别或关键词筛选、查看安全详情、复制和下载 TXT。处理中每 2 秒增量刷新，完成、失败或待复核时停止。

日志保存在 `backend/data/logs/<任务编号>.jsonl`，重启不会丢失。旧任务没有详细日志时不会补造；财务风险与运行错误分别展示。新接口为 `GET /api/tasks/{id}/logs` 和 `GET /api/tasks/{id}/logs/download`。新增接口需要重启后端。

日志和历史记录回归测试：在 backend 目录运行 `python -m unittest test_history_api test_task_logs -v`。

### 能力范围

- 在线报告检索依赖巨潮资讯公开接口，网站不可用时任务会给出明确错误。
- 文本型 PDF 需要 `pdfplumber`；扫描页另外需要 OCR 依赖。
- 扫描候选必须由真实人员确认，模型不能替代人工复核。
- 当前没有用户数据库，任务与结果以本地 JSON 保存，适合单机演示，不适合多人同时使用。
- 公告已支持上传本地 PDF 并识别投资、扩产、工程中标和产业基金事件；在线批量搜索公告目录尚未接入网页。
- 扫描件已支持上传、指定 PDF 物理页码和生成 OCR 候选；正式进入指标计算前仍需人工确认。
- 风险规则是方向性筛查，不构成审计结论、信用评级或投资建议。
