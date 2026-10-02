#!/usr/bin/env python3
"""독자 홈페이지 예약현황 스크래퍼.

선상24가 아닌 독자 홈페이지 10곳의 메인 페이지에서 "선박별 예약현황"을 파싱해
data/status_homepages.json에 기록한다. source="homepage"로 표시.
기존 sunsang24 데이터는 덮어쓰지 않는다.

사이트별 형식:
- 더피싱(thefishing): blessho, haesin, hhj, napoli, ochzeus, sungryung, ssfish
  "배이름 (남은좌석)" 섹션 + "M.D(요일) [상태]" 라인
- hanaho: 하나호 — "M월 D일" 헤더 + "남은자리 : N명" 블록
- daemul: 오천항대물낚시 — "YYYY년 M월 D일" 일별 섹션 + 예약하기/대기하기
- yamujin: 야무진피싱 — "YYYY년 MM월 DD일" + 배이름 + 남은자리
"""
import html as htmlmod
import http.cookiejar
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
os.makedirs(DATA, exist_ok=True)
KST = timezone(timedelta(hours=9))
REQ_GAP = 2.5
TIMEOUT = 25
MAX_FUTURE_DAYS = 75  # 오늘~75일 이내만 기록

SITES = [
    {"key": "blessho", "url": "http://blessho.com/", "parser": "thefishing",
     "boats": ["블레스호", "퀸블레스호", "퍼스트호"], "name_map": {}},
    {"key": "haesin", "url": "http://haesin.net", "parser": "thefishing",
     "boats": ["구라미끼호", "킹스타호", "판타지호", "해신1호", "해신2호", "해신3호", "해신7호"],
     "name_map": {"킹스타": "킹스타호"}},
    {"key": "hhj", "url": "http://hhj.kr/", "parser": "thefishing",
     "boats": ["우리호", "월드피싱호", "형제피싱호"], "name_map": {}},
    {"key": "hanaho", "url": "http://www.hanaho.net", "parser": "hanaho",
     "boats": ["하나호"], "name_map": {}},
    {"key": "napoli", "url": "http://www.napoliho.net", "parser": "thefishing",
     "boats": ["나폴리2", "나폴리7", "나폴리9", "뉴나폴리"],
     "name_map": {"엠피닉스(나폴리9)": "나폴리9", "엠피닉스": "나폴리9"}},
    {"key": "ochzeus", "url": "http://www.ochzeus.com", "parser": "thefishing",
     "boats": ["제우스호"], "name_map": {}},
    {"key": "sungryung", "url": "http://www.sungryungho.com", "parser": "thefishing",
     "boats": ["성령5호", "성령7호"], "name_map": {}},
    {"key": "yamujin", "url": "http://www.yamujinfishing.com", "parser": "yamujin",
     "boats": ["럭셔리호", "루키호", "바다용사호", "야무진2호", "야무진호", "오션투어호", "팀베테랑호"],
     "name_map": {}},
    {"key": "daemul", "url": "http://xn--c20bm9by0le8h59d54l.com", "parser": "daemul",
     "boats": ["대물호", "두드림호", "위더스호"], "name_map": {}},
    {"key": "ssfish", "url": "https://www.ssfish.kr/index.php", "parser": "thefishing",
     "boats": ["무창포뉴천일호"], "name_map": {"뉴천일호": "무창포뉴천일호"}},
]

_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def fetch(url):
    """봇 차단 대응: 브라우저 헤더 + 쿠키 세션. 실패 시 예외."""
    req = urllib.request.Request(url, headers=_HEADERS)
    with _opener.open(req, timeout=TIMEOUT) as r:
        final = r.url
        if "kuipernet" in final:
            raise RuntimeError("blocked by kuipernet WAF")
        raw = r.read()
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def html_to_text(html):
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|tr|li|h[1-6]|table)>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = htmlmod.unescape(text).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def resolve_md(month, day, today):
    """M.D → YYYY-MM-DD. 오늘~MAX_FUTURE_DAYS 이내만 반환, 범위 밖이면 None."""
    for yr in (today.year, today.year + 1):
        try:
            d = date(yr, month, day)
        except ValueError:
            return None
        if d < today:
            continue
        if (d - today).days <= MAX_FUTURE_DAYS:
            return d.isoformat()
        return None
    return None


def classify_status(s):
    """상태 텍스트 → (status, remaining). 판단 불가면 unknown."""
    s = (s or "").strip()
    if not s:
        return ("unknown", None)
    m = re.search(r"(\d+)\s*(명|석|자리)", s)
    if m:
        n = int(m.group(1))
        return ("available", n) if n > 0 else ("full", 0)
    if any(k in s for k in ("예약완료", "예약불가", "마감", "정비일")):
        return ("full", 0)
    return ("unknown", None)


def normalize_boat(raw, name_map):
    raw = (raw or "").strip()
    if raw in name_map:
        return name_map[raw]
    base = re.sub(r"\s*\(.*?\)\s*$", "", raw).strip()
    if base in name_map:
        return name_map[base]
    base = re.sub(r"\s*\[.*?\]\s*$", "", base).strip()
    return base


# ---------------- parsers (text -> {(boat, date): (status, remaining)}) ----------------

