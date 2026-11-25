import { ArchiveRestore, ArrowLeft, ChevronLeft, ChevronRight, Download, FileText, History, LoaderCircle, RefreshCw, Search, Star, StickyNote, Trash2, X } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { financeApi, resultToSnapshot } from '../services/financeApi'
import { exportMetrics } from '../services/metricExport'
import type { BackendResult, HistoryFilters, HistoryRecord, HistoryResponse, HistoryStatus } from '../types/finance'

const statuses: Record<HistoryStatus,string> = { queued:'排队中',processing:'分析中',completed:'已完成',partial:'部分完成',review_required:'待复核',failed:'失败',interrupted:'已中断' }
const kinds = {financial:'财报分析',peer:'同行比较',announcement:'公告解析',scan:'扫描件复核'}
const initial: HistoryFilters = {q:'',status:'',kind:'',year:'',start_date:'',end_date:'',favorites:false,trashed:false,page:1,page_size:10}
const date = (value: string) => value ? new Date(value).toLocaleString('zh-CN',{hour12:false}) : '未记录'

export function HistoryCenter({onOpen,onBack,onTaskCreated}: {onOpen:(record:HistoryRecord,result:BackendResult)=>void;onBack:()=>void;onTaskCreated?: (id: string) => void}) {
  const [filters,setFilters] = useState(initial)
  const [data,setData] = useState<HistoryResponse | null>(null)
  const [loading,setLoading] = useState(true)
  const [busy,setBusy] = useState('')
  const [error,setError] = useState('')
  const [notice,setNotice] = useState('')
  const [selected,setSelected] = useState<HistoryRecord | null>(null)
  const [note,setNote] = useState('')
  const generation = useRef(0)
  const mounted = useRef(false)
  const request = useRef<AbortController>()
  const pending = useRef(false)
  const paused = useRef(false)
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false}},[])
  const load = useCallback(async (quiet = false) => {
    if(!mounted.current||(quiet&&(pending.current||paused.current)))return
    request.current?.abort()
    const controller=new AbortController();request.current=controller
    pending.current=true
    if(!quiet)paused.current=false
    const current = ++generation.current
    if (!quiet) setLoading(true)
    try { const next = await financeApi.listHistory(filters,controller.signal); if(mounted.current&&!controller.signal.aborted&&current === generation.current){ setData(next);setError(''); if (next.total && (filters.page-1)*filters.page_size >= next.total) setFilters(value=>({...value,page:Math.ceil(next.total/value.page_size)})) } }
    catch(cause){ if(mounted.current&&!controller.signal.aborted&&current===generation.current){paused.current=true;setError(cause instanceof Error ? cause.message === 'Not Found' ? '历史记录接口尚未加载，请重启后端后点击刷新。' : cause.message : '历史记录读取失败，自动刷新已暂停，请重试。')} }
    finally{ if(mounted.current&&current===generation.current){pending.current=false;setLoading(false)} }
  },[filters])
  useEffect(()=>{ const timer=setTimeout(()=>load(),200);return()=>{clearTimeout(timer);generation.current++;request.current?.abort();pending.current=false} },[load])
  useEffect(()=>{ const timer=setInterval(()=>load(true),10000);return()=>clearInterval(timer) },[load])
  const change = (patch:Partial<HistoryFilters>)=>setFilters(value=>({...value,...patch,page:1}))
  const act = async(id:string,operation:()=>Promise<void>)=>{
    setBusy(id);setError('');setNotice('')
    try{ await operation();if(mounted.current)await load() }catch(cause){if(mounted.current)setError(cause instanceof Error?cause.message:'操作未完成，请重试')}finally{if(mounted.current)setBusy('')}
  }
  const open = (record:HistoryRecord)=>act(record.id,async()=>{ const result=await financeApi.getTaskResult(record.id);if(mounted.current)onOpen(record,result) })
  const download = (record:HistoryRecord,format:'excel'|'pdf')=>act(record.id,async()=>{
    const result=await financeApi.getTaskResult(record.id), snapshot=resultToSnapshot(result)
    await exportMetrics(snapshot.financialMetrics,{company:record.company,period:record.period,demo:false},format)
  })
  const statistics=data?.statistics ?? {total:'—',completed:'—',review_required:'—',failed:'—'}
  const pages=Math.max(1,Math.ceil((data?.total??0)/filters.page_size))
  return <section className="history-center" id="history-records">
    <header className="history-page-head"><div><span className="eyebrow">分析档案</span><h1><History size={27}/>历史记录</h1><p>重新打开已保存的分析结果，保留报告、指标及原文证据。</p></div><div><button className="secondary-button" onClick={onBack}><ArrowLeft size={16}/>返回工作台</button><button className="secondary-button" onClick={()=>load()} disabled={loading}><RefreshCw className={loading?'spin':''} size={16}/>刷新</button></div></header>
    <div className="history-stats">{[['全部任务',statistics.total],['已完成 / 部分完成',statistics.completed],['待复核',statistics.review_required],['失败 / 已中断',statistics.failed]].map(([label,value])=><article key={label}><small>{label}</small><strong>{value}</strong></article>)}</div>
    <div className="history-toolbar"><div className="history-tabs">{[['全部记录','all'],['已收藏','favorite'],['回收站','trash']].map(([label,key])=><button key={key} className={(key==='trash'?filters.trashed:key==='favorite'?filters.favorites:!filters.favorites&&!filters.trashed)?'active':''} onClick={()=>change({favorites:key==='favorite',trashed:key==='trash'})}>{label}</button>)}</div><label className="history-search"><Search size={18}/><input aria-label="搜索历史记录" placeholder="搜索公司、代码、文件名或备注" value={filters.q} onChange={event=>change({q:event.target.value})}/></label>
      <div className="history-filters"><label>任务类型<select value={filters.kind} onChange={event=>change({kind:event.target.value})}><option value="">全部类型</option>{Object.entries(kinds).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><label>状态<select value={filters.status} onChange={event=>change({status:event.target.value})}><option value="">全部状态</option>{Object.entries(statuses).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><label>报告年度<select value={filters.year} onChange={event=>change({year:event.target.value})}><option value="">全部年度</option>{data?.years.map(year=><option key={year} value={year}>{year}年</option>)}</select></label><label>创建日期起<input type="date" value={filters.start_date} onChange={event=>change({start_date:event.target.value})}/></label><label>创建日期止<input type="date" value={filters.end_date} onChange={event=>change({end_date:event.target.value})}/></label><button className="text-button" onClick={()=>setFilters(initial)}>重置筛选</button></div>
    </div>
    {error&&<div className="history-message error history-read-error" role="alert"><span>{error}</span><button className="secondary-button" disabled={loading} onClick={()=>void load()}>重试读取</button></div>}{notice&&<div className="history-message" role="status">{notice}</div>}
    {loading&&!data?<div className="history-empty"><LoaderCircle className="spin"/><strong>正在读取本地分析记录…</strong></div>:!data?.items.length?<div className="history-empty"><History size={34}/><strong>{error?'暂时无法读取历史记录':filters.trashed?'回收站为空':'没有匹配的分析记录'}</strong><p>完成财报分析、公告解析或扫描件识别后，任务将自动保存在这里。演示数据不计入历史。</p></div>:<div className={`history-list ${loading?'refreshing':''}`}>{data.items.map(record=><article className="history-record" key={record.id}>
      <div className="history-record-head"><span className="history-kind">{kinds[record.kind]}</span><span className={`history-status history-status-${record.status}`}>{statuses[record.status]}</span><span className="history-version">引擎 {record.version} · {record.id.slice(0,8)}</span><button className={`history-star ${record.favorite?'on':''}`} aria-label={record.favorite?'取消收藏':'收藏记录'} disabled={!!busy} onClick={()=>act(record.id,async()=>{await financeApi.updateHistory(record.id,{favorite:!record.favorite})})}><Star size={18} fill={record.favorite?'currentColor':'none'}/></button></div>
      <div className="history-record-body"><div><h2>{record.company}<small>{record.stock_code}</small></h2><p>{record.period} · {record.source==='history_backfill'?'历史回溯补齐':record.source==='cninfo'?'公开报告检索':'本地上传'}</p><div className="history-file"><FileText size={15}/><span title={record.file_name}>{record.file_name||'文件名称未记录'}</span></div></div><div className="history-result-counts"><span><b>{record.metrics}</b>可用指标</span><span><b>{record.risks}</b>风险信号</span><span><b>{record.warnings}</b>系统提示</span></div></div>
      {record.error&&<p className="history-error-copy">{record.error}</p>}{record.note&&<p className="history-note-copy"><StickyNote size={14}/>{record.note}</p>}
      <footer><span className="history-created">{date(record.created_at)}{record.parent_task_id&&<small>{record.source==='history_backfill'?'基于':'由'} {record.parent_task_id.slice(0,8)} {record.source==='history_backfill'?'补齐历史，原结果保留':'重新分析'}</small>}</span><div className="history-record-actions">
        <button className="primary-button" disabled={!!busy||!record.has_result||['queued','processing','interrupted','failed'].includes(record.task_status)} onClick={()=>open(record)}>{busy===record.id?<LoaderCircle className="spin" size={14}/>:<FileText size={14}/>}查看结果</button>
        {record.source_available?<a className="secondary-button" href={financeApi.getTaskFileUrl(record.id)} target="_blank" rel="noopener noreferrer">原始 PDF</a>:<span className="history-unavailable">原文件缺失</span>}
        {record.has_metrics&&<><button className="secondary-button" disabled={!!busy} onClick={()=>download(record,'excel')}><Download size={14}/>Excel</button><button className="secondary-button" disabled={!!busy} onClick={()=>download(record,'pdf')}><Download size={14}/>PDF</button></>}
        {record.has_report&&<a className="secondary-button" href={financeApi.getHistoryReportUrl(record.id)}>文字报告</a>}
        <button className="secondary-button" onClick={()=>{setSelected(record);setNote(record.note)}}><StickyNote size={14}/>详情 / 备注</button>
        <button className="text-button" disabled={!!busy||!record.can_retry||['queued','processing'].includes(record.task_status)} title={!record.can_retry?'旧任务未保存配置，或原文件不存在':'创建新任务并保留本记录'} onClick={()=>act(record.id,async()=>{const task=await financeApi.retryHistory(record.id);onTaskCreated?.(task.id);setNotice('已创建新分析任务，旧结果仍保留。新任务完成后可在列表中打开。')})}><RefreshCw size={14}/>重新分析</button>
        <button className="history-trash" disabled={!!busy||['queued','processing'].includes(record.task_status)} aria-label={record.trashed?'恢复记录':'移入回收站'} onClick={()=>act(record.id,async()=>{await financeApi.updateHistory(record.id,{trashed:!record.trashed});setNotice(record.trashed?'记录已恢复':'记录已移入回收站，PDF及分析结果仍保留。')})}>{record.trashed?<ArchiveRestore size={17}/>:<Trash2 size={17}/>}</button>
      </div></footer>
    </article>)}</div>}
    <div className="history-pagination"><span>共 {data?.total??0} 条记录 · 第 {filters.page} / {pages} 页</span><div><button className="secondary-button" disabled={loading||filters.page<=1} onClick={()=>setFilters(value=>({...value,page:value.page-1}))}><ChevronLeft size={16}/>上一页</button><button className="secondary-button" disabled={loading||filters.page>=pages} onClick={()=>setFilters(value=>({...value,page:value.page+1}))}>下一页<ChevronRight size={16}/></button></div></div>
    {selected&&<div className="modal-backdrop"><section className="history-detail" role="dialog" aria-modal="true" aria-labelledby="history-detail-title"><header><div><span className="eyebrow">任务档案</span><h2 id="history-detail-title">{selected.company} · {selected.period}</h2></div><button className="icon-button" aria-label="关闭任务详情" onClick={()=>setSelected(null)}><X size={20}/></button></header><dl><dt>任务编号</dt><dd>{selected.id}</dd><dt>原文件 SHA-256</dt><dd>{selected.file_hash||'未记录'}</dd><dt>创建时间</dt><dd>{date(selected.created_at)}</dd><dt>原分析配置</dt><dd>{selected.request?<div className="history-config">{Object.entries(selected.request).map(([key,value])=><span key={key}><b>{({query:'公司查询',report_year:'报告年度',lookback_years:'历史回溯年数',peer_codes:'同行代码',peer_group:'业务分组',selection_note:'同行选择理由',nonrecurring_ratio_threshold:'非经常损益阈值（比例）',minimum_negative_cash_quarters:'连续负现金流季度数',ocr_mode:'OCR方式',page_numbers:'PDF物理页码',company_name:'公司名称'} as Record<string,string>)[key]??key}</b>{Array.isArray(value)?value.join('、'):key==='ocr_mode'?({auto:'自动识别',force:'强制OCR',off:'关闭OCR'} as Record<string,string>)[String(value)]:String(value??'未设置')}</span>)}</div>:'旧任务未保存配置；恢复结果不受影响，重新分析时需重新设置。'}</dd></dl><label className="history-note-label">研究备注<textarea rows={4} maxLength={2000} value={note} onChange={event=>setNote(event.target.value)} placeholder="记录业务背景、需复核事项或后续研究计划"/></label><footer><small>记录和备注保存在本机，刷新或后端重启后仍可读取。</small><button className="primary-button" disabled={!!busy} onClick={()=>act(selected.id,async()=>{await financeApi.updateHistory(selected.id,{note});setSelected(null);setNotice('研究备注已保存')})}>保存备注</button></footer></section></div>}
  </section>
}
