"""Read-only real disclosure verification; all outputs isolated under artifacts."""
import sys,json,hashlib
from pathlib import Path
from urllib.request import urlopen,Request
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
sys.path.insert(0,str(ROOT/'backend/financial-report-analysis'))
from financial_engine.interim import analyze_interim_pdf

OUT=ROOT/'artifacts/periodic-reports';OUT.mkdir(parents=True,exist_ok=True)
SOURCES=[('catl-2025-h1','semiannual','https://static.cninfo.com.cn/finalpage/2025-07-31/1224342874.PDF'),
         ('catl-2025-q3','quarterly','https://static.cninfo.com.cn/finalpage/2025-10-21/1224721971.PDF'),
         ('catl-2025-q1','quarterly','https://static.cninfo.com.cn/finalpage/2025-04-15/1223094849.PDF'),
         ('catl-2024-q3','quarterly','https://static.cninfo.com.cn/finalpage/2024-10-19/1221432744.PDF')]
for name,kind,url in SOURCES:
    path=OUT/(name+'.pdf')
    if not path.exists():
        with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0'}),timeout=40) as response:body=response.read(80*1024*1024+1)
        assert body.startswith(b'%PDF-') and len(body)<=80*1024*1024
        path.write_bytes(body)
    result=analyze_interim_pdf(path,kind,security_code='300750')
    (OUT/(name+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(name, 'facts',len(result['facts']),'metrics',len(result['metrics']),flush=True)
    print(json.dumps([(f['period'],f['metric_code'],f['value']) for f in result['facts'] if f['metric_code'] in ('revenue','net_profit','operating_cash_flow','total_assets','total_liabilities')],ensure_ascii=False),flush=True)
