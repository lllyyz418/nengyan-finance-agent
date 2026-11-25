"""Canonical disclosure periods. Search filters never override PDF identity."""
import calendar

ALIASES = {'annual_report':'annual','half_year':'semiannual','half_year_report':'semiannual','quarterly_report':'quarterly'}

def report_kind(value):
    return ALIASES.get(value, value)

def period_key(year, kind='annual', quarter=None, basis='cumulative'):
    kind = report_kind(kind)
    if kind == 'annual': return str(year)
    if kind == 'semiannual': return f'{year}H1' if basis == 'cumulative' else f'{year}Q2'
    if kind == 'quarterly' and quarter in (1,3):
        return f'{year}Q1' if quarter == 1 else f'{year}Q3' if basis == 'single' else f'{year}YTD9'
    raise ValueError('报告类型或季度无效：季报请选择第一季度或第三季度')

def period_spec(doc):
    kind = report_kind(doc.get('report_type'))
    quarter = doc.get('report_quarter')
    if kind not in ('annual','semiannual','quarterly'): raise ValueError('不是可分析的定期报告')
    year = doc['report_year']
    month = 12 if kind == 'annual' else 6 if kind == 'semiannual' else quarter * 3 if quarter in (1,3) else None
    if not month: raise ValueError('季度报告未识别季度')
    return {'report_type':kind,'report_quarter':quarter,'period':period_key(year,kind,quarter),
            'end':f'{year}-{month:02d}-{calendar.monthrange(year,month)[1]}','month':month}

def report_label(kind, quarter=None):
    return '年度报告' if report_kind(kind)=='annual' else '半年度报告' if report_kind(kind)=='semiannual' else '第一季度报告' if quarter==1 else '第三季度报告'
