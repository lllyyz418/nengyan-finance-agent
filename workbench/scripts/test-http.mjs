import {readFileSync} from 'node:fs'
import assert from 'node:assert/strict'
import {test} from 'node:test'
import ts from 'typescript'

const source=readFileSync(new URL('../src/services/http.ts',import.meta.url),'utf8')
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.ESNext,target:ts.ScriptTarget.ES2022}}).outputText
const {requestJson}=await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`)
async function mocked(run){
  const originalFetch=globalThis.fetch,originalWindow=globalThis.window
  const timers=new Map();let next=0
  globalThis.window={setTimeout:(callback,delay)=>{timers.set(++next,{callback,delay});return next},clearTimeout:id=>timers.delete(id)}
  try {await run(timers)}finally{globalThis.fetch=originalFetch;if(originalWindow===undefined)delete globalThis.window;else globalThis.window=originalWindow}
}
test('stalled history read becomes a useful error and releases timeout',async()=>mocked(async timers=>{
  let calls=0
  globalThis.fetch=async(url,{signal})=>{calls++;return new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true}))}
  const pending=requestJson('/api/history',{}, {timeoutMs:12000,timeoutMessage:'历史读取超时，请重试'})
  assert.equal([...timers.values()][0].delay,12000)
  ;[...timers.values()][0].callback()
  await assert.rejects(pending,/历史读取超时/)
  assert.equal(calls,1,'timeout must never automatically repeat an operation')
  assert.equal(timers.size,0)
}))
test('caller cancellation propagates to fetch and removes timer',async()=>mocked(async timers=>{
  const controller=new AbortController()
  globalThis.fetch=async(url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(signal.reason),{once:true}))
  const pending=requestJson('/api/history',{signal:controller.signal})
  controller.abort()
  await assert.rejects(pending,{name:'AbortError'})
  assert.equal(timers.size,0)
}))
test('successful reads and HTTP errors both clear timers',async()=>mocked(async timers=>{
  globalThis.fetch=async()=>({ok:true,json:async()=>({total:4})})
  assert.deepEqual(await requestJson('/api/history'),{total:4})
  assert.equal(timers.size,0)
  globalThis.fetch=async()=>({ok:false,status:503,json:async()=>({detail:'历史服务暂时不可用'})})
  await assert.rejects(requestJson('/api/history'),/历史服务暂时不可用/)
  assert.equal(timers.size,0)
}))
test('posting a task is never automatically timed out or resubmitted',async()=>mocked(async timers=>{
  let calls=0
  globalThis.fetch=async()=>{calls++;return {ok:true,json:async()=>({id:'test-only'})}}
  assert.equal((await requestJson('/api/tasks/upload',{method:'POST'})).id,'test-only')
  assert.equal(calls,1);assert.equal(timers.size,0)
}))
test('network errors are Chinese and do not suggest deleting history',async()=>mocked(async()=>{
  globalThis.fetch=async()=>{throw new TypeError('Failed to fetch')}
  await assert.rejects(requestJson('/api/history'),/无法连接后端.*已有历史文件不会丢失/)
}))

test('structured upload HTTP errors preserve safe reason and advice',async()=>mocked(async()=>{
  const issue={code:'INVALID_PDF',title:'文件不是有效的 PDF',message:'文件内容不符合 PDF 格式要求。',suggestion:'请重新导出为 PDF。',retryable:false}
  globalThis.fetch=async()=>({ok:false,status:400,json:async()=>({detail:issue})})
  await assert.rejects(requestJson('/api/tasks/upload',{method:'POST'}),error=>{
    assert.deepEqual(error.issue,issue)
    assert.match(error.message,/文件不是有效的 PDF/)
    assert.doesNotMatch(error.message,/object Object/)
    return true
  })
}))
test('non-string HTTP details are not rendered as raw validation internals',async()=>mocked(async()=>{
  globalThis.fetch=async()=>({ok:false,status:422,json:async()=>({detail:[{loc:['internal'],msg:'private/path'}]})})
  await assert.rejects(requestJson('/api/tasks/upload'),/请求失败（422）/)
}))
test('history refresh cancels obsolete reads, does not overlap, and pauses on errors',()=>{
  const history=readFileSync(new URL('../src/components/HistoryCenter.tsx',import.meta.url),'utf8')
  assert.ok(history.includes('quiet&&(pending.current||paused.current)'))
  assert.ok(history.includes('request.current?.abort()'))
  assert.ok(history.includes('paused.current=true'))
  assert.ok(history.includes('!controller.signal.aborted&&current === generation.current'))
  assert.ok(history.includes('重试读取'))
  const logs=readFileSync(new URL('../src/components/RuntimeLogs.tsx',import.meta.url),'utf8')
  assert.ok(logs.includes('controller.signal,false'))
})
