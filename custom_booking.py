"""Public schedule adapters; passenger lists are never stored or interpreted."""
import re
from datetime import date
from urllib.parse import urljoin, urlencode, parse_qs, urlsplit
from homepage_engine import DOM, match_boat, status


def parse_bando(html, boats, aliases, start, end, month, url):
    out, sources = {}, {}
    for cell in DOM(html).root.walk('td'):
        # Outer layout cells contain multiple days: only a single day cell is valid.
        headings = [n.text().strip() for n in cell.walk('div')
                    if re.fullmatch(r'\s*\d+월\s*\d+일\s*[가-힣]*\s*', n.text())]
        if len(headings) != 1:
            continue
        m = re.search(r'(\d+)월\s*(\d+)일', headings[0])
        try:
            day = date(month.year, int(m[1]), int(m[2]))
        except ValueError:
            continue
        if not start <= day <= end:
            continue
        for row in cell.walk('li'):
            title = next((n for n in row.walk('span') if n.attrs.get('class') == 'tit'), None)
            if title is None:
                continue
            # Name precedes the destination; do not match passenger text.
            label = title.text().split('(')[0].strip()
            boat = match_boat(label, boats, aliases)
            if not boat:
                continue
            remaining = next((n.text() for n in row.walk('span')
                              if re.match(r'\s*남은자리\s*:', n.text())), '')
            m = re.search(r'남은자리\s*:\s*(\d+)', remaining)
            if not m:
                continue
            seats = int(m[1]); state = status(remaining)
            if state and state[0] in ('cancelled', 'weather', 'maintenance'):
                value = state
            else:
                value = ('available', seats) if seats else ('full', 0)
            key = boat, day.isoformat()
            link = next((a.attrs['href'] for a in row.walk('a')
                         if 'mode=step1' in a.attrs.get('href', '')), None)
            # Closed rows have no link; verified boat's rm_ix is supplied by catalog.
            target = urljoin(url, link) if link else url + '&' + urlencode(
                {'sch_year':day.year,'sch_month':day.month,'sch_day':day.isoformat()})
            out[key], sources[key] = value, target
    return out, sources


def parse_fishapp(payload, boats, aliases, start, end, url, wait_reserved=False):
    out, sources = {}, {}
    for row in payload.get('scheduleList', []):
        if row.get('DEL_FLAG') == 'Y':
            continue
        raw = str(row.get('SCHD_DATE', ''))
        try:
            day = date.fromisoformat(raw[:4]+'-'+raw[4:6]+'-'+raw[6:8])
        except ValueError:
            continue
        if not start <= day <= end:
            continue
        boat = match_boat(row.get('SHIP_NAME', ''), boats, aliases)
        if not boat:
            continue
        code = row.get('STATUS_CD')
        if code == '113_210':
            value = ('cancelled', 0)
        elif code == '113_180':
            value = ('completed', 0)
        else:
            fish = row.get('FISH_KIND') or row.get('FISH_KIND1') or ''
            if fish and not re.search(r'쭈꾸미|주꾸미|쭈갑', fish):
                value = ('other_fish', None)
            elif not fish:
                value=('unspecified',None)
            elif code != '113_110' or 'PSGR_CNT' not in row:
                # No capacity means unpublished, not a full boat.
                continue
            else:
                count = int(row['PSGR_CNT']) - int(row.get('RESERVE_CONFIRM_CNT', 0))
                if wait_reserved:
                    count -= int(row.get('RESERVE_WAIT_CNT', 0)) + int(row.get('WAIT_CNT', 0))
                count = max(0, count)
                value = ('available', count) if count else ('full', 0)
        key = boat, day.isoformat()
        if key in out:
            # Multiple trips must not overwrite or sum seats silently.
            raise ValueError('Fishapp 동일 날짜 복수 회차: 회차별 명부 필요')
        out[key] = value
        sources[key] = url + '?' + urlencode({'SCHD_MONTH':day.strftime('%Y%m'), 'SCHD_DATE':raw})
    return out, sources
