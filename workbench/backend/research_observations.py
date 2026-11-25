"""Server-owned factual observations. No new rates or financial values invented."""
from decimal import Decimal
import re


def observations(packet):
    registry=packet['registry'];output=[]
    current={p['code']:p for p in registry.values() if p['scope']=='本期' and p['kind']=='metric'
             and p['period_basis'] in {'annual','cumulative'} and p['period'][:4]==packet['period'][:4]}

    def add(title,text,points,topic):
        if not points:return
        output.append({'id':f'o{len(output)+1:03d}','headline':title,'fact':text,
                       'evidence_ids':[p['id'] for p in points],'topic':topic})

    for left,right,title,topic in [
        ('revenue_yoy','attributable_net_profit_yoy','收入与归母利润增长的关系','profit'),
        ('revenue_yoy','operating_cost_yoy','收入与营业成本增长的关系','profit'),
        ('operating_cash_flow_yoy','net_profit_yoy','经营现金流与合并利润增长的关系','cash')]:
        a,b=current.get(left),current.get(right)
        if not a or not b or a['period']!=b['period'] or a['unit']!=b['unit']:continue
        relation='高于' if Decimal(a['value'])>Decimal(b['value']) else '低于' if Decimal(a['value'])<Decimal(b['value']) else '等于'
        add(title,a['label']+relation+b['label']+'；这只是已计算增速的关系，不证明具体经营原因。',[a,b],topic)

    notes={
        'gross_margin':('毛利表现与经营结构','profit','毛利率是本期水平；未取得同口径比较值时，不判断其上升或下降。'),
        'adjusted_attributable_net_margin':('扣非盈利的观察边界','profit','扣非净利率不能单独证明利润可持续，需要结合收入、费用与现金流。'),
        'nonrecurring_to_attributable_profit':('非经常性损益的利润贡献','profit','这里只反映非经常性损益与归母净利润的比例；未提取明细不等于公司未披露。'),
        'cash_to_consolidated_profit':('经营现金与合并利润的对应','cash','该比值的利润口径为合并净利润；不能直接替换成归母净利润，也不等同于利润真实性认证。'),
        'simplified_free_cash_flow':('经营现金对长期资产现金投入的覆盖','capital','本指标是经营现金流扣除购建长期资产支付现金后的简化值，不涵盖全部租赁、非现金投资或债务到期安排。'),
        'liabilities_to_assets':('资产负债结构需要进一步拆分','capital','资产负债率包含经营性及其他负债，不能等同于有息负债率，也不能单凭本期水平认定高风险或偿债安全。'),
        'inventory_days':('存货占用与经营节奏','cash','存货周转天数需结合产品结构和备货安排；只有本期水平时，不判断周转改善或恶化。'),
        'receivables_days_approx':('应收回收与结算节奏','cash','应收周转天数为近似口径，不等于合同逾期天数。'),
        'inventory_impairment_to_gross':('存货减值披露的核查边界','cash','存货跌价准备比例不能单独判断计提是否充分；毛利率也不能替代可变现净值测试。'),
        'receivables_over_1y_to_gross':('应收账龄与信用风险的区别','cash','账龄占比不等于逾期率或预期信用损失率，仍需回款和客户信用材料。'),
        'r_and_d_expense_to_revenue':('研发费用投入的观察','capital','研发费用率不是研发资本化率，未取得资本化金额时不能从费用率推断资本化政策。'),
    }
    for code,(title,topic,note) in notes.items():
        point=current.get(code)
        if not point:continue
        prior=next((p for p in registry.values() if p['scope']=='本期' and p['kind']=='metric' and p['code']==code
                    and p['period']<point['period'] and p['unit']==point['unit'] and p['period_basis']==point['period_basis']
                    and not p.get('restated') and not point.get('restated')),None)
        if prior:
            direction='高于' if Decimal(point['value'])>Decimal(prior['value']) else '低于' if Decimal(point['value'])<Decimal(prior['value']) else '等于'
            add(title,'本期已保存的'+point['label']+direction+'同一报告中的同口径比较期值。指标变化不证明具体经营原因，也不自动等同于改善或恶化。',[point,prior],topic)
        else:add(title,note,[point],topic)
    for code in ('revenue_q4_qoq','attributable_net_profit_q4_qoq','operating_cash_flow_q4_qoq'):
        point=next((p for p in registry.values() if p['scope']=='本期' and p['kind']=='metric' and p['code']==code),None)
        if point:add('期末单季变化的持续性待核实','此处仅有引擎计算的相邻季度变化，不能据此声称全年逐季持续改善或恶化。',[point],'profit' if code!='operating_cash_flow_q4_qoq' else 'cash')
    for point in registry.values():
        if point['kind']=='basis_check':
            add(point['label'],'已保存的历史口径核对结论为“'+point['display']+'”：'+point['condition_text']+' 受影响的跨报告变化不能直接认定为经营变化；原因未知时不得编造重述理由。',[point],'history')
            continue
        if point['kind']!='rule':continue
        text='已保存的“'+point['label']+'”评估为“'+point['display']+'”。实际条件：'+point['condition_text']+'。规则触发不等于风险认定，未触发不等于安全；不能用其他指标替换实际触发条件。'
        add(point['label']+'复核观察',text,[point],'capital' if 'expansion' in point['code'] else 'cash')
    # Historical direction is a direct ordering of saved, comparable metrics;
    # no new growth rates/deltas are calculated in the research layer.
    for code,point in current.items():
        candidates=[p for p in registry.values() if p['scope']=='历年同期' and p['kind']=='metric' and p['code']==code
                    and p['unit']==point['unit'] and p['period_basis']==point['period_basis']
                    and p['period'][:4]<point['period'][:4] and not p.get('restated') and not point.get('restated')
                    and not p.get('comparison_blocked') and not point.get('comparison_blocked')]
        if not candidates:continue
        prior=max(candidates,key=lambda p:p['period'])
        direction='高于' if Decimal(point['value'])>Decimal(prior['value']) else '低于' if Decimal(point['value'])<Decimal(prior['value']) else '等于'
        add('同口径历史观察：'+point['label'],'本期已保存的指标值'+direction+'最近可取得的同口径历史值；不推断缺失年份，也不把变化自动等同于好坏。',[point,prior],'history')
        if len([o for o in output if o['topic']=='history'])>=5:break
    # Selected peers are observations, not an industry benchmark or a ranking.
    for point in list(registry.values()):
        if point['scope']!='所选同行' or point['kind']!='metric':continue
        target=current.get(point['code'])
        if not target or any(point[key]!=target[key] for key in ('period','unit','period_basis')):continue
        add('所选同行并排观察：'+point['label'],'仅展示所选公司同期间、同单位的指标；业务结构与会计口径仍需人工核对，不称作行业平均或行业排名。',[target,point],'peers')
        if len([o for o in output if o['topic']=='peers'])>=3:break
    return output[:28]
