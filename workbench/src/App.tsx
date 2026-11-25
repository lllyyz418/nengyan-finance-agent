import { useCallback, useEffect, useRef, useState } from 'react'
import { Activity, Archive, ArrowLeft, BarChart3, Bell, Bot, Building2, ChevronDown, ClipboardCheck, Database, Eye, FileClock, FileScan, FileSearch, FileUp, GitCompareArrows, Menu, Play, Settings2, ShieldCheck, Sparkles, UserRound, X } from 'lucide-react'
import { MetricCard } from './components/MetricCard'
import { RiskPanel } from './components/RiskPanel'
import { ChartsGrid } from './components/ChartsGrid'
import { HistoryBackfillPanel } from './components/HistoryBackfillPanel'
import { FinancialTable } from './components/FinancialTable'
import { EvidenceDrawer } from './components/EvidenceDrawer'
import { UploadDialog } from './components/UploadDialog'
import { CompanySearch } from './components/CompanySearch'
import {ReportControls} from './components/ReportControls'
import {PeriodMetrics} from './components/PeriodMetrics'
import {supportedReport,reportTypeLabel,reportPeriodLabel} from './data/reportPeriods'
import {financialLabel} from './data/financialLabels'
import type {ReportType,PeriodBasis} from './data/reportPeriods'
import { Comparison } from './components/Comparison'
import { TaskMessage, TaskProgress } from './components/TaskProgress'
import type { TaskActivity } from './components/TaskProgress'
import { DocumentStatus } from './components/DocumentStatus'
import { AnnouncementPanel } from './components/AnnouncementPanel'
import { ResearchReportPanel } from './components/ResearchReportPanel'
import { ScanReviewCenter } from './components/ScanReviewCenter'
import { financeApi, resultToSnapshot } from './services/financeApi'
import { snapshots } from './data/mockData'
import { useScrollNavigation } from './hooks/useScrollNavigation'
import { HistoryCenter } from './components/HistoryCenter'
import { RuntimeLogs } from './components/RuntimeLogs'
import { AnalysisPlaceholder } from './components/AnalysisPlaceholder'
import { createEmptySnapshot } from './data/emptyState'
import { companySegments, suggestedPeersForTarget, savedPeerSelection, MAX_PEERS } from './data/peerCatalog'
import type { AnalysisSettings, AnalysisSnapshot, AnalysisViewMode, BackendResult, Evidence, HistoryRecord, PeerSelection } from './types/finance'

const emptySnapshot = createEmptySnapshot()

const navItems = [
  { label: '分析总览', icon: BarChart3, id: 'overview', group: 'workspace' },
  { label: '分析报告', icon: Bot, id: 'agent-report', group: 'workspace' },
  { label: '核心财务指标', icon: Activity, id: 'core-metrics', group: 'workspace' },
  { label: '风险复核信号', icon: ShieldCheck, id: 'risks', group: 'workspace' },
  { label: '历史趋势与数据质量', icon: FileClock, id: 'history', group: 'workspace' },
  { label: '财务指标与可用性', icon: ClipboardCheck, id: 'metrics', group: 'workspace' },
  { label: '人工指定同行比较', icon: GitCompareArrows, id: 'comparison', group: 'workspace' },
  { label: '扫描件人工复核', icon: FileScan, id: 'scan-review', group: 'disclosure' },
  { label: '重大公告事件候选', icon: Archive, id: 'announcements', group: 'disclosure' },
  { label: '分析配置', icon: Settings2, id: 'configuration', group: 'disclosure' },
  { label: '运行日志', icon: FileClock, id: 'runtime-logs', group: 'disclosure' },
  { label: '历史记录', icon: Archive, id: 'history-records', group: 'archive' },
]
const scrollNavItems = navItems.filter(item=>item.id!=='history-records')

