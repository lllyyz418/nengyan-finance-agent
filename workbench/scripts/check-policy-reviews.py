"""Read-only annual source acceptance; writes only isolated QA artifacts."""
import json,sys,hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import app as core
from financial_engine.pdf_adapter import parse_pdf
from financial_engine.core import analyze_parsed_documents
OUT=ROOT/'artifacts/policy-reviews';OUT.mkdir(parents=True,exist_ok=True)
for code,name in [('300750','300750_2025_1225002214.pdf'),('688032','688032_2025_1225266006.pdf')]:
    path=ROOT/'backend/data/report_cache'/name
    if not path.exists():continue
    print('Parsing',name,flush=True)
    if '--refresh-policy' in sys.argv and (OUT/f'{code}-parsed.json').exists():
        import pdfplumber
        from financial_engine.policy_adapter import parse_annual_policies
        parsed=json.loads((OUT/f'{code}-parsed.json').read_text(encoding='utf8'))
        with pdfplumber.open(path) as pdf:
            policy_rows,policy=parse_annual_policies(pdf,parsed['document_id'],parsed['report_year'])
        parsed['rows']=[r for r in parsed['rows'] if r.get('metric_code') not in {'r_and_d_total_investment','r_and_d_capitalized_expenditure'}]+policy_rows
        parsed['policy_review']=policy
    else:
        parsed=parse_pdf(path)
    parsed['security_code']=code
    (OUT/f'{code}-parsed.json').write_text(json.dumps(parsed,ensure_ascii=False,indent=2),encoding='utf8')
    analysis=analyze_parsed_documents([parsed]);analysis['warnings'].extend(parsed.get('parser_warnings',[]))
    payload={'retrieval':{'files':[{'role':'target','status':'accepted','document_id':parsed['document_id'],'local_path':str(path)}]}}
    if '--with-prior' in sys.argv:
        import pdfplumber
        from financial_engine.policy_adapter import parse_annual_policies
        from financial_engine.policy_review import refresh_policy_reviews
        from financial_engine.pdf_adapter import _pdf_metadata
        from financial_engine.mining import _pdf_security_identity
        prior_path=ROOT/'backend/data/report_cache'/('300750_2024_1222806982.pdf' if code=='300750' else '688032_2024_1223417404.pdf')
        identity=_pdf_security_identity(prior_path)
        assert identity.get('stock_code')==code,identity
        sha=hashlib.sha256(prior_path.read_bytes()).hexdigest()
        with pdfplumber.open(prior_path) as pdf:
            company,year=_pdf_metadata(pdf.pages[0].extract_text() or '')
            assert year==2024 and company==parsed['company_name'],(year,company)
            prows,policy=parse_annual_policies(pdf,sha[:16],year)
            prior={'document_id':sha[:16],'security_code':code,'report_year':year,'report_type':'annual','company_name':company,
                   'file_name':prior_path.name,'file_sha256':sha,'policy_review':policy,'rows':prows}
        (OUT/f'{code}-prior-parsed.json').write_text(json.dumps(prior,ensure_ascii=False,indent=2),encoding='utf8')
        refresh_policy_reviews(analysis,[*analysis['documents'],prior])
        analysis['support_documents']=[{k:v for k,v in prior.items() if k!='rows'}]
        payload['history']={'analysis':{'documents':[prior],'facts':[]}}
        payload['retrieval']['files'].append({'role':'history','status':'accepted','document_id':prior['document_id'],'local_path':str(prior_path)})
    result={'engine_version':'policy-qa','analysis':analysis,'payload':payload,'evidence':core.evidence_index(analysis),
            'report_markdown':core.deterministic_report(analysis),'report_generator':'deterministic_template'}
    (OUT/f'{code}-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    for risk in analysis['risk_assessments'][-2:]:
        print(risk['rule_id'],risk['status'],risk['summary'],flush=True)
        print(json.dumps(risk['review_details'],ensure_ascii=False),flush=True)
    print('Source',path,flush=True)
