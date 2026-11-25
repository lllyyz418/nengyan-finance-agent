# 公告取证分析 v1.1.0 本机合并记录

## 交付包与接入方式

来源：公告取证分析增量包_给zyc_v1.1.0_20261017.zip。

压缩包 SHA-256：`e4f8df1146acb231e68df302fe6f2073766bbc3b00e18e5ae6cfb7815907a0f8`。

包在独立目录安全解压，验证全部 13 个 payload 文件的清单哈希。没有运行包内安装脚本、修改密钥、安装依赖或整体覆盖项目。`backend/app.py` 与 `src/services/financeApi.ts` 已有较新改动，采用手工合并；其他已知基线文件在备份后引入。

原文件备份：`artifacts/announcement-integration/original/`。
原始解包：`artifacts/announcement-integration/vendor/announcement-analysis-v1.1.0/`。
逐文件检查：`artifacts/announcement-integration/inspection.json`。

## 新能力

- 基金、扩产/投资建设、其他投资、中标/合同事件的金额和限制条件、完整风险段落、证据页码。
- 公告专用的有限工具分析流程，不挤占或改写年报研究解读流程。
- 同公司、符合分析时点和披露日期要求的本地财报背景；币种/口径不一致时拒绝计算。
- 公告当日/截至今天的追问；每次单独保存新任务，原结果不覆盖。
- 原公告、财报及相关公告的受控证据链接与 SHA-256 检查。
- 工具记录、补证缺口、模型失败后的规则提取回退。

## 兼容修正

- 保留现有年报确定性摘要与独立研究解读、历史核对、响应式 PDF 工作进程及任务日志。
- 追问也接入当前 PDF 子进程运行机制；追问前核查原 PDF，阻止缺失/篡改源文件。
- 历史记录重试追问保留原问题与分析时点，不误走普通上传。
- 前端结果链接绑定最后成功结果的任务编号；失败追问不会破坏旧证据入口。
- 切换历史结果或组件卸载后，过期轮询不得覆盖新结果；补充公告任务停止按钮。
- 离线注入模型显示“未调用真实 DeepSeek”，不能冒充真实服务验收。
- 工具完整输出保存在公告结果中，运行日志不重复记录全部原文段落。

## 验收

后端：379 项测试，375 通过、4 跳过；前端：96 项全部通过；TypeScript 类型检查和隔离生产构建通过。

三份交付包中的真实 PDF，通过原上传 API、结果 API、证据 PDF、独立追问任务和历史读取验收。另使用明确标识的离线注入模型验收原生工具调用协议。

记录：`artifacts/ann-qa-20261017/acceptance.json`。
样例界面：`artifacts/announcement-integration/announcement-preview.jpg`。

**真实 DeepSeek 服务调用未验收，本轮没有消耗模型额度。** 不把离线测试视为真实服务兼容性的证明。

## 启动与当前限制

健康接口新增 `announcement_revision: evidence-agent-1.1.0`。只有看到此标记，才能确认新后端已经生效。

首次检查发现原 8001 端口运行旧版 Python 后端，Windows 拒绝当前会话结束该进程。用户随后于 2026-10-18 在原终端停止了旧后端；新版已通过受管执行会话启动（进程 24144），保留现有 5173 Vite 开发前端，没有另起或结束前端。

生效验收：8001 健康检查正常，5173 `/api/health` 返回 `announcement_revision: evidence-agent-1.1.0`；追问与证据路由已注册，54 条现有历史记录可正常读取。真实模型调用仍未验收，未消耗模型额度。

今后如只需重启后端，可在工程的 backend 目录运行：

```powershell
cd 'C:\Users\乐乐\Documents\Codex\2026-09-29\agent-web\outputs\new-energy-finance-agent\backend'
& 'D:\marktext\dcf-agent-env\python.exe' -m uvicorn app:app --host 127.0.0.1 --port 8001
```

若前后端均已关闭，需要统一启动已编译网页，可运行以下脚本：

```powershell
cd 'C:\Users\乐乐\Documents\Codex\2026-09-29\agent-web\outputs\new-energy-finance-agent'
& '.\scripts\start-workbench.ps1'
```

脚本检查端口，不结束其他进程；执行类型检查和生产构建，再以隐藏窗口启动后端 8001 和已构建前端 5173，日志与所启动进程编号位于 `.local-run/日期时间/`。它不创建开机启动、计划任务或系统服务。如 5173 已占用，应保留原前端或先在其原终端停止，再运行脚本。

该启动方式提供已编译页面，之后代码修改需重新构建。也可继续采用原来的 Vite 开发终端获取热更新。

保留现有 `.env` 配置：未启用在线公告目录搜索；缺少真实披露日期的财报仍不得用于时点分析。旧公告结果不会自动重算，需要重新上传或重新分析。

此前“100% 紧凑布局”仍是独立预览，本次没有把它应用到正式网页。
