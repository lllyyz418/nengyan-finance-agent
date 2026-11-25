"""Structured research prose with server-owned values and evidence references."""
import json
import os
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

PROMPT_VERSION='evidence-research-7-analyst'
TITLES={'overview':'核心判断','business':'业务与竞争基础','profit':'增长与盈利驱动','cash':'现金兑现与营运资金',
        'capital':'投入与资金约束','history':'同口径历史观察','peers':'所选同行观察','outlook':'经营计划与风险验证','followup':'后续核查重点'}
TOKEN=re.compile(r'\{\{(m\d{4})\}\}')


class Claim(BaseModel):
    model_config=ConfigDict(extra='forbid')
    headline:str=Field(min_length=4,max_length=80)
    interpretation:str=Field(min_length=20,max_length=700)
    evidence_ids:list[str]=Field(min_length=1,max_length=8)
    observation_ids:list[str]=Field(min_length=1,max_length=6)
    alternatives:list[str]=Field(min_length=1,max_length=3)
    checks:list[str]=Field(min_length=1,max_length=4)
    limitation:str=Field(min_length=5,max_length=250)
    support_if:str|None=Field(default=None,min_length=8,max_length=250)
    weaken_if:str|None=Field(default=None,min_length=8,max_length=250)


class Section(BaseModel):
    model_config=ConfigDict(extra='forbid')
    key:Literal['overview','business','profit','cash','capital','history','peers','outlook','followup']
    claims:list[Claim]=Field(min_length=1,max_length=3)


class Draft(BaseModel):
    model_config=ConfigDict(extra='forbid')
    sections:list[Section]=Field(min_length=2,max_length=9)


from research_style import SYSTEM


