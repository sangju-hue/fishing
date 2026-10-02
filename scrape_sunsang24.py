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


def parse_month(html, yyyymm):
    """{date: [(ship, fish, status, remaining)]} 반환. status: available|full|cancelled"""
    u = unescape_js(html)
    out = {}
    prefix = yyyymm[:4] + "-" + yyyymm[4:]
    for tr in u.split("<tr")[1:]:
        dm = re.search(r'data-sdate="(\d{4}-\d{2}-\d{2})"', tr)
        if not dm or not dm.group(1).startswith(prefix):
            continue
        sdate = dm.group(1)
        sm = re.search(r'<td class="ship_info">.*?<div class="title">\s*(.*?)\s*</div>', tr, re.S)
        ship = norm_ship(sm.group(1)) if sm else None
        fm = re.search(r'<div id="fish">(.*?)</div>', tr, re.S)
        fish = re.sub(r"<[^>]+>", "", fm.group(1)).strip() if fm else ""
        if not any(k in fish for k in JJUKKUMI):
            continue
        sno_m = re.search(r'data-schedule_no="(\d+)"', tr)
        sno = sno_m.group(1) if sno_m else None
        rm = re.search(r'<li class="remain"(.*?)</li>', tr, re.S)
        status, remaining = "unknown", None
        if rm:
            cell = rm.group(1)
            sm2 = re.search(r'data-status_code="(END|CANCEL|CHECK)"', cell)
            if sm2:
                if sm2.group(1) == "END": status = "full"
                elif sm2.group(1) == "CANCEL": status = "cancelled"
                elif sm2.group(1) == "CHECK": status = "maintenance"
            else:
                nm = re.search(r"남은자리.*?<span[^>]*>(\d+)명</span>", cell, re.S)
                if nm:
                    status, remaining = "available", int(nm.group(1))
                elif "예약마감" in cell:
                    status = "full"
                elif "점검" in cell:
                    status = "maintenance"
        out.setdefault(sdate, []).append((ship, fish, status, remaining, sno))
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
        if not any(k in fish for k in JJUKKUMI):
            continue
        status, remaining = "unknown", None
        sno_m = re.search(r'data-schedule_no="(\d+)"', tr)
        sno = sno_m.group(1) if sno_m else None
        sm2 = re.search(r'data-status_code="(END|CANCEL|CHECK)"', tr)
        if sm2:
            if sm2.group(1) == "END": status = "full"
            elif sm2.group(1) == "CANCEL": status = "cancelled"
            elif sm2.group(1) == "CHECK": status = "maintenance"
        else:
            nm = re.search(r"남은자리.*?<span[^>]*>(\d+)명</span>", tr, re.S)
            if nm:
                status, remaining = "available", int(nm.group(1))
            elif "예약마감" in tr:
                status = "full"
            elif "점검" in tr:
                status = "maintenance"
        out.setdefault(sdate, []).append((ship_name, fish, status, remaining, sno))
    return out



def aggregate(trips):
    """같은 날짜·같은 배의 여러 출조 집계."""
    avail = [t for t in trips if t[2] == "available"]
    is_octopus = any("문어" in (t[1] or "") for t in trips)
    fish_str = "문어" if is_octopus else None
    sno = next((t[4] for t in trips if len(t)>4 and t[4]), None)
    
    if avail:
        res = {"status": "available", "remaining": max(t[3] or 0 for t in avail)}
    elif any(t[2] == "full" for t in trips):
        res = {"status": "full", "remaining": 0}
    elif any(t[2] == "maintenance" for t in trips):
        res = {"status": "maintenance", "remaining": 0}
    elif trips and all(t[2] == "cancelled" for t in trips):
        res = {"status": "cancelled", "remaining": 0}
    else:
        return {"status": "unknown", "remaining": None}
        
    if fish_str:
        res["fish"] = fish_str
    if sno:
        res["sno"] = sno
    return res


def subdomain(url):
    m = re.match(r"https?://([a-z0-9-]+)\.sunsang24\.com", url or "")
    return m.group(1) if m and m.group(1)!='www' else None


