import {readFileSync} from 'node:fs'
import assert from 'node:assert/strict'
import {test} from 'node:test'
import ts from 'typescript'

const source=readFileSync(new URL('../src/services/financeApi.ts',import.meta.url),'utf8')
const file=ts.createSourceFile('financeApi.ts',source,ts.ScriptTarget.Latest,true)
let method
function find(node){if(ts.isMethodDeclaration(node)&&node.name.getText(file)==='searchCompanies')method=node;ts.forEachChild(node,find)}
find(file)
const compiled=ts.transpileModule(`export default {${method.getText(file)}}`,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
function api(request,timers={setTimeout:()=>1,clearTimeout:()=>{}}){return new Function('request','window',`const exports={};${compiled};return exports.default`)(request,timers)}

test('local and online searches carry year, pagination and safely encoded query',async()=>{
  const calls=[]
  const client=api(async(path,options)=>{calls.push({path,options});return {items:[]}})
  await client.searchCompanies('中国三峡新能源（集团）股份有限公司',2025,20)
  await client.searchCompanies('中国核建',2024,0,true)
  assert.equal(calls[0].path.split('?')[0],'/companies')
  assert.equal(new URLSearchParams(calls[0].path.split('?')[1]).get('q'),'中国三峡新能源（集团）股份有限公司')
  assert.equal(new URLSearchParams(calls[0].path.split('?')[1]).get('offset'),'20')
  assert.equal(new URLSearchParams(calls[1].path.split('?')[1]).get('report_year'),'2024')
  assert.equal(calls[1].path.split('?')[0],'/companies/online')
})
test('stale input aborts the in-flight search and clears its timeout',async()=>{
  const controller=new AbortController();let clearCount=0,requestSignal
  const client=api(async(path,{signal})=>{requestSignal=signal;return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true}))},{setTimeout:()=>1,clearTimeout:()=>clearCount++})
  const pending=client.searchCompanies('三峡',2025,0,false,controller.signal)
  controller.abort()
  await assert.rejects(pending)
  assert.equal(requestSignal.aborted,true)
  assert.equal(clearCount,1)
})
test('timeout is surfaced as an error, not an empty candidate list',async()=>{
  let expire
  const client=api(async(path,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true})),{setTimeout:callback=>{expire=callback;return 1},clearTimeout:()=>{}})
  const pending=client.searchCompanies('测试',2025)
  expire()
  await assert.rejects(pending,/公司查询超时/)
})
test('report creation uses selected code and editing input invalidates old identity',()=>{
  const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8')
  assert.match(app,/createOnlineTask\(selectedCompanyCode,/)
  assert.doesNotMatch(app,/createOnlineTask\(companyQuery,/)
  assert.ok(app.includes("onInput={value=>{setCompanyQuery(value);setSelectedCompanyCode('')}}"))
  const selector=readFileSync(new URL('../src/components/CompanySearch.tsx',import.meta.url),'utf8')
  assert.match(selector,/current!==revision.current/)
  assert.match(selector,/if\(mode==='local'&&!result.total/)
  assert.match(selector,/加载更多/)
  assert.match(selector,/role="alert"/)
})
