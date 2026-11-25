import type { FinancialMetric } from '../types/finance'

export interface ExportContext { company: string; period: string; demo: boolean }
const headers = ['指标名称', '当前期', '比较期', '变动', '计算状态', '输入事实', '数据来源 / 缺失原因', '计算公式']
const values = (m: FinancialMetric) => [m.name, m.current === null ? '—' : `${m.current} ${m.unit}`.trim(), m.previous === null ? '未取得' : `${m.previous} ${m.unit}`.trim(), m.change ?? '暂不比较', m.status, `${m.inputFacts} 项`, [m.unavailableReason,m.evidenceId?`${m.currentPeriod??m.source} 本期证据`:m.source,m.comparisonPeriod?`比较期：${m.comparisonPeriod}`:'',m.comparisonNote,m.changeNote].filter(Boolean).join('；'), m.formula]
const encode = (value: string) => new TextEncoder().encode(value)
const xml = (value: string) => value.replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f]/g, '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')
const dateLabel = () => new Date().toLocaleString('zh-CN')

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = filename; document.body.append(anchor); anchor.click(); anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 10000)
}

// Small, uncompressed ZIP writer for the six XML files in a standard XLSX.
// Cell contents are explicit strings, so source text cannot become an Excel formula.
function zip(files: Record<string, string>): Uint8Array {
  const parts: Uint8Array[] = [], directory: Uint8Array[] = []
  let offset = 0
  const record = (length: number) => { const bytes = new Uint8Array(length); return { bytes, view: new DataView(bytes.buffer) } }
  const crc = (bytes: Uint8Array) => {
    let value = 0xffffffff
    for (const byte of bytes) { value ^= byte; for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ ((value & 1) ? 0xedb88320 : 0) }
    return (value ^ 0xffffffff) >>> 0
  }
  for (const [name, content] of Object.entries(files)) {
    const filename = encode(name), body = encode(content), checksum = crc(body)
    const local = record(30)
    local.view.setUint32(0, 0x04034b50, true); local.view.setUint16(4, 20, true); local.view.setUint16(6, 0x800, true)
    local.view.setUint32(14, checksum, true); local.view.setUint32(18, body.length, true); local.view.setUint32(22, body.length, true); local.view.setUint16(26, filename.length, true)
    parts.push(local.bytes, filename, body)
    const central = record(46)
    central.view.setUint32(0, 0x02014b50, true); central.view.setUint16(4, 20, true); central.view.setUint16(6, 20, true); central.view.setUint16(8, 0x800, true)
    central.view.setUint32(16, checksum, true); central.view.setUint32(20, body.length, true); central.view.setUint32(24, body.length, true); central.view.setUint16(28, filename.length, true); central.view.setUint32(42, offset, true)
    directory.push(central.bytes, filename); offset += local.bytes.length + filename.length + body.length
  }
  const end = record(22), directoryLength = directory.reduce((sum, part) => sum + part.length, 0)
  end.view.setUint32(0, 0x06054b50, true); end.view.setUint16(8, Object.keys(files).length, true); end.view.setUint16(10, Object.keys(files).length, true)
  end.view.setUint32(12, directoryLength, true); end.view.setUint32(16, offset, true)
  const output = new Uint8Array(offset + directoryLength + end.bytes.length)
  let cursor = 0
  for (const part of [...parts, ...directory, end.bytes]) { output.set(part, cursor); cursor += part.length }
  return output
}

