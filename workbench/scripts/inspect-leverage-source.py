"""Read/render only balance-sheet source pages used by local QA."""
import json
import re
from pathlib import Path
import pdfplumber
import pypdfium2 as pdfium

PROJECT=Path(__file__).resolve().parents[1]
output=PROJECT/'artifacts'/'leverage-qa'
bundle=json.loads((output/'local-verification.json').read_text(encoding='utf-8'))
for source in bundle['sources']:
    path=Path(source['path'])
    page_number=None
    if source['year']==2023:
        with pdfplumber.open(path) as pdf:
            for index,page in enumerate(pdf.pages):
                lines=(page.extract_text() or '').splitlines()
                if any(re.fullmatch(r'(?:\d+[、.．])?合并资产负债表',re.sub(r'\s+','',line)) for line in lines):
                    page_number=index+1
                    print(f"{source['year']}: page {page_number}",flush=True)
                    print('\n'.join(lines[:28]),flush=True)
                    break
                page.close()
    else:
        fact=next((item for item in source['facts'] if item['metric_code']=='total_liabilities'),None)
        if fact:
            page_number=fact['sources'][0]['page_start']
    fact=next((item for item in source['facts'] if item['metric_code']=='total_liabilities'),None)
    pages={page_number, fact['sources'][0]['page_start'] if fact else None}-{None}
    for page_number in sorted(pages):
        with pdfium.PdfDocument(path) as pdf:
            bitmap=pdf[page_number-1].render(scale=1.4)
            bitmap.to_pil().save(output/f"balance-sheet-{source['year']}-p{page_number}.png")
        print(f"Rendered {source['year']} source page.",flush=True)
