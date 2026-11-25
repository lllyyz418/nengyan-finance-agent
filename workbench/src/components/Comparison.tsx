import { Check, ChevronDown, CircleAlert, FileSearch, Globe, LoaderCircle, Plus, Search, Users, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { financeApi } from '../services/financeApi'
import { canAddPeer, companySegments, dataLabel, identityLabel, localCandidates, localCompanies, MAX_PEERS, suggestedPeersForTarget } from '../data/peerCatalog'
import type { CompanyComparison, CompanyOption, PeerSelection } from '../types/finance'
import './peerSelector.css'
import { PeerComparisonTable } from './PeerComparisonTable'
import { TaskProgress } from './TaskProgress'
import type { TaskActivity } from './TaskProgress'

function MatchedName({name,query}:{name:string;query:string}) {
  const index=name.toLowerCase().indexOf(query.trim().toLowerCase())
  if(!query.trim()||index<0)return <>{name}</>
  return <>{name.slice(0,index)}<mark>{name.slice(index,index+query.trim().length)}</mark>{name.slice(index+query.trim().length)}</>
}
function CompanyStates({item,year}:{item:CompanyOption;year:number}) {
  return <><span className={`peer-identity ${item.identity_status?'verified':''}`} title={`${item.identity_source??'尚无身份来源'}${item.identity_checked_at?` · ${item.identity_checked_at.slice(0,10)}`:''}`}>{item.identity_status&&<Check size={12}/>} {identityLabel(item)}</span><span className={`peer-data peer-data-${item.data_status?.state??'unknown'}`}>{dataLabel(item,year)}</span></>
}

export function Comparison({rows:savedRows,company,targetCode,year,selection,loading,activity,reportLoaded,annual,periodLabel='',interim=false,basis='cumulative',onUpload,onViewReport,onStop,onChange,onAnalyze}: { rows:CompanyComparison[];company:string;targetCode:string;year:number;selection:PeerSelection;loading:boolean;activity?:TaskActivity;reportLoaded:boolean;annual:boolean;periodLabel?:string;interim?:boolean;basis?:string;onUpload:()=>void;onViewReport:()=>void;onStop:()=>void;onChange:(value:PeerSelection)=>void;onAnalyze:(confirmedCode?:string)=>void }) {
  const [query,setQuery]=useState(''),[segment,setSegment]=useState(''),[open,setOpen]=useState(false)
  const [options,setOptions]=useState<CompanyOption[]>([]),[active,setActive]=useState(0),[searching,setSearching]=useState(false)
  const [total,setTotal]=useState(0),[catalogSize,setCatalogSize]=useState(localCompanies.length),[hasMore,setHasMore]=useState(false)
  const [segments,setSegments]=useState<string[]>(companySegments)
  const [notice,setNotice]=useState(''),[lookupError,setLookupError]=useState(''),[lookupItem,setLookupItem]=useState<CompanyOption>()
  const [lookingUp,setLookingUp]=useState(false),[info,setInfo]=useState<Record<string,CompanyOption>>({})
  const [popup,setPopup]=useState({height:380,above:false})
  const box=useRef<HTMLDivElement>(null),input=useRef<HTMLInputElement>(null)
  const revision=useRef(0),searchController=useRef<AbortController>(),lookupController=useRef<AbortController>()
  const [manualCode,setManualCode]=useState(''),[identityConfirmed,setIdentityConfirmed]=useState(false)
  useEffect(()=>{setManualCode('');setIdentityConfirmed(false)},[targetCode,company,year])
  const hasIdentity=/^\d{6}$/.test(targetCode)
  const identityConflict=targetCode==='代码存在冲突'
  const effectiveTarget=hasIdentity?targetCode:identityConfirmed&&!identityConflict?manualCode:''
  const peersKey=selection.peers.map(peer=>peer.code).join(',')
  const excluded=[effectiveTarget,...selection.peers.map(peer=>peer.code)]
  const atLimit=selection.peers.length>=MAX_PEERS
  const canRun=reportLoaded&&annual&&/^\d{6}$/.test(effectiveTarget)&&selection.peers.length>=2&&selection.peers.length<=MAX_PEERS&&!loading
  const update=(changes:Partial<PeerSelection>)=>onChange({...selection,...changes})
  useEffect(()=>{if(!hasIdentity&&identityConfirmed&&/^\d{6}$/.test(manualCode)&&!selection.peers.length)onChange(suggestedPeersForTarget(manualCode))},[manualCode,identityConfirmed])

  useEffect(()=>{
    if(!open)return
    const measure=()=>{const rect=box.current?.getBoundingClientRect();if(!rect)return;const below=window.innerHeight-rect.bottom-20,above=rect.top-80;const up=below<220&&above>below;setPopup({height:Math.min(560,Math.max(180,up?above:below)),above:up})}
    measure();window.addEventListener('resize',measure);window.addEventListener('scroll',measure,{passive:true,capture:true})
    return()=>{window.removeEventListener('resize',measure);window.removeEventListener('scroll',measure,true)}
  },[open])
  useEffect(()=>{if(open&&options[active])document.getElementById(`peer-option-${options[active].code}`)?.scrollIntoView({block:'nearest'})},[active,options,open])

  useEffect(()=>{
    if(selection.peers.some(peer=>peer.code===effectiveTarget))update({peers:selection.peers.filter(peer=>peer.code!==effectiveTarget)})
  },[effectiveTarget])
  useEffect(()=>{
    const controller=new AbortController()
    financeApi.getCompanyInfo(selection.peers.map(peer=>peer.code),year,controller.signal).then(items=>{if(!controller.signal.aborted)setInfo(Object.fromEntries(items.map(item=>[item.code,item])))}).catch(()=>{})
    return()=>controller.abort()
  },[peersKey,year,loading])
  useEffect(()=>{
    setLookupItem(undefined);setLookupError('');setLookingUp(false);lookupController.current?.abort()
    return()=>lookupController.current?.abort()
  },[query,year,targetCode])
  useEffect(()=>{
    const current=++revision.current,controller=new AbortController();searchController.current=controller
    setActive(0);setOptions([]);setHasMore(false)
    if(!open)return()=>controller.abort()
    setSearching(true)
    const timer=window.setTimeout(async()=>{
      try{
        const data=await financeApi.searchPeerCompanies(query,year,segment,excluded,0,controller.signal)
        if(controller.signal.aborted||current!==revision.current)return
        if(!data.catalog_size)throw new Error('公司库新接口尚未加载')
        setOptions(data.items);setTotal(data.total);setHasMore(data.has_more);setCatalogSize(data.catalog_size);setSegments(data.segments);setNotice('')
      }catch{
        if(controller.signal.aborted||current!==revision.current)return
        const items=localCandidates(query,segment,excluded)
        setOptions(items);setTotal(items.length);setHasMore(false)
        setNotice('当前使用本地 55 家公司联想；请重启后端以显示年度数据状态并启用名单外查询。')
      }finally{if(!controller.signal.aborted&&current===revision.current)setSearching(false)}
    },180)
    return()=>{window.clearTimeout(timer);controller.abort()}
  },[query,segment,open,peersKey,targetCode,year])

  const more=async()=>{
    const current=revision.current;setSearching(true)
    try{
      const data=await financeApi.searchPeerCompanies(query,year,segment,excluded,options.length,searchController.current?.signal)
      if(current===revision.current){setOptions(old=>[...old,...data.items.filter(item=>!old.some(peer=>peer.code===item.code))]);setHasMore(data.has_more)}
    }catch{if(current===revision.current)setNotice('加载更多失败，请重新打开下拉框重试。')}
    finally{if(current===revision.current)setSearching(false)}
  }
  const add=(item:CompanyOption)=>{
    if(loading||!canAddPeer(item.code,effectiveTarget,selection.peers))return
    update({peers:[...selection.peers,item]});setQuery('');setLookupItem(undefined);setActive(0);input.current?.focus()
  }
  const lookup=async()=>{
    if(!/^\d{6}$/.test(query)||excluded.includes(query)||lookingUp||loading)return
    const controller=new AbortController();lookupController.current=controller;setLookingUp(true);setLookupError('')
    try{
      const value=await financeApi.lookupCompany(query,year,controller.signal)
      if(!controller.signal.aborted){setLookupItem(value.item);setOpen(true)}
    }catch(cause){if(!controller.signal.aborted)setLookupError(cause instanceof Error?cause.message:'联网核验失败，请稍后重试')}
    finally{if(!controller.signal.aborted)setLookingUp(false)}
  }
  const actualCodes=[...new Set(savedRows.flatMap(row=>row.peerValues?.map(peer=>peer.code)??[]))].sort().join(',')
  const desiredCodes=selection.peers.map(peer=>peer.code).sort().join(',')
  const stale=Boolean(actualCodes&&actualCodes!==desiredCodes)||savedRows.some(row=>(row.reportYear&&row.reportYear!==year)||(row.targetCode&&row.targetCode!==targetCode)||(row.periodBasis??'cumulative')!==basis)
  const rows=stale?[]:savedRows
  const comparable=rows.filter(row=>row.status==='可比较').length,available=rows.filter(row=>row.target!==null).length
  const summary=useMemo(()=>[{label:'已选同行',value:`${selection.peers.length} / ${MAX_PEERS} 家`,note:'默认最多 3 家 · 可选 2—5 家'},{label:'可比较指标',value:rows.length?`${comparable} 项`:'—',note:rows.length?`共 ${rows.length} 项`:'等待计算'},{label:'目标公司有效指标',value:rows.length?`${available} 项`:'—',note:`${periodLabel||`${year} 年度`} · ${rows.length?'已载入结果':'待分析'}`}],[selection.peers.length,comparable,rows.length,available,year,periodLabel])
  const showLookup=/^\d{6}$/.test(query)&&!excluded.includes(query)&&!options.some(item=>item.code===query)

  if(!reportLoaded)return <div className="comparison-awaiting"><FileSearch size={30}/><strong>请先载入一份定期报告</strong><button className="primary-button" disabled={loading} onClick={onUpload}>上传财务报告</button></div>
  return <div className="comparison-shell">
    <div className="peer-builder peer-directory-builder">
      <div className="peer-builder-head"><div><Users size={20}/><span><strong>选择可比较的同行公司</strong></span></div><span className="assumption-badge">人工指定同行 · {year}</span></div>
      <div className="peer-target-strip"><span>目标公司</span><strong>{company}</strong><span>{hasIdentity||identityConflict?targetCode:'证券代码待确认'}</span><i/><span>{periodLabel||`${year} 年度报告`} · {basis==='single'?'单季':'累计'}</span><span className="peer-target-source">当前已载入报告</span><button className="text-button" onClick={onViewReport}>查看报告</button></div>
      {interim&&<p className="peer-directory-notice">同行按当前报告期间及指标区所选口径检索。公司目录中的年报数据状态不代表同期数据已齐全；计算时逐份核验。</p>}
      {!annual&&<p className="peer-directory-notice">当前文件未识别到完整定期报告期间，请先确认原文。</p>}
      {annual&&!hasIdentity&&<div className="peer-identity-confirm"><span>{targetCode==='代码存在冲突'?'公司身份信息存在冲突，请先核对报告原文。':'暂未自动确认公司代码，请对照报告原文补充。'}</span><label>报告公司证券代码<input aria-label="确认目标公司证券代码" value={manualCode} maxLength={6} inputMode="numeric" placeholder="输入六位代码" disabled={loading} onChange={event=>{setManualCode(event.target.value.replace(/\D/g,''));setIdentityConfirmed(false)}}/></label><label><input type="checkbox" checked={identityConfirmed} disabled={loading||!/^\d{6}$/.test(manualCode)||targetCode==='代码存在冲突'} onChange={event=>setIdentityConfirmed(event.target.checked)}/>已对照本期原文确认公司与代码</label></div>}
      <div className="peer-picker-controls">
        <div className="peer-autocomplete" ref={box} onBlur={event=>{if(!box.current?.contains(event.relatedTarget as Node))setOpen(false)}}>
          <label htmlFor="peer-company-search">搜索并添加同行公司</label>
          <div className={`peer-input-wrap ${open?'expanded':''}`}><Search size={18}/><input ref={input} id="peer-company-search" role="combobox" aria-autocomplete="list" aria-expanded={open} aria-controls="peer-company-options" aria-activedescendant={open&&options[active]?`peer-option-${options[active].code}`:undefined} value={query} disabled={loading} onFocus={()=>setOpen(true)} onChange={event=>{setQuery(event.target.value);setOpen(true)}} placeholder="输入简称、代码或拼音首字母，例如：宁德、300、nd" onKeyDown={event=>{
            if(event.nativeEvent.isComposing)return
            if(event.key==='Escape'){setOpen(false);return}
            if(event.key==='ArrowDown'){event.preventDefault();setOpen(true);setActive(index=>Math.min(index+1,Math.max(0,options.length-1)))}
            if(event.key==='ArrowUp'){event.preventDefault();setActive(index=>Math.max(0,index-1))}
            if(event.key==='Enter'&&open){event.preventDefault();if(options[active])add(options[active]);else if(showLookup)void lookup()}
          }}/>{query&&<button className="peer-clear" aria-label="清空同行搜索" onClick={()=>{setQuery('');input.current?.focus()}}><X size={15}/></button>}<button className="peer-toggle" aria-label={open?'收起公司候选':'展开公司候选'} disabled={loading} onClick={()=>{setOpen(!open);if(!open)input.current?.focus()}}><ChevronDown size={17}/></button></div>
          {open&&<div className={`peer-suggestion-panel ${popup.above?'above':''}`} style={{maxHeight:popup.height}}><header><strong>{query?`“${query}”的匹配公司`:'浏览新能源公司库'}</strong><small>{searching?'查询中…':`${total} 家匹配 · 已选 ${selection.peers.length}/5`}</small></header>
            <div className="peer-suggestion-columns"><span>公司与业务分组</span><span>公司身份</span><span>{year} 年本机数据</span></div>
            <div className="peer-candidate-list" id="peer-company-options" role="listbox" aria-label="同行公司候选">{options.map((item,index)=><button type="button" role="option" aria-selected={index===active} id={`peer-option-${item.code}`} key={item.code} disabled={atLimit||loading} className={index===active?'active':''} onMouseDown={event=>event.preventDefault()} onMouseEnter={()=>setActive(index)} onClick={()=>add(item)}><span className="peer-candidate-name"><b><MatchedName name={item.name} query={query}/></b><small>{item.code}.{item.exchange} · {item.segment}</small></span><CompanyStates item={item} year={year}/><Plus size={15} className="peer-add-icon"/></button>)}</div>
            {!searching&&!options.length&&!lookupItem&&<p className="peer-no-matches">{excluded.includes(query)?'这家公司已被选中，或就是目标公司，不会重复添加。':'没有匹配的本地公司。可换个关键词，或输入六位证券代码进行联网核验。'}</p>}
            {searching&&<p className="peer-search-loading"><LoaderCircle size={15} className="spin"/>读取公司候选与本机数据状态…</p>}
            {hasMore&&<button className="peer-more" disabled={searching} onMouseDown={event=>event.preventDefault()} onClick={()=>void more()}>加载更多公司</button>}
            {showLookup&&!lookupItem&&<div className="peer-external-lookup"><span><Globe size={17}/><b>本地名单未收录 {query}</b><small>先核验公司身份，再由你确认添加；不会自动下载年报。</small></span><button className="secondary-button" disabled={lookingUp||loading} onMouseDown={event=>event.preventDefault()} onClick={()=>void lookup()}>{lookingUp?<LoaderCircle size={14} className="spin"/>:<Search size={14}/>}联网查找</button></div>}
            {lookupItem&&<div className="peer-lookup-result"><span><b>{lookupItem.name} · {lookupItem.code}.{lookupItem.exchange}</b><small>{lookupItem.segment}</small><CompanyStates item={lookupItem} year={year}/></span><button className="primary-button" disabled={atLimit||loading} onMouseDown={event=>event.preventDefault()} onClick={()=>add(lookupItem)}><Plus size={14}/>添加这家公司</button></div>}
            {lookupError&&<p className="peer-lookup-error" role="alert">{lookupError}</p>}
            <footer>{atLimit?'已选满 5 家，请先移除一家再添加。':'↑ ↓ 切换候选 · Enter 添加 · Esc 收起'}<span>身份核对 ≠ 年报或指标已可用</span></footer>
          </div>}
        </div>
        <label className="peer-segment-filter"><span>按业务分组筛选</span><select aria-label="同行公司业务筛选" disabled={loading} value={segment} onChange={event=>{setSegment(event.target.value);setOpen(true)}}><option value="">全部业务分组</option>{segments.map(item=><option key={item}>{item}</option>)}</select></label>
      </div>
      {notice&&<p className="peer-directory-notice" role="status"><CircleAlert size={15}/>{notice}</p>}
      <div className="peer-chosen-head"><strong>已选同行 <span>{selection.peers.length}/5</span></strong></div>
      <div className="peer-chosen-grid">{selection.peers.map(peer=>{const item={...peer,...info[peer.code]};return <article key={peer.code}><div><strong>{item.name}</strong><small>{peer.code}.{peer.exchange} · {item.segment}</small></div><button className="icon-button" aria-label={`移除同行 ${peer.name}`} disabled={loading} onClick={()=>update({peers:selection.peers.filter(item=>item.code!==peer.code)})}><X size={15}/></button><CompanyStates item={item} year={year}/></article>})}{!selection.peers.length&&<p className="peer-no-matches">尚未选择同行。请在上方搜索，添加 2—5 家公司。</p>}</div>
      {activity ? <TaskProgress activity={activity} onStop={onStop}/> : <div className="peer-start-row"><button className="primary-button" disabled={!canRun} onClick={()=>{setOpen(false);onAnalyze(effectiveTarget)}}>{loading?<LoaderCircle className="spin" size={16}/>:<Users size={16}/>}计算 {selection.peers.length} 家同行</button></div>}
      {!canRun&&!loading&&<p className="peer-validation"><CircleAlert size={14}/>请确认目标报告身份，并选择 2—5 家不同的同行。</p>}
    </div>
    {stale&&<p className="peer-directory-notice"><CircleAlert size={16}/>同行、目标公司或年度已变化。请重新计算；上一次保存的结果不会被删除。</p>}
    <div className="comparison-summary">{summary.map(item=><article key={item.label}><small>{item.label}</small><strong>{item.value}</strong><span>{item.note}</span></article>)}</div>
    <PeerComparisonTable rows={rows} company={company} targetCode={effectiveTarget||targetCode} year={year} peers={selection.peers} stale={stale} periodLabel={`${periodLabel} · ${basis==='single'?'单季':'累计'}`}/>
  </div>
}