export function createMetricsExcel(rows: FinancialMetric[], context: ExportContext): Blob {
  const title = `${context.company} · ${context.period} · 财务指标与可用性`
  const sheetRows = [[title], [`${context.demo ? '演示数据' : '财报分析结果'} · 导出时间 ${dateLabel()} · 共 ${rows.length} 项`], headers, ...rows.map(values)]
  const sheet = sheetRows.map((row, index) => `<row r="${index + 1}" ht="${index > 2 ? 44 : 28}" customHeight="1">${row.map((cell, column) => `<c r="${String.fromCharCode(65 + column)}${index + 1}" t="inlineStr" s="${index === 2 ? 1 : 0}"><is><t xml:space="preserve">${xml(cell)}</t></is></c>`).join('')}</row>`).join('')
  const files = {
    '[Content_Types].xml': '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>',
    '_rels/.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
    'xl/workbook.xml': '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="财务指标与可用性" sheetId="1" r:id="rId1"/></sheets></workbook>',
    'xl/_rels/workbook.xml.rels': '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
    'xl/styles.xml': '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Microsoft YaHei"/></font><font><b/><sz val="11"/><color rgb="FF0B1F33"/><name val="Microsoft YaHei"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="2"><xf fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf><xf fontId="1" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment vertical="center" wrapText="1"/></xf></cellXfs></styleSheet>',
    'xl/worksheets/sheet1.xml': `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="3" topLeftCell="A4" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews><cols>${[36, 22, 22, 18, 16, 12, 40, 65].map((width, index) => `<col min="${index + 1}" max="${index + 1}" width="${width}" customWidth="1"/>`).join('')}</cols><sheetData>${sheet}</sheetData><autoFilter ref="A3:H${sheetRows.length}"/><mergeCells count="2"><mergeCell ref="A1:H1"/><mergeCell ref="A2:H2"/></mergeCells></worksheet>`,
  }
  const bytes = zip(files)
  return new Blob([bytes], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
}

export async function createMetricsPdf(rows: FinancialMetric[], context: ExportContext): Promise<Blob> {
  await document.fonts.ready
  const width = 1190, height = 1684, margin = 60
  const pageWidth = 595.28, pageHeight = 841.89
  // A4 portrait, white paper, black text and black cell borders.
  // Render at twice page resolution; the browser supplies Chinese fonts.
  // Each page is a JPEG image in a standards-compliant PDF, with no remote fonts or services.
  const pdfHeaders = ['指标名称 / 公式', '当前期', '比较期', '变动', '状态', '事实', '来源 / 缺失原因']
  const columns = [264, 138, 120, 110, 120, 74, 244]
  const canvas = document.createElement('canvas'); canvas.width = width; canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('当前浏览器无法生成PDF，请使用桌面浏览器重试')
  const pages: Uint8Array[] = []
  let y = 0, pageNumber = 0
  const wrap = (text: string, space: number) => {
    const lines: string[] = []; let line = ''
    for (const char of text) { if (char === '\n' || (line && ctx.measureText(line + char).width > space)) { lines.push(line); line = char === '\n' ? '' : char } else line += char }
    if (line) lines.push(line)
    return lines.length ? lines : ['']
  }
  const cellBorders = (top: number, rowHeight: number) => {
    ctx.strokeStyle = '#000000'; ctx.lineWidth = 1
    ctx.strokeRect(margin, top, width - margin * 2, rowHeight)
    let x = margin
    for (const col of columns.slice(0, -1)) {
      x += col; ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + rowHeight); ctx.stroke()
    }
  }
  const beginPage = () => {
    pageNumber++; ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, width, height)
    ctx.fillStyle = '#000000'; ctx.font = 'bold 34px "Microsoft YaHei", sans-serif'; ctx.fillText('财务指标与可用性', margin, 75)
    ctx.font = '22px "Microsoft YaHei", sans-serif'; ctx.fillText(`${context.company} · ${context.period}`, margin, 104)
    ctx.font = '18px "Microsoft YaHei", sans-serif'
    ctx.fillText(`${context.demo ? '演示数据' : '财报分析结果'} · ${rows.length} 项指标 · ${dateLabel()}`, margin, 136)
    cellBorders(158, 70)
    let x = margin; ctx.font = 'bold 21px "Microsoft YaHei", sans-serif'
    pdfHeaders.forEach((header, index) => { wrap(header, columns[index] - 20).forEach((line, n) => ctx.fillText(line, x + 10, 186 + n * 26)); x += columns[index] }); y = 228
  }
  const finishPage = async () => {
    ctx.strokeStyle = '#000000'; ctx.beginPath(); ctx.moveTo(margin, height - 80); ctx.lineTo(width - margin, height - 80); ctx.stroke()
    ctx.font = '18px "Microsoft YaHei", sans-serif'; ctx.fillStyle = '#000000'
    ctx.fillText('缺失与冲突不按零计算 · 仅导出当前筛选及排序后的指标', margin, height - 32)
    ctx.fillText(`第 ${pageNumber} 页`, width - margin - 95, height - 32)
    const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob((value) => value ? resolve(value) : reject(new Error('PDF页面生成失败')), 'image/jpeg', .95))
    pages.push(new Uint8Array(await blob.arrayBuffer()))
  }
  beginPage()
  for (const row of rows) {
    const cells = values(row).slice(0, 7)
    ctx.font = '21px "Microsoft YaHei", sans-serif'
    const lines = cells.map((cell, index) => wrap(cell, columns[index] - 24))
    ctx.font = '18px "Microsoft YaHei", sans-serif'; const formula = wrap(row.formula, columns[0] - 24)
    const rowHeight = Math.max(...lines.map((list, index) => index === 0 ? list.length * 28 + formula.length * 25 + 7 : list.length * 28), 38) + 24
    if (y + rowHeight > height - 105) { await finishPage(); beginPage() }
    cellBorders(y, rowHeight)
    let x = margin
    lines.forEach((list, index) => {
      ctx.font = `${index === 1 ? 'bold ' : ''}21px "Microsoft YaHei", sans-serif`; ctx.fillStyle = '#000000'
      list.forEach((line, n) => ctx.fillText(line, x + 12, y + 29 + n * 28))
      if (index === 0) { ctx.font = '18px "Microsoft YaHei", sans-serif'; formula.forEach((line, n) => ctx.fillText(line, x + 12, y + 29 + list.length * 28 + 4 + n * 25)) }
      x += columns[index]
    })
    y += rowHeight
  }
  await finishPage()
  return jpegPagesToPdf(pages, width, height)
}

