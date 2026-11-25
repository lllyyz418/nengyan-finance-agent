import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, CircleAlert, FileUp, History, LoaderCircle, RefreshCw } from 'lucide-react'
import { financeApi } from '../services/financeApi'
import type { BackendResult, BackfillSummary, DocumentMeta } from '../types/finance'
import { ChartsGrid } from './ChartsGrid'
import './historyBackfill.css'
import {reportTypeLabel} from '../data/reportPeriods'

const labels:Record<string,string> = {ready:'已就绪',queued:'等待中',retrieving:'查找报告',downloading:'下载中',parsing:'解析核验中',failed:'未取得',needs_confirmation:'待确认身份'}
const messageText=(message='')=>message.replace(/Unit not found for table on PDF page (\d+)/g,'PDF 第 $1 页表格金额单位尚未识别，未采用该年数据，请核对原文或补传可识别版本')
export function HistoryBackfillPanel({taskId,history,document,defaultLookback,autoFollow=false,onTaskCreated,onResult,onEvidence,onMetric}: {
  taskId:string; history?:Record<string,any>; document:DocumentMeta; defaultLookback:number; autoFollow?:boolean;
  onTaskCreated:(id:string)=>void; onResult:(result:BackendResult,id:string)=>void;
  onEvidence?:(id:string)=>void;onMetric?:(code:string)=>void;
}) {
  const [summary,setSummary]=useState<BackfillSummary>()
  const [years,setYears]=useState(defaultLookback)
  const [files,setFiles]=useState<File[]>([])
  const [error,setError]=useState('')
  const [submitting,setSubmitting]=useState(false)
  const [reload,setReload]=useState(0)
  const [code,setCode]=useState('')
  const [confirmed,setConfirmed]=useState(false)
  const fileInput=useRef<HTMLInputElement>(null)
  const watching=useRef<string>()
  const alive=useRef(true)
  const callbacks=useRef({onResult,onTaskCreated,autoFollow})
  callbacks.current={onResult,onTaskCreated,autoFollow}
  useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[])
  useEffect(()=>{
    let cancelled=false, timer:number|undefined
    const refresh=async()=>{
      try {
        const value=await financeApi.getBackfill(watching.current??taskId)
        if(cancelled)return
        setSummary(value)
        if(!watching.current){setYears(value.task?value.lookback_years:defaultLookback);if(value.stock_code)setCode(value.stock_code)}
        if(value.result_task_id && value.result_task_id!==taskId && (callbacks.current.autoFollow||watching.current===value.result_task_id)){
          const result=await financeApi.getTaskResult(value.result_task_id)
          if(!cancelled)callbacks.current.onResult(result,value.result_task_id)
          return
        }
        if(value.pending||['queued','processing'].includes(value.task?.status??''))timer=window.setTimeout(refresh,1600)
      } catch(cause){if(!cancelled)setError(`历史回溯接口未连接：${cause instanceof Error?cause.message:'请求失败'}。若刚更新代码，请重启后端。`)}
    }
    void refresh()
    return()=>{cancelled=true;window.clearTimeout(timer)}
  },[taskId,reload,defaultLookback])
  const running=submitting||Boolean(summary?.pending)||['queued','processing'].includes(summary?.task?.status??'')
  const needsIdentity=summary?.task?.status==='review_required'&&years>0
  const start=async(manual=false)=>{
    if(running)return
    setSubmitting(true);setError('')
    try{
      const tokens:string[]=[]
      if(manual){
        if(!files.length)throw new Error('请先选择历史年度的完整年报 PDF')
        for(const file of files){
          if(file.size>80*1024*1024||!file.name.toLowerCase().endsWith('.pdf'))throw new Error(`${file.name}：仅支持 80 MB 以内的 PDF`)
          const staged=await financeApi.stageHistoryFile(taskId,file)
          if(!alive.current)return
          tokens.push(staged.token)
        }
      }
      const task=await financeApi.createBackfill(taskId,{lookback_years:years,file_tokens:tokens,online:!manual,
        ...(confirmed?{stock_code:code,confirm_identity:true}:{})})
      if(!alive.current)return
      watching.current=task.id;callbacks.current.onTaskCreated(task.id);setFiles([])
      if(fileInput.current)fileInput.current.value=''
      setReload(value=>value+1)
    }catch(cause){if(alive.current)setError(cause instanceof Error?cause.message:'历史回溯未启动')}
    finally{if(alive.current)setSubmitting(false)}
  }
  const openVersion=async()=>{
    const id=summary?.newer_result_task_id??summary?.result_task_id
    if(!id)return
    try{const result=await financeApi.getTaskResult(id);if(alive.current)callbacks.current.onResult(result,id)}
    catch(cause){if(alive.current)setError(cause instanceof Error?cause.message:'无法打开历史版本')}
  }
  const stop=async()=>{
    const id=summary?.task?.id ?? watching.current
    if(!id)return
    try { await financeApi.cancelTask(id); setReload(value=>value+1); setError('已停止历史回溯任务；已取得的年度文件和结果会保留。') }
    catch(cause){setError(cause instanceof Error?cause.message:'停止历史回溯失败')}
  }
  const year=summary?.report_year||Number(document.reportYear)
  const scope=reportTypeLabel(document.reportType,document.reportQuarter)
  return <div className="history-backfill">
    <div className="backfill-controls">
      <div className="backfill-title"><History size={23}/><div><h3>{scope==='年度报告'?'自动回溯，逐年有据可查':`历年同期回溯 · ${scope}`}</h3></div></div>
      <div className="backfill-actions"><label>向前回溯<select aria-label="历史趋势回溯年限" value={years} disabled={running||!summary?.eligible} onChange={e=>setYears(Number(e.target.value))}>{[0,1,2,3,4,5].map(n=><option key={n} value={n}>{n===0?'仅本期':`${n} 年`}</option>)}</select></label><button className="primary-button" onClick={()=>void start()} disabled={running||!summary?.eligible||(needsIdentity&&(!confirmed||!/^\d{6}$/.test(code)))}>{running?<LoaderCircle size={15} className="spin"/>:<RefreshCw size={15}/>} {needsIdentity?'确认并继续':summary?.task?'应用范围 / 重试缺失':'开始回溯'}</button>{running&&<button className="stop-button" onClick={()=>void stop()}><CircleAlert size={15}/>停止回溯</button>}</div>

    </div>
    {(summary?.pending||summary?.task)&&<div className={`backfill-job ${running?'running':''}`} role="status">{running?<LoaderCircle size={17} className="spin"/>:summary?.task?.status==='completed'?<CheckCircle2 size={17}/>:<CircleAlert size={17}/>}<span>{summary?.pending?'本期已完成，正在建立历史补齐任务…':summary?.task?.stage}{summary?.task?.error?`：${summary.task.error}`:''}</span>{summary?.result_task_id&&!summary.is_current_version&&!running&&<button className="secondary-button" onClick={()=>void openVersion()}>打开补齐版本</button>}</div>}
    {summary?.message&&<p className="backfill-note">{summary.message}</p>}
    {summary?.newer_result_task_id&&<div className="backfill-job"><span>正在查看当时保存的历史快照，另有新的补齐版本。打开前不会替换本页数据。</span><button className="secondary-button" onClick={()=>void openVersion()}>打开较新版本</button></div>}
    {needsIdentity&&<div className="backfill-identity"><strong>未识别到唯一证券代码，自动下载已暂停</strong><p>请对照本期 PDF 确认公司身份。不会仅凭文件名匹配其他公司的年报。</p><label>6 位证券代码<input value={code} maxLength={6} inputMode="numeric" onChange={e=>{setCode(e.target.value.replace(/\D/g,''));setConfirmed(false)}}/></label><label className="backfill-check"><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我已对照本期原文确认公司与证券代码</label></div>}
    {!!summary?.rows.length&&<div className="backfill-year-grid">{summary.rows.map(row=><article key={row.year} className={`backfill-year ${row.status}`}><header><strong>{row.year}<small> 年</small></strong><span>{labels[row.status]??'待核验'}</span></header><p>{messageText(row.message)}</p><small title={row.file_name}>{row.file_name||row.source||`等待取得完整${scope}`}</small>{row.version_note&&<small>{row.version_note==='original'?'原始披露版本':row.version_note}</small>}{row.status==='ready'&&<a href={financeApi.getBackfillFileUrl(summary.task?.id??taskId,row.year)} target="_blank" rel="noreferrer">查看同期原文件 ↗</a>}</article>)}</div>}
    {summary?.eligible&&<div className="backfill-manual"><div><strong>公开报告没找到？手动补齐</strong></div><input ref={fileInput} type="file" accept="application/pdf,.pdf" multiple className="visually-hidden" onChange={e=>{const chosen=Array.from(e.target.files??[]);if(chosen.length>6){setError('一次最多补传 6 份文件');e.target.value='';setFiles([])}else{setFiles(chosen);setError('')}}}/><button className="secondary-button" disabled={running||years===0} onClick={()=>fileInput.current?.click()}><FileUp size={15}/>选择历史{scope}</button><button className="secondary-button" disabled={running||!files.length||years===0||(needsIdentity&&(!confirmed||!/^\d{6}$/.test(code)))} onClick={()=>void start(true)}>上传并补齐{files.length?`（${files.length} 份）`:''}</button>{!!files.length&&<p className="backfill-selected">{files.map(file=>file.name).join('；')}</p>}</div>}
    {error&&<div className="dialog-error" role="alert"><CircleAlert size={17}/>{error}</div>}
    {summary?.rejected_files.map((file,index)=><p className="backfill-note" key={index}>{file.file_name}：未采用 · {file.message}</p>)}
    <ChartsGrid demo={false} history={history} document={document} onEvidence={onEvidence} onMetric={onMetric}/>
  </div>
}
