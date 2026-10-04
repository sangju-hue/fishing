"""Public collector activity feed. Contains no subscription or owner data."""
from datetime import datetime
from season import KST

HEARTBEAT_SECONDS = 300
VALID_SECONDS = 900

def public_status(scheduler):
    with scheduler.lock:
        s = dict(scheduler.state)
        stopped = scheduler.settings_only or scheduler.start_required
        automatic = not scheduler.paused or bool(scheduler.auto_range)
        queued = scheduler.manual_pending or bool(scheduler.pending_range)
    # Never acquire the alerts lock while holding the scheduler lock.
    alerts_active = bool(scheduler.alerts and scheduler.alerts.targets()[0])
    state = 'running' if s.get('running') else 'waiting' if not stopped and (automatic or queued or alerts_active) else 'stopped'
    kinds = {'full_auto':'전체 자동 수집','full_once':'전체 1회 수집',
             'range_repeat':'범위 수집 반복','range_once':'범위 수집 1회','alerts_auto':'빈자리 알림 대상 수집'}
    detail = kinds.get(s.get('current_collection_kind'),'예약 정보 수집') if state == 'running' else (
        '다음 수집을 기다리는 중' if state == 'waiting' else '수집이 중지되어 있습니다')
    return {'version':1,'state':state,'detail':detail,
            'updated_at':datetime.now(KST).isoformat(),'valid_seconds':VALID_SECONDS}

def publish_status(scheduler):
    """Replace a dedicated single-file branch; never publish through ntfy.

    Parentless commits retain only the current snapshot on this branch and do
    not trigger Pages builds from main. No catalog or subscription data enters it.
    """
    import json
    from urllib.error import HTTPError
    from push_to_github import api, REPO, TOKEN_FILE
    with open(TOKEN_FILE, encoding='utf-8') as f:
        token = f.read().strip()
    if not token:
        raise ValueError('GitHub 상태 게시 인증정보 없음')
    root = f'https://api.github.com/repos/{REPO}'
    payload = public_status(scheduler)
    tree = api('POST', root+'/git/trees', token, {'tree': [
        {'path': 'status.json', 'mode': '100644', 'type': 'blob',
         'content': json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}
    ]})
    commit = api('POST', root+'/git/commits', token,
                 {'message': 'collector status', 'tree': tree['sha'], 'parents': []})
    try:
        api('PATCH', root+'/git/refs/heads/collector-status', token,
            {'sha': commit['sha'], 'force': True})
    except HTTPError as e:
        if e.code not in (404, 422):
            raise
        # GitHub can report a missing reference as 422 rather than 404.
        try:
            api('GET', root+'/git/ref/heads/collector-status', token)
        except HTTPError as missing:
            if missing.code != 404:
                raise
        else:
            raise e
        api('POST', root+'/git/refs', token,
            {'ref': 'refs/heads/collector-status', 'sha': commit['sha']})
    return payload

def status_loop(scheduler):
    import time
    retry = 15
    while True:
        scheduler.status_changed.clear()
        try:
            publish_status(scheduler)
            delay = HEARTBEAT_SECONDS
            retry = 15
        except Exception:
            delay = retry
            retry = min(300, retry * 2)
        scheduler.status_changed.wait(delay)
        # Coalesce rapid queue changes without delaying the collection worker.
        if scheduler.status_changed.is_set():
            time.sleep(2)
