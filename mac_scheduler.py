#!/usr/bin/env python3
"""Mac-only scheduler and loopback control bridge for the local UI.

- 빠른 수집(fast): 설정 분마다 (선상24 + 무창포·오천항·영흥도 설정 일수, 기본 14일 5분)
- 느린 수집(slow): 설정 분마다 (주요 3곳 그 이후 날짜 + 나머지 항구, 기본 30분)
- 수집 설정(날짜·분·분)은 화면에서 바꾸며 scrape_settings.json에 저장
- 수집중지: 진행 중인 수집은 끝까지 하고 이후 자동 수집을 멈춤 (재시작해도 유지)
- 수집시작: 자동 수집 재개 + 즉시 빠른 수집
- 수동수집: 지금 전체(fast+slow) 1회. 수집 중이면 끝난 뒤 이어서 실행
"""
import argparse
import json
import os
import secrets
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone, timedelta
from datetime import date
from collect_homepages import BASE, atomic_json
from season import season_window
from booking_state import OK,FAILED,PARTIAL,SKIPPED

DEFAULTS = {'near_days': 14, 'fast_minutes': 5, 'slow_minutes': 30}
LIMITS = {'near_days': (1, 60), 'fast_minutes': (1, 120), 'slow_minutes': (1, 120)}
SETTINGS = os.path.join(BASE, 'scrape_settings.json')
RUNTIME = os.path.join(BASE, 'data', 'scrape_runtime.json')
PROGRESS = os.path.join(BASE, 'data', 'scrape_progress.json')
PLAN = os.path.join(BASE, 'data', 'collection_plan.json')
ORIGINS = {'https://sangju-hue.github.io', 'http://127.0.0.1:8789', 'http://localhost:8789', 'http://127.0.0.1:8000', 'http://localhost:8000'}
ACTIONS = ('pause', 'resume', 'run', 'config', 'range', 'range_auto', 'range_auto_stop')
KST = timezone(timedelta(hours=9))
ALERT_INTERVAL = 600


def stamp():
    return datetime.now(KST).isoformat(timespec='seconds')


