import unittest
from homepage_engine import booking_notices

class BookingNoticesTest(unittest.TestCase):
    def test_exact_notice_and_correct_ship(self):
        h='<tr><td><span>브이호</span></td><td><p>공지</p><p>27년시즌에 뵙겠습니다.</p></td><td></td></tr>'
        self.assertEqual(booking_notices(h,['브이호','뚱보호']),{'브이호':'27년시즌에 뵙겠습니다.'})
    def test_unrelated_notice_not_assigned(self):
        self.assertEqual(booking_notices('<p>27년시즌에 뵙겠습니다.</p>',['브이호']),{})
    def test_ordinary_trip_notice_is_not_season_closure(self):
        h='<tr><td><span>브이호</span></td><td>오늘 기상 취소</td><td>남은자리 3명</td></tr>'
        self.assertEqual(booking_notices(h,['브이호']),{})