def _validate_strict(raw,packet):
    draft=Draft.model_validate(raw)
    keys=[section.key for section in draft.sections]
    if len(set(keys))!=len(keys) or 'overview' not in keys:
        raise ValueError('研究章节重复或缺少核心判断')
    roles={item['role'] for item in packet['coverage'] if item['measures']}
    if 'history' in keys and '历年同期' not in roles or 'peers' in keys and '所选同行' not in roles:
        raise ValueError('研究解读使用了尚未取得的比较资料')
    registry=packet['registry']
    observations={item['id']:item for item in packet['observations']}
    output=[]
    for section in draft.sections:
        claims=[]
        # Report length is a presentation decision, not a reason to discard an
        # otherwise useful model response. Keep key takeaways plus the leading
        # observation in each detailed section; all retained text is validated.
        for claim in section.claims[:3 if section.key=='overview' else 2]:
            ids=list(dict.fromkeys(claim.evidence_ids))
            if any(identifier not in observations for identifier in claim.observation_ids):raise ValueError('研究引用了未提供的事实观察')
            selected=[observations[identifier] for identifier in claim.observation_ids]
            allowed={identifier for item in selected for identifier in item['evidence_ids']}
            if set(ids)!=allowed:raise ValueError('本条证据必须与所选事实观察一致，不能借用其他指标')
            if section.key not in {'overview','followup','business'} and any(item['topic'] not in {section.key,'business'} and item.get('kind')!='disclosure' for item in selected):
                raise ValueError('研究章节与所选事实观察不对应')
            if section.key=='business' and not any(item.get('kind')=='disclosure' for item in selected):raise ValueError('业务判断需要业务原文')
            if any(identifier not in registry for identifier in ids):raise ValueError('研究引用未提供的证据')
            # Follow-up questions may name another available metric, but may
            # not smuggle its uncited numeric value into this finding.
            claim.checks=[TOKEN.sub(lambda match:registry[match[1]]['label']
                if match[1] in registry and match[1] not in ids else match[0],text) for text in claim.checks]
            fields=[claim.headline,claim.interpretation,claim.limitation,*claim.alternatives,*claim.checks,*filter(None,(claim.support_if,claim.weaken_if))]
            known_terms=set()
            for identifier in ids:
                point=registry[identifier]
                known_terms.update((point['label'],point.get('period_label','')))
                quarter=re.search(r'Q([1-4])$',point['period'])
                if quarter:known_terms.add('第'+'一二三四'[int(quarter[1])-1]+'季度')
                if 'over_1y' in point.get('code',''):known_terms.add('一年以上')
            if any('一年以上' in item['fact'] for item in selected):known_terms.add('一年以上')
            for field in fields:
                without=TOKEN.sub('',field)
                if re.search(r'https?://|javascript:|file://|<[/!a-zA-Z]',without):
                    raise ValueError('研究文字不得加入未经核验的链接或可执行内容')
                checked=without.replace('未提取不等于公司未披露','系统提取与披露范围不同').replace('不必然','不一定').replace('并非必然','并非一定')
                condition_field=field in [*claim.alternatives,*claim.checks,claim.support_if,claim.weaken_if]
                conditional_cause=re.sub(r'(?:若|如果|可能)[^。；]*主要来自','有条件的解释',checked)
                if re.search(r'未(?:单独|明确|充分|详细|分别)?披露|直接原因|保证收益|会计估计上未同步增加风险缓冲|折旧参数一致|已匹配类别',checked) or ('主要来自' in conditional_cause and not condition_field):
                    raise ValueError('研究解释超出提取范围或把推断当作确定成因')
                number_check=without
                for term in sorted(filter(None,known_terms),key=len,reverse=True):number_check=number_check.replace(term,'')
                if condition_field:number_check=re.sub(r'(?:第)?[一二三四]季度|未来[一两三四五]年','后续期间',number_check)
                invalid_number=re.search(r'[\d０-９%％]|[零一二三四五六七八九十百千万亿两]+(?:年|季|月|倍|元|天|百分点)',number_check)
                if (invalid_number
                    or '{{' in without or '}}' in without or len(field)>900):
                    raise ValueError('研究文字包含未经引擎引用的数值；章节 '+section.key+'，问题片段 '+(invalid_number[0] if invalid_number else '占位符或长度'))
                if re.search(r'必然|已证实造假|已确认违约|不存在任何风险|建议买入|建议卖出|目标价|行业第一',checked):
                    raise ValueError('研究文字包含超出证据范围的确定性或投资判断')
                if '行业平均' in re.sub(r'(?:不代表|不能代表|不等于|并非|不是|不能视为|不能当作)行业平均','',without):
                    raise ValueError('所选同行不能被称为行业平均')
                if any(identifier not in ids for identifier in TOKEN.findall(field)):
                    raise ValueError('研究数值未列入本条证据')
            points=[registry[identifier] for identifier in ids]
            if any(point.get('kind')=='disclosure' for point in points) and not re.search(r'公司披露|报告(?:提及|说明|披露|介绍)|公司(?:计划|表示|说明|提及)',claim.interpretation):
                raise ValueError('经营原文须明确归属于公司披露，不能作为独立证实的结论')
            if section.key=='history' and not any(point['scope']=='历年同期' for point in points):
                raise ValueError('历史判断没有引用历史资料')
            if section.key=='peers' and not any(point['scope']=='所选同行' for point in points):
                raise ValueError('同行判断没有引用所选同行资料')
            if any(point.get('restated') for point in points) and len({point['period'] for point in points})>1:
                raise ValueError('重述口径待人工核对，不自动生成跨期变化判断')
            if len({point.get('security_code') or point['company'] for point in points})>1:
                if len({(p['code'],p['period'],p['unit'],p['period_basis']) for p in points})!=1:
                    raise ValueError('同行文字混用了指标、期间或单位口径')
            def render(text):return TOKEN.sub(lambda match:registry[match[1]]['display'],text)
            claims.append({'headline':render(claim.headline),'observed_facts':list(dict.fromkeys(item['fact'] for item in selected)),
                'interpretation':render(claim.interpretation),
                'alternatives':[render(text) for text in claim.alternatives],
                'checks':[render(text) for text in claim.checks],'limitation':render(claim.limitation),
                'evidence':[registry[identifier] for identifier in ids],
                **({'support_if':render(claim.support_if)} if claim.support_if else {}),
                **({'weaken_if':render(claim.weaken_if)} if claim.weaken_if else {})})
        output.append({'key':section.key,'title':TITLES[section.key],'claims':claims})
    return {'title':f"{packet['company']} · {packet['period']}研究解读",'sections':output,
            'coverage':packet['coverage'],'limits':packet['limits'],'business_context':packet.get('business_context'),
            'validation_note':'已核对数值占位、引用有效性和基本期间口径；经营解释及因果推断仍需人工复核。'}


