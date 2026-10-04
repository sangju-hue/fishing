"""Collect every homepage in boats.json; never count HTTP success as parsed data."""
import concurrent.futures
from collections import OrderedDict
import http.cookiejar
import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit, urlencode, urljoin, parse_qsl,quote
from homepage_engine import conditional_notice, booking_notices, match_boat, DOM, parse_booking, parse_hanaho, parse_wz, parse_sunsang, parse_niabbs, booking_links, date_url
from season import season_window
from custom_booking import parse_bando, parse_fishapp
from booking_state import compact_status, collection_outcome, load_targets, SKIPPED

BASE=os.path.dirname(os.path.abspath(__file__))
DATA=os.path.join(os.path.dirname(BASE) if os.path.basename(BASE)=='scrapers' else BASE,'data')
KST=timezone(timedelta(hours=9))
HEADERS={'User-Agent':'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36','Accept-Language':'ko-KR,ko;q=0.9'}

class Redirects(urllib.request.HTTPRedirectHandler):
    http_error_308=urllib.request.HTTPRedirectHandler.http_error_302

class Client:
    def __init__(self, gap=1):
        self.opener=urllib.request.build_opener(Redirects(),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.gap,self.last=gap,0
        self.cache=OrderedDict();self.cache_bytes=0;self.requests=0;self.cache_hits=0;self.started=time.monotonic()
    def fetch(self,url,data=None):
        p=urlsplit(url)
        host=(p.hostname or '').encode('idna').decode('ascii')
        netloc=host+(f':{p.port}' if p.port else '')
        url=urlunsplit((p.scheme,netloc,quote(p.path,safe='/%:@'),quote(p.query,safe='=&?/:@+%,;'),''))
        cache_key=(url,tuple(sorted((data or {}).items())))
        if cache_key in self.cache:
            self.cache_hits+=1;self.cache.move_to_end(cache_key);return self.cache[cache_key]
        time.sleep(max(0,self.gap-(time.monotonic()-self.last)))
        self.last=time.monotonic()
        req=urllib.request.Request(url,data=urlencode(data).encode() if data is not None else None,headers=HEADERS)
        remaining=180-(time.monotonic()-self.started)
        if remaining<=0:raise TimeoutError('site request time budget exceeded; retained data marked stale')
        self.requests+=1
        with self.opener.open(req,timeout=min(12,remaining)) as r:
            final=r.url
            if 'kuipernet' in final:raise RuntimeError('WAF 차단')
            raw=r.read()
        for enc in ('utf-8','euc-kr','cp949'):
            try:
                result=(raw.decode(enc),final);size=2*(len(result[0])+len(final))
                if size<=8*1024*1024:
                    while self.cache and self.cache_bytes+size>8*1024*1024:
                        _,value=self.cache.popitem(last=False);self.cache_bytes-=2*(len(value[0])+len(value[1]))
                    self.cache[cache_key]=result;self.cache_bytes+=size
                return result
            except UnicodeDecodeError:pass
        return raw.decode('utf-8',errors='replace'),final

def months(today,end):
    d=today.replace(day=1)
    while d<=end:
        yield d
        d=(d.replace(day=28)+timedelta(days=4)).replace(day=1)

def resolve_boat_ids(boats, ids):
    """기존 알림의 통합 전 ID를 현재 대표 선박 ID로 연결한다."""
    catalog = {b['bid']: b for b in boats}
    resolved = set()
    for bid in ids:
        # 삭제 전 신청 내역이 남아 있어도 다른 배의 수집은 계속한다.
        if bid not in catalog:
            continue
        seen = set()
        while bid in catalog and catalog[bid].get('canonical_bid') is not None:
            if bid in seen:
                raise ValueError('선박 통합 ID 순환 참조')
            seen.add(bid)
            bid = catalog[bid]['canonical_bid']
        if bid not in catalog:
            raise ValueError(f'등록되지 않은 선박 ID: {bid}')
        resolved.add(bid)
    return sorted(resolved)

def sites_from_catalog(boats):
    out={}
    for b in boats:
        if b.get('canonical_bid') is not None:continue
        u=b.get('channels',{}).get('homepage')
        if not u:continue
        host=urlsplit(u).hostname.encode('idna').decode('ascii').removeprefix('www.')
        g=out.setdefault(host,{'url':u,'boats':[],'boat_ids':{},'aliases':{},'deferred_boats':[],'alternates':[]})
        if b.get('collection_priority')=='deferred_daily':g['deferred_boats'].append(b['name'])
        if b['name'] not in g['boats']:g['boats'].append(b['name'])
        g['boat_ids'][b['name']]=b.get('bid')
        for label in b.get('booking_names',[]):g['aliases'][label]=b['name']
        for alternate in b.get('booking_fallbacks',[]):g['alternates'].append((alternate,[b['name']]))
        # Same host can expose each trip through a different ship selector.
        if u != g['url'] and dict(parse_qsl(urlsplit(u).query)).get('PA_N_UID'):
            g['alternates'].append((u,[b['name']]))
    return out

# Verified formatting differences, not speculative boat renames.
ALIASES={
    'haesin.net':{'킹스타':'킹스타호'},
    'ssfish.kr':{'뉴천일호':'무창포뉴천일호'},
    'napoliho.net':{'엠피닉스':'나폴리9'},
    'hanprofishing.com':{'대천한프로호':'한프로호'},
    'boryeongharbor.com':{'뉴마린스타':'뉴마린스타호','마린스타1':'마린스타1호'},
    'naksi25.net':{'25시수평선':'25시수평선호'},
}
# A dead old domain can still have a verified current booking provider.
ALTERNATES={
    'aceho.net': [('https://oc-ace.sunsang24.com/ship/schedule_fleet',['에이스호'])],
    'sungnamho.com': [('https://sungnam.thefishing.kr/index.php?mid=bk',['성남호'])],
    'vinaho.com': [('https://vinaho.sunsang24.com/ship/schedule_fleet',['비엔나호'])],
    'dongmunfish.com': [('https://dongmun.sunsang24.com/ship/schedule_fleet',['갈매기호','아침바다호','천해호'])],
    'sangsangho.com': [('https://dongmun.sunsang24.com/ship/schedule_fleet',['라벤더호'])],
}

def collect_site(host,g,today,end,gap=1,client=None):
    if today>end:
        return {},{},{'status':'out_of_season','entries':0,'boats':g['boats'],'missing_boats':[],'pages_checked':0,'errors':[]}
    deferred=list(g.get('deferred_boats',[]))
    g=dict(g,boats=[b for b in g['boats'] if b not in deferred])
    if not g['boats']:
        return {},{},{'status':'deferred_daily','url':g['url'],'entries':0,'boats':deferred,'boat_ids':g['boat_ids'],'missing_boats':deferred,'deferred_boats':deferred,'pages_checked':0,'errors':[]}
    client=client or Client(gap);out={};sources={};errors=[];visited=[];seen_boats=set();seen_dates=set();labels=set();notices={};date_notices={}
    aliases=dict(ALIASES.get(host,{}));aliases.update(g.get('aliases',{}))
    def get(url):
        html,final=client.fetch(url);visited.append(final);return html,final
    def absorb(html,url,boats=None):
        notices.update(booking_notices(html,boats or g['boats'],aliases))
        v,b,ds=parse_booking(html,boats or g['boats'],aliases,today,end)
        out.update(v);sources.update({k:url for k in v});seen_boats.update(b);seen_dates.update(ds)
        for row in DOM(html).root.walk('tr'):
            cells=[n for n in row.children if hasattr(n,'tag') and n.tag=='td']
            if len(cells)==3 and any(re.match(r'admin-right-\d{8}-',n.attrs.get('id','')) for n in cells[2].walk('div')):
                label=next(cells[0].walk('span'),cells[0]).text().strip()
                labels.add(label)
                boat=match_boat(label,boats or g['boats'],aliases)
                for marker in cells[2].walk('div'):
                    m=re.match(r'admin-right-(\d{4})(\d{2})(\d{2})-(\d+)-',marker.attrs.get('id',''))
                    if m and boat:
                        y,mo,d,uid=m.groups();key=(boat,f'{y}-{mo}-{d}')
                        if key in v:
                            notice=conditional_notice(cells[1].text()+'\n'+cells[2].text())
                            if notice:date_notices.setdefault(boat,{})[key[1]]=notice
                            parts=urlsplit(url);query=dict(parse_qsl(parts.query));query.update(mid='bk',year=y,month=mo,day=d,mode='list',sel='day',PA_N_UID=uid)
                            sources[key]=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode(query),'list'))
        return ds
    root=None
    p=urlsplit(g['url']);hosts=[p.netloc,p.netloc.removeprefix('www.')]
    variants=[g['url']]+[urlunsplit((scheme,netloc,p.path,p.query,'')) for netloc in dict.fromkeys(hosts) for scheme in ('https','http')]
    for url in dict.fromkeys(variants):
        try:root=get(url);break
        except Exception as e:errors.append({'url':url,'error':f'{type(e).__name__}: {e}'})
    if root:
        html,url=root
        custom = host == 'bandofish.com' or (host == 'fishapp.co.kr' and '/wp/' in url)
        if custom:
            for month in months(today,end):
                try:
                    if host == 'bandofish.com':
                        target=urljoin(url,'board.php')+'?'+urlencode({'bo_table':'schedule','sch_year':month.year,'sch_month':month.month})
                        h,final=get(target)
                        v,src=parse_bando(h,g['boats'],aliases,today,end,month,final)
                    else:
                        schedule=url.split('/schedule')[0].rstrip('/')+'/schedule'
                        fields={n.attrs.get('id'):n.attrs.get('value') for n in DOM(html).root.walk('input')}
                        endpoint=fields.get('scheduleListURL') or urlsplit(schedule).path+'/list.json'
                        raw,final=client.fetch(urljoin(schedule,endpoint),{'SCHD_MONTH':month.strftime('%Y%m')});visited.append(final)
                        v,src=parse_fishapp(json.loads(raw),g['boats'],aliases,today,end,schedule,fields.get('waitReserveYn')=='Y')
                    out.update(v);sources.update(src);seen_boats.update(k[0] for k in v)
                except Exception as e:errors.append({'url':g['url'],'error':str(e)})
        root_dates=absorb(html,url) if not custom else {k[1] for k in out}
        queue=booking_links(html,url)
        # Follow frames and then inspect their menus, not just the wrapper page.
        frames=[urljoin(url,n.attrs['src']) for tag in ('iframe','frame') for n in DOM(html).root.walk(tag) if n.attrs.get('src')]
        for next_url in dict.fromkeys(frames):
            try:
                h,u=get(next_url);queue.extend(booking_links(h,u));absorb(h,u)
            except Exception as e:errors.append({'url':next_url,'error':str(e)})
        # Prefer actual booking/status pages over links in notices.
        queue=sorted(dict.fromkeys(queue),key=lambda u:0 if re.search(r'mid=bk|hid=status|/reservation|/ship/booking',u) else 1)
        niabbs=bool(re.search(r'/niabbs5m?/',url) and re.search(r'doc/sub[\w-]+_in(?:2)?\.htm',html))
        booking=(html,url) if root_dates or host=='sooyangho.co.kr' or niabbs else None
        for next_url in (queue[:6] if not booking and not custom else []):
            try:
                h,u=get(next_url);ds=absorb(h,u)
                if ds:booking=(h,u);break
                if host=='hanaho.net' and '/ship/booking.php' in u:booking=(h,u);break
                if host=='boryeongharbor.com' and 'hid=status' in u:booking=(h,u);break
            except Exception as e:errors.append({'url':next_url,'error':str(e)})
        if booking and not custom:
            h,u=booking
            if host=='sooyangho.co.kr' or niabbs:
                monthly='doc/sub2_in2.htm' if host=='sooyangho.co.kr' else next(iter(re.findall(r'(doc/sub[\w-]+_in2\.htm)',h)),'doc/sub2_in2.htm')
                # The mobile table labels capacity and remaining explicitly;
                # the desktop table also contains a passenger list, not used.
                base=re.sub(r'/niabbs5/', '/niabbs5m/',u)
                for month in months(today,end):
                    target=urljoin(base,monthly)+'?'+urlencode({'toYear':month.year,'toMonth':month.month})
                    try:
                        h,final=get(target);v=parse_niabbs(h,g['boats'],aliases,today,end);out.update(v);sources.update({k:final for k in v});seen_boats.update(k[0] for k in v)
                    except Exception as e:errors.append({'url':target,'error':str(e)})
            elif host=='hanaho.net':
                for month in months(today,end):
                    target=urljoin(u,'ajax_get_bk_diary.php')+'?'+urlencode({'ymd':month.strftime('%Y%m%d'),'fymd':max(today,month).strftime('%Y%m%d')})
                    try:
                        raw,final=get(target);response=json.loads(raw)
                        if response.get('rslt')!='ok':raise ValueError('예약 조회 API 오류')
                        v=parse_hanaho(response['cont'],g['boats'],today,end);out.update(v);sources.update({k:final for k in v});seen_boats.update(k[0] for k in v)
                    except Exception as e:errors.append({'url':target,'error':str(e)})
            elif host=='boryeongharbor.com':
                for month in months(today,end):
                    target=urljoin(u,'page.php')+'?'+urlencode({'hid':'status','sch_year':month.year,'sch_month':month.month,'sch_day':1})
                    try:
                        h,final=get(target);v=parse_wz(h,g['boats'],aliases,today,end,month);out.update(v);sources.update({k:final for k in v});seen_boats.update(k[0] for k in v)
                    except Exception as e:errors.append({'url':target,'error':str(e)})
            else:
                cursor=today
                # The backend ignores won=30 on several sites: advance by dates actually returned.
                while cursor<=end:
                    target=date_url(u,cursor)
                    try:
                        h,final=get(target);ds=absorb(h,final)
                        actual=[date.fromisoformat(d) for d in ds if date.fromisoformat(d)>=cursor]
                        if not actual:
                            errors.append({'url':final,'error':'요청 날짜 이후 예약 표 없음'});break
                        if today<end and cursor==today and len(set(actual))==1:
                            # User deferred providers requiring one request per day.
                            deferred.extend(g['boats']);break
                        cursor=max(actual)+timedelta(days=1)
                    except Exception as e:
                        errors.append({'url':target,'error':str(e)});cursor+=timedelta(days=8)
    for alternate,boats in list(ALTERNATES.get(host,[]))+g.get('alternates',[]):
        boats=[b for b in boats if b in g['boats'] and not any(k[0]==b for k in out)]
        if not boats:continue
        if '.sunsang24.com' in alternate:
            for month in months(today,end):
                target=alternate.rstrip('/')+'/'+month.strftime('%Y%m')
                try:
                    h,u=get(target);v=parse_sunsang(h,boats,today,end);out.update(v);sources.update({k:u for k in v});seen_boats.update(k[0] for k in v)
                except Exception as e:errors.append({'url':target,'error':str(e)})
        else:
            cursor=today
            while cursor<=end:
                target=date_url(alternate,cursor)
                try:
                    h,u=get(target);ds=absorb(h,u,boats);actual=[date.fromisoformat(d) for d in ds if date.fromisoformat(d)>=cursor]
                    if not actual:break
                    if today<end and cursor==today and len(set(actual))==1:
                        deferred.extend(boats);break
                    cursor=max(actual)+timedelta(days=1)
                except Exception as e:errors.append({'url':target,'error':str(e)});break
    missing=[b for b in g['boats'] if not any(k[0]==b for k in out)]
    state='ok' if out and not missing and not errors else 'partial' if out else 'no_data' if visited else 'fetch_failed'
    if deferred:state='deferred_daily' if all(b in deferred for b in g['boats']) else 'partial'
    health={'status':state,'url':g['url'],'entries':len(out),'boats':list(dict.fromkeys(g['boats']+deferred)),'boat_ids':g['boat_ids'],'missing_boats':list(dict.fromkeys(missing+deferred)),'deferred_boats':list(dict.fromkeys(deferred)),'boat_notices':notices,'date_notices':date_notices,'observed_ship_labels':sorted(labels),'pages_checked':len(visited),'http_requests':client.requests,'cache_hits':client.cache_hits,'booking_urls':list(dict.fromkeys(sources.values())),'errors':errors}
    print(f'{host}: {state}, {len(out)}건, 미수집 {len(health["missing_boats"])}척, {len(visited)}페이지',flush=True)
    return out,sources,health

