"""Loopback-only, isolated upload QA. Never use the user's task or report data."""
import asyncio
import sys
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'backend'))
import app as core
from test_upload_errors import unrelated_pdf
from fastapi.staticfiles import StaticFiles
import uvicorn

qa_root = PROJECT / 'artifacts' / 'upload-error-qa'
session_root = qa_root / ('session-' + uuid.uuid4().hex)
for key in ('UPLOAD_DIR', 'CACHE_DIR', 'RESULT_DIR', 'TASK_DIR', 'LOG_DIR', 'BACKFILL_DIR', 'PARSED_DIR', 'COMPANY_DIR'):
    directory = session_root / key
    directory.mkdir(parents=True, exist_ok=True)
    setattr(core, key, directory)
core.TASKS = {}
core.get_report_library = lambda: None
core.library_online_enabled = lambda: False
core.generate_report = lambda analysis: (core.deterministic_report(analysis), 'deterministic_template')
(qa_root / 'unrelated-test.pdf').write_bytes(unrelated_pdf())
(qa_root / 'damaged-test.pdf').write_bytes(b'%PDF-1.4\nnot a complete PDF')


@core.app.post('/__qa__/shutdown')
def shutdown():
    server.should_exit = True
    return {'stopping': True}


core.app.mount('/', StaticFiles(directory=qa_root / 'build', html=True), name='qa-ui')
server = uvicorn.Server(uvicorn.Config(core.app, host='127.0.0.1', port=5184, log_level='warning'))


async def main():
    # Also stop automatically if an interrupted client cannot request teardown.
    asyncio.get_running_loop().call_later(900, setattr, server, 'should_exit', True)
    print('Isolated upload QA: http://127.0.0.1:5184/', flush=True)
    await server.serve()


if __name__ == '__main__':
    asyncio.run(main())