def validate_draft(raw,packet):
    """Exclude unsupported findings, but never publish an unsupported takeaway.

    The strict validator is also used on isolated findings with a validated
    overview as their anchor. No model text is repaired by changing its facts.
    """
    draft=Draft.model_validate(raw)
    keys=[section.key for section in draft.sections]
    if len(set(keys))!=len(keys) or 'overview' not in keys:raise ValueError('研究章节重复或缺少核心判断')
    roles={item['role'] for item in packet['coverage'] if item['measures']}
    if 'history' in keys and '历年同期' not in roles or 'peers' in keys and '所选同行' not in roles:
        raise ValueError('研究解读使用了尚未取得的比较资料')
    overview=next(section for section in draft.sections if section.key=='overview')
    valid_overview=[];anchor=None;rejected=[];body=None
    for claim in overview.claims[:3]:
        value=claim.model_dump()
        try:
            trial=_validate_strict({'sections':[{'key':'overview','claims':[value]},
                {'key':'followup','claims':[value]}]},packet)
            valid_overview.append(trial['sections'][0]['claims'][0]);anchor=anchor or value;body=trial
        except ValueError as exc:rejected.append(str(exc))
    if not anchor:raise ValueError('核心判断未通过证据校验：'+(rejected[0] if rejected else '未返回有效观点'))
    output=[{'key':'overview','title':TITLES['overview'],'claims':valid_overview}]
    for section in draft.sections:
        if section.key=='overview':continue
        valid=[]
        for claim in section.claims[:2]:
            try:
                trial=_validate_strict({'sections':[{'key':'overview','claims':[anchor]},
                    {'key':section.key,'claims':[claim.model_dump()]}]},packet)
                valid.extend(trial['sections'][1]['claims'])
            except ValueError as exc:rejected.append(str(exc))
        if valid:output.append({'key':section.key,'title':TITLES[section.key],'claims':valid})
    minimum=4 if packet.get('business_context',{}).get('status')=='available' and len(packet['observations'])>=10 else 2
    if len(output)<minimum:raise ValueError('有效章节不足，需保留有据的经营、盈利、现金和资金主线；逐条修正：'+'；'.join(dict.fromkeys(rejected))[:1200])
    count=0;seen=set();pruned=[]
    for section in output:
        claims=[]
        for claim in section['claims']:
            fingerprint=re.sub(r'\s+','',claim['interpretation'])
            if fingerprint in seen or count>=10:continue
            claims.append(claim);seen.add(fingerprint);count+=1
        if claims:pruned.append({**section,'claims':claims})
    # Legacy responses can repeat a takeaway as a follow-up. Keep compatibility
    # if deduplication would remove the only second chapter, rather than invent prose.
    if len(pruned)>=2:output=pruned
    return {**body,'sections':output,'excluded_findings':len(rejected),
            'validation_note':body['validation_note']+(f' 另有 {len(rejected)} 条未通过校验的观点已排除。' if rejected else '')}