def atomic_json(path,value):
    import tempfile
    os.makedirs(os.path.dirname(os.path.abspath(path)),exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.json-',dir=os.path.dirname(os.path.abspath(path)))
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,separators=(',',':'))
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def collect_target_site(host,g,start,end,gap,targets):
    if targets is None:return collect_site(host,g,start,end,gap)
    wanted=sorted({ds for bid in g['boat_ids'].values() for ds in targets.get(str(bid),[]) if start.isoformat()<=ds<=end.isoformat()})
    spans=[]
    for ds in wanted:
        d=date.fromisoformat(ds)
        if spans and d==spans[-1][1]+timedelta(days=1):spans[-1][1]=d
        else:spans.append([d,d])
    client=Client(gap);out={};sources={};parts=[]
    for a,b in spans:
        names=[name for name,bid in g['boat_ids'].items() if any(a.isoformat()<=ds<=b.isoformat() for ds in targets.get(str(bid),[]))]
        selected=dict(g,boats=names,boat_ids={name:g['boat_ids'][name] for name in names})
        rows,urls,h=collect_site(host,selected,a,b,gap,client)
        for key,value in rows.items():
            if key[1] in targets.get(str(g['boat_ids'][key[0]]),[]):out[key]=value;sources[key]=urls[key]
        parts.append(h)
    h={'url':g['url'],'boats':g['boats'],'boat_ids':g['boat_ids'],'entries':len(out),
       'pages_checked':sum(x.get('pages_checked',0) for x in parts),'errors':[e for x in parts for e in x.get('errors',[])],
       'missing_boats':[n for n in g['boats'] if not any(k[0]==n for k in out)],
       'boat_notices':{},'date_notices':{},'deferred_boats':[]}
    for x in parts:
        h['boat_notices'].update(x.get('boat_notices',{}))
        for n,days in x.get('date_notices',{}).items():h['date_notices'].setdefault(n,{}).update(days)
        h['deferred_boats']+=x.get('deferred_boats',[])
    h['http_requests']=client.requests;h['cache_hits']=client.cache_hits
    h['status']='ok' if parts and all(x.get('status')=='ok' for x in parts) else 'partial' if out else 'fetch_failed'
    return out,sources,h

