import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import assert from 'node:assert/strict'
import { test } from 'node:test'
import ts from 'typescript'
const require=createRequire(import.meta.url)
function compile(path,dependencies={}) {
  const source=readFileSync(new URL(path,import.meta.url),'utf8')
  const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,esModuleInterop:true,jsx:ts.JsxEmit.ReactJSX}}).outputText
  const module={exports:{}}
  new Function('require','module','exports',compiled)(name=>dependencies[name]??require(name),module,module.exports)
  return module.exports
}
const labels=compile('../src/data/financialLabels.ts')
const glossary=JSON.parse(readFileSync(new URL('../shared/reportTerms.json',import.meta.url),'utf8'))
const {localizeReport}=compile('../src/data/reportLanguage.ts',{'./financialLabels':labels,'../../shared/reportTerms.json':glossary})

test('screenshot metrics, missing inputs, warnings and internal flags become Chinese',()=>{
  const original='`total_profit_yoy`：MISSING_INPUT（2025:total_profit, 2024:total_profit）。PDF第165页 aging 表、第172页 inventory 表。PARSER_NOT_COVERED；`materiality_assessed: false`。'
  const text=localizeReport(original)
  for(const word of ['利润总额同比增长率','缺少计算所需数据','2025 年的利润总额','2024 年的利润总额','应收账款账龄明细 表','存货明细 表','当前解析器暂未覆盖','尚未评估变化的重要性'])assert.ok(text.includes(word),word)
  for(const code of ['total_profit','MISSING_INPUT','aging','inventory','PARSER_NOT_COVERED','materiality_assessed'])assert.ok(!text.includes(code))
  assert.ok(original.includes('total_profit_yoy'),'Original input is not mutated')
})
test('replace complete identifiers only, preserve negative values and all precision',()=>{
  assert.equal(localizeReport('revenue_yoy = -9.400001%；not_triggered；not_evaluated'),'营业收入同比增长率 = -9.400001%；未触发；尚未评估')
  assert.equal(localizeReport('custom_total_profit_yoy_extension'),'custom_total_profit_yoy_extension')
  assert.equal(localizeReport('2025Q4:operating_cash_flow'),'2025 年第 4 季度经营活动现金流量净额')
})
test('negative-base explanations stay Chinese in report and copy/PDF presentation',()=>{
  assert.equal(localizeReport('NONPOSITIVE_INPUT：2025Q3:attributable_net_profit must be positive'),'计算基数非正，不满足当前公式要求：2025 年第 3 季度归母净利润 须为正数')
})
test('protect source URLs, file names, fact identifiers and file hashes',()=>{
  const raw='https://example.test/revenue_yoy/PARSER_NOT_COVERED.pdf total_profit.pdf total_profit-财报.pdf abcdef1234567890:2025:total_profit'
  assert.equal(localizeReport(raw),raw)
})
test('localization is idempotent and covers every known metric and fact',()=>{
  const codes=Object.keys({...labels.factNames,...labels.metricNames}).join(' ')
  assert.ok(!/[a-z]+_[a-z_]+/.test(localizeReport(codes)))
  assert.equal(localizeReport(localizeReport(codes)),localizeReport(codes))
})
test('actual report renderer and copy/PDF inputs use the same Chinese report',()=>{
  const {AgentReport}=compile('../src/components/AgentReport.tsx',{'../data/reportLanguage':{localizeReport},'../services/reportExport':{exportReportPdf(){throw new Error('No export during SSR test')}}})
  const React=require('react'),{renderToStaticMarkup}=require('react-dom/server')
  const html=renderToStaticMarkup(React.createElement(AgentReport,{markdown:'| 指标 | 原因 |\n|---|---|\n| total_profit_yoy | PARSER_NOT_COVERED |\n\n`materiality_assessed: false`',generator:'deepseek'}))
  assert.ok(html.includes('利润总额同比增长率'))
  assert.ok(html.includes('当前解析器暂未覆盖'))
  assert.ok(html.includes('尚未评估变化的重要性'))
  assert.ok(!html.includes('PARSER_NOT_COVERED'))
  const source=readFileSync(new URL('../src/components/AgentReport.tsx',import.meta.url),'utf8')
  assert.ok(source.includes('writeText(displayMarkdown)'))
  assert.ok(source.includes('renderMarkdown(displayMarkdown)'))
  assert.ok(source.includes('exportReportPdf(reportRef.current'))
})

test('old-style summary is default without overwriting or mislabelling the AI original',()=>{
  const {AgentReport}=compile('../src/components/AgentReport.tsx',{'../data/reportLanguage':{localizeReport},'../services/reportExport':{exportReportPdf(){throw new Error('No export during SSR test')}}})
  const React=require('react'),{renderToStaticMarkup}=require('react-dom/server')
  const html=renderToStaticMarkup(React.createElement(AgentReport,{summary:'# 旧版财务分析摘要\n\n## 2.5 资产质量\n\n| 指标 | 数值 |\n|---|---|\n| 存货 | 7.55 |',markdown:'# AI原始正文不应出现在摘要视图',generator:'deepseek'}))
  assert.ok(html.includes('旧版财务分析摘要'));assert.ok(html.includes('2.5 资产质量'))
  assert.ok(!html.includes('AI原始正文不应出现在摘要视图'))
  assert.ok(html.includes('旧版结构 · 引擎数据排版'))
  assert.ok(html.includes('DeepSeek 生成原文'))
  const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8')
  assert.ok(app.includes("setReportSummary(result.report_summary_markdown??'')"))
})
