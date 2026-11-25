"""Evidence-first annual R&D and fixed-asset policy extraction.

Only explicitly labelled, ordered report columns become monetary facts. Blank
cells, development-asset balances, and generic policy boilerplate are not zero
capitalisation or evidence of an actual change. Text evidence stays out of the
numeric fact contract.
"""
from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

VERSION = 'annual-policy-1'
RD_LABELS = {
    '研发投入金额': 'r_and_d_total_investment',
    '研发投入合计': 'r_and_d_total_investment',
    '研发投入总额': 'r_and_d_total_investment',
    '资本化研发投入': 'r_and_d_capitalized_expenditure',
    '资本化研发投入金额': 'r_and_d_capitalized_expenditure',
    '研发投入资本化的金额': 'r_and_d_capitalized_expenditure',
    '研发资本化金额': 'r_and_d_capitalized_expenditure',
}
_NUMBER = re.compile(r'[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?')


def compact(value: Any) -> str:
    return re.sub(r'\s+', '', str(value or '')).replace('（', '(').replace('）', ')')


def _label(value: Any) -> str:
    return re.sub(r'\((?:人民币)?(?:千元|万元|亿元|元)\)', '', compact(value))


def _source(doc_id: str, number: int, text: str, bbox=None, section='研发投入') -> dict:
    return {'document_id': doc_id, 'page_start': number, 'page_end': number,
            'section': section, 'raw_text': text, 'row_bbox': list(bbox) if bbox else None,
            'extractor': VERSION}


def _ordered(text: str, year: int) -> bool:
    flat = compact(text)
    years = re.findall(r'(20\d{2})年', flat)
    if years:
        return years[:2] == [str(year), str(year-1)]
    for left, right in [('本年度', '上年度'), ('本年', '上年')]:
        if left in flat and right in flat:
            return flat.index(left) < flat.index(right)
    return False


def _unit(label: str, context: str) -> str | None:
    explicit = re.search(r'\((?:人民币)?(千元|万元|亿元|元)\)', compact(label))
    if explicit:
        return explicit[1]
    units = re.findall(r'单位[:：](?:人民币)?(千元|万元|亿元|元)', compact(context))
    return units[-1] if units else None


def _depreciation_grid(grid: list) -> list:
    # Word-generated PDFs split one merged visual column into several grid
    # fragments. Collapse only a complete five-field depreciation layout;
    # never do this for monetary tables, where blanks have meaning.
    if grid and any('折旧方法' in compact(c) for c in grid[0]):
        header=[c for c in grid[0] if compact(c)]
        collapsed=[[c for c in row if compact(c)] for row in grid]
        if len(header)==5 and all(len(row)==5 for row in collapsed):
            return collapsed
    return grid


def _report_grid(table: dict, year: int) -> list:
    """Map merged cells by visual header centres, preserving empty columns."""
    grid=table['rows']; boxes=table.get('row_cells')
    if not boxes:
        return grid
    header_index=next((i for i,row in enumerate(grid) if _ordered(' '.join(str(c or '') for c in row),year)),None)
    if header_index is None:
        return grid
    header=grid[header_index]
    columns=[j for j,c in enumerate(header) if compact(c)]
    if len(columns)<3 or not all(boxes[header_index][j] for j in columns):
        return grid
    centres=[(boxes[header_index][j][0]+boxes[header_index][j][2])/2 for j in columns]
    # Some exchanges leave the label header blank. It is still a real
    # column, not a reason to shift the two year columns to the left.
    if re.fullmatch(r'(?:20\d{2}年|本年度|本年)',compact(header[columns[0]])):
        label_box=next((boxes[i][j] for i,row in enumerate(grid) for j,c in enumerate(row)
                        if _label(c) in RD_LABELS and boxes[i][j]),None)
        if not label_box or label_box[2]>centres[0]:
            return grid
        centres.insert(0,(label_box[0]+label_box[2])/2)
    mapped=[]
    for i,row in enumerate(grid):
        cells=[]
        for centre in centres:
            matches=[j for j,bbox in enumerate(boxes[i]) if bbox and bbox[0]<centre<bbox[2]]
            if len(matches)!=1:
                return grid
            cells.append(row[matches[0]])
        mapped.append(cells)
    return mapped


