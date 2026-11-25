"""Portable local deployment wrapper; business implementation stays unchanged."""
from pathlib import Path
import os,sys,json,shutil,hashlib,threading,time,socket,subprocess,webbrowser,argparse,secrets,http.client,urllib.request,urllib.error
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
ROOT=Path(__file__).resolve().parent
BACKEND=ROOT/'workbench/backend';DATA=BACKEND/'data';RUN=ROOT/'run'
RUN.mkdir(exist_ok=True)
DLL_HANDLES=[]
for folder in [ROOT/'runtime/Library/bin',ROOT/'runtime/DLLs']:
    if folder.exists() and hasattr(os,'add_dll_directory'):DLL_HANDLES.append(os.add_dll_directory(str(folder)))
os.environ['PATH']=os.pathsep.join([str(ROOT/'runtime'),str(ROOT/'runtime/Library/bin'),str(ROOT/'runtime/DLLs'),os.environ.get('PATH','')])
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ['PYTHONIOENCODING']='utf-8'

def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
def read_config():return json.loads((ROOT/'运行配置.json').read_text(encoding='utf8'))
def replace_paths(v):
    if isinstance(v,dict):return {k:replace_paths(x) for k,x in v.items()}
    if isinstance(v,list):return [replace_paths(x) for x in v]
    if isinstance(v,str):return v.replace('__DATA_ROOT__',DATA.as_posix()).replace('__PROJECT_ROOT__',(ROOT/'workbench').as_posix())
    return v
def write_json(p,v):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf8')
def restore():
    manifest=json.loads((ROOT/'materials/material_manifest.json').read_text(encoding='utf8'))
    selected=manifest['selected'];valid_hashes=set()
    for r in selected:
        original=ROOT/'materials/pdf_store'/f'{r["sha256"]}.pdf'
        if not original.is_file() or digest(original)!=r['sha256']:raise RuntimeError('PDF缺失或校验失败：'+r['sha256'][:16]+'，请完整解压三个包。')
        valid_hashes.add(r['sha256'])
        for alias in r['aliases']:
            dest=(DATA/alias).resolve()
            if not dest.is_relative_to(DATA.resolve()):raise RuntimeError('材料清单路径越界')
            dest.parent.mkdir(parents=True,exist_ok=True)
            if dest.exists():
                if digest(dest)!=r['sha256']:raise RuntimeError('已有数据与交付样例冲突，未覆盖：'+str(dest))
                continue
            try:os.link(original,dest)
            except OSError:shutil.copy2(original,dest)
    for name in ['index.json','companies.json']:
        source=ROOT/'materials'/('library_index.json' if name=='index.json' else 'companies.json')
        target=DATA/'report_library'/name
        if not target.exists():target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    # Reuse bundled OCR model weights locally; never require a first-run model download.
    model_root=DATA/'scan_models';model_root.mkdir(parents=True,exist_ok=True)
    for model in (ROOT/'runtime/Lib/site-packages/rapidocr/models').glob('*.onnx'):
        target=model_root/model.name
        if not target.exists():
            try:os.link(model,target)
            except OSError:shutil.copy2(model,target)
    archive=ROOT/'materials/state_archive';active=[]
    def requirements(obj):
        paths=[];hashes=[]
        def walk(v):
            if isinstance(v,dict):
                for k,x in v.items():
                    if k in {'sha256','file_sha256','source_hash'} and isinstance(x,str) and len(x)==64:hashes.append(x)
                    walk(x)
            elif isinstance(v,list):
                for x in v:walk(x)
            elif isinstance(v,str) and '__DATA_ROOT__' in v and v.lower().endswith('.pdf'):paths.append(Path(replace_paths(v)))
        walk(obj)
        return paths,hashes
    for p in sorted((archive/'tasks').glob('*.json')):
        if len(p.stem)!=32:continue
        obj=json.loads(p.read_text(encoding='utf8'))
        if obj.get('status') not in {'completed','review_required'}:continue
        paths,hashes=requirements(obj)
        if paths and not all(x.exists() for x in paths):continue
        # Hashes with no path may refer to absent historical/peer PDFs; do not claim full restoration.
        if any(x not in valid_hashes for x in hashes):continue
        if not paths and not hashes:continue
        dest=DATA/'tasks'/p.name
        if not dest.exists():write_json(dest,replace_paths(obj))
        active.append(p.stem)
        q=archive/'results'/p.name
        if q.exists() and not (DATA/'results'/p.name).exists():write_json(DATA/'results'/p.name,replace_paths(json.loads(q.read_text(encoding='utf8'))))
        log=archive/'logs'/(p.stem+'.jsonl');target=DATA/'logs'/log.name
        if log.exists() and not target.exists():target.parent.mkdir(parents=True,exist_ok=True);target.write_text(replace_paths(log.read_text(encoding='utf8')),encoding='utf8')
    # Research prose is restored only when the associated base task is present.
    for p in (archive/'research_reports').rglob('*.json'):
        rel=p.relative_to(archive);obj=json.loads(p.read_text(encoding='utf8'))
        if obj.get('base_task_id') in active:
            dest=DATA/rel
            if not dest.exists():write_json(dest,replace_paths(obj))
    info={'pdf_count':len(selected),'active_tasks':len(active),'task_ids':active,'data_root':str(DATA)}
    write_json(RUN/'恢复摘要.json',info)
    print(f'材料就绪：{len(selected)}份不同PDF，{len(active)}条可核验历史任务。',flush=True)
    return info