def merge_site_health(old,new,g,start,end,targets=None):
    """Refresh queried ships/dates; preserve other ships and dates in this fleet."""
    merged=dict(old);merged.update(new)
    names=set(g['boats']);notice=dict(old.get('boat_notices',{}))
    # A failed lookup cannot clear a previously verified notice.
    if new.get('status')=='ok':
        for n in names:notice.pop(n,None)
    notice.update(new.get('boat_notices',{}));merged['boat_notices']=notice
    dates={n:dict(ds) for n,ds in old.get('date_notices',{}).items()}
    if new.get('status')=='ok':
        for n in names:
            dates[n]={ds:t for ds,t in dates.get(n,{}).items() if not (start.isoformat()<=ds<=end.isoformat() and (targets is None or ds in targets.get(str(g['boat_ids'][n]),[])))}
    for n,ds in new.get('date_notices',{}).items():dates.setdefault(n,{}).update(ds)
    merged['date_notices']={n:ds for n,ds in dates.items() if ds}
    merged['boats']=list(dict.fromkeys(old.get('boats',[])+new.get('boats',[])))
    merged['boat_ids']=dict(old.get('boat_ids',{}),**new.get('boat_ids',{}))
    merged['missing_boats']=[n for n in old.get('missing_boats',[]) if n not in names]+new.get('missing_boats',[])
    return merged

