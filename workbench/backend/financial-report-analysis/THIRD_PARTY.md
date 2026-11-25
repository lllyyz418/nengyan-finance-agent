# 第三方组件记录

| 组件 | 当前测试版本 | 来源 | 许可证 | 使用范围 |
| --- | --- | --- | --- | --- |
| pdfplumber | 0.11.9 | https://github.com/jsvine/pdfplumber | MIT | 年报和公告 PDF 文本、表格与位置读取。 |
| RapidOCR | 3.9.2 | https://github.com/RapidAI/RapidOCR | Apache-2.0 | 扫描公告与选定年度摘要页的可选 OCR，随安装包包含模型。 |
| ONNX Runtime | 1.30.0 | https://github.com/microsoft/onnxruntime | MIT | RapidOCR 的可选 CPU 推理后端。 |
| pypdfium2 | 5.13.0 | https://github.com/pypdfium2-team/pypdfium2 | BSD-3-Clause / Apache-2.0（含底层依赖通知） | 将扫描 PDF 页面渲染为 OCR 图像。 |

`financial_engine/core.py` 只使用 Python 标准库。公告目录和金额规则也是本项目代码；本项目没有复制 PDF/OCR 项目的源码或模型到交付压缩包。交付和现场展示时应记录实际安装版本；如升级或替换解析器，重新运行三个年报样本和公告样本的验证，并按第三方许可证要求保留相应通知。

`financial_engine/disclosure.py` 通过 Python 标准库读取巨潮资讯网公开披露目录及 PDF；`financial_engine/peer.py` 的比较计算也只用标准库。这两部分没有复制外部开源项目代码。网站查询接口可能调整，调用方应读取 `retrieval` 状态并保留下载文件及来源清单。

跨报告风险结构参考 [FinanceToolkit](https://github.com/JerBouma/FinanceToolkit) 的透明公式组织思路；项目未将其作为运行依赖，也未复制其源码或财务数据。财务筛查解释参考 CFA Institute 财报质量分析框架，项目自定义规则的条件和限制见 `CONTEXT_RISK.md`。
