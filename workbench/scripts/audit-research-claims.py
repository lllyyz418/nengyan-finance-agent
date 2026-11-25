"""Audit recorded model claims offline; no API or saved-version modifications."""
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from research_writer import bind_citations,_validate_strict
session=Path(sys.argv[1]).resolve();assert session.is_relative_to((ROOT/'artifacts/research-reports').resolve())
packet=json.loads(next((session/'research_reports').glob('*/*.input.json')).read_text(encoding='utf-8'))
raw=bind_citations(json.loads((session/f'model-attempt-{sys.argv[2] if len(sys.argv)>2 else 0}.json').read_text(encoding='utf-8')),packet)
for section in raw['sections']:
    for claim in section['claims']:
        overview={'key':'overview','claims':[claim]}
        trial={'sections':[overview,{'key':section['key'] if section['key']!='overview' else 'followup','claims':[claim]}]}
        try:_validate_strict(trial,packet);reason='PASS'
        except ValueError as exc:reason=str(exc)
        print(section['key'],claim['headline'],reason,flush=True)
