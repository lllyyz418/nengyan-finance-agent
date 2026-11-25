"""Render the actual browser-exported PDF for visual acceptance."""
import json
from pathlib import Path
import pdfplumber
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1]
folder=ROOT/'artifacts/research-templates';path=folder/'research-export-acceptance.pdf'
reader=PdfReader(path);count=len(reader.pages)
assert count>2,'Only visible report text may have been exported'
with pdfplumber.open(path) as pdf:
    for index in sorted({0,count//2,count-1}):
        pdf.pages[index].to_image(resolution=85).save(folder/f'research-export-page-{index+1}.png')
(folder/'export-acceptance.json').write_text(json.dumps({'pages':count,'bytes':path.stat().st_size,
    'image_pages':sum(bool(page.images) for page in reader.pages)},indent=2),encoding='utf-8')
print('Export pages:',count,'bytes:',path.stat().st_size)
