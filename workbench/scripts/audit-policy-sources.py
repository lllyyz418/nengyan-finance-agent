"""Independent read-only monetary assertions and original-page render QA."""
import json,sys
from decimal import Decimal
from pathlib import Path
import pdfplumber,pypdfium2 as pdfium
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend/financial-report-analysis'))
from financial_engine.policy_adapter import extract_policy_pages
from financial_engine.core import analyze_parsed_documents
OUT=ROOT/'artifacts/policy-reviews';cache=ROOT/'backend/data/report_cache'
path=cache/'002594_2025_1225045351.pdf'
with pdfplumber.open(path) as pdf:
    page=pdf.pages[38]
    data={'page':39,'text':page.extract_text(),'lines':page.extract_text_lines(),
          'tables':[{'rows':t.extract(),'bbox':t.bbox,'row_cells':[r.cells for r in t.rows],'row_bboxes':[r.bbox for r in t.rows]} for t in page.find_tables()]}
    rows,policy=extract_policy_pages([data],'byd-page-audit',2025)
result=analyze_parsed_documents([{'document_id':'byd-page-audit','company_name':'比亚迪股份有限公司（仅研发原页核验）',
    'security_code':'002594','report_type':'annual','report_year':2025,'rows':rows,'policy_review':policy}])
actual={(f['period'],f['metric_code']):Decimal(f['value']) for f in result['facts']}
expected={('2025','r_and_d_total_investment'):'63441379000',('2024','r_and_d_total_investment'):'54160964000',
          ('2025','r_and_d_capitalized_expenditure'):'5463274000',('2024','r_and_d_capitalized_expenditure'):'966219000'}
assert all(actual.get(key)==Decimal(value) for key,value in expected.items()),actual
rd=next(r for r in result['risk_assessments'] if r['rule_id']=='r_and_d_capitalization_policy')
assert rd['status']=='review_required',rd
observed=rd['conditions'][0]['observed']
assert Decimal(observed['current_percent']).quantize(Decimal('.01'))==Decimal('8.61')
assert Decimal(observed['previous_percent']).quantize(Decimal('.01'))==Decimal('1.78')
assert Decimal(observed['share_change_percentage_points']).quantize(Decimal('.01'))==Decimal('6.83')
(OUT/'byd-rd-original-page-audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
print('BYD original p39: all four amounts and both ratios verified; direction observation, not fraud judgment.',flush=True)
for code,total,previous,count in [('300750','22146581000','18606756000',7),('688032','348872957.28','266114530.11',4)]:
    parsed=json.loads((OUT/f'{code}-parsed.json').read_text(encoding='utf8'))
    facts=analyze_parsed_documents([parsed])['facts']
    values={(f['period'],f['metric_code']):Decimal(f['value']) for f in facts}
    assert values[('2025','r_and_d_total_investment')]==Decimal(total)
    assert values[('2024','r_and_d_total_investment')]==Decimal(previous)
    assert not any(f['metric_code']=='r_and_d_capitalized_expenditure' for f in facts)
    tables=parsed['policy_review']['depreciation_tables']
    assert sum(len(t['rows']) for t in tables)==count,(code,tables)
    for entry in parsed['policy_review']['evidence']:
        assert entry['source']['page_start']>0
print('CATL/Hoymiles: exact R&D totals, withheld blank capitalisation, 7/4 fixed-asset rows verified.',flush=True)
for file,pages in [('002594_2025_1225045351.pdf',[39]),('300750_2025_1225002214.pdf',[146,147,160]),
                   ('688032_2025_1225266006.pdf',[31,161]),('688032_2024_1223417404.pdf',[161])]:
    pdf=pdfium.PdfDocument(str(cache/file))
    for number in pages:
        page=pdf[number-1];bitmap=page.render(scale=1.3);image=bitmap.to_pil()
        image.save(OUT/f'{Path(file).stem}-p{number}.png');image.close();bitmap.close();page.close()
    pdf.close()
print('Original pages rendered for visual review.',flush=True)
