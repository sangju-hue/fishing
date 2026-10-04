"""Public Pages -> encrypted ntfy inbox -> Mac -> individual ntfy topics.
Private keys, owners, topics and delivery state stay in .ntfy/, never in Git.
"""
import base64
import json
import os
import re
import secrets
import subprocess
import threading
import time
import urllib.request
from datetime import date, datetime
from urllib.parse import urlencode,urlsplit,urlunsplit,parse_qsl
from collect_homepages import atomic_json
from season import KST, season_window
from booking_state import select_observation, observed_time

SERVER='https://ntfy.sh'
OPENSSL='/usr/bin/openssl'

def read(path, default):
    try:
        with open(path, encoding='utf-8') as f:return json.load(f)
    except (OSError,ValueError):return default

def stamp():return datetime.now(KST).isoformat(timespec='seconds')

def booking_url(raw, ds):
    if not raw:return None
    p=urlsplit(raw)
    if p.scheme not in ('http','https'):return None
    year,month,day=ds.split('-');q=dict(parse_qsl(p.query));path=p.path;fragment=p.fragment
    if p.hostname and p.hostname.endswith('.sunsang24.com'):
        path='/ship/schedule_fleet/'+year+month;q={};fragment='d'+ds
    elif '/niabbs5' in path:
        path=re.sub(r'/(?:inc\.php|doc/sub2_in2?\.htm)$','/doc/sub2_in.htm',path);q={'toYear':year,'toMonth':month,'callday':day};fragment=''
    elif q.get('mid')=='bk' or 'sel' in q:
        q.update(mid='bk',year=year,month=month,day=day,mode='list',sel='day',won='1');fragment='list'
    return urlunsplit((p.scheme,p.netloc,path,urlencode(q),fragment))