export function jpegPagesToPdf(pages: Uint8Array[], width: number, height: number): Blob {
  const pageWidth = 595.28, pageHeight = 841.89
  const objects: Uint8Array[] = [encode('<< /Type /Catalog /Pages 2 0 R >>')]
  objects.push(encode(`<< /Type /Pages /Count ${pages.length} /Kids [${pages.map((_, index) => `${3 + index * 3} 0 R`).join(' ')}] >>`))
  const concat = (parts: Uint8Array[]) => { const all = new Uint8Array(parts.reduce((n, part) => n + part.length, 0)); let offset = 0; for (const part of parts) { all.set(part, offset); offset += part.length } return all }
  pages.forEach((page, index) => {
    const id = 3 + index * 3
    objects.push(encode(`<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageWidth} ${pageHeight}] /Resources << /XObject << /Image ${id + 1} 0 R >> >> /Contents ${id + 2} 0 R >>`))
    objects.push(concat([encode(`<< /Type /XObject /Subtype /Image /Width ${width} /Height ${height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${page.length} >>\nstream\n`), page, encode('\nendstream')]))
    const command = `q ${pageWidth} 0 0 ${pageHeight} 0 0 cm /Image Do Q`
    objects.push(encode(`<< /Length ${encode(command).length} >>\nstream\n${command}\nendstream`))
  })
  const chunks = [encode('%PDF-1.4\n')], offsets = [0]; let position = chunks[0].length
  objects.forEach((object, index) => { const part = concat([encode(`${index + 1} 0 obj\n`), object, encode('\nendobj\n')]); offsets.push(position); chunks.push(part); position += part.length })
  chunks.push(encode(`xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.slice(1).map((offset) => `${String(offset).padStart(10, '0')} 00000 n \n`).join('')}trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${position}\n%%EOF`))
  return new Blob([concat(chunks)], { type: 'application/pdf' })
}

export async function exportMetrics(rows: FinancialMetric[], context: ExportContext, format: 'excel' | 'pdf') {
  const blob = format === 'excel' ? createMetricsExcel(rows, context) : await createMetricsPdf(rows, context)
  const filename = `${context.company}-${context.period}-财务指标${context.demo ? '-演示数据' : ''}`.replace(/[\\/:*?"<>|]/g, '-')
  download(blob, `${filename}.${format === 'excel' ? 'xlsx' : 'pdf'}`)
}