def extract_policy_pages(pages: list[dict], doc_id: str, year: int) -> tuple[list[dict], dict]:
    """Pure extraction over relevant page lines/grids; also used by fixtures."""
    rows, evidence, candidates, missing, depreciation = [], [], [], [], []
    depreciation_unverified=[]
    estimate_disclosures = []

    def add_evidence(source: dict, label: str) -> str:
        eid = f'{doc_id}:policy:{len(evidence)+1}'
        evidence.append({'evidence_id': eid, 'label': label, 'source': source})
        return eid

    for page in pages:
        number, text = page['page'], page['text']
        lines = page.get('lines') or [{'text': line} for line in text.splitlines()]
        # Company/group disclosure only: do not import a parent-company table.
        is_parent = any('母公司' in compact(line['text']) for line in lines[:5])
        tables = page.get('tables') or []
        accepted_codes = set()
        for table in tables:
            grid = _report_grid(table,year)
            # Even an unreadable grid must block flattened-line fallback for
            # its rows, because a blank column could otherwise disappear.
            located={RD_LABELS[_label(c)] for row in table['rows'] for c in row if _label(c) in RD_LABELS}
            accepted_codes.update(located)
            handled_codes=set()
            for i, cells in enumerate(grid):
                if not cells or is_parent:
                    continue
                code = RD_LABELS.get(_label(cells[0]))
                if not code or len(cells) < 3:
                    continue
                # Header must be in this table, not an unrelated page header.
                headers = grid[:i]
                header = next((h for h in reversed(headers) if len(h) >= 3 and _ordered(' '.join(str(c or '') for c in h), year)), None)
                if header is None:
                    continue
                # The first two data columns themselves must identify current/prior.
                if not _ordered(' '.join(str(c or '') for c in header[1:3]), year):
                    continue
                source = _source(doc_id, number, '\n'.join(' | '.join(str(c or '') for c in r) for r in [header, cells]),
                                 table.get('row_bboxes', [None]*len(grid))[i])
                eid = add_evidence(source, str(cells[0]))
                candidates.append({'code': code, 'evidence_id': eid})
                handled_codes.add(code)
                values = [compact(c).replace('−','-') for c in cells[1:3]]
                unit = _unit(str(cells[0]), text[:text.find(str(cells[0]))] if str(cells[0]) in text else text[:300])
                valid_values=[value if _NUMBER.fullmatch(value) else None for value in values]
                if unit and any(v is not None for v in valid_values):
                    rows.append({'kind': 'note', 'metric_code': code, 'label': str(cells[0]), 'values': valid_values,
                                 'unit': unit, 'source': source, 'allow_missing_values':True})
                # A located grid always takes precedence over flattened text,
                # even if a cell was blank. Never slide a change column left.
                accepted_codes.add(code)
                for offset, value in enumerate(values):
                    if not value or not _NUMBER.fullmatch(value) or not unit:
                        missing.append({'period': str(year-offset), 'code': code,
                                        'reason': 'POLICY_UNIT_UNCONFIRMED' if not unit else 'POLICY_CELL_EMPTY_OR_UNREADABLE',
                                        'evidence_id': eid})
            if located and not is_parent:
                for code in sorted(located-handled_codes):
                    source=_source(doc_id,number,'\n'.join(' | '.join(str(c or '') for c in r) for r in table['rows']),table.get('bbox'))
                    eid=add_evidence(source,'研发投入表列待确认');candidates.append({'code':code,'evidence_id':eid})
                    missing.extend({'period':str(period),'code':code,'reason':'POLICY_COLUMNS_UNCONFIRMED','evidence_id':eid} for period in (year,year-1))
        # Gridless but unambiguous one-line summary tables (e.g. CATL).
        for i, line in enumerate(lines):
            raw = line['text']
            match = re.match(r'^\s*(研发投入金额|研发投入合计|研发投入总额|资本化研发投入金额|资本化研发投入|研发投入资本化的金额|研发资本化金额)([（(](?:人民币)?(?:千元|万元|亿元|元)[）)])?\s+(.+)$', raw)
            if not match or is_parent:
                continue
            code = RD_LABELS[match[1]]
            if code in accepted_codes:
                continue
            prior_lines = lines[max(0, i-5):i]
            if not any(_ordered(item['text'], year) for item in prior_lines):
                continue
            tokens = match[3].split()
            if len(tokens) < 2 or not all(_NUMBER.fullmatch(v) for v in tokens[:2]):
                continue
            # A percentage row or ambiguous flattened/missing column is rejected.
            unit = _unit(match[1]+(match[2] or ''), '\n'.join(item['text'] for item in lines[max(0,i-12):i]))
            if not unit:
                continue
            source = _source(doc_id, number, '\n'.join(item['text'] for item in [*prior_lines, line]), line.get('bbox'))
            eid = add_evidence(source, match[1]); candidates.append({'code': code, 'evidence_id': eid})
            rows.append({'kind':'note', 'metric_code':code, 'label':match[1], 'values':tokens[:2], 'unit':unit, 'source':source})

        # Extract fixed-asset depreciation grids, not right-of-use/amortisation.
        for table in tables:
            grid = _depreciation_grid(table['rows'])
            header_index = next((i for i,r in enumerate(grid) if r and '折旧方法' in compact(''.join(str(c or '') for c in r)) and '残值率' in compact(''.join(str(c or '') for c in r))), None)
            if header_index is None:
                continue
            candidate_source=_source(doc_id,number,'\n'.join(' | '.join(str(c or '') for c in r) for r in grid),table.get('bbox'), '固定资产折旧政策')
            header = [compact(c) for c in grid[header_index]]
            if len(header) != 5 or not ('类别' in header[0] or '资产' in header[0]):
                continue
            if not ('折旧方法' in header[1] and '年限' in header[2] and '残值' in header[3] and '折旧率' in header[4]):
                continue
            bbox = table.get('bbox')
            above = '\n'.join(l['text'] for l in lines if not bbox or l.get('top',0) < bbox[1])
            # Nearest asset heading must be fixed assets; generic method tables
            # or a page containing only leased/right-of-use assets are not enough.
            headings = [m[1] for raw in above.splitlines() if (m:=re.fullmatch(r'(?:\d+[、.．])?(使用权资产|无形资产|固定资产|投资性房地产)',compact(raw)))]
            if not headings or headings[-1] != '固定资产' or is_parent:
                continue
            candidate_id=add_evidence(candidate_source,'固定资产折旧原表')
            structured, ids = [], []
            valid = True
            for i, cells in enumerate(grid[header_index+1:], header_index+1):
                vals = [compact(c) for c in cells]
                if len(vals) != 5 or not all(vals):
                    valid = False; break
                nondepreciable=vals[1:] == ['不适用','永久','不适用','不适用']
                if not nondepreciable and vals[1] not in {'年限平均法','直线法','工作量法','双倍余额递减法','年数总和法'}:
                    valid = False; break
                numeric_pattern=r'\d+(?:\.\d+)?(?:[、,，~～至—–－\-]\d+(?:\.\d+)?)*[%％]?'
                if not nondepreciable and not all(re.fullmatch(numeric_pattern, value) for value in vals[2:]):
                    valid = False; break
                if not nondepreciable:
                    life=[Decimal(n) for n in re.findall(r'\d+(?:\.\d+)?',vals[2])]
                    rates=[Decimal(n) for value in vals[3:] for n in re.findall(r'\d+(?:\.\d+)?',value)]
                    if min(life)<=0 or max(rates)>100:
                        valid=False;break
                source = _source(doc_id, number, ' | '.join(str(c or '') for c in cells),
                                 table.get('row_bboxes',[None]*len(grid))[i], '固定资产折旧政策')
                ids.append(add_evidence(source, vals[0]+'折旧参数'))
                structured.append(dict(zip(('category','method','useful_life','residual_rate','annual_rate'),vals)))
            if valid and structured:
                depreciation.append({'rows':structured,'evidence_ids':[candidate_id,*ids]})
            else:
                depreciation_unverified.append(candidate_id)

        # A checkbox is evidence only in the narrowly scoped actual-change
        # subsection. "每年复核、如有变化则调整" boilerplate never qualifies.
        if is_parent:
            continue
        for i,line in enumerate(lines):
            if not re.fullmatch(r'(?:[（(]?\d+[）)]?[.、．]?)?重要会计估计(?:的)?变更', compact(line['text'])):
                continue
            block = []
            for following in lines[i+1:i+16]:
                flat = compact(following['text'])
                if re.match(r'^(?:[一二三四五六七八九十]+[、．]|\d+[、．]|[（(]\d+[）)])',flat) and block:
                    break
                block.append(following['text'])
            excerpt = '\n'.join(block)
            flat = compact(excerpt)
            state = 'none' if re.match(r'□适用[√✓☑\uf052]不适用',flat) else (
                'changed' if re.match(r'[√✓☑\uf052]适用□不适用',flat) and re.search(r'折旧|残值|固定资产.{0,12}(?:年限|寿命)',flat) else 'uncertain')
            if state == 'uncertain' and not re.search(r'折旧|残值|固定资产',flat):
                continue
            source = _source(doc_id, number, line['text']+'\n'+excerpt, line.get('bbox'), '重要会计估计变更')
            eid = add_evidence(source,'会计估计变更披露')
            estimate_disclosures.append({'state':state,'evidence_id':eid})

    return rows, {'version':VERSION,'evidence':evidence,'rd_candidates':candidates,'missing_inputs':missing,
                  'depreciation_tables':depreciation,'depreciation_unverified':depreciation_unverified,'estimate_disclosures':estimate_disclosures}


