import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {createRequire} from 'node:module'
import ts from 'typescript'
const require=createRequire(import.meta.url),React=require('react'),{renderToStaticMarkup}=require('react-dom/server')
function compile(file,dependencies={}){
  const code=ts.transpileModule(readFileSync(new URL(file,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText
  const module={exports:{}};new Function('require','module','exports',code)(name=>dependencies[name]??require(name),module,module.exports);return module.exports
}
const model=compile('../src/data/historyBasis.ts'),values=compile('../src/data/historyCharts.ts'),labels=compile('../src/data/financialLabels.ts'),periods=compile('../src/data/reportPeriods.ts')
function fixture(){
  const facts=['old','new'].map((id,i)=>({fact_id:id,document_id:'d'+i,value:String(100+i*20),unit:'CNY',raw_value:String(100+i*20),raw_unit:'元',sources:[{page_start:10+i}]}))
  return {basis_view:{read_only:true},analysis:{documents:[{document_id:'d0',report_year:2023,report_type:'annual'},{document_id:'d1',report_year:2024,report_type:'annual'}],facts},comparison:{basis_check_version:'precision-basis-1',restatement_checks:[
    {year:2023,metric_code:'operating_cost',status:'difference',reason_code:'OUTSIDE_PRECISION_RANGE',explanation:'超出精度范围，不直接认定重述。',original_fact_id:'old',later_comparative_fact_id:'new',difference:'20',unit:'CNY',affected_metrics:[{metric_code:'gross_margin'}]},
    {year:2023,metric_code:'revenue',status:'rounding_compatible',original_fact_id:'old',later_comparative_fact_id:'new'},
    {year:2024,metric_code:'inventory',status:'matched',original_fact_id:'old',later_comparative_fact_id:'new'},
    {year:2023,metric_code:'total_assets',status:'unavailable',original_fact_id:'old'},
  ]}}
}
function panel(filter='focus',expanded){
  let i=0;const states=[filter,'all',expanded]
  const hooks={...React,useState(){return [states[i++],()=>{}]}}
  return compile('../src/components/HistoryBasisPanel.tsx',{react:hooks,'../data/historyBasis':model,'../data/historyCharts':values,'../data/financialLabels':labels,'../data/reportPeriods':periods,'./historyBasis.css':{}}).HistoryBasisPanel
}
test('basis classification counts precision separately and puts meaningful issues first',()=>{
  const rows=model.basisRows(fixture())
  assert.deepEqual(rows.map(r=>r.status),['difference','unavailable','rounding_compatible','matched'])
  assert.deepEqual(model.basisCounts(rows),{matched:1,rounding_compatible:1,difference:1,adjusted:0,unavailable:1})
  assert.equal(rows.filter(model.focusRow).length,2)
})
test('default focus omits small differences but preserves all original values in full view',()=>{
  const html=renderToStaticMarkup(React.createElement(panel(),{history:fixture(),onEvidence(){}}))
  assert.match(html,/显示 2 \/ 4 项/);assert.match(html,/营业成本/)
  assert.doesNotMatch(html,/原披露：120 元.*2024年年度报告中的 2023年值.*原披露：120 元/)
  const full=renderToStaticMarkup(React.createElement(panel('all'),{history:fixture()}))
  assert.match(full,/显示 4 \/ 4 项/);assert.match(full,/2024年年度报告中的 2023年值/)
  assert.match(full,/精度范围内差异/);assert.match(full,/未改写/)
})
test('expanded item shows dual provenance, exact delta, affected metrics and no invented reason',()=>{
  const html=renderToStaticMarkup(React.createElement(panel('focus','2023-operating_cost-0'),{history:fixture(),onEvidence(){},onMetric(){}}))
  for(const text of ['依据与影响','查看原始披露','核对后续报告原文','+20 元','合并毛利率','对分析的影响','前期差错更正','原因保留为待核查'])assert.ok(html.includes(text),text)
  assert.match(html,/第 10 页/);assert.match(html,/第 11 页/);assert.doesNotMatch(html,/已确认公司发生重述/)
})
test('trend notes are metric-specific, retain original curves and do not mark unrelated metrics',()=>{
  const rows=model.basisRows(fixture())
  assert.match(model.basisNotice(rows,['gross_margin']),/不能把跨期变化直接解释/)
  assert.match(model.basisNotice(rows,['revenue']),/披露精度相容/)
  assert.equal(model.basisNotice(rows,['liabilities_to_assets']), '')
})
test('missing and demo states never advertise a successful financial check',()=>{
  const empty=renderToStaticMarkup(React.createElement(panel(),{}))
  assert.match(empty,/没有数据不等于一致/)
  const demo=renderToStaticMarkup(React.createElement(panel(),{demo:true,history:fixture()}))
  assert.match(demo,/演示模式不生成真实核对结论/);assert.doesNotMatch(demo,/营业成本/)
})
test('evidence and metric navigation are connected through the actual workbench',()=>{
  const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8'),backfill=readFileSync(new URL('../src/components/HistoryBackfillPanel.tsx',import.meta.url),'utf8')
  assert.match(app,/onEvidence=\{openEvidence\} onMetric=/)
  assert.match(app,/searchFocus=\{metricFocus\}/)
  assert.match(backfill,/onEvidence=\{onEvidence\} onMetric=\{onMetric\}/)
  const table=readFileSync(new URL('../src/components/FinancialTable.tsx',import.meta.url),'utf8')
  assert.match(table,/setFilter\(searchFocus.name\)/)
})