def generate(packet,trace=None):
    from openai import OpenAI
    client=OpenAI(api_key=os.environ['DEEPSEEK_API_KEY'],base_url='https://api.deepseek.com',timeout=120,max_retries=0)
    # Addressability metadata stays server-side. The model receives a bounded
    # research packet, not repeated source URLs, task IDs or full PDF text.
    relevant={identifier for item in packet['observations'] for identifier in item['evidence_ids']}
    material={key:value for key,value in packet.items() if key not in {'risks','business_source'}}
    material['coverage']=[{key:value for key,value in row.items() if key!='task_id'} for row in packet['coverage']]
    material['registry']={identifier:{key:point[key] for key in
        ('id','kind','label','period_label','company','scope','report_type','period_basis','value','unit','restated','excerpt','statement_type') if key in point}
        for identifier,point in packet['registry'].items() if identifier in relevant}
    messages=[{'role':'system','content':SYSTEM+'\nJSON Schema:\n'+json.dumps(Draft.model_json_schema(),ensure_ascii=False)},
              {'role':'user','content':json.dumps(material,ensure_ascii=False)}]
    try:
        for attempt in range(2):
            response=client.chat.completions.create(model=os.getenv('DEEPSEEK_MODEL','').strip() or 'deepseek-flash',
                temperature=.2,max_tokens=9000,response_format={'type':'json_object'},
                extra_body={'thinking':{'type':'disabled'}},messages=messages)
            choice=response.choices[0]
            if trace:trace(attempt,choice.message.content)
            if choice.finish_reason!='stop' or not choice.message.content:raise ValueError('研究解读未完整返回')
            try:return validate_draft(bind_citations(json.loads(choice.message.content),packet),packet)
            except ValueError as exc:
                if attempt:raise
                reason=(json.dumps([{'field':'.'.join(map(str,item['loc'])),'issue':item['msg']}
                                    for item in exc.errors()[:8]],ensure_ascii=False)
                        if isinstance(exc,ValidationError) else 'JSON 格式有误' if isinstance(exc,json.JSONDecodeError) else str(exc))
                messages.extend([{'role':'assistant','content':choice.message.content},
                    {'role':'user','content':'上次结果未通过服务器校验。只修正这些问题，保留有证据的观察，不补造数据。校验反馈：'+reason}])
    finally:client.close()


def bind_citations(raw,packet):
    """Complete citation metadata from selected observations, never repair prose.

    An extra declared known reference is not authority to use its value. Unknown
    IDs and text referencing data outside the chosen observations still fail.
    The strict validator always runs after this deterministic metadata binding.
    """
    import copy
    result=copy.deepcopy(raw);observations={item['id']:item for item in packet['observations']}
    for section in result.get('sections',[]):
        for claim in section.get('claims',[]):
            ids=claim.get('observation_ids') or []
            declared=claim.get('evidence_ids') or []
            if not ids or any(identifier not in observations for identifier in ids):continue
            if any(identifier not in packet['registry'] for identifier in declared):continue
            expected=list(dict.fromkeys(identifier for observation in ids for identifier in observations[observation]['evidence_ids']))
            # A referenced placeholder must already belong to a selected server
            # observation; do not add observations to rescue a model assertion.
            used=TOKEN.findall(json.dumps(claim,ensure_ascii=False))
            if any(identifier not in expected for identifier in used):continue
            if len(expected)<=8:claim['evidence_ids']=expected
    return result


def markdown(body):
    lines=['# '+body['title'],'','> DeepSeek 辅助研究解读，不是审计结论或交易建议。','']
    for section in body['sections']:
        lines+=['## '+section['title'],'']
        for claim in section['claims']:
            lines+=['### '+claim['headline'],'',claim['interpretation'],'']
            lines+=['- 已核对观察：'+text for text in claim.get('observed_facts',[])]
            lines+=['- 数据依据：'+p['company']+' · '+p['label']+' · '+p.get('period_label',p['period'])+'：'+p['display']+'（'+'；'.join(dict.fromkeys(source['label'] for source in p['sources']))+'）' for p in claim['evidence']]
            lines+=['- 其他可能解释：'+text for text in claim['alternatives']]
            lines+=['- 后续核查：'+text for text in claim['checks']]
            if claim.get('support_if'):lines+=['- 支持判断的信号：'+claim['support_if']]
            if claim.get('weaken_if'):lines+=['- 削弱判断的信号：'+claim['weaken_if']]
            lines+=['- 判断边界：'+claim['limitation'],'']
    lines+=['## 数据范围与限制','',*['- '+text for text in body['limits']],body['validation_note']]
    return '\n'.join(lines)