def main():
    import argparse
    import concurrent.futures
    from collect_homepages import Client, atomic_json, months
    from homepage_engine import match_boat
    parser=argparse.ArgumentParser()
    parser.add_argument('--year',type=int)
    parser.add_argument('--incremental',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    now=datetime.now(KST)
    year=args.year or now.year+(now.month==12)
    start,first,end=season_window(now,year)
    checked_at=now.isoformat(timespec='seconds')
    boats=json.load(open(os.path.join(DATA,'boats.json'),encoding='utf-8'))['boats']
    groups={}
    for b in boats:
        if b.get('canonical_bid') is not None:continue
        sub=subdomain(b.get('channels',{}).get('sunsang24'))
        if sub:groups.setdefault(sub,[]).append(b)
    path=os.path.join(DATA,'status.json')
    try:data=json.load(open(path,encoding='utf-8'))
    except (OSError,ValueError):data={'dates':{}}
    data.setdefault('by_boat_id',{})
    def collect(item):
        sub,group=item;client=Client(1);results={};errors=[]
        names=[b['name'] for b in group]
        aliases={label:b['name'] for b in group for label in b.get('booking_names',[])}
        for month in months(first,end):
            ym=month.strftime('%Y%m');url=f'https://{sub}.sunsang24.com/ship/schedule_fleet/{ym}'
            try:
                h,_=client.fetch(url);parsed=parse_month(h,ym)
                if not any(parsed.values()):
                    simple_url = f'https://{sub}.sunsang24.com/ship/schedule_fleet_simple/{ym}'
                    h_simple, _ = client.fetch(simple_url)
                    ship_buttons = re.findall(r'<button class="btn btn-schedule-ship btn[^>]*data-ship-list-no="(\d+)".*?>\s*<strong>(.*?)</strong>', h_simple)
                    for ship_no, ship_name in ship_buttons:
                        if int(ship_no) == 0: continue
                        list_url = f'https://{sub}.sunsang24.com/ship/schedule_fleet/{ym}/{ship_no}/ship_one_list'
                        h_list, _ = client.fetch(list_url)
                        parsed_list = parse_simple_list(h_list, ym, norm_ship(ship_name))
                        for d, trips in parsed_list.items():
                            parsed.setdefault(d, []).extend(trips)
                named=any(t[0] for trips in parsed.values() for t in trips)
                for ds,trips in parsed.items():
                    if not first.isoformat()<=ds<=end.isoformat():continue
                    for b in group:
                        mine=[t for t in trips if match_boat(t[0],names,aliases)==b['name']] if named else trips if len(group)==1 else []
                        state=aggregate(mine)
                        if state['status']=='unknown':continue
                        cap=b.get('capacity')
                        if cap and (state['remaining'] or 0)>cap:continue
                        surl = f'https://{sub}.sunsang24.com/mypage/reservation_ready/{state.pop("sno")}' if state.get("sno") else url
                        results[(str(b['bid']),ds)]=dict(state,boat_id=b['bid'],source='sunsang24',source_url=surl,checked_at=checked_at)
            except Exception as e:errors.append(str(e))
        print(sub,len(results),'건',len(errors),'오류',flush=True)
        return group,results,errors
    total=0
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for group,results,errors in pool.map(collect,groups.items()):
            # Refresh only dates queried this run; preserve historical season data.
            for ds,day in data['by_boat_id'].items():
                if first.isoformat()<=ds<=end.isoformat():
                    for b in group:
                        key=str(b['bid'])
                        if key in day:
                            if errors:day[key]['stale']=True
                            else:del day[key]
            for ds,day in data.get('dates',{}).items():
                if first.isoformat()<=ds<=end.isoformat():
                    for b in group:
                        if day.get(b['name'],{}).get('source')=='sunsang24':
                            if errors:day[b['name']]['stale']=True
                            else:del day[b['name']]
            lookup={str(b['bid']):b for b in group}
            for (bid,ds),entry in results.items():
                data['by_boat_id'].setdefault(ds,{})[bid]=entry
                b=lookup[bid];day=data.setdefault('dates',{}).setdefault(ds,{})
                if day.get(b['name'],{}).get('boat_id') in (None,b['bid']):day[b['name']]=entry
            total+=len(results)
    for key in ('dates','by_boat_id'):
        data[key]={ds:day for ds,day in sorted(data[key].items()) if start.isoformat()<=ds<=end.isoformat() and day}
    data['updated_at']=checked_at
    data['queried_range']={'from':first.isoformat(),'to':end.isoformat()}
    atomic_json(path,data)
    print('선상24 수집 완료:',total,'건',flush=True)

if __name__=='__main__':main()