class Alerts:
    def __init__(self, base):
        self.base=base;self.directory=os.path.join(base,'.ntfy');os.makedirs(self.directory,mode=0o700,exist_ok=True)
        self.key=os.path.join(self.directory,'private.pem');self.path=os.path.join(self.directory,'state.json')
        self.topic_changed=threading.Event();self.lock=threading.RLock();self.state=read(self.path,{'subscriptions':[], 'seen':[], 'cursor':None})
        self.state.setdefault('subscriptions',[]);self.state.setdefault('seen',[]);self.state.setdefault('receipts',{})
        for receipt in self.state['receipts'].values():receipt.pop('retry_at',None)
        self._file_cache={};self._last_check_signature=None;self._last_saved=None;self._check_day=None
        self.online=False;self.error='';self.last_poll=None;self.on_change=lambda:None;self.on_activate=lambda:None;self.topics_dirty=True;self.topics_feed_dirty=True;self.last_topic_feed=0;self.topics_error=''
        if not os.path.exists(self.key):
            subprocess.run([OPENSSL,'genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:3072','-out',self.key],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            os.chmod(self.key,0o600)
        config_path=os.path.join(base,'data','ntfy_public.json')
        config=read(config_path,{})
        if not config.get('inbox'):config['inbox']='fishing-requests-'+secrets.token_hex(16)
        pub=subprocess.run([OPENSSL,'pkey','-in',self.key,'-pubout','-outform','DER'],check=True,capture_output=True).stdout
        self.config={'version':1,'supports_multi':True,'supports_manage':True,'supports_min_seats':True,'supports_receipts':True,'server':SERVER,'inbox':config['inbox'],'public_key':base64.b64encode(pub).decode()}
        atomic_json(config_path,self.config)
        self.save()

    def save(self):
        with self.lock:
            # Expired rules no longer reserve topics or consume capacity.
            today=datetime.now(KST).date().isoformat()
            self.state['subscriptions']=[r for r in self.state['subscriptions'] if r['date']>=today]
            serialized=json.dumps(self.state,sort_keys=True,ensure_ascii=False)
            if serialized!=self._last_saved:
                atomic_json(self.path,self.state);os.chmod(self.path,0o600);self._last_saved=serialized
            today=datetime.now(KST).date().isoformat()
            topics=sorted({r['topic'] for r in self.state['subscriptions'] if r['date']>=today})
            public_path=os.path.join(self.base,'data','ntfy_topics.json')
            old=read(public_path,{})
            if old.get('topics')!=topics:
                atomic_json(public_path,{'topics':topics,'updated_at':datetime.now(KST).isoformat()})
                self.topics_dirty=True;self.topics_feed_dirty=True;self.topic_changed.set()

    def publish_topic_feed(self):
        if not self.topics_feed_dirty:return
        path=os.path.join(self.base,'data','ntfy_topics.json');before=read(path,{})
        try:
            self.publish(self.config['inbox']+'-topics','사용 중인 토픽 목록',json.dumps(before,ensure_ascii=False))
            with self.lock:self.topics_feed_dirty=read(path,{})!=before;self.last_topic_feed=time.monotonic()
        except Exception:self.topics_error='실시간 토픽 목록 갱신 재시도 대기'

    def publish_topics(self):
        if not self.topics_dirty:return
        import push_to_github
        path=os.path.join(self.base,'data','ntfy_topics.json')
        if not os.path.exists(push_to_github.TOKEN_FILE):
            self.topics_error='GitHub 토픽 목록 갱신용 맥 인증정보 없음';return
        before=read(path,{})
        try:
            push_to_github.publish_files({'data/ntfy_topics.json':path,'data/ntfy_public.json':os.path.join(self.base,'data','ntfy_public.json')},'update active ntfy topics and capabilities')
            with self.lock:self.topics_dirty=read(path,{})!=before;self.topics_error=''
        except Exception:self.topics_error='GitHub 토픽 목록 갱신 재시도 대기'


    def catalog(self):return {int(b['bid']):b for b in read(os.path.join(self.base,'data','boats.json'),{}).get('boats',[])}

    def register(self,payload,persist=True,catalog=None):
        catalog=self.catalog() if catalog is None else catalog
        owner=payload.get('owner','');topic=payload.get('topic','');ds=payload.get('date','');bid=payload.get('bid')
        if not isinstance(owner,str) or not re.fullmatch(r'[a-f0-9]{32}',owner):raise ValueError('사용자 식별값 오류')
        if not isinstance(topic,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',topic) or topic==self.config['inbox']:raise ValueError('토픽은 영문·숫자·_- 1~64자')
        if not isinstance(bid,int) or isinstance(bid,bool) or bid not in catalog:raise ValueError('선박을 선택하세요')
        try:d=date.fromisoformat(ds)
        except (TypeError,ValueError):raise ValueError('날짜를 선택하세요')
        _,first,last=season_window()
        if not first<=d<=last:raise ValueError('오늘 이후 9~11월 날짜만 등록할 수 있습니다')
        if catalog[bid].get('canonical_bid') is not None:raise ValueError('현재 명부의 대표 선박을 선택하세요')
        minimum=payload.get('min_seats',1)
        if not isinstance(minimum,int) or isinstance(minimum,bool) or not 1<=minimum<=100:raise ValueError('최소 빈자리는 1~100 사이 정수')
        label=payload.get('label','')
        if not isinstance(label,str) or not label.strip():raise ValueError('이름을 입력해 주세요. 예: 홍길동')
        if len(label.strip()) > 16:raise ValueError('이름은 16자까지')
        with self.lock:
            today=datetime.now(KST).date().isoformat()
            self.state['subscriptions']=[r for r in self.state['subscriptions'] if r['date']>=today]
            rows=self.state['subscriptions']
            if any(r['topic']==topic and r['owner']!=owner for r in rows):raise ValueError('다른 사용자가 사용 중인 토픽입니다. 새 토픽을 생성하세요')
            old=next((r for r in rows if r['owner']==owner and r['topic']==topic and r['bid']==bid and r['date']==ds),None)
            if old:
                old['enabled']=True
                if old.get('min_seats',1)!=minimum:old['notified']=False
                old['min_seats']=minimum
                old['label']=str(payload.get('label',old.get('label',''))).strip()
                if persist:self.save();self.on_change();self.on_activate()
                return old
            if len(rows)>=50000 or sum(r['owner']==owner for r in rows)>=10000:raise ValueError('등록 수 제한: 사용자당 10000개, 전체 50000개')
            boat=catalog[bid]
            row={'id':secrets.token_hex(8),'owner':owner,'topic':topic,'bid':bid,'date':ds,'label':str(payload.get('label','')),'boat':boat['name'],'port':boat.get('port',''),'enabled':True,'min_seats':minimum,'created_at':stamp(),'last_checked_at':None,'last_sent_at':None,'last_status':'unknown','observed':None,'notified':False,'error':''}
            rows.append(row)
            if persist:self.save();self.on_change();self.on_activate()
            return row

    @staticmethod
    def port_name(raw):
        raw=str(raw or '').strip()
        for pattern,name in [(r'^대천항(?:\s*\(|$)','대천항'),(r'^전곡항(?:\s*\(|$)','전곡항'),(r'^신진도$','신진도항'),(r'^비응항(?:\s*\(|$)','비응항'),(r'^남항','남항'),(r'^(?:영흥도|진두선착장|진두항)','영흥도')]:
            if re.search(pattern,raw):return name
        return raw

    def register_request(self,payload):
        if 'ports' not in payload and 'bids' not in payload and payload.get('bid')!=0:return [self.register(payload)]
        catalog=self.catalog();ports=payload.get('ports',[payload.get('port')]);bids=payload.get('bids',[])
        if not isinstance(ports,list) or len(ports)>1000 or not ports or not all(isinstance(p,str) and p for p in ports):raise ValueError('항구를 선택하세요')
        if not isinstance(bids,list) or len(bids)>1000 or not all(isinstance(i,int) and not isinstance(i,bool) for i in bids):raise ValueError('배 선택 오류')
        eligible={bid for bid,b in catalog.items() if b.get('canonical_bid') is None and ('*' in ports or self.port_name(b.get('port')) in ports)}
        ids=sorted(set(bids) if bids else eligible)
        if not ids or not set(ids)<=eligible:raise ValueError('선택한 항구의 배를 선택하세요')
        group=payload.get('group') or secrets.token_hex(8)
        if not isinstance(group,str) or not re.fullmatch(r'[a-f0-9]{16}',group):raise ValueError('신청 묶음 오류')
        with self.lock:
            import copy
            before=copy.deepcopy(self.state['subscriptions'])
            try:rows=[self.register(dict(payload,bid=bid),persist=False,catalog=catalog) for bid in ids]
            except Exception:
                self.state['subscriptions']=before
                raise
            for row in rows:row.update(scope_port='*' if '*' in ports else ', '.join(ports),scope_ports=ports,scope_all=not bids,request_group=group)
            self.save();self.on_change();self.on_activate();return rows

    def matches(self,r,p):
        if r['owner']!=p.get('owner') or r['topic']!=p.get('topic') or r['date']!=p.get('date'):return False
        if p.get('group'):return r.get('request_group')==p['group']
        return r['bid']==p.get('bid') or p.get('bid')==0 and (p.get('port')=='*' or self.port_name(r.get('port'))==p.get('port'))

    def remove(self,payload):
        with self.lock:
            self.state['subscriptions']=[r for r in self.state['subscriptions'] if not self.matches(r,payload)]
            self.save();self.on_change()

    def manage(self,payload):
        action=payload.get('action')
        with self.lock:
            import copy
            before=copy.deepcopy(self.state['subscriptions'])
            if action=='update':
                old=payload.get('old')
                if not isinstance(old,list) or len(old) not in (4,5):raise ValueError('수정할 신청 정보 없음')
                selector=dict(payload,bid=old[0],date=old[1],topic=old[2],port=old[3],group=old[4] if len(old)>4 else None)
            else:selector=payload
            rows=[r for r in self.state['subscriptions'] if self.matches(r,selector)]
            if not rows:raise ValueError('본인이 신청한 알림을 찾지 못했습니다')
            if action in ('pause','resume'):
                for r in rows:r['enabled']=action=='resume'
            elif action=='update':
                self.state['subscriptions']=[r for r in self.state['subscriptions'] if r not in rows]
                try:self.register_request(payload)
                except Exception:
                    self.state['subscriptions']=before
                    raise
            else:raise ValueError('지원하지 않는 동작')
            self.save();self.on_change()
            if action=='resume':self.on_activate()

    def group_key(self,row):
        import hashlib
        key=[row['owner'],row['topic'],row['date'],row.get('request_group') or row.get('created_at'),None if row.get('request_group') else row.get('scope_port',self.port_name(row.get('port')))]
        return hashlib.sha256(json.dumps(key,ensure_ascii=False).encode()).hexdigest()[:24]

    def admin_update(self,payload):
        import copy
        with self.lock:
            keys=set(str(payload.get('id','')).split(','))
            rows=[r for r in self.state['subscriptions'] if self.group_key(r) in keys]
            if not rows or len({r['owner'] for r in rows})!=1:raise ValueError('수정할 신청 묶음 없음')
            before=copy.deepcopy(self.state['subscriptions'])
            self.state['subscriptions']=[r for r in self.state['subscriptions'] if r not in rows]
            try:
                p=dict(payload,owner=rows[0]['owner'])
                if 'ports' not in p and p.get('bid')==0 and all(r['date']==p.get('date') and (p.get('port')=='*' or self.port_name(r.get('port'))==p.get('port')) for r in rows):
                    updated=[self.register(dict(p,bid=r['bid']),persist=False) for r in rows]
                    for r in updated:r.update(scope_all=True,scope_port=p['port'])
                else:updated=self.register_request(p)
                if not any(r['enabled'] for r in rows):
                    for r in updated:r['enabled']=False
            except Exception:
                self.state['subscriptions']=before
                raise
            self.save();self.on_change()

    def admin(self,action,identifier):
        with self.lock:
            if action in ('group_delete','group_pause','group_resume'):
                keys=set(str(identifier).split(','))
                rows=[r for r in self.state['subscriptions'] if self.group_key(r) in keys]
                if not rows:raise ValueError('신청 묶음 없음')
                if action=='group_delete':self.state['subscriptions']=[r for r in self.state['subscriptions'] if r not in rows]
                else:
                    for r in rows:r['enabled']=action=='group_resume'
                self.save();self.on_change()
                if action=='group_resume':self.on_activate()
                return
            row=next((r for r in self.state['subscriptions'] if r['id']==identifier),None)
            if not row:raise ValueError('등록 항목 없음')
            if action=='delete':self.state['subscriptions'].remove(row)
            elif action=='toggle':row['enabled']=not row['enabled']
            elif action=='test':
                self.publish(row['topic'],'빈자리 알림 연결 테스트',f"{row['date']} {row['port']} {row['boat']} 알림 수신을 확인해 주세요.")
                row['last_test_at']=stamp()
            else:raise ValueError('지원하지 않는 동작')
            self.save()
            if action=='toggle' and row['enabled']:self.on_activate()

    def publish(self,topic,title,message,click=None):
        body={'topic':topic,'title':title,'message':message,'tags':['fishing'],'priority':3}
        if click:body['click']=click
        request=urllib.request.Request(SERVER,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(request,timeout=4) as response:
            result=json.loads(response.read(16384))
            if not result.get('id'):raise ValueError('ntfy 전송 응답 오류')

    def decrypt(self,message):
        if not isinstance(message,str) or len(message)>24000:raise ValueError('신청 형식 오류')
        if message.startswith('fish1:'):raw=base64.b64decode(message[6:],validate=True);parts=None
        elif message.startswith('fish2:'):
            parts=[base64.b64decode(x,validate=True) for x in message[6:].split('.')]
            if len(parts)!=4:raise ValueError('신청 형식 오류')
            raw=parts[0]
        else:raise ValueError('신청 형식 오류')
        result=subprocess.run([OPENSSL,'pkeyutl','-decrypt','-inkey',self.key,'-pkeyopt','rsa_padding_mode:oaep','-pkeyopt','rsa_oaep_md:sha256','-pkeyopt','rsa_mgf1_md:sha256'],input=raw,capture_output=True,timeout=5)
        if result.returncode:raise ValueError('신청 암호화 확인 실패')
        if parts:
            import hmac,hashlib
            secret=result.stdout
            if len(secret)!=64 or len(parts[1])!=16 or not hmac.compare_digest(hmac.new(secret[32:],parts[1]+parts[2],hashlib.sha256).digest(),parts[3]):raise ValueError('신청 암호화 확인 실패')
            result=subprocess.run([OPENSSL,'enc','-aes-256-cbc','-d','-K',secret[:32].hex(),'-iv',parts[1].hex()],input=parts[2],capture_output=True,timeout=5)
            if result.returncode:raise ValueError('신청 암호화 확인 실패')
        return json.loads(result.stdout)

    def process_item(self,item):
        if item.get('event')=='open':self.poll();return
        if item.get('event')!='message':return
        identifier=item.get('id')
        if identifier in self.state['seen']:return
        p={};rows=[];response=None;request_id=None
        try:
            p=self.decrypt(item.get('message'));action=p.get('action')
            request_id=p.get('request_id');reply=p.get('reply_topic')
            if request_id is not None and (not isinstance(request_id,str) or not re.fullmatch(r'[a-f0-9]{32}',request_id) or not isinstance(reply,str) or not re.fullmatch(r'fishing-reply-[a-f0-9]{32}',reply)):raise ValueError('신청 응답 경로 오류')
            with self.lock:
                prior=self.state['receipts'].get(request_id) if request_id else None
                if prior:
                    if prior['owner']!=p.get('owner'):raise ValueError('신청 식별값 오류')
                    response=prior['result'];prior['pending']=True
                else:
                    if action=='add':rows=self.register_request(p)
                    elif action=='delete':self.remove(p)
                    elif action in ('pause','resume','update'):self.manage(p)
                    else:raise ValueError('지원하지 않는 신청')
                    response={'request_id':request_id,'ok':True}
        except Exception as e:
            error=str(e) if isinstance(e,ValueError) else '신청 처리 오류'
            self.state['last_request_error']=error;self.state['last_request_error_at']=stamp()
            response={'request_id':request_id,'ok':False,'error':error}
        with self.lock:
            if request_id and re.fullmatch(r'[a-f0-9]{32}',str(request_id)) and re.fullmatch(r'fishing-reply-[a-f0-9]{32}',str(p.get('reply_topic',''))):
                if request_id not in self.state['receipts']:
                    self.state['receipts'][request_id]={'owner':p.get('owner'),'topic':p['reply_topic'],'result':response,'pending':True,'created_at':stamp()}
                    self.state['receipts']=dict(list(self.state['receipts'].items())[-2000:])
            self.state['seen']=(self.state['seen']+[identifier])[-1500:];self.state['cursor']=identifier;self.save()
        self.deliver_receipts()
        # Existing phone registration confirmation remains compatible with old clients.
        if rows:
            row=rows[0]
            try:self.publish(row['topic'],'빈자리 알림 등록 완료',f"{row['date']} 선택 항구·배 {len(rows)}척 · {row.get('min_seats',1)}자리 이상일 때 알립니다.")
            except Exception:
                with self.lock:row['error']='등록 완료 안내 전송 실패: 토픽 권한 또는 ntfy 연결 확인';self.save()
        self.last_poll=stamp();self.online=True;self.error=''

    def deliver_receipts(self):
        with self.lock:
            pending=[(key,dict(r)) for key,r in self.state['receipts'].items() if r.get('pending') and time.monotonic()>=r.get('retry_at',0)]
        for key,r in pending:
            try:self.publish(r['topic'],'신청 처리 결과',json.dumps(r['result'],ensure_ascii=False));success=True
            except Exception:success=False
            with self.lock:
                current=self.state['receipts'].get(key)
                if current:
                    current['pending']=not success
                    current['retry_at']=time.monotonic()+30
                    self.save()

    def poll(self):
        cursor=self.state.get('cursor');params=urlencode({'poll':'1','since':cursor or 'all'})
        with urllib.request.urlopen(f"{SERVER}/{self.config['inbox']}/json?{params}",timeout=4) as response:
            for line in response:
                self.process_item(json.loads(line))

    def stream_requests(self):
        while True:
            try:
                params=urlencode({'since':self.state.get('cursor') or 'all'})
                with urllib.request.urlopen(f"{SERVER}/{self.config['inbox']}/json?{params}",timeout=75) as response:
                    self.online=True;self.error=''
                    pending=b''
                    while True:
                        chunk=response.read1(4096)
                        if not chunk:break
                        pending+=chunk
                        if len(pending)>1024*1024:raise ValueError('신청 스트림 크기 초과')
                        while b'\n' in pending:
                            line,pending=pending.split(b'\n',1)
                            if line.strip():self.process_item(json.loads(line))
            except Exception:
                self.online=False;self.error='ntfy 실시간 신청 연결 재시도 중'
                time.sleep(3)

    def topic_updates(self):
        self.topic_changed.set();retry=3
        while True:
            self.topic_changed.wait();self.topic_changed.clear()
            self.publish_topic_feed()
            self.publish_topics()
            if self.topics_feed_dirty or self.topics_dirty:
                self.topic_changed.wait(retry);retry=min(60,retry*2);self.topic_changed.set()
            else:retry=3

    def targets(self):
        with self.lock:
            today=datetime.now(KST).date().isoformat()
            rows=[r for r in self.state['subscriptions'] if r['enabled'] and r['date']>=today]
            return sorted({r['bid'] for r in rows}),sorted({r['date'] for r in rows})

    def target_pairs(self):
        with self.lock:
            today=datetime.now(KST).date().isoformat();catalog=self.catalog();pairs={}
            sun=read(os.path.join(self.base,'data','status.json'),{});hp=read(os.path.join(self.base,'data','status_homepages.json'),{})
            for r in self.state['subscriptions']:
                if not r['enabled'] or r['date']<today or r['bid'] not in catalog:continue
                bid=catalog[r['bid']].get('canonical_bid') or r['bid']
                info=select_observation((sun,hp),r['date'],bid)
                age=datetime.now(KST).timestamp()-observed_time(info or {})
                if 0<=age<240:continue
                pairs.setdefault(str(bid),set()).add(r['date'])
            return {k:sorted(v) for k,v in pairs.items()}

    def snapshot(self):
        with self.lock:
            rows=[dict({k:v for k,v in r.items() if k!='owner'},group_id=self.group_key(r)) for r in self.state['subscriptions']]
            return {'realtime_topics':True,'supports_receipts':True,'supports_multi':True,'supports_manage':True,'supports_min_seats':True,'online':self.online,'error':self.error or self.topics_error,'last_poll_at':self.last_poll,'last_request_error':self.state.get('last_request_error'),'subscriptions':rows,'check_minutes':10}

    def check(self):
        today=datetime.now(KST).date().isoformat()
        with self.lock:
            if self._check_day!=today:self.save();self._check_day=today
            active=[r for r in self.state['subscriptions'] if r['enabled'] and r['date']>=today]
            if not active:return
            signature=tuple((r['id'],r['bid'],r['date'],r.get('min_seats',1),r.get('notified'),r.get('error')) for r in active)
        def cached(filename):
            path=os.path.join(self.base,'data',filename)
            try:st=os.stat(path);version=(st.st_mtime_ns,st.st_size)
            except OSError:version=None
            old=self._file_cache.get(filename)
            if old is None or old[0]!=version:self._file_cache[filename]=(version,read(path,{}))
            return self._file_cache[filename][1]
        raw=cached('boats.json');catalog={int(b['bid']):b for b in raw.get('boats',[])}
        sun=cached('status.json');hp=cached('status_homepages.json');health=cached('site_health.json')
        versions=tuple((k,v[0]) for k,v in sorted(self._file_cache.items()))
        freshness=tuple(0<=datetime.now(KST).timestamp()-observed_time(select_observation((sun,hp),r['date'],(catalog.get(r['bid']) or {}).get('canonical_bid') or r['bid']) or {})<=900 for r in active)
        fingerprint=(versions,signature,today,freshness)
        if fingerprint==self._last_check_signature and not any(r.get('error') for r in active):return
        self._last_check_signature=fingerprint
        notices={};date_notices={}
        for site in health.get('sites',{}).values():
            for name,text in site.get('boat_notices',{}).items():notices[str(site.get('boat_ids',{}).get(name))]=text
            for name,dates in site.get('date_notices',{}).items():date_notices[str(site.get('boat_ids',{}).get(name))]=dates
        pending={}
        with self.lock:
            for r in self.state['subscriptions'][:]:
                if r not in self.state['subscriptions'] or not r['enabled']:continue
                if r['date']<datetime.now(KST).date().isoformat():r['last_status']='expired';continue
                b=catalog.get(r['bid']);bid=str((b or {}).get('canonical_bid') or r['bid']);ds=r['date']
                info=select_observation((sun,hp),ds,bid)
                r['last_checked_at']=stamp()
                if not b or not info:r['last_status']='unknown';continue
                checked=info.get('checked_at')
                try:fresh=0<=(datetime.now(KST).timestamp()-observed_time(info))<=900
                except (ValueError,TypeError):fresh=False
                if not fresh:r['last_status']='stale';continue
                state=(b.get('status_overrides') or {}).get(ds) or info.get('status')
                state={'a':'available','f':'full','c':'cancelled','m':'maintenance','w':'weather','p':'conditional'}.get(state,state)
                if bid in notices:state='maintenance'
                if ds in date_notices.get(bid,{}):state='conditional'
                r['last_status']=state;r['remaining']=info.get('remaining');r['source_checked_at']=checked
                count=info.get('remaining')
                known_count=isinstance(count,int) and not isinstance(count,bool)
                qualifies=state=='available' and known_count and count>=r.get('min_seats',1)
                if r.get('observed')!=checked:
                    r['observed']=checked
                    if state!='available' or known_count and not qualifies:r['notified']=False
                if qualifies and not r.get('notified'):
                    if info.get('remaining') is not None and info['remaining']<=0:continue
                    pending.setdefault((r['topic'],ds,bid),[]).append((r,checked,b,info))
            self.save()
        for (topic,ds,bid),items in pending.items():
            # One delivery per topic/date/boat; cancellation and updates are rechecked.
            with self.lock:
                items=[(r,checked,b,info) for r,checked,b,info in items if r in self.state['subscriptions'] and r['enabled'] and not r.get('notified') and r.get('observed')==checked]
                if not items:continue
                _,_,b,info=items[0]
                link=booking_url(b.get('booking_page') or info.get('source_url') or b.get('channels',{}).get('sunsang24') or b.get('channels',{}).get('homepage'),ds)
                title=f"예약일 {ds[5:]} · {b['name']} 빈자리"
                message=f"{ds}\n{b.get('port','')} · {b['name']} · 잔여 {info['remaining']}석\n예약현황에서 최종 확인해 주세요."
                try:
                    self.publish(topic,title,message,link)
                    for r,checked,b,info in items:r['notified']=True;r['last_sent_at']=stamp();r['error']=''
                except Exception:
                    for r,checked,b,info in items:r['error']='전송 실패: ntfy 연결·토픽 권한 확인 (다음 확인 때 재시도)'
                self.save()



    def loop(self):
        threading.Thread(target=self.stream_requests,daemon=True).start()
        threading.Thread(target=self.topic_updates,daemon=True).start()
        while True:
            try:self.deliver_receipts();self.check()
            except Exception:self.error='예약 데이터 확인 실패 · 재시도 대기'
            time.sleep(30)
