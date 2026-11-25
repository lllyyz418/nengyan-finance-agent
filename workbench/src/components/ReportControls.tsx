import type {ReactNode} from 'react'
import {Building2,CalendarDays,FileSearch,FileUp,Play} from 'lucide-react'
import {reportYears} from '../data/reportPeriods'
import type {ReportType} from '../data/reportPeriods'
import './reportPeriods.css'

export function ReportControls({children,type,year,quarter,segment,segments,loading,canSearch,onType,onYear,onQuarter,onSegment,onUpload,onSearch}:{
  children:ReactNode;type:ReportType;year:number;quarter:number;segment:string;segments:string[];loading:boolean;canSearch:boolean;
  onType:(value:ReportType)=>void;onYear:(value:number)=>void;onQuarter:(value:number)=>void;onSegment:(value:string)=>void;onUpload:()=>void;onSearch:()=>void;
}) {
  return <section className={`control-bar period-controls ${type==='quarterly'?'has-quarter':''}`} aria-label="财务报告搜索条件">
    {children}
    <label><span>报告类型</span><div className="control-input"><FileSearch size={16}/><select aria-label="报告类型" value={type} disabled={loading} onChange={e=>onType(e.target.value as ReportType)}><option value="annual">年报</option><option value="semiannual">半年报</option><option value="quarterly">季报</option></select></div></label>
    <label><span>报告年度</span><div className="control-input"><CalendarDays size={16}/><select aria-label="报告年度" value={year} disabled={loading} onChange={e=>onYear(Number(e.target.value))}>{[...new Set([year,...reportYears()])].sort((a,b)=>b-a).map(value=><option key={value} value={value}>{value}年</option>)}</select></div></label>
    {type==='quarterly'&&<label><span>季度</span><div className="control-input"><select aria-label="报告季度" value={quarter} disabled={loading} onChange={e=>onQuarter(Number(e.target.value))}><option value={1}>第一季度</option><option value={3}>第三季度</option></select></div></label>}
    <label><span>业务分组</span><div className="control-input"><Building2 size={16}/><select aria-label="业务分组" value={segment} onChange={e=>onSegment(e.target.value)}>{segments.map(value=><option key={value}>{value}</option>)}</select></div></label>
    <div className="control-actions"><button className="secondary-button" disabled={loading} onClick={onUpload}><FileUp size={16}/>上传本地财报</button><button className="primary-button" disabled={loading||!canSearch} onClick={onSearch}>{loading?<span className="button-loader"/>:<Play size={15} fill="currentColor"/>}{loading?'分析中…':'搜索公开报告'}</button></div>
  </section>
}
