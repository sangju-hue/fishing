import unittest
from scrape_sunsang24 import parse_month,parse_simple_list
from homepage_engine import status

class CancelWithoutFish(unittest.TestCase):
    def test_explicit_cancel_without_target_fish(self):
        h='<tr data-sdate="2026-10-04"><td class="ship_info"><div class="title">누리호</div></td><div id="fish">출조안내</div><li class="remain"><b data-status_code="CANCEL">출항취소</b></li></tr>'
        self.assertEqual(parse_month(h,'202610')['2026-10-04'][0][2],'cancelled')
        self.assertEqual(parse_simple_list(h,'202610','누리호')['2026-10-04'][0][2],'cancelled')
    def test_homepage_cancel_word(self):
        self.assertEqual(status('출항취소'),('cancelled',0))
