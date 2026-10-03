import unittest
from homepage_engine import booking_notices, parse_booking
from datetime import date

class BookingNoticesTest(unittest.TestCase):
    def test_exact_notice_and_correct_ship(self):
        h='<tr><td><span>브이호</span></td><td><p>공지</p><p>27년시즌에 뵙겠습니다.</p></td><td></td></tr>'
        self.assertEqual(booking_notices(h,['브이호','뚱보호']),{'브이호':'27년시즌에 뵙겠습니다.'})
    def test_unrelated_notice_not_assigned(self):
        self.assertEqual(booking_notices('<p>27년시즌에 뵙겠습니다.</p>',['브이호']),{})
    def test_ordinary_trip_notice_is_not_season_closure(self):
        h='<tr><td><span>브이호</span></td><td>오늘 기상 취소</td><td>남은자리 3명</td></tr>'
        self.assertEqual(booking_notices(h,['브이호']),{})

class ConditionalBookingTest(unittest.TestCase):
    def test_conditional_notice_overrides_booking_full_button(self):
        h='<tr><td><span>위너호</span><a>대기하기</a></td><td>선장님 개인사정으로 2026년도 쭈갑예약은 상황에 따라 오픈합니다.</td><td><div id="admin-right-20261007-123-1"></div>예약마감</td></tr>'
        values,_,_=parse_booking(h,['위너호'],today=date(2026,10,3),end=date(2026,11,30))
        self.assertEqual(values[('위너호','2026-10-07')],('conditional',None))
