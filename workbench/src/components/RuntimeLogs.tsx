import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ArrowDown, CheckCircle2, ChevronDown, ChevronUp, CircleAlert, Copy, Download, FileClock, Info, RefreshCw, Search } from 'lucide-react'
import { financeApi } from '../services/financeApi'
import type { HistoryRecord, LogLevel, RuntimeLog, RuntimeLogResponse } from '../types/finance'
import './runtimeLogs.css'

const levelNames = { info: '信息', success: '成功', warning: '警告', error: '错误' }
const levelIcons = { info: Info, success: CheckCircle2, warning: AlertTriangle, error: CircleAlert }
const statuses = { queued: '排队中', processing: '处理中', completed: '已完成', failed: '失败', review_required: '待人工复核', interrupted: '已中断' }
const kindNames = { financial: '财报分析', peer: '同行比较', announcement: '公告解析', scan: '扫描件识别' }
const running = (status?: string) => status === 'queued' || status === 'processing'
const beijing = (value: string, timeOnly = false) => new Date(value).toLocaleString('zh-CN', {
  timeZone: 'Asia/Shanghai', hour12: false, ...(timeOnly ? {} : {year:'numeric',month:'2-digit',day:'2-digit'}), hour:'2-digit',minute:'2-digit',second:'2-digit',
})
const textFor = (event: RuntimeLog) => `${beijing(event.timestamp)} [${levelNames[event.level]}] ${event.stage} · ${event.message}${event.details ? `\n  ${event.details}` : ''}`

