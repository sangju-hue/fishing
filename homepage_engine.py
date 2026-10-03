"""Public booking-page discovery and structural HTML parsers (stdlib only)."""
import re
from html.parser import HTMLParser
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlsplit, parse_qsl, urlencode, urlunsplit

class Node:
    def __init__(self, tag='', attrs=(), parent=None):
        self.tag, self.attrs, self.parent, self.children = tag, dict(attrs), parent, []
    def walk(self, tag=None):
        for child in self.children:
            if isinstance(child, Node):
                if tag is None or child.tag == tag:
                    yield child
                yield from child.walk(tag)
    def text(self):
        if self.tag in ('script', 'style'):
            return ''
        if self.tag == 'img':
            return self.attrs.get('alt', '')
        return ' '.join(c.text() if isinstance(c, Node) else c for c in self.children)

class DOM(HTMLParser):
    VOID = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr','frame'}
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]
        self.feed(html)
    def handle_starttag(self, tag, attrs):
        # Some older templates omit closing cells/rows.
        if tag in ('td','th') and self.stack[-1].tag in ('td','th'):
            self.stack.pop()
        if tag == 'tr' and self.stack[-1].tag == 'tr':
            self.stack.pop()
        n = Node(tag, attrs, self.stack[-1])
        self.stack[-1].children.append(n)
        if tag not in self.VOID:
            self.stack.append(n)
    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID: self.handle_endtag(tag)
    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break
    def handle_data(self, text):
        self.stack[-1].children.append(text)

def compact(text):
    return re.sub(r'\s+', '', text)

def match_boat(label, boats, aliases=None):
    raw = label.strip().strip('★☆◆◇◈●○ ')
    # Captain labels are separated by a space or underscore; do not accept a
    # different boat whose name merely starts with the same characters.
    raw = re.split(r'_|(?<=호)\s+(?=[가-힣A-Za-z])', raw)[0]
    label = compact(raw)
    label = re.sub(r'^(?:\(신조선\)|신조선)','',label)
    aliases = aliases or {}
    # Match the ship heading, never passenger names or notices in another cell.
    variants = {compact(b): b for b in boats}
    variants.update({compact(a): b for a,b in aliases.items() if b in boats})
    if label in variants: return variants[label]
    label = re.split(r'\(|（|\d+인승|\d+[.]\d+톤|▶|전용선', label)[0]
    if label in variants: return variants[label]
    # Formatting often adds a capacity or description after the actual name.
    for name in sorted(variants, key=len, reverse=True):
        if label.startswith(name) and re.match(r'^[\W\d]', label[len(name):]):
            return variants[name]
    return None

def status(text):
    text = compact(text)
    if re.search(r'출조취소|운항취소|결항|기상악화', text): return ('cancelled', 0)
    if re.search(r'예약완료|예약마감|예약불가|대기하기|정비일|휴무|출조없음', text): return ('full', 0)
    m = re.search(r'(?:남은자리|남은좌석|잔여석|잔여좌석|잔여인원)[:：]?(\d+)(?:명|석|자리)?', text)
    if not m: m = re.fullmatch(r'(\d+)(?:명|석|자리)',text)
    if m:
        n = int(m.group(1))
        return ('available',n) if n else ('full',0)
    return None

def merge_trip(out, key, result):
    """단일 출조의 최대 잔여석: 오전/오후의 좌석을 더하지 않는다."""
    old=out.get(key)
    rank={'cancelled':0,'full':1,'available':2}
    if old is None or rank[result[0]]>rank[old[0]] or result[0]==old[0]=='available' and (result[1] or 0)>(old[1] or 0):
        out[key]=result

