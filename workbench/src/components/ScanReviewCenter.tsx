import { useEffect, useRef, useState } from 'react'
import { financeApi, metricNames } from '../services/financeApi'
import { requestJson } from '../services/http'
import type { BackendTask } from '../types/finance'
import { edited, requestKey, reviewProblems } from './scanReviewModel'
import type { Candidate, Decision, Review, ReviewState, Reviewed } from './scanReviewModel'
import './scan-review.css'

const base = (import.meta.env.VITE_API_BASE_URL ?? '/api').replace(/\/$/,'')
const names: Record<string,string> = { revenue:'营业收入', attributable_net_profit:'归母净利润', operating_cash_flow:'经营活动现金流量净额', total_assets:'总资产', attributable_equity:'归母净资产' }
const statuses: Record<string,string> = { pending:'待复核', confirm:'确认', correct:'纠正', reject:'拒绝', confirmed:'已确认', corrected:'已纠正', withheld_conflict:'冲突，暂缓计算', withheld_identity:'口径未确认', ready:'已处理本批候选', partial:'部分字段待复核', blocked:'口径未确认', no_confirmed_data:'尚无可用数据' }
const warnings: Record<string,string> = { OCR_IDENTITY_UNVERIFIED:'未能从所选页独立核对公司和年度，请对照报告封面。', OCR_SECTION_UNVERIFIED:'未识别到主要会计数据和财务指标章节，请检查物理页码。', NO_VERIFIED_FINANCIAL_ROWS:'未提取到符合本版布局要求的财务行。', OCR_LOW_CONFIDENCE:'部分识别置信度不足，请重点核对。', OCR_UNIT_MISSING:'缺少清晰的金额单位。', OCR_FAILED:'OCR不可用或识别失败，请查看后端日志。', OCR_YOY_MISMATCH:'金额与原文同比百分比不一致，请核查两列和单位。' }
type Preview = { url:string; width:number; height:number }