def parse_thefishing(text, boats, name_map):
    """더피싱 형식: '배이름 (남은좌석)' 섹션 + 'M.D(요일) [상태]' 라인."""
    today = date.today()
    out = {}
    lines = [ln.strip() for ln in text.split("\n")]
    sections = []
    for i, ln in enumerate(lines):
        if "(남은좌석)" in ln:
            before = ln.split("(남은좌석)")[0].strip()
            if before:
                boat_raw = before
            else:
                j = i - 1
                while j >= 0 and not lines[j]:
                    j -= 1
                boat_raw = lines[j] if j >= 0 else ""
            # "(남은좌석)" 앞부분에 다른 텍스트가 섞인 경우(예: 공지사항) 걸러내기
            # 배 이름은 보통 짧음
            sections.append((boat_raw, i))
    date_re = re.compile(r"(\d{1,2})\.(\d{1,2})\s*\(([^)]*)\)\s*(.*)")
    for idx, (boat_raw, start) in enumerate(sections):
        end = sections[idx + 1][1] if idx + 1 < len(sections) else len(lines)
        boat = normalize_boat(boat_raw, name_map)
        if boat not in boats:
            continue
        sec = lines[start + 1:end]
        i = 0
        while i < len(sec):
            m = date_re.match(sec[i])
            if not m:
                i += 1
                continue
            status_text = m.group(4).strip()
            # 상태가 같은 줄에 없으면 다음 줄 확인 (다음 줄이 날짜/헤더가 아닐 때만)
            if not status_text and i + 1 < len(sec):
                nxt = sec[i + 1]
                if not date_re.match(nxt) and "(남은좌석)" not in nxt:
                    status_text = nxt.strip()
                    i += 1
            ds = resolve_md(int(m.group(1)), int(m.group(2)), today)
            if ds:
                status, remaining = classify_status(status_text)
                if status != "unknown":
                    out[(boat, ds)] = (status, remaining)
            i += 1
    return out


def parse_hanaho(text, boats, name_map=None):
    """하나호 형식: 'M월 D일' 헤더 나열 + 배 이름 + '※' 블록별 '남은자리 : N명'."""
    today = date.today()
    out = {}
    # 날짜 헤더 추출 (예약현황 섹션 근처)
    m_sec = re.search(r"예약현황(.*?)커뮤니티", text, re.S)
    sec = m_sec.group(1) if m_sec else text
    dates = []
    for m in re.finditer(r"(\d{1,2})월\s*(\d{1,2})일", sec):
        ds = resolve_md(int(m.group(1)), int(m.group(2)), today)
        if ds and ds not in dates:
            dates.append(ds)
    if not dates or "하나호" not in boats:
        return out
    # 배 이름 이후 블록들을 ※ 로 분리
    after = sec.split("하나호", 1)[-1]
    blocks = [b for b in after.split("※") if "남은자리" in b]
    for ds, blk in zip(dates, blocks):
        m = re.search(r"남은자리\s*:\s*(\d+)\s*명", blk)
        if not m:
            continue
        n = int(m.group(1))
        status = "available" if n > 0 else "full"
        out[("하나호", ds)] = (status, n if n > 0 else 0)
    return out


def parse_daemul(text, boats, name_map=None):
    """오천항대물낚시: 'YYYY년 M월 D일' 일별 섹션, 배별 '예약하기'(가능)/'대기하기'(마감)."""
    today = date.today()
    out = {}
    # 일별 섹션 분리
    parts = re.split(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", text)
    # parts: [pre, y, m, d, body, y, m, d, body, ...]
    for i in range(1, len(parts) - 1, 4):
        try:
            d = date(int(parts[i]), int(parts[i + 1]), int(parts[i + 2]))
        except ValueError:
            continue
        if d < today or (d - today).days > MAX_FUTURE_DAYS:
            continue
        ds = d.isoformat()
        body = parts[i + 3]
        for boat in boats:
            # 배 이름 섹션 찾기
            m = re.search(re.escape(boat) + r"(.*?)(?=" +
                          "|".join(re.escape(b) for b in boats if b != boat) + r"|\d{4}년\s*\d{1,2}월|$)",
                          body, re.S)
            if not m:
                continue
            seg = m.group(1)
            if "대기하기" in seg:
                out[(boat, ds)] = ("full", 0)
            elif "예약하기" in seg:
                out[(boat, ds)] = ("available", None)
    return out


def parse_yamujin(text, boats, name_map=None):
    """야무진: 'YYYY년 MM월 DD일' + 배이름[...] + '남은자리'."""
    today = date.today()
    out = {}
    lines = [ln.strip() for ln in text.split("\n")]
    cur_date = None
    cur_boat = None
    for ln in lines:
        m = re.match(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", ln)
        if m:
            try:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                cur_date = None
                continue
            cur_date = d.isoformat() if (d >= today and (d - today).days <= MAX_FUTURE_DAYS) else None
            cur_boat = None
            continue
        if cur_date:
            b = normalize_boat(ln, {})
            if b in boats:
                cur_boat = b
                continue
            if cur_boat and "남은자리" in ln:
                m2 = re.search(r"(\d+)\s*명", ln)
                if m2 and int(m2.group(1)) > 0:
                    out[(cur_boat, cur_date)] = ("available", int(m2.group(1)))
                else:
                    # 숫자 없으면 예약 가능으로 보되 인원 미상 → unknown扱いせず available(None)
                    out[(cur_boat, cur_date)] = ("available", None)
                cur_boat = None
    return out


PARSERS = {
    "thefishing": parse_thefishing,
    "hanaho": parse_hanaho,
    "daemul": parse_daemul,
    "yamujin": parse_yamujin,
}


def main():
    from collect_homepages import main as collect_all
    collect_all()


if __name__ == "__main__":
    main()