function App() {
  const [metricFocus,setMetricFocus]=useState<{name:string;revision:number}>()
  const [historyOpen, setHistoryOpen] = useState(false)
  const [logTask, setLogTask] = useState<{id?: string; revision: number}>({revision: 0})
  const selectLogTask = useCallback((id: string) => setLogTask(previous => ({id, revision: previous.revision + 1})), [])
  const [scanRestoreId, setScanRestoreId] = useState<string>()
  const [announcementRestore, setAnnouncementRestore] = useState<{id:string;result:BackendResult;fileName:string}>()
  const [activeNav, setActiveNav] = useState('分析总览')
  useScrollNavigation(scrollNavItems, setActiveNav, !historyOpen)
  useEffect(() => {
    const list = document.querySelector<HTMLElement>('.sidebar-navigation')
    const selected = list?.querySelector<HTMLElement>('button.active')
    if (!list || !selected) return
    const box = selected.getBoundingClientRect(), viewport = list.getBoundingClientRect()
    if (box.top < viewport.top) list.scrollTop -= viewport.top - box.top + 8
    else if (box.bottom > viewport.bottom) list.scrollTop += box.bottom - viewport.bottom + 8
  }, [activeNav])
  const [company, setCompany] = useState('')
  const [period, setPeriod] = useState('2025年年度报告')
  const [fileType, setFileType] = useState<ReportType>('annual')
  const [reportQuarter,setReportQuarter]=useState(1)
  const [loadedResult,setLoadedResult]=useState<BackendResult>()
  const [periodBasis,setPeriodBasis]=useState<PeriodBasis>('cumulative')
  const [segment, setSegment] = useState('动力及储能电池')
  const [savedSnapshot, setSnapshot] = useState<AnalysisSnapshot>(createEmptySnapshot)
  const [viewMode, setViewMode] = useState<AnalysisViewMode>('empty')
  const [loading, setLoading] = useState(false)
  const activeTaskRef = useRef<string>()
  const stopRequestedRef = useRef(false)
  const analysisRunningRef = useRef(false)
  const [activity, setActivity] = useState<TaskActivity>()
  const [evidence, setEvidence] = useState<Evidence | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const evidenceRequest = useRef(0)
  const [uploadOpen, setUploadOpen] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const [toast, setToast] = useState('')
  useEffect(() => {
    if (!toast || loading) return
    const timer = window.setTimeout(() => setToast(''), 8000)
    return () => window.clearTimeout(timer)
  }, [toast, loading])
  const [backendOnline, setBackendOnline] = useState(false)
  const [backendIssue,setBackendIssue]=useState('')
  const [llmConfigured, setLlmConfigured] = useState(false)
  const [periodSupport,setPeriodSupport]=useState(false)
  const [companyQuery, setCompanyQuery] = useState('')
  const [selectedCompanyCode, setSelectedCompanyCode] = useState('')
  const [taskId, setTaskId] = useState<string>()
  useEffect(()=>setMetricFocus(undefined),[taskId])
  // The peer task is a derived task; keep the report/backfill task as the history source.
  const [historyTaskId, setHistoryTaskId] = useState<string>()
  const [reportMarkdown, setReportMarkdown] = useState('')
  const [reportSummary,setReportSummary]=useState('')
  const [reportGenerator, setReportGenerator] = useState('')
  const [savedHistory, setSavedHistory] = useState<Record<string,any>>()
  const [autoFollowBackfill,setAutoFollowBackfill]=useState(false)
  const [settings, setSettings] = useState<AnalysisSettings>({ lookbackYears: 2, nonrecurringRatioThreshold: 30, minimumNegativeCashQuarters: 3, announcementOcrMode: 'auto' })
  const [peerSelection, setPeerSelection] = useState<PeerSelection>(()=>({peers:[],group:'',note:''}))
  const peerReportKey = useRef('')
  const peerRestoreRevision = useRef(0)
  const loadedTaskRef = useRef<string>()
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = Number(window.localStorage.getItem('neyan-sidebar-width'))
    return Number.isFinite(saved) ? Math.min(360, Math.max(260, saved)) : 340
  })
  const resizingSidebar = useRef(false)
  useEffect(() => { document.documentElement.style.setProperty('--sidebar-width', `${sidebarWidth}px`); window.localStorage.setItem('neyan-sidebar-width', String(sidebarWidth)) }, [sidebarWidth])
  useEffect(() => {
    const move = (event: PointerEvent) => { if (resizingSidebar.current) setSidebarWidth(Math.min(360, Math.max(260, event.clientX))) }
    const stop = () => { resizingSidebar.current = false; document.body.classList.remove('sidebar-resizing') }
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', stop)
    return () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', stop) }
  }, [])

  const isDemo = viewMode === 'demo'
  const isReal = viewMode === 'result' && Boolean(taskId)
  const hasData = isDemo || isReal
  const snapshot = isDemo ? snapshots['宁德时代'] : isReal ? savedSnapshot : emptySnapshot
  const single=loadedResult?.analysis?.single_quarter
  const interim=isReal&&!['annual','annual_report'].includes(snapshot.document.reportType)
  const metricView=isReal&&periodBasis==='single'&&single&&loadedResult?resultToSnapshot({...loadedResult,analysis:{...loadedResult.analysis,period_basis:'single',metrics:single.metrics,unavailable_metrics:single.unavailable_metrics}}):snapshot
  const switchView = (mode: AnalysisViewMode) => { evidenceRequest.current += 1; setViewMode(mode); setDrawerOpen(false); setEvidence(null) }
  useEffect(() => { financeApi.health().then((health) => { setBackendOnline(health.status === 'ok'); setLlmConfigured(health.llm_configured);setPeriodSupport(Boolean(health.report_types?.includes('quarterly')));setBackendIssue(health.api_revision==='upload-errors-1'&&health.report_types?.includes('quarterly')?'':'后端版本尚未更新：请重启后端，再刷新网页。只刷新网页不会加载新的后端代码，无需删除历史记录或 PDF。') }).catch(() => {setBackendOnline(false);setBackendIssue('后端连接失败或读取超时，请确认后端已启动。已有历史文件不会因此丢失。')}) }, [])
  const openEvidence = useCallback(async (id: string, sourceTaskId?:string) => {
    if (viewMode === 'empty' || (viewMode === 'result' && !taskId)) return
    const requestId = ++evidenceRequest.current
    try {
      const nextEvidence = await financeApi.getEvidence(id, viewMode === 'result' ? sourceTaskId??taskId : undefined)
      if (requestId !== evidenceRequest.current) return
      setEvidence(nextEvidence); setDrawerOpen(true)
    }
    catch (error) { if (requestId === evidenceRequest.current) setToast(error instanceof Error ? error.message : '证据读取失败') }
  }, [taskId, viewMode])
  const navigate = (label: string, id: string) => {
    setActiveNav(label); setMobileNav(false); setHistoryOpen(id==='history-records')
    requestAnimationFrame(()=>{if(id==='history-records') window.scrollTo({top:0});else document.getElementById(id)?.scrollIntoView({ behavior:'smooth',block:'start' })})
  }
  const returnToWorkspace = () => {
    peerRestoreRevision.current += 1; loadedTaskRef.current = undefined
    switchView('empty'); setUploadOpen(false); setToast('')
    setLogTask(previous => ({revision: previous.revision + 1}))
    navigate('分析总览', 'overview')
  }
  const stopActiveTask = async () => {
    const id = activeTaskRef.current
    if (!analysisRunningRef.current || stopRequestedRef.current) return
    stopRequestedRef.current = true
    setActivity(previous => previous ? {...previous, stopping:true} : previous)
    if (!id) return // Creation is still in flight; cancel as soon as its ID arrives.
    try {
      await financeApi.cancelTask(id)
    } catch (error) {
      if (activeTaskRef.current !== id) return
      stopRequestedRef.current = false
      setActivity(previous => previous ? {...previous, stopping:false} : previous)
      setToast(error instanceof Error ? error.message : '停止请求发送失败，请重试')
    }
  }
  const applyResult = (result: BackendResult, id: string, autoFollow=false, preserveHistory=false) => {
    setLoadedResult(result);setPeriodBasis('cumulative')
    evidenceRequest.current += 1
    selectLogTask(id)
    const next = resultToSnapshot(result)
    const sourceDoc=result.analysis?.documents?.[0]??{}
    setFileType(['half_year','half_year_report','semiannual'].includes(sourceDoc.report_type)?'semiannual':sourceDoc.report_type==='quarterly'?'quarterly':'annual')
    setReportQuarter(sourceDoc.report_quarter??1)
    loadedTaskRef.current = id
    const revision = ++peerRestoreRevision.current
    const key = `${next.ticker}|${next.document.reportYear}|${next.document.fileHash === '—' ? id : next.document.fileHash}`
    const sameReport = peerReportKey.current === key
    const keepComparison = sameReport && next.comparison.length === 0
    const restoredSelection = savedPeerSelection(result)
    if (restoredSelection && !keepComparison) setPeerSelection(restoredSelection)
    else if (peerReportKey.current !== key) setPeerSelection(suggestedPeersForTarget(next.ticker))
    peerReportKey.current = key
    void financeApi.getSavedPeerResult(id).then(saved=>{
      if (revision !== peerRestoreRevision.current || !saved.result) return
      const restored = resultToSnapshot(saved.result)
      setSnapshot(previous=>({...previous,ticker:restored.ticker,comparison:restored.comparison}))
      const selection=savedPeerSelection(saved.result)
      if(selection)setPeerSelection(selection)
    }).catch(()=>{})
    setViewMode('result')
    setTaskId(id);if(!preserveHistory)setHistoryTaskId(id);setSnapshot(previous=>keepComparison?{...next,comparison:previous.comparison}:next);setCompany(next.company);setCompanyQuery(next.company);setPeriod(next.period);setSelectedCompanyCode(next.ticker);setSegment(next.segment)
    setReportMarkdown(result.report_markdown);setReportSummary(result.report_summary_markdown??'');setReportGenerator(result.report_generator);setDrawerOpen(false)
    // A peer-only result may not carry the historical backfill payload. Never clear
    // the already loaded history just because a derived comparison task omitted it.
    setSavedHistory(preserveHistory ? (savedHistory ?? result.payload?.history) : result.payload?.history)
    setAutoFollowBackfill(autoFollow)
  }
  const openHistory = (record: HistoryRecord, result: BackendResult) => {
    selectLogTask(record.id)
    if (viewMode === 'demo') switchView('empty')
    setHistoryOpen(false)
    let target='overview', label='分析总览'
    if(record.kind==='scan' && record.task_status==='review_required'){setScanRestoreId(record.id);target='scan-review';label='扫描件人工复核'}
    else if(record.kind==='announcement'){setAnnouncementRestore({id:record.id,result,fileName:record.file_name});target='announcements';label='重大公告事件候选'}
    else {applyResult(result,record.id)}
    setActiveNav(label);setToast(result.payload?.history?.basis_view?.read_only?'已打开历史任务：财务原值保持不变，历史口径按当前精度规则核对':'已打开历史任务：使用当时保存的结果，没有重新计算')
    requestAnimationFrame(()=>document.getElementById(target)?.scrollIntoView({behavior:'smooth',block:'start'}))
    window.setTimeout(()=>setToast(''),5000)
  }
  const startAnalysis = async (includePeers = false, confirmedCode?:string) => {
    if (analysisRunningRef.current) return
    if(!periodSupport&&(!includePeers&&fileType!=='annual'||includePeers&&interim)){setToast('当前后端尚未支持中期报告，请先重启后端，再刷新网页。');return}
    if (!includePeers && !/^\d{6}$/.test(selectedCompanyCode)) {setToast('请先从公司候选列表中选择公司，再搜索该公司的公开报告');return}
    peerRestoreRevision.current += 1
    analysisRunningRef.current = true
    stopRequestedRef.current = false
    setLoading(true); setToast('')
    setActivity({kind:includePeers ? 'peer' : 'report', stage:includePeers ? '正在提交同行比较任务' : '正在提交公开报告分析任务', progress:0, stopping:false})
    try {
      const targetCode = confirmedCode || savedSnapshot.ticker
      const baseTaskId = taskId
      if (includePeers && (!isReal || !baseTaskId || !supportedReport(savedSnapshot.document.reportType))) throw new Error('请先载入并分析一份期间明确的定期报告')
      if (includePeers && !/^\d{6}$/.test(targetCode)) throw new Error('请先对照报告确认目标公司的证券代码')
      const effectivePeers = { ...peerSelection, peers: peerSelection.peers.filter((peer) => peer.code !== targetCode) }
      if (includePeers && effectivePeers.peers.length < 2) throw new Error('请至少选择两家不包含目标公司的同行企业')
      if (includePeers && effectivePeers.peers.length > MAX_PEERS) throw new Error('一次最多选择五家同行，请先移除多余公司')
      if (!includePeers) switchView('empty')
      let task = includePeers
        ? await financeApi.createPeerTask(baseTaskId!, effectivePeers, targetCode,periodBasis)
        : await financeApi.createOnlineTask(selectedCompanyCode, Number(period.slice(0, 4)), settings,undefined,fileType,reportQuarter)
      activeTaskRef.current = task.id
      if (stopRequestedRef.current) {
        try { task = await financeApi.cancelTask(task.id) }
        catch (error) { stopRequestedRef.current = false; setToast(error instanceof Error ? error.message : '停止请求失败，请重试') }
      }
      setActivity(previous => previous ? {...previous, stage:task.stage, progress:task.progress, stopping:stopRequestedRef.current} : previous)
      selectLogTask(task.id)
      while (!['completed', 'failed', 'review_required','interrupted'].includes(task.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000))
        task = await financeApi.getTaskProgress(task.id)
        setActivity(previous => previous ? {...previous, stage:task.stage, progress:task.progress, stopping:stopRequestedRef.current} : previous)
      }
      if (task.status === 'interrupted') {
        setToast(includePeers ? '同行比较已停止' : '公开报告分析已停止')
        return
      }
      if (task.status === 'failed') throw new Error(task.error || '公开报告分析失败')
      const result = await financeApi.getTaskResult(task.id)
      if (includePeers) {
        if (loadedTaskRef.current === baseTaskId) {
          const peerResult = resultToSnapshot(result)
          setSnapshot(previous=>({...previous,ticker:peerResult.ticker,comparison:peerResult.comparison}))
        }
      } else applyResult(result,task.id,true)
      setToast(includePeers ? '同行比较已完成，可查看各公司指标与同行中位数' : '公开报告分析已完成')
    } catch (error) { setToast(error instanceof Error ? error.message : '分析失败，请检查后端和网络') } finally { activeTaskRef.current = undefined; analysisRunningRef.current = false; stopRequestedRef.current = false; setActivity(undefined); setLoading(false) }
  }
  const triggered = snapshot.risks.filter((r) => r.status === '已触发').length
  const policyFollowups = snapshot.risks.filter(r=>['发现变化，需复核','需人工确认'].includes(r.status)).length

  return <div className={`app-shell ${activity ? 'has-active-task' : ''}`}>
    <button className={`mobile-overlay ${mobileNav ? 'show' : ''}`} onClick={() => setMobileNav(false)} aria-label="关闭导航"/>
    <aside className={`sidebar ${mobileNav ? 'mobile-open' : ''}`}>
      <div className="brand"><span className="brand-mark" aria-label="能研财智 Logo"><svg viewBox="0 0 36 36" aria-hidden="true"><path d="M8 24.5 14.5 18l4.2 4.1L28 12.5" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"/><circle cx="8" cy="24.5" r="2.4" fill="#0f766e"/><circle cx="28" cy="12.5" r="2.4" fill="#0f766e"/><path d="M18 8.5c4.8.4 8 3.1 9.5 7.5-4.5.5-8.1-1-9.5-7.5Z" fill="#55b7a7" opacity=".9"/></svg></span><div><strong>能研财智</strong><small>新能源企业财务研究平台</small></div><button className="mobile-close" onClick={() => setMobileNav(false)}><X size={18}/></button></div>
      <div className="sidebar-navigation"><div className="side-label">分析工作台</div><nav aria-label="主导航">{navItems.filter(item=>item.group==='workspace').map(({label,icon:Icon,id}) => <button key={id} aria-current={activeNav === label ? 'location' : undefined} className={activeNav === label ? 'active' : ''} onClick={() => navigate(label,id)}><Icon size={20}/><span>{label}</span>{id === 'risks' && hasData && <i>{triggered}</i>}</button>)}</nav>
      <div className="side-label second">披露与配置</div><nav aria-label="披露与配置导航">{navItems.filter(item=>item.group==='disclosure').map(({label,icon:Icon,id}) => <button key={id} aria-current={activeNav === label ? 'location' : undefined} className={activeNav === label ? 'active' : ''} onClick={() => navigate(label,id)}><Icon size={20}/><span>{label}</span></button>)}</nav>
      <div className="side-label second">任务档案</div><nav aria-label="任务档案导航">{navItems.filter(item=>item.group==='archive').map(({label,icon:Icon,id})=><button key={id} aria-current={activeNav===label?'page':undefined} className={activeNav===label?'active':''} onClick={()=>navigate(label,id)}><Icon size={20}/><span>{label}</span></button>)}</nav></div>
      <div className="sidebar-spacer"/><div className="data-freshness"><span><Database size={15}/>引擎状态</span><strong><i className={backendOnline ? '' : 'offline'}/>financial-engine v0.17</strong><small>{backendOnline ? `后端已连接 · ${llmConfigured ? 'AI报告可用' : '模板报告模式'}` : '后端未连接 · 请启动后端以创建任务'}</small></div><div className="user-card"><span><UserRound size={18}/></span><div><strong>研究员账户</strong><small>人工复核与审计工作台</small></div><ChevronDown size={15}/></div>
      <button className="sidebar-resize-handle" aria-label="拖拽调整侧边栏宽度" title="拖拽调整宽度" onPointerDown={(event) => { event.preventDefault(); resizingSidebar.current = true; document.body.classList.add('sidebar-resizing') }} />
    </aside>

    <main><header className="topbar"><button className="menu-button" onClick={() => setMobileNav(true)}><Menu size={21}/></button><div className="breadcrumb"><span>披露分析</span><i>/</i><strong>{activeNav}</strong></div><div className="top-actions"><span className="engine-badge"><i/>{backendOnline ? '后端已连接' : '后端未连接'}</span><span className={`view-mode-badge ${viewMode}`}>{isDemo ? '演示数据' : isReal ? '真实任务' : loading ? '分析处理中' : '等待分析'}</span>{loading&&<button className="stop-button" disabled={activity?.stopping} onClick={()=>void stopActiveTask()}><X size={15}/>{activity?.stopping ? '停止中…' : '停止分析'}</button>}<button className="secondary-button workspace-return" disabled={loading} title={loading ? '分析执行中，请完成后返回工作台' : '回到总览并恢复等待分析，已保存报告不会删除'} onClick={returnToWorkspace}><ArrowLeft size={16}/>返回工作台</button><button className="icon-button" aria-label="通知"><Bell size={18}/><i className="notification-dot"/></button></div></header>
      <div className="content">
        {historyOpen&&<HistoryCenter onOpen={openHistory} onBack={returnToWorkspace} onTaskCreated={selectLogTask}/>}
        <div hidden={historyOpen}>
        <section className="analysis-overview" id="overview"><div className="workspace-head"><div className="company-title"><span className="company-logo">{!hasData ? <FileSearch size={26}/> : snapshot.company === '宁德时代' ? 'CATL' : snapshot.company === '亿纬锂能' ? 'EVE' : 'NE'}</span><div><span className="eyebrow">公开披露分析工作台</span><h1>{hasData ? snapshot.company : '新能源财务分析'}{hasData && <small>{snapshot.ticker}</small>}</h1></div></div><div className="report-summary refined"><span>{isDemo ? '演示状态 · 非真实结论' : '本次分析状态'}</span><strong><ShieldCheck size={18}/>{!hasData ? loading ? '正在执行真实分析' : '等待分析' : triggered || policyFollowups ? '存在需复核事项' : snapshot.risks.some(r=>['数据不足','尚未评估'].includes(r.status))?'部分规则尚待评估':'本次已评估项目未发现触发项'}</strong><small>{hasData ? `${triggered}项规则已触发${policyFollowups?` · ${policyFollowups}项政策待复核`:''} · ${snapshot.document.unavailable}项指标不可计算 · ${snapshot.document.warnings}项系统警告` : '尚无分析结果，不展示风险判断'}</small></div></div>
        <div className={`analysis-mode-banner ${viewMode}`} role="status"><div className="analysis-mode-message">{isDemo ? <Eye size={22}/> : <FileSearch size={22}/>}<div><strong>{isDemo ? '演示模式 · 以下内容均为模拟样例' : isReal ? '当前展示已保存的真实任务结果' : loading ? '正在分析，请等待引擎返回' : '尚未载入分析结果'}</strong></div></div><div className="analysis-mode-actions"><button className="secondary-button" disabled={loading} onClick={()=>switchView(isDemo ? taskId ? 'result' : 'empty' : 'demo')}>{isDemo ? <X size={16}/> : <Eye size={16}/>} {isDemo ? taskId ? '返回真实结果' : '退出演示' : '查看演示'}</button>{!hasData && <button className="secondary-button" onClick={()=>navigate('历史记录','history-records')}><Archive size={16}/>历史记录</button>}</div></div>

