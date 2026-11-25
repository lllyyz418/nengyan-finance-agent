import {readFileSync} from 'node:fs'
import assert from 'node:assert/strict'
import {test} from 'node:test'
import ts from 'typescript'

const source=readFileSync(new URL('../src/data/uploadFailure.ts',import.meta.url),'utf8')
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText
const {uploadFailure,uploadViewState}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
const task={id:'a'.repeat(32),source:'upload',status:'processing',stage:'财务事实提取',progress:35}

test('structured backend reason and next step are kept intact',()=>{
  const issue={code:'DOCUMENT_TYPE_UNRECOGNIZED',title:'未识别为可分析的财务报告',message:'未识别到受支持的报告内容。',suggestion:'请重新选择报告文件。',retryable:false,stage:'文件识别'}
  assert.deepEqual(uploadFailure('',{...task,status:'failed',error_info:issue}),issue)
  assert.deepEqual(uploadFailure(Object.assign(new Error('http failure'),{issue})),issue)
})
test('legacy raw exception is never displayed or mistaken for unrelated PDF',()=>{
  const issue=uploadFailure(new Error("'documents' D:/private/secret-token"),{...task,status:'failed'})
  assert.equal(issue.code,'SYSTEM_ERROR')
  assert.match(issue.message,/无法确定/)
  assert.doesNotMatch(JSON.stringify(issue),/secret-token|documents/)
})
test('network loss while polling resumes the known task, not another upload',()=>{
  const issue=uploadFailure(new Error('无法连接后端'),task)
  const state=uploadViewState(task,issue,false,false)
  assert.equal(state.canResume,true)
  assert.equal(state.active,false)
  assert.equal(state.showProgress,false)
  assert.match(issue.suggestion,/不要重复提交/)
})
test('unacknowledged upload is not advertised as safe to retry',()=>{
  const issue=uploadFailure(new Error('上传请求等待超时'),null,true)
  assert.equal(issue.code,'UPLOAD_STATUS_UNKNOWN')
  assert.equal(issue.retryable,false)
  assert.match(issue.suggestion,/历史记录/)
})
test('terminal failures never spin or show completion progress',()=>{
  const issue=uploadFailure(new Error('documents'))
  const state=uploadViewState({...task,status:'failed',progress:100},issue,false,false)
  assert.equal(state.active,false)
  assert.equal(state.showProgress,false)
  assert.equal(state.terminal,true)
  assert.equal(state.canResume,false)
})
test('user interruption is a stopped state, not an error',()=>{
  const state=uploadViewState({...task,status:'interrupted'},null,false,false)
  assert.equal(state.interrupted,true)
  assert.equal(state.failed,false)
  assert.equal(state.active,false)
  assert.equal(state.showProgress,false)
})
test('preflight reasons are specific and do not offer futile retries',()=>{
  assert.equal(uploadFailure(new Error('文件超过 80 MB')).code,'FILE_TOO_LARGE')
  assert.equal(uploadFailure(new Error('仅支持 PDF 文件')).code,'FILE_TYPE_UNSUPPORTED')
  assert.equal(uploadFailure(new Error('文件内容不是有效PDF')).code,'INVALID_PDF')
  assert.equal(uploadFailure(new Error('文件超过 80 MB')).retryable,false)
})
test('upload UI separates active copy, diagnostics, retry and reselect actions',()=>{
  const dialog=readFileSync(new URL('../src/components/UploadDialog.tsx',import.meta.url),'utf8')
  assert.ok(dialog.includes('state.active&&<p className="backfill-note">'))
  assert.ok(dialog.includes('state.showProgress&&<div className="progress-track">'))
  assert.ok(dialog.includes('!state.terminal&&<button'))
  assert.ok(dialog.includes('查看诊断信息'))
  assert.ok(dialog.includes('重新选择文件'))
  assert.ok(dialog.includes('继续查看进度'))
  assert.ok(dialog.includes('if(resume&&task)'))
  assert.ok(!dialog.includes('<strong>任务未完成</strong>'))
})
test('new upload keeps existing results until a replacement is successfully loaded',()=>{
  const app=readFileSync(new URL('../src/App.tsx',import.meta.url),'utf8')
  const uploadTag=app.slice(app.indexOf('<UploadDialog'),app.indexOf('/>',app.indexOf('<UploadDialog')))
  assert.ok(uploadTag.includes('onTaskCreated={selectLogTask}'))
  assert.ok(!uploadTag.includes("switchView('empty')"))
  assert.ok(app.includes("health.api_revision==='upload-errors-1'"))
})
test('a saved report with unavailable metrics remains visible as partial, not failed or complete',async()=>{
  const apiSource=readFileSync(new URL('../src/services/financeApi.ts',import.meta.url),'utf8')
  const file=ts.createSourceFile('financeApi.ts',apiSource,ts.ScriptTarget.Latest,true)
  const printer=ts.createPrinter()
  const functions=file.statements.filter(node=>ts.isFunctionDeclaration(node)&&['displayValue','resultToSnapshot'].includes(node.name?.text))
    .map(node=>printer.printNode(ts.EmitHint.Unspecified,node,file)).join('\n')
  const body='const metricNames={},formulaNames={},localCompanies=[];function reportTypeLabel(){return "年度报告";}function reportSecurityCode(){return "000001";}function metricPresentation(){return {};}function riskPresentation(){return [];}\n'+functions
  const js=ts.transpileModule(body,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText
  const {resultToSnapshot}=await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
  const snapshot=resultToSnapshot({analysis:{documents:[{company_name:'测试公司',report_type:'annual',report_year:2025}],metrics:[],warnings:[],unavailable_metrics:[{metric_code:'revenue_yoy',value:null,reason_code:'MISSING_INPUT'}]},payload:{}})
  assert.equal(snapshot.document.status,'部分完成')
  assert.equal(snapshot.document.unavailable,1)
  assert.equal(snapshot.financialMetrics[0].current,null)
})
