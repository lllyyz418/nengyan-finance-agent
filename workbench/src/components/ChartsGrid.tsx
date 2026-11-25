import { useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import { BarChart3, CircleAlert, FileWarning, GitCompare, Layers3 } from 'lucide-react'
import type { EChartsOption } from 'echarts'
import { annualSeries } from '../data/mockData'
import { financialLabel } from '../data/financialLabels'
import { historyValues, historyYears, leverageDetails, leverageTooltip, trendCharts, trendGroups } from '../data/historyCharts'
import {basisRows,basisNotice} from '../data/historyBasis'
import {HistoryBasisPanel} from './HistoryBasisPanel'
import type { LeverageDetail, TrendChart, TrendGroup } from '../data/historyCharts'
import { EChart } from './EChart'
import type { DocumentMeta } from '../types/finance'
import './historyTrends.css'

const demoValues:Record<string,number[]>={revenue:annualSeries.revenue,gross_margin:annualSeries.grossMargin,
  adjusted_attributable_net_margin:annualSeries.adjustedMargin,cash_to_consolidated_profit:annualSeries.cashRatio,
  inventory_days:annualSeries.inventoryDays,inventory_impairment_to_gross:annualSeries.impairment}
const formatNumber=(value:number)=>value.toLocaleString('zh-CN',{maximumFractionDigits:2})
const gradient=(color:string,soft=false)=>({type:'linear' as const,x:0,y:0,x2:0,y2:1,colorStops:[{offset:0,color:color+(soft?'24':'e6')},{offset:1,color:color+(soft?'02':'70')}]})

function chartOption(chart:TrendChart,years:number[],values:(number|null)[][],details?:LeverageDetail[]):EChartsOption {
  return {
    animationDuration:450, color:chart.series.map(item=>item.color), textStyle:{fontFamily:'Microsoft YaHei, PingFang SC, sans-serif'},
    tooltip:{trigger:'axis',confine:true,backgroundColor:'#fff',borderColor:'#dce6ef',padding:12,textStyle:{color:'#2f455b',fontSize:16},axisPointer:{type:'line',lineStyle:{color:'#b8cad9',type:'dashed'}},
      valueFormatter:(value:any)=>value===null||value===undefined||value==='-'?'缺少有效数据':`${formatNumber(Number(value))} ${chart.unit}`,
      ...(details?{formatter:(params:any)=>{const point=Array.isArray(params)?params[0]:params;return details[point?.dataIndex]?leverageTooltip(details[point.dataIndex]):'缺少有效数据'}}:{})},
    legend:{type:'scroll',bottom:0,left:0,right:0,itemWidth:14,itemHeight:9,itemGap:14,textStyle:{fontSize:14,color:'#5f7187'},formatter:(name:string)=>chart.series.find(item=>financialLabel(item.code)===name)?.label??name},
    grid:{left:12,right:18,top:32,bottom:70,containLabel:true},
    toolbox:{right:4,top:0,itemSize:13,iconStyle:{borderColor:'#8395a9'},feature:{saveAsImage:{title:'保存图表',name:chart.title,pixelRatio:2,backgroundColor:'#fff'}}},
    xAxis:{type:'category',data:years.map(String),axisLine:{lineStyle:{color:'#d8e2eb'}},axisTick:{show:false},axisLabel:{color:'#617489',fontSize:14,margin:14}},
    yAxis:{type:'value',name:chart.unit,nameTextStyle:{color:'#617489',fontSize:14},axisLabel:{color:'#617489',fontSize:14,formatter:(value:number)=>Math.abs(value)>=10000?`${formatNumber(value/10000)}万`:formatNumber(value)},splitLine:{lineStyle:{color:'#edf2f7',type:'dashed'}},axisLine:{show:false}},
    series:chart.series.map((item,index)=>({name:financialLabel(item.code),type:item.type,data:values[index],
      connectNulls:false,smooth:false,symbol:'circle',symbolSize:7,showSymbol:true,
      ...(item.type==='line'?{lineStyle:{width:3,color:item.color},itemStyle:{color:item.color,borderColor:'#fff',borderWidth:2},areaStyle:chart.series.length===1?{color:gradient(item.color,true)}:undefined}:
        {barMaxWidth:38,barGap:'25%',itemStyle:{color:gradient(item.color),borderRadius:4}}),
      emphasis:{focus:'series'},
    })),
  } as EChartsOption
}

function TrendCard({chart,years,values,history}:{chart:TrendChart;years:number[];values:(number|null)[][];history?:Record<string,any>}) {
  const coverage=Math.max(0,...values.map(series=>series.filter(value=>value!==null).length)), available=coverage>=2
  const latestIndex=values[0].map((value,index)=>value===null?-1:index).filter(index=>index>=0).pop()
  const latest=latestIndex===undefined?null:values[0][latestIndex]
  const details=useMemo(()=>chart.id==='leverage'?leverageDetails(history,years,values[0]):undefined,[chart,history,years,values])
  const option=useMemo(()=>chartOption(chart,years,values,details),[chart,years,values,details])
  const basis=useMemo(()=>basisNotice(basisRows(history),chart.series.map(s=>s.code)),[history,chart])
  return <article className="trend-card" data-trend-id={chart.id} style={{'--trend-accent':chart.series[0].color} as CSSProperties}>
    <header><div><span className="trend-category">{chart.group}</span><h3>{chart.title}</h3><p>{chart.subtitle}</p></div><span className={`trend-coverage ${available?'ready':''}`}>{coverage}/{years.length} 年有效</span></header>
    <div className="trend-card-stat"><strong>{latest===null?'—':formatNumber(latest)}<small>{latest===null?'':chart.unit}</small></strong><span>{latestIndex===undefined?'暂无有效指标':`${years[latestIndex]} 年 · ${financialLabel(chart.series[0].code)}`}{details&&latestIndex!==undefined&&<><br/>{details[latestIndex].changeText}</>}</span></div>
    {available?<EChart option={option} ariaLabel={`${chart.title}年度趋势图`} height={255}/>:<div className="trend-no-data"><BarChart3 size={27}/><strong>暂不足以绘制跨年趋势</strong><p>{chart.emptyHint??'同一指标至少需要两个年度有效值。缺失、冲突或单位不匹配的数据不补零。'}</p></div>}
    {basis&&<p className="trend-basis-note"><CircleAlert size={15}/>{basis}</p>}
  </article>
}

export function ChartsGrid({demo=true,history,document:documentMeta,onEvidence,onMetric}: {demo?:boolean;history?:Record<string,any>;document?:DocumentMeta;onEvidence?:(id:string)=>void;onMetric?:(code:string)=>void}) {
  const [tab,setTab]=useState<'年度趋势'|'历史口径核对'|'数据可用性'>('年度趋势')
  const basis=useMemo(()=>basisRows(history),[history])
  const pending=basis.filter(row=>row.status==='difference'||row.status==='adjusted').length
  const [group,setGroup]=useState<TrendGroup|'全部'>('全部')
  const interim=Boolean(documentMeta&&!['annual','annual_report','自动识别'].includes(documentMeta.reportType))
  const scope=history?.comparison?.period_label??(documentMeta?.reportType==='semiannual'?'上半年':documentMeta?.reportQuarter===1?'第一季度':'前三季度')
  const model=useMemo(()=>{
    const years=demo?annualSeries.years.map(Number):historyYears(history)
    return {years,charts:trendCharts.map(chart=>({chart:interim?{...chart,subtitle:`历年${scope} · ${chart.subtitle.replace('同年度原报告当期金额','同报告期间原始金额')}${chart.id==='returns'?' · 期间回报率，未年化':''}`,emptyHint:'需要至少两个年度同期的有效输入；仅使用同类型、同季度数据，缺失不补零。'}:chart,values:chart.series.map(series=>demo?years.map((_,index)=>demoValues[series.code]?.[index]??null):historyValues(history,years,series,chart.unit))}))}
  },[history,demo,interim,scope])
  const ready=model.charts.filter(item=>item.values.some(values=>values.filter(value=>value!==null).length>=2)).length
  const enoughReports=demo||(history?.comparison?.report_years?.length??0)>=2
  return <div className="trend-shell history-trends">
    <div className="analysis-tabs" role="tablist" aria-label="历史数据视图">{(['年度趋势','历史口径核对','数据可用性'] as const).map(item=><button role="tab" aria-selected={tab===item} className={tab===item?'active':''} onClick={()=>setTab(item)} key={item}>{item==='年度趋势'?<Layers3 size={16}/>:item==='历史口径核对'?<GitCompare size={16}/>:<FileWarning size={16}/>} {item==='年度趋势'&&interim?'历年同期趋势':item}</button>)}</div>
    {tab==='年度趋势'&&<><div className="trend-overview"><div><span className="trend-overline">{demo?'演示视图':interim?`历年${scope}财务画像`:'年度财务画像'}</span><h3>{interim?`同口径比较 · 历年${scope}`:'从规模到质量，看清跨年变化'}</h3></div><div className="trend-count"><strong>{ready}<small> / {trendCharts.length}</small></strong><span>可绘制图表</span></div></div>
      <div className="trend-filters" role="group" aria-label="年度趋势指标分类">{(['全部',...trendGroups] as const).map(item=><button key={item} aria-pressed={group===item} className={group===item?'active':''} onClick={()=>setGroup(item)}>{item}<span>{item==='全部'?trendCharts.length:trendCharts.filter(chart=>chart.group===item).length}</span></button>)}</div>
      {!enoughReports&&<div className="trend-insufficient"><CircleAlert size={18}/><p>目前不足两个有效年度，暂不能形成跨年趋势。下方保留指标位置与可用性说明；可在上方补齐历史报告。</p></div>}
      {pending>0&&<div className="basis-global-note"><span>{pending} 项历史口径待核查。曲线保留原报告数值，相关变化不能直接解释为经营变化。</span><button className="text-button" onClick={()=>setTab('历史口径核对')}>查看依据与影响 ↗</button></div>}
      <div className="trend-chart-grid">{model.charts.filter(item=>group==='全部'||item.chart.group===group).map(item=><TrendCard key={item.chart.id} chart={item.chart} years={model.years} values={item.values} history={history}/>)}</div>
    </>}
    {tab==='历史口径核对'&&<HistoryBasisPanel history={history} demo={demo} onEvidence={onEvidence} onMetric={onMetric}/>}
    {tab==='数据可用性'&&<div className="availability-grid">{[{n:documentMeta?.facts??(demo?135:0),l:'本期标准化事实',s:'页码与坐标已保留',c:'teal'},{n:documentMeta?.metrics??(demo?39:0),l:'本期已计算指标',s:'Decimal 精确计算',c:'navy'},{n:documentMeta?.unavailable??(demo?6:0),l:'本期不可计算指标',s:'附具体原因',c:'gold'},{n:documentMeta?.warnings??(demo?3:0),l:'本期解析警告',s:'需要复核',c:'red'}].map(x=><article key={x.l} className={`availability-card ${x.c}`}><strong>{x.n}</strong><span>{x.l}</span><small>{x.s}</small></article>)}</div>}
  </div>
}
