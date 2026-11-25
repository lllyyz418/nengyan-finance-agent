export type RiskStatus = '已触发' | '未触发' | '数据不足' | '尚未评估' | '已完成复核' | '发现变化，需复核' | '需人工确认'
export type MetricStatus = '已计算' | '不可计算' | '数据冲突'
export type AnalysisViewMode = 'empty' | 'demo' | 'result'

export interface MetricCardData { id: string; label: string; value: string; change: string; direction: 'up' | 'down' | 'flat'; favorable: boolean; context: string; formula: string; trend: number[] }
export interface RiskItem { id: string; ruleId: string; title: string; status: RiskStatus; metric: string; trigger: string; summary: string; details: string[]; missing?: string[]; evidenceId?: string; evidenceLinks?:{id:string;label:string}[]; policyReview?:boolean; cashSetting?:string }
export interface FinancialMetric { id: string; name: string; current: string | null; previous: string | null; change: string | null; unit: string; status: MetricStatus; formula: string; inputFacts: number; source: string; unavailableReason?: string; evidenceId?: string; currentPeriod?:string; comparisonPeriod?:string; comparisonNote?:string; changeNote?:string; comparisonEvidenceId?:string }
export interface Evidence { contentKind?:'policy'|'research'; id: string; factId: string; metricId: string; file: string; fileHash: string; parserVersion: string; page: number; section: string; excerpt: string; location: string; originalField: string; originalValue: string; originalUnit: string; normalizedValue: string; periodType: string; consolidated: boolean; restated: boolean; sourceUrl?: string; quality: '跨章节核对一致' | '单一来源' | '附注舍入差异' | '披露冲突' }
export interface PeerValue { code: string; name: string; value: string | null; status: string }
export interface CompanyComparison { metric: string; target: string | null; peers: string[]; peerValues?: PeerValue[]; peerMedian: string | null; delta: string | null; validPeers: number; unit: string; status: '可比较' | '仅并排展示' | '不可比较';reportYear?:number;targetCode?:string;periodBasis?:string }
export interface DocumentMeta { reportType: string; reportYear: string; reportQuarter?:number; pages: number; consolidated: boolean; assurance: string; fileHash: string; parserVersion: string; status: '等待分析' | '解析完成' | '部分完成' | '解析失败'; facts: number; metrics: number; unavailable: number; warnings: number }
export interface AnalysisSnapshot { company: string; ticker: string; period: string; segment: string; document: DocumentMeta; metrics: MetricCardData[]; risks: RiskItem[]; financialMetrics: FinancialMetric[]; comparison: CompanyComparison[] }
export type TaskStage = '文件校验' | '类型识别' | '事实提取' | '一致性核对' | '指标计算' | '规则筛查'
export interface AnalysisTask { id: string; stage: TaskStage; progress: number; status: 'processing' | 'completed' | 'failed' }
export interface CompanyOption { name: string; code: string; exchange: string; segment: string; verified: boolean; full_name?:string; pinyin?:string; aliases?:string[]; identity_status?:'catalog_verified'|'online_verified'; identity_checked_at?:string; identity_source?:string; data_status?:{state:'unknown'|'failed'|'pdf_saved'|'limited'|'analysis_saved';metrics:number;year?:number} }
export interface CompanySearchResponse { items:CompanyOption[]; total:number; has_more:boolean; segments:string[]; catalog_size:number; max_peers:number; source:string }
export type OcrMode = 'auto' | 'force' | 'off'
export interface AnalysisSettings { lookbackYears: number; nonrecurringRatioThreshold: number; minimumNegativeCashQuarters: number; announcementOcrMode: OcrMode }
export interface PeerSelection { peers: CompanyOption[]; group: string; note: string }
export interface UploadFailure { code: string; title: string; message: string; suggestion: string; retryable: boolean; stage?: string }
export interface BackendTask { id: string; source: string; file_name?: string; status: 'queued' | 'processing' | 'completed' | 'failed' | 'review_required' | 'interrupted'; stage: string; progress: number; error?: string | null; error_info?: UploadFailure | null }
export interface BackfillRow { year: number; status: string; file_name?: string; source?: string; message?: string; version_note?: string }
export interface BackfillSummary { eligible: boolean; company: string; report_year: number; stock_code?: string; lookback_years: number; task?: BackendTask; rows: BackfillRow[]; rejected_files: {file_name:string;message:string}[]; pending: boolean; result_task_id?: string; newer_result_task_id?:string; is_current_version: boolean; message: string }
export interface BackfillConfig { lookback_years: number; file_tokens?: string[]; stock_code?: string; confirm_identity?: boolean; online?: boolean }
export type LogLevel = 'info' | 'success' | 'warning' | 'error'
export interface RuntimeLog { id: string; seq: number; timestamp: string; level: LogLevel; stage: string; message: string; details: string }
export interface RuntimeLogResponse {
  task: BackendTask & { created_at: string; updated_at: string; finished_at?: string; company_name?: string };
  events: RuntimeLog[]; next_seq: number; has_more: boolean; has_logs: boolean; total: number;
}
export type HistoryStatus = BackendTask['status'] | 'partial'
export interface HistoryRecord {
  id: string; source: string; file_name: string; kind: 'financial' | 'peer' | 'announcement' | 'scan';
  company: string; stock_code: string; year: number | null; period: string; status: HistoryStatus; task_status: BackendTask['status'];
  created_at: string; updated_at: string; stage: string; progress: number; error?: string; metrics: number; risks: number; warnings: number;
  version: string; file_hash?: string; favorite: boolean; note: string; trashed: boolean; has_result: boolean; has_metrics: boolean; has_report: boolean;
  source_available: boolean; can_retry: boolean; request: Record<string, any> | null; parent_task_id?: string; review_candidates: number;
}
export interface HistoryFilters { q: string; status: string; kind: string; year: string; start_date: string; end_date: string; favorites: boolean; trashed: boolean; page: number; page_size: number; sort?: 'recent' }
export interface HistoryResponse { items: HistoryRecord[]; total: number; page: number; page_size: number; statistics: {total: number; completed: number; review_required: number; failed: number}; years: number[] }
export interface BackendResult { engine_version: string; payload: Record<string, any>; analysis: Record<string, any>; report_markdown: string; report_generator: string; evidence: Record<string, any>;report_identity?:Record<string,any>;report_summary_markdown?:string }
export interface ResearchSource {task_id:string;fact_id:string;page:number;label:string}
export interface ResearchPoint {id:string;kind?:string;label:string;period:string;period_label?:string;company:string;scope:string;display:string;sources:ResearchSource[]}
export interface ResearchClaim {headline:string;observed_facts?:string[];interpretation:string;alternatives:string[];checks:string[];limitation:string;evidence:ResearchPoint[];support_if?:string;weaken_if?:string}
export interface ResearchCoverage {role:string;task_id:string;company:string;years:number[];measures:number}
export interface ResearchVersion {id:string;base_task_id:string;status:'queued'|'processing'|'completed'|'failed'|'interrupted';stage?:string;created_at:string;updated_at:string;error?:string;scope:string;prompt_version:string;model:string;data_signature:string}
export interface ResearchReport extends ResearchVersion {body?:{title:string;sections:{key:string;title:string;claims:ResearchClaim[]}[];coverage:ResearchCoverage[];limits:string[];validation_note:string;business_context?:{status:string;excerpts:number;message:string;scope_note?:string}};markdown:string}
export interface ResearchSummary {configured:boolean;eligible:boolean;message:string;latest_job:ResearchVersion|null;versions:ResearchVersion[];report:ResearchReport|null;new_data_available:boolean;available_signature?:string;available_coverage:ResearchCoverage[]}
export interface ScanCandidate { candidate_id: string; metric_code: string; status?: string; value?: string | number; previous_value?: string | number; raw_unit?: string; calculated_yoy_percent?: string | null; checks?: Record<string, boolean | null>; sources?: Record<string, { page?: number; text?: string; bbox?: number[] } | null> }
export interface ScanWarning { code: string; page?: number; metric_code?: string; detail?: string }
export interface ScanResult { engine_version: string; source_path?: string; scan: { file_sha256?: string; candidate_snapshot_sha256?: string; candidates?: ScanCandidate[]; warnings?: ScanWarning[] } }
