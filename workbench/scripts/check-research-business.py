"""Bounded, offline prose acceptance against the existing public CATL PDF."""
import json
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from pdf_runtime import extract
from research_excerpts import VERSION
folder=ROOT/'artifacts/research-templates';folder.mkdir(parents=True,exist_ok=True)
started=time.monotonic()
result=extract('research_business',ROOT/'backend/data/report_cache/300750_2025_1225002214.pdf',folder/'source-cache',
    version=VERSION,max_seconds=100,idle_seconds=45)
(folder/'issuer-business.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'elapsed':round(time.monotonic()-started,2),**result},ensure_ascii=False),flush=True)
