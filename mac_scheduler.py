#!/usr/bin/env python3
"""Mac-only scheduler and loopback settings bridge for the GitHub Pages UI."""
import argparse
import json
import math
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone, timedelta
from collect_homepages import BASE, atomic_json

OPTIONS=(1,5,10,30,60)
SETTINGS=os.path.join(BASE,'scrape_settings.json')
RUNTIME=os.path.join(BASE,'data','scrape_runtime.json')
ORIGINS={'https://sangju-hue.github.io','http://127.0.0.1:8789','http://localhost:8789','http://127.0.0.1:8000','http://localhost:8000'}
KST=timezone(timedelta(hours=9))

def read_interval():
    try:
        with open(SETTINGS,encoding='utf-8') as f:value=json.load(f)['interval_minutes']
        return value if type(value) is int and value in OPTIONS else 5
    except (OSError,ValueError,KeyError):return 5

class Scheduler:
    def __init__(self):
        self.lock=threading.Lock();self.wake=threading.Event()
        self.interval=read_interval();self.csrf=secrets.token_urlsafe(32)
        self.state={'running':False,'last_started_at':None,'last_finished_at':None,'last_result':None}
        self.last_start=None
        self.next_start=None
    def snapshot(self):
        with self.lock:return dict(self.state,interval_minutes=self.interval)
    def set_interval(self,value):
        if type(value) is not int or value not in OPTIONS:raise ValueError('지원하지 않는 주기')
        with self.lock:
            atomic_json(SETTINGS,{'interval_minutes':value});self.interval=value
            if self.last_start is not None:self.next_start=max(time.monotonic(),self.last_start+value*60)
        self.wake.set()
    def run_once(self):
        with self.lock:
            if self.state['running']:return
            self.state['running']=True
            self.state['last_started_at']=datetime.now(KST).isoformat(timespec='seconds')
            self.last_start=time.monotonic()
        try:
            # fishing.sh also takes a file lock against old launch agents/manual runs.
            result=subprocess.run(['/bin/bash',os.path.join(BASE,'fishing.sh')],cwd=BASE)
            outcome='ok' if result.returncode==0 else 'failed'
        except Exception as e:
            print(type(e).__name__,str(e),flush=True);outcome='failed'
        with self.lock:
            self.state.update(running=False,last_finished_at=datetime.now(KST).isoformat(timespec='seconds'),last_result=outcome)
            period=self.interval*60
            self.next_start=self.last_start+max(1,math.ceil((time.monotonic()-self.last_start)/period))*period
        atomic_json(RUNTIME,self.snapshot())
    def loop(self):
        while True:
            now=time.monotonic()
            with self.lock:delay=0 if self.next_start is None else max(0,self.next_start-now)
            if delay:
                self.wake.wait(delay);self.wake.clear();continue
            self.run_once()

def handler(scheduler,port):
    class Handler(BaseHTTPRequestHandler):
        def trusted(self):
            origin=self.headers.get('Origin')
            return self.headers.get('Host') in {f'127.0.0.1:{port}',f'localhost:{port}'} and (not origin or origin in ORIGINS)
        def send(self,status,payload):
            body=json.dumps(payload,ensure_ascii=False).encode()
            self.send_response(status)
            origin=self.headers.get('Origin')
            if origin in ORIGINS:self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Vary','Origin')
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body)))
            self.end_headers();self.wfile.write(body)
        def do_OPTIONS(self):
            if not self.trusted():return self.send(403,{'error':'허용되지 않은 요청'})
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin',self.headers.get('Origin',''))
            self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers','Content-Type, X-CSRF-Token')
            self.send_header('Access-Control-Allow-Private-Network','true')
            self.end_headers()
        def do_GET(self):
            if not self.trusted():return self.send(403,{'error':'허용되지 않은 요청'})
            if self.path!='/settings':return self.send(404,{'error':'없음'})
            self.send(200,dict(scheduler.snapshot(),csrf_token=scheduler.csrf))
        def do_POST(self):
            if not self.trusted() or self.headers.get('X-CSRF-Token')!=scheduler.csrf:return self.send(403,{'error':'허용되지 않은 요청'})
            if self.path!='/settings':return self.send(404,{'error':'없음'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=1024:raise ValueError('잘못된 요청')
                payload=json.loads(self.rfile.read(size))
                scheduler.set_interval(payload['interval_minutes'])
            except (ValueError,KeyError):return self.send(400,{'error':'1, 5, 10, 30, 60분만 지원합니다'})
            self.send(200,scheduler.snapshot())
    return Handler

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8788)
    p.add_argument('--settings-only',action='store_true',help='점검용: 수집 없이 설정 서버만 실행')
    args=p.parse_args();s=Scheduler()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler(s,args.port))
    if not args.settings_only:threading.Thread(target=s.loop,daemon=True).start()
    print(f'맥 수집기: {s.interval}분, 설정 연결 http://127.0.0.1:{args.port}',flush=True)
    server.serve_forever()

if __name__=='__main__':main()
