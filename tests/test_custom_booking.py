import unittest
from datetime import date
from custom_booking import parse_bando, parse_fishapp
from homepage_engine import date_url
from urllib.parse import urlsplit, parse_qs
from collect_homepages import sites_from_catalog, resolve_boat_ids

START=date(2026,10,4);END=date(2026,11,30)
class CustomBookingTests(unittest.TestCase):
    def test_bando_only_target_day_and_explicit_remaining(self):
        html='''<table><tr><td><div>10월 9일 금요일</div><ul>
        <li class="live"><span class="tit">반도피싱호(서해 오천항) 다른배이름(9)</span>
        <a href="/bbs/board.php?bo_table=schedule&amp;mode=step1&amp;rm_ix=4&amp;sch_day=2026-10-09">예약</a>
        <span>남은자리 : 3 예약가능</span></li>
        <li><span class="tit">다른호</span><span>남은자리 : 20 예약가능</span></li>
        </ul></td></tr><tr><td><div>10월 3일 토요일</div><li><span class="tit">반도피싱호</span><span>남은자리 : 5</span></li></td></tr></table>'''
        out,src=parse_bando(html,['반도피싱호'],{},START,END,date(2026,10,1),'https://www.bandofish.com/bbs/board.php?bo_table=schedule')
        self.assertEqual(out,{('반도피싱호','2026-10-09'):('available',3)})
        self.assertIn('rm_ix=4',src['반도피싱호','2026-10-09'])
    def parse(self,rows,wait=False):
        return parse_fishapp({'scheduleList':rows},['뉴해양호'],{'푸르러호(뉴해양호)':'뉴해양호'},START,END,'https://www.fishapp.co.kr/wp/B/H/schedule',wait)[0]
    def row(self,**fields):
        r={'SCHD_DATE':'20261009','SHIP_NAME':'푸르러호(뉴해양호)','STATUS_CD':'113_110','PSGR_CNT':8,'RESERVE_CONFIRM_CNT':4,'FISH_KIND':'쭈꾸미','DEL_FLAG':'N'};r.update(fields);return r
    def test_capacity_minus_confirmed_and_optional_wait(self):
        row=self.row(RESERVE_WAIT_CNT=1,WAIT_CNT=1)
        self.assertEqual(self.parse([row])[('뉴해양호','2026-10-09')],('available',4))
        self.assertEqual(self.parse([row],True)[('뉴해양호','2026-10-09')],('available',2))
    def test_cancelled_full_and_expired_are_distinct(self):
        for fields,want in [({'STATUS_CD':'113_210'},('cancelled',0)),({'STATUS_CD':'113_180'},('completed',0)),({'RESERVE_CONFIRM_CNT':8},('full',0)),({'FISH_KIND':'우럭'},('other_fish',None))]:
            self.assertEqual(self.parse([self.row(**fields)])[('뉴해양호','2026-10-09')],want)
    def test_unspecified_fish_is_not_available(self):
        self.assertEqual(self.parse([self.row(FISH_KIND='')])[('뉴해양호','2026-10-09')],('unspecified',None))
    def test_unpublished_past_deleted_and_wrong_ship_not_full(self):
        row=self.row();row.pop('PSGR_CNT')
        self.assertEqual(self.parse([row,self.row(DEL_FLAG='Y'),self.row(SHIP_NAME='다른호'),self.row(SCHD_DATE='20261003')]),{})
    def test_day_collection_preserves_selected_full_day_ship(self):
        query=parse_qs(urlsplit(date_url('http://redlight.kr/index.php?mid=bk&PA_N_UID=4885',START)).query)
        self.assertEqual(query['PA_N_UID'],['4885'])
    def test_trip_selectors_are_not_lost_when_grouping_host(self):
        boats=[{'bid':i,'name':name,'channels':{'homepage':'http://redlight.kr/index.php?mid=bk&PA_N_UID='+uid}} for i,name,uid in [(42,'오전','3179'),(43,'오후','3180'),(44,'종일','4885')]]
        group=sites_from_catalog(boats)['redlight.kr']
        self.assertEqual([names for url,names in group['alternates']],[['오후'],['종일']])
    def test_official_rename_preserves_old_subscription_id(self):
        self.assertEqual(resolve_boat_ids([{'bid':279,'canonical_bid':318},{'bid':318}],[279]),[318])
    def test_multiple_trips_require_separate_identity(self):
        with self.assertRaisesRegex(ValueError,'복수 회차'):self.parse([self.row(),self.row()])
if __name__=='__main__':unittest.main()
