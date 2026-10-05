#!/usr/bin/env python3
"""선상24 예약달력 스크래퍼.

{서브도메인}.sunsang24.com/ship/schedule_fleet/YYYYMM 페이지를 가져와
날짜별 쭈꾸미 출조의 남은 자리를 파싱, data/status.json에 저장한다.
"""
import json
import os
import re
import time
import urllib.request
from html import unescape
from datetime import date, datetime, timedelta, timezone
from season import season_window

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(BASE) if os.path.basename(BASE)=="scrapers" else BASE,"data")
KST = timezone(timedelta(hours=9))
REQ_GAP = 2.0              # 배 사이 요청 간격(초) — 과도한 크롤링 방지
TIMEOUT = 25

JJUKKUMI = ("쭈꾸미", "주꾸미", "쭈갑", "갑오징어", "문어")  # 갑오징어 병행 출조도 포함


def fetch(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", errors="replace")


def unescape_js(h):
    return h.replace("\\n", "\n").replace('\\"', '"').replace("\\/", "/")


def norm_ship(s):
    return re.sub(r"\s+", "", s or "")


OTHER_FISH = ('광어','우럭','참돔','농어','백조기','갈치','부시리','방어','열기','대구','민어')

def parse_month(html, yyyymm, include_other_fish=False):
    """{date: [(ship, fish, status, remaining)]} 반환. status: available|full|cancelled"""
    from homepage_engine import DOM
    out = {}
    prefix = yyyymm[:4] + "-" + yyyymm[4:]
    root = DOM(unescape_js(html)).root
    seen=set()
    for dated in root.walk():
        sdate=dated.attrs.get('data-sdate','')
        if not sdate.startswith(prefix):continue
        row=dated
        while row.parent and row.tag!='tr':row=row.parent
        if row.tag!='tr':continue
        nodes=list(row.walk())
        ship_cell=next((n for n in nodes if 'ship_info' in n.attrs.get('class','').split()),None)
        title=next((n for n in ship_cell.walk() if 'title' in n.attrs.get('class','').split()),None) if ship_cell else None
        ship=title.text().strip() if title else None
        fish_node=next((n for n in nodes if n.attrs.get('id')=='fish'),None)
        fish=fish_node.text().strip() if fish_node else ''
        sno=dated.attrs.get('data-schedule_no') or next((n.attrs['data-schedule_no'] for n in nodes if n.attrs.get('data-schedule_no')),None)
        remain=next((n for n in nodes if 'remain' in n.attrs.get('class','').split()),None)
        state,remaining='unknown',None
        if remain:
            remain_text=re.sub(r'\s+','',remain.text())
            code=next((n.attrs['data-status_code'] for n in remain.walk() if n.attrs.get('data-status_code')),None)
            state='weather' if re.search(r'기상\s*악화',remain_text) else {'END':'full','CANCEL':'cancelled','CHECK':'maintenance'}.get(code,'unknown')
            if state=='unknown':
                text=remain_text
                number=re.search(r'남은자리(\d+)명',text)
                if number:state,remaining='available',int(number.group(1))
                elif '예약마감' in text:state='full'
                elif '점검' in text:state='maintenance'
        key=(sdate,ship,sno,fish,state,remaining)
        if key in seen:continue
        seen.add(key)
        if not include_other_fish and not any(k in fish for k in JJUKKUMI) and state not in ('cancelled','maintenance','weather'):continue
        out.setdefault(sdate,[]).append((ship,fish,state,remaining,sno))
    return out


def parse_simple_list(html, yyyymm, ship_name):
    """선단(fleet)에서 개별 배 일정(ship_one_list)의 HTML 파싱."""
    u = unescape_js(html)
    out = {}
    prefix = yyyymm[:4] + "-" + yyyymm[4:]
    for tr in u.split("<tr")[1:]:
        dm = re.search(r'data-sdate="(\d{4}-\d{2}-\d{2})"', tr)
        if not dm or not dm.group(1).startswith(prefix):
            continue
        sdate = dm.group(1)
        fm = re.search(r'<div id="fish">(.*?)</div>', tr, re.S)
        fish = re.sub(r"<[^>]+>", "", fm.group(1)).strip() if fm else ""
        status, remaining = "unknown", None
        sno_m = re.search(r'data-schedule_no="(\d+)"', tr)
        sno = sno_m.group(1) if sno_m else None
        sm2 = re.search(r'data-status_code="(END|CANCEL|CHECK)"', tr)
        if sm2:
            if sm2.group(1) == "END": status = "full"
            elif sm2.group(1) == "CANCEL": status = "cancelled"
            elif sm2.group(1) == "CHECK": status = "maintenance"
            if re.search(r'기상\s*악화', tr):status = 'weather'
        else:
            nm = re.search(r"남은자리.*?<span[^>]*>(\d+)명</span>", tr, re.S)
            if nm:
                status, remaining = "available", int(nm.group(1))
            elif "예약마감" in tr:
                status = "full"
            elif "점검" in tr:
                status = "maintenance"
        if status not in ("available","full","cancelled","maintenance","weather"):
            continue
        out.setdefault(sdate, []).append((ship_name, fish, status, remaining, sno))
    return out



def aggregate(trips):
    """같은 날짜·같은 배의 여러 출조 집계."""
    avail = [t for t in trips if t[2] == "available"]
    chosen = max(avail,key=lambda t:t[3] or 0) if avail else next((t for t in trips if t[2]=='full'),next((t for t in trips if t[2]=='maintenance'),trips[0] if trips else None))
    is_octopus = chosen and "문어" in (chosen[1] or "")
    fish_str = "문어" if is_octopus else None
    sno = chosen[4] if chosen and len(chosen)>4 else None
    
    if avail:
        res = {"status": "available", "remaining": max(t[3] or 0 for t in avail)}
    elif any(t[2] == "full" for t in trips):
        res = {"status": "full", "remaining": 0}
    elif any(t[2] == "maintenance" for t in trips):
        res = {"status": "maintenance", "remaining": 0}
    elif any(t[2] == "weather" for t in trips):
        res = {"status": "weather", "remaining": 0}
    elif trips and all(t[2] == "cancelled" for t in trips):
        res = {"status": "cancelled", "remaining": 0}
    else:
        return {"status": "unknown", "remaining": None}
        
    if fish_str:
        res["fish"] = fish_str
    if sno:
        res["sno"] = sno
    return res


def booking_state(trips):
    target=[t for t in trips if any(k in t[1] for k in JJUKKUMI)]
    other=[t for t in trips if any(k in t[1] for k in OTHER_FISH)]
    selected=target or other or trips
    state=aggregate(selected)
    if not target and state['status'] in ('available','full'):
        state['actual_status']=state['status']
        state['status']='other_fish' if other else 'unspecified'
        state['fish']=' / '.join(dict.fromkeys(t[1] or '어종 미표기' for t in selected))
    return state


def subdomain(url):
    m = re.match(r"https?://([a-z0-9-]+)\.sunsang24\.com", url or "")
    return m.group(1) if m and m.group(1)!='www' else None


def main():
    import argparse
    import concurrent.futures
    from collect_homepages import Client, atomic_json, months, resolve_boat_ids
    from homepage_engine import match_boat
    from booking_state import load_targets,compact_status,collection_outcome,SKIPPED
    parser=argparse.ArgumentParser()
    parser.add_argument('--from-date');parser.add_argument('--to-date');parser.add_argument('--near-days',type=int);parser.add_argument('--after-days',type=int)
    parser.add_argument('--targets-file',help=argparse.SUPPRESS)
    parser.add_argument('--year',type=int)
    parser.add_argument('--incremental',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--subdomains',nargs='*',help='점검할 선상24 선단 (생략하면 전체)')
    parser.add_argument("--boat-ids",nargs="+",type=int,help="지정 선박만 수집")
    parser.add_argument("--ports",nargs="+",help="수집할 항구 (생략하면 전체)")
    args=parser.parse_args()
    now=datetime.now(KST)
    year=args.year or now.year+(now.month==12)
    start,first,end=season_window(now,year)
    if args.near_days:end=min(end,first+timedelta(days=args.near_days-1))
    if args.after_days:first+=timedelta(days=args.after_days)
    if args.from_date or args.to_date:
        try:first=max(first,date.fromisoformat(args.from_date));end=min(end,date.fromisoformat(args.to_date))
        except (ValueError,TypeError):parser.error('시작/종료 날짜를 함께 지정하세요')
    if first>end:return SKIPPED
    checked_at=now.isoformat(timespec='seconds')
    with open(os.path.join(DATA,'boats.json'), encoding='utf-8') as f:
        boats = json.load(f)['boats']
    targets=load_targets(args.targets_file,boats)
    if targets is not None and not targets:return SKIPPED
    if targets is not None:args.boat_ids=[int(bid) for bid in targets]
    if args.boat_ids:
        args.boat_ids=resolve_boat_ids(boats,args.boat_ids)
        if not args.boat_ids:
            print("수집 대상 선박이 모두 삭제되어 건너뜁니다.")
            return SKIPPED
    groups={}
    for b in boats:
        if args.ports and b.get("port") not in args.ports:continue
        if args.boat_ids and b["bid"] not in args.boat_ids:continue
        if b.get('canonical_bid') is not None:continue
        sub=subdomain(b.get('channels',{}).get('sunsang24'))
        if sub:groups.setdefault(sub,[]).append(b)
    if args.subdomains:groups={sub:g for sub,g in groups.items() if sub in args.subdomains}
    path=os.path.join(DATA,'status.json')
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError,ValueError):data={'dates':{}}
    data.setdefault('by_boat_id',{});data.setdefault('dates',{})
    def collect(item):
        sub,group=item;client=Client(1);results={};errors=[];observed=set();capacity_rejected=set();all_labels=set();selectable_labels=set();excluded_fish={}
        names=[b['name'] for b in group]
        aliases={label:b['name'] for b in group for label in b.get('booking_names',[])}
        for month in months(first,end):
            if targets is not None and not any(ds[:7]==month.strftime('%Y-%m') for b in group for ds in targets.get(str(b['bid']),[])):continue
            ym=month.strftime('%Y%m');url=f'https://{sub}.sunsang24.com/ship/schedule_fleet/{ym}'
            try:
                h,_=client.fetch(url);all_trips=parse_month(h,ym,include_other_fish=True)
                for ds,trips in all_trips.items():
                    if not first.isoformat()<=ds<=end.isoformat():continue
                    for trip in trips:
                        if trip[0]:all_labels.add(trip[0])
                        name=match_boat(trip[0],names,aliases)
                        if name and not any(k in trip[1] for k in JJUKKUMI):excluded_fish.setdefault(name,set()).add(trip[1] or '어종 미표기')
                parsed={ds:[t for t in trips if t[2] in ("available","full","cancelled","maintenance","weather")] for ds,trips in all_trips.items()}
                matched={match_boat(t[0],names,aliases) for trips in parsed.values() for t in trips}
                if any(name not in matched for name in names):
                    simple_url = f'https://{sub}.sunsang24.com/ship/schedule_fleet_simple/{ym}'
                    h_simple, _ = client.fetch(simple_url)
                    from homepage_engine import DOM
                    ship_buttons=[(button.attrs.get('data-ship-list-no',''),next(button.walk('strong'),button).text().strip()) for button in DOM(h_simple).root.walk('button') if 'btn-schedule-ship' in button.attrs.get('class','').split()]
                    selectable_labels.update(name for ship_no,name in ship_buttons if ship_no.isdigit() and int(ship_no)>0)
                    for ship_no, ship_name in ship_buttons:
                        if not ship_no.isdigit() or int(ship_no) == 0: continue
                        name=match_boat(ship_name,names,aliases)
                        if not name or name in matched:continue
                        list_url = f'https://{sub}.sunsang24.com/ship/schedule_fleet/{ym}/{ship_no}/ship_one_list'
                        h_list, _ = client.fetch(list_url)
                        parsed_list = parse_simple_list(h_list, ym, ship_name)
                        for d, trips in parsed_list.items():
                            parsed.setdefault(d, []).extend(trips)
                name_to_ship_no = {}
                from homepage_engine import DOM
                for button in DOM(h).root.walk('button'):
                    if 'btn-schedule-ship' in button.attrs.get('class','').split():
                        sno = button.attrs.get('data-ship-list-no','')
                        sname = next(button.walk('strong'), button).text().strip()
                        if sno.isdigit() and int(sno) > 0:
                            mname = match_boat(sname, names, aliases)
                            if mname:
                                name_to_ship_no[mname] = sno
                
                observed.update(t[0] for trips in parsed.values() for t in trips if t[0])
                named=any(t[0] for trips in parsed.values() for t in trips)
                for ds,trips in parsed.items():
                    if not first.isoformat()<=ds<=end.isoformat():continue
                    for b in group:
                        if targets is not None and ds not in targets.get(str(b['bid']),[]):continue
                        mine=[t for t in trips if match_boat(t[0],names,aliases)==b['name']] if named else trips if len(group)==1 else []
                        state=booking_state(mine)
                        if state['status']=='unknown':continue
                        cap=b.get('capacity')
                        if cap and (state['remaining'] or 0)>cap:
                            capacity_rejected.add(b['name']);continue
                        _ = state.pop('sno', None)
                        ship_no = name_to_ship_no.get(b['name'])
                        qs = f"?sch_ship_no={ship_no}" if ship_no else ""
                        surl = f'https://{sub}.sunsang24.com/ship/schedule_fleet/{ym}{qs}#d{ds}'
                        results[(str(b['bid']),ds)]=dict(state,boat_id=b['bid'],source='sunsang24',source_url=surl,checked_at=checked_at)
            except Exception as e:errors.append(str(e))
        print(sub,len(results),'건',len(errors),'오류',flush=True)
        collected={bid for bid,ds in results}
        missing=[b['name'] for b in group if str(b['bid']) not in collected]
        health={'status':'partial' if results and (errors or missing) else 'ok' if results else 'fetch_failed' if errors else 'no_data','url':f'https://{sub}.sunsang24.com/ship/schedule_fleet','entries':len(results),'boats':names,'boat_ids':{b['name']:b['bid'] for b in group},'missing_boats':missing,'observed_ship_labels':sorted(observed),'all_ship_labels':sorted(all_labels),'selectable_ship_labels':sorted(selectable_labels),'excluded_fish':{name:sorted(fish) for name,fish in excluded_fish.items()},'capacity_rejected':sorted(capacity_rejected),'errors':errors}
        finished=datetime.now(KST).isoformat(timespec='seconds')
        for entry in results.values():entry['checked_at']=finished
        return group,results,errors,sub,health
    total=0
    health_path=os.path.join(DATA,'site_health_sunsang24.json')
    try:
        with open(health_path, encoding='utf-8') as f:
            health = json.load(f)
    except (OSError,ValueError):health={}
    health.update(checked_at=checked_at,queried_range={'from':first.isoformat(),'to':end.isoformat()})
    health.setdefault('sites',{})
    outcomes=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for group,results,errors,sub,h in pool.map(collect,groups.items()):
            h['checked_at']=datetime.now(KST).isoformat(timespec='seconds')
            h['queried_range']={'from':first.isoformat(),'to':end.isoformat()}
            health['sites'][sub]=h;outcomes.append(h)
            # Refresh only dates queried this run; preserve historical season data.
            for ds,day in data['by_boat_id'].items():
                if first.isoformat()<=ds<=end.isoformat():
                    for b in group:
                        if targets is not None and ds not in targets.get(str(b['bid']),[]):continue
                        key=str(b['bid'])
                        if key in day:
                            if errors:day[key]['stale']=True
                            else:del day[key]
            for ds,day in data.get('dates',{}).items():
                if first.isoformat()<=ds<=end.isoformat():
                    for b in group:
                        if targets is not None and ds not in targets.get(str(b['bid']),[]):continue
                        if day.get(b['name'],{}).get('source')=='sunsang24':
                            if errors:day[b['name']]['stale']=True
                            else:del day[b['name']]
            lookup={str(b['bid']):b for b in group}
            for (bid,ds),entry in results.items():
                data['by_boat_id'].setdefault(ds,{})[bid]=entry
                b=lookup[bid];day=data.setdefault('dates',{}).setdefault(ds,{})
                if day.get(b['name'],{}).get('boat_id') in (None,b['bid']):day[b['name']]=entry
            total+=len(results)
    season_start,_,season_end=season_window(now,year)
    for key in ('dates','by_boat_id'):
        data[key]={ds:day for ds,day in sorted(data[key].items()) if season_start.isoformat()<=ds<=season_end.isoformat() and day}
    data['updated_at']=datetime.now(KST).isoformat(timespec='seconds')
    data['queried_range']={'from':first.isoformat(),'to':end.isoformat()}
    with open(os.path.join(DATA,'boats.json'),encoding='utf-8') as f:current=json.load(f)['boats']
    compact_status(data,current)
    atomic_json(path,data)
    atomic_json(health_path,health)
    print('선상24 수집 완료:',total,'건',flush=True)
    return collection_outcome(outcomes)

if __name__=='__main__':
    import sys
    sys.exit(main())
