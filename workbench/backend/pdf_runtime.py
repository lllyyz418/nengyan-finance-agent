"""Bounded PDF subprocesses, versioned fact caches and cancellable progress.

Only PDF extraction is moved out of the API process. Financial calculations,
issuer validation and task persistence keep their existing authority in the host.
"""
import hashlib
import json
import queue
import subprocess
import sys
import threading
import time
import weakref
from contextlib import contextmanager
from functools import lru_cache, wraps
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path

SLOTS = threading.BoundedSemaphore(2)
WORKER = Path(__file__).with_name('pdf_worker.py')
MAX_SECONDS = 900
IDLE_SECONDS = 180
KEY_LOCKS = weakref.WeakValueDictionary()
KEY_LOCKS_GUARD = threading.Lock()


@contextmanager
def parsing_slot(key, checkpoint, max_seconds):
    with KEY_LOCKS_GUARD:
        lock=KEY_LOCKS.get(key)
        if lock is None:
            lock=threading.Lock();KEY_LOCKS[key]=lock
    started=time.monotonic()
    def acquire(semaphore):
        while not semaphore.acquire(timeout=.25):
            checkpoint()
            if time.monotonic()-started>max_seconds:
                raise TimeoutError('PDF 解析排队超时，请稍后重试；原文件仍保留。')
    acquire(lock)
    try:
        acquire(SLOTS)
        try:yield
        finally:SLOTS.release()
    finally:lock.release()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


@lru_cache(maxsize=1)
def parser_version():
    value = hashlib.sha256(b'isolated-pdf-1')
    value.update(sys.version.encode())
    for name in ('pdfplumber','pdfminer.six','pypdfium2','rapidocr'):
        try: value.update((name+version(name)).encode())
        except PackageNotFoundError: pass
    engine = WORKER.parent / 'financial-report-analysis' / 'financial_engine'
    for path in [WORKER, Path(__file__), *sorted(engine.glob('*.py'))]:
        value.update(path.name.encode())
        value.update(path.read_bytes())
    return value.hexdigest()


def cached_value(cache, key):
    try:
        saved = json.loads(cache.read_text(encoding='utf-8'))
        if saved.get('key') == key and isinstance(saved.get('value'), dict):
            return saved['value']
    except (OSError, ValueError, TypeError):
        pass
    return None


def extract(operation, path, cache_dir, *, checkpoint=lambda: None,
            progress=lambda detail: None, max_seconds=MAX_SECONDS,
            idle_seconds=IDLE_SECONDS, **parameters):
    path = Path(path).resolve(strict=True)
    checkpoint()
    source_hash = digest(path)
    key = hashlib.sha256(json.dumps([parser_version(), source_hash, operation, parameters],
                                   sort_keys=True).encode()).hexdigest()
    cache_dir = Path(cache_dir) / 'pdf-runtime'
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f'{key}.json'

    def read_cache():
        value = cached_value(cache, key)
        if value is not None:
            checkpoint()
            progress({'phase': '复用已核验的解析缓存', 'cache_hit': True})
            if 'file_name' in value:
                value['file_name'] = path.name
        return value

    value = read_cache()
    if value is not None:
        return value
    progress({'phase': '等待 PDF 解析工作进程'})
    slot=parsing_slot(key,checkpoint,max_seconds)
    slot.__enter__()
    process = None
    reader = None
    try:
        checkpoint()
        value = read_cache()
        if value is not None:
            return value
        # Arguments contain no PDF text, API key or other request secrets.
        process = subprocess.Popen([sys.executable, '-X', 'utf8', '-B', '-u', str(WORKER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        process.stdin.write(json.dumps({'operation': operation, 'path': str(path),
            'parameters': parameters, 'key': key, 'source_hash': source_hash,
            'cache': str(cache)}, ensure_ascii=False) + '\n')
        process.stdin.close()
        messages = queue.Queue()

        def collect():
            for line in process.stdout:
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        messages.put(event)
                except ValueError:
                    pass  # Libraries may emit diagnostics; never expose raw PDF text.

        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        started = last_event = last_update = time.monotonic()
        detail = {'phase': '启动 PDF 解析工作进程'}
        progress(detail)
        while True:
            checkpoint()
            now = time.monotonic()
            if now - started > max_seconds or now - last_event > idle_seconds:
                raise TimeoutError('PDF 解析超时，已停止本次解析；原文件及已完成结果保留。可重试或更换清晰的完整报告。')
            try:
                event = messages.get(timeout=.25)
            except queue.Empty:
                if process.poll() is not None and not reader.is_alive():
                    raise RuntimeError('PDF 解析工作进程异常退出，未采用不完整数据；请重试或核对文件。')
                if now - last_update >= 10:
                    progress({**detail, 'elapsed_seconds': int(now-started)})
                    last_update = now
                continue
            last_event = time.monotonic()
            if event.get('type') == 'error':
                message = event.get('message') or 'PDF 解析失败'
                if event.get('failure_code') not in {None,'SYSTEM_ERROR'}:
                    from upload_errors import UploadAnalysisError, FAILURES
                    if event['failure_code'] in FAILURES:
                        raise UploadAnalysisError(event['failure_code'],'PDF解析')
                error_type = {'ValueError': ValueError, 'OSError': OSError,
                              'DocumentReadError': ValueError}.get(event.get('error_type'), RuntimeError)
                raise error_type(message)
            if event.get('type') == 'done':
                checkpoint()
                value = cached_value(cache, key)
                if value is None or digest(path) != source_hash:
                    raise ValueError('解析过程中原文件发生变化或缓存无效，请重新选择文件。')
                return value
            if event.get('type') == 'progress':
                detail = event['detail']
                if time.monotonic()-last_update >= 2:
                    progress({**detail, 'elapsed_seconds': int(time.monotonic()-started)})
                    last_update = time.monotonic()
    finally:
        try:
            if process:
                if process.poll() is None:
                    process.terminate()  # Only the child created by this invocation.
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                if reader:
                    reader.join(timeout=2)
                process.stdout.close()
        finally:
            slot.__exit__(None,None,None)


def task_runtime(function):
    """Install a context-local runner; never monkeypatch other live tasks."""
    @wraps(function)
    def wrapped(task_id, *args, **kwargs):
        import app as core
        from financial_engine.parse_runtime import use_runner

        def progress(detail):
            core.ensure_task_running(task_id)
            current = core.task_by_id(task_id) or {}
            label = current.get('parse_report_label') or current.get('file_name') or 'PDF'
            page = f" · 第 {detail['page']}/{detail['total']} 页" if detail.get('page') else ''
            elapsed = f" · 已用时 {detail['elapsed_seconds']} 秒" if detail.get('elapsed_seconds') else ''
            core.update_task(task_id, stage=f"{label}：{detail['phase']}{page}{elapsed}",
                             parse_detail=detail)

        def runner(operation, path, **parameters):
            return extract(operation, path, core.PARSED_DIR,
                checkpoint=lambda: core.ensure_task_running(task_id), progress=progress, **parameters)

        with use_runner(runner):
            return function(task_id, *args, **kwargs)
    return wrapped


def core_task_runtime(function):
    @wraps(function)
    def wrapped(core, task_id, *args, **kwargs):
        return task_runtime(lambda identifier: function(core, identifier, *args, **kwargs))(task_id)
    return wrapped
