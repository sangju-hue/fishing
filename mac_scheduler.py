#!/usr/bin/env python3
"""Mac-only scheduler and loopback control bridge for the local UI.

- 빠른 수집(fast): 5분마다 (선상24 + 무창포·오천항·영흥도 30일)
- 느린 수집(slow): 30분마다 (주요 3곳 31일 이후 + 나머지 항구)
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
from collect_homepages import BASE, atomic_json

FAST_MINUTES = 5
SLOW_MINUTES = 30
SETTINGS = os.path.join(BASE, 'scrape_settings.json')
RUNTIME = os.path.join(BASE, 'data', 'scrape_runtime.json')
PROGRESS = os.path.join(BASE, 'data', 'scrape_progress.json')
PLAN = os.path.join(BASE, 'data', 'collection_plan.json')
ORIGINS = {'https://sangju-hue.github.io', 'http://127.0.0.1:8789', 'http://localhost:8789', 'http://127.0.0.1:8000', 'http://localhost:8000'}
ACTIONS = ('pause', 'resume', 'run')
KST = timezone(timedelta(hours=9))


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
        self.csrf = secrets.token_urlsafe(32)
        self.paused = bool(read_json(SETTINGS, {}).get('paused', False))
        self.manual_pending = False
        self.state = {'running': False, 'current_mode': None, 'last_started_at': None,
                      'last_finished_at': None, 'last_result': None, 'last_mode': None,
                      'last_fast_at': None, 'last_slow_at': None}
        now = time.monotonic()
        self.next_fast = now  # 시작 직후 첫 수집
        self.next_slow = now

    def save_settings(self):
        atomic_json(SETTINGS, {'paused': self.paused, 'fast_minutes': FAST_MINUTES, 'slow_minutes': SLOW_MINUTES})

    def snapshot(self):
        with self.lock:
            now = time.monotonic()
            snap = dict(self.state, paused=self.paused, manual_pending=self.manual_pending,
                        fast_minutes=FAST_MINUTES, slow_minutes=SLOW_MINUTES,
                        next_fast_in=None if self.paused else max(0, round(self.next_fast - now)),
                        next_slow_in=None if self.paused else max(0, round(self.next_slow - now)))
        snap['progress'] = read_json(PROGRESS, None) if snap['running'] else None
        snap['plan'] = read_json(PLAN, None)
        return snap

    def control(self, action):
        if action not in ACTIONS:
            raise ValueError('지원하지 않는 동작')
        with self.lock:
            if action == 'pause':
                self.paused = True
                self.save_settings()
            elif action == 'resume':
                self.paused = False
                self.next_fast = time.monotonic()
                self.save_settings()
            elif action == 'run':
                self.manual_pending = True
        self.wake.set()

    def due_mode(self):
        with self.lock:
            if self.manual_pending:
                self.manual_pending = False
                return 'full'
            if self.paused:
                return None
            now = time.monotonic()
            fast_due, slow_due = now >= self.next_fast, now >= self.next_slow
            if fast_due and slow_due:
                return 'full'
            if fast_due:
                return 'fast'
            if slow_due:
                return 'slow'
            return None

    def run_once(self, mode):
        started_mono = time.monotonic()
        with self.lock:
            self.state.update(running=True, current_mode=mode, last_started_at=stamp())
            # 다음 예정은 '시작 시각' 기준. 수집이 길어져 지났으면 끝나는 즉시 다시 시작.
            if mode in ('fast', 'full'):
                self.next_fast = started_mono + FAST_MINUTES * 60
            if mode in ('slow', 'full'):
                self.next_slow = started_mono + SLOW_MINUTES * 60
        atomic_json(RUNTIME, self.snapshot_light())
        try:
            result = subprocess.run([sys.executable, os.path.join(BASE, 'run_collection.py'), '--mode', mode], cwd=BASE)
            outcome = 'ok' if result.returncode == 0 else 'failed'
        except Exception as e:
            print(type(e).__name__, str(e), flush=True)
            outcome = 'failed'
        with self.lock:
            finished = stamp()
            self.state.update(running=False, current_mode=None, last_finished_at=finished, last_result=outcome, last_mode=mode)
            if mode in ('fast', 'full'):
                self.state['last_fast_at'] = finished
            if mode in ('slow', 'full'):
                self.state['last_slow_at'] = finished
        atomic_json(RUNTIME, self.snapshot_light())

    def snapshot_light(self):
        with self.lock:
            return dict(self.state, paused=self.paused, fast_minutes=FAST_MINUTES, slow_minutes=SLOW_MINUTES)

    def loop(self):
        while True:
            mode = self.due_mode()
            if mode:
                self.run_once(mode)
                continue
            with self.lock:
                if self.paused:
                    delay = None
                else:
                    delay = max(0.5, min(self.next_fast, self.next_slow) - time.monotonic())
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
            if self.path != '/control':
                return self.send(404, {'error': '없음'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024:
                    raise ValueError('잘못된 요청')
                scheduler.control(json.loads(self.rfile.read(size))['action'])
            except (ValueError, KeyError):
                return self.send(400, {'error': 'pause, resume, run 만 지원합니다'})
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
    s.save_settings()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(s, args.port))
    if not args.settings_only:
        threading.Thread(target=s.loop, daemon=True).start()
    print(f'맥 수집기: 빠른 {FAST_MINUTES}분 / 느린 {SLOW_MINUTES}분, {"중지 상태" if s.paused else "자동 수집 켜짐"}, 제어 http://127.0.0.1:{args.port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()