<ReportControls type={fileType} year={Number(period.slice(0,4))} quarter={reportQuarter} segment={segment} segments={[...new Set([segment,...companySegments])]} loading={loading} canSearch={backendOnline&&/^\d{6}$/.test(selectedCompanyCode)} onType={setFileType} onYear={year=>setPeriod(String(year))} onQuarter={setReportQuarter} onSegment={setSegment} onUpload={()=>setUploadOpen(true)} onSearch={()=>void startAnalysis(false)}><CompanySearch value={companyQuery} code={selectedCompanyCode} year={Number(period.slice(0,4))} disabled={loading} onInput={value=>{setCompanyQuery(value);setSelectedCompanyCode('')}} onSelect={item=>{setCompany(item.name);setCompanyQuery(item.name);setSelectedCompanyCode(item.code);setSegment(item.segment)}}/></ReportControls>

        {backendIssue&&<div className="history-message error" role="alert">{backendIssue}</div>}
        <DocumentStatus data={snapshot.document} company={snapshot.company} demo={isDemo} pending={!hasData}/>
        </section>
        <div className={`page-content ${loading ? 'is-loading' : ''}`}>
          <section className="section-block" id="agent-report"><div className="section-heading"><div><span className="section-index agent">AI</span><div><h2>分析报告</h2></div></div><span className="coverage-tag">{isDemo ? '演示模式' : isReal && reportMarkdown ? '财务结果已保存 · 研究解读独立生成' : '等待真实任务'}</span></div><ResearchReportPanel taskId={isReal?taskId:undefined} markdown={isReal ? reportMarkdown : ''} summary={isReal ? reportSummary : ''} generator={isReal ? reportGenerator : ''} demo={isDemo} onEvidence={openEvidence}/></section>
          <section className="section-block" id="core-metrics"><div className="section-heading"><div><span className="section-index">01</span><div><h2>核心财务指标</h2></div></div><span className="as-of">{hasData ? `${isDemo ? '演示 · ' : ''}${snapshot.period}` : '等待报告 · 不以零替代缺失数据'}</span></div><div className="metrics-grid">{snapshot.metrics.map((metric) => <MetricCard key={metric.id} metric={metric} pending={!hasData}/>)}</div></section>
          <section className="section-block" id="risks"><div className="section-heading"><div><span className="section-index alert">02</span><div><h2>风险复核信号</h2><p>组合规则的方向性筛查，不替代审计和投资判断</p></div></div><span className="risk-count"><ShieldCheck size={15}/>{hasData ? `${isDemo ? '演示 · ' : ''}${triggered}项已触发${policyFollowups?` · ${policyFollowups}项政策待复核`:''}` : '等待筛查'}</span></div>{hasData ? <RiskPanel risks={snapshot.risks} onEvidence={openEvidence}/> : <AnalysisPlaceholder icon={ShieldCheck} title="尚未进行风险筛查" description="报告完成解析后才会执行规则判断。没有数据不代表没有风险，也不会预先展示示例结论或证据。"/>}</section>
          <section className="section-block" id="history"><div className="section-heading"><div><span className="section-index">03</span><div><h2>历史趋势与数据质量</h2><p>跨年变化、历史口径核对与指标可用性</p></div></div></div>{isDemo?<ChartsGrid demo document={snapshot.document}/>:isReal&&historyTaskId?<HistoryBackfillPanel key={historyTaskId} taskId={historyTaskId} history={savedHistory} document={snapshot.document} defaultLookback={settings.lookbackYears} autoFollow={autoFollowBackfill} onTaskCreated={selectLogTask} onEvidence={openEvidence} onMetric={code=>{setMetricFocus(previous=>({name:financialLabel(code),revision:(previous?.revision??0)+1}));navigate('财务指标与可用性','metrics')}} onResult={(result,id)=>{applyResult(result,id);setToast('已打开历史补齐新版本，原分析和原文件仍保留')}}/>:<AnalysisPlaceholder icon={BarChart3} title="暂无可展示的趋势" description="取得足够的跨年度真实数据后再绘制图表。首次打开不加载模拟曲线，不补造缺失年份。"/>}</section>
          <section className="section-block" id="metrics"><div className="section-heading"><div><span className="section-index">04</span><div><h2>财务指标与可用性</h2><p>不可计算、冲突和缺失均显示具体原因，不以零替代</p></div></div></div><PeriodMetrics interim={interim} label={snapshot.period} singleAvailable={Boolean(single)} basis={periodBasis} onBasis={setPeriodBasis} data={metricView.financialMetrics} onEvidence={openEvidence} company={snapshot.company} period={`${snapshot.period} · ${periodBasis==='single'?'单季度':'累计'}`} demo={isDemo} searchFocus={metricFocus}/></section>
          <section className="section-block" id="comparison"><div className="section-heading"><div><span className="section-index">05</span><div><h2>人工指定同行比较</h2><p>选择 2—5 家同行，读取同年度、同报告期间和同口径的数据</p></div></div><span className="coverage-tag"><GitCompareArrows size={13}/>{peerSelection.peers.length} 家已选同行</span></div><Comparison periodLabel={isReal?reportPeriodLabel(snapshot.document.reportType,snapshot.document.reportYear,snapshot.document.reportQuarter,periodBasis):''} interim={interim} basis={periodBasis} rows={snapshot.comparison} company={isReal ? savedSnapshot.company : ''} targetCode={isReal ? savedSnapshot.ticker : ''} year={isReal ? Number(savedSnapshot.document.reportYear) : 0} reportLoaded={isReal} annual={isReal && supportedReport(savedSnapshot.document.reportType)} onUpload={()=>setUploadOpen(true)} onViewReport={()=>navigate('分析报告','agent-report')} selection={peerSelection} loading={loading} activity={activity?.kind === 'peer' ? activity : undefined} onStop={() => void stopActiveTask()} onChange={value=>{peerRestoreRevision.current+=1;setPeerSelection(value)}} onAnalyze={(code)=>void startAnalysis(true,code)}/></section>
          <section className="section-block" id="scan-review"><div className="section-heading"><div><span className="section-index review">06</span><div><h2>扫描件人工复核</h2><p>确认、纠正或拒绝 OCR 候选后，才允许进入指标计算</p></div></div><span className="coverage-tag">v0.17 确认链</span></div><ScanReviewCenter restoreTaskId={scanRestoreId} onTaskCreated={selectLogTask}/></section>
          <section className="section-block" id="announcements"><div className="section-heading"><div><span className="section-index">07</span><div><h2>重大公告事件候选</h2><p>上传真实公告，提取事件、金额、风险条款与原文位置</p></div></div><span className="coverage-tag">4 类事件已覆盖</span></div><AnnouncementPanel ocrMode={settings.announcementOcrMode} restored={announcementRestore} onTaskCreated={selectLogTask}/></section>
