"""Interim facts, same-period metrics and evidence-linked single-quarter views.

No annualization. Stocks are instants, flows are durations. Missing adjacent
filings never become zeros; derived facts preserve all contributing sources.
"""
from __future__ import annotations
import calendar
import copy
import hashlib
import re
from datetime import date
from decimal import Decimal, localcontext
from pathlib import Path
from .core import parse_amount, metric_code
from .periods import period_spec, period_key, report_label
from .semiannual import (parse_semiannual_pdf, _statement_rows, _STATEMENT_TITLE,
                         _FLOW_FIELDS, _STOCK_FIELDS, _row_unit, _unit)

VERSION = 'interim-periods-1'

def _quarter_summary(page, doc_id, year, quarter):
    rows=[]
    for table_index, table in enumerate(page.find_tables()):
        cells=table.extract()
        if not cells: continue
        width=max(map(len,cells)); mode='flow'; columns={};column_boxes={}
        for index, row in enumerate(cells):
            text=''.join(str(x or '') for x in row)
            if '本报告期末' in text and '上年度末' in text:
                mode='stock'; columns={}
                for col,cell in enumerate(row):
                    value=re.sub(r'\s+','',str(cell or ''))
                    column_boxes[col]=table.rows[index].cells[col]
                    if value=='本报告期末':columns[col]=f'{year}-{quarter*3:02d}-{calendar.monthrange(year,quarter*3)[1]}'
                    if value=='上年度末':columns[col]=f'{year-1}-12-31'
                continue
            if ('本报告期' in text or '年初至报告期末' in text) and not re.search(r'营业收入|净利润|现金流',text):
                for col,cell in enumerate(row):
                    value=re.sub(r'\s+','',str(cell or ''))
                    if any(x in value for x in ('增减','增长','比上','%','％')):continue
                    if value=='本报告期':columns[col]=f'{year}Q{quarter}'
                    if value in ('年初至报告期末','年初到报告期末'):columns[col]=period_key(year,'quarterly',quarter)
                    if value=='上年同期' and quarter==1:columns[col]=f'{year-1}Q1'
                    if col in columns:column_boxes[col]=table.rows[index].cells[col]
                continue
            amount_columns={}
            for header_col,period in columns.items():
                box=column_boxes.get(header_col)
                if not box:continue
                center=(box[0]+box[2])/2
                matched=[col for col,cell in enumerate(table.rows[index].cells) if cell and cell[0]<center<cell[2]]
                if len(matched)==1:amount_columns[matched[0]]=period
            boundary=min(amount_columns) if amount_columns else 1
            label=''.join(str(cell or '') for cell in row[:boundary]).replace('\n','')
            if amount_columns and any(re.fullmatch(r'-?[\d,]+(?:\.\d+)?',str(row[col] or '').strip()) for col in amount_columns if col<len(row)):
                for following in cells[index+1:index+4]:
                    if any(re.fullmatch(r'-?[\d,]+(?:\.\d+)?',str(following[col] or '').strip()) for col in amount_columns if col<len(following)):break
                    continuation=''.join(str(cell or '') for cell in following[:boundary]).replace('\n','')
                    if not continuation or any(term in continuation for term in ('项目','本报告期','上年度末')):break
                    label+=continuation
            code=metric_code(label,'annual')
            if code not in (_STOCK_FIELDS if mode=='stock' else _FLOW_FIELDS):continue
            for col,period in amount_columns.items():
                if col>=len(row):continue
                unit=_row_unit(label,_unit(page.extract_text() or ''))
                if parse_amount(row[col],unit or '') is None:continue
                rows.append({'kind':'summary_stock' if mode=='stock' else 'summary_flow','metric_code':code,'label':label,
                    'periods':[period],'values':[str(row[col])],'unit':unit,'source':{'document_id':doc_id,
                    'page_start':page.page_number,'page_end':page.page_number,'section':'quarterly_summary',
                    'table_index':table_index,'row_bbox':list(table.rows[index].bbox),'extractor':'pdf_table'}})
    return rows

