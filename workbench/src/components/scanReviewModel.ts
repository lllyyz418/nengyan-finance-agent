export type Cell = { raw_value: string; raw_unit: string }
export type Source = { page: number; bbox: number[]; text: string; confidence: number }
export type Candidate = { candidate_id: string; metric_code: string; period: string; previous_period: string; raw_unit: string; status: string; sources: { label: Source; current: Source; previous: Source } }
export type Decision = { candidate_id: string; decision: 'pending'|'confirm'|'correct'|'reject'; evidence_confirmed: boolean; note: string|null; values: { current: Cell; previous: Cell } }
export type Review = { schema_version: string; file_sha256: string; candidate_snapshot_sha256: string; reviewer: { id: string|null; kind: string|null }; identity: { decision: 'pending'|'confirm'|'reject'; company_name: string; report_year: number; currency: string; statement_scope: string|null; comparative_restated: boolean|null }; decisions: Decision[]; expected_revision: number; request_id: string|null }
export type ReviewState = { revision: number; scan: { document: { file_name: string; page_count: number; file_sha256: string }; identity_claim: { company_name: string; report_year: number }; candidates: Candidate[]; pages_read: number[]; warnings: { code: string; page?: number; detail?: string }[] }; template: Review; result: Reviewed|null }
export type Reviewed = { status: string; review_revision?: number; review?: { status: string; decisions: { candidate_id: string; status: string }[] }; analysis?: { metrics?: { metric_code: string; value: string; unit: string; period: string }[]; unavailable_metrics?: { metric_code: string; reason_code: string; period: string }[] }; payload?: Reviewed; reviewer_identity_basis?: string }

export function edited(candidate: Candidate, decision: Decision): boolean {
  return (['current','previous'] as const).some(role => decision.values[role].raw_value !== candidate.sources[role].text || decision.values[role].raw_unit !== candidate.raw_unit)
}

export function reviewProblems(review: Review, candidates: Candidate[]): string[] {
  const errors: string[] = []
  if (!review.reviewer.id?.trim()) errors.push('请填写复核人姓名或编号。')
  if (review.identity.decision !== 'confirm' || review.identity.statement_scope !== 'consolidated' || review.identity.comparative_restated === null) errors.push('请先核对公司、年度、合并口径及比较数据重述情况。')
  for (const decision of review.decisions) {
    const candidate = candidates.find(c => c.candidate_id === decision.candidate_id)
    if (!candidate) { errors.push('候选编号已变化，请刷新。'); continue }
    if (decision.decision === 'pending') continue
    if (decision.decision === 'reject') { if (!decision.note?.trim()) errors.push('拒绝候选需要填写原因。'); continue }
    if (!decision.evidence_confirmed) errors.push('确认或纠正前，请勾选已核对原文的两列金额、年份和单位。')
    if (decision.decision === 'confirm' && edited(candidate,decision)) errors.push('修改过的金额或单位请选择“纠正”。')
    if (decision.decision === 'correct' && !edited(candidate,decision)) errors.push('“纠正”需要实际修改金额或单位。')
    if ((decision.decision === 'correct' || candidate.status === 'needs_review') && !decision.note?.trim()) errors.push('纠正或重点复核候选需要填写核查说明。')
    if (!decision.values.current.raw_value.trim() || !decision.values.previous.raw_value.trim()) errors.push('本期和上期金额均不能为空；缺失时请保持待复核或拒绝。')
  }
  return [...new Set(errors)]
}

export function requestKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `review-${Date.now()}-${Math.random().toString(36).slice(2)}`
}
