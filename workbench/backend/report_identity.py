"""One report identity for upload, saved results, history and peer comparison.

Reading an old result enriches its response only: no financial recomputation,
network lookup or rewrite of the user's historical snapshot.
"""
import copy
import re


def valid_code(value):
    return value if isinstance(value, str) and re.fullmatch(r'\d{6}', value) else None


def document(task):
    docs = ((task.get('result') or {}).get('analysis') or {}).get('documents') or []
    return docs[0] if len(docs) == 1 else {}


def same_report(left, right):
    if not left or not right or left.get('report_year') != right.get('report_year'):
        return False
    if left.get('file_sha256') and right.get('file_sha256'):
        return left['file_sha256'] == right['file_sha256']
    return bool(left.get('document_id') and left.get('document_id') == right.get('document_id')
                and left.get('company_name') == right.get('company_name'))


def stored_identity(task):
    result = task.get('result') or {}
    payload = result.get('payload') or {}
    doc = document(task)
    identity = payload.get('identity') or {}
    unified = result.get('report_identity') or {}
    conflict = identity.get('status') in {'ambiguous','conflict','conflict_or_unverified'} or unified.get('status') == 'conflict'
    values = [(doc.get('security_code'),'document'),(doc.get('stock_code'),'document'),
              (task.get('verified_stock_code'),'verified_history'),(task.get('confirmed_peer_code'),'manual_confirmation'),
              ((result.get('history_backfill') or {}).get('stock_code'),'verified_history'),
              ((payload.get('peer_binding') or {}).get('target_code'),'verified_peer')]
    if identity.get('status') not in {'unverified','absent'}:
        values.append((identity.get('stock_code'),'saved_identity'))
    values.append((unified.get('stock_code'),'saved_identity'))
    history_docs = ((payload.get('history') or {}).get('analysis') or {}).get('documents') or []
    values.extend((item.get('security_code'),'verified_history') for item in history_docs if same_report(doc,item))
    return [(code,source) for value,source in values if (code:=valid_code(value))], conflict


def resolve_identity(core, task, *, inspect_pdf=False, pdf_identity=None):
    doc = document(task)
    if not doc:
        return {'stock_code':None,'status':'unresolved','sources':[]}
    values, conflict = stored_identity(task)
    # Follow only linked, completed tasks containing the exact same source PDF.
    # Never copy a code merely because company/year or editable inputs match.
    queue = [task]
    seen = {task.get('id')}
    while queue and len(seen) <= 16:
        current = queue.pop(0)
        for key in ('parent_task_id','latest_backfill_task_id','latest_peer_task_id'):
            related_id = current.get(key)
            if not related_id or related_id in seen:
                continue
            seen.add(related_id)
            related = core.task_by_id(related_id)
            if not related or related.get('trashed') or related.get('status') != 'completed' or not same_report(doc,document(related)):
                continue
            extra, blocked = stored_identity(related)
            values.extend(extra)
            conflict |= blocked
            queue.append(related)
    if inspect_pdf and pdf_identity is None and not values:
        path = core.evidence_file_path(task)
        if path:
            try:
                from financial_engine.mining import _pdf_security_identity
                pdf_identity = _pdf_security_identity(path)
            except core.TaskCancelled:
                raise
            except Exception:
                pass  # Identity failure must not discard the financial analysis.
    if pdf_identity:
        conflict |= pdf_identity.get('status') == 'ambiguous'
        if code := valid_code(pdf_identity.get('stock_code')):
            values.insert(0,(code,'pdf_code_reported'))
    codes = {code for code,_ in values}
    if conflict or len(codes) > 1:
        return {'stock_code':None,'status':'conflict','sources':[{'stock_code':code,'source':source} for code,source in values]}
    if not codes:
        # Only exact registered names/aliases, never fuzzy or substring matches.
        from company_directory import directory, normalized
        name = normalized(doc.get('company_name') or doc.get('company_short_name') or '')
        if name:
            try:
                matches = {item['code'] for item in directory(core)
                           if item.get('identity_status') in {'catalog_verified','online_verified'}
                           and name in {normalized(value) for value in [item['name'],item.get('full_name',''),*item.get('aliases',[])]}}
            except (OSError, ValueError):
                matches = set()
            if len(matches) == 1:
                values = [(matches.pop(),'catalog_exact_match')]
            elif len(matches) > 1:
                return {'stock_code':None,'status':'conflict','sources':[]}
    return {'stock_code':values[0][0] if values else None,
            'status':values[0][1] if values else 'unresolved',
            'sources':[{'stock_code':code,'source':source} for code,source in dict.fromkeys(values)]}


def result_with_identity(core, task, *, inspect_pdf=False):
    result = task.get('result') or {}
    identity = resolve_identity(core,task,inspect_pdf=inspect_pdf)
    doc = document(task)
    if identity['status'] != 'conflict' and (not identity['stock_code'] or doc.get('security_code') == identity['stock_code']):
        return result
    enriched = copy.deepcopy(result)
    enriched['report_identity'] = identity
    if identity['stock_code']:
        enriched['analysis']['documents'][0]['security_code'] = identity['stock_code']
        enriched.setdefault('payload',{})['identity'] = {**(enriched.get('payload',{}).get('identity') or {}),**identity}
    return enriched