def main():
    import argparse
    parser=argparse.ArgumentParser(description='등록된 모든 홈페이지의 날짜별 예약 현황 수집')
    parser.add_argument('--targets-file',help=argparse.SUPPRESS)
    parser.add_argument('--year',type=int,help='시즌 연도 (기본: 올해, 12월이면 다음 해)')
    parser.add_argument('--incremental',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--sites',nargs='*',help='점검할 도메인 (생략하면 전체)')
    parser.add_argument('--boat-ids',nargs='+',type=int,help='지정 선박만 수집')
    parser.add_argument('--ports',nargs='+',help='수집할 항구 (생략하면 전체)')
    parser.add_argument('--workers',type=int,default=8)
    parser.add_argument('--gap',type=float,default=1.0)
    span=parser.add_mutually_exclusive_group()
    span.add_argument('--near-days',type=int,help='오늘부터 N일까지만 수집')
    span.add_argument('--after-days',type=int,help='오늘+N일 이후만 수집')
    span.add_argument('--from-date',help='지정 범위 수집 시작일 YYYY-MM-DD (--to-date와 함께)')
    parser.add_argument('--to-date',help='지정 범위 수집 종료일 YYYY-MM-DD')
    args=parser.parse_args()
    if not 1<=args.workers<=8:parser.error('--workers: 1~8')
    with open(os.path.join(DATA,'boats.json'), encoding='utf-8') as f:
        boats = json.load(f)['boats']
    if args.boat_ids:
        args.boat_ids=resolve_boat_ids(boats,args.boat_ids)
        if not args.boat_ids:
            print("수집 대상 선박이 모두 삭제되어 건너뜁니다.")
            return SKIPPED
    targets=load_targets(args.targets_file,boats)
    if targets is not None and not targets:return SKIPPED
    if targets is not None:args.boat_ids=[int(bid) for bid in targets]
    # Dedicated Sunsang24 fleets are handled once by scrape_sunsang24.py.
    boats=[b for b in boats if not ('.sunsang24.com' in (b.get('channels',{}).get('homepage') or '') and urlsplit(b.get('channels',{}).get('sunsang24') or '').hostname==urlsplit(b.get('channels',{}).get('homepage') or '').hostname)]
    sites=sites_from_catalog(boats)
    all_sites=sites.copy()
    if args.ports or args.boat_ids:sites=sites_from_catalog([b for b in boats if (not args.ports or b.get("port") in args.ports) and (not args.boat_ids or b["bid"] in args.boat_ids)])
    if args.sites:
        selected={h.encode('idna').decode('ascii').removeprefix('www.') for h in args.sites}
        sites={k:v for k,v in sites.items() if k in selected}
    now=datetime.now(KST);year=args.year or now.year+(1 if now.month==12 else 0)
    today,collection_start,end=season_window(now,year);checked=now.isoformat(timespec='seconds')
    # 이번 실행에서 실제로 조회·교체할 날짜 범위 (시즌 전체 보존 범위는 today~end 그대로)
    range_start,range_end=collection_start,end
    if args.near_days:range_end=min(end,collection_start+timedelta(days=args.near_days-1))
    if args.after_days:range_start=collection_start+timedelta(days=args.after_days)
    if args.from_date or args.to_date:
        try:range_start=max(collection_start,date.fromisoformat(args.from_date));range_end=min(end,date.fromisoformat(args.to_date))
        except (TypeError,ValueError):parser.error('--from-date와 --to-date를 YYYY-MM-DD로 함께 지정하세요')
    if range_start>range_end:
        print(f'수집할 날짜 없음: {range_start}~{range_end}');return SKIPPED
    path=os.path.join(DATA,'status_homepages.json')
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError,json.JSONDecodeError):data={'dates':{}}
    data.setdefault('by_boat_id',{});data.setdefault('dates',{})
    for ds,day in data.get('dates',{}).items():
        for info in day.values():
            if info.get('boat_id') is not None:data['by_boat_id'].setdefault(ds,{})[str(info['boat_id'])]=info.copy()
    health={'checked_at':checked,'range':{'from':today.isoformat(),'to':end.isoformat()},'queried_range':{'from':range_start.isoformat(),'to':range_end.isoformat()},'sites':{}}
    if True:
        try:
            with open(os.path.join(DATA,'site_health.json'), encoding='utf-8') as f:
                previous = json.load(f)
            if previous.get('range',{}).get('to')==health['range']['to']:
                for old_host,h in previous.get('sites',{}).items():
                    canonical=old_host.encode('idna').decode('ascii').removeprefix('www.')
                    if canonical not in all_sites:continue
                    names=all_sites[canonical]['boats'];h=dict(h)
                    h['boats']=names;h['boat_ids']=all_sites[canonical]['boat_ids']
                    h['missing_boats']=[b for b in names if b in h.get('missing_boats',[])]
                    health['sites'][canonical]=h
        except (FileNotFoundError,json.JSONDecodeError):pass
    outcomes=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(collect_target_site,host,g,range_start,range_end,args.gap,targets):host for host,g in sites.items()}
        for future in concurrent.futures.as_completed(futures):
            host=futures[future]
            try:out,sources,h=future.result()
            except Exception as e:
                out={};sources={};h={'status':'error','error':str(e),'boats':sites[host]['boats'],'boat_ids':sites[host]['boat_ids'],'missing_boats':sites[host]['boats'],'entries':0}
            outcomes.append(h)
            entry_checked=datetime.now(KST).isoformat(timespec='seconds')
            health['sites'][host]=merge_site_health(health['sites'].get(host,{}),h,sites[host],range_start,range_end,targets)
            health['sites'][host]['last_attempt']={k:h[k] for k in ('status','entries','boats','boat_ids','missing_boats','deferred_boats','observed_ship_labels','errors','error') if k in h}
            health['sites'][host]['last_attempt'].update(checked_at=entry_checked,queried_range={'from':range_start.isoformat(),'to':range_end.isoformat()})
            # Replace freshly read boats in this range; keep old data only for failures, marked stale.
            succeeded={k[0] for k in out}
            for ds,day in data['by_boat_id'].items():
                if not range_start.isoformat()<=ds<=range_end.isoformat():continue
                for boat,bid in sites[host]['boat_ids'].items():
                    if targets is not None and ds not in targets.get(str(bid),[]):continue
                    if str(bid) in day:
                        if boat in succeeded:del day[str(bid)]
                        else:day[str(bid)]['stale']=True
            for ds,day in data.get('dates',{}).items():
                if range_start.isoformat()<=ds<=range_end.isoformat():
                    for boat in list(day):
                        if boat not in sites[host]['boat_ids']:continue
                        if targets is not None and ds not in targets.get(str(sites[host]['boat_ids'][boat]),[]):continue
                        if boat in succeeded and day[boat].get('source')!='sunsang24' and day[boat].get('boat_id') in (None,sites[host]['boat_ids'][boat]):del day[boat]
                        elif boat in sites[host]['boats'] and day[boat].get('source')=='homepage' and day[boat].get('boat_id') in (None,sites[host]['boat_ids'][boat]):day[boat]['stale']=True
            for (boat,ds),(state,n) in out.items():
                day=data.setdefault('dates',{}).setdefault(ds,{})
                entry={'status':state,'remaining':n,'source':'homepage','source_url':sources[(boat,ds)],'checked_at':entry_checked,'boat_id':sites[host]['boat_ids'][boat]}
                data['by_boat_id'].setdefault(ds,{})[str(entry['boat_id'])]=entry
                if day.get(boat,{}).get('source')!='sunsang24' and day.get(boat,{}).get('boat_id') in (None,entry['boat_id']):day[boat]=entry
    data['by_boat_id']={ds:day for ds,day in sorted(data['by_boat_id'].items()) if today.isoformat()<=ds<=end.isoformat() and day}
    data['dates']={ds:day for ds,day in sorted(data['dates'].items()) if today.isoformat()<=ds<=end.isoformat() and day}
    data['updated_at']=datetime.now(KST).isoformat(timespec='seconds')
    health['summary']={'sites':len(health['sites']),'entries':sum(h.get('entries',0) for h in health['sites'].values()),'collected_boats':sum(len(h.get('boats',[]))-len(h.get('missing_boats',[])) for h in health['sites'].values()),'missing_boats':sum(len(h.get('missing_boats',[])) for h in health['sites'].values())}
    with open(os.path.join(DATA,'boats.json'),encoding='utf-8') as f:current=json.load(f)['boats']
    compact_status(data,current)
    atomic_json(path,data);atomic_json(os.path.join(DATA,'site_health.json'),health)
    print(json.dumps(health['summary'],ensure_ascii=False))
    return collection_outcome(outcomes)

if __name__=='__main__':
    import sys
    sys.exit(main())
