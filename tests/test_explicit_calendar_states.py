import unittest
from homepage_engine import status,parse_booking
from scrape_sunsang24 import booking_state
from datetime import date
class ExplicitCalendarStates(unittest.TestCase):
    def test_actual_departure_completion(self):
        h='<tr><td><span>뉴승진호</span></td><td>출조를 완료하였습니다. 모두 수고하셨습니다.</td><td><div id="admin-right-20261003-1-0"></div></td></tr>'
        entries,_,_=parse_booking(h,['뉴승진호'],today=date(2026,10,3),end=date(2026,11,30))
        self.assertEqual(entries[('뉴승진호','2026-10-03')],('completed',0))
        self.assertIsNone(status('입금 완료'))
    def test_other_fish_is_not_target_availability(self):
        state=booking_state([('베테랑호','광어,우럭','full',0,'123')])
        self.assertEqual((state['status'],state['actual_status']),('other_fish','full'))
    def test_target_trip_wins_over_other_fish_available_seats(self):
        state=booking_state([('배','쭈꾸미','full',0,'123'),('배','우럭','available',20,'456')])
        self.assertEqual((state['status'],state['sno']),('full','123'))
    def test_unspecified_species_remains_unverified(self):
        state=booking_state([('배','출조안내','available',10,'123')])
        self.assertEqual(state['status'],'unspecified')
    def test_cancel_is_kept_without_species(self):
        self.assertEqual(booking_state([('배','','cancelled',None,'123')])['status'],'cancelled')

class NestedCalendarTests(unittest.TestCase):
    def test_nested_passenger_table_does_not_split_status_from_date(self):
        from scrape_sunsang24 import parse_month
        html='<table><tr><td class="ship_info"><div class="title">아이언호</div></td><td><ul data-sdate="2026-10-07" data-schedule_no="77"><div id="fish">주꾸미,갑오징어</div><table><tr><td>승객 표시 영역</td></tr></table></ul><li class="remain" style="text-align:center"><span data-status_code="END">예약마감</span></li></td></tr></table>'
        self.assertEqual(parse_month(html,'202610')['2026-10-07'],[('아이언호','주꾸미,갑오징어','full',None,'77')])
