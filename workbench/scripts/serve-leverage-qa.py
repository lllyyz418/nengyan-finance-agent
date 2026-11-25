"""Loopback-only chart QA using separately parsed local reports, no user writes."""
import asyncio
import json
import sys
import uuid
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'backend'))
import app as core
from fastapi.staticfiles import StaticFiles
import uvicorn

qa_root = PROJECT / 'artifacts' / 'leverage-qa'
session_root = qa_root / ('session-' + uuid.uuid4().hex)
for key in ('UPLOAD_DIR', 'CACHE_DIR', 'RESULT_DIR', 'TASK_DIR', 'LOG_DIR', 'BACKFILL_DIR', 'PARSED_DIR', 'COMPANY_DIR'):
    directory = session_root / key
    directory.mkdir(parents=True, exist_ok=True)
    setattr(core, key, directory)
core.TASKS = {}
core.get_report_library = lambda: None
core.library_online_enabled = lambda: False
core.generate_report = lambda analysis: (core.deterministic_report(analysis), 'deterministic_template')
bundle = json.loads((qa_root / 'local-verification.json').read_text(encoding='utf-8'))
bundle['history'].update(available_years=[item['year'] for item in bundle['sources']], status='completed', rows=[])
task = core.create_task('upload', '本地年报图表核验（隔离测试）.pdf', {'lookback_years': 2})
result = {'engine_version': 'local-chart-qa', 'analysis': bundle['analysis'],
          'payload': {'history': bundle['history']},
          'evidence': core.evidence_index(bundle['history']['analysis']),
          'report_markdown': core.deterministic_report(bundle['analysis']),
          'report_generator': 'deterministic_template'}
core.update_task(task['id'], status='completed', stage='本地核验完成', progress=100, result=result)


@core.app.post('/__qa__/shutdown')
def shutdown():
    server.should_exit = True
    return {'stopping': True}


core.app.mount('/', StaticFiles(directory=qa_root / 'build', html=True), name='chart-qa-ui')
server = uvicorn.Server(uvicorn.Config(core.app, host='127.0.0.1', port=5186, log_level='warning'))


async def main():
    asyncio.get_running_loop().call_later(900, setattr, server, 'should_exit', True)
    print('Isolated chart QA: http://127.0.0.1:5186/index.html', flush=True)
    await server.serve()


if __name__ == '__main__':
    asyncio.run(main())
