import {readFileSync} from 'node:fs'
import {createRequire} from 'node:module'
import assert from 'node:assert/strict'
import test from 'node:test'
import ts from 'typescript'
const require=createRequire(import.meta.url)
function compile(path,deps={}) {
  const code=ts.transpileModule(readFileSync(new URL(path,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText
  const module={exports:{}};new Function('require','module','exports',code)(name=>deps[name]??require(name),module,module.exports);return module.exports
}
const labels=compile('../src/data/financialLabels.ts')
const {riskPresentation,metricPresentation,unavailableExplanation}=compile('../src/data/analysisPresentation.ts',{'./financialLabels':labels})
const format=(v,u)=>u==='percent'?`${Number(v).toFixed(2)}%`:u==='percentage_points'?`${Number(v).toFixed(2)} 个百分点`:u==='CNY'?`${v} 元`:`${v} ${u}`
test('all real condition identifiers become Chinese with observed numbers',()=>{
  const codes=['gross_inventory_growth_above_revenue_growth','inventory_allowance_share_increased','gross_margin_declined','over_1y_share_increased','fixed_assets_cip_share_increased','simplified_free_cash_flow_negative']
  const risks=riskPresentation({analysis:{risk_assessments:[{rule_id:'inventory_margin_pressure',status:'not_triggered',conditions:codes.map(code=>({condition_code:code,passed:false,observed:{current_percent:'10',previous_percent:'12'}}))}]}},format)
  assert.ok(!/[a-z]+_[a-z]+/.test(JSON.stringify(risks[0].details)))
  assert.ok(risks[0].details[0].includes('10.00%'))
})
test('missing inputs preserve years, deduplicate, and never claim no disclosure',()=>{
  const risks=riskPresentation({analysis:{risk_assessments:[{period:'2025',rule_id:'inventory_margin_pressure',status:'insufficient_data',conditions:[],missing_or_invalid:[{period:'2025',code:'inventory_gross',reason:'MISSING_INPUT'},{period:'2024',code:'inventory_gross',reason:'MISSING_INPUT'},{period:'2025',code:'inventory_gross',reason:'MISSING_INPUT'}]}]}},format)
  assert.equal(risks[0].metric,'待补齐 2 项输入');assert.equal(risks[0].missing.length,2)
  assert.ok(risks[0].missing[0].includes('2025年度存货原值'));assert.ok(!risks[0].metric.includes('0 / 0'))
  assert.ok(risks[0].missing[0].includes('不代表公司未披露'))
})
test('cash displays the saved threshold, run, and meaningful partial observation',()=>{
  const analysis={analysis_options:{minimum_negative_cash_quarters:3},facts:[-1,2,-3,-4].map((v,i)=>({document_id:'d',period:`2025Q${i+1}`,metric_code:'operating_cash_flow',quality:'extracted',value:String(v)})),risk_assessments:[{document_id:'d',period:'2025',rule_id:'cash_collection_pressure',status:'insufficient_data',missing_or_invalid:[{period:'2025',code:'net_profit',reason:'MISSING_INPUT'}]}]}
  const risk=riskPresentation({analysis},format)[0]
  assert.ok(risk.cashSetting.includes('连续 3 季度'))
  assert.ok(risk.details[0].includes('连续 2 季度'))
  assert.equal(risk.status,'数据不足')
})
test('comparison is same metric, period and unit; deltas must be engine supplied',()=>{
  const item={document_id:'d',period:'2025',metric_code:'gross_margin',status:'calculated',value:'30',unit:'percent',comparison:{document_id:'d',period:'2024',status:'calculated',value:'25',unit:'percent',absolute_change:'5',change_unit:'percentage_points',input_fact_ids:['prior']}}
  const row=metricPresentation(item,{analysis:{}},format)
  assert.equal(row.previous,'25.00%');assert.equal(row.change,'5.00 个百分点');assert.equal(row.comparisonPeriod,'2024年度')
  delete item.comparison.absolute_change;assert.equal(metricPresentation(item,{analysis:{}},format).change,null)
  item.comparison.document_id='other';assert.equal(metricPresentation(item,{analysis:{}},format).previous,null)
})
test('negative-base amount change explains losses, keeps evidence and never supplies a fake percent',()=>{
  const text=unavailableExplanation({reason_code:'NONPOSITIVE_INPUT',detail:'2025Q3:attributable_net_profit must be positive',amount_comparison:{current_period:'2025Q4',previous_period:'2025Q3',current:'-7',previous:'-4',absolute_change:'-3'}},{},format)
  assert.ok(text.includes('2025年第3季度归母净利润'));assert.ok(text.includes('亏损扩大'));assert.ok(!text.includes('must be'))
  const source=readFileSync(new URL('../src/components/FinancialTable.tsx',import.meta.url),'utf8')
  assert.ok(source.includes('i.row.original.unavailableReason&&'));assert.ok(source.includes('i.row.original.evidenceId&&'))
})
test('history fills a missing prior metric without overriding a same-report comparison; restatement withholds change',()=>{
  const item={document_id:'d25',period:'2025',metric_code:'revenue_yoy',status:'calculated',value:'20.00',unit:'percent',comparison:{document_id:'d25',period:'2024',status:'unavailable',reason_code:'MISSING_INPUT',missing_inputs:[{period:'2023',code:'revenue'}]}}
  const history={comparison:{series:{revenue_yoy:[{year:2024,document_id:'d24',metric_id:'m24',unit:'percent',value:'10',input_fact_ids:['f24']},{year:2025,document_id:'d25',metric_id:'m25',unit:'percent',value:'20'}]},changes:[{metric_code:'revenue_yoy',from_year:2024,to_year:2025,from_metric_id:'m24',to_metric_id:'m25',status:'calculated',absolute_change:'10',unit:'percentage_points'}]}}
  let row=metricPresentation(item,{analysis:{},payload:{history}},format)
  assert.equal(row.previous,'10.00%');assert.equal(row.change,'10.00 个百分点');assert.equal(row.changeNote,'')
  history.comparison.changes[0]={...history.comparison.changes[0],status:'unavailable',reason_code:'RESTATED_COMPARATIVE_CONFLICT'}
  row=metricPresentation(item,{analysis:{},payload:{history}},format)
  assert.equal(row.previous,'10.00%');assert.equal(row.change,null);assert.ok(row.changeNote.includes('口径存在差异'))
  history.comparison.series.revenue_yoy.push({...history.comparison.series.revenue_yoy[0]})
  assert.equal(metricPresentation(item,{analysis:{},payload:{history}},format).previous,null)
})
test('legacy actual record is localized without pretending old extraction was repaired',()=>{
  const old=JSON.parse(readFileSync(new URL('../backend/data/results/ef059d8ccca24b0ab20b34a4ca06d60f.json',import.meta.url),'utf8'))
  const risks=riskPresentation(old,format)
  assert.equal(risks[0].status,'数据不足')
  assert.ok(risks[1].missing.every(text=>!text.includes('MISSING_INPUT')&&!text.includes('inventory_gross')))
  const row=old.analysis.unavailable_metrics.find(item=>item.metric_code==='consolidated_net_margin')
  assert.ok(metricPresentation(row,old,format).unavailableReason.includes('合并利润表'))
})
test('risk component hides internal rule ids and removed disclaimer',()=>{
  const {renderToStaticMarkup}=require('react-dom/server'),React=require('react')
  const {RiskPanel}=compile('../src/components/RiskPanel.tsx',{'./analysisDiagnostics.css':{}})
  const html=renderToStaticMarkup(React.createElement(RiskPanel,{risks:[{id:'r',ruleId:'cash_collection_pressure',title:'现金回款压力',summary:'观察',status:'未触发',metric:'1 / 3 条件满足',trigger:'合并净利润为正',details:[],cashSetting:'本次任务阈值：连续 3 季度为负'}],onEvidence(){}}))
  assert.ok(!html.includes('cash_collection_pressure'));assert.ok(!html.includes('方向性复核信号，不构成风险定论'));assert.ok(html.includes('本次任务阈值'))
})
