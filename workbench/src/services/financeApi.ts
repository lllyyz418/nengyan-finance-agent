import { evidenceById, snapshots } from '../data/mockData'
import { metricNames } from '../data/financialLabels'
import { localCompanies, peerRequestMetadata } from '../data/peerCatalog'
import { reportSecurityCode } from '../data/reportIdentity'
import { metricPresentation, riskPresentation } from '../data/analysisPresentation'
import { requestJson } from './http'
import {reportTypeLabel} from '../data/reportPeriods'
import type {ReportType,PeriodBasis} from '../data/reportPeriods'
import type {ResearchSummary,ResearchReport,ResearchVersion} from '../types/finance'
import type { CompanySearchResponse } from '../types/finance'
import type { AnalysisSettings, AnalysisSnapshot, BackendResult, BackendTask, BackfillSummary, BackfillConfig, CompanyOption, Evidence, FinancialMetric, HistoryFilters, HistoryRecord, HistoryResponse, MetricCardData, OcrMode, PeerSelection, RiskItem, LogLevel, RuntimeLogResponse } from '../types/finance'

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api'
const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

async function request<T>(path: string, init?: RequestInit, options?:{timeoutMs?:number;timeoutMessage?:string}): Promise<T> {
  return requestJson<T>(`${API_BASE}${path}`,init,options)
}

export { metricNames } from '../data/financialLabels'
const formulaNames: Record<string, string> = {
  revenue_qoq:'（本季营业收入－上季营业收入）÷上季营业收入 × 100%',
  attributable_net_profit_qoq:'（本季归母净利润－上季归母净利润）÷上季归母净利润 × 100%',
  attributable_net_profit_ex_nonrecurring_qoq:'（本季扣非归母净利润－上季扣非归母净利润）÷上季扣非归母净利润 × 100%',
  operating_cash_flow_qoq:'（本季经营现金流－上季经营现金流）÷上季经营现金流 × 100%',
  liabilities_to_assets: '合并期末负债合计÷同期期末资产总计 × 100%',
  revenue_yoy: '（本期营业收入－上期营业收入）÷上期营业收入 × 100%',
  revenue_q4_qoq: '（第四季度营业收入－第三季度营业收入）÷第三季度营业收入 × 100%',
  operating_cost_yoy: '（本期营业成本－上期营业成本）÷上期营业成本 × 100%',
  r_and_d_expense_yoy: '（本期研发费用－上期研发费用）÷上期研发费用 × 100%',
  net_profit_yoy: '（本期净利润－上期净利润）÷上期净利润 × 100%',
  attributable_net_profit_yoy: '（本期归母净利润－上期归母净利润）÷上期归母净利润 × 100%',
  attributable_net_profit_q4_qoq: '（第四季度归母净利润－第三季度归母净利润）÷第三季度归母净利润 × 100%',
  attributable_net_profit_ex_nonrecurring_yoy: '（本期扣非归母净利润－上期扣非归母净利润）÷上期扣非归母净利润 × 100%',
  attributable_net_profit_ex_nonrecurring_q4_qoq: '（第四季度扣非归母净利润－第三季度扣非归母净利润）÷第三季度扣非归母净利润 × 100%',
  operating_cash_flow_yoy: '（本期经营现金流－上期经营现金流）÷上期经营现金流 × 100%',
  operating_cash_flow_q4_qoq: '（第四季度经营现金流－第三季度经营现金流）÷第三季度经营现金流 × 100%',
  total_profit_yoy: '（本期利润总额－上期利润总额）÷上期利润总额 × 100%', total_profit_q4_qoq: '（第四季度利润总额－第三季度利润总额）÷第三季度利润总额 × 100%',
  gross_margin: '（营业收入－营业成本）÷营业收入 × 100%', consolidated_net_margin: '净利润÷营业收入 × 100%',
  adjusted_attributable_net_margin: '扣非归母净利润÷营业收入 × 100%', cash_to_consolidated_profit: '经营活动现金流净额÷净利润',
  simplified_free_cash_flow: '经营活动现金流净额－购建长期资产支付的现金', fixed_assets_cip_to_assets: '（固定资产＋在建工程）÷总资产 × 100%',
  inventory_days: '年度天数 × 平均存货÷营业成本', receivables_days_approx: '年度天数 × 平均应收账款÷营业收入',
  return_on_assets: '净利润÷平均总资产 × 100%', return_on_attributable_equity: '归母净利润÷平均归母净资产 × 100%',
  r_and_d_expense_to_revenue: '研发费用÷营业收入 × 100%', inventory_impairment_to_gross: '存货跌价准备÷存货原值 × 100%',
  receivables_over_1y_to_gross: '一年以上应收账款÷应收账款原值 × 100%', nonrecurring_to_attributable_profit: '非经常性损益÷归母净利润 × 100%',
}