def check():
    import importlib
    required=['ssl','sqlite3','ctypes','fastapi','uvicorn','dotenv','openai','pdfplumber','pypdfium2']
    optional=['rapidocr','onnxruntime','cv2','numpy']
    errors=[]
    for name in required+optional:
        try:importlib.import_module(name);print('[OK]',name)
        except Exception as e:
            print('[失败]',name,type(e).__name__,str(e)[:180]);errors.append(name)
    for p in ['homepage/index.html','workbench/dist/index.html','workbench/backend/app.py','materials/material_manifest.json']:
        assert (ROOT/p).is_file(),'缺少文件：'+p
    if errors:raise RuntimeError('依赖检查未通过：'+', '.join(errors)+'。先确认三个包都已解压。')
    info=restore();print('环境检查通过。OCR模型如未随当前环境提供，首次调用可能需要下载；候选仍需人工确认。')
    return info

def stop_existing():
    state=ROOT/'run/service.json'
    if not state.exists():print('本交付目录没有已登记的运行实例。');return
    s=json.loads(state.read_text(encoding='utf8'))
    req=urllib.request.Request(f'http://127.0.0.1:{s["homepage_port"]}/__stop',data=s['token'].encode(),method='POST')
    try:
        with urllib.request.urlopen(req,timeout=5) as r:print(r.read().decode())
    except Exception as e:print('无法连接本目录登记的实例；未结束任何其他进程。',str(e))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--check',action='store_true');ap.add_argument('--stop',action='store_true');ap.add_argument('--no-browser',action='store_true');a=ap.parse_args()
    if a.stop:stop_existing();return
    if a.check:check();return
    config=read_config();bp=int(config['backend_port']);wp=int(config['workbench_port']);hp=int(config['homepage_port'])
    if len({bp,wp,hp})!=3:raise RuntimeError('三个端口必须不同。')
    probes=[]
    try:
        for port in [bp,wp,hp]:
            s=socket.socket();s.bind(('127.0.0.1',port));probes.append(s)
    except OSError:raise RuntimeError(f'端口{port}被占用。请停止旧实例或修改运行配置；未结束其他程序。')
    finally:
        for s in probes:s.close()
    check()
    env=os.environ.copy();env.pop('DEEPSEEK_API_KEY',None)
    env['FINANCE_LIBRARY_DIR']='data/report_library';env['FINANCE_LIBRARY_ALLOW_ONLINE']='1' if config.get('allow_online_reports') else '0'
    env['PYTHONPATH']=str(BACKEND);env['PYTHONDONTWRITEBYTECODE']='1'
    log=open(RUN/'backend.log','a',encoding='utf8')
    process=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(bp)],cwd=BACKEND,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    stop=threading.Event();token=secrets.token_hex(24)
    class Home(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kw):super().__init__(*args,directory=str(ROOT/'homepage'),**kw)
        def do_GET(self):
            if self.path.split('?')[0] in {'/','/index.html'}:
                raw=(ROOT/'homepage/index.html').read_text(encoding='utf8').replace('http://127.0.0.1:5173/',f'http://127.0.0.1:{wp}/').encode()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            else:super().do_GET()
        def do_POST(self):
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            if self.path=='/__stop' and body.decode()==token:
                raw='正在停止本目录的服务。'.encode();self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);stop.set()
            else:self.send_error(403)
        def log_message(self,*args):pass
    class Workbench(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kw):super().__init__(*args,directory=str(ROOT/'workbench/dist'),**kw)
        def proxy(self):
            body=self.rfile.read(int(self.headers.get('Content-Length','0'))) if self.command not in {'GET','HEAD'} else None
            conn=http.client.HTTPConnection('127.0.0.1',bp,timeout=180)
            try:
                headers={k:v for k,v in self.headers.items() if k.lower() not in {'host','connection','transfer-encoding'}}
                conn.request(self.command,self.path,body=body,headers=headers);r=conn.getresponse()
                payload=r.read();self.send_response(r.status)
                for k,v in r.getheaders():
                    if k.lower() not in {'connection','transfer-encoding','content-length','server','date'}:self.send_header(k,v)
                self.send_header('Content-Length',str(len(payload)));self.end_headers()
                if self.command!='HEAD':self.wfile.write(payload)
            except (OSError,http.client.HTTPException) as exc:self.send_error(502,str(exc))
            finally:conn.close()
        def do_GET(self):
            if self.path.startswith('/api/'):return self.proxy()
            return super().do_GET()
        def do_HEAD(self):
            if self.path.startswith('/api/'):return self.proxy()
            return super().do_HEAD()
        do_POST=proxy;do_PATCH=proxy;do_DELETE=proxy;do_PUT=proxy
        def log_message(self,*args):pass
    servers=[]
    try:
        for i in range(60):
            if process.poll() is not None:raise RuntimeError('后端启动失败，请查看run/backend.log')
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{bp}/api/health',timeout=2) as r:
                    if r.status==200:break
            except (OSError,urllib.error.URLError):time.sleep(.4)
        else:raise RuntimeError('后端就绪超时，请查看run/backend.log')
        for port,handler in [(hp,Home),(wp,Workbench)]:
            server=ThreadingHTTPServer(('127.0.0.1',port),handler);servers.append(server)
            threading.Thread(target=server.serve_forever,daemon=True).start()
        write_json(RUN/'service.json',{'token':token,'homepage_port':hp,'backend_pid':process.pid})
        print(f'首页：http://127.0.0.1:{hp}\n工作台：http://127.0.0.1:{wp}\n后端：http://127.0.0.1:{bp}/docs\n保持此窗口打开，Ctrl+C停止。',flush=True)
        if not a.no_browser:webbrowser.open(f'http://127.0.0.1:{hp}')
        while not stop.wait(1):
            if process.poll() is not None:raise RuntimeError('后端意外停止，请查看run/backend.log')
    finally:
        for server in servers:server.shutdown();server.server_close()
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=8)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        log.close()
        state=RUN/'service.json'
        if state.exists() and json.loads(state.read_text(encoding='utf8')).get('token')==token:state.unlink()
if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:print('已停止。')
    except Exception as e:print('启动/检查失败：',e,flush=True);sys.exit(1)
