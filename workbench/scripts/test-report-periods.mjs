import {readFileSync,existsSync} from 'node:fs'
import {createRequire} from 'node:module'
import {fileURLToPath} from 'node:url'
import path from 'node:path'
import ts from 'typescript'
import test from 'node:test'
import assert from 'node:assert/strict'
const require=createRequire(import.meta.url),root=fileURLToPath(new URL('../',import.meta.url)),cache=new Map()
function load(file){
  if(cache.has(file))return cache.get(file).exports
  const module={exports:{}};cache.set(file,module)
  const source=readFileSync(file,'utf8').replace(/import\.meta\.env\.VITE_API_BASE_URL/g,'"/api"')
  const code=ts.transpileModule(source,{fileName:file,compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText
  new Function('require','module','exports',code)(name=>{
    if(name.endsWith('.css'))return {}
    if(name==='./http')return {requestJson:async(url,init)=>({url,body:JSON.parse(init.body)})}
    if(name.startsWith('.')){const target=path.resolve(path.dirname(file),name);const match=[target,target+'.ts',target+'.tsx',target+'.json'].find(existsSync);return match.endsWith('.json')?JSON.parse(readFileSync(match,'utf8')):load(match)}
    return require(name)
  },module,module.exports);return module.exports
}
const periods=load(path.join(root,'src/data/reportPeriods.ts'))
const {financeApi,resultToSnapshot}=load(path.join(root,'src/services/financeApi.ts'))
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server')
test('controls separate type/year, show only Q1/Q3 for quarter reports',()=>{
  const {ReportControls}=load(path.join(root,'src/components/ReportControls.tsx'))
  const props={type:'quarterly',year:2025,quarter:3,segment:'新能源',segments:['新能源'],loading:false,canSearch:true,onType(){},onYear(){},onQuarter(){},onSegment(){},onUpload(){},onSearch(){}}
  const html=renderToStaticMarkup(React.createElement(ReportControls,props))
  assert.ok(html.includes('报告类型'));assert.ok(html.includes('报告年度'));assert.ok(html.includes('第三季度'));assert.ok(!html.includes('第四季度'))
  assert.ok(!renderToStaticMarkup(React.createElement(ReportControls,{...props,type:'semiannual'})).includes('报告季度'))
})
test('public searches carry requested filing type and exact quarter',async()=>{
  const settings={lookbackYears:2,minimumNegativeCashQuarters:3,nonrecurringRatioThreshold:30}
  const quarterly=await financeApi.createOnlineTask('300750',2025,settings,undefined,'quarterly',3)
  assert.equal(quarterly.body.report_type,'quarterly');assert.equal(quarterly.body.report_quarter,3)
  const h1=await financeApi.createOnlineTask('300750',2025,settings,undefined,'semiannual')
  assert.equal(h1.body.report_type,'semiannual');assert.equal(h1.body.report_quarter,null)
})
test('interim snapshots preserve quarter, Chinese period, and unannualized formula',()=>{
  const snapshot=resultToSnapshot({analysis:{period_basis:'cumulative',documents:[{report_type:'quarterly',report_year:2025,report_quarter:3,security_code:'300750'}],metrics:[{metric_code:'return_on_assets',period:'2025YTD9',value:'2',unit:'percent',status:'calculated'}]},payload:{}})
  assert.equal(snapshot.period,'2025年第三季度报告');assert.equal(snapshot.document.reportQuarter,3)
  assert.ok(snapshot.financialMetrics[0].formula.includes('未年化'))
})
test('metric comparison keeps prior same H1/9M/Q1, not prior calendar quarter',()=>{
  const {metricPresentation}=load(path.join(root,'src/data/analysisPresentation.ts'))
  for(const [current,previous] of [['2025H1','2024H1'],['2025YTD9','2024YTD9'],['2025Q1','2024Q1']]){
    const item={document_id:'d',period:current,metric_code:'gross_margin',unit:'percent',status:'calculated',value:'25',comparison:{document_id:'d',period:previous,status:'calculated',value:'20',unit:'percent',absolute_change:'5',change_unit:'percentage_points'}}
    const row=metricPresentation(item,{analysis:{period_basis:'cumulative'}},v=>String(v))
    assert.equal(row.previous,'20');assert.equal(row.change,'5');assert.ok(row.comparisonNote.includes('上年同期'))
  }
})
test('peer submissions bind the requested cumulative/single basis',async()=>{
  const reply=await financeApi.createPeerTask('task',{peers:[{code:'300014'},{code:'002594'}]},'300750','single')
  assert.equal(reply.body.period_basis,'single')
})
test('single-quarter UI explains original cumulative report and export basis',()=>{
  const {PeriodMetrics}=load(path.join(root,'src/components/PeriodMetrics.tsx'))
  const html=renderToStaticMarkup(React.createElement(PeriodMetrics,{interim:true,label:'2025年前三季度',singleAvailable:true,basis:'single',onBasis(){},data:[],onEvidence(){},company:'测试',period:'2025Q3 · 单季',demo:false}))
  assert.ok(html.includes('单季度'));assert.ok(html.includes('累计口径'));assert.ok(html.includes('不自动年化'))
})
test('years are generated dynamically; upload supports quarter and confirms a period mismatch',()=>{
  assert.equal(periods.reportYears()[0],new Date().getFullYear())
  const source=readFileSync(path.join(root,'src/components/UploadDialog.tsx'),'utf8')
  assert.ok(source.includes('确认切换并查看结果'));assert.ok(source.includes('季报'))
  const app=readFileSync(path.join(root,'src/App.tsx'),'utf8')
  assert.ok(app.includes("const metricView=isReal&&periodBasis==='single'"))
})
