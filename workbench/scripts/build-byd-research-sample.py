"""A standalone, source-bound, vector PDF design sample. No application edits."""
import json
import math
import hashlib
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor, Color, white
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph, Table, TableStyle

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/pdf';OUT.mkdir(parents=True,exist_ok=True)
QA=ROOT/'tmp/pdfs/byd-2025-layout';QA.mkdir(parents=True,exist_ok=True)
PDF=OUT/'比亚迪2025年度研究报告-彩色设计样稿.pdf'
pdfmetrics.registerFont(TTFont('YH','C:/Windows/Fonts/msyh.ttc',subfontIndex=0))
pdfmetrics.registerFont(TTFont('YHB','C:/Windows/Fonts/msyhbd.ttc',subfontIndex=0))
pdfmetrics.registerFontFamily('YH',normal='YH',bold='YHB',italic='YH',boldItalic='YHB')
W,H=595.2756,841.8898
M=44;CW=W-M*2
NAVY='#102F43';TEAL='#0C8580';MINT='#36B2A2';BLUE='#567FD1';PURPLE='#9374B8';GOLD='#BD904B';RED='#C16A74'
INK='#294555';MUTED='#6E8490';LINE='#DDE8EC';PALE='#F0F7F6';SKY='#F1F5FC';SAND='#FBF6ED';ROSE='#FBF0F2'
PALETTE=[TEAL,BLUE,PURPLE,GOLD,MINT,RED]
AUDIT=[];DRAW=[];PAGE=0
c=canvas.Canvas(str(PDF),pagesize=(W,H),pageCompression=1)
c.setTitle('比亚迪2025年度研究报告 | 彩色设计样稿 | 能研财智')
c.setAuthor('能研财智 - 研究报告设计样稿')
c.setSubject('基于本地2025年报及已保存财务结果的26页彩色样稿；非评级或盈利预测报告')

material=json.loads((QA/'material.json').read_text(encoding='utf-8'))
task=next(t for t in material['tasks'] if t['id']=='8f0ad2e8599d47af805bc337d6ea5284')
analysis=task['analysis'];doc=analysis['documents'][0]
assert doc['file_sha256']==next(s['sha256'] for s in material['sources'] if s['year']==2025)

def fact(code,period='2025'):
    found=[f for f in analysis['facts'] if f['metric_code']==code and f['period']==period and f.get('quality')!='conflict']
    assert len(found)==1,(code,period)
    f=found[0];AUDIT.append({'kind':'saved_fact','code':code,'period':period,'value':f['value'],'document_id':f['document_id']})
    return float(Decimal(str(f['value']))/Decimal('100000000'))

def metric(code):
    m=next(m for m in analysis['metrics'] if m['metric_code']==code and m['period']=='2025' and m['status']=='calculated')
    AUDIT.append({'kind':'saved_metric','code':code,'value':m['value'],'unit':m['unit']})
    return float(m['value'])/(1e8 if m['unit']=='CNY' else 1)

REV=[6023.15354,7771.02455,8039.64958]
ATTR=[300.40811,402.54346,326.19022]
ADJ=[284.62432,369.82887,294.45703]
CFO=[1697.25025,1334.53873,591.35544]
COST=[fact('operating_cost','2024'),fact('operating_cost')]
NP=[fact('net_profit','2024'),fact('net_profit')]
CAPEX=[fact('capital_expenditure_cash_paid','2024'),fact('capital_expenditure_cash_paid')]
GM=[(REV[1]-COST[0])/REV[1]*100,metric('gross_margin')]
NPM=[NP[0]/REV[1]*100,metric('consolidated_net_margin')]
ADJM=[ADJ[1]/REV[1]*100,metric('adjusted_attributable_net_margin')]
FCF=[CFO[1]-CAPEX[0],metric('simplified_free_cash_flow')]
QR=[1703.60448,2009.205,1949.84598,2376.99412]
QP=[91.54985,63.55548,78.2264,92.85849]
QA_PROFIT=[81.71627,54.28114,68.90751,89.55211]
QC=[85.80961,232.52510,90.12027,182.90046]
assert abs(sum(QR)-REV[2])<.0001 and abs(sum(QP)-ATTR[2])<.0001 and abs(sum(QC)-CFO[2])<.0001

def col(code):return HexColor(code)
def rect(x,top,w,h,fill,stroke=None,r=0):
    c.setFillColor(col(fill));c.setStrokeColor(col(stroke or fill))
    if r:c.roundRect(x,H-top-h,w,h,r,fill=1,stroke=bool(stroke))
    else:c.rect(x,H-top-h,w,h,fill=1,stroke=bool(stroke))
def line(x1,t1,x2,t2,color=LINE,width=.6,dash=None):
    c.setStrokeColor(col(color));c.setLineWidth(width);c.setDash(dash or [])
    c.line(x1,H-t1,x2,H-t2);c.setDash([])
def text(x,top,value,size=10,color=INK,bold=False,align='left'):
    value=str(value);c.setFont('YHB' if bold else 'YH',size);c.setFillColor(col(color))
    getattr(c,{'left':'drawString','right':'drawRightString','center':'drawCentredString'}[align])(x,H-top-size*.83,value)
    DRAW.append({'page':PAGE,'top':top,'bottom':top+size,'kind':'text'})
def rich(s):
    # Author-authored short emphasis only; source PDF prose is never injected as markup.
    return s
def para(s,top,x=M,w=CW,size=10,leading=17,color=INK,bg=None,pad=12,bold=False):
    style=ParagraphStyle('p',fontName='YHB' if bold else 'YH',fontSize=size,leading=leading,
        textColor=col(color),wordWrap='CJK',splitLongWords=True,spaceAfter=0,allowWidows=0,allowOrphans=0)
    p=Paragraph(rich(s),style)
    iw=w-pad*2 if bg else w
    _,h=p.wrap(iw,1000)
    if bg:rect(x,top,w,h+pad*2,bg,r=5);px=x+pad;pt=top+pad
    else:px=x;pt=top
    p.drawOn(c,px,H-pt-h)
    DRAW.append({'page':PAGE,'top':top,'bottom':pt+h+(pad if bg else 0),'kind':'paragraph'})
    return pt+h+(pad if bg else 0)
def source(s,top=767):
    return para(s,top,size=7.1,leading=10.2,color=MUTED)
def heading(s,top,x=M,w=CW):
    rect(x,top+1,3,13,TEAL);text(x+10,top,s,11.3,NAVY,True)
def callout(label,s,top,x=M,w=CW,tone=TEAL):
    bg={TEAL:PALE,BLUE:SKY,GOLD:SAND,RED:ROSE}.get(tone,PALE)
    p=Paragraph(s,ParagraphStyle('box',fontName='YH',fontSize=9.3,leading=15.2,textColor=col(INK),wordWrap='CJK'))
    _,hh=p.wrap(w-28,1000);height=hh+48
    rect(x,top,w,height,bg,r=5);rect(x,top,3,height,tone)
    text(x+14,top+12,label,9.5,tone,True);p.drawOn(c,x+14,H-top-33-hh)
    DRAW.append({'page':PAGE,'top':top,'bottom':top+height,'kind':'callout'})
    return top+height
