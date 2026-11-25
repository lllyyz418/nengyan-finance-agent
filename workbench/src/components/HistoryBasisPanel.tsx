import {useMemo,useState} from 'react'
import {CheckCircle2,ChevronDown,CircleAlert,ExternalLink,FileWarning,GitCompare,Layers3} from 'lucide-react'
import {basisCounts,basisLabels,basisReasons,basisRows,focusRow} from '../data/historyBasis'
import type {BasisRow,BasisStatus} from '../data/historyBasis'
import {exactFactValue} from '../data/historyCharts'
import {financialLabel} from '../data/financialLabels'
import {reportTypeLabel} from '../data/reportPeriods'
import './historyBasis.css'

const statuses:BasisStatus[]=['matched','rounding_compatible','difference','adjusted','unavailable']
const reportName=(doc?:Record<string,any>)=>doc?`${doc.report_year}年${reportTypeLabel(doc.report_type,doc.report_quarter)}`:'来源报告待核对'
const raw=(fact?:Record<string,any>)=>fact?.raw_value&&fact?.raw_unit?`${fact.raw_value} ${fact.raw_unit}`:'原始披露精度未取得'
const money=(value:any,unit:any)=>exactFactValue({value,unit})
const differenceText=(item:Record<string,any>)=>{
  if(item.difference==null)return '—'
  const value=Number(item.difference),sign=String(item.difference).startsWith('-')||value===0?'':'+'
  if(['CNY','yuan'].includes(item.unit)&&Number.isFinite(value)&&Math.abs(value)>=1e8)return `约 ${value>0?'+':''}${(value/1e8).toLocaleString('zh-CN',{maximumFractionDigits:2})} 亿元`
  return sign+money(item.difference,item.unit)
}
const dataPeriod=(row:BasisRow)=>`${row.item.year}${row.originalDoc?.report_type==='semiannual'?'年上半年':row.originalDoc?.report_type==='quarterly'?(row.originalDoc.report_quarter===1?'年第一季度':'年前三季度'):row.original?.period_type==='instant'?'年末':'年度'}`
const relative=(raw:any)=>{
  if(raw==null||!Number.isFinite(Number(raw)))return '原值为零或不可比，不计算差异比例'
  const value=Number(raw)
  return value!==0&&Math.abs(value)<.000001?'差异幅度小于 0.000001%':`相对原值 ${value.toLocaleString('zh-CN',{maximumFractionDigits:6})}%`
}
type Props={history?:Record<string,any>;demo?:boolean;onEvidence?:(id:string)=>void;onMetric?:(code:string)=>void}

