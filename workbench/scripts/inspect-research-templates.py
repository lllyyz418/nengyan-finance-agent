"""Read-only study of user-supplied analyst reports; no model transmission."""
import hashlib
import json
from pathlib import Path
from pypdf import PdfReader
import pdfplumber

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts/research-templates';OUT.mkdir(parents=True,exist_ok=True)
BASE=Path('D:/xwechat_files/wxid_1zkfv1ibtnc522_f149/msg/file/2026-10')
NAMES=['H3_AP202605101822112135_1.pdf','H3_AP202603101820459417_1.pdf',
       'H3_AP202408061639151491_1 (1).pdf','rc-2021-winning-report-bi-norwegian-business-school.pdf',
       'H3_AP202408061639151491_1(1).pdf']
manifest=[];seen={}
for index,name in enumerate(NAMES,1):
    path=BASE/name;data=path.read_bytes();digest=hashlib.sha256(data).hexdigest()
    entry={'path':str(path),'sha256':digest,'bytes':len(data)}
    if digest in seen:
        entry['duplicate_of']=seen[digest];manifest.append(entry);continue
    slug=f'template-{index}';seen[digest]=slug
    reader=PdfReader(path);texts=[page.extract_text() or '' for page in reader.pages]
    entry.update(id=slug,pages=len(texts))
    (OUT/f'{slug}.txt').write_text('\n\n'.join(f'=== PAGE {i+1} ===\n{text}' for i,text in enumerate(texts)),encoding='utf-8')
    (OUT/f'{slug}-pages.json').write_text(json.dumps(texts,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(entry,ensure_ascii=False))
    print('\n'.join(f'PAGE {i+1}: '+text[:350].replace('\n',' ') for i,text in enumerate(texts)))
    try:
        with pdfplumber.open(path) as pdf:
            for number in sorted({0,min(2,len(pdf.pages)-1)}):
                pdf.pages[number].to_image(resolution=85).save(OUT/f'{slug}-page-{number+1}.png',format='PNG')
    except Exception as exc:print('Render unavailable: '+type(exc).__name__)
    manifest.append(entry)
(OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
