import { AlertTriangle, CheckCircle2, CircleStop, FileUp, Info, LoaderCircle, RefreshCw, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { financeApi } from '../services/financeApi'
import type { AnalysisSettings, BackendResult, BackendTask, UploadFailure } from '../types/finance'
import { uploadFailure, uploadViewState } from '../data/uploadFailure'
import {reportTypeLabel} from '../data/reportPeriods'

export function UploadDialog({ open, settings, expectedPeriod, onClose, onComplete, onTaskCreated }: { open: boolean; settings:AnalysisSettings;expectedPeriod?:{type:string;year:number;quarter:number}; onClose: () => void; onComplete: (result: BackendResult, taskId: string, lookbackYears: number) => void; onTaskCreated?: (id: string) => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [task, setTask] = useState<BackendTask | null>(null)
  const [issue, setIssue] = useState<UploadFailure|null>(null)
  const [years,setYears]=useState(settings.lookbackYears)
  const [starting,setStarting]=useState(false)
  const [stopping,setStopping]=useState(false)
  const ticket=useRef(0)
  const [periodConfirmation,setPeriodConfirmation]=useState<{result:BackendResult;id:string;label:string}>()
  useEffect(()=>{if(!open)setPeriodConfirmation(undefined)},[open])

  useEffect(() => { ticket.current+=1;if (!open) { setFile(null); setTask(null); setIssue(null);setStarting(false);setStopping(false) }else{setYears(settings.lookbackYears)} }, [open])
  if (!open) return null
  const state=uploadViewState(task,issue,starting,stopping)

  const followTask=async (initial:BackendTask,current:number)=>{
      if(current!==ticket.current)return
      let next=initial
      setTask(next)
      while (!['completed', 'failed', 'review_required','interrupted'].includes(next.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 900))
        if(current!==ticket.current)return
        next = await financeApi.getTaskProgress(next.id)
        if(current!==ticket.current)return
        setTask(next)
      }
      if(next.status==='failed'){setIssue(uploadFailure(next.error,next));return}
      if(next.status==='interrupted')return
      const result = await financeApi.getTaskResult(next.id)
      if(current!==ticket.current)return
      const doc=result.analysis?.documents?.[0]
      if(doc&&expectedPeriod&&(doc.report_year!==expectedPeriod.year||doc.report_type!==expectedPeriod.type||doc.report_type==='quarterly'&&doc.report_quarter!==expectedPeriod.quarter)){
        setPeriodConfirmation({result,id:next.id,label:`${doc.report_year}年${reportTypeLabel(doc.report_type,doc.report_quarter)}`});return
      }
      onComplete(result, next.id, years)
  }
  const run = async (resume=false) => {
    if (!file) return inputRef.current?.click()
    if(starting)return
    setIssue(null)
    setStarting(true)
    const current=++ticket.current
    let knownTask:BackendTask|null=resume?task:null
    let submitting=false
    try {
      if(resume&&task){await followTask(await financeApi.getTaskProgress(task.id),current);return}
      if(!file.name.toLowerCase().endsWith('.pdf'))throw new Error('仅支持 PDF 文件')
      if(file.size>80*1024*1024)throw new Error('文件超过 80 MB，请选择较小的 PDF')
      setTask(null)
      submitting=true
      let next = await financeApi.uploadReport(file,'off',{...settings,lookbackYears:years})
      if(current!==ticket.current)return
      knownTask=next;submitting=false
      onTaskCreated?.(next.id)
      await followTask(next,current)
    } catch (cause) {
      if(current===ticket.current)setIssue(uploadFailure(cause,knownTask,submitting))
    }
    finally{if(current===ticket.current){setStarting(false);setStopping(false)}}
  }
  const stop = async () => {
    if (!task?.id || stopping) return
    setStopping(true); const current=++ticket.current
    try {
      const stopped = await financeApi.cancelTask(task.id)
      if(current!==ticket.current)return
      setTask(stopped);setIssue(null)
      if(stopped.status==='failed')setIssue(uploadFailure(stopped.error,stopped))
      else if(stopped.status==='completed'||stopped.status==='review_required')await followTask(stopped,current)
    }
    catch (cause) { if(current===ticket.current)setIssue(uploadFailure(cause,task)) }
    finally { if(current===ticket.current){setStarting(false);setStopping(false)} }
  }

  return <div className="modal-backdrop" role="presentation"><section className="upload-dialog" role="dialog" aria-modal="true" aria-labelledby="upload-title">
    <header><div><span className="eyebrow">新建真实分析任务</span><h2 id="upload-title">上传本地财务报告</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭"><X size={20}/></button></header>
    <input ref={inputRef} disabled={starting} className="visually-hidden" type="file" accept="application/pdf,.pdf" onChange={(event) => { if(!event.target.files?.[0])return;ticket.current+=1;setFile(event.target.files[0]);setTask(null);setIssue(null);setStarting(false);setStopping(false) }}/>
    {!task && <button className="drop-zone" disabled={starting} onClick={() => inputRef.current?.click()}><span><FileUp size={26}/></span><strong>{file ? file.name : '选择 PDF 文件'}</strong><small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB · 点击可重新选择` : '支持文本型年报、半年报、季报与重大公告，最大 80 MB'}</small></button>}
    {!task&&<div className="upload-history-setting"><label>分析后自动向前回溯<select aria-label="上传报告回溯年限" disabled={starting} value={years} onChange={e=>setYears(Number(e.target.value))}>{[0,1,2,3,4,5].map(n=><option value={n} key={n}>{n?`${n} 年（含本期共 ${n+1} 年）`:'不回溯，仅本期'}</option>)}</select></label><p>按 PDF 正文自动识别实际年度与报告类型；半年报回溯历年上半年，季报回溯历年同期，不混入全年数据。</p></div>}
    {task && <div className="task-progress"><div className={`task-file ${state.failed?'task-file--failed':''}`}>
      {state.active?<LoaderCircle size={20} className="spin"/>:state.interrupted?<CircleStop size={20}/>:issue||state.failed?<AlertTriangle size={20}/>:<CheckCircle2 size={20}/>}
      <span><strong>{task.file_name || file?.name}</strong><small>{state.failed?`已结束 · ${issue?.stage??'文件处理'}未完成`:state.interrupted?'任务已停止':issue?'状态暂未确认':`${task.stage} · ${task.progress}%`}</small></span>
      {!state.terminal&&<button className="stop-button" disabled={stopping} onClick={()=>void stop()}><X size={15}/>{stopping?'停止中…':'停止分析'}</button>}
      </div>{state.showProgress&&<div className="progress-track"><div style={{ width: `${Math.min(99,task.progress)}%` }}/></div>}
      {state.active&&<p className="backfill-note">正在分析本期 PDF。完成后先展示本期结果{years>0?'，历史回溯将独立启动，可在“历史趋势与数据质量”查看逐年进度':''}。</p>}
      {state.interrupted&&<p className="upload-stopped" role="status"><Info size={17}/>任务已停止，原文件和已有记录仍保留，不会继续生成分析报告。</p>}
      </div>}
    {periodConfirmation&&<div className="upload-history-setting" role="status"><strong>识别为：{periodConfirmation.label}</strong><p>实际报告期间与顶部选择不同，已按原文期间分析，没有套用顶部类型。是否切换到该报告？</p><button className="primary-button" onClick={()=>onComplete(periodConfirmation.result,periodConfirmation.id,years)}>确认切换并查看结果</button></div>}
    {issue && <div className="dialog-error upload-failure" role="alert"><AlertTriangle size={20}/><div className="upload-failure__body"><strong>{issue.title}</strong><p>{issue.message}</p><p className="upload-failure__suggestion">{issue.suggestion}</p><details><summary>查看诊断信息</summary><dl><div><dt>错误编号</dt><dd>{issue.code}</dd></div><div><dt>失败阶段</dt><dd>{issue.stage??'文件处理'}</dd></div>{task&&<div><dt>任务编号</dt><dd>{task.id}</dd></div>}</dl></details></div></div>}
    {(issue||state.interrupted)&&<footer className="dialog-actions"><button className="secondary-button" onClick={onClose}>关闭</button>{state.canResume?<button className="primary-button" disabled={starting} onClick={()=>void run(true)}><RefreshCw size={16}/>继续查看进度</button>:<>{issue?.retryable&&<button className="secondary-button" disabled={starting||!file} onClick={()=>void run()}><RefreshCw size={16}/>重试分析</button>}<button className="primary-button" onClick={()=>{if(inputRef.current){inputRef.current.value='';inputRef.current.click()}}}><FileUp size={16}/>重新选择文件</button></>}</footer>}
    {!task&&!issue&&<footer className="dialog-actions"><button className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" disabled={!file||starting} onClick={()=>void run()}>{starting?<LoaderCircle size={16} className="spin"/>:<FileUp size={16}/>}上传并分析</button></footer>}
  </section></div>
}
