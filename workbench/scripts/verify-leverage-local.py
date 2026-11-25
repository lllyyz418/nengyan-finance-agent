"""Read-only local-PDF verification; writes only isolated QA artifacts."""
import json
import sys
from pathlib import Path

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/'backend'))
import app as core
from financial_engine.core import analyze_parsed_documents
from financial_engine.pdf_adapter import parse_pdf
from financial_engine.history import build_annual_history

output=PROJECT/'artifacts'/'leverage-qa'
output.mkdir(parents=True,exist_ok=True)
library=core.get_report_library()
if library is None:
    raise SystemExit('No local report library; offline unit tests remain available.')
analyses=[]
sources=[]
for year in (2023,2024,2025):
    info=library.provide('300750',year)
    if not info:
        print(f'{year}: local PDF unavailable',flush=True)
        continue
    print(f'{year}: checking local PDF',flush=True)
    parsed=parse_pdf(info['local_path'])
    parsed['security_code']='300750'
    analysis=analyze_parsed_documents([parsed])
    analysis['warnings'].extend(parsed.get('parser_warnings',[]))
    metric=next((item for item in analysis['metrics'] if item['metric_code']=='liabilities_to_assets'),None)
    facts=[item for item in analysis['facts'] if item['period']==str(year) and item['metric_code'] in {'total_assets','total_liabilities'}]
    sources.append({'year':year,'path':info['local_path'],'sha256':info['sha256'],
                    'facts':facts,'metric':metric})
    analyses.append(analysis)
    print(f'{year}: ratio {"verified" if metric else "unavailable"}; source pages '+','.join(str(item['sources'][0]['page_start']) for item in facts),flush=True)
if not analyses:
    raise SystemExit('No local PDFs were available.')
merged={key:[item for analysis in analyses for item in analysis.get(key,[])]
        for key in ('documents','facts','metrics','unavailable_metrics','warnings')}
history={'requested_years':[2023,2024,2025],'analysis':merged,'comparison':build_annual_history(merged,'300750')}
bundle={'analysis':analyses[-1],'history':history,'sources':sources}
(output/'local-verification.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2),encoding='utf-8')
print('Saved isolated local verification.',flush=True)