<section className="section-block" id="configuration"><div className="section-heading"><div><span className="section-index">08</span><div><h2>分析配置</h2><p>以下参数仅影响下一次分析；已保存结果不会自动重算</p></div></div><span className="config-active">已启用</span></div><div className="configuration-card active-config">
  <label><span>历史回溯年限</span><select value={settings.lookbackYears} onChange={(e) => setSettings({ ...settings, lookbackYears: Number(e.target.value) })}>{[0,1,2,3,4,5].map(years=><option key={years} value={years}>{years?`向前 ${years} 年`:'仅本期'}</option>)}</select><small>含本期共 {settings.lookbackYears+1} 个年度；上传先出本期结果，再独立补齐历史</small></label>
  <label><span>非经常性损益筛查阈值</span><div className="config-number"><input type="number" min="0" max="100" value={settings.nonrecurringRatioThreshold} onChange={(e) => setSettings({ ...settings, nonrecurringRatioThreshold: Math.max(0, Math.min(100, Number(e.target.value))) })}/><b>%</b></div><small>占归母净利润比例超过该值时提示</small></label>
  <label><span>连续负经营现金流季度数</span><select value={settings.minimumNegativeCashQuarters} onChange={(e) => setSettings({ ...settings, minimumNegativeCashQuarters: Number(e.target.value) })}><option value={2}>连续 2 季度</option><option value={3}>连续 3 季度</option><option value={4}>连续 4 季度</option></select><small>用于现金回款压力筛查；实际连续季度数与本次阈值在该规则中展示</small></label>
  <label><span>公告 OCR 方式</span><select value={settings.announcementOcrMode} onChange={(e) => setSettings({ ...settings, announcementOcrMode: e.target.value as AnalysisSettings['announcementOcrMode'] })}><option value="auto">自动识别（推荐）</option><option value="force">强制 OCR</option><option value="off">关闭 OCR</option></select><small>{settings.announcementOcrMode === 'auto' ? '仅扫描页执行OCR，兼顾速度与准确性' : settings.announcementOcrMode === 'force' ? '每页重新识别，适合文字层乱码' : '只读取PDF自带文字，速度最快'}</small></label>
