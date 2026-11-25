"""Read-only policy-source inspection; no production records are written."""
import re, sys
from pathlib import Path
import pypdfium2 as pdfium

root = Path(__file__).resolve().parents[1]
paths = [root / 'backend/data/report_cache' / name for name in (
    '300750_2025_1225002214.pdf', '688032_2025_1225266006.pdf')]
if len(sys.argv)>1:
    paths=[Path(name) for name in sys.argv[1:]]
for path in paths:
    if not path.exists():
        continue
    print('\nFILE', path.name, flush=True)
    with pdfium.PdfDocument(path) as pdf:
        for index in range(len(pdf)):
            page = pdf[index]
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            lines = text.splitlines()
            hits = [i for i,line in enumerate(lines) if re.search(
                r'资本化研发|研发投入金额|研发投入合计|研发投入总额|折旧方法|重要会计估计变更|重要会计政策和会计估计', line)]
            if hits:
                print('PAGE', index+1)
                for i in hits:
                    print('\n'.join(lines[max(0,i-2):i+12]))
            textpage.close(); page.close()
