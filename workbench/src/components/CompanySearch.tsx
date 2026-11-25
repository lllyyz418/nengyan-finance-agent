import { useEffect, useRef, useState } from 'react'
import { Check, Globe, LoaderCircle, Search } from 'lucide-react'
import { financeApi } from '../services/financeApi'
import { dataLabel } from '../data/peerCatalog'
import type { CompanyOption, CompanySearchResponse } from '../types/finance'
import './companySearch.css'

export function CompanySearch({value,code,year,disabled,onInput,onSelect}:{value:string;code:string;year:number;disabled:boolean;onInput:(value:string)=>void;onSelect:(item:CompanyOption)=>void}) {
  const [open,setOpen]=useState(false),[mode,setMode]=useState<'local'|'online'>('local')
  const [data,setData]=useState<CompanySearchResponse>(),[busy,setBusy]=useState(false),[error,setError]=useState('')
  const [active,setActive]=useState(0),[retry,setRetry]=useState(0)
  const [popup,setPopup]=useState({height:400,above:false})
  const box=useRef<HTMLDivElement>(null),revision=useRef(0),controller=useRef<AbortController>(),autoOnline=useRef(true)
  const query=value.trim()
  useEffect(()=>{
    const close=(event:Event)=>{if(!box.current?.contains(event.target as Node))setOpen(false)}
    document.addEventListener('pointerdown',close)
    document.addEventListener('focusin',close)
    return()=>{document.removeEventListener('pointerdown',close);document.removeEventListener('focusin',close)}
  },[])
  useEffect(()=>{
    if(!open)return
    const measure=()=>{
      const rect=box.current?.getBoundingClientRect();if(!rect)return
      const below=window.innerHeight-rect.bottom-16,above=rect.top-76,up=below<200&&above>below
      setPopup({height:Math.max(160,Math.min(540,up?above:below)),above:up})
    }
    measure();window.addEventListener('resize',measure);window.addEventListener('scroll',measure,{passive:true,capture:true})
    return()=>{window.removeEventListener('resize',measure);window.removeEventListener('scroll',measure,true)}
  },[open])
  useEffect(()=>{
    const current=++revision.current,abort=new AbortController();controller.current=abort
    setData(undefined);setError('');setActive(0);setBusy(open)
    if(!open)return()=>abort.abort()
    const timer=window.setTimeout(async()=>{
      try {
        const result=await financeApi.searchCompanies(query,year,0,mode==='online',abort.signal)
        if(abort.signal.aborted||current!==revision.current)return
        if(mode==='local'&&!result.total&&query.length>=2&&autoOnline.current){autoOnline.current=false;setMode('online');return}
        setData(result)
      } catch(cause) {
        if(!abort.signal.aborted&&current===revision.current)setError(cause instanceof Error?cause.message:'公司查询失败，请重试')
      } finally {if(!abort.signal.aborted&&current===revision.current)setBusy(false)}
    },180)
    return()=>{window.clearTimeout(timer);abort.abort()}
  },[query,year,open,mode,retry])
  const choose=(item:CompanyOption)=>{onSelect(item);setOpen(false);setMode('local')}
  const more=async()=>{
    if(busy||!data?.has_more)return
    const current=revision.current;setBusy(true);setError('')
    try {
      const result=await financeApi.searchCompanies(query,year,data.items.length,mode==='online',controller.current?.signal)
      if(current===revision.current&&!controller.current?.signal.aborted)setData(previous=>previous?{...result,items:[...previous.items,...result.items.filter(item=>!previous.items.some(old=>old.code===item.code))]}:result)
    } catch(cause) {if(current===revision.current&&!controller.current?.signal.aborted)setError(cause instanceof Error?cause.message:'加载更多失败，请重试')}
    finally {if(current===revision.current&&!controller.current?.signal.aborted)setBusy(false)}
  }
  const move=(next:number)=>{setActive(next);document.getElementById(`report-company-${data?.items[next]?.code}`)?.scrollIntoView({block:'nearest'})}
  const items=data?.items??[]
  return <div className="company-search-label report-company-search" ref={box}>
    <label htmlFor="report-company-query">公司 / 证券代码</label>
    <div className="control-input"><Search size={17}/><input id="report-company-query" value={value} disabled={disabled} autoComplete="off" role="combobox" aria-expanded={open} aria-controls="report-company-options" aria-autocomplete="list" aria-activedescendant={open&&items[active]?`report-company-${items[active].code}`:undefined}
      onFocus={()=>setOpen(true)} onChange={event=>{onInput(event.target.value);autoOnline.current=true;setMode('local');setOpen(true)}} placeholder="输入简称、全称、代码或拼音" onKeyDown={event=>{
        if(event.key==='Escape'){setOpen(false);return}
        if(event.key==='ArrowDown'){event.preventDefault();if(!open)setOpen(true);else if(items.length)move(Math.min(active+1,items.length-1))}
        if(event.key==='ArrowUp'){event.preventDefault();if(items.length)move(Math.max(active-1,0))}
        if(event.key==='Enter'&&open&&items[active]){event.preventDefault();choose(items[active])}
      }}/>{/^\d{6}$/.test(code)&&<Check size={16} className="company-selected-check"/>}</div>
    {open&&!disabled&&<div className="report-company-popup" style={{maxHeight:popup.height,...(popup.above?{bottom:'calc(100% + 6px)',top:'auto'}:{})}}>
      <div className="company-popup-heading"><strong>{mode==='local'?'本地候选':'联网候选 · A 股'}</strong><span>{data?`${data.total} 家匹配 · 已显示 ${items.length} 家`:busy?'正在查询…':'查询状态'}</span></div>
      <div id="report-company-options" role="listbox" aria-label="公司候选" aria-busy={busy}>
        {items.map((item,index)=><button type="button" role="option" aria-selected={item.code===code} className={`company-option ${active===index?'keyboard-active':''}`} id={`report-company-${item.code}`} key={item.code} onMouseEnter={()=>setActive(index)} onClick={()=>choose(item)}>
          <span className="company-option-title"><strong>{item.name}</strong><b>{item.code}.{item.exchange}</b></span>
          {item.full_name&&item.full_name!==item.name&&<span className="company-full-name">{item.full_name}</span>}
          <span className="company-option-details"><span>{item.segment}</span><span>{dataLabel(item,year)}</span></span>
        </button>)}
      </div>
      {busy&&<div className="company-search-state" role="status"><LoaderCircle size={17} className="company-search-spinner"/>{mode==='online'?'正在查询官方证券目录…':'正在查询本地候选…'}</div>}
      {error&&<div className="company-search-state company-search-error" role="alert"><span>{error}</span><button type="button" onClick={()=>data?.items.length?void more():setRetry(old=>old+1)}>重试</button></div>}
      {!busy&&!error&&data&&!items.length&&<div className="company-search-state">{mode==='online'?'未找到匹配的 A 股公司，请换用证券简称或六位代码。':'本地没有匹配公司。'}</div>}
      {data?.has_more&&<button type="button" className="company-search-action" disabled={busy} onClick={()=>void more()}>加载更多（{items.length} / {data.total}）</button>}
      <div className="company-popup-footer">{mode==='local'?<button type="button" className="company-search-action" disabled={query.length<2||busy} onClick={()=>setMode('online')}><Globe size={15}/>联网搜索更多公司</button>:<button type="button" className="company-search-action" onClick={()=>{autoOnline.current=false;setMode('local')}}>返回本地候选</button>}<span>{mode==='local'?`本地目录 ${data?.catalog_size??'—'} 家`:'仅查询身份，不会下载或分析报告'}</span></div>
    </div>}
  </div>
}
