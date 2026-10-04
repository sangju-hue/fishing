"""Public collector activity feed. Contains no subscription or owner data."""
from datetime import datetime
from season import KST

HEARTBEAT_SECONDS = 60
VALID_SECONDS = 150

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
    import json
    payload = public_status(scheduler)
    scheduler.alerts.publish(scheduler.alerts.config['inbox']+'-collector',
                            '수집 상태',json.dumps(payload,ensure_ascii=False))

def status_loop(scheduler):
    while True:
        scheduler.status_changed.clear()
        try:
            publish_status(scheduler)
            delay = HEARTBEAT_SECONDS
        except Exception:
            delay = 15
        scheduler.status_changed.wait(delay)
