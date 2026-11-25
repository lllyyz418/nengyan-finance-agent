import { AlertTriangle, CircleAlert, FileSearch, FileText, LoaderCircle, MapPin, Upload, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { financeApi } from '../services/financeApi'
import { announcementEventNames, announcementModelStatus, formatAnnouncementAmount } from '../services/announcementView'
import type { BackendResult, BackendTask, OcrMode } from '../types/finance'

const roleNames: Record<string, string> = { bid_amount: '中标金额', contract_amount: '合同金额', fund_target_size: '基金目标规模', company_commitment: '公司拟认缴金额', project_total_investment: '项目总投资' }
const fieldNames: Record<string, string> = { investment_subject: '投资／履约主体', investment_target: '投资／合同对象', funding_source: '资金来源', time_horizon: '期限安排', exit_arrangements: '退出安排', consolidation_scope: '合并范围', approval_conditions: '审批与生效条件', event_progress: '事件进度', other_conditions: '其他限制条件' }
const warningNames: Record<string, string> = { NOT_AN_ANNOUNCEMENT: '该文件未识别为公告', EVENT_TYPE_UNSUPPORTED: '暂不支持该公告事件类型', EVENT_AMOUNT_NOT_FOUND: '没有提取到明确金额', MULTIPLE_AMOUNTS_SAME_ROLE: '同一金额口径存在多个候选', SECURITY_CODE_CONFLICT: '公告内证券代码与目录信息不一致' }

async function waitForTask(task: BackendTask, onProgress: (task: BackendTask) => void) {
  let current = task
  while (!['completed', 'failed', 'interrupted'].includes(current.status)) {
    await new Promise(resolve => setTimeout(resolve, 900))
    current = await financeApi.getTaskProgress(current.id); onProgress(current)
  }
  if (current.status === 'failed' || current.status === 'interrupted') throw new Error(current.error || '公告解析失败')
  return current
}

export function AnnouncementPanel({ ocrMode, restored, onTaskCreated }: { ocrMode: OcrMode; restored?: { id: string; result: BackendResult; fileName: string }; onTaskCreated?: (id: string) => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [task, setTask] = useState<BackendTask | null>(null)
  const [result, setResult] = useState<BackendResult | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [question, setQuestion] = useState('')
  const [asOfMode, setAsOfMode] = useState<'announcement' | 'today'>('announcement')
  const [resultTaskId, setResultTaskId] = useState<string>()
  const revision = useRef(0)
  useEffect(() => () => { revision.current += 1 }, [])
  useEffect(() => { if (!restored) return; revision.current += 1; setLoading(false); setResultTaskId(restored.id); setResult(restored.result); setFile(null); setError(''); setTask({ id: restored.id, source: 'upload', status: 'completed', stage: '历史结果', progress: 100 }) }, [restored])
  const choose = (next?: File | null) => {
    if (loading) return
    if (next?.type === 'application/pdf' || next?.name.toLowerCase().endsWith('.pdf')) {
      if (next.size > 80 * 1024 * 1024) { setError('公告 PDF 超过 80 MB'); return }
      setFile(next); setError(''); setResult(null); setResultTaskId(undefined); setTask(null)
    } else if (next) setError('仅支持 PDF 公告文件')
  }
  const upload = async () => {
    if (!file) { setError('请先选择公告 PDF。'); return }
    const current = ++revision.current
    const check = () => { if (current !== revision.current) throw new Error('已切换结果') }
    setLoading(true); setError(''); setResult(null)
    try {
      const created = await financeApi.uploadReport(file, ocrMode); check(); onTaskCreated?.(created.id); setTask(created)
      const completed = await waitForTask(created, next => { check(); setTask(next) }); check(); setTask(completed)
      const value = await financeApi.getTaskResult(completed.id); check(); setResult(value); setResultTaskId(completed.id)
    } catch (cause) { if (current === revision.current) setError(cause instanceof Error ? cause.message : '公告解析失败') }
    finally { if (current === revision.current) setLoading(false) }
  }
  const askQuestion = async () => {
    if (!resultTaskId || question.trim().length < 2) { setError('请输入需要核查的具体问题。'); return }
    const current = ++revision.current
    const check = () => { if (current !== revision.current) throw new Error('已切换结果') }
    setLoading(true); setError('')
    try {
      const created = await financeApi.askAnnouncement(resultTaskId, question.trim(), asOfMode); check()
      onTaskCreated?.(created.id); setTask(created)
      const completed = await waitForTask(created, next => { check(); setTask(next) }); check(); setTask(completed)
      const value = await financeApi.getTaskResult(completed.id); check(); setResult(value); setResultTaskId(completed.id); setQuestion('')
    } catch (cause) { if (current === revision.current) setError(cause instanceof Error ? cause.message : '公告追问失败') }
    finally { if (current === revision.current) setLoading(false) }
  }
  const analysis = result?.analysis
  const event = analysis?.event
  const amounts = Array.isArray(event?.amount_candidates) ? event.amount_candidates : []
  const clauses = Array.isArray(event?.risk_clauses) ? event.risk_clauses : []
  const warnings = Array.isArray(analysis?.warnings) ? analysis.warnings : []
  const interpretation = analysis?.event_analysis
  const meta = analysis?.llm_metadata
  const blocks = Array.isArray(analysis?.evidence_blocks) ? analysis.evidence_blocks : []
  const date = String(event?.reported_identity?.announcement_date || event?.published_at || '')
  const openPdf = (page?: number) => {
    if (resultTaskId && !loading) window.open(`${financeApi.getTaskFileUrl(resultTaskId)}${page ? `#page=${page}` : ''}`, '_blank', 'noopener,noreferrer')
  }
  const sourceLinks = (ids: string[] = []) => ids.map(id => {
    const block = blocks.find((item: any) => item.evidence_id === id)
    if (!block) return null
    const pages = [...new Set<number>((block.sources ?? []).map((source: any) => source.page))].sort((a, b) => a - b)
    const openEvidence = () => { if (resultTaskId && !loading) window.open(`${financeApi.getAnnouncementEvidenceUrl(resultTaskId, id)}#page=${pages[0] ?? 1}`, '_blank', 'noopener,noreferrer') }
    return <button type="button" className="secondary-button" key={id} title={block.text} onClick={meta?.mode === 'evidence_agent' ? openEvidence : () => openPdf(pages[0])}><MapPin size={12} />{block.source_type === 'calculation' ? '计算输入' : block.source_type === 'normalized_financial_fact' ? '财报原文' : '原文'}第 {pages.join('、')} 页</button>
  })
  return <div className="announcement-workspace">
    <div className={`announcement-dropzone ${dragging ? 'dragging' : ''}`} onDragOver={e => { e.preventDefault(); setDragging(true) }} onDragLeave={() => setDragging(false)} onDrop={e => { e.preventDefault(); setDragging(false); choose(e.dataTransfer.files?.[0]) }}>
      <input ref={inputRef} type="file" accept="application/pdf,.pdf" hidden onChange={e => choose(e.target.files?.[0])} />
      <span className="announcement-upload-icon"><Upload size={22} /></span>
      <div><strong>拖入重大公告 PDF，或点击选择文件</strong><p>支持投资、扩产、工程中标与产业基金公告；最大 80 MB</p></div>
      <button className="secondary-button" disabled={loading} onClick={() => inputRef.current?.click()}><FileSearch size={15} />选择公告文件</button>
    </div>
    {file && <div className="selected-announcement"><FileText size={17} /><span><strong>{file.name}</strong><small>{(file.size / 1024 / 1024).toFixed(2)} MB · {loading ? '正在处理' : '等待解析'}</small></span><button disabled={loading} onClick={() => { setFile(null); setResult(null); setTask(null); if (inputRef.current) inputRef.current.value = '' }} aria-label="移除文件"><X size={15} /></button><button className="primary-button" disabled={loading} onClick={upload}>{loading ? <LoaderCircle className="spin" size={15} /> : <Upload size={15} />} {loading ? task?.stage || '正在处理' : '开始解析公告'}</button></div>}
    {loading && <><div className="announcement-progress"><span style={{ width: `${task?.progress ?? 5}%` }} /><small>{task?.stage || '正在上传'} · {task?.progress ?? 5}%</small></div>{task && <button className="stop-button" onClick={() => void financeApi.cancelTask(task.id).catch(cause => setError(cause instanceof Error ? cause.message : '停止请求未成功，请查看运行日志'))}><X size={15}/>停止公告分析</button>}</>}
    {error && <div className="dialog-error"><CircleAlert size={17} /><span><strong>公告处理失败</strong><small>{error}</small></span></div>}
    {!file && !result && <div className="announcement-empty"><FileText size={28} /><strong>尚未上传真实公告</strong><p>上传后展示事件、金额、完整风险条款、影响分析和原文入口。</p></div>}
    {result && !event && <div className="announcement-empty warning"><AlertTriangle size={26} /><strong>未识别到支持的重大公告事件</strong><p>{warnings.map((item: any) => warningNames[item.code] ?? item.message ?? item.code).join('；') || '请核对文件类型；扫描公告需启用 OCR。'}</p></div>}
    {event && <div className="announcement-result">
      <header><div><span className="event-type">{announcementEventNames[event.event_type] ?? '尚未配置专项规则的公告'}</span><span className="review-badge">{analysis?.review_status === 'requires_human_review' ? '需要人工复核' : '候选提取完成'}</span></div><button className="secondary-button" onClick={() => openPdf()}><FileText size={15} />打开原公告</button></header>
      <h3>{analysis?.title || file?.name || restored?.fileName || '已上传公告'}</h3>
      <div className="announcement-facts"><article><small>公告日期</small><strong>{date || '未识别'}</strong></article><article><small>证券代码</small><strong>{event.stock_code || event.reported_identity?.security_code || '待核验'}</strong></article><article><small>金额候选</small><strong>{amounts.length} 项</strong></article><article><small>披露风险段落</small><strong>{clauses.length} 段</strong></article></div>
      <div className="announcement-columns">
        <section><h4>金额候选</h4>{amounts.length ? amounts.map((item: any, index: number) => <div className="amount-candidate" key={`${item.role}-${index}`}><span><small>{roleNames[item.role] ?? '公告金额'}</small><strong title={`${item.amount} ${item.currency}`}>{formatAnnouncementAmount(item.amount, item.currency)}</strong></span><b>{item.qualifier || '以原文为准'}</b><p>{item.context}</p>{item.evidence_ids?.length ? sourceLinks(item.evidence_ids) : <button className="secondary-button" onClick={() => openPdf(item.sources?.[0]?.page)}><MapPin size={12} />原文第 {item.sources?.[0]?.page ?? '—'} 页</button>}</div>) : <p className="result-empty">未找到金额候选；不表示金额为零。</p>}</section>
        <section><h4>原文披露的风险与限制</h4>{clauses.length ? clauses.map((item: any, index: number) => <blockquote key={item.evidence_id || index}>{item.text}<small>{item.evidence_id ? sourceLinks([item.evidence_id]) : `第 ${item.source?.page ?? '—'} 页`}</small></blockquote>) : <p className="result-empty">未提取到明确风险条款，仍需阅读原文；不表示不存在风险。</p>}</section>
      </div>
      <section style={{ marginTop: 20 }}><h4>分析执行状态</h4><p>{announcementModelStatus(meta)}</p><p>分析时点：{meta?.as_of || '未记录'}{meta?.as_of_mode === 'today' ? '（截至今天）' : '（公告当日）'}；财务重要性尚未评估。</p><p>程序检查不能保证所有语义判断正确。{interpretation?.user_question ? `本轮问题：${interpretation.user_question}` : ''}</p>{(meta?.calls ?? []).map((call: any, index: number) => <p key={index}>{call.stage === 'evidence_agent' ? `取证与分析第 ${call.round} 轮` : call.stage === 'extract' ? '语义摘录' : '影响解释'}：{call.status === 'accepted' ? '通过检查' : call.status === 'tools_executed' ? '已完成工具取证' : '未通过'} · {call.model || '模型未返回'}{call.error_code ? ` · 原因 ${call.error_code}` : ''}</p>)}</section>
      {meta?.tool_trace?.length > 0 && <section style={{ marginTop: 20 }}><h4>实际取证记录</h4>{meta.tool_trace.map((action: any, index: number) => <p key={index}>{index + 1}. {action.label}：{({ found: '找到候选', loaded: '已核对载入', calculated: '已计算', no_match: '有限范围未命中', not_found: '未找到可用数据', unavailable: '暂不可用', not_comparable: '口径不可比', rejected: '请求未通过检查' } as Record<string, string>)[action.status] ?? action.status}{action.error_code ? ` · ${action.error_code}` : ''}</p>)}</section>}
      {analysis?.context_gaps?.length > 0 && <section style={{ marginTop: 20 }}><h4>补证缺口</h4>{analysis.context_gaps.map((gap: any, index: number) => <p key={index}>{gap.reason}</p>)}</section>}
      {interpretation && <>
        <section style={{ marginTop: 20 }}><h4>事件条件与原文摘录</h4>{(interpretation.facts ?? []).map((fact: any, index: number) => <blockquote key={index}><strong>{fieldNames[fact.field] ?? fact.field}：</strong>{fact.text}<small>{sourceLinks(fact.evidence_ids)}</small></blockquote>)}</section>
        {([['summary', '事件摘要'], ['impacts', '经营与财务影响'], ['risks', '风险分析']] as const).map(([key, title]) => <section key={key} style={{ marginTop: 20 }}><h4>{title}</h4>{interpretation[key]?.length ? interpretation[key].map((claim: any, index: number) => <blockquote key={index}><strong>{claim.kind === 'source_fact' ? '原文事实' : claim.kind === 'needs_verification' ? '待核查' : '分析解释'}：</strong>{claim.text}<small>{sourceLinks(claim.evidence_ids)}</small></blockquote>) : <p className="result-empty">当前没有通过校验的模型分析，请核对模型状态。</p>}</section>)}
      </>}
      <section style={{ marginTop: 20 }}><h4>复核原因与待核查事项</h4>{(analysis?.review_reasons ?? []).map((reason: any, index: number) => <p key={`reason-${index}`}>{reason.message}</p>)}{(interpretation?.follow_up ?? []).map((question: string, index: number) => <p key={`question-${index}`}>{question}</p>)}</section>
      <section style={{ marginTop: 20 }}><h4>基于原文继续追问</h4><textarea aria-label="公告追问" maxLength={1200} disabled={loading} value={question} onChange={event => setQuestion(event.target.value)} placeholder="例如：哪些条款影响实际缴款和退出安排？" style={{ width: '100%', minHeight: 80, padding: 10, border: '1px solid #dbe3e9' }} /><div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 8 }}><label>分析时点 <select disabled={loading} value={asOfMode} onChange={event => setAsOfMode(event.target.value as 'announcement' | 'today')}><option value="announcement">公告当日</option><option value="today">截至今天</option></select></label><button className="primary-button" disabled={loading || question.trim().length < 2 || !meta} onClick={askQuestion}>{loading ? '正在补证' : '提交追问'}</button></div><p>追问会新建任务并保留原结果。选择“截至今天”允许使用较晚披露的信息，结果会标明分析时点。</p></section>
    </div>}
    <div className="announcement-boundary"><CircleAlert size={15} /><span><strong>能力说明</strong><small>公告金额不计入经审计财务指标。模型可按需查阅原文、已接入的本地财报和相关公告候选；在线公告目录需后端显式开启。检索不保证完整，相关事项需核实，规模参照不等于风险评级。</small></span></div>
  </div>
}