</div></section>
          <section className="section-block" id="runtime-logs"><div className="section-heading"><div><span className="section-index">09</span><div><h2>运行日志</h2><p>查看任务真实执行阶段、提示与失败原因，不替代财务风险判断</p></div></div><span className="coverage-tag">本地留存 · 真实事件</span></div><p className="analysis-logs-note">日志对应下方所选任务，与总览数据独立；选择历史日志不会自动载入该任务的财务结果。</p><RuntimeLogs currentTaskId={logTask.id} refreshKey={logTask.revision} visible={!historyOpen} autoSelectLatest={false} onHistory={()=>navigate('历史记录','history-records')}/></section>

        </div>
      </div>
      </div>
    </main>
    <EvidenceDrawer evidence={evidence} open={drawerOpen} demo={isDemo} onClose={() => setDrawerOpen(false)}/><UploadDialog open={uploadOpen} settings={settings} expectedPeriod={{type:fileType,year:Number(period.slice(0,4)),quarter:reportQuarter}} onClose={() => setUploadOpen(false)} onTaskCreated={selectLogTask} onComplete={(result, nextTaskId, lookbackYears) => { setSettings(previous => ({...previous, lookbackYears})); applyResult(result,nextTaskId,true);setUploadOpen(false);setToast(lookbackYears ? `本期 PDF 解析完成，正在后台回溯 ${lookbackYears} 年同期；历史趋势区会持续更新` : '本期 PDF 解析完成') }}/>{activity && <TaskProgress activity={activity} onStop={() => void stopActiveTask()} floating/>}{toast && <TaskMessage message={toast} onClose={() => setToast('')} duringTask={Boolean(activity)}/>}
  </div>
}
export default App