def parse_quarterly_pdf(path):
    import pdfplumber
    path=Path(path).resolve(strict=True)
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    with pdfplumber.open(path) as pdf:
        cover='\n'.join((page.extract_text() or '') for page in pdf.pages[:2])
        title=re.search(r'(20\d{2})\s*年\s*第\s*([一三13])\s*季度报告',cover)
        if not title or '摘要' in title.group(0):raise ValueError('PDF 未识别到完整第一季度或第三季度报告')
        year=int(title[1]);quarter=1 if title[2] in ('一','1') else 3
        if re.search(r'第\s*[一三13]\s*季度报告摘要',cover):raise ValueError('请上传完整季度报告，不能使用摘要')
        company=re.search(r'([\u4e00-\u9fff]{2,50}股份有限公司)',cover)
        rows=[];start=None
        for index,page in enumerate(pdf.pages):
            lines=page.extract_text_lines() or []
            if index<8:rows.extend(_quarter_summary(page,sha[:16],year,quarter))
            if any(_STATEMENT_TITLE.fullmatch(re.sub(r'\s+','',line['text'])) and '合并资产负债表' in line['text'] for line in lines):
                start=index;break
            page.close()
        if start is None:raise ValueError('未找到可校验的合并财务报表；不以母公司报表替代')
        statements,warnings,sections=_statement_rows(pdf,start,sha[:16],year,quarter*3)
        return {'document_id':sha[:16],'file_name':path.name,'file_sha256':sha,'company_name':company[1] if company else None,
                'report_year':year,'report_type':'quarterly','report_quarter':quarter,'currency':'CNY',
                'pdf_pages':len(pdf.pages),'report_title':f'{year}年{report_label("quarterly",quarter)}',
                'parser_version':VERSION,'assurance':'以原文披露为准','sections_found':['quarterly_summary',*sections],
                'parser_warnings':warnings,'rows':[*rows,*statements]}

def parse_interim_pdf(path, kind):
    from .parse_runtime import RUNNER
    runner = RUNNER.get()
    if runner:
        return runner('interim', path, kind=kind)
    return _parse_interim_local(path, kind)


def _parse_interim_local(path, kind):
    return parse_semiannual_pdf(path) if kind in ('semiannual','half_year_report') else parse_quarterly_pdf(path)

