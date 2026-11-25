"""Offline real-PDF smoke test in isolated task storage; never calls a model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import shutil
import uuid
from contextlib import contextmanager
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT/'backend'))
from fastapi.testclient import TestClient
import app as core


@contextmanager
def isolated_storage():
    root = (PROJECT/'artifacts').resolve()
    work = root / f'library-check-{uuid.uuid4().hex}'
    work.mkdir()
    try:
        yield work
    finally:
        assert work.resolve().is_relative_to(root)
        shutil.rmtree(work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--library',type=Path,default=PROJECT/'backend/data/report_library')
    parser.add_argument('--output',type=Path,default=PROJECT/'artifacts/report-library-smoke.json')
    args = parser.parse_args()
    library = args.library.resolve(strict=True)
    external_calls = []
    original_connect = socket.socket.connect
    def local_connections_only(sock, address):
        # Windows asyncio uses a loopback socket pair even for in-process APIs.
        if isinstance(address, tuple) and address[0] in {'127.0.0.1','::1'}:
            return original_connect(sock,address)
        external_calls.append(str(address))
        raise AssertionError('External network is disabled in this smoke test')
    (PROJECT/'artifacts').mkdir(exist_ok=True)
    with isolated_storage() as work:
        paths = {name:Path(work)/name for name in ('TASK_DIR','RESULT_DIR','UPLOAD_DIR','CACHE_DIR','LOG_DIR','BACKFILL_DIR','PARSED_DIR','COMPANY_DIR')}
        for path in paths.values():path.mkdir()
        with patch.multiple(core,**paths,TASKS={},_CANCELLED_TASKS=set()), \
             patch.dict(os.environ,{'FINANCE_LIBRARY_DIR':str(library),'FINANCE_LIBRARY_ALLOW_ONLINE':'0'}), \
             patch.object(core,'generate_report',side_effect=lambda analysis:(core.deterministic_report(analysis),'deterministic_template')), \
             patch.object(socket.socket,'connect',new=local_connections_only):
            client = TestClient(core.app)
            print('1/3: analyzing local target 300750 / 2025',flush=True)
            response = client.post('/api/tasks/online',json={'query':'300750','report_year':2025,'lookback_years':0})
            assert response.status_code==202, response.text
            base = core.task_by_id(response.json()['id'])
            assert base['status']=='completed', base.get('error')
            print('2/3: backfilling local years 2023 and 2024',flush=True)
            response = client.post(f"/api/tasks/{base['id']}/backfill",json={'lookback_years':2,'online':False})
            assert response.status_code==202, response.text
            history = core.task_by_id(response.json()['id'])
            assert history['status']=='completed', history.get('error')
            assert history['result']['payload']['history']['status']=='complete', history.get('backfill_rows')
            print('3/3: comparing local peers 002074 and 300207',flush=True)
            response = client.post(f"/api/tasks/{history['id']}/peers",json={
                'peer_codes':['002074','300207'],'peer_group':'动力及储能电池相关企业',
                'selection_note':'离线接口验收样本，产品结构差异仍需人工判断'})
            assert response.status_code==202, response.text
            peer = core.task_by_id(response.json()['id'])
            assert peer['status']=='completed', peer.get('error')
            payload = peer['result']['payload']
            assert payload['peers']['status']=='complete', payload['peers'].get('warnings')
            assert payload['history']==history['result']['payload']['history']
            evidence = peer['result']['evidence']
            seen, source_checks = set(), []
            for entry in evidence.values():
                if entry['document_id'] in seen:continue
                seen.add(entry['document_id'])
                response = client.get(f"/api/tasks/{peer['id']}/file",params={'fact_id':entry['fact_id']})
                assert response.status_code==200, response.text
                actual = hashlib.sha256(response.content).hexdigest()
                assert actual==entry['file_hash']
                source_checks.append({'document_id':entry['document_id'],'sha256':actual,'status':'passed'})
            assert len(source_checks)==5, source_checks
            summary = {'status':'passed','target':'300750','year':2025,
                'history_years':payload['history']['available_years'],
                'peer_codes':['002074','300207'],
                'current_calculated_metrics':len(peer['result']['analysis'].get('metrics') or []),
                'metrics_with_two_valid_peers':sum(item['peer_median']['status']=='calculated' for item in payload['peers']['comparison']['metric_comparisons']),
                'sources_verified':source_checks,'external_network_calls':len(external_calls),
                'model_calls':0,'production_task_files_changed':False,
                'note':'五份真实 PDF 的离线流程验收；不表示全库字段准确性认证。'}
            assert not external_calls, external_calls
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
