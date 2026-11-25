"""Read local sources and saved financial results; never run models or change tasks."""
import json
import hashlib
from pathlib import Path
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'tmp/pdfs/byd-2025-layout';OUT.mkdir(parents=True,exist_ok=True)
sources=[]
for year,announcement in [(2023,'1219412018'),(2024,'1222881496'),(2025,'1225045351')]:
    path=ROOT/f'backend/data/report_cache/002594_{year}_{announcement}.pdf'
    meta=json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    assert digest==meta['sha256'],path
    reader=PdfReader(path)
    count=min(len(reader.pages),60 if year==2025 else 18)
    pages=[page.extract_text() or '' for page in reader.pages[:count]]
    (OUT/f'{year}-pages.json').write_text(json.dumps(pages,ensure_ascii=False),encoding='utf-8')
    sources.append({'year':year,'path':str(path),'sha256':digest,'url':meta['source_url'],'pages':len(reader.pages)})
    print('SOURCE',year,'PAGES',len(reader.pages),'READ',count,flush=True)
    if year==2025:
        for i,text in enumerate(pages):
            terms=[key for key in ['主要会计数据和财务指标','主营业务分析','分行业','分地区','研发投入','现金流','销量','资产及负债状况'] if key in text]
            if terms:print('PAGE',i+1,','.join(terms),text[:90].replace('\n',' '),flush=True)
tasks=[]
for path in (ROOT/'backend/data/tasks').glob('*.json'):
    raw=path.read_text(encoding='utf-8')
    if '002594' not in raw:continue
    try:item=json.loads(raw)
    except ValueError:continue
    result=item.get('result') or {};analysis=result.get('analysis') or {}
    docs=analysis.get('documents') or []
    if not docs or docs[0].get('security_code')!='002594':continue
    if item.get('status')!='completed':continue
    tasks.append({'id':item['id'],'created_at':item.get('created_at'),'analysis':analysis,'payload':result.get('payload'),
                  'evidence':result.get('evidence'),'file_path':item.get('file_path')})
    print('SAVED',item['id'],docs[0].get('report_year'),len(analysis.get('metrics') or []),'metrics',flush=True)
(OUT/'material.json').write_text(json.dumps({'sources':sources,'tasks':tasks},ensure_ascii=False),encoding='utf-8')
