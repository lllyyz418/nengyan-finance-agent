"""Read-only diagnostic of the saved report matching the user's screenshots."""
import json
import re
import sys
from pathlib import Path
import pypdfium2 as pdfium
import pdfplumber

PROJECT=Path(__file__).resolve().parents[1]
saved=json.loads((PROJECT/'backend/data/results/ef059d8ccca24b0ab20b34a4ca06d60f.json').read_text(encoding='utf-8'))
task=json.loads((PROJECT/'backend/data/tasks/ef059d8ccca24b0ab20b34a4ca06d60f.json').read_text(encoding='utf-8'))
path=Path(task['file_path'])
out=PROJECT/'artifacts/risk-diagnostics'
out.mkdir(parents=True,exist_ok=True)
pages={177,178,189,190}
with pdfium.PdfDocument(path) as pdf:
    for index in range(len(pdf)):
        page=pdf[index]
        text=page.get_textpage().get_text_range()
        if any(re.fullmatch(r'(?:[一二三四五六七八九十\d]+[、.．])?合并利润表',re.sub(r'\s+','',line)) for line in text.splitlines()):
            pages.update(range(index+1,min(index+4,len(pdf))+1))
        page.close()
    for page_no in sorted(pages):
        pdf[page_no-1].render(scale=1.4).to_pil().save(out/f'source-p{page_no}.png')
with pdfplumber.open(path) as pdf:
    texts=[]
    for page_no in sorted(pages):
        text=pdf.pages[page_no-1].extract_text() or ''
        texts.append(f'PDF physical page {page_no}\n{text}')
        print(f'Page {page_no}: '+ ' | '.join(line for line in text.splitlines() if any(word in line for word in ('净利润','合并利润表','项目','账龄','账面余额','坏账准备','存货跌价','账面价值'))),flush=True)
    (out/'source-excerpts.txt').write_text('\n\n'.join(texts),encoding='utf-8')
print('Read-only PDF diagnostics saved separately.',flush=True)
sys.path.insert(0,str(PROJECT/'backend/financial-report-analysis'))
from financial_engine.pdf_adapter import parse_pdf
from financial_engine.core import analyze_parsed_documents
parsed=parse_pdf(path)
(out/'parsed-fixture.json').write_text(json.dumps(parsed,ensure_ascii=False,indent=2),encoding='utf-8')
parsed['security_code']='688032'
analysis=analyze_parsed_documents([parsed],{'minimum_negative_cash_quarters':3})
analysis['warnings'].extend(parsed.get('parser_warnings',[]))
from decimal import Decimal
expected={('2025','net_profit'):'-161839438.07',('2024','net_profit'):'343442754.48',
          ('2025','accounts_receivable_gross'):'703781639.47',('2024','accounts_receivable_gross'):'785789505.12',
          ('2025','accounts_receivable_aging_over_3y'):'3191850.50',('2024','accounts_receivable_aging_over_3y'):'4095980.23',
          ('2025','inventory_gross'):'1158639921.14',('2024','inventory_gross'):'970806196.94',
          ('2025','inventory_impairment_allowance'):'31633181.85',('2024','inventory_impairment_allowance'):'30185538.07'}
actual={(fact['period'],fact['metric_code']):fact for fact in analysis['facts']}
for key,value in expected.items():
    assert key in actual, f'Missing independently checked original amount: {key}'
    assert actual[key]['quality']!='conflict' and Decimal(actual[key]['value'])==Decimal(value),key
result={**saved,'analysis':analysis,'payload':{},'analysis_parameters':{'minimum_negative_cash_quarters':3}}
(out/'verified-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(f"Verified {len(expected)} manually checked original amounts; {len(analysis['metrics'])} metrics; {len(analysis['unavailable_metrics'])} unavailable.",flush=True)
print([(m['metric_code'],m['reason_code']) for m in analysis['unavailable_metrics']],flush=True)