def read_json(path, default):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class Scheduler:
    def __init__(self):
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.alert_activation_pending = threading.Event()
        self.status_changed = threading.Event()
        self.csrf = secrets.token_urlsafe(32)
        saved = read_json(SETTINGS, {})
        self.paused = bool(saved.get('paused', False))
        self.start_required = bool(saved.get('start_required', False))
        self.cfg = {}
        for key, (lo, hi) in LIMITS.items():
            v = saved.get(key, DEFAULTS[key])
            self.cfg[key] = v if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi else DEFAULTS[key]
        self.alerts = None
        self.settings_only = False
        self.next_alerts = time.monotonic()
        self.active_boat_ids = []
        self.active_targets = {}
        self.last_queue='general'
        self.manual_pending = False
        self.next_collection_kind = None
        self.pending_range_ports = []
        self.active_range_ports = []
        self.auto_range_ports = saved.get("auto_range_ports", [])
        self.pending_range = None
        self.active_range = None
        saved_range = saved.get('auto_range')
        self.auto_range = saved_range if isinstance(saved_range, list) and len(saved_range) == 2 and all(isinstance(v, str) for v in saved_range) else None
        try:
            if self.auto_range:
                date.fromisoformat(self.auto_range[0]); date.fromisoformat(self.auto_range[1])
        except ValueError:
            self.auto_range = None
        minutes = saved.get('range_interval_minutes', 10)
        self.range_interval_minutes = minutes if isinstance(minutes, int) and not isinstance(minutes, bool) and 1 <= minutes <= 120 else 10
        self.next_range = time.monotonic()
        self.state = {'running': False, 'current_mode': None, 'last_started_at': None,
                      'last_finished_at': None, 'last_result': None, 'last_mode': None,
                      'last_fast_at': None, 'last_slow_at': None}
        previous = read_json(RUNTIME, {})
        # 완료 기록만 복원. 이전 프로세스의 실행 중 표시는 현재 실행으로 오인하지 않는다.
        for key in ('last_finished_at', 'last_result', 'last_mode', 'last_collection_kind', 'last_range', 'last_range_ports', 'last_fast_at', 'last_slow_at','last_success_at','last_diagnostics'):
            if key in previous:self.state[key] = previous[key]
        now = time.monotonic()
        self.next_fast = now  # 시작 직후 첫 수집
        self.next_slow = now

    def save_settings(self):
        atomic_json(SETTINGS, dict(self.cfg, paused=self.paused, start_required=self.start_required, auto_range=self.auto_range, auto_range_ports=self.auto_range_ports, range_interval_minutes=self.range_interval_minutes))

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            snap = dict(self.state, **self.cfg, settings_only=self.settings_only, start_required=self.start_required, paused=self.paused, manual_pending=self.manual_pending, pending_range=self.pending_range, active_range=self.active_range, active_range_ports=self.active_range_ports, auto_range=self.auto_range, auto_range_ports=self.auto_range_ports, range_interval_minutes=self.range_interval_minutes,
                        next_range_in=max(0, round(self.next_range-now)) if self.auto_range else None,
                        next_alerts_in=max(0, round(self.next_alerts-now)),
                        next_fast_in=None if self.paused else max(0, round(self.next_fast - now)),
                        next_slow_in=None if self.paused else max(0, round(self.next_slow - now)))
        snap['progress'] = read_json(PROGRESS, None) if snap['running'] else None
        snap['plan'] = read_json(PLAN, None)
        if self.alerts:snap['ntfy']=self.alerts.snapshot()
        return snap

    def control(self, action, values=None):
        if self.settings_only and action in ('resume','run','range','range_auto'):
            raise ValueError('수집 강제 중지 상태입니다. 현재 설정 조회·저장만 가능합니다.')
        if action not in ACTIONS:
            raise ValueError('지원하지 않는 동작')
        if action == 'config':
            new = {}
            for key, (lo, hi) in LIMITS.items():
                v = (values or {}).get(key)
                if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                    raise ValueError(f'{key}는 {lo}~{hi} 사이 정수')
                new[key] = v
        if action in ('range', 'range_auto'):
            try:
                d1, d2 = date.fromisoformat((values or {}).get('from_date')), date.fromisoformat((values or {}).get('to_date'))
            except (TypeError, ValueError):
                raise ValueError('날짜는 YYYY-MM-DD')
            _, first, last = season_window(datetime.now(KST))
            if d1 < first or d2 > last or d1 > d2:
                raise ValueError(f'범위는 {first}~{last} 안에서 시작일 <= 종료일')
            new_range = [d1.isoformat(), d2.isoformat()]
            ports = (values or {}).get('ports', [])
            catalog = read_json(os.path.join(BASE, 'data', 'boats.json'), {}).get('boats', [])
            allowed = {b.get('port', '') for b in catalog}
            if not isinstance(ports, list) or any(not isinstance(v, str) or v not in allowed for v in ports):
                raise ValueError('등록된 항구를 선택하세요')
            ports = list(dict.fromkeys(ports))
            if action == 'range_auto':
                minutes = (values or {}).get('interval_minutes', 10)
                if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= 120:
                    raise ValueError('범위 수집 간격은 1~120분 사이 정수')
        with self.lock:
            if action in ('resume','run','range','range_auto'):self.start_required=False
            if action == 'range':
                self.pending_range = new_range
                self.pending_range_ports = ports
            elif action == 'range_auto':
                self.auto_range = new_range
                self.auto_range_ports = ports
                self.range_interval_minutes = minutes
                self.next_range = time.monotonic()
                self.save_settings()
            elif action == 'range_auto_stop':
                self.auto_range = None
                self.save_settings()
            elif action == 'config':
                # 진행 중인 수집은 그대로 두고, 다음 예정 시각만 새 간격으로 다시 계산
                now = time.monotonic()
                old = self.cfg
                self.cfg = new
                if new['fast_minutes'] != old['fast_minutes']:
                    self.next_fast = min(self.next_fast, now + new['fast_minutes'] * 60)
                if new['slow_minutes'] != old['slow_minutes']:
                    self.next_slow = min(self.next_slow, now + new['slow_minutes'] * 60)
                self.save_settings()
            elif action == 'pause':
                self.paused = True
                self.auto_range = None
                self.save_settings()
            elif action == 'resume':
                self.paused = False
                self.next_fast = time.monotonic()
                self.save_settings()
            elif action == 'run':
                self.manual_pending = True
            if action in ('run','range'):self.save_settings()
        self.wake.set()
        self.status_changed.set()

    def request_alert_collection(self):
        # Called while Alerts may hold its own lock: never acquire Scheduler's lock here.
        if self.settings_only:return
        self.alert_activation_pending.set()
        self.wake.set()
        self.status_changed.set()

    def supplemental_alert_pairs(self):
        """Use automatic results for covered targets; collect only uncovered pairs."""
        # Full automation covers the season through its fast and slow queues.
        if not self.paused and not self.auto_range:
            return {}
        pairs = self.alerts.target_pairs()
        if not self.auto_range:
            return pairs
        catalog = self.alerts.catalog() if self.auto_range_ports else {}
        start, end = self.auto_range
        return {bid: remaining for bid, dates in pairs.items()
                if (remaining := [ds for ds in dates if not (
                    start <= ds <= end and (not self.auto_range_ports or
                    (catalog.get(int(bid)) or {}).get('port') in self.auto_range_ports))])}

    def due_mode(self):
        with self.lock:
            if self.alert_activation_pending.is_set():
                self.alert_activation_pending.clear()
                if self.start_required:
                    self.start_required=False
                    self.save_settings()
                self.next_alerts=time.monotonic()
                self.status_changed.set()
            if self.start_required:return None
            self.active_boat_ids=[];self.active_targets={}
            if self.manual_pending:
                self.manual_pending=False;self.next_collection_kind='full_once';self.last_queue='general';return 'full'
            if self.pending_range:
                self.active_range,self.pending_range=self.pending_range,None
                self.active_range_ports=self.pending_range_ports[:];self.pending_range_ports=[]
                self.next_collection_kind='range_once';self.last_queue='general';return 'range'
            now=time.monotonic();general=None
            if self.auto_range:
                _,first,last=season_window(datetime.now(KST))
                start,end=max(self.auto_range[0],first.isoformat()),min(self.auto_range[1],last.isoformat())
                if start>end:self.auto_range=None;self.save_settings()
                elif now>=self.next_range:general='range'
            elif not self.paused:
                due=[(at,mode) for at,mode in [(self.next_fast,'fast'),(self.next_slow,'slow')] if now>=at]
                if due:general=min(due)[1]
            alert_due=bool(self.alerts and now>=self.next_alerts)
            if alert_due and (not general or self.last_queue!='alerts'):
                pairs=self.supplemental_alert_pairs()
                self.next_alerts=now+ALERT_INTERVAL
                if pairs:
                    dates=sorted({ds for values in pairs.values() for ds in values})
                    self.active_targets=pairs;self.active_boat_ids=sorted(map(int,pairs))
                    self.active_range=[dates[0],dates[-1]];self.active_range_ports=[]
                    self.next_collection_kind='alerts_auto';self.last_queue='alerts';return 'range'
            if general:
                self.last_queue='general'
                if general=='range':
                    self.active_range=[start,end];self.active_range_ports=self.auto_range_ports[:]
                    self.next_range=now+self.range_interval_minutes*60;self.next_collection_kind='range_repeat'
                else:self.next_collection_kind='full_auto'
                return general
            return None

    def run_once(self, mode):
        started_mono = time.monotonic()
        with self.lock:
            self.state.update(running=True, current_mode=mode, current_collection_kind=self.next_collection_kind or ("range_once" if mode == "range" else "full_auto"), last_started_at=stamp())
            self.next_collection_kind = None
            if mode != 'range':
                self.active_range = None
            # The completion path schedules the next interval, including long runs.
            if mode in ('fast', 'full'):
                self.next_fast = started_mono + self.cfg['fast_minutes'] * 60
            if mode in ('slow', 'full'):
                self.next_slow = started_mono + self.cfg['slow_minutes'] * 60
        atomic_json(RUNTIME, self.snapshot_light())
        self.status_changed.set()
        target_file=None
        try:
            cmd = [sys.executable, os.path.join(BASE, 'run_collection.py'), '--mode', mode]
            if mode == 'range':
                cmd += ['--from-date', self.active_range[0], '--to-date', self.active_range[1]]
                if self.active_range_ports:
                    cmd += ['--ports', *self.active_range_ports]
                if self.active_targets:
                    import tempfile
                    fd,target_file=tempfile.mkstemp(prefix='fishing-targets-',suffix='.json')
                    with os.fdopen(fd,'w') as f:json.dump(self.active_targets,f)
                    cmd+=['--targets-file',target_file]
                if self.active_boat_ids:cmd+=['--boat-ids',*map(str,self.active_boat_ids)]
            result = subprocess.run(cmd, cwd=BASE)
            outcome={OK:'ok',PARTIAL:'partial',SKIPPED:'skipped'}.get(result.returncode,'failed')
        except Exception as e:
            print(type(e).__name__, str(e), flush=True)
            outcome = 'failed'
        finally:
            if target_file and os.path.exists(target_file):os.unlink(target_file)
        from collection_diagnostics import build_diagnostics
        diagnostics=build_diagnostics(BASE,self.state.get('last_started_at'))
        with self.lock:
            finished = stamp()
            completed=time.monotonic()
            if mode in ('fast','full'):self.next_fast=completed+(60 if outcome=='skipped' else self.cfg['fast_minutes']*60)
            if mode in ('slow','full'):self.next_slow=completed+(60 if outcome=='skipped' else self.cfg['slow_minutes']*60)
            kind=self.state.get('current_collection_kind')
            if kind=='alerts_auto':self.next_alerts=completed+(60 if outcome=='skipped' else ALERT_INTERVAL)
            if kind=='range_repeat':self.next_range=completed+(60 if outcome=='skipped' else self.range_interval_minutes*60)
            self.state["last_collection_kind"] = self.state.get("current_collection_kind")
            self.state["current_collection_kind"] = None
            self.state.update(running=False, current_mode=None, last_finished_at=finished, last_result=outcome, last_mode=mode,last_diagnostics=diagnostics)
            if mode == 'range':
                self.state['last_range'] = list(self.active_range)
                self.state['last_range_ports'] = self.active_range_ports[:]
                self.active_range = None
            if outcome=='ok':self.state['last_success_at']=finished
            if outcome=='ok' and mode in ('fast', 'full'):
                self.state['last_fast_at'] = finished
            if outcome=='ok' and mode in ('slow', 'full'):
                self.state['last_slow_at'] = finished
        atomic_json(RUNTIME, self.snapshot_light())
        self.status_changed.set()

    def snapshot_light(self):
        with self.lock:
            return dict(self.state, **self.cfg, paused=self.paused, auto_range=self.auto_range, auto_range_ports=self.auto_range_ports, range_interval_minutes=self.range_interval_minutes)

    def loop(self):
        while True:
            mode = self.due_mode()
            if mode:
                self.run_once(mode)
                continue
            with self.lock:
                if self.auto_range:
                    delay = max(0.5, self.next_range-time.monotonic())
                elif self.paused:
                    delay = None
                else:
                    delay = max(0.5, min(self.next_fast, self.next_slow) - time.monotonic())
            if self.alerts and self.alerts.targets()[0]:
                delay=min(delay, max(0.5,self.next_alerts-time.monotonic())) if delay is not None else max(0.5,self.next_alerts-time.monotonic())
            self.wake.wait(delay)
            self.wake.clear()