def parse_annual_policies(pdf: Any, doc_id: str, year: int) -> tuple[list[dict],dict]:
    """Keep only candidate pages; release pdfplumber's per-page caches."""
    pages = []
    for index,page in enumerate(pdf.pages):
        from .parse_runtime import report_progress
        report_progress('核验会计政策披露', index+1, len(pdf.pages))
        from .parse_runtime import cached_page_text
        text = cached_page_text(page)
        if re.search(r'研发投入(?:金额|合计|总额)|资本化研发投入|研发投入资本化的金额|研发资本化金额|折旧方法|重要会计估计(?:的)?变更', compact(text)):
            lines = page.extract_text_lines()
            lines = [{**line,'bbox':[line[k] for k in ('x0','top','x1','bottom')]} for line in lines]
            tables = [{'rows':table.extract(), 'bbox':list(table.bbox),
                       'row_cells':[row.cells for row in table.rows],
                       'row_bboxes':[list(row.bbox) for row in table.rows]} for table in page.find_tables()]
            pages.append({'page':index+1,'text':text,'lines':lines,'tables':tables})
            # Depreciation tables commonly continue immediately on the next page.
            if '折旧年限' in compact(text) and index+1 < len(pdf.pages):
                nxt = pdf.pages[index+1]
                nt = cached_page_text(nxt)
                # Join only an exact five-column, aligned continuation grid.
                for original in tables:
                    normalized = _depreciation_grid(original['rows'])
                    if not normalized or len(normalized[0]) != 5 or not any('折旧方法' in compact(c) for c in normalized[0]):
                        continue
                    if original['bbox'][3] < float(page.height)-120:
                        continue
                    for continuation in nxt.find_tables():
                        if continuation.bbox[1] > 100 or abs(continuation.bbox[0]-original['bbox'][0])>2 or abs(continuation.bbox[2]-original['bbox'][2])>2:
                            continue
                        grid = continuation.extract()
                        if grid and all(len(r)==5 for r in grid):
                            # Retain the source page per row for evidence anchors.
                            original.setdefault('continuation',[]).append({'page':index+2,'rows':grid,'row_bboxes':[list(r.bbox) for r in continuation.rows]})
                nxt.close()
        page.close()
    # Do not flatten continuation anchors into the wrong PDF page. Keep the
    # heading/header context on a synthetic next-page candidate instead.
    for page in list(pages):
        for table in page['tables']:
            for continuation in table.get('continuation',[]):
                header = next((r for r in _depreciation_grid(table['rows']) if '折旧方法' in ''.join(str(c or '') for c in r)),None)
                if header:
                    pages.append({'page':continuation['page'],'text':'固定资产\n', 'lines':[{'text':'固定资产'}],
                                  'tables':[{'rows':[header,*continuation['rows']], 'row_bboxes':[None,*continuation['row_bboxes']]}]})
    return extract_policy_pages(pages,doc_id,year)