def analyze_parsed_interim(parsed, options=None, support=None):
    """Analyze one actual filing, optionally with verified same-company filings."""
    options=options or {}; spec=period_spec(parsed);year=parsed['report_year'];month=spec['month']
    if spec['report_type']=='annual':raise ValueError('年度报告应使用原年度引擎')
    doc={key:copy.deepcopy(value) for key,value in parsed.items() if key!='rows'}
    doc.update(period=spec['period'],period_end=spec['end'],period_basis='cumulative',parser_version=VERSION)
    result={'schema_version':VERSION,'documents':[doc],'facts':[],'metrics':[],'unavailable_metrics':[],
            'comparison_metrics':[],'risk_assessments':[],'findings':[],'warnings':copy.deepcopy(parsed.get('parser_warnings',[])),
            'audit':[],'analysis_options':copy.deepcopy(options),'period_basis':'cumulative'}
    facts={}
    def add(fact):
        key=(fact['period'],fact['metric_code']);old=facts.get(key)
        if old is None:facts[key]=fact;result['facts'].append(fact)
        elif old['value']==fact['value']:
            old['sources'].extend(source for source in fact['sources'] if source not in old['sources'])
        else:
            old['quality']='conflict';old.setdefault('conflicting_values',[]).append(fact)
    prior=period_key(year-1,spec['report_type'],spec['report_quarter'])
    for row in parsed.get('rows',[]):
        stock=row.get('kind') in ('summary_stock','balance_sheet')
        periods=row.get('periods') or ([spec['end'],f'{year-1}-12-31'] if stock else [spec['period'],prior])
        for period,raw in zip(periods,row.get('values',[])):
            value=parse_amount(raw,row.get('unit',''))
            if value is None:continue
            source=copy.deepcopy(row['source']);source.update(raw_value=str(raw),raw_unit=row['unit'],raw_label=row.get('label'))
            end=period if stock else f'{period[:4]}-{month:02d}-{calendar.monthrange(int(period[:4]),month)[1]}'
            if not stock and re.fullmatch(r'\d{4}Q[13]',period):
                q=int(period[-1]);start=f'{period[:4]}-{q*3-2:02d}-01';end=f'{period[:4]}-{q*3:02d}-{calendar.monthrange(int(period[:4]),q*3)[1]}'
            else:start=None if stock else f'{period[:4]}-01-01'
            add({'fact_id':f'{doc["document_id"]}:{period}:{row["metric_code"]}','document_id':doc['document_id'],
                 'company_name':doc.get('company_name'),'metric_code':row['metric_code'],'period':period,
                 'period_type':'instant' if stock else 'duration','period_start':start,'period_end':end,
                 'statement_scope':'consolidated','value':format(value,'f'),'unit':'CNY','raw_label':row.get('label'),
                 'raw_value':str(raw),'raw_unit':row['unit'],'quality':'extracted','sources':[source],
                 'comparative_restated':bool(row.get('restated_comparative') and str(period).startswith(str(year-1)))})
    # Adjacent filings may only supply same-company facts with known periods.
    support_conflict=False
    for analysis in support or []:
        docs=analysis.get('documents',[])
        if len(docs)!=1 or docs[0].get('security_code')!=doc.get('security_code') or not doc.get('security_code'):continue
        for fact in analysis.get('facts',[]):
            if fact.get('quality')=='conflict' or fact.get('statement_scope')!='consolidated':continue
            old=facts.get((fact['period'],fact['metric_code']))
            if old and old['value']!=fact['value']:
                support_conflict=True
                result['warnings'].append({'code':'ADJACENT_PERIOD_BASIS_CONFLICT','message':'相邻报告重叠期间存在金额差异，未采用累计相减推导'})
            if (fact['period'],fact['metric_code']) not in facts:
                result['facts'].append(copy.deepcopy(fact));facts[(fact['period'],fact['metric_code'])]=result['facts'][-1]
        for supporting in docs:
            if supporting['document_id']!=doc['document_id']:result.setdefault('support_documents',[]).append(copy.deepcopy(supporting))
    def get(period,code):
        fact=facts.get((period,code));return fact if fact and fact.get('quality')!='conflict' else None
    def calc(code,period,required,unit,formula,op,positive=(),nonnegative=(),comparison=False):
        inputs=[get(*pair) for pair in required];missing=[{'period':p,'code':c} for (p,c),fact in zip(required,inputs) if fact is None]
        base={'metric_id':f'{doc["document_id"]}:{period}:{code}','document_id':doc['document_id'],'metric_code':code,
              'period':period,'unit':unit,'formula':formula,'input_fact_ids':[x['fact_id'] for x in inputs if x],
              'period_basis':'single' if period.endswith(('Q2','Q3')) else 'cumulative'}
        reason=None
        if missing:reason='CONFLICTING_INPUT' if any(facts.get(pair,{}).get('quality')=='conflict' for pair in required) else 'MISSING_INPUT'
        else:
            values=[Decimal(x['value']) for x in inputs]
            invalid=[i for i in positive if values[i]<=0]+[i for i in nonnegative if values[i]<0]
            if invalid:
                reason='NEGATIVE_BALANCE' if any(values[i]<0 for i in nonnegative) else 'DIVISION_BY_ZERO' if any(values[i]==0 for i in positive) else 'NONPOSITIVE_INPUT'
                missing=[{'period':required[i][0],'code':required[i][1]} for i in invalid]
            if code=='fixed_assets_cip_to_assets' and values[0]+values[1]>values[2]:reason='INCONSISTENT_COMPONENTS'
        if reason:
            base.update(status='unavailable',value=None,reason_code=reason,missing_inputs=missing,
                        detail=', '.join(f'{x["period"]}:{x["code"]}' for x in missing))
            if code.endswith(('_yoy','_qoq')) and not any(x is None for x in inputs):
                base['amount_comparison']={'current':inputs[0]['value'],'previous':inputs[1]['value'],
                    'absolute_change':format(Decimal(inputs[0]['value'])-Decimal(inputs[1]['value']),'f'),
                    'current_period':required[0][0],'previous_period':required[1][0],'unit':'CNY'}
        else:
            with localcontext() as ctx:
                ctx.prec=60;value=op(values)
            base.update(status='calculated',value=format(value,'f'))
        if comparison:result['comparison_metrics'].append(base)
        else:result['metrics' if base['status']=='calculated' else 'unavailable_metrics'].append(base)
        return base
    def metrics(period,previous,close,opening,days,comparison=False):
        for field in sorted(_FLOW_FIELDS):
            calc(field+'_yoy',period,[(period,field),(previous,field)],'percent','(current_period - prior_same_period) / prior_same_period * 100',lambda v:(v[0]-v[1])/v[1]*100,(1,),comparison=comparison)
        specs=[('gross_margin',['revenue','operating_cost'],'percent','(revenue - operating_cost) / revenue * 100',lambda v:(v[0]-v[1])/v[0]*100,(0,),(1,)),
               ('consolidated_net_margin',['net_profit','revenue'],'percent','net_profit / revenue * 100',lambda v:v[0]/v[1]*100,(1,),()),
               ('adjusted_attributable_net_margin',['attributable_net_profit_ex_nonrecurring','revenue'],'percent','adjusted_profit / revenue * 100',lambda v:v[0]/v[1]*100,(1,),()),
               ('cash_to_consolidated_profit',['operating_cash_flow','net_profit'],'times','operating_cash_flow / net_profit',lambda v:v[0]/v[1],(1,),()),
               ('simplified_free_cash_flow',['operating_cash_flow','capital_expenditure_cash_paid'],'CNY','operating_cash_flow - capital_expenditure_cash_paid',lambda v:v[0]-v[1],(),(1,)),
               ('r_and_d_expense_to_revenue',['r_and_d_expense','revenue'],'percent','r_and_d_expense / revenue * 100',lambda v:v[0]/v[1]*100,(1,),(0,))]
        for code,fields,unit,formula,op,pos,nonneg in specs:calc(code,period,[(period,f) for f in fields],unit,formula,op,pos,nonneg,comparison)
        for code,stock,flow in [('inventory_days','inventory','operating_cost'),('receivables_days_approx','accounts_receivable','revenue')]:
            calc(code,period,[(opening,stock),(close,stock),(period,flow)],'days',f'{days} * average_balance / period_flow',lambda v:(v[0]+v[1])/2/v[2]*days,(2,),(0,1),comparison)
        calc('fixed_assets_cip_to_assets',period,[(close,'fixed_assets'),(close,'construction_in_progress'),(close,'total_assets')],'percent','(fixed_assets + construction_in_progress) / total_assets * 100',lambda v:(v[0]+v[1])/v[2]*100,(2,),(0,1),comparison)
        calc('liabilities_to_assets',period,[(close,'total_liabilities'),(close,'total_assets')],'percent','total_liabilities / total_assets * 100',lambda v:v[0]/v[1]*100,(1,),(0,),comparison)
        calc('return_on_assets',period,[(period,'net_profit'),(opening,'total_assets'),(close,'total_assets')],'percent','period_net_profit / average_assets * 100',lambda v:v[0]/((v[1]+v[2])/2)*100,(1,2),(),comparison)
        calc('return_on_attributable_equity',period,[(period,'attributable_net_profit'),(opening,'attributable_equity'),(close,'attributable_equity')],'percent','period_attributable_profit / average_equity * 100',lambda v:v[0]/((v[1]+v[2])/2)*100,(1,2),(),comparison)
    days=Decimal((date(year,month,calendar.monthrange(year,month)[1])-date(year,1,1)).days+1)
    metrics(spec['period'],prior,spec['end'],f'{year-1}-12-31',days)
    prior_end=f'{year-1}-{month:02d}-{calendar.monthrange(year-1,month)[1]}'
    prior_days=Decimal((date(year-1,month,calendar.monthrange(year-1,month)[1])-date(year-1,1,1)).days+1)
    metrics(prior,period_key(year-2,spec['report_type'],spec['report_quarter']),prior_end,f'{year-2}-12-31',prior_days,True)
    comparisons={m['metric_code']:m for m in result['comparison_metrics']}
    for item in [*result['metrics'],*result['unavailable_metrics']]:
        previous=comparisons.get(item['metric_code'])
        if previous:
            item['comparison']=copy.deepcopy(previous)
            if previous['status']==item['status']=='calculated':
                item['comparison'].update(absolute_change=format(Decimal(item['value'])-Decimal(previous['value']),'f'),change_unit='percentage_points' if item['unit']=='percent' else item['unit'])
    # Never evaluate annual combination rules on interim cumulative inputs.
    from .risk import RULES
    for rule_id,(summary,_) in RULES.items():
        result['risk_assessments'].append({'assessment_id':f'{doc["document_id"]}:{rule_id}','rule_id':rule_id,
            'document_id':doc['document_id'],'period':spec['period'],'status':'not_evaluated',
            'summary':summary,'reason_code':'PERIOD_RULE_NOT_APPLICABLE','conditions':[],
            'period_explanation':'本期为中期报告；年度组合规则不直接套用，需对应期末附注及可靠逐季数据。','evidence_fact_ids':[]})
    if month in (6,9):
        q=month//3;single=f'{year}Q{q}';previous_cumulative=f'{year}Q1' if q==2 else f'{year}H1'
        for derived_year in (year,year-1):
            if month==9:
                for field in _FLOW_FIELDS:
                    h1=get(f'{derived_year}H1',field);q1=get(f'{derived_year}Q1',field);q2=f'{derived_year}Q2'
                    if h1 and q1 and not get(q2,field) and not support_conflict and not h1.get('comparative_restated') and not q1.get('comparative_restated'):
                        add({'fact_id':f'{doc["document_id"]}:{q2}:{field}','document_id':doc['document_id'],'metric_code':field,
                            'period':q2,'period_type':'duration','period_start':f'{derived_year}-04-01','period_end':f'{derived_year}-06-30',
                            'statement_scope':'consolidated','value':format(Decimal(h1['value'])-Decimal(q1['value']),'f'),
                            'unit':'CNY','quality':'derived','derivation':'累计金额相减（非独立披露值）',
                            'input_fact_ids':[h1['fact_id'],q1['fact_id']],'sources':copy.deepcopy(h1['sources']+q1['sources'])})
            target=f'{derived_year}Q{q}'
            cumulative=period_key(derived_year,spec['report_type'],spec['report_quarter'])
            adjacent=f'{derived_year}Q1' if q==2 else f'{derived_year}H1'
            for field in _FLOW_FIELDS:
                current=get(cumulative,field);previous=get(adjacent,field)
                # Prefer directly disclosed single-quarter data; derive only if inputs are consistent.
                if get(target,field) or not current or not previous or support_conflict:continue
                if current.get('comparative_restated') or previous.get('comparative_restated'):continue
                add({'fact_id':f'{doc["document_id"]}:{target}:{field}','document_id':doc['document_id'],'metric_code':field,
                    'period':target,'period_type':'duration','period_start':f'{derived_year}-{month-2:02d}-01',
                    'period_end':f'{derived_year}-{month:02d}-{calendar.monthrange(derived_year,month)[1]}',
                    'statement_scope':'consolidated','value':format(Decimal(current['value'])-Decimal(previous['value']),'f'),
                    'unit':'CNY','quality':'derived','derivation':'累计金额相减（非独立披露值）',
                    'input_fact_ids':[current['fact_id'],previous['fact_id']],'sources':copy.deepcopy(current['sources']+previous['sources'])})
        # A separate view is always returned; missing inputs remain explicit, never estimated.
        start_count=(len(result['metrics']),len(result['unavailable_metrics']))
        opening=f'{year}-{month-3:02d}-{calendar.monthrange(year,month-3)[1]}'
        qdays=Decimal((date(year,month,calendar.monthrange(year,month)[1])-date(year,month-2,1)).days+1)
        metrics(single,f'{year-1}Q{q}',spec['end'],opening,qdays)
        for field in ('revenue','attributable_net_profit','attributable_net_profit_ex_nonrecurring','operating_cash_flow'):
            calc(field+'_qoq',single,[(single,field),(f'{year}Q{q-1}',field)],'percent',
                 '(current_quarter - previous_quarter) / previous_quarter * 100',lambda v:(v[0]-v[1])/v[1]*100,(1,))
            calc(field+'_qoq',f'{year-1}Q{q}',[(f'{year-1}Q{q}',field),(f'{year-1}Q{q-1}',field)],'percent',
                 '(current_quarter - previous_quarter) / previous_quarter * 100',lambda v:(v[0]-v[1])/v[1]*100,(1,),comparison=True)
        prior_close=f'{year-1}-{month:02d}-{calendar.monthrange(year-1,month)[1]}'
        prior_open=f'{year-1}-{month-3:02d}-{calendar.monthrange(year-1,month-3)[1]}'
        prior_qdays=Decimal((date(year-1,month,calendar.monthrange(year-1,month)[1])-date(year-1,month-2,1)).days+1)
        metrics(f'{year-1}Q{q}',f'{year-2}Q{q}',prior_close,prior_open,prior_qdays,True)
        result['single_quarter']={'period':single,'metrics':result['metrics'][start_count[0]:],
            'unavailable_metrics':result['unavailable_metrics'][start_count[1]:],
            'message':'单季数据优先采用原文；累计相减值保留双份证据，缺少同口径前期报告时不推算。'}
        del result['metrics'][start_count[0]:];del result['unavailable_metrics'][start_count[1]:]
        for item in result['single_quarter']['metrics']:
            derived=[facts.get((single,field)) for field in _FLOW_FIELDS if facts.get((single,field),{}).get('quality')=='derived']
            if any(fact['fact_id'] in item['input_fact_ids'] for fact in derived):item['derived_from_cumulative']=True
        previous_metrics={m['metric_code']:m for m in result['comparison_metrics'] if m['period']==f'{year-1}Q{q}'}
        for item in [*result['single_quarter']['metrics'],*result['single_quarter']['unavailable_metrics']]:
            previous=previous_metrics.get(item['metric_code'])
            if previous:
                item['comparison']=copy.deepcopy(previous)
                if previous['status']==item['status']=='calculated':
                    with localcontext() as ctx:
                        ctx.prec=60;difference=Decimal(item['value'])-Decimal(previous['value'])
                    item['comparison'].update(absolute_change=format(difference,'f'),change_unit='percentage_points' if item['unit']=='percent' else item['unit'])
    # Interim rule availability is explicit rather than a silent 0/0 or a reused annual result.
    cash=result['risk_assessments'][0]
    quarter_end=year*4+month//3-1
    rolling=[f'{index//4}Q{index%4+1}' for index in range(quarter_end-3,quarter_end+1)]
    cash.update(status='insufficient_data',reason_code='MISSING_INPUT',period_explanation='连续负现金流须核验截至本期的逐季金额；累计现金流不能代替连续季度判断。',
                missing_or_invalid=[{'period':period,'code':'operating_cash_flow','reason':'MISSING_INPUT'} for period in rolling if not get(period,'operating_cash_flow')])
    cash['missing_or_invalid'] += [{'period':spec['end'],'code':'accounts_receivable_gross','reason':'MISSING_INPUT'},
                                   {'period':f'{year-1}-{month:02d}-{calendar.monthrange(year-1,month)[1]}','code':'accounts_receivable_gross','reason':'MISSING_INPUT'}]
    return result

def analyze_interim_pdf(path, kind, options=None, support=None, security_code=None):
    parsed=parse_interim_pdf(path,kind)
    if security_code:parsed['security_code']=security_code
    return analyze_parsed_interim(parsed,options,support)