export function ScanReviewCenter({restoreTaskId,onTaskCreated}:{restoreTaskId?:string;onTaskCreated?:(id:string)=>void}) {
  const [file,setFile]=useState<File|null>(null), [localUrl,setLocalUrl]=useState('')
  const [company,setCompany]=useState('宁德时代'), [year,setYear]=useState('2025'), [pages,setPages]=useState('11')
  const [task,setTask]=useState<BackendTask|null>(null), [state,setState]=useState<ReviewState|null>(null), [form,setForm]=useState<Review|null>(null)
  const [page,setPage]=useState(1), [preview,setPreview]=useState<Preview|null>(null), [previewError,setPreviewError]=useState(''), [zoom,setZoom]=useState(100)
  const [selected,setSelected]=useState(''), [busy,setBusy]=useState(false), [error,setError]=useState(''), [message,setMessage]=useState('')
  const [history,setHistory]=useState<{revision:number;created_at:string;review:{reviewer:{id:string}}}[]>([])
  const submission=useRef<{signature:string;body:Review}|null>(null), generation=useRef(0)
  useEffect(()=>{if(!file){setLocalUrl('');return}const url=URL.createObjectURL(file);setLocalUrl(url);return()=>URL.revokeObjectURL(url)},[file])
  useEffect(()=>()=>{generation.current++},[])

  const load=async(id:string,token:number)=>{
    const next=await requestJson<ReviewState>(`${base}/scan/${encodeURIComponent(id)}/state`)
    if(generation.current!==token)return
    setState(next);setForm(next.template);setCompany(next.scan.identity_claim.company_name);setYear(String(next.scan.identity_claim.report_year));setPages(next.scan.pages_read.join(','))
    setPage(next.scan.pages_read[0]??1);setSelected(next.scan.candidates[0]?.candidate_id??'');submission.current=null
    setHistory([])
  }
  useEffect(()=>{
    if(!restoreTaskId)return
    const token=++generation.current;setBusy(true);setError('');setMessage('');setState(null);setForm(null)
    financeApi.getTaskProgress(restoreTaskId).then(async t=>{if(generation.current!==token)return;setTask(t);await load(t.id,token)}).catch(e=>{if(generation.current===token)setError(String(e.message??e))}).finally(()=>{if(generation.current===token)setBusy(false)})
  },[restoreTaskId])

  useEffect(()=>{
    if(!task?.id||!state)return
    const controller=new AbortController();let url='';setPreview(null);setPreviewError('')
    fetch(`${base}/scan/${encodeURIComponent(task.id)}/pages/${page}`,{signal:controller.signal}).then(async response=>{
      if(!response.ok){const body=await response.json().catch(()=>({}));throw new Error(body.detail??'页面预览失败')}
      const width=Number(response.headers.get('X-PDF-Width')),height=Number(response.headers.get('X-PDF-Height'))
      if(!(Number.isFinite(width)&&width>0&&Number.isFinite(height)&&height>0))throw new Error('未能读取原文页面尺寸，已暂停高亮；请检查跨域响应头配置。')
      const blob=await response.blob();if(controller.signal.aborted)return
      url=URL.createObjectURL(blob);setPreview({url,width,height})
    }).catch(e=>{if(!controller.signal.aborted)setPreviewError(String(e.message??e))})
    return()=>{controller.abort();if(url)URL.revokeObjectURL(url)}
  },[task?.id,state?.scan.document.file_sha256,page])

  async function start(){
    if(!file)return
    if(!/^\s*\d+(\s*,\s*\d+)*\s*$/.test(pages)||!company.trim()||!/^20\d{2}$/.test(year)){setError('请填写公司、年度和物理页码；每次最多5页。');return}
    const token=++generation.current;setBusy(true);setError('');setState(null);setForm(null);setMessage('')
    try{
      let next=await financeApi.createScanTask(file,pages,company.trim(),Number(year))
      if(generation.current!==token)return
      setTask(next);onTaskCreated?.(next.id)
      const deadline=Date.now()+10*60*1000
      while(!['completed','review_required','failed','interrupted'].includes(next.status)){
        if(generation.current!==token)return
        if(Date.now()>deadline)throw new Error('识别仍在后台运行，可稍后从任务记录恢复；请勿重复上传。')
        await new Promise(resolve=>setTimeout(resolve,1000));next=await financeApi.getTaskProgress(next.id)
        if(generation.current!==token)return
        setTask(next)
      }
      if(['failed','interrupted'].includes(next.status))throw new Error(next.error??'扫描识别失败')
      await load(next.id,token)
    }catch(e){if(generation.current===token)setError(e instanceof Error?e.message:String(e))}
    finally{if(generation.current===token)setBusy(false)}
  }
  function changeDecision(index:number,change:Partial<Decision>){setForm(old=>old?{...old,decisions:old.decisions.map((d,i)=>i===index?{...d,...change}:d)}:old)}
  async function submit(){
    if(!form||!state||!task)return
    const problems=reviewProblems(form,state.scan.candidates);if(problems.length){setError(problems.join(' '));return}
    const token=generation.current;setBusy(true);setError('');setMessage('')
    const signature=JSON.stringify(form)
    if(submission.current?.signature!==signature)submission.current={signature,body:{...form,request_id:requestKey(),expected_revision:state.revision}}
    const body=submission.current!.body
    try{
      const response=await requestJson<Reviewed>(`${base}/scan/${task.id}/review`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      if(generation.current!==token)return
      const next=await requestJson<ReviewState>(`${base}/scan/${task.id}/state`)
      if(generation.current!==token)return
      setState(next);setForm(next.template);submission.current=null;setHistory([])
      setMessage(`已保存版本 ${response.review_revision}：${statuses[response.payload?.status??'']??response.payload?.status??'已保存'}。本页仅覆盖已复核的摘要字段。`)
    }catch(e){if(generation.current===token)setError(`${e instanceof Error?e.message:String(e)}；版本冲突时请点击“加载已保存版本”，注意该操作会替换未保存修改。`)}
    finally{if(generation.current===token)setBusy(false)}
  }
  const active=state?.scan.candidates.find(c=>c.candidate_id===selected)
  const result=state?.result
  const metrics=result?.analysis?.metrics??[], unavailable=result?.analysis?.unavailable_metrics??[]
  function locate(candidate:Candidate){setSelected(candidate.candidate_id);setPage(candidate.sources.current.page)}

  return <div className="sr-root">
    <div className="sr-toolbar"><label>公司名称<input value={company} disabled={busy} onChange={e=>setCompany(e.target.value)}/></label><label>年度<input value={year} disabled={busy} onChange={e=>setYear(e.target.value)}/></label><label>识别物理页码（最多5页）<input value={pages} disabled={busy} onChange={e=>setPages(e.target.value)} placeholder="11,12"/></label><label>扫描 PDF<input type="file" accept=".pdf,application/pdf" disabled={busy} onChange={e=>setFile(e.target.files?.[0]??null)}/></label><button disabled={busy||!file} onClick={start}>{busy?'处理中…':'上传并提取候选'}</button></div>
    <p className="sr-hint">本版识别年度报告“主要会计数据和财务指标”中的五类字段。物理页码可能与印刷页码不同；可先在左侧预览中找表格。未复核数据不参与计算。</p>
    {error&&<div className="sr-error" role="alert">{error}</div>}{message&&<div className="sr-message" role="status">{message}</div>}
    <div className="sr-columns"><section className="sr-viewer" aria-label="扫描原文">
      {state&&<div className="sr-pagebar"><button disabled={page<=1} onClick={()=>setPage(p=>p-1)}>上一页</button><label>物理页<input type="number" min={1} max={state.scan.document.page_count} value={page} onChange={e=>{const p=Number(e.target.value);if(Number.isInteger(p)&&p>=1&&p<=state.scan.document.page_count)setPage(p)}}/></label><span>／{state.scan.document.page_count}</span><button disabled={page>=state.scan.document.page_count} onClick={()=>setPage(p=>p+1)}>下一页</button><select value={zoom} onChange={e=>setZoom(Number(e.target.value))} aria-label="缩放">{[100,150,200].map(z=><option key={z} value={z}>{z}%</option>)}</select></div>}
      <div className="sr-page-scroll">{preview?<svg aria-label={`原文物理第${page}页`} viewBox={`0 0 ${preview.width} ${preview.height}`} style={{width:`${zoom}%`,maxWidth:'none',display:'block'}}><image href={preview.url} width={preview.width} height={preview.height}/>{active&&(['label','current','previous'] as const).map(role=>{const s=active.sources[role];return s.page===page?<rect key={role} x={s.bbox[0]} y={s.bbox[1]} width={s.bbox[2]-s.bbox[0]} height={s.bbox[3]-s.bbox[1]} fill={role==='label'?'#fbbf2433':'#14b8a633'} stroke={role==='label'?'#d97706':'#0f766e'} strokeWidth={1.5}><title>{role==='label'?'项目':role==='current'?'本期':'上期'}：{s.text}</title></rect>:null})}</svg>:state?<p>{previewError||'正在加载扫描页…'}</p>:localUrl?<iframe src={localUrl} title="上传前 PDF 预览"/>:<p>选择 PDF 后预览原文；识别后点击右侧候选定位金额。</p>}</div>
      {task&&<a href={financeApi.getTaskFileUrl(task.id)} target="_blank" rel="noreferrer">打开完整原 PDF</a>}
    </section><section className="sr-workbench">
      <h3>人工复核中心 {state&&<small>已保存版本 {state.revision}</small>}</h3>
      {busy&&!state&&<p role="status">{task?.stage??'正在准备'}；首次使用可能需要下载 OCR 模型。</p>}
      {form&&state&&<>
        <fieldset disabled={busy}><legend>先确认报告身份与口径</legend><p>{form.identity.company_name} · {form.identity.report_year}年度 · 人民币</p><label>复核人<input value={form.reviewer.id??''} onChange={e=>setForm({...form,reviewer:{id:e.target.value,kind:'human'}})} placeholder="姓名或编号"/></label><small>本地版记录自报身份；不等同于登录认证。</small><label><input type="checkbox" checked={form.identity.decision==='confirm'} onChange={e=>setForm({...form,identity:{...form.identity,decision:e.target.checked?'confirm':'pending'}})}/>已核对原文中的公司名称、年度及本期／上期列</label><label><input type="checkbox" checked={form.identity.statement_scope==='consolidated'} onChange={e=>setForm({...form,identity:{...form.identity,statement_scope:e.target.checked?'consolidated':null}})}/>已确认属于合并披露口径，非母公司单体报表</label><label>上期数据重述情况<select value={form.identity.comparative_restated===null?'unknown':String(form.identity.comparative_restated)} onChange={e=>setForm({...form,identity:{...form.identity,comparative_restated:e.target.value==='unknown'?null:e.target.value==='true'}})}><option value="unknown">尚未核对</option><option value="false">未重述</option><option value="true">采用本报告重述后的比较数</option></select></label></fieldset>
        {state.scan.candidates.length===0&&<div className="sr-error">本次未形成可复核字段，请检查所选页是否包含目标表格、年份及单位。此时不能生成完整财务分析。</div>}
        {state.scan.candidates.map((candidate,index)=>{const decision=form.decisions.find(d=>d.candidate_id===candidate.candidate_id);if(!decision)return null;const di=form.decisions.indexOf(decision);const saved=result?.review?.decisions.find(d=>d.candidate_id===candidate.candidate_id);return <article key={candidate.candidate_id} className={selected===candidate.candidate_id?'sr-card sr-selected':'sr-card'}><header><button onClick={()=>locate(candidate)}>{names[candidate.metric_code]??candidate.metric_code} · 定位原文</button><span>{saved?statuses[saved.status]??saved.status:candidate.status==='needs_review'?'需要重点复核':'待人工复核'}</span></header><p className="sr-hint">OCR 原值：{candidate.sources.current.text}／{candidate.sources.previous.text} {candidate.raw_unit}；物理第{candidate.sources.current.page}页</p><fieldset disabled={busy}><div className="sr-cells">{(['current','previous'] as const).map(role=><label key={role}>{role==='current'?candidate.period:candidate.previous_period}年<input type="text" inputMode="decimal" value={decision.values[role].raw_value} onFocus={()=>locate(candidate)} onChange={e=>changeDecision(di,{values:{...decision.values,[role]:{...decision.values[role],raw_value:e.target.value}},evidence_confirmed:false})}/><select value={decision.values[role].raw_unit} aria-label={`${role}金额单位`} onChange={e=>changeDecision(di,{values:{...decision.values,[role]:{...decision.values[role],raw_unit:e.target.value}},evidence_confirmed:false})}>{['元','千元','万元','亿元'].map(u=><option key={u}>{u}</option>)}</select></label>)}</div><label>处理<select value={decision.decision} onChange={e=>changeDecision(di,{decision:e.target.value as Decision['decision']})}>{(['pending','confirm','correct','reject'] as const).map(s=><option key={s} value={s}>{statuses[s]}</option>)}</select></label>{edited(candidate,decision)&&<small>金额或单位已修改，请选择“纠正”并填写原因。</small>}<label><input type="checkbox" checked={decision.evidence_confirmed} onChange={e=>changeDecision(di,{evidence_confirmed:e.target.checked})}/>已逐项核对原文的项目、两列金额、年份和单位</label><label>核查说明／纠正或拒绝原因<textarea value={decision.note??''} onChange={e=>changeDecision(di,{note:e.target.value})}/></label></fieldset></article>})}
        <div className="sr-actions"><button disabled={busy||!state.scan.candidates.length} onClick={submit}>保存复核并计算已确认字段</button><button disabled={busy} onClick={async()=>{if(!task)return;setBusy(true);setError('');try{await load(task.id,generation.current)}catch(e){setError(String(e))}finally{setBusy(false)}}}>加载已保存版本</button><button disabled={busy} onClick={async()=>{try{const response=await requestJson<{reviews:typeof history}>(`${base}/scan/${task!.id}/reviews`);setHistory(response.reviews)}catch(e){setError(String(e))}}}>查看复核历史</button></div>
        {!!history.length&&<ul>{history.map(h=><li key={h.revision}>版本{h.revision} · {h.review.reviewer.id} · {h.created_at}</li>)}</ul>}
        <details><summary>文件与候选绑定信息</summary><p className="sr-hash">文件 SHA-256：{form.file_sha256}<br/>候选快照：{form.candidate_snapshot_sha256}<br/>任务：{task?.id}</p></details>
      </>}
      {!!state?.scan.warnings.length&&<details open><summary>识别限制与提示</summary><ul>{state.scan.warnings.map((w,i)=><li key={i}>{w.page?`第${w.page}页：`:''}{warnings[w.code]??w.detail??w.code}</li>)}</ul></details>}
    </section></div>
    {result&&<section className="sr-results"><h3>已复核数据的计算结果</h3><p>本批状态：{statuses[result.status]??result.status}。仅覆盖摘要字段；缺失指标不补零，数据不足不代表无风险。</p><table><thead><tr><th>指标</th><th>期间</th><th>结果（引擎原始精确值）</th></tr></thead><tbody>{metrics.map((m,i)=><tr key={i}><td>{metricNames[m.metric_code]??m.metric_code}</td><td>{m.period}</td><td>{m.value} {m.unit==='percent'?'%':m.unit}</td></tr>)}</tbody></table>{!metrics.length&&<p>当前尚无可计算指标。</p>}<details><summary>不可计算项（{unavailable.length}项）</summary><ul>{unavailable.map((m,i)=><li key={i}>{metricNames[m.metric_code]??m.metric_code} · {m.period}：{m.reason_code}</li>)}</ul></details></section>}
  </div>
}