export function RuntimeLogs({ currentTaskId, refreshKey, visible, onHistory, autoSelectLatest = true }: { currentTaskId?: string; refreshKey: number; visible: boolean; onHistory: () => void; autoSelectLatest?: boolean }) {
  const [choices, setChoices] = useState<HistoryRecord[]>([])
  const [selectedId, setSelectedId] = useState(currentTaskId ?? '')
  const [response, setResponse] = useState<RuntimeLogResponse>()
  const [events, setEvents] = useState<RuntimeLog[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [listError, setListError] = useState('')
  const [notice, setNotice] = useState('')
  const [level, setLevel] = useState<LogLevel | ''>('')
  const [query, setQuery] = useState('')
  const [collapsed, setCollapsed] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [following, setFollowing] = useState(true)
  const [refresh, setRefresh] = useState(0)
  const [now, setNow] = useState(Date.now())
  const listRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)

  useEffect(() => { if (currentTaskId) setSelectedId(currentTaskId); else if (!autoSelectLatest) setSelectedId('') }, [currentTaskId, refreshKey, autoSelectLatest])
  useEffect(() => {
    if(!visible)return
    let cancelled = false
    const controller=new AbortController()
    financeApi.listHistory({q:'',status:'',kind:'',year:'',start_date:'',end_date:'',favorites:false,trashed:false,page:1,page_size:100,sort:'recent'},controller.signal,false).then(data => {
      if (cancelled) return
      const sorted = [...data.items].sort((a,b) => b.created_at.localeCompare(a.created_at))
      setChoices(sorted)
      if (autoSelectLatest) setSelectedId(previous => previous || sorted[0]?.id || '')
      setListError('')
    }).catch(() => { if (!cancelled) setListError('无法连接后端，日志刷新已暂停。请确认后端已启动，并点击刷新。') })
    return () => { cancelled = true;controller.abort() }
  }, [currentTaskId, refreshKey, refresh, autoSelectLatest,visible])

  useEffect(() => {
    if (!selectedId) { setResponse(undefined); setEvents([]); setError(''); setLoading(false); return }
    if (!visible) return
    let cancelled = false
    let timer: number | undefined
    let cursor = 0
    setLoading(true); setResponse(undefined); setEvents([]); setError(''); setNotice('')
    followRef.current = true; setFollowing(true)
    const read = async () => {
      try {
        let latest: RuntimeLogResponse
        do {
          latest = await financeApi.getTaskLogs(selectedId, cursor)
          if (cancelled) return
          cursor = latest.next_seq
          setEvents(previous => [...new Map([...previous, ...latest.events].map(event => [event.seq, event])).values()].slice(-500))
          setResponse(latest)
        } while (latest.has_more)
        setLoading(false); setError(''); setNow(Date.now())
        if (running(latest.task.status)) timer = window.setTimeout(read, 2000)
      } catch (cause) {
        if (!cancelled) { setLoading(false); setError(`日志读取失败，自动刷新已暂停。${cause instanceof Error ? cause.message : '请检查后端连接'}。如提示接口不存在，请重启后端。`) }
      }
    }
    void read()
    return () => { cancelled = true; window.clearTimeout(timer) }
  }, [selectedId, refreshKey, refresh, visible])

  const filtered = useMemo(() => events.filter(event => (!level || event.level === level) &&
    (!query || `${event.stage} ${event.message} ${event.details}`.toLowerCase().includes(query.toLowerCase()))), [events,level,query])
  useEffect(() => { if (followRef.current && listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight }, [filtered,expanded,collapsed])
  useEffect(() => {
    if (!running(response?.task.status)) return
    const timer = window.setInterval(() => setNow(Date.now()),1000)
    return () => window.clearInterval(timer)
  }, [response?.task.status])
  const task = response?.task
  const panelError = error || listError
  const selected = choices.find(item => item.id === selectedId)
  const endTime = task ? running(task.status) ? now : Date.parse(task.finished_at || task.updated_at) : 0
  const elapsed = task ? Math.max(0,Math.floor((endTime - Date.parse(task.created_at)) / 1000)) : 0
  const duration = elapsed < 60 ? `${elapsed} 秒` : `${Math.floor(elapsed/60)} 分 ${elapsed%60} 秒`
  const jumpToLatest = () => { followRef.current = true; setFollowing(true); if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight }
  const copy = async () => {
    try { await navigator.clipboard.writeText(filtered.map(textFor).join('\n')); setNotice('已复制当前筛选的已加载日志') }
    catch { setNotice('浏览器未允许复制，请使用下载 TXT') }
  }

  return <div className="runtime-logs">
    <header className="log-header"><div className="log-task-selector"><FileClock size={20}/><label>查看任务<select aria-label="选择日志任务" value={selectedId} onChange={event => setSelectedId(event.target.value)}>
      <option value="">{choices.length ? '请选择一个任务查看日志' : '尚未创建分析任务'}</option>
      {selectedId && !selected && <option value={selectedId}>当前任务 · {selectedId.slice(0,8)}</option>}
      {choices.map(item => <option value={item.id} key={item.id}>{item.company} · {kindNames[item.kind]} · {item.file_name} · {item.id.slice(0,8)}</option>)}
    </select></label></div><div className="log-header-actions">
      {task && <span className={`log-task-status ${task.status}`}>{running(task.status) && <span className="button-loader"/>}{statuses[task.status]}</span>}
      <button className="secondary-button" disabled={loading} onClick={() => setRefresh(value => value + 1)}><RefreshCw size={15} className={loading ? 'spin' : ''}/>刷新</button>
      <button className="icon-button" aria-label={collapsed ? '展开运行日志' : '折叠运行日志'} aria-expanded={!collapsed} onClick={() => setCollapsed(value => !value)}>{collapsed ? <ChevronDown size={20}/> : <ChevronUp size={20}/>}</button>
    </div></header>
    {!collapsed && <>
      {task && <div className="log-task-meta"><span><b>{task.company_name || selected?.company || task.file_name || '当前任务'}</b> · {selected ? kindNames[selected.kind] : task.source === 'scan' ? '扫描件识别' : task.source === 'cninfo' ? '公开报告分析' : '本地文件分析'}</span><span>任务编号：<code title={task.id}>{task.id.slice(0,12)}</code></span><span>{running(task.status) ? '已运行' : task.status === 'review_required' ? '识别耗时' : '耗时'}：{duration}</span><span>最后更新：{beijing(task.updated_at)}</span></div>}
      <div className="log-toolbar"><div className="log-levels" aria-label="日志级别筛选">{(['','info','success','warning','error'] as const).map(value => <button key={value} aria-pressed={level === value} className={level === value ? 'selected' : ''} onClick={() => setLevel(value)}>{value ? levelNames[value] : '全部'}</button>)}</div>
        <label className="log-search"><Search size={16}/><input aria-label="搜索运行日志" placeholder="搜索阶段、提示或原因" value={query} onChange={event => setQuery(event.target.value)}/></label>
        <div className="log-export"><button className="secondary-button" onClick={copy} disabled={!filtered.length}><Copy size={15}/>复制</button><a className={`secondary-button ${!selectedId ? 'disabled' : ''}`} aria-disabled={!selectedId} tabIndex={selectedId ? 0 : -1} href={selectedId ? financeApi.getTaskLogDownloadUrl(selectedId,level,query) : undefined} download><Download size={15}/>下载 TXT</a></div>
      </div>
      {panelError && <div className="log-error" role="alert"><CircleAlert size={18}/><span>{panelError}</span></div>}
      {task?.status === 'review_required' && <p className="log-state-note">识别已结束，等待人工复核。日志不会自动刷新；复核提交后可点击刷新。</p>}
      {task?.status === 'interrupted' && <p className="log-state-note">服务曾停止，任务已中断。原文件和日志保留，可在历史记录重新分析。</p>}
      <div className={`log-list ${expanded ? 'expanded' : ''}`} ref={listRef} role="region" aria-label="任务日志列表" tabIndex={0} onScroll={event => {
        const element = event.currentTarget
        const next = element.scrollHeight - element.scrollTop - element.clientHeight < 32
        followRef.current = next; setFollowing(next)
      }}>
        <div className="log-column-head"><span>时间（北京时间）</span><span>级别</span><span>处理阶段 / 说明</span></div>
        {!filtered.length ? <div className="log-empty"><FileClock size={26}/><strong>{loading ? '正在读取真实运行日志…' : panelError ? '暂时无法读取日志' : !selectedId ? choices.length ? '尚未选择分析任务' : '尚未创建分析任务' : response && !response.has_logs ? '该历史任务未记录详细运行日志' : '没有符合筛选条件的日志'}</strong><p>{!selectedId ? '新建任务后自动展示其实际执行过程，也可在上方手动选择已保存的历史任务。' : response && !response.has_logs ? `仅保留任务最后阶段：${task?.stage || '未知'}。不会补造过去的执行记录。` : '可以切换级别、清除关键词或点击刷新。'}</p></div>
          : filtered.map(event => { const Icon = levelIcons[event.level]; return <article className="log-row" key={event.id}><time title={beijing(event.timestamp)}>{beijing(event.timestamp,true)}</time><span className={`log-level ${event.level}`}><Icon size={15}/>{levelNames[event.level]}</span><div><strong>{event.stage}</strong><p>{event.message}</p>{event.details && <details><summary>查看详情与提示</summary><p>{event.details}</p></details>}</div></article> })}
      </div>
      <footer className="log-footer"><span>{notice || (response?.total && response.total > 500 ? `仅展示最近 500 条，下载 TXT 可获得完整筛选日志。` : `已加载 ${events.length} 条 · 显示 ${filtered.length} 条`)}{running(task?.status) && !panelError ? ' · 每 2 秒自动刷新' : task && !panelError ? ' · 自动刷新已停止' : ''}</span><div><button className="text-button" onClick={jumpToLatest} disabled={!filtered.length}><ArrowDown size={14}/>{following ? '跟随最新' : '跳到最新'}</button><button className="text-button" onClick={() => setExpanded(value => !value)}>{expanded ? '收起高度' : '展开高度'}</button><button className="text-button" onClick={onHistory}>历史记录</button></div></footer>
    </>}
  </div>
}
