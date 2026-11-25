import { jpegPagesToPdf } from './metricExport'

/** Render the complete report, independently of the on-screen scroll viewport. */
export async function createReportPdf(root: HTMLElement): Promise<Blob> {
  await document.fonts.ready
  const width = 1190, height = 1684, margin = 84, bottom = height - 105
  const canvas = document.createElement('canvas')
  canvas.width = width; canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('当前浏览器无法生成 PDF，请换用桌面浏览器重试')
  const pages: Uint8Array[] = []
  let y = 150, page = 0
  const font = (size = 25, bold = false) => {
    ctx.font = `${bold ? 'bold ' : ''}${size}px "Microsoft YaHei", sans-serif`
    ctx.fillStyle = '#000'; ctx.textBaseline = 'top'
  }
  const wrap = (value: string, space: number) => {
    const lines: string[] = []; let line = ''
    for (const char of value) {
      if (char === '\n' || (line && ctx.measureText(line + char).width > space)) {
        lines.push(line); line = char === '\n' ? '' : char
      } else line += char
    }
    if (line) lines.push(line)
    return lines.length ? lines : ['']
  }
  const begin = () => {
    page++; ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, width, height)
    font(19); ctx.fillText('能研财智 | 财务分析报告', margin, 48)
    ctx.strokeStyle = '#000'; ctx.lineWidth = 1
    ctx.beginPath(); ctx.moveTo(margin, 87); ctx.lineTo(width - margin, 87); ctx.stroke()
    y = 116
  }
  const finish = async () => {
    font(18); ctx.fillText(`第 ${page} 页`, width - margin - 100, height - 63)
    const image = await new Promise<Blob>((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('报告页面生成失败')), 'image/jpeg', .96))
    pages.push(new Uint8Array(await image.arrayBuffer()))
  }
  const next = async () => { await finish(); begin() }
  const text = async (value: string, size = 25, bold = false, inset = 0) => {
    font(size, bold)
    const lines = wrap(value, width - margin * 2 - inset)
    for (const line of lines) {
      if (y + size * 1.55 > bottom) { await next(); font(size, bold) }
      ctx.fillText(line, margin + inset, y); y += size * 1.55
    }
  }
  const table = async (element: HTMLTableElement) => {
    const rows = Array.from(element.rows)
    if (!rows.length) return
    const columns = Math.max(...rows.map(row => row.cells.length))
    const colWidth = (width - margin * 2) / columns
    const header = rows[0].parentElement?.tagName === 'THEAD' ? rows[0] : undefined
    const drawRow = async (row: HTMLTableRowElement, repeat = false) => {
      const isHeader = row === header
      font(21, isHeader)
      const cells = Array.from({ length: columns }, (_, i) => wrap(row.cells[i]?.textContent?.trim() || '—', colWidth - 24))
      let offset = 0
      const count = Math.max(...cells.map(cell => cell.length))
      while (offset < count) {
        if (bottom - y < 58) {
          await next()
          if (header && !isHeader && !repeat) await drawRow(header, true)
        }
        let capacity = Math.max(1, Math.floor((bottom - y - 22) / 31))
        // Keep ordinary rows together; split only rows taller than one page.
        if (offset === 0 && count > capacity && count * 31 + 22 < bottom - 200 && y > 220) {
          await next()
          if (header && !isHeader && !repeat) await drawRow(header, true)
          capacity = Math.max(1, Math.floor((bottom - y - 22) / 31))
        }
        const lines = Math.min(capacity, count - offset), rowHeight = lines * 31 + 22
        font(21, isHeader); ctx.strokeStyle = '#000'; ctx.lineWidth = 1
        cells.forEach((cell, i) => {
          const x = margin + i * colWidth
          ctx.strokeRect(x, y, colWidth, rowHeight)
          cell.slice(offset, offset + lines).forEach((line, j) => ctx.fillText(line, x + 12, y + 11 + j * 31))
        })
        y += rowHeight; offset += lines
      }
    }
    y += 8
    for (const row of rows) await drawRow(row)
    y += 24
  }
  const block = async (element: Element) => {
    const tag = element.tagName, value = element.textContent?.trim() || ''
    if (!value) return
    if(element.hasAttribute('data-report-export-skip'))return
    if (tag === 'TABLE') { await table(element as HTMLTableElement); return }
    if (tag === 'DIV'||tag==='DETAILS'||tag==='SECTION') { for (const child of Array.from(element.children)) if(child.tagName!=='SUMMARY') await block(child); return }
    if (/^H[1-6]$/.test(tag)) {
      const size = tag === 'H1' ? 36 : tag === 'H2' ? 31 : 27
      if (y + 150 > bottom) await next()
      y += 17; await text(value, size, true); y += 14; return
    }
    if (tag === 'UL' || tag === 'OL') {
      for (const [i, child] of Array.from(element.children).entries()) { await text(`${tag === 'OL' ? `${i + 1}.` : '•'} ${child.textContent?.trim() || ''}`, 25, false, 14); y += 8 }
      y += 10; return
    }
    await text(value, tag === 'PRE' ? 21 : 25, false, tag === 'BLOCKQUOTE' ? 18 : 0)
    y += 20
  }
  begin()
  for (const element of Array.from(root.children)) await block(element)
  await finish()
  return jpegPagesToPdf(pages, width, height)
}

export async function exportReportPdf(root: HTMLElement, title: string) {
  const blob = await createReportPdf(root)
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url; link.download = `${title.replace(/[\\/:*?"<>|]/g, '-').slice(0, 100)}-分析报告.pdf`
  document.body.append(link); link.click(); link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 10000)
}
