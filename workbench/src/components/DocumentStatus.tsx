import { AlertTriangle, CheckCircle2, ChevronDown, FileCheck2, FileClock, Fingerprint, Gauge, Layers3 } from 'lucide-react'
import type { DocumentMeta } from '../types/finance'
import './documentStatus.css'

const reportTypes: Record<string, string> = {
  annual: '年度报告', annual_report: '年度报告', half_year: '半年度报告', half_year_report: '半年度报告',
  semiannual: '半年度报告', announcement: '重大公告', quarterly: '季度报告', quarterly_report: '季度报告',
}

export function DocumentStatus({ data, company, demo, pending = false }: { data: DocumentMeta; company: string; demo: boolean; pending?: boolean }) {
  const reportType = data.reportType==='quarterly'?(data.reportQuarter===1?'第一季度报告':'第三季度报告'):reportTypes[data.reportType] || (/^[\u4e00-\u9fff\s]+$/.test(data.reportType) ? data.reportType : '未识别类型')
  const items = [
    { icon: Layers3, label: '解析覆盖', value: `${data.facts} 项事实 / ${data.metrics} 项指标`, tone: 'navy' },
    { icon: Gauge, label: '指标可用性', value: `${data.unavailable} 项不可计算`, tone: 'gold' },
    { icon: AlertTriangle, label: '数据质量', value: `${data.warnings} 项系统提示`, tone: data.warnings ? 'gold' : 'teal' },
  ]
  return <div className={`overview-document ${pending ? 'is-pending' : ''}`} aria-label="文件与解析状态">
    <header><div className="overview-document-title"><FileCheck2 size={22}/><div><h2>文件与解析状态</h2><p>{pending ? '尚未上传或载入报告，解析状态将在任务完成后展示' : <>{data.reportYear !== '—' ? `${data.reportYear} 年 · ` : ''}{reportType} · {data.pages ? `${data.pages} 页` : '页数未识别'}</>}</p></div></div>
      <div className="overview-document-badges">{demo && <span className="overview-document-demo">演示数据</span>}<span className={`overview-document-state ${pending ? 'waiting' : data.status === '解析完成' ? 'complete' : data.status === '解析失败' ? 'failed' : 'partial'}`}>{pending ? <FileClock size={16}/> : data.status === '解析完成' ? <CheckCircle2 size={16}/> : <AlertTriangle size={16}/>} {pending ? '等待分析' : data.status}</span></div>
    </header>
    <div className="overview-document-stats">{items.map(({icon: Icon,label,value,tone}) => <article key={label} className={pending ? 'waiting' : tone}><Icon size={19}/><div><span>{label}</span><strong>{pending ? '—' : value}</strong></div></article>)}</div>
    {!pending && <details className="overview-document-details"><summary><span>查看解析详情</span><ChevronDown size={17}/></summary><div className="overview-document-detail-grid">
      <div><span>当前结果中的公司</span><strong>{company}</strong></div><div><span>报告年度与类型</span><strong>{data.reportYear} · {reportType}</strong></div>
      <div><span>财务报表口径</span><strong>{data.consolidated ? '合并口径' : '单体口径'} · {data.assurance}</strong></div><div><span>解析引擎版本</span><strong>{data.parserVersion}</strong></div>
      <div className="overview-document-fingerprint"><span><Fingerprint size={14}/>文件指纹</span><code>{data.fileHash}</code></div>
    </div></details>}
  </div>
}
