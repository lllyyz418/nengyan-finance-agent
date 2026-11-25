import {readFileSync} from 'node:fs'
import {createRequire} from 'node:module'
import assert from 'node:assert/strict'
import test from 'node:test'
import ts from 'typescript'
import React from 'react'
import {renderToStaticMarkup} from 'react-dom/server'
const require=createRequire(import.meta.url)
function compile(path,deps={}){
  const code=ts.transpileModule(readFileSync(new URL(path,import.meta.url),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText
  const module={exports:{}};new Function('require','module','exports',code)(name=>name.endsWith('.css')?{}:deps[name]??require(name),module,module.exports);return module.exports
}
const labels=compile('../src/data/financialLabels.ts')
const {riskPresentation}=compile('../src/data/analysisPresentation.ts',{'./financialLabels':labels})
const {RiskPanel}=compile('../src/components/RiskPanel.tsx')
const fmt=(v)=>String(v)
function item(status){return {assessment_id:'policy',rule_id:'r_and_d_capitalization_policy',status,screen_type:'policy_observation',
  summary:'资本化比例上升，请核对依据',review_metric:'资本化比例上升',review_scope:'同口径研发投入比较',
  review_details:['2025年资本化比例：20.00%','2024年资本化比例：10.00%'],
  evidence_links:[{id:'d:policy:1',label:'研发投入原表'},{id:'d:2025:cap',label:'2025年资本化金额'}]}}

test('new policy statuses do not masquerade as triggered risk or unsupported parser',()=>{
  for(const [status,label] of [['reviewed','已完成复核'],['review_required','发现变化，需复核'],['manual_review','需人工确认']]){
    const risk=riskPresentation({analysis:{risk_assessments:[item(status)]}},fmt)[0]
    assert.equal(risk.status,label);assert.equal(risk.policyReview,true);assert.equal(risk.evidenceLinks.length,2)
    assert.equal(risk.metric,'资本化比例上升');assert.ok(!JSON.stringify(risk).includes('PARSER_NOT_COVERED'))
    const html=renderToStaticMarkup(React.createElement(RiskPanel,{risks:[risk],onEvidence:()=>{}}))
    assert.ok(html.includes('披露复核'));assert.ok(html.includes(label));assert.ok(!html.includes('0 / 0'))
  }
})
test('policy missing reasons are Chinese, period-specific, and preserve evidence',()=>{
  const raw={...item('insufficient_data'),missing_or_invalid:[{period:'2025',code:'r_and_d_capitalized_expenditure',reason:'POLICY_CELL_EMPTY_OR_UNREADABLE'},
    {period:'2024',code:'fixed_asset_depreciation_policy_previous',reason:'POLICY_PRIOR_REPORT_NEEDED'},
    {period:'2025',code:'r_and_d_total_investment',reason:'POLICY_COLUMNS_UNCONFIRMED'}]}
  const risk=riskPresentation({analysis:{risk_assessments:[raw]}},fmt)[0]
  assert.equal(risk.status,'数据不足');assert.ok(risk.missing[0].includes('不按零处理'))
  assert.ok(risk.missing[1].includes('历史回溯'));assert.ok(risk.missing[2].includes('列位置'))
  assert.ok(!risk.missing.join('').includes('POLICY_'));assert.equal(risk.evidenceId,'d:policy:1')
})
test('legacy saved unsupported policies and interim applicability stay honest',()=>{
  const raw={rule_id:'depreciation_policy_change',status:'not_evaluated',reason_code:'PARSER_NOT_COVERED'}
  let risk=riskPresentation({analysis:{risk_assessments:[raw]}},fmt)[0]
  assert.equal(risk.status,'尚未评估');assert.equal(risk.metric,'解析能力待覆盖')
  risk=riskPresentation({analysis:{risk_assessments:[{...raw,reason_code:'PERIOD_RULE_NOT_APPLICABLE'}]}},fmt)[0]
  assert.equal(risk.metric,'本期规则不适用')
})
test('source actions include every supplied policy evidence link',()=>{
  const source=readFileSync(new URL('../src/components/RiskPanel.tsx',import.meta.url),'utf8')
  assert.ok(source.includes('risk.evidenceLinks.map'));assert.ok(source.includes('onEvidence(link.id)'))
  const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8')
  assert.ok(app.includes('policyFollowups'));assert.ok(app.includes('项政策待复核'))
})
