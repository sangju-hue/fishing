#!/usr/bin/env python3
"""맥에서 긁은 홈페이지 예약현황을 GitHub에 푸시."""
import base64
import json
import os
import sys
import urllib.request
import hashlib

REPO = "sangju-hue/fishing"
DATA_FILES = ("data/status.json", "data/status_homepages.json", "data/site_health.json", "data/site_health_sunsang24.json")
BASE = os.path.dirname(os.path.abspath(__file__))
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

def publish_files(files, message):
    """Publish an explicit file list atomically; never upload tokens or diagnostics."""
    token=get_token();root=f"https://api.github.com/repos/{REPO}"
    def call(method,path,data=None):return api(method,root+path,token,data)
    head=call("GET","/git/ref/heads/main")['object']['sha']
    base_tree=call("GET","/git/commits/"+head)['tree']['sha']
    old={x['path']:x['sha'] for x in call("GET","/git/trees/"+base_tree+"?recursive=1")['tree']}
    entries=[]
    for remote,local in files.items():
        if remote=='.github_token' or remote.startswith('.ntfy/') or remote=='scrape_settings.json' or remote.startswith('diagnostics/') or '..' in remote.split('/'):
            raise ValueError('업로드 대상 경로 오류')
        with open(local,'rb') as f:raw=f.read()
        sha=hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
        if old.get(remote)==sha:continue
        blob=call("POST","/git/blobs",{'content':base64.b64encode(raw).decode(),'encoding':'base64'})
        entries.append({'path':remote,'mode':'100644','type':'blob','sha':blob['sha']})
    if not entries:
        print('변경된 파일 없음');return head
    tree=call('POST','/git/trees',{'base_tree':base_tree,'tree':entries})
    commit=call('POST','/git/commits',{'message':message,'tree':tree['sha'],'parents':[head]})
    # A concurrent update fails here rather than overwriting someone else's commit.
    call('PATCH','/git/refs/heads/main',{'sha':commit['sha'],'force':False})
    print('푸시 완료:',', '.join(e['path'] for e in entries))
    return commit['sha']

def main():
    files={path:os.path.join(BASE,path) for path in DATA_FILES}
    missing=[path for path,local in files.items() if not os.path.exists(local)]
    if missing:raise SystemExit('수집 결과 파일 없음: '+', '.join(missing))
    publish_files(files,'update booking status from Mac')

if __name__ == "__main__":
    main()