export function HistoryBasisPanel({history,demo=false,onEvidence,onMetric}:Props){
  const rows=useMemo(()=>basisRows(history),[history]),counts=basisCounts(rows)
  const [filter,setFilter]=useState('focus'),[year,setYear]=useState('all'),[expanded,setExpanded]=useState<string>()
  const years=[...new Set(rows.map(row=>String(row.item.year)))].sort().reverse()
  const shown=rows.filter(row=>(year==='all'||String(row.item.year)===year)&&
    (filter==='all'||filter==='focus'&&focusRow(row)||filter===row.status))
  const source=(row:BasisRow,side:'original'|'later')=>{
    const fact=side==='original'?row.original:row.later,doc=side==='original'?row.originalDoc:row.laterDoc
    const id=side==='original'?row.item.original_fact_id:row.item.later_comparative_fact_id
    const pages=[...new Set((fact?.sources??[]).map((s:any)=>s.page_start).filter(Boolean))]
    return <div><h4>{side==='original'?'原始披露':'后续报告中的同年比较值'}</h4><strong>{reportName(doc)}</strong>
      <p>{raw(fact)}</p><small>{doc?.file_name??'原文件名未记录'}{pages.length?` · 第 ${pages.join('、')} 页`:''}</small>
      <button className="text-button" disabled={!id||!onEvidence||demo} onClick={()=>id&&onEvidence?.(id)}><ExternalLink size={14}/>{side==='original'?'查看原始披露':'核对后续报告原文'}</button></div>
  }
  return <div className="basis-panel">
    <header className="basis-intro"><GitCompare size={23}/><div><h3>历史口径核对{demo?' · 演示':''}</h3><p>核对同一期间在前后报告中的披露值，不是比较两年的经营增长。</p></div></header>
    {demo?<div className="basis-empty"><Layers3 size={25}/><strong>演示模式不生成真实核对结论</strong><p>载入财报并补齐历史后，可查看原值、精度差异与受影响指标。</p></div>:<>
      <div className="basis-stats" aria-label="历史口径核对统计">{statuses.map(status=><button key={status} className={`basis-stat ${status}`} aria-pressed={filter===status} onClick={()=>setFilter(status)}><span>{basisLabels[status]}</span><strong>{counts[status]}<small> 项</small></strong></button>)}</div>
      <div className="basis-toolbar"><div role="group" aria-label="历史口径核对筛选"><button className={filter==='focus'?'active':''} aria-pressed={filter==='focus'} onClick={()=>setFilter('focus')}>重点事项 <span>{rows.filter(focusRow).length}</span></button><button className={filter==='all'?'active':''} aria-pressed={filter==='all'} onClick={()=>setFilter('all')}>全部明细 <span>{rows.length}</span></button></div>
        <label>数据所属年度<select aria-label="核对数据年度" value={year} onChange={e=>setYear(e.target.value)}><option value="all">全部年度</option>{years.map(y=><option key={y} value={y}>{y}年</option>)}</select></label><span>显示 {shown.length} / {rows.length} 项</span></div>
      {history?.basis_view?.read_only&&<p className="basis-view-note">按当前精度规则核对已保存数据，原始财报、历史数值和旧报告均未改写。</p>}
      {!history?.comparison?.basis_check_version&&rows.length>0&&<p className="basis-legacy">当前后端尚未提供精度核对，请更新并重启后端；未将旧版“存在差异”自动解释为重述。</p>}
      {!shown.length?<div className="basis-empty">{rows.length?<CheckCircle2 size={27}/>:<FileWarning size={27}/>}<strong>{rows.length?'当前筛选下没有待展示事项':'尚无可核对的历史比较值'}</strong><p>{rows.length?'可点击统计卡片或“全部明细”查看其余项目。':'需要原报告和后续同类型报告中的同期比较值；没有数据不等于一致。'}</p></div>:
      <div className="basis-table-wrap"><table className="basis-table"><thead><tr><th>财务项目 / 所属期间</th><th>原报告披露值</th><th>后续报告重列的同年值</th><th>差额（后者－前者）</th><th>核对结论 / 操作</th></tr></thead><tbody>{shown.map(row=>{
        const item=row.item,open=expanded===row.key,metrics=[...new Set<string>((item.affected_metrics??[]).map((m:any)=>m.metric_code))]
        return <Row key={row.key} row={row} open={open} metrics={metrics} onToggle={()=>setExpanded(open?undefined:row.key)} onMetric={onMetric} source={source}/>
      })}</tbody></table></div>}
      <footer className="basis-footer"><CircleAlert size={16}/><span>精度相容不等于已证实差异原因；待核查不等于违规。只有原文明确支持并完成对应核验后，才可确认调整。原值不自动替换，也不以金额大小代替重要性判断。</span></footer>
    </>}
  </div>
}

