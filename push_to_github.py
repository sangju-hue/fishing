#!/usr/bin/env python3
"""맥에서 긁은 홈페이지 예약현황을 GitHub에 푸시."""
import base64
import json
import os
import sys
import urllib.request
import hashlib
import fcntl
import threading
import time
from urllib.error import HTTPError
from collect_homepages import atomic_json

REPO = "sangju-hue/fishing"
DATA_FILES = ("data/boats.json", "data/status.json", "data/status_homepages.json", "data/site_health.json", "data/site_health_sunsang24.json")
BASE = os.path.dirname(os.path.abspath(__file__))
_publish_mutex=threading.RLock()
TOKEN_FILE = os.path.join(BASE, ".github_token")

def get_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    print(".github_token 파일에 GitHub Personal Access Token을 넣어주세요.")
    print("github.com → Settings → Developer settings → Personal access tokens → Tokens (classic)")
    print("권한: repo (classic 토큰)")
    sys.exit(1)

def api(method, url, token, data=None):
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw=r.read()
        return json.loads(raw.decode()) if raw else {}

def publish_files(files,message):
    """Serialize Mac publishers; retry concurrent remote changes without force push."""
    for remote in files:
        if remote=='.github_token' or remote.startswith(('.ntfy/','diagnostics/','.git/')) or remote in ('scrape_settings.json','scrape.log','.scrape.lock','.publish.lock','.publish_state.json','.apply_pending') or '..' in remote.split('/') or remote.startswith('/'):
            raise ValueError('업로드 대상 경로 오류')
    with _publish_mutex,open(os.path.join(BASE,'.publish.lock'),'a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        payloads={remote:open_bytes(local) for remote,local in files.items()}
        token=get_token();root=f'https://api.github.com/repos/{REPO}'
        def call(method,path,data=None):return api(method,root+path,token,data)
        for attempt in range(3):
            head=call('GET','/git/ref/heads/main')['object']['sha']
            base_tree=call('GET','/git/commits/'+head)['tree']['sha']
            tree_data=call('GET','/git/trees/'+base_tree+'?recursive=1')
            if tree_data.get('truncated'):raise ValueError('GitHub 파일 목록이 잘려 게시 중단')
            old={x['path']:x['sha'] for x in tree_data['tree']};entries=[]
            for remote,raw in payloads.items():
                sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
                if old.get(remote)==sha:continue
                blob=call('POST','/git/blobs',{'content':base64.b64encode(raw).decode(),'encoding':'base64'})
                entries.append({'path':remote,'mode':'100644','type':'blob','sha':blob['sha']})
            if not entries:print('변경된 파일 없음');return head
            tree=call('POST','/git/trees',{'base_tree':base_tree,'tree':entries})
            commit=call('POST','/git/commits',{'message':message,'tree':tree['sha'],'parents':[head]})
            try:call('PATCH','/git/refs/heads/main',{'sha':commit['sha'],'force':False})
            except HTTPError as e:
                if e.code not in (409,422) or attempt==2:raise
                time.sleep(attempt+1);continue
            print('푸시 완료:',', '.join(e['path'] for e in entries));return commit['sha']

def open_bytes(path):
    with open(path,'rb') as f:return f.read()

def semantic_value(value):
    if isinstance(value,dict):return {k:semantic_value(v) for k,v in value.items() if k not in ('updated_at','checked_at')}
    if isinstance(value,list):return [semantic_value(v) for v in value]
    return value

def main():
    files={path:os.path.join(BASE,path) for path in DATA_FILES}
    missing=[path for path,local in files.items() if not os.path.exists(local)]
    if missing:raise SystemExit('수집 결과 파일 없음: '+', '.join(missing))
    content={path:json.loads(open_bytes(local)) for path,local in files.items()}
    digest=hashlib.sha256(json.dumps(semantic_value(content),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    state_path=os.path.join(BASE,'.publish_state.json')
    try:state=json.loads(open_bytes(state_path))
    except (OSError,ValueError):state={}
    # Changed seats/notices publish immediately. Unchanged results share one heartbeat.
    if state.get('digest')==digest and time.time()-state.get('published_at',0)<600:
        print('예약 변경 없음 · GitHub 게시 간격 대기');return
    manifest={'version':hashlib.sha256(b''.join(open_bytes(files[path]) for path in sorted(files))).hexdigest(),'updated_at':time.time()}
    manifest_path=os.path.join(BASE,'data','snapshot.json');atomic_json(manifest_path,manifest)
    files['data/snapshot.json']=manifest_path
    publish_files(files,'update booking status from Mac')
    atomic_json(state_path,{'digest':digest,'published_at':time.time()})

if __name__=='__main__':main()