def parse_booking(html, boats, aliases=None, today=None, end=None):
    """Use only ship/status cells under an explicit date, preserving image alt text."""
    today = today or date.today()
    end = end or today + timedelta(days=365)
    dom = DOM(html).root
    out, seen_boats, seen_dates = {}, set(), set()
    # Fishmap mobile pages expose several dates in reservation blocks instead
    # of desktop table rows. Read only the heading and ship_num status.
    for block in dom.walk('div'):
        if 'reservation' not in block.attrs.get('class','').split():continue
        head=next(block.walk('h1'),None)
        m=re.search(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일',head.text() if head else '')
        if not m:continue
        try:d=date(*map(int,m.groups()))
        except ValueError:continue
        seen_dates.add(d.isoformat())
        if not today<=d<=end:continue
        for box in block.walk('div'):
            if 'res_box' not in box.attrs.get('class','').split():continue
            heading=next((n for n in box.walk('div') if 'ship_name' in n.attrs.get('class','').split()),None)
            h2=next(heading.walk('h2'),heading) if heading else None
            boat=match_boat(h2.text(),boats,aliases) if h2 else None
            if not boat:continue
            seen_boats.add(boat)
            cell=next((n for n in box.walk('p') if 'ship_num' in n.attrs.get('class','').split()),None)
            result=status(cell.text()) if cell else None
            if result:merge_trip(out,(boat,d.isoformat()),result)
    for row in dom.walk('tr'):
        cells = [n for n in row.children if isinstance(n,Node) and n.tag == 'td']
        if len(cells) != 3: continue
        marker = next((n.attrs.get('id','') for n in cells[2].walk('div')
                       if re.match(r'admin-right-\d{8}-',n.attrs.get('id',''))), '')
        if not marker: continue
        try: d = datetime.strptime(marker.split('-')[2],'%Y%m%d').date()
        except ValueError: continue
        seen_dates.add(d.isoformat())
        if not today <= d <= end: continue
        heading = next(cells[0].walk('span'), cells[0])
        boat = match_boat(heading.text(), boats, aliases)
        if not boat: continue
        seen_boats.add(boat)
        result = status(cells[2].text())
        if result is None:
            for image in cells[2].walk('img'):
                m = re.search(r'/r_x_(\d+)\.',image.attrs.get('src',''))
                if m:
                    n=int(m.group(1));result=('available',n) if n else ('full',0)
        if result is None:
            buttons=' '.join(n.text() for n in cells[0].walk('a'))
            result=status(buttons)
            if result is None and '예약하기' in buttons: result=('available',None)
        if result is not None: merge_trip(out,(boat,d.isoformat()),result)
    # Other providers render one calendar_list table per date.
    for table in dom.walk('table'):
        if 'calendar_list' not in table.attrs.get('class',''): continue
        heads=list(table.walk('th'))
        if not heads: continue
        m=re.search(r'(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일',heads[0].text())
        if not m: continue
        try:d=date(*map(int,m.groups()))
        except ValueError:continue
        seen_dates.add(d.isoformat())
        if not today <= d <= end:continue
        for row in table.walk('tr'):
            cells=[n for n in row.children if isinstance(n,Node) and n.tag=='td']
            if len(cells)!=3:continue
            boat=match_boat(next(cells[0].walk('strong'),cells[0]).text(),boats,aliases)
            if not boat:continue
            seen_boats.add(boat)
            result=status(cells[2].text())
            if result:merge_trip(out,(boat,d.isoformat()),result)
    return out, seen_boats, seen_dates

def booking_links(html, base):
    out=[]
    for n in DOM(html).root.walk():
        href=n.attrs.get('href','') if n.tag=='a' else n.attrs.get('src','') if n.tag in ('frame','iframe') else ''
        if not href:continue
        text=n.text()+' '+n.attrs.get('title','')
        relevant=n.tag in ('frame','iframe') or re.search(r'예약|실시간|출조.*일정',text) or re.search(r'mid=bk(?:&|$)|/reservation(?:[/?]|$)|/booking',href)
        if not relevant or re.search(r'문의|취소|로그인|회원가입',text) or re.search(r'mid=(?:rqna|notice|gall|room)|mode=view|/post/|bo_table=',href):continue
        url=urljoin(base,href)
        if urlsplit(url).scheme in ('http','https') and url not in out:out.append(url)
    return out

def date_url(url, start, days=30):
    p=urlsplit(url);q=dict(parse_qsl(p.query))
    q.update(year=str(start.year),month=f'{start.month:02}',day=f'{start.day:02}',mode='list',won=str(days),PA_N_UID='0',sel='day')
    return urlunsplit((p.scheme,p.netloc,p.path,urlencode(q),''))

def parse_hanaho(html, boats, today, end):
    out={}
    for row in DOM(html).root.walk('tr'):
        m=re.fullmatch(r'tr_(\d{8})',row.attrs.get('id',''))
        if not m:continue
        d=datetime.strptime(m.group(1),'%Y%m%d').date()
        if not today<=d<=end:continue
        ship=next((n for n in row.walk('span') if 'ship-name' in n.attrs.get('class','').split()),None)
        avail=next((n for n in row.walk('span') if 'availcnt' in n.attrs.get('class','').split()),None)
        if avail is None:
            avail=next((n for n in row.walk('span') if '남은자리' in n.text()),None)
        if ship and avail:
            boat=match_boat(ship.text(),boats)
            result=status(avail.text())
            if boat and result:merge_trip(out,(boat,d.isoformat()),result)
    return out

def parse_wz(html, boats, aliases, today, end, month):
    out={}
    for row in DOM(html).root.walk('tr'):
        cells=[n for n in row.children if isinstance(n,Node) and n.tag=='td']
        if len(cells)!=5:continue
        day=re.match(r'\s*(\d{1,2})\s*\(',cells[0].text())
        if not day:continue
        try:d=date(month.year,month.month,int(day.group(1)))
        except ValueError:continue
        if not today<=d<=end:continue
        boat=match_boat(cells[2].text(),boats,aliases)
        num=next((n for n in cells[4].walk('span') if 'num' in n.attrs.get('class','').split()),None)
        if boat and num and num.text().strip().isdigit():
            n=int(num.text());merge_trip(out,(boat,d.isoformat()),('available',n) if n else ('full',0))
    return out

def parse_sunsang(html, boats, today, end):
    out={}
    # Calendars can be embedded as JSON-escaped HTML.
    html=html.replace('\\n','\n').replace('\\"','"').replace('\\/','/')
    for row in DOM(html).root.walk('tr'):
        ds=row.attrs.get('data-sdate','') or next((n.attrs['data-sdate'] for n in row.walk() if n.attrs.get('data-sdate')), '')
        if not ds:continue
        try:d=date.fromisoformat(ds)
        except ValueError:continue
        if not today<=d<=end:continue
        ship_cell=next((n for n in row.walk('td') if 'ship_info' in n.attrs.get('class','').split()),None)
        if ship_cell is None:continue
        title=next((n for n in ship_cell.walk('div') if 'title' in n.attrs.get('class','').split()),None)
        if title is None:continue
        boat=match_boat(title.text(),boats)
        if not boat:continue
        remain=next((n for n in row.walk('li') if 'remain' in n.attrs.get('class','').split()),None)
        if remain is None:continue
        code=next((n.attrs.get('data-status_code') for n in remain.walk() if n.attrs.get('data-status_code')),remain.attrs.get('data-status_code'))
        result=('cancelled',0) if code=='CANCEL' else ('full',0) if code=='END' else status(remain.text())
        if result:
            merge_trip(out,(boat,ds),result)
    return out

def parse_niabbs(html, boats, aliases, today, end):
    out={};current=None;current_boat=None
    for row in DOM(html).root.walk('tr'):
        cells=[n for n in row.children if isinstance(n,Node) and n.tag=='td']
        if not cells:continue
        marker=re.search(r'(\d{4})-(\d{2})[\s-]+(\d{1,2})\s*\(',cells[0].text())
        if marker:
            try:current=date(*map(int,marker.groups()))
            except ValueError:current=None
            current_boat=None
            cells=cells[1:]
        if len(cells)==1 and current is not None and today<=current<=end:
            cell=cells[0]
            p=next(cell.walk('p'),None)
            if p and re.search('주꾸미|쭈꾸미|쭈갑|갑오징어|문어',p.text()):
                current_boat=match_boat(p.text(),boats,aliases)
            if current_boat and re.match(r'\s*정원\s*[:：]',cell.text()):
                remaining=re.search(r'잔여\s*[:：]?\s*(\d+)',cell.text())
                if remaining:
                    n=int(remaining.group(1));merge_trip(out,(current_boat,current.isoformat()),('available',n) if n else ('full',0))
            continue
        if len(cells)!=5 or current is None or not today<=current<=end:continue
        heading=cells[0].text().strip()
        if not re.search('주꾸미|쭈꾸미|쭈갑|갑오징어|문어',heading):continue
        period=re.match(r'^\s*(오전|오후)\s*',heading)
        ship=re.sub(r'^\s*(?:오전|오후|종일|고속정)\s*','',heading)
        base=re.split(r'\(|\d{1,2}:\d{2}|쭈꾸미|주꾸미|갑오징어|문어',ship)[0].strip()
        split_name=f'{base}({period.group(1)})' if period else ''
        boat=split_name if split_name in boats else match_boat(ship,boats,aliases)
        remaining=re.search(r'잔여\s*(\d+)',cells[3].text())
        if boat and remaining:
            n=int(remaining.group(1));merge_trip(out,(boat,current.isoformat()),('available',n) if n else ('full',0))
    return out
