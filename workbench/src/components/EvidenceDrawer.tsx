import { CheckCircle2, Copy, ExternalLink, FileText, Fingerprint, MapPin, X } from 'lucide-react'
import type { Evidence } from '../types/finance'

export function EvidenceDrawer({ evidence, open, onClose, demo = false }: { evidence: Evidence | null; open: boolean; onClose: () => void; demo?: boolean }) {
  if (!open || !evidence) return null
  return <><button className="drawer-backdrop" aria-label="关闭证据抽屉" onClick={onClose}/><aside className="evidence-drawer" role="dialog" aria-modal="true" aria-labelledby="evidence-title">
    <header><div><span className="eyebrow">Evidence chain</span><h2 id="evidence-title">原文证据与计算溯源</h2></div><button className="icon-button" onClick={onClose} aria-label="关闭"><X size={20}/></button></header>
    {demo && <p className="evidence-demo-note">演示证据：以下页码、摘录及金额均为模拟样例，没有对应真实 PDF，不能作为研究或核验依据。</p>}
    <div className="evidence-id-strip"><span><Fingerprint size={14}/>{evidence.factId}</span><span>{evidence.metricId}</span><button title="复制ID" onClick={() => navigator.clipboard?.writeText(evidence.factId)}><Copy size={13}/></button></div>
    <div className="evidence-meta"><div><FileText size={16}/><span><small>源文件</small>{evidence.file}<em>SHA-256 {evidence.fileHash} · {evidence.parserVersion}</em></span></div><div><MapPin size={16}/><span><small>原文定位</small>第 {evidence.page} 页 · {evidence.section}<em>坐标 {evidence.location}</em></span></div></div>
    <div className="pdf-preview"><div className="pdf-toolbar"><span>{evidence.sourceUrl ? 'PDF 原文预览' : '原文预览未提供'}</span><span>第 {evidence.page} 页</span></div>{evidence.sourceUrl ? <iframe title="证据对应PDF原文" src={evidence.sourceUrl} className="evidence-pdf-frame"/> : <div className="evidence-preview-empty"><FileText size={30}/><p>当前证据没有关联可打开的 PDF 文件。</p><small>演示数据无真实原文；上传财报或完成公开报告分析后可查看。</small></div>}</div>
    <section className="evidence-block"><span>原文摘录</span><blockquote>“{evidence.excerpt}”</blockquote></section>
    {evidence.contentKind==='research'?<section className="evidence-block"><span>披露性质与范围</span><p>{evidence.normalizedValue} · {evidence.periodType}</p><p>此处为公司报告的文字节选，不属于已计算财务事实；经营计划不代表已经实现，风险披露不代表损失已经发生。</p></section>:<div className="normalization-grid"><div><small>原始字段</small><strong>{evidence.originalField}</strong></div><div><small>原始披露值</small><strong>{evidence.originalValue} {evidence.originalUnit}</strong></div><div><small>{evidence.contentKind==='policy'?'证据内容':'归一化金额'}</small><strong>{evidence.normalizedValue}</strong></div><div><small>期间 / 口径</small><strong>{evidence.periodType} · {evidence.consolidated ? '合并' : '单体'}</strong></div></div>}
    <section className="consistency-row"><div><CheckCircle2 size={17}/><span>事实质量</span></div><strong className={evidence.quality === '披露冲突' ? 'warn' : 'ok'}>{evidence.quality}</strong></section>
    {evidence.sourceUrl ? <a className="primary-button full evidence-open-link" href={evidence.sourceUrl} target="_blank" rel="noopener noreferrer"><ExternalLink size={16}/>打开原文位置 · 第 {evidence.page} 页</a> : <button className="primary-button full" disabled><ExternalLink size={16}/>原文暂不可用</button>}<p className="drawer-note">{evidence.sourceUrl ? '打开本地原始 PDF 并定位页码；坐标供人工核对，暂不自动高亮。' : '该证据未关联真实 PDF，因此无法跳转原文。'}</p>
  </aside></>
}
