"""Issuer prose lives only in independent research versions, not financial facts."""
import copy
from research_excerpts import VERSION


def source_info(core,task):
    doc=(((task.get('result') or {}).get('analysis') or {}).get('documents') or [{}])[0]
    try:path=core.evidence_file_path(task,{'document_id':doc.get('document_id')},verify=False)
    except (OSError,ValueError):path=None
    return {'version':VERSION,'available':bool(path),'document_id':doc.get('document_id'),
            'sha256':doc.get('file_sha256'),'enabled':True}


def enrich(core,task,report_id,packet,folder,checkpoint=lambda:None,progress=lambda detail:None):
    from pdf_runtime import extract,digest
    result=copy.deepcopy(packet);source=packet.get('business_source') or {}
    doc=(((task.get('result') or {}).get('analysis') or {}).get('documents') or [{}])[0]
    status={'status':'unavailable','excerpts':0,'message':'未取得可核验的经营原文，本版仅根据已保存财务数据解读。'}
    try:
        checkpoint()
        path=core.evidence_file_path(task,{'document_id':source.get('document_id')})
        if not source.get('available') or not source.get('sha256') or not path:
            result['business_context']=status;return result
        if doc.get('document_id')!=source['document_id'] or digest(path)!=source['sha256']:
            raise ValueError('经营原文与冻结的财报版本不一致')
        # Use the existing operation/hash-keyed PDF cache rather than nesting a
        # second cache beneath long per-version paths on Windows.
        extracted=extract('research_business',path,core.PARSED_DIR,version=VERSION,
                          checkpoint=checkpoint,progress=progress,max_seconds=100,idle_seconds=45)
        count=0
        for excerpt in extracted.get('excerpts') or []:
            checkpoint();count+=1
            identifier=f'm{len(result["registry"])+1:04d}'
            fact_id=f'research:{report_id}:b{count:04d}'
            statement='公司经营计划，尚非实现结果' if excerpt['topic']=='outlook' else '公司披露的风险，尚非已发生损失' if excerpt['topic']=='risk' else '公司报告中的经营说明'
            item={'id':identifier,'kind':'disclosure','code':'issuer_'+excerpt['topic'],'label':excerpt['section'],
                  'company':packet['company'],'security_code':doc.get('security_code'),'period':str(doc.get('report_year')),
                  'period_label':packet['period'],'scope':'本期经营原文','report_type':doc.get('report_type'),
                  'period_basis':'disclosure','restated':False,'unit':'文本','value':statement,'display':statement,
                  'excerpt':excerpt['text'],'statement_type':excerpt['topic'],
                  'sources':[{'task_id':task['id'],'fact_id':fact_id,'page':excerpt['page'],
                              'label':f'经营原文第 {excerpt["page"]} 页'}],
                  'source_evidence':{'document_id':doc['document_id'],'file_hash':source['sha256'],
                      'file':doc.get('file_name') or path.name,'page':excerpt['page'],'section':excerpt['section'],
                      'excerpt':excerpt['text'],'statement_type':statement}}
            result['registry'][identifier]=item
            topic=excerpt['topic'] if excerpt['topic'] in {'profit','cash','capital','business','outlook'} else 'outlook'
            result['observations'].append({'id':f'o{len(result["observations"])+1:03d}',
                'headline':excerpt['section']+'原文核对','fact':statement+'：'+excerpt['text'],
                'evidence_ids':[identifier],'topic':topic,'kind':'disclosure'})
        result['business_context']={'status':'available' if count else 'unavailable','excerpts':count,
            'message':f'已补充 {count} 段可定位经营原文。' if count else status['message'],
            'scanned_pages':extracted.get('scanned_pages'),'total_pages':extracted.get('total_pages'),
            'scope_note':extracted.get('scope_note')}
        result['limits']=[line for line in result['limits'] if '尚未系统提取产品' not in line]
        result['limits'].append('经营说明来自本次财报的有限文字节选；计划、风险及管理层解释属于公司披露，不是独立验证的经营事实。未引入实时行业、价格或订单数据库。')
        return result
    except (ValueError,OSError,RuntimeError,TimeoutError) as exc:
        checkpoint()
        result['business_context']={**status,'reason_type':type(exc).__name__}
        result['limits'].append('本次经营原文提取未完成或未通过版本核验，未采用不完整原文；财务数据解读仍保留。')
        return result
