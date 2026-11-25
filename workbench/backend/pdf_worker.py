"""One PDF operation. No app import, network request or task-file writes."""
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'financial-report-analysis'))


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    from financial_engine.parse_runtime import PROGRESS
    from pdf_runtime import digest
    request = json.loads(sys.stdin.readline())
    path = Path(request['path'])
    if digest(path) != request['source_hash']:
        raise ValueError('原文件在解析前发生变化，请重新选择。')
    last = [0]

    def progress(detail):
        if time.monotonic()-last[0] >= .5 or not detail.get('page'):
            emit({'type': 'progress', 'detail': detail})
            last[0] = time.monotonic()

    PROGRESS.set(progress)
    operation = request['operation']
    progress({'phase': '读取 PDF 文件'})
    if operation == 'annual':
        from financial_engine.pdf_adapter import parse_pdf
        value = parse_pdf(path)
    elif operation == 'identity':
        from financial_engine.mining import _pdf_security_identity
        value = _pdf_security_identity(path)
    elif operation == 'interim':
        from financial_engine.interim import parse_interim_pdf
        value = parse_interim_pdf(path, **request['parameters'])
    elif operation == 'document':
        from financial_engine.document_reader import read_pdf_document
        value = read_pdf_document(path, **request['parameters'])
    elif operation == 'research_business':
        from research_excerpts import extract_business
        value = extract_business(path,progress=progress,**request['parameters'])
    else:
        raise ValueError('不支持的 PDF 解析操作')
    if digest(path) != request['source_hash']:
        raise ValueError('原文件在解析过程中发生变化，未保存本次数据。')
    cache = Path(request['cache'])
    temporary = cache.with_name(f'.pdf-{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps({'key': request['key'], 'value': value}, ensure_ascii=False), encoding='utf-8')
        temporary.replace(cache)
    finally:
        temporary.unlink(missing_ok=True)
    emit({'type': 'done'})


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        from upload_errors import classify_upload_exception
        failure=classify_upload_exception(exc,'PDF解析')
        emit({'type': 'error', 'error_type': type(exc).__name__, 'message': str(exc)[:1000],
              'failure_code':failure['code']})
        sys.exit(1)
