"""Deterministic worker protocol fixture. Never used by the running app."""
import json
import sys
import time
from pathlib import Path

request=json.loads(sys.stdin.readline())
mode=Path(request['path']).read_text()
if mode=='crash':sys.exit(3)
if mode in {'timeout','cancel','busy'}:
    if mode=='busy':
        until=time.monotonic()+2
        while time.monotonic()<until:sum(range(1000))
    else:time.sleep(20)
if mode=='error':
    print(json.dumps({'type':'error','error_type':'ValueError','message':'fixture failure'}),flush=True)
else:
    print(json.dumps({'type':'progress','detail':{'phase':'fixture','page':1,'total':1}}),flush=True)
    Path(request['cache']).write_text(json.dumps({'key':request['key'],'value':{'fixture':mode}}))
    print(json.dumps({'type':'done'}),flush=True)
