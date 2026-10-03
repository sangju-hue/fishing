import unittest
from homepage_engine import match_boat,booking_notices

class MatchingPriority(unittest.TestCase):
    def test_missing_calendar_heading_is_safe(self):
        self.assertIsNone(match_boat(None,['해성호']))
    def test_exact_boat_wins_over_another_boats_alias(self):
        self.assertEqual(match_boat('뉴승진호',['뉴승진호','빅토리호'],{'뉴승진호':'빅토리호'}),'뉴승진호')
    def test_decorated_alias(self):
        self.assertEqual(match_boat('☆발키리호☆',['발키리호 (오천루어스쿨)'],{'☆발키리호☆':'발키리호 (오천루어스쿨)'}),'발키리호 (오천루어스쿨)')
    def test_numbered_boat_is_not_same_boat(self):
        self.assertIsNone(match_boat('프랜드호2호',['프랜드호']))
    def test_explicit_year_booking_closure(self):
        text='선장님 개인사정으로 인해 2026년도 쭈갑예약 받지 않습니다.'
        h='<tr><td><span>뉴홀랜드호</span></td><td>'+text+'</td><td></td></tr>'
        self.assertEqual(booking_notices(h,['뉴홀랜드호']),{'뉴홀랜드호':text})