def handler(scheduler, port):
    class Handler(BaseHTTPRequestHandler):
        def trusted(self):
            origin = self.headers.get('Origin')
            return self.headers.get('Host') in {f'127.0.0.1:{port}', f'localhost:{port}'} and (not origin or origin in ORIGINS)

        def send(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            origin = self.headers.get('Origin')
            if origin in ORIGINS:
                self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            if not self.trusted():
                return self.send(403, {'error': '허용되지 않은 요청'})
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', self.headers.get('Origin', ''))
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-CSRF-Token')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.end_headers()

        def do_GET(self):
            if not self.trusted():
                return self.send(403, {'error': '허용되지 않은 요청'})
            if self.path != '/settings':
                return self.send(404, {'error': '없음'})
            self.send(200, dict(scheduler.snapshot(), csrf_token=scheduler.csrf))

        def do_POST(self):
            if not self.trusted() or self.headers.get('X-CSRF-Token') != scheduler.csrf:
                return self.send(403, {'error': '허용되지 않은 요청'})
            if self.path not in ('/control', '/alerts'):
                return self.send(404, {'error': '없음'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= (65536 if self.path=='/alerts' else 4096):
                    raise ValueError('잘못된 요청')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body,dict):raise ValueError('잘못된 요청')
                if self.path == '/alerts':
                    if not scheduler.alerts:raise ValueError('알림 연결 준비 중')
                    if body['action']=='add':scheduler.alerts.register_request(body)
                    elif body['action']=='admin_update':scheduler.alerts.admin_update(body)
                    elif body['action']=='remove':scheduler.alerts.remove(body)
                    elif body['action'] in ('pause','resume','update'):scheduler.alerts.manage(body)
                    else:scheduler.alerts.admin(body['action'],body.get('id'))
                    scheduler.wake.set()
                else:scheduler.control(body['action'], body)
            except (ValueError, KeyError) as e:
                return self.send(400, {'error':str(e)})
            except Exception:
                return self.send(502, {'error':'ntfy 전송 실패: 연결 및 토픽 권한 확인'})
            self.send(200, scheduler.snapshot())

        def log_message(self, fmt, *args):
            pass  # 화면이 몇 초마다 상태를 묻기 때문에 접속 로그는 남기지 않음

    return Handler


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=8788)
    p.add_argument('--settings-only', action='store_true', help='점검용: 수집 없이 설정 서버만 실행')
    args = p.parse_args()
    s = Scheduler()
    if not s.state.get('last_diagnostics'):
        from collection_diagnostics import build_diagnostics
        s.state['last_diagnostics']=build_diagnostics(BASE,read_json(RUNTIME,{}).get('last_started_at'))
    s.settings_only = args.settings_only
    if args.settings_only:s.paused=True
    s.save_settings()
    from ntfy_alerts import Alerts
    s.alerts = Alerts(BASE)
    def alert_changed():
        s.next_alerts=time.monotonic();s.wake.set();s.status_changed.set()
    s.alerts.on_change=alert_changed
    s.alerts.on_activate=s.request_alert_collection
    from collector_status import status_loop
    threading.Thread(target=status_loop,args=(s,),daemon=True).start()
    threading.Thread(target=s.alerts.loop, daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(s, args.port))
    if not args.settings_only:
        threading.Thread(target=s.loop, daemon=True).start()
    print(f'맥 수집기: 주요3곳 {s.cfg["near_days"]}일 {s.cfg["fast_minutes"]}분 / 그 외 {s.cfg["slow_minutes"]}분, {"중지 상태" if s.paused else "자동 수집 켜짐"}, 제어 http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