export function displayValue(value: unknown, unit = '') {
  if (value === null || value === undefined || value === '') return '不可计算'
  const text = String(value)
  if (unit === 'percent') return `${Number(text).toFixed(2)}%`
  if (unit === 'percentage_points') return `${Number(text)>0?'+':''}${Number(text).toFixed(2)} 个百分点`
  if (unit === 'ratio' || unit === 'times') return `${Number(text).toFixed(2)} 倍`
  if (unit === 'days') return `${Number(text).toFixed(1)} 天`
  if (unit === 'CNY' || unit === 'yuan') {
    const amount = Number(text)
    if (!Number.isFinite(amount)) return `${text} 元`
    if (Math.abs(amount) >= 100000000) return `${(amount / 100000000).toFixed(2)} 亿元`
    if (Math.abs(amount) >= 10000) return `${(amount / 10000).toFixed(2)} 万元`
    return `${amount.toFixed(2)} 元`
  }
  return `${text}${unit ? ` ${unit}` : ''}`
}

export function resultToSnapshot(result: BackendResult): AnalysisSnapshot {
  const analysis = result.analysis ?? {}
  const documents = Array.isArray(analysis.documents) ? analysis.documents : []
  const doc = documents[0] ?? {}
  const calculated = Array.isArray(analysis.metrics) ? analysis.metrics : []
  const unavailable = Array.isArray(analysis.unavailable_metrics) ? analysis.unavailable_metrics : []
  const allMetrics = [...calculated, ...unavailable]
  const formulaFor=(item:any)=>(analysis.period_basis&&['inventory_days','receivables_days_approx'].includes(item.metric_code)?'本期间实际天数 × 期初期末平均余额 ÷ 本期间营业成本或收入':analysis.period_basis&&['return_on_assets','return_on_attributable_equity'].includes(item.metric_code)?'本期间利润 ÷ 期初期末平均资产或权益 × 100%（未年化）':formulaNames[item.metric_code]??'按本期间同口径输入计算')+(item.derived_from_cumulative?' · 含累计相减推导值':'')
  const desired = ['revenue_yoy', 'gross_margin', 'adjusted_attributable_net_margin', 'cash_to_consolidated_profit', 'simplified_free_cash_flow', 'fixed_assets_cip_to_assets']
  const latest = (code: string) => [...calculated].reverse().find((item) => item.metric_code === code)
  const cards: MetricCardData[] = desired.map((code) => {
    const item = latest(code)
    return { id: code, label: metricNames[code] ?? code, value: item ? displayValue(item.value, item.unit) : '不可计算', change: item ? '已按披露口径计算' : '数据不足', direction: 'flat', favorable: true, context: item?.period ? `${item.period} · 引擎计算` : '当前报告未提供完整输入', formula: formulaNames[code] ?? '详见指标口径', trend: [] }
  })
  const financialMetrics: FinancialMetric[] = allMetrics.map((item, index) => ({
    id: String(item.metric_id ?? `${item.metric_code}-${index}`), name: metricNames[item.metric_code] ?? item.metric_code ?? '未命名指标',
    current: item.value === null || item.value === undefined ? null : displayValue(item.value, item.unit), previous: null, change: null, unit: '',
    status: item.status === 'calculated' ? '已计算' : item.reason_code === 'CONFLICTING_INPUT' ? '数据冲突' : '不可计算', formula: formulaFor(item),
    inputFacts: Array.isArray(item.input_fact_ids) ? item.input_fact_ids.length : 0, source: item.period ?? '指标引擎',
    unavailableReason: item.detail ?? item.reason_code, evidenceId: item.input_fact_ids?.[0],
    ...metricPresentation(item,result,displayValue),
  }))
  const risks: RiskItem[] = riskPresentation(result,displayValue)
  const company = doc.company_name ?? doc.company_short_name ?? '已上传公司'
  const code = reportSecurityCode(result)
  const companyByCode:Record<string,string>=Object.fromEntries(localCompanies.map(item=>[item.code,item.name]))
  Object.entries(result.payload?.peers?.reports??{}).forEach(([peerCode,report])=>{const name=(report as any)?.documents?.[0]?.company_name;if(name&&!companyByCode[peerCode])companyByCode[peerCode]=name})
  const peerComparison = result.payload?.peers?.comparison
  const comparison = (peerComparison?.metric_comparisons ?? []).map((item: any) => {
    const peerValues = (item.peers ?? []).map((peer: any) => ({ code: peer.stock_code, name: companyByCode[peer.stock_code] ?? peer.stock_code, value: peer.status === 'available' ? displayValue(peer.value, peer.unit ?? item.unit) : null, status: peer.status }))
    const validPeers = Number(item.valid_peer_count ?? 0)
    const deltaValue = item.target_minus_peer_median
    const delta = deltaValue === null || deltaValue === undefined ? null : item.difference_unit === 'percentage_points' ? `${Number(deltaValue) >= 0 ? '+' : ''}${Number(deltaValue).toFixed(2)} 个百分点` : displayValue(deltaValue, item.difference_unit ?? item.unit)
return { reportYear:doc.report_year, targetCode:code,periodBasis:peerComparison?.period_basis??'cumulative', metric: metricNames[item.metric_code] ?? item.metric_code, target: item.target?.status === 'available' ? displayValue(item.target.value, item.target.unit ?? item.unit) : null, peers: peerValues.map((peer: any) => `${peer.name} ${peer.value ?? '不可用'}`), peerValues, peerMedian: item.peer_median?.status === 'calculated' ? displayValue(item.peer_median.value, item.peer_median.unit ?? item.unit) : null, delta, validPeers, unit: item.unit ?? '', status: validPeers >= 2 ? '可比较' as const : validPeers === 1 ? '仅并排展示' as const : '不可比较' as const }
  })
  return {
    company, ticker: code, period: doc.report_year ? `${doc.report_year}年${reportTypeLabel(doc.report_type,doc.report_quarter)}` : '已上传报告', segment: '新能源行业',
    document: { reportType: doc.report_type ?? '自动识别',reportQuarter:doc.report_quarter, reportYear: String(doc.report_year ?? '—'), pages: Number(doc.page_count ?? doc.pdf_pages ?? 0), consolidated: true, assurance: '以原文披露为准', fileHash: String(doc.file_sha256 ?? '').slice(0, 16) || '—', parserVersion: doc.parser_version ?? 'financial-engine v0.17', status: documents.length ? ((analysis.warnings ?? []).length || unavailable.length ? '部分完成' : '解析完成') : '解析失败', facts: (analysis.facts ?? []).length, metrics: calculated.length, unavailable: unavailable.length, warnings: (analysis.warnings ?? []).length },
    metrics: cards, risks, financialMetrics, comparison,
  }
}