function Row({row,open,metrics,onToggle,onMetric,source}:{row:BasisRow;open:boolean;metrics:string[];onToggle:()=>void;onMetric?:Props['onMetric'];source:(row:BasisRow,side:'original'|'later')=>React.ReactNode}){
  const item=row.item,p=item.precision
  return <><tr className={`basis-row ${row.status}`}><th>{financialLabel(item.metric_code)}<small>{dataPeriod(row)}</small>{metrics.length>0&&<span className="basis-impact-count">关联 {metrics.length} 项指标</span>}</th>
    <td>{exactFactValue(row.original)}<small>{reportName(row.originalDoc)}</small><small>原披露：{raw(row.original)}</small></td>
    <td>{exactFactValue(row.later)}<small>{reportName(row.laterDoc)}中的 {item.year}年值</small><small>原披露：{raw(row.later)}</small></td>
    <td className="basis-delta" title={item.difference!=null?`精确差额：${money(item.difference,item.unit)}`:undefined}>{differenceText(item)}<small>{relative(item.relative_difference_percent)}</small></td>
    <td><span className={`basis-badge ${row.status}`}>{basisLabels[row.status]}</span><small>{basisReasons[item.reason_code]??'需核对来源及口径'}</small><button className="text-button basis-expand" aria-expanded={open} onClick={onToggle}>{open?'收起':'依据与影响'}<ChevronDown size={14}/></button></td></tr>
    {open&&<tr className="basis-detail-row"><td colSpan={5}><div className="basis-detail"><p className="basis-reason">{item.explanation??'旧版结果未区分披露精度。请更新后端后重新打开；当前保留原始核对结果。'}</p>
      {item.difference!=null&&<p>精确差额（后续报告比较值－原报告当期值）：{money(item.difference,item.unit)}</p>}
      <div className="basis-sources">{source(row,'original')}{source(row,'later')}</div>
      {item.adjustment_evidence&&<div className="basis-precision"><h4>已核对的调整披露</h4><p>{item.adjustment_evidence.reason} · 第 {item.adjustment_evidence.page} 页</p><blockquote>{item.adjustment_evidence.excerpt}</blockquote></div>}
      {p&&<div className="basis-precision"><h4>披露精度依据</h4><p>原报告最小披露单位：{p.original?money(p.original.step,item.unit):'尚未验证'}；后续报告：{p.later?money(p.later.step,item.unit):'尚未验证'}。</p>{p.combined_half_step&&<p>按就近舍入估计，两处半单位之和为 {money(p.combined_half_step,item.unit)}。仅严格落在相容范围内才归入精度差异，不采用统一金额阈值。</p>}<small>{p.note}</small></div>}
      <div className="basis-impacts"><h4>对分析的影响</h4><p>{row.status==='matched'?'该项两处披露一致。':row.status==='rounding_compatible'?'不再仅因这一精度差异阻断跨期计算；结果仍使用各期原始值，并保留精度提示。':row.status==='unavailable'?'该项尚不能证明可比；涉及它的跨期比较仍需核对，不把缺失视为零。':`${item.year} → ${item.later_report_year??row.laterDoc?.report_year??'后续报告'} 年涉及该差异的跨报告变动暂不发布；趋势保留各期原值并提示口径待核查，不直接解释为经营变化。`}</p>
        {!!item.withheld_changes?.length&&<ul>{item.withheld_changes.map((change:any,index:number)=><li key={index}>{change.from_year} → {change.to_year} 年：{financialLabel(change.metric_code)}的跨报告变动暂不发布</li>)}</ul>}
        {metrics.length?<div className="basis-metrics">{metrics.map(code=><button key={code} disabled={!onMetric} onClick={()=>onMetric?.(code)}>{financialLabel(code)} ↗</button>)}</div>:<small>未发现该项直接进入已保存的计算指标，仍保留原文核查记录。</small>}
        {metrics.length>0&&<small>以上入口定位当前任务的同名指标，并不表示该指标所有期间都受到影响。</small>}
        {(item.affected_rules??[]).length>0&&<small>该项同时关联 {item.affected_rules.length} 项已有风险筛查的输入；口径未核清不代表风险已证实。</small>}</div>
      {['difference','adjusted'].includes(row.status)&&<div className="basis-checklist"><h4>建议核查</h4><p>先核对两份原表是否同属合并口径、同一项目及期间，再查找“会计政策变更”“前期差错更正”“追溯调整”“重分类”等说明。未取得明确说明时，原因保留为待核查，不交给模型猜测。</p></div>}
    </div></td></tr>}
  </>
}
