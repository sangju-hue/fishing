"""Prevent concurrent collection, including manually invoked fishing.sh.

수집 모드 (작업 결과를 모아 한 번 게시):
  fast (5분)  : 선상24 가까운 날짜 → 무창포·오천항·영흥도 홈페이지 한 번에 (오늘~14일)
  slow (30분) : 선상24 먼 날짜 + 무창포·오천항·영흥도 홈페이지 15일째 이후 → 나머지 항구 홈페이지 전체
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
from collect_homepages import BASE, atomic_json, sites_from_catalog, resolve_boat_ids
from booking_state import OK,FAILED,PARTIAL,SKIPPED,combine_outcomes

PRIORITY_GROUPS = (('무창포', '무창포'), ('오천항', '오천'), ('영흥도', '영흥'))
SETTINGS = os.path.join(BASE, 'scrape_settings.json')


def load_settings():
    """화면에서 저장한 날짜·분 설정 (없거나 잘못되면 기본값 14일/5분/30분)."""
    d = {'near_days': 14, 'fast_minutes': 5, 'slow_minutes': 30}
    try:
        with open(SETTINGS,encoding='utf-8') as f:saved=json.load(f)
    except (OSError, ValueError):
        return d
    for k, (lo, hi) in {'near_days': (1, 60), 'fast_minutes': (1, 120), 'slow_minutes': (1, 120)}.items():
        v = saved.get(k)
        if isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi:
            d[k] = v
    return d


NEAR_DAYS = load_settings()['near_days']
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
    with open(os.path.join(BASE,'data','boats.json'),encoding='utf-8') as f:return json.load(f)['boats']


def site_groups(boats=None):
    """홈페이지 도메인을 소속 배의 항구 다수결로 그룹에 배정한다."""
    boats = load_boats() if boats is None else boats
    port_by_bid = {b['bid']: (b.get('port') or b.get('region') or '') for b in boats}
    groups = {name: [] for name, _ in PRIORITY_GROUPS}
    rest = []
    for host, site in sites_from_catalog(boats).items():
        if '.sunsang24.com' in host and all('.sunsang24.com' in (b.get('channels',{}).get('sunsang24') or '') for b in boats if b['bid'] in site['boat_ids'].values()):continue
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
        with open(os.path.join(BASE, 'data', 'site_health.json'), encoding='utf-8') as f:
            health = json.load(f).get('sites', {})
    except (OSError, ValueError):
        health = {}
    groups = []
    for name, hosts in site_groups(boats):
        groups.append({'name': name, 'sites': len(hosts),
                       'boats': sum(len(sites[h]['boats']) for h in hosts),
                       'pages_last': sum(health.get(h, {}).get('pages_checked', 0) for h in hosts)})
    sunsang = [b for b in boats if b.get('canonical_bid') is None and '.sunsang24.com' in (b.get('channels', {}).get('sunsang24') or '')]
    subs = {b['channels']['sunsang24'].split('//')[-1].split('.')[0] for b in sunsang}
    atomic_json(PLAN, {'near_days': NEAR_DAYS, 'fast_minutes': load_settings()['fast_minutes'], 'slow_minutes': load_settings()['slow_minutes'],
                       'sunsang24': {'boats': len(sunsang), 'fleets': len(subs)},
                       'groups': groups,
                       'total': {'boats': sum(g['boats'] for g in groups), 'sites': len(sites)},
                       'workers': 8, 'gap_seconds': 1, 'updated_at': now()})


def collect_group(mode, started, name, hosts, *range_args):
    if not hosts:return SKIPPED
    progress(mode,f'{name} 홈페이지 {len(hosts)}곳',started)
    return run('collect_homepages.py',*range_args,'--sites',*hosts)

def fast(mode, started, groups):
    progress(mode,'선상24 가까운 날짜',started)
    sun=run('scrape_sunsang24.py','--incremental','--near-days',str(NEAR_DAYS))
    hosts=[h for _,items in groups[:-1] for h in items]
    hp=collect_group(mode,started,'주요 3곳',hosts,'--near-days',str(NEAR_DAYS))
    return combine_outcomes([sun,hp])

def slow(mode, started, groups):
    progress(mode,'선상24 먼 날짜',started)
    sun=run('scrape_sunsang24.py','--incremental','--after-days',str(NEAR_DAYS))
    hosts=[h for _,items in groups[:-1] for h in items]
    hp=collect_group(mode,started,f'주요 3곳({NEAR_DAYS+1}일째 이후)',hosts,'--after-days',str(NEAR_DAYS))
    name,hosts=groups[-1]
    rest=collect_group(mode,started,name,hosts)
    return combine_outcomes([sun,hp,rest])

def by_range(mode, started, groups, start, end, ports=None, boat_ids=None,targets_file=None):
    if boat_ids:
        boat_ids=resolve_boat_ids(load_boats(),boat_ids)
        if not boat_ids:return SKIPPED
    port_args=['--ports',*ports] if ports else []
    if boat_ids:port_args+=['--boat-ids',*map(str,boat_ids)]
    if targets_file:port_args+=['--targets-file',targets_file]
    if ports or boat_ids:groups=site_groups([b for b in load_boats() if (not ports or b.get('port') in ports) and (not boat_ids or b['bid'] in boat_ids)])
    span=['--from-date',start,'--to-date',end]
    progress(mode,'선상24 선택 날짜·선박',started)
    sun=run('scrape_sunsang24.py','--incremental',*span,*port_args)
    hosts=[h for _,items in groups for h in items]
    hp=collect_group(mode,started,f'홈페이지 범위({start}~{end})',hosts,*span,*port_args) if hosts else SKIPPED
    return combine_outcomes([sun,hp])

def main():
    parser = argparse.ArgumentParser(description='낚시배 예약현황 수집')
    parser.add_argument('--mode', choices=('fast', 'slow', 'full', 'range'), default='full')
    parser.add_argument('--targets-file',help=argparse.SUPPRESS)
    parser.add_argument('--from-date')
    parser.add_argument('--to-date')
    parser.add_argument('--boat-ids', nargs='+', type=int, help='지정 선박만 수집')
    parser.add_argument('--ports', nargs='+', help='수집할 항구 (생략하면 전체)')
    args = parser.parse_args()
    marker=os.path.join(BASE,'.apply_pending')
    if os.path.exists(marker) and time.time()-os.path.getmtime(marker)<1800:
        print('수집기 업데이트 적용 대기 · 다음 실행으로 보류',flush=True)
        return SKIPPED
    with open(os.path.join(BASE, '.scrape.lock'), 'a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('이미 수집 중입니다. 중복 실행을 건너뜁니다.', flush=True)
            return SKIPPED
        started = now()
        try:
            write_plan()
        except Exception as e:
            print('수집 방법 안내 갱신 실패:', e, flush=True)
        groups = site_groups()
        results=[]
        if args.mode=='range':results.append(by_range(args.mode,started,groups,args.from_date,args.to_date,args.ports,args.boat_ids,args.targets_file))
        if args.mode in ('fast','full'):results.append(fast(args.mode,started,groups))
        if args.mode in ('slow','full'):results.append(slow(args.mode,started,groups))
        outcome=combine_outcomes(results)
        if outcome!=SKIPPED:
            progress(args.mode,'GitHub 반영',started)
            if run('push_to_github.py')!=OK:outcome=FAILED
        labels={OK:'완료',PARTIAL:'완료(일부 실패)',FAILED:'실패',SKIPPED:'보류(수집 대상 없음)'}
        progress(args.mode,labels[outcome],started)
    return outcome


if __name__ == '__main__':
    sys.exit(main())
