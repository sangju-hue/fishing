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
    def test_mobile_stacked_calendar_reads_only_remaining_row(self):
        h='<table><tr><td>2027-10-03(일요일)</td></tr><tr><td><p>성주산호(쭈꾸미) 오전</p></td></tr><tr><td>예약확정 99명</td></tr><tr><td>정원 : 60 명 / 잔여 : <span>3</span> 명</td></tr></table>'
        out=parse_niabbs(h,['성주산호'],{},date(2027,10,3),END)
        self.assertEqual(out,{('성주산호','2027-10-03'):('available',3)})
    def test_daily_only_provider_is_deferred_after_first_date(self):
        from collect_homepages import collect_site
        group={'url':'https://example.test/index.php?mid=bk','boats':['테스트호'],'boat_ids':{'테스트호':1},'aliases':{}}
        with patch('collect_homepages.Client.fetch',return_value=(booking_row('20271003'),'https://example.test/index.php?mid=bk')) as fetch:
            _,_,health=collect_site('example.test',group,date(2027,10,3),END,0)
        self.assertEqual(health['deferred_boats'],['테스트호'])
        self.assertLessEqual(fetch.call_count,2)
    def test_booking_link_belongs_to_trip_with_displayed_remaining(self):
        from scrape_sunsang24 import aggregate
        result=aggregate([('테스트호','문어','available',2,'101'),('테스트호','쭈꾸미','available',7,'102')])
        self.assertEqual(result['remaining'],7)
        self.assertEqual(result['sno'],'102')
        self.assertNotIn('fish',result)
    def test_captain_suffix_is_not_part_of_boat_name(self):
        self.assertEqual(match_boat('배짱호_임선장',['배짱호']),'배짱호')
        self.assertEqual(match_boat('총각1호 김선장(18인승)',['총각1호']),'총각1호')
        self.assertIsNone(match_boat('총각1호신조선',['총각1호']))
    def test_mobile_status_does_not_read_passenger_or_notice(self):
        h='<div class="reservation"><a name="20271003"></a><h1>2027년 10월 03일</h1><div class="res_box"><div class="ship_name"><h2>★테스트호★</h2></div><p class="ship_num">남은자리 4명</p><div class="ship_notice">잔여석 99명 승선자 명단</div></div></div>'
        out,_,dates=parse_booking(h,['테스트호'],today=date(2027,10,3),end=END)
        self.assertEqual(out,{('테스트호','2027-10-03'):('available',4)})
        self.assertEqual(dates,{'2027-10-03'})
    def test_morning_and_afternoon_keep_separate_ids(self):
        h='<table><tr><td rowspan="2">2027-10 03 (일)</td></tr><tr><td>오전 해피호 06:30~11:20 쭈꾸미</td><td>요금</td><td>정원 58</td><td>잔여 7</td><td>명단</td></tr><tr><td>오후 해피호 12:00~17:20 쭈꾸미</td><td>요금</td><td>정원 58</td><td>잔여 2</td><td>명단</td></tr></table>'
        out=parse_niabbs(h,['해피호(오전)','해피호(오후)'],{},date(2027,10,3),END)
        self.assertEqual(out,{('해피호(오전)','2027-10-03'):('available',7),('해피호(오후)','2027-10-03'):('available',2)})
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
        with tempfile.TemporaryDirectory() as tmp, patch.object(mac_scheduler, 'SETTINGS', os.path.join(tmp, 'settings.json')):
            s = mac_scheduler.Scheduler()
            self.assertEqual(s.cfg['near_days'], 14)
            self.assertEqual(s.cfg['fast_minutes'], 5)
            self.assertEqual(s.cfg['slow_minutes'], 30)
            # 유효한 설정 적용 테스트
            s.control('config', {'near_days': 7, 'fast_minutes': 3, 'slow_minutes': 20})
            self.assertEqual(s.cfg['near_days'], 7)
            self.assertEqual(s.cfg['fast_minutes'], 3)
            self.assertEqual(s.cfg['slow_minutes'], 20)
            # 허용 범위 벗어난 설정 예외 검사
            for invalid in ({'near_days': 0, 'fast_minutes': 5, 'slow_minutes': 30},
                            {'near_days': 14, 'fast_minutes': 0, 'slow_minutes': 30},
                            {'near_days': 14, 'fast_minutes': 5, 'slow_minutes': 150},
                            {'near_days': True, 'fast_minutes': 5, 'slow_minutes': 30}):
                with self.assertRaises(ValueError):
                    s.control('config', invalid)

    def test_range_validation(self):
        s = mac_scheduler.Scheduler()
        # 정상 범위 설정
        s.control('range', {'from_date': '2026-10-05', 'to_date': '2026-10-10'})
        self.assertEqual(s.pending_range, ['2026-10-05', '2026-10-10'])
        # 시작일이 종료일보다 늦은 경우 예외
        with self.assertRaises(ValueError):
            s.control('range', {'from_date': '2026-10-20', 'to_date': '2026-10-10'})

    def test_overrun_schedules_rest_from_completion(self):
        s = mac_scheduler.Scheduler()
        s.cfg['fast_minutes'] = 5
        with patch.object(mac_scheduler.time, 'monotonic', side_effect=[100, 430]), \
             patch.object(mac_scheduler.subprocess, 'run') as run, \
             patch.object(mac_scheduler, 'atomic_json'):
            run.return_value.returncode = 0
            s.run_once('fast')
        # 완료(430) 후 5분(300초) 휴지시간
        self.assertEqual(s.next_fast, 730)

