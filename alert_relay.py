"""Outbound-only Mac relay client. Configuration and credentials remain private."""
import json, os, re, time, urllib.request
from urllib.parse import urlsplit
from ntfy_alerts import read

def config(base):
    c=read(os.path.join(base,'.ntfy','relay.json'),{})
    u=c.get('url','');p=urlsplit(u)
    if p.scheme!='https' or not p.hostname or not p.hostname.endswith('.workers.dev') or p.username or p.password or p.query or p.fragment or p.path not in ('','/'):
        return None
    if not isinstance(c.get('agent_token'),str) or not re.fullmatch(r'[a-f0-9]{64}',c['agent_token']):return None
    return dict(c,url=u.rstrip('/'))

def request(c,path,body=None):
    raw=json.dumps(body).encode() if body is not None else None
    req=urllib.request.Request(c['url']+path,data=raw,headers={'Authorization':'Bearer '+c['agent_token'],'Content-Type':'application/json','User-Agent':'BadajariMac/1.0'})
    with urllib.request.urlopen(req,timeout=10) as r:return json.loads(r.read(600000))

def poll_once(alerts,c):
    items=request(c,'/agent/requests').get('requests',[])
    if not isinstance(items,list) or len(items)>20:raise ValueError('중계 응답 형식 오류')
    for item in items:
        result=alerts.process_relay_request(item.get('id'),item.get('message'))
        request(c,'/agent/results',result)
    alerts.last_poll=__import__('ntfy_alerts').stamp();alerts.online=True;alerts.error=''

def loop(alerts):
    retry=5
    while True:
        c=config(alerts.base)
        try:
            if not c:raise ValueError('알림 중계 설정 확인 필요')
            poll_once(alerts,c);retry=5
        except Exception:
            alerts.online=False;alerts.error='알림 신청 중계 연결 재시도 중';retry=min(300,retry*2)
        time.sleep(retry)
