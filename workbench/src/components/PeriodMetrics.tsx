import type {FinancialMetric} from '../types/finance'
import type {PeriodBasis} from '../data/reportPeriods'
import {FinancialTable} from './FinancialTable'

export function PeriodMetrics({interim,label,singleAvailable,basis,onBasis,...table}:{
  interim:boolean;label:string;singleAvailable:boolean;basis:PeriodBasis;onBasis:(value:PeriodBasis)=>void;
  data:FinancialMetric[];onEvidence:(id:string)=>void;company:string;period:string;demo:boolean;
  searchFocus?:{name:string;revision:number};
}) {
  return <>{interim&&<div className="period-basis-bar"><div><strong>{label} · {basis==='single'?'单季度':'累计'}口径</strong><p>流量按所选期间计算，余额按期末日展示，回报率不自动年化。分析报告保留原累计口径。</p></div>{singleAvailable&&<div className="period-view-switch" aria-label="指标计算口径"><button aria-pressed={basis==='cumulative'} onClick={()=>onBasis('cumulative')}>累计数据</button><button aria-pressed={basis==='single'} onClick={()=>onBasis('single')}>单季度</button></div>}</div>}<FinancialTable {...table}/></>
}
