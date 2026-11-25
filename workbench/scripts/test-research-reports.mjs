import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {createRequire} from 'node:module'
import ts from 'typescript'
const require=createRequire(import.meta.url),React=require('react')
const {renderToStaticMarkup}=require('react-dom/server')

function compile(file,dependencies={}) {
  const source=readFileSync(new URL(file,import.meta.url),'utf8')
  const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,jsx:ts.JsxEmit.ReactJSX,esModuleInterop:true}}).outputText
  const module={exports:{}}
  new Function('require','module','exports',code)(name=>dependencies[name]??require(name),module,module.exports)
  return module.exports
}
const presentation=compile('../src/data/researchPresentation.ts')
test('research status is distinct from saved financial results and handles failures',()=>{
  assert.equal(presentation.researchRunning({latest_job:{status:'processing'}}),true)
  assert.equal(presentation.researchRunning({latest_job:{status:'failed'}}),false)
  assert.match(presentation.researchStatus({configured:true,eligible:true,latest_job:{status:'failed'}}),/未完成/)
  assert.match(presentation.researchStatus({configured:false,eligible:true}),/尚未配置/)
})

function panel(state,selected) {
  let index=0
  const values=['research',state,selected]
  const hooks={...React,useState(initial){const value=index<values.length?values[index]:initial;index++;return [value,()=>{}]},useEffect(){},useRef(value){return {current:value}}}
  return compile('../src/components/ResearchReportPanel.tsx',{
    react:hooks,'./AgentReport':{AgentReport:()=>React.createElement('div',null,'旧版财务摘要与原始报告')},
    '../services/financeApi':{financeApi:{}},'../services/reportExport':{exportReportPdf(){}},
    '../data/researchPresentation':presentation,'./researchReport.css':{},
  }).ResearchReportPanel
}
test('default research view does not hide access to the original financial summary',()=>{
  const Panel=panel(undefined,undefined)
  const html=renderToStaticMarkup(React.createElement(Panel,{taskId:'a'.repeat(32),markdown:'# 原报告',generator:'deterministic_template',onEvidence(){}}))
  assert.match(html,/aria-selected="true"[^>]*>.*研究解读/)
  assert.match(html,/财务摘要/)
  assert.match(html,/先查看财务摘要/)
  assert.doesNotMatch(html,/DeepSeek 已生成/)
})
test('validated findings show evidence, alternative explanations, checks and limits',()=>{
  const report={id:'b'.repeat(32),created_at:'2026-10-17T00:00:00Z',markdown:'# 已保存解读',body:{title:'示例公司 · 研究解读',coverage:[],sections:[{key:'overview',title:'核心判断',claims:[{
    headline:'现金兑现需要核实',interpretation:'应结合盈利与回款观察。',alternatives:['结算时点可能影响结果。'],checks:['核对后续回款。'],limitation:'相关性不证明因果。',
    support_if:'若回款改善且应收增速放缓，则支持兑现改善。',weaken_if:'若利润增长但现金转弱，则需重新检验判断。',
    evidence:[{id:'m0001',label:'经营现金流同比',display:'0.00%',company:'示例公司',period:'2025YTD9',period_label:'2025年前三季度',scope:'本期',sources:[{task_id:'c'.repeat(32),fact_id:'source',label:'原文第 8 页'}]}],
  }]}],limits:['没有行业数据库。'],validation_note:'解释仍需人工复核。'}}
  const state={configured:true,eligible:true,latest_job:{status:'completed'},versions:[],report}
  const Panel=panel(state,report)
  const html=renderToStaticMarkup(React.createElement(Panel,{taskId:'a'.repeat(32),markdown:'# 原报告',generator:'deterministic_template',onEvidence(){}}))
  for(const text of ['0.00%','2025年前三季度','原文第 8 页','其他可能解释','后续核查','判断边界','复制解读','导出 PDF'])assert.ok(html.includes(text),text)
  assert.ok(!html.includes('2025YTD9'))
  assert.match(html,/role="region" aria-label="研究解读正文，可在框内滚动" tabindex="0"/)
  assert.match(html,/<details class="research-proof"><summary>/)
  assert.doesNotMatch(html,/<details[^>]* open/)
  for(const text of ['研究章节','支持判断的信号','削弱判断的信号','数据、原文与核查路径'])assert.ok(html.includes(text),text)
})

test('research frame is bounded with internal scrolling and expands only for print',()=>{
  const css=readFileSync(new URL('../src/components/researchReport.css',import.meta.url),'utf8')
  assert.match(css,/\.research-shell\{height:800px;min-height:0;max-height:800px;overflow:hidden/)
  assert.match(css,/\.research-scroll\{flex:1;min-height:0;overflow:auto;overscroll-behavior:contain/)
  assert.match(css,/@media print\{[\s\S]*height:auto/)
  assert.match(css,/\.agent-report-shell\.research-shell>aside\{display:none/)
  assert.match(presentation.researchStatus({configured:true,eligible:true,latest_job:{status:'processing',stage:'补充经营原文'}}),/经营原文/)
})

test('full report exporter includes closed proof and offscreen ending, not UI controls',async()=>{
  const drawn=[];let pages=0
  const ctx={measureText:value=>({width:value.length*12}),fillText:value=>drawn.push(value),fillRect(){},beginPath(){},moveTo(){},lineTo(){},stroke(){}}
  const oldDocument=globalThis.document
  globalThis.document={fonts:{ready:Promise.resolve()},createElement:()=>({getContext:()=>ctx,toBlob:callback=>callback(new Blob(['image']))})}
  const el=(tag,text='',children=[],skip=false)=>({tagName:tag,textContent:text||children.map(c=>c.textContent).join(''),children,
    hasAttribute:attribute=>skip&&attribute==='data-report-export-skip'})
  try{
    const exporter=compile('../src/services/reportExport.ts',{'./metricExport':{jpegPagesToPdf(images){pages=images.length;return new Blob(['test-only'])}}})
    await exporter.createReportPdf(el('DIV','',[
      el('H1','完整研究解读'),el('DETAILS','',[el('SUMMARY','不要导出折叠按钮'),el('DIV','',[el('P','未展开原文依据仍然完整导出')])]),
      el('P','长正文测试。'.repeat(1200)),el('BUTTON','不要导出操作按钮',[],true),el('P','报告末尾的判断边界完整保留'),
    ]))
    const all=drawn.join('');assert.ok(pages>1)
    assert.ok(all.includes('未展开原文依据仍然完整导出'));assert.ok(all.includes('报告末尾的判断边界完整保留'))
    assert.ok(!all.includes('不要导出'))
  } finally{globalThis.document=oldDocument}
})
test('reads never automatically create research jobs and citations retain source task identity',()=>{
  const source=readFileSync(new URL('../src/components/ResearchReportPanel.tsx',import.meta.url),'utf8')
  assert.match(source,/getResearch\(taskId,controller.signal\)/)
  assert.match(source,/onEvidence\(source.fact_id,source.task_id\)/)
  assert.match(source,/cancelResearch\(taskId,state.latest_job.id\)/)
  assert.match(source,/selectedId.current===identifier/)
  assert.match(source,/revision.current/)
  assert.match(source,/await navigator.clipboard.writeText\(selected\?\.markdown/)
  assert.match(source,/已复制全文/)
  assert.match(source,/复制未成功/)
})
