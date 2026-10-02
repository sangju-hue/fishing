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

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.normpath(os.path.join(BASE, "..", "data"))
KST = timezone(timedelta(hours=9))
TARGET_MONTHS = 2          # 이번 달 + 다음 달
REQ_GAP = 2.0              # 배 사이 요청 간격(초) — 과도한 크롤링 방지
TIMEOUT = 25

JJUKKUMI = ("쭈꾸미", "주꾸미", "쭈갑", "갑오징어")  # 갑오징어 병행 출조도 포함


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
        rm = re.search(r'<li class="remain"(.*?)</li>', tr, re.S)
        status, remaining = "unknown", None
        if rm:
            cell = rm.group(1)
            sm2 = re.search(r'data-status_code="(END|CANCEL)"', cell)
            if sm2:
                status = "full" if sm2.group(1) == "END" else "cancelled"
            else:
                nm = re.search(r"남은자리.*?<span[^>]*>(\d+)명</span>", cell, re.S)
                if nm:
                    status, remaining = "available", int(nm.group(1))
                elif "예약마감" in cell:
                    status = "full"
        out.setdefault(sdate, []).append((ship, fish, status, remaining))
    return out


def aggregate(trips):
    """같은 날짜·같은 배의 여러 출조 집계.
    셀에 표시할 숫자는 '한 출조당 최대 잔여석'(max) — 합산하면 정원(예: 20명)을
    초과하는 숫자가 되어 오해를 부르므로, 실제 예약 가능한 단일 출조 기준으로 표시."""
    avail = [t for t in trips if t[2] == "available"]
    if avail:
        return {"status": "available", "remaining": max(t[3] or 0 for t in avail)}
    if any(t[2] == "full" for t in trips):
        return {"status": "full", "remaining": 0}
    if trips and all(t[2] == "cancelled" for t in trips):
        return {"status": "cancelled", "remaining": 0}
    return {"status": "unknown", "remaining": None}


def subdomain(url):
    m = re.match(r"https?://([a-z0-9-]+)\.sunsang24\.com", url or "")
    return m.group(1) if m else None


def main():
    with open(os.path.join(DATA, "boats.json"), encoding="utf-8") as f:
        boats_data = json.load(f)

    try:
        with open(os.path.join(DATA, "status.json"), encoding="utf-8") as f:
            status_data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        status_data = {"updated_at": None, "dates": {}}

    today = date.today().isoformat()
    months = []
    d = date.today().replace(day=1)
    for _ in range(TARGET_MONTHS):
        months.append(f"{d.year}{d.month:02d}")
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)

    now = datetime.now(KST)
    checked_at = now.strftime("%Y-%m-%d %H:%M")
    n_boats, n_dates = 0, 0

    for boat in boats_data.get("boats", []):
        sub = subdomain((boat.get("channels") or {}).get("sunsang24"))
        if not sub:
            continue
        n_boats += 1
        want = norm_ship(boat["name"])
        for ym in months:
            try:
                html = fetch(f"https://{sub}.sunsang24.com/ship/schedule_fleet/{ym}")
            except Exception as e:
                print(f"  ! {boat['name']} {ym}: fetch fail ({e})")
                continue
            parsed = parse_month(html, ym)
            # 페이지에 선박명 표기가 하나라도 있으면 선단 페이지 → 배 이름으로 필터
            page_has_ships = any(t[0] for trips in parsed.values() for t in trips)
            for sdate, trips in parsed.items():
                if sdate < today:
                    continue
                if page_has_ships:
                    mine = [t for t in trips if t[0] == want]
                    if not mine:
                        continue  # 이 배의 출조가 없는 날짜 → 미확인
                else:
                    mine = trips  # 단일 선박 페이지 → 전부 이 배의 것
                agg = aggregate(mine)
                if agg["status"] == "unknown":
                    continue
                # 정원을 초과하는 잔여석은 집계 오류 → 미확인 처리
                cap = boat.get("capacity")
                if cap and agg.get("remaining") and agg["remaining"] > cap:
                    print(f"  ! {boat['name']} {sdate}: remaining {agg['remaining']} > capacity {cap} — skipped")
                    continue
                entry = {
                    "status": agg["status"],
                    "remaining": agg["remaining"],
                    "source": "sunsang24",
                    "checked_at": checked_at,
                }
                status_data["dates"].setdefault(sdate, {})[boat["name"]] = entry
                n_dates += 1
            time.sleep(REQ_GAP)

    # 과거 날짜 정리
    status_data["dates"] = {k: v for k, v in status_data["dates"].items() if k >= today}
    status_data["updated_at"] = checked_at
    tmp = os.path.join(DATA, "status.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status_data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(DATA, "status.json"))
    print(f"done: {n_boats} boats, {n_dates} date-entries, updated {checked_at}")


if __name__ == "__main__":
    main()
