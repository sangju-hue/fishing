"""Prevent concurrent collection, including manually invoked fishing.sh.

수집 모드 (그룹이 끝날 때마다 바로 GitHub에 올려 화면에 먼저 반영):
  fast (5분)  : 선상24 전체 → 무창포 → 오천항 → 영흥도 홈페이지 (오늘~30일)
  slow (30분) : 무창포·오천항·영흥도 홈페이지 31일 이후 → 나머지 항구 홈페이지 전체
  full        : fast + slow (수동수집, fishing.sh 기본값)
"""
import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from collect_homepages import BASE, atomic_json, sites_from_catalog

PRIORITY_GROUPS = (('무창포', '무창포'), ('오천항', '오천'), ('영흥도', '영흥'))
NEAR_DAYS = 30
PROGRESS = os.path.join(BASE, 'data', 'scrape_progress.json')
PLAN = os.path.join(BASE, 'data', 'collection_plan.json')
KST = timezone(timedelta(hours=9))


def now():
    return datetime.now(KST).isoformat(timespec='seconds')


def progress(mode, step, started):
    atomic_json(PROGRESS, {'mode': mode, 'step': step, 'started_at': started, 'updated_at': now()})


def run(script, *args):
    started = time.monotonic()
    result = subprocess.run([sys.executable, os.path.join(BASE, script), *args], cwd=BASE)
    label = ' '.join(a for a in args if a.startswith('--'))
    print(f'[{script} {label}] {time.monotonic()-started:.0f}초, 종료코드 {result.returncode}', flush=True)
    return result.returncode


def load_boats():
    return json.load(open(os.path.join(BASE, 'data', 'boats.json'), encoding='utf-8'))['boats']


def site_groups(boats=None):
    """홈페이지 도메인을 소속 배의 항구 다수결로 그룹에 배정한다."""
    boats = boats or load_boats()
    port_by_bid = {b['bid']: (b.get('port') or b.get('region') or '') for b in boats}
    groups = {name: [] for name, _ in PRIORITY_GROUPS}
    rest = []
    for host, site in sites_from_catalog(boats).items():
        votes = Counter()
        for bid in site.get('boat_ids', {}).values():
            port = port_by_bid.get(bid, '')
            for name, keyword in PRIORITY_GROUPS:
                if keyword in port:
                    votes[name] += 1
        if votes:
            groups[votes.most_common(1)[0][0]].append(host)
        else:
            rest.append(host)
    return [(name, groups[name]) for name, _ in PRIORITY_GROUPS] + [('나머지', rest)]


def write_plan():
    """화면의 '수집 방법' 안내에 쓰는 그룹별 배·사이트·페이지 수."""
    boats = load_boats()
    sites = sites_from_catalog(boats)
    try:
        health = json.load(open(os.path.join(BASE, 'data', 'site_health.json'), encoding='utf-8')).get('sites', {})
    except (OSError, ValueError):
        health = {}
    groups = []
    for name, hosts in site_groups(boats):
        groups.append({'name': name, 'sites': len(hosts),
                       'boats': sum(len(sites[h]['boats']) for h in hosts),
                       'pages_last': sum(health.get(h, {}).get('pages_checked', 0) for h in hosts)})
    sunsang = [b for b in boats if b.get('canonical_bid') is None and '.sunsang24.com' in (b.get('channels', {}).get('sunsang24') or '')]
    subs = {b['channels']['sunsang24'].split('//')[-1].split('.')[0] for b in sunsang}
    atomic_json(PLAN, {'near_days': NEAR_DAYS, 'fast_minutes': 5, 'slow_minutes': 30,
                       'sunsang24': {'boats': len(sunsang), 'fleets': len(subs)},
                       'groups': groups,
                       'total': {'boats': sum(g['boats'] for g in groups), 'sites': len(sites)},
                       'workers': 8, 'gap_seconds': 1, 'updated_at': now()})


def collect_group(mode, started, name, hosts, *range_args):
    if not hosts:
        return 0
    progress(mode, f'{name} 홈페이지 {len(hosts)}곳', started)
    print(f'== {name} 그룹: {len(hosts)}개 사이트 {" ".join(range_args)}', flush=True)
    if run('collect_homepages.py', *range_args, '--sites', *hosts):
        return 1
    progress(mode, f'{name} 업로드', started)
    return 1 if run('push_to_github.py') else 0


def fast(mode, started, groups):
    failed = 0
    progress(mode, '선상24 전체', started)
    if run('scrape_sunsang24.py', '--incremental') == 0:
        progress(mode, '선상24 업로드', started)
        failed |= 1 if run('push_to_github.py') else 0
    else:
        failed = 1
    for name, hosts in groups[:-1]:
        failed |= collect_group(mode, started, name, hosts, '--near-days', str(NEAR_DAYS))
    return failed


def slow(mode, started, groups):
    failed = 0
    priority_hosts = [h for _, hosts in groups[:-1] for h in hosts]
    failed |= collect_group(mode, started, '주요 3곳(31일 이후)', priority_hosts, '--after-days', str(NEAR_DAYS))
    name, hosts = groups[-1]
    failed |= collect_group(mode, started, name, hosts)
    return failed


def main():
    parser = argparse.ArgumentParser(description='낚시배 예약현황 수집')
    parser.add_argument('--mode', choices=('fast', 'slow', 'full'), default='full')
    args = parser.parse_args()
    with open(os.path.join(BASE, '.scrape.lock'), 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('이미 수집 중입니다. 중복 실행을 건너뜁니다.', flush=True)
            return 0
        started = now()
        try:
            write_plan()
        except Exception as e:
            print('수집 방법 안내 갱신 실패:', e, flush=True)
        groups = site_groups()
        failed = 0
        if args.mode in ('fast', 'full'):
            failed |= fast(args.mode, started, groups)
        if args.mode in ('slow', 'full'):
            failed |= slow(args.mode, started, groups)
        progress(args.mode, '완료' if not failed else '완료(일부 실패)', started)
    return failed


if __name__ == '__main__':
    sys.exit(main())
