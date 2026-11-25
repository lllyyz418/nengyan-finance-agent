import { Download, Filter, Search, ChevronsUpDown, Link2, FileText, LoaderCircle } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { createColumnHelper, flexRender, getCoreRowModel, getFilteredRowModel, getSortedRowModel, useReactTable } from '@tanstack/react-table'
import type { FinancialMetric } from '../types/finance'
import { exportMetrics } from '../services/metricExport'

const columnHelper = createColumnHelper<FinancialMetric>()
export function FinancialTable({ data, onEvidence, company, period, demo,searchFocus }: { data: FinancialMetric[]; onEvidence: (id: string) => void; company: string; period: string; demo: boolean;searchFocus?:{name:string;revision:number} }) {
  const [filter, setFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('全部状态')
  const [showFilters, setShowFilters] = useState(false)
  const [exporting, setExporting] = useState<'excel' | 'pdf' | null>(null)
  const [exportError, setExportError] = useState('')
  useEffect(()=>{if(searchFocus){setFilter(searchFocus.name);setStatusFilter('全部状态')}},[searchFocus])
  const filteredData = useMemo(() => statusFilter === '全部状态' ? data : data.filter((item) => item.status === statusFilter), [data, statusFilter])
  const columns = useMemo(() => [
    columnHelper.accessor('name', { header: '指标名称', cell: (i) => <div className="metric-name-cell"><strong>{i.getValue()}</strong><small>{i.row.original.formula}</small></div> }),
    columnHelper.accessor('current', { header: '当前期', cell: (i) => <div className="metric-period-cell"><b className="numeric">{i.getValue()??'—'} {i.row.original.unit}</b><small>{i.row.original.currentPeriod}</small></div> }),
    columnHelper.accessor('previous', { header: '比较期', cell: (i) => <div className="metric-period-cell" title={i.row.original.comparisonNote}><b>{i.getValue()??'未取得'}</b><small>{i.row.original.comparisonPeriod}</small></div> }),
    columnHelper.accessor('change', { header: '变动', cell: (i) => <span title={i.row.original.changeNote}>{i.getValue()??'暂不比较'}</span> }),
    columnHelper.accessor('status', { header: '计算状态', cell: (i) => <span className={`status metric-status-${i.getValue()}`}>{i.getValue()}</span> }),
    columnHelper.accessor('inputFacts', { header: '输入事实', cell: (i) => `${i.getValue()} 项` }),
    columnHelper.accessor('source', { header: '依据 / 原因', cell: (i) => <div className="metric-diagnostic-cell">{i.row.original.unavailableReason&&<span className="unavailable-copy">{i.row.original.unavailableReason}</span>}{i.row.original.evidenceId&&<button className="source-link" onClick={() => onEvidence(i.row.original.evidenceId!)}><Link2 size={12} />本期证据</button>}{i.row.original.comparisonEvidenceId&&<button className="source-link" onClick={()=>onEvidence(i.row.original.comparisonEvidenceId!)}><Link2 size={12}/>比较期证据</button>}{(i.row.original.comparisonNote||i.row.original.changeNote)&&<details><summary>比较口径与说明</summary><p>{i.row.original.comparisonNote}{i.row.original.changeNote&&`；${i.row.original.changeNote}`}</p></details>}</div> }),
  ], [onEvidence])
  const table = useReactTable({ data: filteredData, columns, state: { globalFilter: filter }, onGlobalFilterChange: setFilter, getCoreRowModel: getCoreRowModel(), getFilteredRowModel: getFilteredRowModel(), getSortedRowModel: getSortedRowModel() })
  const exportFile = async (format: 'excel' | 'pdf') => {
    setExporting(format); setExportError('')
    try { await exportMetrics(table.getRowModel().rows.map((row) => row.original), { company, period, demo }, format) }
    catch (error) { setExportError(error instanceof Error ? error.message : '导出失败，请重试') }
    finally { setExporting(null) }
  }
  return <div className="table-wrap">
    <div className="table-toolbar"><label className="table-search"><Search size={16} /><input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="搜索指标、公式或状态" /></label><div className="table-export-actions"><button className="secondary-button table-filter-button" aria-expanded={showFilters} onClick={() => setShowFilters(!showFilters)}><Filter size={15} />筛选</button><button className="secondary-button" disabled={!!exporting || !table.getRowModel().rows.length} onClick={() => exportFile('excel')}>{exporting === 'excel' ? <LoaderCircle className="spin" size={15}/> : <Download size={15}/>}下载 Excel</button><button className="secondary-button" disabled={!!exporting || !table.getRowModel().rows.length} onClick={() => exportFile('pdf')}>{exporting === 'pdf' ? <LoaderCircle className="spin" size={15}/> : <FileText size={15}/>}下载 PDF</button></div></div>
    {showFilters && <label className="table-filter-row">计算状态<select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>{['全部状态', '已计算', '不可计算', '数据冲突'].map((status) => <option key={status}>{status}</option>)}</select></label>}
    {exportError && <p className="table-export-error" role="alert">{exportError}</p>}
    <div className="scroll-table"><table><thead>{table.getHeaderGroups().map((g) => <tr key={g.id}>{g.headers.map((h) => <th key={h.id}><button onClick={h.column.getToggleSortingHandler()}>{flexRender(h.column.columnDef.header, h.getContext())}<ChevronsUpDown size={13} /></button></th>)}</tr>)}</thead><tbody>{!table.getRowModel().rows.length && <tr><td colSpan={columns.length} className="table-empty-cell">{data.length ? '没有符合筛选条件的指标，请调整筛选。' : '尚无指标数据。请先载入并完成财报分析，才可查看来源或导出文件。'}</td></tr>}{table.getRowModel().rows.map((row) => <tr className={row.original.status !== '已计算' ? 'unavailable-row' : ''} key={row.id}>{row.getVisibleCells().map((c) => <td key={c.id}>{flexRender(c.column.columnDef.cell, c.getContext())}</td>)}</tr>)}</tbody></table></div>
    <footer className="table-footer">{data.length ? `${data.filter((m) => m.status === '已计算').length} 项已计算 · ${data.filter((m) => m.status !== '已计算').length} 项不可用` : '暂无已载入的指标结果'} <span>缺失与冲突不会按 0 展示</span></footer>
  </div>
}
