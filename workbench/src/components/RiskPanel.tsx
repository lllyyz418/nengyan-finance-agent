import { AlertTriangle, Ban, CheckCircle2, ChevronDown, CircleDashed, Eye, XCircle } from 'lucide-react'
import { useState } from 'react'
import type { RiskItem, RiskStatus } from '../types/finance'
import './analysisDiagnostics.css'

const riskIcon: Record<RiskStatus, typeof AlertTriangle> = { 已触发: AlertTriangle, 未触发: CheckCircle2, 数据不足: CircleDashed, 尚未评估: Ban, 已完成复核:CheckCircle2, '发现变化，需复核':AlertTriangle, 需人工确认:Eye }

export function RiskPanel({ risks, onEvidence }: { risks: RiskItem[]; onEvidence: (id: string) => void }) {
  const [expanded, setExpanded] = useState<string | null>('cash')
  return <div className="risk-list">{risks.map((risk) => {
    const Icon = riskIcon[risk.status]; const isOpen = expanded === risk.id
    return <article className={`risk-row risk-${risk.status}${risk.policyReview?' policy-review-row':''}`} key={risk.id}>
      <button className="risk-summary" onClick={() => setExpanded(isOpen ? null : risk.id)} aria-expanded={isOpen}>
        <span className="risk-icon"><Icon size={18} /></span>
        <span className="risk-main"><strong>{risk.title}</strong><small>{risk.summary}</small>{risk.cashSetting&&<small className="risk-applied-setting">{risk.cashSetting}</small>}</span>
        <span className={`status status-${risk.status}`}>{risk.status}</span>
        <span className="risk-metric"><small>{risk.policyReview?'披露复核':'规则观察'}</small><strong>{risk.metric}</strong></span>
        <ChevronDown size={18} className={isOpen ? 'rotate' : ''} />
      </button>
      {isOpen && <div className="risk-details">
        <div><span>{risk.policyReview?'复核口径':'筛查条件'}</span><p>{risk.trigger}</p></div>
        <div>{risk.details.length > 0 && <ul>{risk.details.map((detail) => <li key={detail}>{detail.startsWith('满足：')?<CheckCircle2 size={12}/>:detail.startsWith('未满足：')?<XCircle size={12}/>:<CircleDashed size={12}/>}<span>{detail}</span></li>)}</ul>}{risk.missing && <ul className="missing-list">{risk.missing.map((item) => <li key={item}><CircleDashed size={12} /><span>{item}</span></li>)}</ul>}</div>
        {risk.evidenceLinks?.length?<div className="policy-evidence-links">{risk.evidenceLinks.map(link=><button key={link.id} className="text-button" onClick={()=>onEvidence(link.id)}><Eye size={15}/>{link.label}</button>)}</div>:risk.evidenceId && <button className="text-button" onClick={() => onEvidence(risk.evidenceId!)}><Eye size={15} />查看证据链</button>}
      </div>}
    </article>
  })}</div>
}
