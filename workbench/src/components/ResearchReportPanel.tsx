import {useEffect,useRef,useState} from 'react'
import {BookOpen,Copy,Download,ExternalLink,FileText,LoaderCircle,RefreshCw,Sparkles} from 'lucide-react'
import {AgentReport} from './AgentReport'
import {financeApi} from '../services/financeApi'
import {exportReportPdf} from '../services/reportExport'
import {researchRunning,researchStatus} from '../data/researchPresentation'
import type {ResearchReport,ResearchSource,ResearchSummary} from '../types/finance'
import './researchReport.css'

type Props={taskId?:string;markdown:string;summary?:string;generator:string;demo?:boolean;onEvidence:(id:string,taskId?:string)=>void}
const date=(value:string)=>new Date(value).toLocaleString('zh-CN',{hour12:false})

export function ResearchReportPanel({taskId,markdown,summary='',generator,demo=false,onEvidence}:Props) {
  const [view,setView]=useState<'research'|'summary'>('research')
  const [state,setState]=useState<ResearchSummary>()
  const [selected,setSelected]=useState<ResearchReport>()
  const [error,setError]=useState('')
  const [busy,setBusy]=useState(false)
  const [exporting,setExporting]=useState(false)
  const [reload,setReload]=useState(0)
  const [copied,setCopied]=useState(false)
  const selectedId=useRef<string>()
  const reportRoot=useRef<HTMLDivElement>(null)
  const scrollRoot=useRef<HTMLDivElement>(null)
  const revision=useRef(0)
  useEffect(()=>{
    revision.current++;selectedId.current=undefined;setSelected(undefined);setState(undefined);setError('');setBusy(false);setView('research')
  },[taskId])
  useEffect(()=>{
    if(!taskId||demo)return
    let cancelled=false,timer:number|undefined
    const controller=new AbortController()
    const refresh=async()=>{
      try {
        const next=await financeApi.getResearch(taskId,controller.signal)
        if(cancelled)return
        setState(next);setError('')
        if(!selectedId.current)setSelected(next.report??undefined)
        if(next.configured&&next.eligible)timer=window.setTimeout(refresh,researchRunning(next)?2500:15000)
      } catch(cause) {
        if(!cancelled&&!controller.signal.aborted)setError(cause instanceof Error?cause.message:'研究版本读取失败，财务摘要仍可查看。')
      }
    }
    void refresh()
    return()=>{cancelled=true;controller.abort();window.clearTimeout(timer)}
  },[taskId,demo,reload])
  const generate=async()=>{
    if(!taskId||busy||researchRunning(state))return
    const current=revision.current;setBusy(true);setError('')
    try {
      const job=await financeApi.createResearch(taskId)
      if(current!==revision.current)return
      selectedId.current=undefined
      setState(previous=>previous?{...previous,latest_job:job}:previous)
      setReload(value=>value+1)
    } catch(cause){if(current===revision.current)setError(cause instanceof Error?cause.message:'研究解读未能提交；请先刷新确认任务状态，避免重复提交。')}
    finally{if(current===revision.current)setBusy(false)}
  }
  const version=async(identifier:string)=>{
    if(!taskId||exporting)return
    const current=revision.current
    selectedId.current=identifier;setError('')
    try {
      const next=await financeApi.getResearchVersion(taskId,identifier)
      if(current===revision.current&&selectedId.current===identifier)setSelected(next)
    } catch(cause){if(current===revision.current)setError(cause instanceof Error?cause.message:'无法读取研究版本')}
  }
  const stop=async()=>{
    if(!taskId||!state?.latest_job)return
    const current=revision.current;setBusy(true);setError('')
    try{
      await financeApi.cancelResearch(taskId,state.latest_job.id)
      if(current===revision.current)setReload(value=>value+1)
    }catch(cause){if(current===revision.current)setError(cause instanceof Error?cause.message:'停止请求未完成，请刷新确认任务状态。')}
    finally{if(current===revision.current)setBusy(false)}
  }
  const exportPdf=async()=>{
    if(!reportRoot.current||exporting||!selected?.body)return
    setExporting(true);setError('')
    try{await exportReportPdf(reportRoot.current,selected.body.title)}
    catch(cause){setError(cause instanceof Error?cause.message:'导出失败，请重试')}
    finally{setExporting(false)}
  }
  const copyReport=async()=>{
    try{
      if(!navigator.clipboard)throw new Error('当前浏览器不支持自动复制，请导出 PDF 或手动选择正文复制。')
      await navigator.clipboard.writeText(selected?.markdown??'');setCopied(true)
    }catch{setError('复制未成功，请导出 PDF 或手动选择正文复制。');setCopied(false)}
  }
  useEffect(()=>{scrollRoot.current?.scrollTo({top:0});setCopied(false)},[selected?.id,taskId])
  const jump=(key:string)=>{
    const scroll=scrollRoot.current,target=scroll?.querySelector<HTMLElement>(`[data-research-section="${key}"]`)
    if(scroll&&target)scroll.scrollTo({top:scroll.scrollTop+target.getBoundingClientRect().top-scroll.getBoundingClientRect().top-16,behavior:'smooth'})
  }
  if(!taskId||demo)return <AgentReport markdown={markdown} summary={summary} generator={generator} demo={demo}/>
  const body=selected?.body,running=busy||researchRunning(state)
  const openSource=(source:ResearchSource)=>onEvidence(source.fact_id,source.task_id)
  return <div className="research-panel">
    <div className="research-tabs" role="tablist" aria-label="分析报告视图">
      <button role="tab" aria-selected={view==='research'} onClick={()=>setView('research')}><BookOpen size={17}/>研究解读</button>
      <button role="tab" aria-selected={view==='summary'} onClick={()=>setView('summary')}><FileText size={17}/>财务摘要</button>
    </div>
    {view==='summary'?<AgentReport markdown={markdown} summary={summary} generator={generator}/>:<div className="agent-report-shell research-shell">
      <aside><span className="report-orb"><BookOpen size={23}/></span><strong>经营研究解读</strong><small>研究主线 · 驱动拆解 · 信号验证</small>{body&&<nav className="research-outline" aria-label="研究章节">{body.sections.map((section,index)=><button key={section.key} onClick={()=>jump(section.key)}><span>{String(index+1).padStart(2,'0')}</span>{section.title}</button>)}<button onClick={()=>jump('limits')}>资料与判断边界</button></nav>}<div className="report-separation"><Sparkles size={15}/><span><b>财务结果已独立保存</b><small>文字生成失败不影响指标、摘要与原文证据。</small></span></div></aside>
      <article><header><div><BookOpen size={18}/><strong>研究解读</strong></div><div className="report-export-actions">
        {body&&<><button className="secondary-button" onClick={()=>void copyReport()}><Copy size={15}/>{copied?'已复制全文':'复制解读'}</button><button className="secondary-button" disabled={exporting} onClick={()=>void exportPdf()}>{exporting?<LoaderCircle className="spin" size={15}/>:<Download size={15}/>}导出 PDF</button></>}
        <button className="secondary-button" disabled={busy} onClick={()=>setReload(value=>value+1)} aria-label="刷新研究版本"><RefreshCw size={15}/>刷新</button>
        <button className="primary-button" disabled={!state?.configured||!state.eligible||running} onClick={()=>void generate()}>{running?<LoaderCircle className="spin" size={15}/>:<Sparkles size={15}/>} {running?'生成中':state?.new_data_available?'更新研究解读':state?.latest_job?.status==='failed'||state?.latest_job?.status==='interrupted'?'重试生成':body?'核对可用数据':'生成研究解读'}</button>
        {researchRunning(state)&&<button className="stop-button" disabled={busy} onClick={()=>void stop()}>停止生成</button>}
      </div></header>
      <div className="research-scroll" ref={scrollRoot} role="region" aria-label="研究解读正文，可在框内滚动" tabIndex={0}>
      <div className={`research-state ${running?'running':''}`} role="status">{running&&<LoaderCircle className="spin" size={16}/>}<span>{researchStatus(state)}{running?'，财务摘要可先查看。':''}</span></div>
      {(error||state?.latest_job?.error||state?.message)&&<p className="research-notice error" role="alert">{error||state?.latest_job?.error||state?.message}</p>}
      {state?.new_data_available&&<p className="research-notice">资料或研究解读规则已有更新。可生成新版本，旧解读仍保留；刷新不会自动调用模型。</p>}
      {state?.versions.some(item=>item.status==='completed')&&<label className="research-version">已保存版本<select aria-label="研究解读版本" disabled={exporting} value={selected?.id??''} onChange={event=>void version(event.target.value)}>{state.versions.filter(item=>item.status==='completed').map(item=><option key={item.id} value={item.id}>{date(item.created_at)} · {item.scope==='current'?'本期资料':'已取得资料'}</option>)}</select></label>}
      {body?<div className="research-body" ref={reportRoot}>
        <h1>{body.title}</h1><p className="research-provenance">DeepSeek 辅助研究解读 · {selected?.created_at?date(selected.created_at):''} · 非审计结论或交易建议</p>
        <div className="research-coverage"><strong>本版研究范围</strong>{body.coverage.map((item,index)=><p key={index}>{item.role}：{item.company} · {item.years.join('、')} · {item.measures} 项有据数据</p>)}{body.business_context&&<p className={body.business_context.status==='available'?'research-prose-ready':'research-prose-limited'}>{body.business_context.message}</p>}</div>
        {body.sections.map(section=><div className="research-section" data-research-section={section.key} key={section.key}><h2>{section.title}</h2>{section.claims.map((claim,index)=><div className="research-claim" key={index}>
          <h3>{claim.headline}</h3><p className="research-interpretation">{claim.interpretation}</p>
          {(claim.support_if||claim.weaken_if)&&<div className="research-signals">{claim.support_if&&<div><h4>支持判断的信号</h4><p>{claim.support_if}</p></div>}{claim.weaken_if&&<div><h4>削弱判断的信号</h4><p>{claim.weaken_if}</p></div>}</div>}
          <details className="research-proof"><summary>数据、原文与核查路径 · {claim.evidence.length} 项依据</summary><div>
          {claim.observed_facts?.map((text,i)=><p className="research-observed" key={i}><strong>已核对观察：</strong>{text}</p>)}
          <div className="research-evidence"><h4>数据与原文依据</h4>{claim.evidence.map(point=><div key={point.id}><p><strong>{point.label}：{point.display}</strong>{' '}<span>{point.company} · {point.period_label??point.period} · {point.scope}</span></p><div>{point.sources.map((source,i)=><button className="text-button" key={i} onClick={()=>openSource(source)}><ExternalLink size={13}/>{source.label}</button>)}</div></div>)}</div>
          <div className="research-reasoning"><div><h4>其他可能解释</h4><ul>{claim.alternatives.map((text,i)=><li key={i}>{text}</li>)}</ul></div><div><h4>后续核查</h4><ul>{claim.checks.map((text,i)=><li key={i}>{text}</li>)}</ul></div></div>
          </div></details>
          <p className="research-boundary"><strong>判断边界：</strong>{claim.limitation}</p>
        </div>)}</div>)}
        <div className="research-limits" data-research-section="limits"><h2>数据范围与限制</h2><ul>{body.limits.map((text,index)=><li key={index}>{text}</li>)}</ul>{body.business_context?.scope_note&&<p>{body.business_context.scope_note}</p>}<p>{body.validation_note}</p></div>
      </div>:<div className="report-empty"><BookOpen size={30}/><strong>{running?'财务结果已就绪，研究解读生成中':'还没有研究解读'}</strong><p>{state&&!state.configured?'尚未配置 DeepSeek；请先查看财务摘要，配置后再生成解读。':'研究解读结合已取得的财务数据形成经营观察；只有有依据的内容才会展示。'}</p><button className="secondary-button" onClick={()=>setView('summary')}>先查看财务摘要</button></div>}
      </div></article></div>}
  </div>
}
