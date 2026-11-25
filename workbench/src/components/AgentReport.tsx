import { Bot, Copy, Download, FileText, LoaderCircle, Sparkles } from 'lucide-react'
import { Fragment, ReactNode, useEffect, useRef, useState } from 'react'

import { exportReportPdf } from '../services/reportExport'
import { localizeReport } from '../data/reportLanguage'

function inline(value: string): ReactNode {
  return value.split(/(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g).filter(Boolean).map((part, index) => part.startsWith('**') ? <strong key={index}>{part.slice(2, -2)}</strong> : part.startsWith('`') ? <code key={index}>{part.slice(1, -1)}</code> : part.startsWith('*') ? <em key={index}>{part.slice(1, -1)}</em> : <Fragment key={index}>{part}</Fragment>)
}
function tableRow(line: string) { return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim()) }
function isDivider(line: string) { return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line) }
function renderMarkdown(markdown: string) {
  const lines = markdown.replace(/```markdown\s*/gi, '```').split(/\r?\n/); const blocks: ReactNode[] = []; let index = 0; let key = 0
  while (index < lines.length) {
    const line = lines[index].trimEnd(); if (!line.trim()) { index++; continue }
    if (line.startsWith('```')) { const code: string[] = []; index++; while (index < lines.length && !lines[index].startsWith('```')) code.push(lines[index++]); index++; blocks.push(<pre className="report-code" key={key++}><code>{code.join('\n')}</code></pre>); continue }
    const heading = line.match(/^(#{1,4})\s+(.+)$/); if (heading) { const Tag = `h${Math.min(4, heading[1].length)}` as keyof JSX.IntrinsicElements; blocks.push(<Tag key={key++}>{inline(heading[2])}</Tag>); index++; continue }
    if (line.startsWith('> ')) { blocks.push(<blockquote key={key++}>{inline(line.slice(2))}</blockquote>); index++; continue }
    if (line.includes('|') && index + 1 < lines.length && isDivider(lines[index + 1])) { const headers = tableRow(line); const rows: string[][] = []; index += 2; while (index < lines.length && lines[index].includes('|') && lines[index].trim()) rows.push(tableRow(lines[index++])); blocks.push(<div className="report-table-wrap" key={key++}><table className="report-table"><thead><tr>{headers.map((cell, i) => <th key={i}>{inline(cell)}</th>)}</tr></thead><tbody>{rows.map((row, i) => <tr key={i}>{headers.map((_, j) => <td key={j}>{inline(row[j] ?? '—')}</td>)}</tr>)}</tbody></table></div>); continue }
    if (/^\s*[-*+]\s+/.test(line) || /^\s*\d+[.)]\s+/.test(line)) { const ordered = /^\s*\d+[.)]\s+/.test(line); const items: string[] = []; while (index < lines.length && (ordered ? /^\s*\d+[.)]\s+/.test(lines[index]) : /^\s*[-*+]\s+/.test(lines[index]))) items.push(lines[index++].replace(/^\s*(?:[-*+]|\d+[.)])\s+/, '')); const List = ordered ? 'ol' : 'ul'; blocks.push(<List className="report-list" key={key++}>{items.map((item, i) => <li key={i}>{inline(item)}</li>)}</List>); continue }
    const paragraph = [line.trim()]; index++; while (index < lines.length && lines[index].trim() && !/^#{1,4}\s/.test(lines[index]) && !lines[index].includes('|') && !lines[index].startsWith('> ') && !/^\s*[-*+]\s+/.test(lines[index])) paragraph.push(lines[index++].trim()); blocks.push(<p key={key++}>{inline(paragraph.join(' '))}</p>)
  }
  return blocks
}
export function AgentReport({ markdown, summary='', generator, demo = false }: { markdown: string; summary?:string; generator: string; demo?: boolean }) {
  const [original,setOriginal]=useState(false)
  useEffect(()=>setOriginal(false),[markdown,summary])
  const usingSummary=Boolean(summary)&&!original
  const displayMarkdown = localizeReport(usingSummary?summary:markdown)
  const generated = Boolean(markdown||summary)
  const reportRef = useRef<HTMLDivElement>(null)
  const [exporting, setExporting] = useState(false)
  const [exportError, setExportError] = useState('')
  const downloadPdf = async () => {
    if (!reportRef.current || exporting) return
    setExporting(true); setExportError('')
    try { await exportReportPdf(reportRef.current, reportRef.current.querySelector('h1,h2')?.textContent || '财务') }
    catch (error) { setExportError(error instanceof Error ? error.message : '报告 PDF 导出失败，请重试') }
    finally { setExporting(false) }
  }
  return <div className="agent-report-shell"><aside><span className="report-orb"><Bot size={23}/></span><strong>{generated ? usingSummary?'财务分析摘要':'报告已生成' : '等待分析结果'}</strong><small>{usingSummary?'旧版结构 · 引擎数据排版':generator === 'deepseek' ? 'DeepSeek 辅助解读' : generated ? '确定性模板报告' : '上传财报或搜索公开报告后生成'}</small><div className="report-separation"><Sparkles size={15}/><span><b>数据与文字分离</b><small>指标由财务引擎计算，文字仅负责解释。</small></span></div></aside><article>{generated ? <><header><div><FileText size={18}/><span><strong>财务分析报告</strong></span></div><div className="report-export-actions">{summary&&markdown&&<select className="report-format-select" aria-label="报告视图" value={original?'original':'summary'} disabled={exporting} onChange={event=>setOriginal(event.target.value==='original')}><option value="summary">旧版财务摘要</option><option value="original">{generator==='deepseek'?'DeepSeek 生成原文':'原始模板报告'}</option></select>}<button className="secondary-button" onClick={() => navigator.clipboard?.writeText(displayMarkdown)}><Copy size={16}/>复制报告</button><button className="secondary-button" disabled={exporting} onClick={() => void downloadPdf()}>{exporting ? <LoaderCircle size={16} className="spin"/> : <Download size={16}/>} {exporting ? '正在导出…' : '导出 PDF 报告'}</button></div></header>{exportError && <p className="report-export-error" role="alert">{exportError}</p>}<div ref={reportRef} className="markdown-body" title="财务数值以确定性引擎结果为准；报告术语已转换为中文">{renderMarkdown(displayMarkdown)}</div></> : <div className="report-empty"><Bot size={30}/><strong>尚未生成真实分析报告</strong><p>{demo ? '当前为主动开启的演示模式，样例指标及风险不是正式报告，也不代表真实披露。' : '上传 PDF、搜索公开报告，或从历史记录打开已完成任务；在此之前不展示示例分析结论。'}</p></div>}</article></div>
}
