import json
import os
import tempfile
import unittest
from datetime import date
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
from homepage_engine import parse_booking,parse_sunsang,parse_niabbs,match_boat,date_url,booking_links
from season import season_window,KST
from datetime import datetime, timezone
import mac_scheduler
import push_to_github
from collect_homepages import sites_from_catalog

START=date(2027,9,1)
END=date(2027,11,30)

def booking_row(day='20270903',remaining='<img alt="남은자리 7명">',ship='테스트호 22인승'):
    return f'<table><tr><td><span>{ship}</span></td><td>예약자 21명<table><tr><td>승선 명부</td></tr></table></td><td><div id="admin-right-{day}-1-0">{remaining}</div></td></tr></table>'

def sunsang_row(state,remaining=0):
    code=f'data-status_code="{state}"' if state in ('END','CANCEL') else ''
    return f'<tr><td class="ship_info"><div class="title">테스트호</div></td><td><ul data-sdate="2027-10-03"><li class="remain"><span {code}>남은자리 {remaining}명</span></li></ul></td></tr>'

class ParsingTests(unittest.TestCase):
    def test_same_korean_domain_shares_one_collection(self):
        host='범블비호.com';encoded=host.encode('idna').decode('ascii')
        boats=[dict(bid=1,name='가호',channels={'homepage':'http://'+host}),dict(bid=2,name='나호',channels={'homepage':'http://www.'+encoded})]
        self.assertEqual(len(sites_from_catalog(boats)),1)
    def test_query_window_never_starts_in_past(self):
        self.assertEqual(season_window(date(2026,10,2)),(date(2026,9,1),date(2026,10,2),date(2026,11,30)))
        self.assertEqual(season_window(date(2027,3,1))[1],date(2027,9,1))
        self.assertEqual(season_window(date(2026,12,1))[1],date(2027,9,1))
        self.assertGreater(season_window(date(2026,10,2),2025)[1],date(2025,11,30))
    def test_query_window_uses_korean_midnight(self):
        now=datetime(2026,10,1,16,tzinfo=timezone.utc)
        self.assertEqual(season_window(now)[1],date(2026,10,2))
    def test_image_alt_not_passenger_count(self):
        result,_,_=parse_booking(booking_row(),['테스트호'],today=START,end=END)
        self.assertEqual(result[('테스트호','2027-09-03')],('available',7))
    def test_empty_status_stays_unknown(self):
        result,_,dates=parse_booking(booking_row(remaining=''),['테스트호'],today=START,end=END)
        self.assertEqual(result,{})
        self.assertIn('2027-09-03',dates)
    def test_explicit_full(self):
        result,_,_=parse_booking(booking_row(remaining='<img alt="예약마감">'),['테스트호'],today=START,end=END)
        self.assertEqual(result[('테스트호','2027-09-03')],('full',0))
    def test_season_and_incremental_boundary(self):
        h=booking_row('20270831')+booking_row('20270901')+booking_row('20271130')+booking_row('20271201')
        out,_,_=parse_booking(h,['테스트호'],today=date(2027,10,1),end=END)
        self.assertEqual(set(out),{('테스트호','2027-11-30')})
    def test_renamed_or_different_boat_not_inferred(self):
        self.assertIsNone(match_boat('뉴테스트호',['테스트호']))
        self.assertEqual(match_boat('표시이름',['테스트호'],{'표시이름':'테스트호'}),'테스트호')
        self.assertEqual(match_boat('(신조선)테스트호(20인승)',['테스트호']),'테스트호')
    def test_date_selection_flag_and_next_year(self):
        q=parse_qs(urlsplit(date_url('https://example.test/index.php?mid=bk',date(2028,9,2))).query)
        self.assertEqual([q[k][0] for k in ('year','month','day','sel')],['2028','09','02','day'])
        self.assertEqual(q['mid'],['bk'])
    def test_bookings_do_not_follow_cancellation_and_login(self):
        h='<a href="/index.php?mid=bk">예약현황</a><a href="/login">로그인</a><a href="/cancel">예약취소</a>'
        self.assertEqual(booking_links(h,'https://example.test'),['https://example.test/index.php?mid=bk'])
    def test_multi_trip_uses_max_not_sum(self):
        h='<table>'+sunsang_row('END')+sunsang_row('',4)+sunsang_row('',9)+'</table>'
        self.assertEqual(parse_sunsang(h,['테스트호'],START,END)[('테스트호','2027-10-03')],('available',9))
        h=booking_row(remaining='남은자리 7명')+booking_row(remaining='예약마감')
        self.assertEqual(parse_booking(h,['테스트호'],today=START,end=END)[0][('테스트호','2027-09-03')],('available',7))
    def test_month_calendar_skips_past_and_uses_remaining_not_capacity(self):
        def trip(day,n):
            return f'<tr><td>{day}</td><td>오전 수양2호 (06:30) 쭈꾸미</td><td>요금</td><td>정원 61</td><td>잔여 {n}</td><td>예약 명단</td></tr>'
        h='<table>'+trip('2027-10 02 (토)',50)+trip('2027-10 03 (일)',26)+trip('',11)+'</table>'
        out=parse_niabbs(h,['수양호'],{'수양2호':'수양호'},date(2027,10,3),END)
        self.assertEqual(out,{('수양호','2027-10-03'):('available',26)})

class SettingsTests(unittest.TestCase):
    def test_default_valid_options_and_reject_invalid(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(mac_scheduler,'SETTINGS',os.path.join(tmp,'settings.json')):
            s=mac_scheduler.Scheduler();self.assertEqual(s.interval,5)
            for n in (1,5,10,30,60):
                s.set_interval(n);self.assertEqual(mac_scheduler.read_interval(),n)
            for n in (0,2,True,'5',None):
                with self.assertRaises(ValueError):s.set_interval(n)
            s.set_interval(5)
    def test_already_running_never_starts_second_process(self):
        s=mac_scheduler.Scheduler();s.state['running']=True
        with patch.object(mac_scheduler.subprocess,'run') as run:
            s.run_once();run.assert_not_called()
    def test_overrun_skips_missed_start_times(self):
        s=mac_scheduler.Scheduler();s.interval=5
        with patch.object(mac_scheduler.time,'monotonic',side_effect=[100,430]),patch.object(mac_scheduler.subprocess,'run') as run,patch.object(mac_scheduler,'atomic_json'):
            run.return_value.returncode=0;s.run_once()
        self.assertEqual(s.next_start,700)

class UploadTests(unittest.TestCase):
    def test_atomic_fast_forward_upload_and_secret_exclusion(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=os.path.join(tmp,'result.json')
            with open(path,'w') as f:json.dump({'dates':{}},f)
            calls=[]
            def api(method,url,token,data=None):
                calls.append((method,url,data))
                if '/git/ref/heads/' in url:return {'object':{'sha':'head'}}
                if method=='GET' and '/git/commits/' in url:return {'tree':{'sha':'base'}}
                if method=='GET' and '/git/trees/' in url:return {'tree':[]}
                return {'sha':'created'}
            with patch.object(push_to_github,'get_token',return_value='test-placeholder'),patch.object(push_to_github,'api',side_effect=api):
                push_to_github.publish_files({'data/result.json':path},'test')
                self.assertEqual(calls[-1][2],{'sha':'created','force':False})
                with self.assertRaises(ValueError):push_to_github.publish_files({'.github_token':path},'test')

if __name__=='__main__':unittest.main()
