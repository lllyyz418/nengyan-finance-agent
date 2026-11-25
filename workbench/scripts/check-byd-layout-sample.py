"""Render every page and inspect selectable text and vector PDF construction."""
import json
from pathlib import Path
from pypdf import PdfReader
import pypdfium2 as pdfium
from PIL import Image,ImageDraw,ImageFont
ROOT=Path(__file__).resolve().parents[1]
QA=ROOT/'tmp/pdfs/byd-2025-layout';renders=QA/'renders';renders.mkdir(parents=True,exist_ok=True)
path=ROOT/'output/pdf/比亚迪2025年度研究报告-彩色设计样稿.pdf'
reader=PdfReader(path);assert len(reader.pages)==26
doc=pdfium.PdfDocument(str(path));counts=[]
for index in range(len(reader.pages)):
    page=reader.pages[index];text=page.extract_text() or ''
    assert '�' not in text,(index+1,'replacement character')
    assert len(text)>160,(index+1,len(text))
    counts.append({'page':index+1,'text_chars':len(text),'image_objects':len(page.images)})
    image=doc[index].render(scale=1.45).to_pil().convert('RGB')
    image.save(renders/f'page-{index+1:02d}.png')
doc.close()
assert all(item['image_objects']==0 for item in counts),'Expected vector output, not image-only pages'
thumb_w=220;thumb_h=round(thumb_w*841.89/595.28)
sheet=Image.new('RGB',(thumb_w*5+24*6,(thumb_h+31)*6+24),'#E4EBEF')
d=ImageDraw.Draw(sheet)
for index in range(26):
    im=Image.open(renders/f'page-{index+1:02d}.png');im.thumbnail((thumb_w,thumb_h))
    x=24+(index%5)*(thumb_w+24);y=20+(index//5)*(thumb_h+31)
    sheet.paste(im,(x,y));d.text((x,y+thumb_h+5),f'{index+1:02d}',fill='#16384B')
sheet.save(QA/'contact-sheet.png')
preview=Image.new('RGB',(936,448),'#E4EBEF')
for pos,number in enumerate([1,7,10]):
    im=Image.open(renders/f'page-{number:02d}.png');im.thumbnail((290,410))
    preview.paste(im,(15+pos*310,18))
preview.save(ROOT/'output/pdf/比亚迪2025研究报告-预览.png')
(QA/'pdf-check.json').write_text(json.dumps({'pages':26,'bytes':path.stat().st_size,'selectable_text':True,
    'vector_charts':True,'counts':counts},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pages':26,'body_pages':22,'text_chars':sum(item['text_chars'] for item in counts),
    'vector_pages':26,'bytes':path.stat().st_size},ensure_ascii=False))
