"""Render selected original pages for read-only parser QA."""
from pathlib import Path
import pypdfium2 as pdfium
ROOT=Path(__file__).resolve().parents[1]/'artifacts/periodic-reports'
for name,pages in [('catl-2025-q1',[1,9,10]),('catl-2025-q3',[1,9,11]),('catl-2025-h1',[7])]:
    pdf=pdfium.PdfDocument(str(ROOT/(name+'.pdf')))
    for number in pages:
        page=pdf[number-1];bitmap=page.render(scale=1.1);image=bitmap.to_pil()
        image.save(ROOT/f'{name}-source-p{number}.png');image.close();bitmap.close();page.close()
    pdf.close()
