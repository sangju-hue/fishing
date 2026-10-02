"""한국 시간 기준 매년 9~11월, 오늘 이후만 조회한다."""
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))

def season_window(now=None, year=None):
    now = now or datetime.now(KST)
    if isinstance(now, datetime):
        now = now.astimezone(KST).date()
    year = year if year is not None else now.year + (now.month == 12)
    start, end = date(year, 9, 1), date(year, 11, 30)
    return start, max(start, now), end