export const financeApi = {
  createPeerTask(baseTaskId:string,selection:PeerSelection,targetCode?:string,basis:PeriodBasis='cumulative') {
    return request<BackendTask>(`/tasks/${baseTaskId}/peers`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({period_basis:basis,peer_codes:selection.peers.map(peer=>peer.code),...peerRequestMetadata(selection.peers),...(targetCode?{target_code:targetCode,confirm_identity:true}:{})})})
  },
  getSavedPeerResult(baseTaskId:string) {return request<{task_id:string|null;result:BackendResult|null}>(`/tasks/${baseTaskId}/peers`)},
  searchPeerCompanies(query:string,year:number,segment:string,exclude:string[],offset=0,signal?:AbortSignal) {
    const params=new URLSearchParams({q:query,report_year:String(year),segment,exclude:exclude.join(','),offset:String(offset),limit:'20'})
    return request<CompanySearchResponse>(`/companies?${params}`,{signal})
  },
  getCompanyInfo(codes:string[],year:number,signal?:AbortSignal) {
    return Promise.all(codes.map(code=>request<CompanySearchResponse>(`/companies?q=${code}&report_year=${year}&limit=1`,{signal}))).then(values=>values.flatMap(value=>value.items).filter(item=>codes.includes(item.code)))
  },
  lookupCompany(code:string,year:number,signal?:AbortSignal) {return request<{item:CompanyOption}>(`/companies/lookup?code=${encodeURIComponent(code)}&report_year=${year}`,{signal})},
  getBackfill(id:string) { return request<BackfillSummary>(`/tasks/${id}/backfill`) },
  createBackfill(id:string, config:BackfillConfig) { return request<BackendTask>(`/tasks/${id}/backfill`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(config)}) },
  stageHistoryFile(id:string,file:File) { return request<{token:string;file_name:string}>(`/tasks/${id}/backfill/files`,{method:'POST',headers:{'Content-Type':'application/pdf','X-Filename':encodeURIComponent(file.name)},body:file}) },
  getBackfillFileUrl(id:string,year:number) { return `${API_BASE}/tasks/${id}/backfill/files/${year}` },
  async getTaskLogs(id: string, afterSeq = 0) { return request<RuntimeLogResponse>(`/tasks/${id}/logs?after_seq=${afterSeq}&limit=200`) },
  getTaskLogDownloadUrl(id: string, level: LogLevel | '', q: string) { return `${API_BASE}/tasks/${id}/logs/download?${new URLSearchParams({level,q})}` },
  async listHistory(filters: HistoryFilters,signal?:AbortSignal,includeSource=true): Promise<HistoryResponse> {
    const query = new URLSearchParams()
    Object.entries(filters).forEach(([key,value]) => { if (value !== '') query.set(key,String(value)) })
    if(!includeSource)query.set('include_source','false')
    return request<HistoryResponse>(`/history?${query}`,{signal},{timeoutMs:12000,timeoutMessage:'历史摘要读取超时，自动刷新已暂停。原记录仍保留，请稍后点击“重试读取”；无需删除历史文件。'})
  },
  async updateHistory(id: string, changes: Partial<Pick<HistoryRecord, 'favorite' | 'note' | 'trashed'>>) { return request<HistoryRecord>(`/history/${id}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(changes)}) },
  async retryHistory(id: string) { return request<BackendTask>(`/history/${id}/retry`, {method:'POST'}) },
  getHistoryReportUrl(id: string) { return `${API_BASE}/history/${id}/report` },
  async health() { return request<{ status: string; engine_version: string; llm_configured: boolean;api_revision?:string;report_types?:string[];announcement_revision?:string }>('/health') },
  async searchCompanies(query:string,year:number,offset=0,online=false,signal?:AbortSignal) {
    const params=new URLSearchParams({q:query,report_year:String(year),offset:String(offset),limit:'20'})
    const controller=new AbortController(),cancel=()=>controller.abort(signal?.reason)
    if(signal?.aborted)cancel()
    signal?.addEventListener('abort',cancel,{once:true})
    const timer=window.setTimeout(()=>controller.abort(new Error('公司查询超时，请重试；若刚更新程序，请先重启后端')),20000)
    try {return await request<CompanySearchResponse>(`/companies${online?'/online':''}?${params}`,{signal:controller.signal},{timeoutMs:0})}
    finally {window.clearTimeout(timer);signal?.removeEventListener('abort',cancel)}
  },
  async getAnalysis(company: string, period: string): Promise<AnalysisSnapshot> { await wait(100); const source = snapshots[company] ?? snapshots['宁德时代']; return { ...source, period } },
  async getEvidence(id: string, taskId?: string): Promise<Evidence> {
    if (taskId) {
      const item = await request<any>(`/tasks/${taskId}/evidence/${encodeURIComponent(id)}`)
      item.sourceUrl = item.source_available ? `${API_BASE}/tasks/${taskId}/file?fact_id=${encodeURIComponent(id)}#page=${Math.max(1, Number(item.page ?? 1))}` : undefined
      return { contentKind:item.evidence_kind==='policy'?'policy':item.evidence_kind==='research'?'research':undefined, sourceUrl: item.sourceUrl, id: item.id, factId: item.fact_id, metricId: item.metric_code ?? '', file: item.file, fileHash: item.file_hash, parserVersion: item.parser_version, page: Number(item.page ?? 0), section: item.section, excerpt: item.excerpt, location: Array.isArray(item.location) ? JSON.stringify(item.location) : String(item.location ?? '—'), originalField: item.original_field, originalValue: String(item.original_value ?? '—'), originalUnit: item.original_unit ?? '—', normalizedValue: String(item.normalized_value ?? '—'), periodType: item.period_type ?? '—', consolidated: item.statement_scope !== 'parent', restated: Boolean(item.comparative_restated), quality: item.quality === 'conflict' ? '披露冲突' : item.quality === 'cross_checked' ? '跨章节核对一致' : '单一来源' }
    }
    await wait(80); return evidenceById[id] ?? evidenceById['ev-revenue']
  },
  async uploadReport(file: File, ocrMode: OcrMode = 'off', settings?:AnalysisSettings): Promise<BackendTask> {
    const query = new URLSearchParams({ocr_mode:ocrMode})
    if(settings){query.set('lookback_years',String(settings.lookbackYears));query.set('nonrecurring_ratio_threshold',String(settings.nonrecurringRatioThreshold/100));query.set('minimum_negative_cash_quarters',String(settings.minimumNegativeCashQuarters))}
    return request<BackendTask>(`/tasks/upload?${query}`, { method: 'POST', headers: { 'Content-Type': 'application/pdf', 'X-Filename': encodeURIComponent(file.name) }, body: file },{timeoutMs:60000,timeoutMessage:'上传请求等待超时，尚未确认任务是否创建。请先查看历史记录，避免重复上传。'})
  },
  async createOnlineTask(query: string, reportYear: number, settings: AnalysisSettings, peerSelection?: PeerSelection, reportType:ReportType='annual',reportQuarter?:number): Promise<BackendTask> { return request<BackendTask>('/tasks/online', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query, report_year: reportYear,report_type:reportType,report_quarter:reportType==='quarterly'?reportQuarter:null, lookback_years: settings.lookbackYears, peer_codes: peerSelection?.peers.map((peer) => peer.code) ?? [], peer_group: peerSelection?.peers.length ? peerSelection.group : null, selection_note: peerSelection?.peers.length ? peerSelection.note : null, nonrecurring_ratio_threshold: String(settings.nonrecurringRatioThreshold / 100), minimum_negative_cash_quarters: settings.minimumNegativeCashQuarters }) }) },
  async createScanTask(file: File, pages: string, company: string, year: number): Promise<BackendTask> { return request<BackendTask>(`/scan/tasks?page_numbers=${encodeURIComponent(pages)}&company_name=${encodeURIComponent(company)}&report_year=${year}`, { method: 'POST', headers: { 'Content-Type': 'application/pdf', 'X-Filename': encodeURIComponent(file.name) }, body: file }) },
  async getScanReviewTemplate(taskId: string) { return request<Record<string, any>>(`/scan/${taskId}/review-template`, { method: 'POST' }) },
  async submitScanReview(taskId: string, review: Record<string, any>) { return request<BackendTask>(`/scan/${taskId}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(review) }) },
  async getTaskProgress(taskId: string) { return request<BackendTask>(`/tasks/${taskId}`,undefined,{timeoutMs:15000,timeoutMessage:'任务进度查询超时，后台分析不一定已停止。请勿重复提交，可稍后从历史记录查看结果。'}) },
  async cancelTask(taskId: string) { return request<BackendTask>(`/tasks/${taskId}/cancel`, { method: 'POST' }) },
  async getTaskResult<T = BackendResult>(taskId: string) { return request<T>(`/tasks/${taskId}/result`) },
  async getResearch(taskId:string,signal?:AbortSignal) {return request<ResearchSummary>(`/tasks/${taskId}/research`,{signal},{timeoutMs:12000,timeoutMessage:'研究版本读取超时，财务结果仍保留，请稍后刷新。'})},
  async createResearch(taskId:string) {return request<ResearchVersion>(`/tasks/${taskId}/research`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope:'available'})})},
  async getResearchVersion(taskId:string,version:string) {return request<ResearchReport>(`/tasks/${taskId}/research/${version}`)},
  async cancelResearch(taskId:string,version:string) {return request<ResearchVersion>(`/tasks/${taskId}/research/${version}/cancel`,{method:'POST'})},
  async askAnnouncement(taskId: string, question: string, asOfMode: 'announcement' | 'today' = 'announcement') {
    return request<BackendTask>(`/tasks/${taskId}/announcement-question`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question, as_of_mode: asOfMode }) })
  },
  getAnnouncementEvidenceUrl(taskId: string, evidenceId: string) { return `${API_BASE}/tasks/${taskId}/announcement-evidence/${encodeURIComponent(evidenceId)}` },
  getTaskFileUrl(taskId: string) { return `${API_BASE}/tasks/${taskId}/file` },
}