class RangePortTests(unittest.TestCase):
    def test_selected_ports_reach_collector_and_completion(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(mac_scheduler, 'SETTINGS', os.path.join(tmp, 'settings.json')), patch.object(mac_scheduler, 'atomic_json'):
            s = mac_scheduler.Scheduler()
            s.control('range', {'from_date':'2026-10-05','to_date':'2026-10-06','ports':['오천항']})
            self.assertEqual(s.due_mode(), 'range')
            with patch.object(mac_scheduler.subprocess, 'run') as run:
                run.return_value.returncode = 0
                s.run_once('range')
                self.assertEqual(run.call_args.args[0][-2:], ['--ports', '오천항'])
            self.assertEqual(s.state['last_range_ports'], ['오천항'])
            with self.assertRaises(ValueError):
                s.control('range', {'from_date':'2026-10-05','to_date':'2026-10-06','ports':['없는항구']})

    def test_range_collects_selected_hosts_only(self):
        import run_collection as rc
        boats=[dict(bid=1,name='가호',port='오천항',channels={'homepage':'https://one.test'}),dict(bid=2,name='나호',port='무창포항',channels={'homepage':'https://two.test'})]
        with patch.object(rc,'load_boats',return_value=boats), patch.object(rc,'progress'), patch.object(rc,'run',return_value=0) as run, patch.object(rc,'collect_group',return_value=0) as group:
            rc.by_range('range','now',[], '2026-10-05','2026-10-06',['오천항'])
            self.assertEqual(run.call_args_list[0].args,('scrape_sunsang24.py','--incremental','--from-date','2026-10-05','--to-date','2026-10-06','--ports','오천항'))
            self.assertEqual(group.call_args.args[3],['one.test'])
            self.assertEqual(group.call_args.args[-2:],('--ports','오천항'))
            group.reset_mock()
            rc.by_range('range','now',[], '2026-10-05','2026-10-06',['없는항구'])
            group.assert_not_called()

class ManualStartTests(unittest.TestCase):
    def test_restore_waits_for_user_start_and_preserves_settings(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(mac_scheduler,'SETTINGS',os.path.join(tmp,'settings.json')):
            with open(mac_scheduler.SETTINGS,'w') as f:json.dump({'paused':True,'start_required':True,'near_days':14,'fast_minutes':10,'slow_minutes':30},f)
            s=mac_scheduler.Scheduler()
            class Alerts:
                def target_pairs(self):raise AssertionError('must not collect before user starts')
            s.alerts=Alerts()
            self.assertIsNone(s.due_mode());self.assertFalse(s.settings_only)
            s.control('run');self.assertEqual(s.due_mode(),'full')
            restored=mac_scheduler.Scheduler();self.assertFalse(restored.start_required)
            self.assertEqual(restored.cfg['fast_minutes'],10)

class RepeatingRangeTests(unittest.TestCase):
    def test_interval_persistence_and_due_time(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(mac_scheduler, 'SETTINGS', os.path.join(tmp, 'settings.json')):
            s = mac_scheduler.Scheduler()
            s.paused = True
            with patch.object(mac_scheduler.time, 'monotonic', return_value=100):
                s.control('range_auto', {'from_date': '2026-10-05', 'to_date': '2026-10-06', 'interval_minutes': 7})
                self.assertEqual(s.due_mode(), 'range')
                self.assertEqual(s.active_range, ['2026-10-05', '2026-10-06'])
            self.assertEqual(s.next_range, 520)
            with patch.object(mac_scheduler.time, 'monotonic', return_value=519):
                self.assertIsNone(s.due_mode())
            restored = mac_scheduler.Scheduler()
            self.assertEqual(restored.auto_range, s.auto_range)
            self.assertEqual(restored.range_interval_minutes, 7)
            s.control('range_auto_stop')
            self.assertIsNone(s.auto_range)
            self.assertTrue(s.paused)

    def test_reject_invalid_interval_and_pause_stops_repeat(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(mac_scheduler, 'SETTINGS', os.path.join(tmp, 'settings.json')):
            s = mac_scheduler.Scheduler()
            values = {'from_date': '2026-10-05', 'to_date': '2026-10-06'}
            for invalid in (0, 121, True, 1.5):
                with self.assertRaises(ValueError):
                    s.control('range_auto', dict(values, interval_minutes=invalid))
            s.control('range_auto', dict(values, interval_minutes=10))
            s.control('pause')
            self.assertIsNone(s.auto_range)
            self.assertIsNone(s.due_mode())

    def test_expired_range_does_not_collect_past_dates(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(mac_scheduler, 'SETTINGS', os.path.join(tmp, 'settings.json')):
            s = mac_scheduler.Scheduler()
            s.paused = True
            s.control('range_auto', {'from_date': '2026-10-05', 'to_date': '2026-10-05', 'interval_minutes': 10})
            with patch.object(mac_scheduler, 'season_window', return_value=(2026, mac_scheduler.date(2026, 10, 6), mac_scheduler.date(2026, 11, 30))):
                self.assertIsNone(s.due_mode())
            self.assertIsNone(s.auto_range)

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
