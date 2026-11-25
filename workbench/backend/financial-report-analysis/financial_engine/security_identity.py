"""Identify the report issuer, not every listed company mentioned in a PDF."""
import re

IDENTITY_VERSION = 'issuer-context-1'
CODE = re.compile(r'(?:证券代码|股票代码|公司代码|A股代码)(?:[（(]A股[）)])?[：:]?(?:为)?(\d{6})(?!\d)')
HEADING_PREFIX = r'(?:(?:第[一二三四五六七八九十百\d]+[章节])|(?:[一二三四五六七八九十\d]+[、.．]))?'
GLOSSARY = re.compile(HEADING_PREFIX + r'(?:释义|名词解释|常用词语释义|定义)')
PROFILE = re.compile(HEADING_PREFIX + r'(?:公司简介(?:和主要财务指标)?|公司基本情况|公司基本信息|公司信息|公司股票简况|公司证券基本情况|股票简况|股票及证券信息)')
OTHER_CHAPTER = re.compile(r'第[一二三四五六七八九十百\d]+[章节][\u4e00-\u9fff]{2,40}')


def identify_security(pages):
    """Consume (physical page, extracted text), retaining accepted/ignored evidence.

    No requested code is supplied here: genuine conflicts between issuer fields
    must remain ambiguous, even when one code matches the caller's selection.
    """
    sources, ignored = [], []
    section = 'unknown'
    for number, text in pages:
        pending_table = 0
        pending_label = False
        for line in text.splitlines():
            flat = re.sub(r'\s+', '', line)
            if not flat:
                continue
            if GLOSSARY.fullmatch(flat):
                section = 'glossary'
            elif PROFILE.fullmatch(flat):
                section = 'profile'
            elif OTHER_CHAPTER.fullmatch(flat):
                section = 'other'
            reference = section == 'glossary' or bool(re.search(r'指.*(?:股票|证券|公司)代码|(?:子公司|参股公司|联营企业|关联方|标的公司|该公司).*代码', flat))
            own_field = bool(re.match(r'(?:(?:本公司|本集团)(?:的)?)?(?:(?:A股)?(?:股票|证券|公司)代码|A股代码|(?:A股)?(?:股票|证券|公司)简称)', flat))
            explicit_issuer = bool(re.search(r'本公司(?:的)?(?:股票|证券)代码', flat))
            for match in CODE.finditer(flat):
                entry = {'stock_code':match[1], 'page':number, 'text':line}
                if not reference and (own_field or explicit_issuer):
                    sources.append({**entry,'context':'company_profile' if section == 'profile' else 'issuer_field'})
                else:
                    ignored.append({**entry,'reason':'glossary_reference' if section == 'glossary' else 'other_company_reference'})

            # A real stock-summary table often separates its header and value.
            # Restrict row interpretation to issuer context and a short window;
            # six-digit amounts, postcodes and H/B-share codes are not evidence.
            in_issuer_context = not reference and (section == 'profile' or number <= 2)
            if pending_label and in_issuer_context and re.fullmatch(r'\d{6}',flat):
                sources.append({'stock_code':flat,'page':number,'text':line,'context':'issuer_field'})
            pending_label = in_issuer_context and bool(re.fullmatch(r'(?:股票|证券|公司)代码[：:]?',flat))
            if in_issuer_context and re.search(r'(?:股票|证券)代码',flat) and re.search(r'(?:股票|证券)简称',flat) and not CODE.search(flat):
                pending_table = 4
            elif pending_table:
                pending_table -= 1
                if in_issuer_context and re.search(r'A股|人民币普通股|上海证券交易所|深圳证券交易所|北京证券交易所',flat) and not re.search(r'H股|B股|香港|优先股|债券',flat):
                    for code in re.findall(r'(?<!\d)\d{6}(?!\d)',flat):
                        sources.append({'stock_code':code,'page':number,'text':line,'context':'issuer_stock_table'})
    sources = list({(item['stock_code'],item['page'],item['text']):item for item in sources}.values())
    codes = {item['stock_code'] for item in sources}
    return {'stock_code':next(iter(codes)) if len(codes)==1 else None,
            'status':'pdf_code_reported' if len(codes)==1 else 'ambiguous' if codes else 'absent',
            'sources':sources,'ignored_sources':ignored,'identity_version':IDENTITY_VERSION}
