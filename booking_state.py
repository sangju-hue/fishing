"""Shared observation selection, bounded collection targets and status serialization."""
import json
from datetime import datetime
from season import KST

VALID_STATES = {'available','full','cancelled','maintenance','weather','conditional','completed','other_fish','unspecified'}
OK, FAILED, PARTIAL, SKIPPED = 0, 1, 2, 3

def observed_time(info):
    try:
        value=datetime.fromisoformat(info.get('checked_at',''))
        return value.timestamp() if value.tzinfo else float('-inf')
    except (ValueError,TypeError,AttributeError):return float('-inf')

def select_observation(sources, ds, bid):
    candidates=[]
    for rank,data in enumerate(sources):
        info=data.get('by_boat_id',{}).get(ds,{}).get(str(bid))
        if not info or info.get('stale') or info.get('status') not in VALID_STATES:continue
        if info.get('boat_id') is not None and str(info['boat_id'])!=str(bid):continue
        candidates.append((observed_time(info),-rank,info))
    return max(candidates,key=lambda x:x[:2])[2] if candidates else None

def collection_outcome(sites):
    sites=[s for s in sites if s.get('status') not in ('deferred_daily','out_of_season')]
    if not sites:return SKIPPED
    good=sum(bool(s.get('entries') or s.get('boat_notices') or s.get('date_notices')) for s in sites)
    bad=sum(s.get('status') not in ('ok',) for s in sites)
    return FAILED if not good and bad else PARTIAL if bad else OK

def combine_outcomes(results):
    actual=[x for x in results if x!=SKIPPED]
    if not actual:return SKIPPED
    if all(x==FAILED for x in actual):return FAILED
    return PARTIAL if any(x in (FAILED,PARTIAL) for x in actual) else OK

def compact_status(data, boats):
    """Schema 2: IDs only; legacy input is migrated before stripping duplicate rows."""
    valid={str(b['bid']) for b in boats if b.get('canonical_bid') is None}
    by=data.setdefault('by_boat_id',{})
    for ds,rows in data.get('dates',{}).items():
        for row in rows.values():
            key=str(row.get('boat_id'))
            if key in valid:by.setdefault(ds,{}).setdefault(key,row)
    data['by_boat_id']={ds:{bid:row for bid,row in rows.items() if bid in valid} for ds,rows in by.items()}
    data['by_boat_id']={ds:rows for ds,rows in data['by_boat_id'].items() if rows}
    data.pop('dates',None);data['schema_version']=2
    return data

def load_targets(path, boats):
    """Private target file: preserve individual boat/date pairs and map old IDs."""
    if not path:return None
    from collect_homepages import resolve_boat_ids
    with open(path,encoding='utf-8') as f:raw=json.load(f)
    if not isinstance(raw,dict) or len(raw)>5000:raise ValueError('잘못된 수집 대상')
    out={}
    for bid,dates in raw.items():
        if not isinstance(dates,list) or len(dates)>91:raise ValueError('잘못된 수집 날짜')
        for ds in dates:datetime.strptime(ds,'%Y-%m-%d')
        for current in resolve_boat_ids(boats,[int(bid)]):out.setdefault(str(current),set()).update(dates)
    return out
