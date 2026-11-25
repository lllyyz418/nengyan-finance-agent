import { ArrowDownRight, ArrowUpRight, CircleAlert, Users } from 'lucide-react'
import type { CompanyComparison, CompanyOption } from '../types/finance'
import './peerComparisonTable.css'

export function PeerComparisonTable({rows, company, targetCode, year, peers, stale,periodLabel}: {
  rows:CompanyComparison[];company:string;targetCode:string;year:number;peers:CompanyOption[];stale:boolean;periodLabel?:string;
}) {
  const peerColumns = Math.max(1, peers.length)
  return <div className="peer-results">
    <div className="peer-results-scroll" role="region" aria-label="同行比较结果表，可横向滚动" tabIndex={0}>
      <table className="peer-results-table" style={{minWidth:690+peerColumns*140}}>
        <caption className="peer-results-caption">{company} · {periodLabel??`${year} 年度`}同行财务指标比较</caption>
        <colgroup><col style={{width:180}}/><col style={{width:200}}/>{Array.from({length:peerColumns},(_,i)=><col key={i} style={{width:140}}/>)}<col style={{width:145}}/><col style={{width:165}}/></colgroup>
        <thead><tr>
          <th rowSpan={2} scope="col">指标</th>
          <th rowSpan={2} scope="col" className="peer-results-target"><span>目标公司</span><strong title={company}>{company}</strong><small>{targetCode} · {year}</small></th>
          <th colSpan={peerColumns} scope="colgroup" className="peer-results-group">同行公司值</th>
          <th rowSpan={2} scope="col">同行中位数</th>
          <th rowSpan={2} scope="col">差异 / 状态</th>
        </tr><tr>{peers.length?peers.map(peer=><th key={peer.code} scope="col" className="peer-results-company"><strong title={peer.name}>{peer.name}</strong><small>{peer.code}</small></th>):<th scope="col">等待选择同行</th>}</tr></thead>
        <tbody>{!rows.length?<tr><td colSpan={4+peerColumns}><div className="peer-results-empty"><Users size={24}/><strong>{stale?'请按新配置重新计算':'尚未生成真实同行比较'}</strong></div></td></tr>:rows.map(row=>{
          const positive=Boolean(row.delta?.startsWith('+'))
          return <tr key={row.metric}>
            <th scope="row">{row.metric}</th><td className="peer-results-number peer-results-target-number">{row.target??'不可用'}</td>
            {peers.length?peers.map((peer,index)=>{
              const point=row.peerValues?.find(item=>item.code===peer.code)
              // Historical records may only contain display strings. Retain those
              // verbatim when no code-linked points were saved; never invent values.
              const value=row.peerValues?point?.value:(row.peers[index]??null)
              return <td key={peer.code} className={`peer-results-number ${value==null?'peer-results-missing':''}`} title={value==null?'未取得可比较的有效指标':String(value)}>{value??'不可用'}</td>
            }):<td className="peer-results-missing">—</td>}
            <td><div className="peer-results-median"><b>{row.peerMedian??'—'}</b><small>{row.validPeers} 家有效</small></div></td>
            <td><div className="peer-results-outcome">{row.delta&&<b className={positive?'positive':'negative'}>{positive?<ArrowUpRight size={15}/>:<ArrowDownRight size={15}/>}<span>{row.delta}</span></b>}<em className={`compare-status compare-${row.status}`}>{row.status}</em></div></td>
          </tr>
        })}</tbody>
      </table>
    </div>
    <p className="comparison-note"><CircleAlert size={16}/>同行中位数仅用于描述性比较；产品结构与会计政策差异仍需人工判断。</p>
  </div>
}
