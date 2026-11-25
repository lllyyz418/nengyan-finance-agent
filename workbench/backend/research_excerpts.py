"""Bounded issuer-report prose extraction. No model, network, or financial math."""
import hashlib
import re

VERSION='issuer-prose-4'
TOPICS={
    'business':('主营业务|主要业务|业务模式|商业模式|产品.*应用|核心竞争力|业务布局|动力电池|储能电池','业务与竞争基础'),
    'profit':('毛利率|盈利能力|产品价格|单位成本|成本控制|售价|量价|收入.*(?:增长|下降).*主要','盈利驱动'),
    'cash':('销售回款|回款.*(?:增加|减少)|营运资金|现金流.*主要|经营活动.*现金.*主要|应收账款.*主要','现金与营运资金'),
    'capital':('产能利用率|资本开支|在建工程|研发投入|研发项目|产能.*建设|投资.*项目','投入与产能'),
    'outlook':('经营计划|发展战略|未来展望|未来发展|公司将|下一年度|未来.*计划','经营计划'),
    'risk':('面临的风险|风险因素|原材料.*风险|市场竞争.*风险|汇率.*风险|可能.*(?:损失|风险)|开发风险|应对措施','风险披露'),
}
UNTRUSTED_INSTRUCTIONS=re.compile(r'忽略.{0,12}(?:指令|提示词)|system\s*prompt|api[_ -]?key|<script|javascript:|执行.{0,8}(?:命令|代码)',re.I)


def paragraphs(text):
    parts=[];buffer=''
    for line in text.splitlines():
        line=line.strip()
        if not line or re.search(r'年度报告|半年度报告|季度报告|^\d+\s*[/／]\s*\d+$',line) and len(line)<90:continue
        if line.isdigit():continue
        # Drop dense table rows before they are joined to the following prose.
        # Financial amounts continue to come from the financial parser, not here.
        if sum(c.isdigit() for c in line)>=8 and sum(c.isdigit() for c in line)/len(line)>.28:
            if len(buffer)>=90:parts.append(buffer[:1100])
            buffer='';continue
        if len(line)<8 and not buffer:continue
        buffer+=line
        if (len(buffer)>=90 and re.search(r'[。；;]$',line)) or len(buffer)>=700:
            cut=max(buffer.rfind(mark,0,1100)+1 for mark in '。；;')
            if cut<90:cut=min(len(buffer),1100)
            parts.append(buffer[:cut]);buffer=buffer[cut:]
    if len(buffer)>=90:parts.append(buffer[:1100])
    return parts


def candidates(text,page):
    if '目录' in text[:160] and (text.count('...')>3 or text.count('…')>5):return []
    found=[]
    for paragraph in paragraphs(text):
        if UNTRUSTED_INSTRUCTIONS.search(paragraph) or len(re.findall(r'[\u4e00-\u9fff]',paragraph))<50:continue
        if re.search(r'承诺函|本承诺|同业竞争|控股股东|实际控制人',paragraph):continue
        if sum(c.isdigit() for c in paragraph)/len(paragraph)>.18:continue
        if not re.search(r'[。；;]',paragraph):continue
        scores={key:len(re.findall(pattern,paragraph)) for key,(pattern,_) in TOPICS.items()}
        topic=max(scores,key=scores.get)
        if not scores[topic]:continue
        # Plans and risks must never be presented as already realized outcomes.
        if re.search(TOPICS['risk'][0],paragraph):topic='risk'
        elif re.search(TOPICS['outlook'][0],paragraph) and re.search(r'将|计划|未来|预计|拟',paragraph):topic='outlook'
        score=scores[topic]+min(paragraph.count('。'),3)
        if topic=='business':
            score+=6*bool(re.search(r'主营业务|主要业务|主要从事|业务模式',paragraph))
            score-=4*bool(re.search(r'根据.*(?:数据|统计)|市占率',paragraph))
        if topic=='cash':score+=5*bool(re.search(r'主要是|主要原因|销售回款',paragraph))
        if topic=='risk':score+=5*bool(re.search(r'风险',paragraph))+4*bool(re.search(r'（\d+）.*风险|面临的风险|风险因素',paragraph))
        if topic=='outlook':score+=4*bool(re.search(r'未来|发展战略|经营计划',paragraph))+4*bool(re.search(r'公司将|公司计划',paragraph))
        found.append({'topic':topic,'section':TOPICS[topic][1],'page':page,'text':paragraph,'score':score})
    return found


def select_excerpts(items,max_chars=9000):
    chosen=[];seen=set();total=0
    for topic in ('business','profit','cash','capital','outlook','risk'):
        for item in sorted((p for p in items if p['topic']==topic),key=lambda p:(-p['score'],p['page'])):
            digest=hashlib.sha256(item['text'].encode()).hexdigest()
            if digest in seen or total+len(item['text'])>max_chars:continue
            chosen.append({key:value for key,value in item.items() if key!='score'});seen.add(digest);total+=len(item['text'])
            if sum(p['topic']==topic for p in chosen)>=2:break
    return chosen


def extract_business(path,version=VERSION,max_pages=80,progress=lambda detail:None):
    import pdfplumber
    if version!=VERSION:raise ValueError('经营原文提取版本不一致')
    items=[];management=False;scanned=0
    with pdfplumber.open(path) as pdf:
        limit=min(len(pdf.pages),max(1,min(max_pages,80)))
        for index in range(limit):
            page=pdf.pages[index]
            try:
                text=page.extract_text() or '';scanned=index+1
                if management and re.search(r'第[一二三四五六七八九十]+节\s*(?:公司治理|重要事项|股份|董事|股东|环境|社会责任)',text):break
                if re.search(r'管理层讨论与分析|经营情况讨论与分析',text) and not '目录' in text[:160]:management=True
                items.extend(candidates(text,index+1))
            finally:page.close()
            progress({'phase':'提取经营讨论原文','page':index+1,'total':limit})
        return {'version':VERSION,'excerpts':select_excerpts(items),'scanned_pages':scanned,'total_pages':len(pdf.pages),
                'scope_note':'仅对报告前部有限页数进行文字提取；未识别的内容不等于公司未披露，不执行 OCR，不视为完整经营尽调。'}
