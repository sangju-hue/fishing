"""네이버에서 예약 주소 후보를 찾는다. 검색 결과만으로 명부를 변경하지 않는다."""
import argparse
import concurrent.futures
import json
import os
import re
import time
from datetime import datetime
from urllib.parse import quote, urlsplit
from collect_homepages import Client, atomic_json
from homepage_engine import DOM
from season import KST


def search_boat(boat):
    query = boat.get("search_query") or f"{boat.get('port', '')} {boat['name']} 예약"
    url = 'https://search.naver.com/search.naver?query=' + quote(query)
    result = {'boat_id': boat['bid'], 'name': boat['name'], 'port': boat.get('port'),
              'query': query, 'search_url': url, 'candidates': [], 'error': None}
    try:
        time.sleep(2)
        html, _ = Client(1).fetch(url)
        seen = set()
        for a in DOM(html).root.walk('a'):
            href = a.attrs.get('href', '')
            host = urlsplit(href).hostname or ''
            text = re.sub(r'\s+', ' ', a.text()).strip()
            if not href.startswith(('http://', 'https://')) or not text or href in seen: continue
            if (host.endswith('naver.com') and host not in ('blog.naver.com', 'cafe.naver.com', 'm.place.naver.com', 'booking.naver.com')) or host.endswith('pstatic.net'): continue
            if any(x in host for x in ('youtube.', 'instagram.', 'facebook.', 'coupang.', 'navercorp.', 'w3.org')): continue
            seen.add(href)
            result['candidates'].append({'url': href, 'title': text[:240]})
        result['candidates'] = result['candidates'][:30]
    except Exception as e:
        result['error'] = f'{type(e).__name__}: {e}'
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', default='diagnostics/unconfirmed/boats_before.json')
    parser.add_argument('--output', default='diagnostics/unconfirmed/naver_search.json')
    parser.add_argument('--workers', type=int, default=2)
    args = parser.parse_args()
    boats = json.load(open(args.input, encoding='utf-8'))
    if isinstance(boats, dict): boats = boats['boats']
    try: previous = json.load(open(args.output, encoding='utf-8'))
    except (OSError, ValueError): previous = {}
    jobs = [b for b in boats if str(b['bid']) not in previous or previous[str(b['bid'])].get('error')]
    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 3))) as pool:
        for result in pool.map(search_boat, jobs):
            result['checked_at'] = datetime.now(KST).isoformat(timespec='seconds')
            previous[str(result['boat_id'])] = result
            atomic_json(args.output, previous)
            print(result['boat_id'], result['name'], len(result['candidates']), result['error'] or '', flush=True)


if __name__ == '__main__': main()
