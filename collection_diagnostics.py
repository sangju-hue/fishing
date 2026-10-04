"""Explain collection problems without treating deferred or fallback data as lost."""
import json, os, re
from datetime import datetime
from booking_state import select_observation, observed_time

LABELS={'connection':'접속 실패','rate_limit':'요청 제한','identity':'배·링크 확인 필요',
        'no_data':'예약 데이터 미확인','deferred':'일별 조회 보류','auxiliary':'부가 요청 오류'}
def read(base,name):
    try:
        with open(os.path.join(base,'data',name),encoding='utf-8') as f:return json.load(f)
    except (OSError,ValueError):return {}

def classify(site):
    missing=site.get('missing_boats',[]);errors=site.get('errors',[])
    text=' '.join(str(e.get('error','')) for e in errors)+' '+str(site.get('error',''))
    if site.get('deferred_boats') or site.get('status')=='deferred_daily':return 'deferred'
    if not missing and site.get('entries') and errors:return 'auxiliary'
    if '429' in text:return 'rate_limit'
    if errors or site.get('status') in ('fetch_failed','error'):return 'connection'
    labels=site.get('observed_ship_labels',[])+site.get('selectable_ship_labels',[])
    normalized=lambda s:re.sub(r'\s+','',str(s)).lower()
    if missing and labels and any(not any(normalized(n) in normalized(label) for label in labels) for n in missing):return 'identity'
    return 'no_data'

def build_diagnostics(base,since=None):
    sources=[read(base,'status.json'),read(base,'status_homepages.json')]
    catalog={str(b['bid']):b for b in read(base,'boats.json').get('boats',[])}
    health_docs=[read(base,f) for f in ('site_health.json','site_health_sunsang24.json')]
    timestamps=[d['checked_at'] for d in health_docs if d.get('checked_at')]
    first_checked=min(timestamps) if timestamps else ''
    grouped={key:[] for key in LABELS}
    for health in health_docs:
        for host,stored in health.get('sites',{}).items():
            site=stored.get('last_attempt',stored)
            checked=site.get('checked_at') or health.get('checked_at','')
            if since and checked<since:continue
            if site.get('status') in ('ok','out_of_season',None):continue
            category=classify(site);names=site.get('missing_boats') or site.get('boats',[])
            recovered=[]
            span=site.get('queried_range') or health.get('queried_range',{})
            try:cutoff=datetime.fromisoformat(since or first_checked).timestamp()
            except (KeyError,ValueError):cutoff=float('inf')
            for name in names:
                bid=str(site.get('boat_ids',{}).get(name));bid=str(catalog.get(bid,{}).get('canonical_bid') or bid)
                if category=='auxiliary':continue
                if any(span.get('from','')<=ds<=span.get('to','9999') and (info:=select_observation(sources,ds,bid)) and observed_time(info)>=cutoff
                       for ds in set(sources[0].get('by_boat_id',{}))|set(sources[1].get('by_boat_id',{}))):recovered.append(name)
            reason='; '.join(str(e.get('error','')) for e in site.get('errors',[])) or site.get('error','')
            if category=='identity':reason='예약표 이름과 등록 이름 대조 필요'
            if category=='deferred':reason='하루씩 조회하는 방식으로 보류'
            if category=='no_data':reason='대상 배의 날짜별 예약 데이터 미확인'
            grouped[category].append({'site':host,'boats':names,'recovered':recovered,'reason':reason[:300]})
    groups=[{'key':k,'label':LABELS[k],'items':v} for k,v in grouped.items() if v]
    problems=sum(len(g['items']) for g in groups if g['key'] not in ('deferred','auxiliary'))
    return {'groups':groups,'summary':f'확인 필요 {problems}곳 · 일별 조회 보류 {len(grouped["deferred"])}곳 · 부가 요청 오류 {len(grouped["auxiliary"])}곳'}
