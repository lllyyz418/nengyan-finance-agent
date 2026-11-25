import {readFileSync} from 'node:fs'
import assert from 'node:assert/strict'
import {test} from 'node:test'
import ts from 'typescript'

const source=readFileSync(new URL('../src/data/reportIdentity.ts',import.meta.url),'utf8')
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText
const {reportSecurityCode}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
test('title and peer target use the recovered code from the same response',()=>{
  const result={analysis:{documents:[{company_name:'宁德时代新能源科技股份有限公司'}]},payload:{identity:{stock_code:'300750',status:'verified_history'}}}
  assert.equal(reportSecurityCode(result),'300750')
  assert.equal(/^\d{6}$/.test(reportSecurityCode(result)),true)
})
test('legacy history metadata, empty stock_code and leading zero codes',()=>{
  assert.equal(reportSecurityCode({analysis:{documents:[{stock_code:'',security_code:'002074'}]}}),'002074')
  assert.equal(reportSecurityCode({history_backfill:{stock_code:'300750'}}),'300750')
})
test('conflicts never choose the first code or enable automatic comparison',()=>{
  assert.equal(reportSecurityCode({analysis:{documents:[{security_code:'300750',stock_code:'002074'}]}}),'代码存在冲突')
  assert.equal(reportSecurityCode({analysis:{documents:[{security_code:'300750'}]},report_identity:{status:'conflict'}}),'代码存在冲突')
})
test('unknown or unverified identity retains manual confirmation, no hardcoded company',()=>{
  assert.equal(reportSecurityCode({analysis:{documents:[{company_name:'未知公司'}]}}),'证券代码待确认')
  assert.equal(reportSecurityCode({payload:{identity:{stock_code:'300750',status:'unverified'}}}),'证券代码待确认')
})
