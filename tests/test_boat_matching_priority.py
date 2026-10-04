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

    def test_phone_after_heading(self):
        self.assertEqual(match_boat('메이저호 010-4172-1383 (이선장)', ['메이저호']), '메이저호')
        self.assertEqual(match_boat('스파이더호 010-1234-5678 (김선장)', ['대천항 스파이더호'], {'스파이더호':'대천항 스파이더호'}), '대천항 스파이더호')
        self.assertIsNone(match_boat('메이저호2호 010-1234-5678', ['메이저호']))

class CanonicalSelection(unittest.TestCase):
    def test_old_subscription_selects_current_ship(self):
        from collect_homepages import resolve_boat_ids, sites_from_catalog
        boats=[{'bid':1,'canonical_bid':2}, {'bid':2,'name':'현재호','channels':{'homepage':'https://example.com'}}]
        ids=resolve_boat_ids(boats,[1,2])
        self.assertEqual(ids,[2])
        self.assertEqual(sites_from_catalog([b for b in boats if b['bid'] in ids])['example.com']['boats'],['현재호'])
    def test_deleted_subscription_does_not_block_remaining_boats(self):
        from collect_homepages import resolve_boat_ids
        self.assertEqual(resolve_boat_ids([{'bid':2}],[1,2]),[2])

    def test_invalid_merge_fails_explicitly(self):
        from collect_homepages import resolve_boat_ids
        with self.assertRaises(ValueError):resolve_boat_ids([{'bid':1,'canonical_bid':1}],[1])
        with self.assertRaises(ValueError):resolve_boat_ids([{'bid':1,'canonical_bid':2}],[1])

class NiabbsHeading(unittest.TestCase):
    def test_trip_prefix_and_time_do_not_hide_boat(self):
        from homepage_engine import parse_niabbs
        from datetime import date
        for title,name in [('종일 하나호 04:30~16:00 쭈꾸미/갑오징어','하나호'),('먼바다 고속정 부킹호 04:00~17:00 갑오징어','부킹호'),('오전 해피호 06:30~11:20 쭈꾸미','해피호(오전)')]:
            html='<tr><td>2026-10-09 (금)</td><td><p>'+title+'</p></td></tr><tr><td>정원: 20 잔여: 4</td></tr>'
            self.assertEqual(parse_niabbs(html,[name],{},date(2026,10,9),date(2026,10,9)),{(name,'2026-10-09'):('available',4)})

class DeletedRange(unittest.TestCase):
    def test_all_deleted_does_not_trigger_full_collection(self):
        import run_collection
        from unittest.mock import patch
        with patch.object(run_collection,'load_boats',return_value=[{'bid':2}]), patch.object(run_collection,'run') as run:
            self.assertEqual(run_collection.by_range('range','now',[], '2026-10-09','2026-10-09',boat_ids=[1]),0)
            run.assert_not_called()