def newpage(section,title,deck):
    global PAGE
    PAGE+=1
    c.setFillColor(white);c.rect(0,0,W,H,fill=1,stroke=0)
    text(M,25,'能研财智  /  公司研究',8.2,NAVY,True)
    text(W-M,25,'BYD  ·  2025 ANNUAL REVIEW',7.5,MUTED,align='right')
    line(M,45,W-M,45)
    text(M,65,section,8.5,TEAL,True)
    text(M,86,title,20,NAVY,True)
    para(deck,119,size=8.6,leading=13,color=MUTED)
    c.bookmarkPage('p'+str(PAGE));c.addOutlineEntry(title,'p'+str(PAGE),level=0)
    line(M,800,W-M,800)
    text(M,812,'研究设计样稿  |  2025年报口径  |  非评级或投资建议',7,MUTED)
    text(W-M,810,f'{PAGE:02d} / 26',8.5,NAVY,True,align='right')
def finish():c.showPage()
def cards(items,top=158):
    width=(CW-20)/3
    for i,(label,value,note,color) in enumerate(items):
        x=M+(i%3)*(width+10);t=top+(i//3)*90
        rect(x,t,width,78,PALE if color==TEAL else SKY if color==BLUE else SAND if color==GOLD else ROSE,r=5)
        text(x+12,t+11,label,8.8,MUTED);text(x+12,t+29,value,21,color,True);text(x+12,t+58,note,7.6,MUTED)
def table(headers,rows,top,widths=None,x=M,w=CW,size=8.5,highlight=None):
    widths=widths or [w/len(headers)]*len(headers)
    def cell(s,ishead=False):
        return Paragraph(str(s),ParagraphStyle('cell',fontName='YHB' if ishead else 'YH',fontSize=size,
            leading=size*1.55,textColor=white if ishead else col(INK),wordWrap='CJK'))
    data=[[cell(v,True) for v in headers]]+[[cell(v) for v in row] for row in rows]
    tb=Table(data,colWidths=widths,hAlign='LEFT')
    styles=[('BACKGROUND',(0,0),(-1,0),col(TEAL)),('VALIGN',(0,0),(-1,-1),'TOP'),
        ('LEFTPADDING',(0,0),(-1,-1),10),('RIGHTPADDING',(0,0),(-1,-1),9),
        ('TOPPADDING',(0,0),(-1,-1),9),('BOTTOMPADDING',(0,0),(-1,-1),9),
        ('LINEBELOW',(0,0),(-1,0),.5,col(TEAL)),('LINEBELOW',(0,1),(-1,-1),.35,col(LINE))]
    for i in range(1,len(data)):styles.append(('BACKGROUND',(0,i),(-1,i),col('#F2F7F8' if i%2 else '#FFFFFF')))
    if highlight is not None:styles.append(('BACKGROUND',(0,highlight+1),(-1,highlight+1),col('#E3F2EF')))
    tb.setStyle(TableStyle(styles));_,h=tb.wrap(w,1000);tb.drawOn(c,x,H-top-h)
    DRAW.append({'page':PAGE,'top':top,'bottom':top+h,'kind':'table'})
    return top+h
def panel(title,top,h=235,x=M,w=CW,unit=None):
    rect(x,top,w,h,'#FFFFFF',LINE,r=6);text(x+15,top+13,title,10,NAVY,True)
    if unit:text(x+w-15,top+15,unit,7.2,MUTED,align='right')
    return x+47,top+51,w-65,h-91
def nice_range(values):
    lo=min(0,min(values));hi=max(0,max(values));span=hi-lo or 1
    step=10**math.floor(math.log10(span/4))
    ratio=span/4/step;step*=next((v for v in [1,2,2.5,5,10] if v>=ratio),10)
    lo=math.floor(lo/step)*step;hi=math.ceil(hi/step)*step
    if hi==lo:hi+=step
    return lo,hi,step
def chart(labels,series,top,title,unit='亿元',h=235,x=M,w=CW,kind='bar',dec=1,ylimits=None):
    px,pt,pw,ph=panel(title,top,h,x,w,unit)
    values=[v for _,vs,_ in series for v in vs]
    lo,hi,step=ylimits or nice_range(values)
    def yy(v):return pt+ph-(v-lo)/(hi-lo)*ph
    ticks=round((hi-lo)/step)
    for i in range(ticks+1):
        v=lo+i*step;y=yy(v);line(px,y,px+pw,y,LINE,.4,dash=[2,2]);text(px-8,y-3,f'{v:g}',7,MUTED,align='right')
    line(px,yy(0),px+pw,yy(0),'#B8CAD2',.6)
    group=pw/len(labels);n=len(series)
    for si,(label,vs,color) in enumerate(series):
        if kind=='line':
            points=[(px+group*(i+.5),yy(v)) for i,v in enumerate(vs)]
            for a,b in zip(points,points[1:]):line(*a,*b,color,1.8)
            for i,(xx,y) in enumerate(points):
                c.setFillColor(col(color));c.circle(xx,H-y,2.5,stroke=0,fill=1)
                text(xx,y+6 if si==2 else y-14,f'{vs[i]:.{dec}f}',7.4,color,True,align='center')
        else:
            bw=min(38,group*.72/n)
            for i,v in enumerate(vs):
                xx=px+group*(i+.5)+(si-(n-1)/2)*bw
                y=yy(v);zero=yy(0);rect(xx-bw*.42,min(y,zero),bw*.84,max(1,abs(y-zero)),color,r=1)
                inside=v<0 and abs(y-zero)>22
                text(xx,y-12 if v>=0 else y-13 if inside else y+3,f'{v:,.{dec}f}',7.2,'#FFFFFF' if inside else color,True,align='center')
    for i,label in enumerate(labels):text(px+group*(i+.5),pt+ph+9,label,7.6,MUTED,align='center')
    cursor=x+16
    for label,vs,color in series:
        rect(cursor,top+h-18,6,6,color,r=1);text(cursor+10,top+h-20,label,7.5,MUTED)
        cursor+=pdfmetrics.stringWidth(label,'YH',7.5)+29
    DRAW.append({'page':PAGE,'top':top,'bottom':top+h,'kind':'chart'})
    return top+h
def stacked(labels,series,top,title,unit='亿元',h=225):
    px,pt,pw,ph=panel(title,top,h,unit=unit)
    totals=[sum(vs[i] for _,vs,_ in series) for i in range(len(labels))]
    _,hi,step=nice_range(totals)
    for i in range(round(hi/step)+1):
        y=pt+ph-i*step/hi*ph;line(px,y,px+pw,y,LINE,.4,[2,2]);text(px-8,y-3,f'{i*step:g}',7,MUTED,align='right')
    gw=pw/len(labels);bw=50
    for i,label in enumerate(labels):
        acc=0;xx=px+gw*(i+.5)-bw/2
        for name,vs,color in series:
            v=vs[i];height=v/hi*ph;topv=pt+ph-(acc+v)/hi*ph
            rect(xx,topv,bw,height,color);acc+=v
        text(xx+bw/2,pt+ph-acc/hi*ph-13,f'{acc:,.1f}',7.8,INK,True,align='center')
        text(xx+bw/2,pt+ph+8,label,8,MUTED,align='center')
    cursor=M+16
    for name,_,color in series:
        rect(cursor,top+h-17,6,6,color);text(cursor+10,top+h-19,name,7.4,MUTED);cursor+=pdfmetrics.stringWidth(name,'YH',7.4)+30
    return top+h
def donut(items,top,title,center,sub):
    panel(title,top,228)
    cx=M+129;cy=H-(top+132);radius=69;start=90
    for label,v,color in items:
        extent=360*v/sum(item[1] for item in items)
        c.setFillColor(col(color));c.wedge(cx-radius,cy-radius,cx+radius,cy+radius,start,extent,stroke=0,fill=1);start+=extent
    c.setFillColor(white);c.circle(cx,cy,47,fill=1,stroke=0)
    text(cx,top+113,center,22,TEAL,True,align='center');text(cx,top+145,sub,8,MUTED,align='center')
    for i,(label,v,color) in enumerate(items):
        xx=M+242;tt=top+70+i*43;rect(xx,tt+3,7,7,color,r=1);text(xx+15,tt,label,9,INK)
        text(xx+15,tt+17,f'{v:,.2f}',13,color,True)
def two_notes(left,right,top):
    width=(CW-16)/2
    callout(left[0],left[1],top,w=width,tone=TEAL)
    callout(right[0],right[1],top,x=M+width+16,w=width,tone=GOLD)
def flow(nodes,top,h=100):
    gap=14;w=(CW-gap*(len(nodes)-1))/len(nodes)
    for i,(label,desc) in enumerate(nodes):
        xx=M+i*(w+gap);rect(xx,top,w,h,[PALE,SKY,SAND,ROSE][i%4],r=5)
        text(xx+12,top+13,f'{i+1:02d}',9,PALETTE[i%len(PALETTE)],True)
        para(label,top+34,xx+12,w-24,size=10,leading=15,color=NAVY,bold=True)
        para(desc,top+62,xx+12,w-24,size=8,leading=13,color=MUTED)
        if i<len(nodes)-1:text(xx+w+4,top+h/2-5,'›',12,MUTED)

# 01 - Cover: original vector artwork, not an AI-generated background.
PAGE=1
rect(0,0,W,H,NAVY)
for i in range(9):
    path=c.beginPath();path.moveTo(W*.40,H-180-i*32);path.curveTo(W*.86,H-70-i*16,W*.43,270-i*13,W+70,230-i*27)
    c.setStrokeColor(Color(.12,.55,.53,alpha=.10+i*.025));c.setLineWidth(1);c.drawPath(path)
rect(M,51,23,23,TEAL,r=5);text(M+34,54,'能研财智',13,'#FFFFFF',True)
text(W-M,57,'RESEARCH SERIES  /  001',8,'#97BBB9',align='right')
text(M,160,'2025',78,'#8AD5C9',True)
text(M,266,'比亚迪',42,'#FFFFFF',True)
text(M,329,'规模之外，重看增长的质量',21,'#FFFFFF',True)
para('年度财务与经营研究报告',378,size=12,leading=20,color='#B8D5DC')
line(M,431,W-M,431,'#42616F',.7)
para('收入结构  /  利润兑现  /  现金与投入',455,size=11,leading=18,color='#D0E6E7')
para('以已保存的财务计算为底座，连接业务变化、经营现金与资本配置。<br/>本样稿用于确认完整研报的内容展开、彩色图表与 PDF 版式。',493,w=440,size=10,leading=19,color='#AFC8D0')
for i,(big,label) in enumerate([('8,039.65','营业收入 / 亿元'),('326.19','归母净利润 / 亿元'),('38.65%','境外收入占比')]):
    xx=M+i*175;text(xx,614,big,29,'#8AD5C9',True);text(xx,655,label,9,'#BDCFD6')
rect(M,714,CW,52,'#1A3D50',r=5)
para('设计样稿 · 非正式投资评级报告<br/>真实披露与测算分别标注；敏感性矩阵为假设演示，不是业绩预测。',727,x=M+13,w=CW-26,size=8.4,leading=14,color='#D6E6E9')
text(M,794,'样稿制作日期  2026.10.17',8,'#9FBDC8');text(W-M,794,'01 / 26',8,'#D0E6E7',align='right')
c.bookmarkPage('p1');c.addOutlineEntry('封面','p1',level=0);finish()

# 02 - Navigation.
newpage('阅读导航 / READING MAP','从规模走向质量，先建立研究坐标','正文第 3-24 页共 22 页；本文件是独立设计样稿，尚未改变工作台或模型生成流程。')
heading('六条主线，一份可回溯的研究底稿',158)
table(['研究主线','页码','希望回答的问题'],[
    ['核心判断与业务底座','03-04','收入、利润与现金为什么需要分开观察？'],
    ['规模与结构','05-09','增长来自哪里？产品、区域和季度如何变化？'],
    ['盈利与现金','10-15','利润的压力在哪一环？现金能否支持经营？'],
    ['资产、资金与研发','16-20','扩张用了多少资金？研发口径如何影响理解？'],
    ['战略、风险与验证','21-24','哪些信号支持判断，哪些信号需要重估？'],
    ['方法与来源','25-26','哪些是事实，哪些是计算，哪些仍是假设？'],
],190,[128,54,CW-182],size=9)
heading('样稿的信息层级',519)
two_notes(('实数与计算','青绿与蓝色用于财务披露和可复算图表。关键数据可回到年报物理页，计算公式在图注或方法页列示。'),
    ('假设与缺口','金色用于需要验证的解释、未取得的资料和敏感性假设。不使用虚构同行数值、目标价或评级。'),551)
para('阅读建议：先看第 3 页的核心矛盾，再按问题进入对应章节。图表是论证工具，不是独立结论；公司经营说明属于管理层披露，不替代独立核验。',685,size=10,leading=18)
source('来源说明：S1 为本地已核验的比亚迪 2025 年年度报告；R1 为系统已保存的财务分析结果。')
finish()

# 03 - Executive research.
newpage('01 / 核心判断','增长没有消失，但质量发生分化','研究主线：境外收入扩张与国内收入收缩并存；利润和经营现金的回落幅度大于集团收入变化。')
cards([('营业收入','8,039.65','亿元 · 同比 +3.46%',TEAL),('归母净利润','326.19','亿元 · 同比 -18.97%',RED),('经营现金流','591.36','亿元 · 同比 -55.69%',BLUE),
    ('境外收入占比','38.65%','较上年 +10.10 个百分点',TEAL),('合并毛利率','17.74%','系统已保存计算结果',BLUE),('研发总投入','634.41','亿元 · 同比 +17.13%',GOLD)],153)
heading('本报告的三项研究判断',360)
y=391
for title,body in [
    ('01  境外增长是重要增量，但不能代表所有业务','境外收入增加，与中国地区收入下降形成对照。需要区分区域销售增长、区域盈利及海外资本投入，不能只凭出海叙事推断集团利润改善。'),
    ('02  利润承压需要回到业务与成本结构','集团收入仍增长，但汽车相关和电子产品口径毛利率均下降。应先检验分部盈利，再讨论价格、结构与费用；年报没有给出完整量价利润桥，不能把原因单一化。'),
    ('03  现金与资本投入的配合更值得跟踪','经营现金流下降，而购建长期资产现金支出增加。负的简化自由现金流提示资金配置问题，但不等于企业已经出现偿债危机。')]:
    text(M,y,title,11,NAVY,True);y=para(body,y+24,size=10,leading=18)+20
callout('判断边界','这是一份基于年度披露的经营研究样稿，不形成买卖评级、目标价或未来盈利预测。',690,tone=GOLD)
source('来源：S1，物理页 11、29-30、39-40；R1，合并毛利率。')
finish()

# 04 - Business system.
newpage('02 / 公司与业务','先把业务口径讲清，再讨论竞争力','集团口径包含不同业务，不能直接将集团收入、利润或境外销售等同于新能源汽车整车表现。')
para('年报列示的主营业务包括新能源汽车、手机部件及组装、二次充电电池与光伏，并拓展城市轨道交通。财务研究首先要匹配公司叙事与报表分类：产品口径中的“汽车、汽车相关产品及其他产品”不是纯整车收入。',157,size=10,leading=18)
flow([('技术与零部件','电池、电机、电控等'),('产品与业务','整车、电子、其他'),('区域与渠道','中国、境外；直销、经销'),('财务与现金','收入、毛利、费用、回款')],259,124)
table(['业务观察对象','本稿采用的披露口径','避免的误读'],[
    ['汽车及相关业务','汽车、汽车相关产品及其他产品','不直接用分部收入除整车销量当作整车售价'],
    ['电子相关业务','手机部件、组装及其他产品','不与汽车业务套用同一盈利驱动'],
    ['境外扩张','集团分地区收入与毛利率','不当作汽车出口收入或海外工厂盈利'],
    ['研发与产能','研发总投入、研发费用、固定资产等','不把计划、投资额或产量视为真实产能利用率'],
],417,[100,176,CW-276],size=8.8)
callout('研究含义','垂直整合可能影响成本、研发协同与资金占用，但优势是否兑现，仍需通过成本率、投入效率和现金表现验证。不能由产业链覆盖范围直接推导盈利优势。',669)
source('来源：S1，物理页 13-14、29-30。业务描述为年报披露的归纳；流程关系为本稿研究框架。')
finish()

# 05 - Annual scale.
newpage('03 / 规模与增长','收入仍增长，利润却回落','将营业收入和归母利润拆开呈现，避免双坐标图放大或缩小变化的视觉感受。')
para('2025 年营业收入为 <b>8,039.65 亿元</b>，归母净利润为 <b>326.19 亿元</b>，分别同比增长 3.46% 和下降 18.97%。收入增速较上年放缓，而归母净利率由约 5.18% 降至 4.06%，每单位集团收入对应的股东盈利减少。研究重点因而不只是寻找销量增长点，还要拆解存量业务的盈利压力与新增业务的回报兑现。',157,size=10,leading=18)
chart(['2023','2024','2025'],[('营业收入',REV,TEAL)],258,'图 1  营业收入：规模变化',h=210)
chart(['2023','2024','2025'],[('归母净利润',ATTR,BLUE)],485,'图 2  归母净利润：盈利并非同步增长',h=210)
para('<b>观察：</b>2025 年归母利润仍高于 2023 年，但低于 2024 年。不能据此直接判断长期竞争力上升或下降，需要解释利润波动来源。',711,size=9.3,leading=15.5)
source('来源：S1，物理页 11；三个年度均取自 2025 年报的比较数据。金额统一为亿元。')
finish()

# 06 - Segments.
newpage('03 / 收入结构','汽车相关业务占主导，电子业务不可忽略','产品分类用于解释集团结构；两类业务的毛利率、周期和收入确认特征不宜混同。')
para('汽车相关产品收入占比为 <b>80.68%</b>，电子相关产品占比为 19.31%。前者收入较上年增加约 312.64 亿元，后者减少约 43.72 亿元，两者共同解释集团大部分收入变化。汽车相关业务毛利率高于电子相关业务，前者占比提升并没有阻止集团毛利率下降，说明业务自身盈利的变化，比仅看收入组合更值得研究。',157,size=10,leading=18)
stacked(['2024','2025'],[('汽车及相关',[6173.81935,6486.45636],TEAL),('电子相关',[1596.08576,1552.36528],BLUE),('其他',[1.11944,.82794],GOLD)],258,'图 3  分产品收入结构',h=222)
table(['产品口径','2024 年','2025 年','同比','2025 占比'],[
    ['汽车及相关产品','6,173.82','6,486.46','+5.06%','80.68%'],
    ['电子相关产品','1,596.09','1,552.37','-2.74%','19.31%'],
    ['其他','1.12','0.83','-26.04%','0.01%'],
],505,[145,91,91,90,CW-417],size=8.5)
callout('进一步研究','分部增速不同，不代表盈利贡献按同样方向变化。下一步应将收入结构与分部毛利率并列，避免把“汽车占比上升”等同于集团毛利率一定上升。',666)
source('来源：S1，物理页 29；汽车及相关、电子相关为本稿简写，完整分类见原表。表内金额单位：亿元。')
finish()

# 07 - Geography.
newpage('03 / 区域结构','境外收入增加，抵消中国地区回落','区域分化是本期收入变化的重要观察，但集团地区口径不等于汽车出口口径。')
para('中国（包括港澳台）地区收入下降 11.17%，境外收入增长 <b>40.05%</b>。境外增加约 888.56 亿元收入，超过集团整体约 268.63 亿元的增量，抵消了中国地区约 619.94 亿元的回落。境外占比升至 38.65%，且其毛利率改善；区域扩张正在影响集团的收入与利润组合，但这里是集团地区口径，不等同于整车出口利润。',157,size=10,leading=18)
chart(['中国地区','境外'],[('2024 年',[5552.17682,2218.84773],BLUE),('2025 年',[4932.23970,3107.40988],TEAL)],257,'图 4  区域收入对比',h=235)
table(['区域','收入变动（亿元）','毛利率 2024→2025','解读边界'],[
    ['中国地区','-619.94','20.18% → 16.66%','集团地区口径'],
    ['境外','+888.56','17.58% → 19.46%','不等于汽车出口利润'],
    ['合计','+268.63','见合并口径分析','增量不可直接分配至单一业务'],
],518,[90,126,145,CW-361],size=8.5)
callout('支持与反向信号','支持信号是境外收入与毛利增长能转化为经营现金；反向信号是海外库存、应收或固定资产投入增长快于现金回收。当前不能仅以境外收入占比推断投入回报。',668,tone=BLUE)
source('来源：S1，物理页 29-30。收入变动=2025 减 2024；2024 地区毛利率由本期值与披露变动复原。')
finish()

# 08 - Volumes.
newpage('03 / 产销与产品','销量增长，也有车型结构的分化','整车快报销量作为经营量指标；不与集团全部收入直接拼接为单车售价。')
para('2025 年整车快报销量为 <b>460.24 万辆</b>，同比增长 7.73%。其中 SUV 增加约 29.18 万辆，MPV 增加约 7.10 万辆，轿车减少约 6.77 万辆，产品组合呈现分化。乘用车仍贡献主要销量，商用车高增长则建立在较小基数上。车型结构可能影响收入与成本，但没有匹配的成本表时，不能将分类销量直接换算为单车利润。',157,size=10,leading=18)
chart(['轿车','SUV','MPV'],[('2024 年',[235.8032,178.8787,10.3551],BLUE),('2025 年',[229.0292,208.0625,17.4506],TEAL)],258,'图 5  乘用车销量结构',unit='万辆',h=232,dec=2)
table(['整车快报口径','2024 年（万辆）','2025 年（万辆）','同比'],[
    ['乘用车','425.04','454.54','+6.94%'],['商用车','2.18','5.70','+161.83%'],['整车合计','427.21','460.24','+7.73%'],
],517,[160,125,125,CW-410],size=8.7)
callout('重要口径提醒','原表“产能状况”注释明确按报告期产量列示。本稿不会将该字段当作设计产能，也不会据此制造“产能利用率”图。真实产能口径未核验，保留待补。',669,tone=GOLD)
source('来源：S1，物理页 13-14。整车快报销量与营业收入口径不同；数据统一换算为万辆。')
finish()

# 09 - Quarterly performance.
newpage('03 / 季度节奏','四季度回升，不等于全年趋势反转','采用年报列示的单季度数据，而不是累计值相减与单季值混用。')
para('第四季度收入为 2,376.99 亿元、归母净利润为 92.86 亿元，分别较第三季度增加 <b>21.91% 和 18.70%</b>。归母净利率约 3.91%，略低于第三季度约 4.01%，说明收入与利润同步回升并不意味着单季盈利效率改善。四季度扣非利润环比增长约 29.96%，还需结合非经常性项目和费用确认节奏理解，不能直接外推下一年度。',157,size=10,leading=18)
chart(['Q1','Q2','Q3','Q4'],[('营业收入',QR,TEAL)],254,'图 6  单季度营业收入',h=207,kind='line')
chart(['Q1','Q2','Q3','Q4'],[('归母净利润',QP,BLUE),('扣非归母净利润',QA_PROFIT,PURPLE)],478,'图 7  单季度归母与扣非盈利',h=219)
para('<b>验证重点：</b>季度间利润率变化，可能同时受到产品结构、费用确认和交付节奏影响。次期数据和分季度成本明细尚未纳入本稿，不外推未来季度业绩。',710,size=9.3,leading=16)
source('来源：S1，物理页 11-12。Q1-Q4 均为单季度金额，单位：亿元。')
finish()

# 10 - Gross profit.
newpage('04 / 盈利驱动','收入增长，成本增长更快','先呈现能复算的毛利变化，再讨论可能驱动，避免用叙事替代利润桥。')
para(f'收入同比增长 3.46%，营业成本同比增长 5.63%。按合并收入与成本计算，毛利率由 {GM[0]:.2f}% 降至 {GM[1]:.2f}%。毛利额的变化说明收入规模增加并未直接转化为更多毛利；但价格、产品组合、材料与制造费用各自贡献仍需进一步拆分。',157,size=10,leading=18)
chart(['2024','2025'],[('合并毛利率',GM,TEAL),('合并净利率',NPM,BLUE),('扣非归母净利率',ADJM,PURPLE)],259,'图 8  盈利质量与利润率',unit='%',h=220,dec=2,ylimits=(0,25,5),kind='line')
table(['毛利桥（亿元）','金额','计算或解释'],[
    ['2024 年毛利',f'{REV[1]-COST[0]:,.2f}','收入减营业成本'],
    ['收入增加','+268.63','2025 收入减 2024 收入'],
    ['营业成本增加','-352.59','成本增加对毛利的负向影响'],
    ['2025 年毛利',f'{REV[2]-COST[1]:,.2f}','上述变动后的结果'],
],503,[170,100,CW-270],size=8.7,highlight=3)
para('<b>分析：</b>这是会计金额的毛利桥，不是价格、销量或成本项目的因果分解。要进一步解释来源，应取得可匹配的分产品量价与成本明细。',714,size=9.2,leading=15.5)
source('来源：S1，物理页 11、29-30；R1，营业成本与毛利率。桥表为本稿复算，不含未核验驱动假设。')
finish()

# 11 - Profit quality.
newpage('04 / 利润质量','扣非利润下降，非经常性贡献需分开看','归母、合并与扣非口径各有用途，不能替换分子后仍使用同一指标名称。')
para('归母净利润下降 18.97%，扣非归母净利润下降 20.38%。非经常性损益净额由 32.71 亿元略降至 31.73 亿元，但占归母利润的比例从约 8.13% 升至 <b>9.73%</b>。占比上升主要伴随盈利分母收缩，不能简单理解为一次性收益大增。经营盈利的压力需要回到业务毛利与费用，而非只用非经常性占比解释。',157,size=10,leading=18)
donut([('扣非归母净利润',294.45703,TEAL),('非经常性损益净额',31.73319,GOLD)],258,'图 9  归母盈利的组成（亿元）','9.73%','非经常性占归母')
table(['观察指标','2024 年','2025 年','研究含义'],[
    ['归母净利润（亿元）','402.54','326.19','股东口径盈利'],
    ['扣非归母净利润（亿元）','369.83','294.46','扣除非经常性项目后'],
    ['非经常性损益（亿元）','32.71','31.73','税后归母净额'],
    ['扣非归母净利率','4.76%','3.66%','不等同于合并净利率'],
],512,[156,80,80,CW-316],size=8.5)
para('<b>核查路径：</b>政府补助、资产处置及金融资产相关损益应逐项核对，不把投资活动现金流与投资收益直接一一对应。',716,size=9.2,leading=15.5)
source('来源：S1，物理页 11-12；R1，非经常性损益占比与扣非归母净利率。')
finish()

# 12 - Expenses.
newpage('04 / 费用与经营杠杆','费用率变化，是毛利之后的第二层检验','费用率使用集团收入作分母；不用于推算单一车型或单个业务的成本。')
para('销售、管理及研发费用合计较上年增加约 <b>84.38 亿元</b>，与毛利额减少约 83.96 亿元共同形成盈利压力。财务费用由 12.16 亿元转为 -6.49 亿元，形成约 18.66 亿元的费用端改善，公司解释涉及汇兑收益。上述项目解释了部分利润变化，但并非完整的税后归母利润桥，仍需考虑其他损益、税费和少数股东。',157,size=10,leading=18)
exp24=[240.85317,186.44661,531.94745];exp25=[261.84568,201.99944,579.78105]
chart(['销售费用率','管理费用率','研发费用率'],[('2024 年',[v/REV[1]*100 for v in exp24],BLUE),('2025 年',[v/REV[2]*100 for v in exp25],TEAL)],258,'图 10  主要费用率对比',unit='%',h=231,dec=2)
table(['费用项目（亿元）','2024 年','2025 年','公司披露的变化线索'],[
    ['销售费用','240.85','261.85','广告展览费增加'],['管理费用','186.45','202.00','薪酬及折旧摊销增加'],
    ['研发费用','531.95','579.78','折旧摊销、检测与物耗增加'],['财务费用','12.16','-6.49','汇兑损益变化'],
],514,[130,78,78,CW-286],size=8.5)
para('<b>研究问题：</b>费用投入能否带来后续销售与效率提升，应以产出与现金验证，而不是仅因投入增加就判断竞争力提升。',716,size=9.3,leading=16)
source('来源：S1，物理页 32；费用率=对应费用÷集团营业收入。公司原因说明为归纳，不是独立因果验证。')
finish()

# 13 - CFO.
newpage('05 / 现金兑现','经营现金流的变化，比利润更明显','绝对现金创造能力与相对现金转化效率需要同时观察，不能只展示其中一个指标。')
para('经营活动现金流净额由 1,334.54 亿元降至 <b>591.36 亿元</b>。经营现金流入增加约 469.22 亿元，但流出增加约 1,212.41 亿元，流出增量明显更大。公司将净额下降主要归因于购买商品、接受劳务支付现金增加。供应链付款、采购安排与营运资金是后续取证重点，不能仅凭现金下降认定销售回款出了问题。',157,size=10,leading=18)
chart(['2023','2024','2025'],[('经营活动现金流净额',CFO,TEAL)],258,'图 11  经营现金流的三年变化',h=222)
table(['现金活动（亿元）','2024 年','2025 年','同比'],[
    ['经营现金流入','8,148.18','8,617.40','+5.76%'],
    ['经营现金流出','6,813.64','8,026.05','+17.79%'],
    ['经营现金净流量','1,334.54','591.36','-55.69%'],
],507,[172,105,105,CW-382],size=8.7,highlight=2)
callout('下一步取证','要核实现金流出增加是采购节奏、付款条件、业务扩张还是其他因素，应对照现金流补充资料与应付项目附注。本稿不把单一年度波动认定为回款恶化。',670,tone=BLUE)
source('来源：S1，物理页 11、40；经营流入、流出与净额在同一年度口径下比较。')
finish()

# 14 - CFO/profit and free cash.
newpage('05 / 利润与自由现金','现金利润比为正，不能掩盖投入缺口','现金利润比与简化自由现金流回答不同问题，应当并列而非相互替代。')
para(f'本期现金利润比为 {metric("cash_to_consolidated_profit"):.2f} 倍，说明经营现金净额高于合并净利润；但购建长期资产现金支出更高，简化自由现金流为负。由此可以讨论投资与融资安排，不能直接推断流动性危机，也不能因现金利润比大于一而认定不存在资金约束。',157,size=10,leading=18)
chart(['2024','2025'],[('经营现金流',CFO[1:],TEAL),('购建长期资产支出',CAPEX,BLUE)],260,'图 12  经营现金与长期资产现金支出',h=230)
table(['指标','2024 年','2025 年','定义'],[
    ['现金利润比',f'{CFO[1]/NP[0]:.2f} 倍',f'{CFO[2]/NP[1]:.2f} 倍','经营现金净额÷合并净利润'],
    ['简化自由现金流',f'{FCF[0]:,.2f}',f'{FCF[1]:,.2f}','经营现金净额减购建长期资产现金支出'],
],514,[115,90,90,CW-295],size=8.5)
callout('口径限制','简化自由现金流不是完整估值自由现金流，不包含全部并购、金融资产投资、租赁及非现金购建。不能用它直接替代企业价值模型中的 FCFF。',658,tone=GOLD)
source('来源：R1，合并净利润、购建长期资产现金支出及已计算指标；S1，物理页 40。表内现金金额：亿元。')
finish()

# 15 - Quarter cash.
newpage('05 / 季度现金','季度现金波动，需要与交付节奏对照','这里展示单季金额，不把累计现金流与单季利润并列。')
para('二季度贡献全年约 <b>39.32%</b> 的经营现金净额，高于该季度约 24.99% 的收入占比；四季度经营现金同比口径未在本稿展开，但环比第三季度增加约 102.95%。现金的季度分布与收入并不一致，提示付款和结算节奏值得研究。单季经营现金除收入的比例仅用于描述现金净创造，不是销售回款率。',157,size=10,leading=18)
chart(['Q1','Q2','Q3','Q4'],[('经营现金流净额',QC,TEAL),('归母净利润',QP,BLUE)],258,'图 13  季度现金与股东盈利',h=238)
table(['2025 年单季度','收入（亿元）','经营现金（亿元）','经营现金÷收入'],[
    [f'Q{i+1}',f'{QR[i]:,.2f}',f'{QC[i]:,.2f}',f'{QC[i]/QR[i]*100:.2f}%'] for i in range(4)
],521,[135,118,128,CW-381],size=8.5)
para('<b>指标说明：</b>表中经营现金÷收入只描述同季金额比例，不是应收账款回收率，也不反映税费、采购付款和所有结算安排。',714,size=9.3,leading=16)
source('来源：S1，物理页 11-12；季度比例由同一季度披露金额复算。')
finish()

# 16 - Capital.
newpage('06 / 资本投入','资产扩张之后，需要验证投入产出','固定资产、在建工程和现金支出分属不同会计维度，不能相互当作同一项目的投资进度。')
para('购建长期资产现金支出由 973.60 亿元增至 <b>1,568.08 亿元</b>，增加约 594.48 亿元；在建工程年末余额较年初增加约 283.39 亿元。资产投入明显加快，而本期集团收入增长 3.46%，投入与收入兑现存在时间差。后续应验证转固、交付和现金回报，不能把在建工程余额增加直接等同于当年资本开支。',157,size=10,leading=18)
chart(['2024','2025'],[('固定资产',[2622.87302,2927.75929],TEAL),('在建工程',[199.54343,482.93781],BLUE)],258,'图 14  长期经营资产的构成',h=228)
table(['投入观察','2024 年末 / 年度','2025 年末 / 年度','不能直接代表'],[
    ['在建工程（亿元）','199.54','482.94','当年全部资本开支'],
    ['固定资产（亿元）','2,622.87','2,927.76','真实设计产能'],
    ['购建现金支出（亿元）',f'{CAPEX[0]:,.2f}',f'{CAPEX[1]:,.2f}','仅新增项目金额'],
    ['固定资产及在建占总资产','36.03%','38.59%','产能利用率或投资回报'],
],512,[122,116,116,CW-354],size=8.5)
para('<b>验证顺序：</b>项目清单与预算 → 转固时间 → 利用与交付 → 毛利、折旧和经营现金。不因“扩产”一词直接作正面或负面判断。',715,size=9.3,leading=16)
source('来源：S1，物理页 41；R1，购建长期资产现金支出、资产占比。2024 占比由同期余额复算。')
finish()

# 17 - Working capital.
newpage('06 / 营运资金','存货与应收变化，不宜合并成一个故事','资产构成表使用账面净额；与风险规则使用的账面余额、账龄和准备口径分开。')
para('存货账面价值增长约 <b>19.29%</b>，快于收入增速；应收账款账面价值则下降约 40.60%，两者并不同向。合同负债增加约 17.70%，提供了另一项经营余额观察。库存消化、应收结算和客户预付款需要合并研究，尚不能只用一项余额判断营运资金效率；本页也不将期末净额变化直接认定为现金流入流出。',157,size=10,leading=18)
chart(['存货账面价值','应收账款账面价值','合同负债'],[('2024 年末',[1160.36237,622.98988,437.29585],BLUE),('2025 年末',[1384.20580,370.04685,514.71055],TEAL)],258,'图 15  经营性余额并不同向变化',h=235)
two_notes(('库存如何验证','需要取得原材料、在产品、库存商品和海外在途的构成，观察库存增加与销售、交付及产品迭代是否匹配。净额增加不能独立证明积压。'),
    ('应收如何验证','需要区分账面余额、准备与账龄，检查票据、应收款项融资以及期后回款。账面净额下降不能直接等同于全部以现金收回。'),519)
callout('复用系统成果，但不混用口径',f'系统已计算总体存货周转 {metric("inventory_days"):.2f} 天、近似应收周转 {metric("receivables_days_approx"):.2f} 天。本页净额图仅作余额观察，不用于重新替换引擎的周转计算输入。',673,tone=BLUE)
source('来源：S1，物理页 41；R1，周转指标。余额图为年末数，单位：亿元。')
finish()

# 18 - Funding.
newpage('06 / 资金结构','经营、投资与融资需要放在同一张图里','资金来源不只来自利润；股权融资、借款及债券与经营创造的现金性质不同。')
para('2025 年经营现金净流入 591.36 亿元，投资现金净流出 1,974.63 亿元，筹资现金净流入 <b>1,046.14 亿元</b>。融资补充是本期资金安排的重要组成，公司解释涉及 H 股配售、借款和债券。短期、长期借款年末余额均增加，需区分股权与债务融资性质。三类现金净额之和与最终现金净增额还可能存在汇率等差异。',157,size=10,leading=18)
chart(['经营活动','投资活动','筹资活动'],[('2024 年',[1334.53873,-1290.82282,-102.67547],BLUE),('2025 年',[591.35544,-1974.63109,1046.13870],TEAL)],258,'图 16  三类现金活动的年度净流量',h=234)
table(['年末余额（亿元）','2024 年末','2025 年末','本页解读'],[
    ['货币资金','1,027.39','754.25','不等于现金等价物余额'],['短期借款','121.03','384.85','需结合到期结构'],
    ['长期借款','82.58','607.06','不能单独推断资金紧张'],['归母净资产','1,852.51','2,462.75','不等于合并全部权益'],
],518,[143,94,94,CW-331],size=8.5)
para('<b>尚待补充：</b>受限资金、债务到期时间、利率及担保条款未作完整核验，因此本稿不计算净负债率、不判断短期偿债覆盖是否充分。',716,size=9.2,leading=15.5)
source('来源：S1，物理页 11、40-41；流量与余额明确分列，金额均为亿元。')
finish()

# 19 - Research intensity.
newpage('06 / 研发投入','费用化与资本化，需要同时看','研发总投入、研发费用和开发支出余额不是同一个概念；不要只挑其中一个指标叙述技术投入。')
para('研发总投入增加至 <b>634.41 亿元</b>，较上年增加约 92.80 亿元。其中，资本化金额增加约 44.97 亿元，占总投入增量约 48.46%；费用化投入增加约 47.83 亿元。总投入率升幅高于研发费用率升幅，说明只看利润表费用会遗漏会计去向的变化。研发能否形成产品与制造回报，还需跟踪项目量产、收入贡献及摊销。',157,size=10,leading=18)
stacked(['2024','2025'],[('费用化研发',[531.94745,579.78105],TEAL),('资本化研发',[9.66219,54.63274],GOLD)],258,'图 17  研发总投入的两种会计去向',h=229)
table(['研发观察','2024 年','2025 年','变化'],[
    ['总投入（亿元）','541.61','634.41','+17.13%'],['总投入占收入','6.97%','7.89%','+0.92 个百分点'],
    ['资本化金额（亿元）','9.66','54.63','+465.43%'],['研发人员（人）','121,598','127,665','+4.99%'],
],515,[163,105,105,CW-373],size=8.5)
para('<b>研究含义：</b>研发增加需要与项目进展、量产导入、研发资产摊销和未来收入形成联系。不能把研发人员增加直接换算为技术领先程度。',715,size=9.3,leading=16)
source('来源：S1，物理页 32、39。研发费用与资本化金额相加等于披露总投入，四舍五入存在尾差。')
finish()

# 20 - Accounting research.
newpage('06 / 会计口径复核','资本化比例上升，先核对依据而非定性','用可见的口径变化提出核查问题，不从资本化增加直接推断利润调节或违规。')
para('资本化研发投入占总投入的比例由 1.78% 升至 8.61%。这一变化值得追踪，但尚不能说明研发确认条件发生变化。应逐项目检查资本化起点、技术可行性、商业化计划及摊销安排，并核对是否存在减值。',157,size=10,leading=18)
chart(['2024','2025'],[('研发资本化比例',[1.78,8.61],GOLD)],258,'图 18  研发资本化比例的变动',unit='%',h=212,dec=2,ylimits=(0,10,2))
table(['需要核查的环节','应取得的证据','不能替代的材料'],[
    ['确认条件','分项目资本化条件与起始时点','不能仅看费用率下降或上升'],
    ['投入与转化','项目进展、量产节点及受益期','不能以技术宣传替代验收'],
    ['后续计量','摊销政策、剩余账面额及减值','不能只看当年新增金额'],
    ['利润影响','费用、摊销与税务影响的完整桥表','不能假设全部新增金额等额增厚净利润'],
],497,[91,203,CW-294],size=8.6)
callout('数据缺口，不填虚构结论','本稿尚未建立逐项目开发支出与摊销明细。相关栏目保留为待核查，不给出“激进”或“保守”等缺乏完整依据的标签。',680,tone=GOLD)
source('来源：S1，物理页 39-40。研发资本化比例为公司披露值；核查框架为本稿提出的方法。')
finish()

# 21 - Global strategy.
newpage('07 / 战略与竞争基础','海外布局的价值，需要穿过投入期来验证','公司披露的全球化进展是研究线索；不能把项目建设、出口量和区域利润混为一谈。')
para('年报经营讨论提及出口增长、海外工厂、运输与渠道布局。财务表中境外收入与毛利率增加，为进一步研究提供依据；但集团区域收入并非单一工厂的产出，尚不能据此评价海外每个项目的回报。',157,size=10,leading=18)
flow([('本地化适配','产品与市场需求匹配'),('制造与物流','建设、爬坡与交付'),('渠道与售后','终端销售与服务投入'),('利润与回款','毛利、费用与现金验证')],259,126)
table(['公司披露的推进方向','可能影响的财务环节','需要验证的结果'],[
    ['海外制造与运营','固定资产、在建工程、折旧','项目投产后的利用与回报'],
    ['运输与渠道建设','物流成本、营销费用、库存','交付效率与终端消化'],
    ['产品适配与品牌拓展','研发、收入结构与毛利','持续订单及真实盈利贡献'],
    ['海外销售扩大','应收、结算货币与税费','现金回收与风险管理'],
],416,[148,162,CW-310],size=8.7)
callout('反向检验','即使境外收入保持增长，如果资产与营运资金占用增长更快，投资回报也未必改善。应将“扩张”与“兑现”分成两个跟踪问题。',674,tone=BLUE)
source('来源：S1，物理页 25、29-30、41。流程与影响路径为条件性分析，不是已验证的项目因果结论。')
finish()

# 22 - Risks.
newpage('07 / 风险与反证','把风险写成传导链，而不是泛泛罗列','风险表不打未经估计的概率分，不把系统规则触发当作损失已经发生。')
para('研究风险的价值在于指出哪一环可能偏离、通过什么渠道影响财务，以及什么资料能证实或削弱担忧。以下为基于本稿观察提出的核查框架，不是对公司已经发生风险事件的认定。',157,size=10,leading=18)
table(['观察主题','可能的传导路径','跟踪证据'],[
    ['价格与产品结构','售价或产品组合变化 → 毛利承压 → 利润与现金','分产品收入成本、促销与费用'],
    ['境外扩张','建设与爬坡 → 资产及库存占用 → 回报兑现延后','项目进度、地区毛利、经营现金'],
    ['研发转化','研发增加 → 量产或商业化滞后 → 费用、摊销与减值','分项目进展、受益期与减值测试'],
    ['营运资金','采购与结算变化 → 现金流出增加 → 融资需求','应付与票据、现金流补充资料'],
    ['库存与迭代','产品更新 → 库存消化放慢 → 跌价与现金占用','库存构成、库龄与准备计提'],
],256,[85,224,CW-309],size=9)
two_notes(('什么会支持改善','分部毛利率企稳；经营现金恢复；投资强度与收入兑现匹配；项目商业化证据更清楚。需要组合观察，不依赖单一信号。'),
    ('什么会要求重估','分部毛利继续收窄；库存和资金投入增加却未带来交付与回款；研发资产或项目出现新的减值证据。'),606)
source('来源：本稿基于 S1 的条件性分析；非风险概率模型，不提供风险评分、信用评级或事件认定。')
finish()

# 23 - Sensitivity, explicitly synthetic assumptions not company forecast.
newpage('07 / 条件与敏感性','让假设透明，而不是制造业绩预测','假设演示：只观察收入与毛利率组合对毛利额的机械影响，不预测净利润、股价或估值。')
para('以本期收入为基准，设置不同的假设收入变化与假设毛利率，观察毛利额如何变化。其用途是识别研究变量的敏感性，不代表这些组合会发生，也不包含费用、税费、少数股东和资本结构等完整盈利模型。',157,size=10,leading=18)
rect(M,254,CW,38,SAND,r=5);text(M+13,266,'假设演示 / 所有行列条件均为设计输入，不是公司指引或盈利预测',9.1,GOLD,True)
rates=[15,17,19,21];growth=[-10,0,10]
left=M+100;top=328;cellw=(CW-100)/4;cellh=68
text(M,309,'毛利额：亿元',8.5,MUTED)
for j,r in enumerate(rates):text(left+cellw*(j+.5),307,f'毛利率 {r}%',9,NAVY,True,align='center')
for i,g in enumerate(growth):
    text(M+4,top+i*cellh+24,f'收入 {g:+d}%',9,INK,True)
    for j,r in enumerate(rates):
        value=REV[2]*(1+g/100)*r/100
        t=(i+j)/(len(growth)+len(rates)-2)
        fill=Color(.93-.68*t,.97-.44*t,.96-.46*t)
        c.setFillColor(fill);c.rect(left+j*cellw,H-top-(i+1)*cellh,cellw-3,cellh-3,fill=1,stroke=0)
        text(left+cellw*(j+.5),top+i*cellh+24,f'{value:,.1f}',13,'#FFFFFF' if t>.55 else NAVY,True,align='center')
callout('可以得出的结论','在该简化关系中，收入与毛利率同时决定毛利额。应进一步研究毛利变化如何穿过费用、税费与资本投入，不能将表中毛利额直接改名为净利润或自由现金流。',568)
para('<b>仍然保留的占位栏目：</b>销量与单车经济性、分业务费用、税率、资本开支与营运资金假设尚未建立完整预测模型，因此目标价、估值区间及未来年度 EPS 均不填数。',680,size=9.5,leading=17)
source('演示公式：毛利额=2025 年收入×（1+假设收入变化）×假设毛利率。基准收入来自 S1，第 11 页。')
finish()

# 24 - Tracking dashboard.
newpage('07 / 后续验证','把报告结论变成可执行的跟踪清单','当前已知、下一步要看什么、需要什么证据，三者分开。')
para('有价值的研究不只给出当期判断，还应告诉读者未来如何更新判断。以下清单将本稿核心观察转化为取证任务。它不设没有模型支持的精确阈值，也不把单次变动自动解释为趋势。',157,size=10,leading=18)
table(['研究问题','本期已知的锚点','下次披露应核对什么'],[
    ['境外增长能否兑现','境外收入占比 38.65%','地区毛利、现金回收与项目投入'],
    ['利润压力是否缓和','合并毛利率 17.74%','业务毛利与收入结构的同步变化'],
    ['现金能否覆盖投入','简化自由现金流为负','经营流入流出、资本开支与融资'],
    ['存货能否顺利消化','存货账面价值 1,384.21 亿元','库龄、产品构成、跌价与转销'],
    ['研发投入能否转化','总投入 634.41 亿元','量产与商业化、摊销和减值'],
    ['季度回升能否持续','四季度收入和归母环比上升','次期交付、利润率、现金与费用'],
],256,[113,161,CW-274],size=9)
callout('更新判断的方法','先检查新资料与本稿是否同公司、同期间和同口径，再更新图表与结论。对数据差异保留原值与来源，不为了延续原观点而挑选有利数据。',650,tone=BLUE)
source('来源：S1 与 R1；本页为研究跟踪框架，不是投资操作清单。')
finish()

# 25 - Methods.
newpage('附录 A / 指标与方法','同一个名字，必须对应同一个计算口径','复用系统的确定性计算，同时把样稿新增复算与假设演示明确分开。')
table(['指标','本稿口径','使用限制'],[
    ['合并毛利率','（集团收入－营业成本）÷收入','不替代分车型毛利率'],
    ['扣非归母净利率','扣非归母净利润÷集团收入','不等同于合并净利率'],
    ['现金利润比','经营现金净额÷合并净利润','不以归母利润替换分母'],
    ['简化自由现金流','经营现金净额－购建长期资产现金支出','不是完整估值自由现金流'],
    ['研发资本化比例','资本化研发投入÷研发总投入','不以期末开发支出余额替换'],
    ['年末资产占比','对应年末余额÷年末总资产','不等于产能利用率'],
    ['敏感性毛利额','基准收入×收入变化假设×毛利率假设','纯条件演示，不是盈利预测'],
],158,[108,219,CW-327],size=8.8)
heading('已有成果的使用方式',569)
para('本地比亚迪分析快照包含 64 项事实与 25 项指标，本样稿从中复用营业成本、合并净利润、资本开支、毛利率、现金利润比、自由现金流和周转等项目；再以年报的业务与地区表补充结构分析。没有重新启动分析任务，也没有修改旧报告。',599,size=10,leading=18)
callout('比较口径','三年主要数据统一使用 2025 年报列示的比较数；本稿未实施完整跨版本重述核对。新绘图延续系统的颜色语言，但不是把其他公司的历史图截图贴入。',675,tone=GOLD)
source('R1：本地任务 8f0ad2e8…，2025 年度分析快照；源 PDF 哈希与 S1 匹配。详细复用记录随生成脚本留存。')
finish()

# 26 - Provenance and boundary.
newpage('附录 B / 资料与边界','让来源可查，让样稿与正式报告分开','资料核验以本地年报文件和已保存分析结果为限；未调用外部模型或新增行业数据库。')
table(['编号','资料','本稿使用范围'],[
    ['S1','比亚迪股份有限公司 2025 年年度报告<br/>公告号 1225045351，268 页','第 11-15、25、29-32、39-41 页等'],
    ['R1','系统已保存的比亚迪 2025 分析结果<br/>任务 8f0ad2e8599d47af805bc337d6ea5284','事实、公式结果与来源绑定'],
    ['演示','本稿的条件分析与敏感性矩阵','明确标注的假设；非真实预测'],
],158,[43,287,CW-330],size=8.5)
para('S1 原始披露链接：<link href="https://static.cninfo.com.cn/finalpage/2026-03-28/1225045351.PDF" color="#0C8580">巨潮资讯 · 比亚迪 2025 年年度报告</link><br/>本地 SHA-256：710d8b2e08e37ba521f1c4071d2c3e2369d3cadabc21a45073215d9f72138b0b',349,size=8,leading=14,color=MUTED)
heading('模板只提供研究组织与视觉参考',411)
para('参考用户提供的行业综述、公司业绩点评、公司深度研究与 CFA Research Challenge 获奖报告的组织方式：观点先行、图表支撑、财务与业务连贯、风险可验证。五个输入文件中有两份内容重复，实际为四份不同参考。没有复制它们的企业数据、预测、图表或原文，也不冒用券商名称与分析师身份。',441,size=9.7,leading=17)
heading('这份样稿已经展示什么',548)
para('完整彩色封面、章节导航、重点语句、18 组编号图形、结构流程、彩色数据表、条件矩阵、页眉页脚与来源注释。正文可选择、可搜索；图形采用矢量绘制。分析正文为第 3-24 页，不依赖放大字号或空白页凑足二十页。',578,size=9.7,leading=17)
callout('尚未完成与使用限制','这是一份人工组织、程序排版的独立效果样稿，尚未接入工作台自动生成。未作独立行业核验、完整估值、信用评估或法律审查；研究推断仍需人工复核，不应据此直接进行投资交易。',660,tone=GOLD)
source('样稿日期：2026-10-17。所有金额按图表注明单位；小数四舍五入可能形成尾差。')
finish()

assert PAGE==26,PAGE
c.save()
bad=[item for item in DRAW if item['page']>1 and item['bottom']>795 and item['top']<800]
assert not bad,('Content overlaps footer',bad)
assert all(item['bottom']<=761 for item in DRAW if item['kind']=='callout'), 'Callout overlaps source notes'
(QA/'build-audit.json').write_text(json.dumps({'pdf':str(PDF),'pages':PAGE,'task_id':task['id'],
    'sources':material['sources'],'reuse':AUDIT,'drawing_bounds':DRAW},ensure_ascii=False,indent=2),encoding='utf-8')
print(str(PDF));print('Pages:',PAGE,'Bytes:',PDF.stat().st_size)
