"""Optional host-owned PDF execution. Standalone engine behavior is unchanged."""
from contextvars import ContextVar
from contextlib import contextmanager

RUNNER = ContextVar('financial_pdf_runner', default=None)
PROGRESS = ContextVar('financial_pdf_progress', default=None)
PAGE_TEXTS = ContextVar('financial_pdf_page_texts', default=None)


@contextmanager
def page_text_cache():
    token=PAGE_TEXTS.set({})
    try:yield
    finally:PAGE_TEXTS.reset(token)


def cached_page_text(page):
    """Reuse plain text only; PDF geometry caches may still be released eagerly."""
    cache=PAGE_TEXTS.get()
    if cache is None:return page.extract_text() or ''
    key=id(page)
    if key not in cache:cache[key]=page.extract_text() or ''
    return cache[key]


@contextmanager
def use_runner(runner):
    token = RUNNER.set(runner)
    try:
        yield
    finally:
        RUNNER.reset(token)


def report_progress(phase, page=None, total=None):
    callback = PROGRESS.get()
    if callback:
        callback({'phase': phase, 'page': page, 'total': total})
