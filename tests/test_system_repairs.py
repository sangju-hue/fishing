import contextlib,io,json,os,tempfile,threading,unittest
from datetime import datetime,timedelta,date
from unittest.mock import patch
from urllib.error import HTTPError
from season import KST
import test_ntfy_alerts as fixtures
import ntfy_alerts,collect_homepages as ch,mac_scheduler as ms,run_collection as rc,push_to_github as push
from booking_state import select_observation,compact_status,collection_outcome,OK,FAILED,PARTIAL,SKIPPED

class RepairTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.NtfyTests();self.f.setUp();self.a=self.f.a;self.p=self.f.p
    def tearDown(self):self.f.tearDown()
    def test_mobile_registration_starts_alert_targets_without_general_collection(self):
        with patch.object(ms,'SETTINGS',self.f.base+'/scheduler.json'):
            s=ms.Scheduler();s.paused=True;s.start_required=True;s.alerts=self.a
            self.a.on_activate=s.request_alert_collection
            self.assertIsNone(s.due_mode())
            self.a.register_request(self.p)
            self.assertTrue(s.alert_activation_pending.is_set());self.assertTrue(s.paused)
            self.assertEqual(s.due_mode(),'range');self.assertFalse(s.start_required);self.assertEqual(s.next_collection_kind,'alerts_auto')
            self.assertEqual(s.active_targets,{str(self.p['bid']):[self.p['date']]})
            s.start_required=True
            self.a.register_request(self.p);self.assertEqual(s.due_mode(),'range');self.assertFalse(s.start_required)
    def test_settings_only_blocks_alert_activation(self):
        s=ms.Scheduler();s.settings_only=True;s.start_required=True
        with patch.object(s,'save_settings') as save:s.request_alert_collection();save.assert_not_called()
        self.assertTrue(s.start_required)
    def test_latest_valid_backup_sends_and_wrong_ship_is_ignored(self):
        self.a.register(self.p);self.f.result('full',checked=(datetime.now(KST)-timedelta(hours=1)).isoformat())
        fresh={'status':'available','remaining':4,'checked_at':datetime.now(KST).isoformat(),'boat_id':1}
        self.f.write('status_homepages.json',{'by_boat_id':{self.f.ds:{'1':fresh}}})
        with patch.object(self.a,'publish') as send:self.a.check();send.assert_called_once()
        self.assertEqual(self.a.state['subscriptions'][0]['last_status'],'available')
        fresh['boat_id']=2
        self.assertIsNone(select_observation(({'by_boat_id':{self.f.ds:{'1':fresh}}},),self.f.ds,1))
    def test_topic_expiry_releases_collision_and_quota(self):
        self.a.register(self.p);self.a.state['subscriptions'][0]['date']='2025-10-01';self.a.save()
        self.a.register(dict(self.p,owner='2'*32));self.assertEqual(len(self.a.state['subscriptions']),1)
    def test_receipt_rejection_and_retry_are_idempotent(self):
        self.a.register(self.p)
        p=dict(self.p,owner='2'*32,request_id='a'*32,reply_topic='fishing-reply-'+'b'*32)
        with patch.object(self.a,'decrypt',return_value=p),patch.object(self.a,'publish') as send:
            self.a.process_item({'event':'message','id':'one','message':'mock'})
            self.assertFalse(json.loads(send.call_args.args[2])['ok'])
            self.a.process_item({'event':'message','id':'two','message':'mock'})
        self.assertEqual(len(self.a.state['subscriptions']),1)
        self.assertEqual(len(self.a.state['receipts']),1)
    def test_receipt_success_replay_does_not_create_new_group_or_reconfirm(self):
        p=dict(self.p,request_id='c'*32,reply_topic='fishing-reply-'+'d'*32)
        with patch.object(self.a,'decrypt',return_value=p),patch.object(self.a,'publish') as send:
            self.a.process_item({'event':'message','id':'one','message':'mock'});self.a.process_item({'event':'message','id':'two','message':'mock'})
            self.assertEqual(len(self.a.state['subscriptions']),1)
            self.assertEqual(sum(call.args[1]=='빈자리 알림 등록 완료' for call in send.call_args_list),1)
    def test_unchanged_files_are_not_reparsed_or_state_rewritten(self):
        self.a.register(self.p);self.f.result()
        with patch.object(self.a,'publish'):self.a.check();self.a.check()
        with patch('ntfy_alerts.read',wraps=ntfy_alerts.read) as read,patch('ntfy_alerts.atomic_json',wraps=ch.atomic_json) as write:
            self.a.check();self.assertEqual(read.call_count,0);self.assertEqual(write.call_count,0)
    def test_exact_sparse_targets_and_fresh_targets_skipped(self):
        self.f.write('boats.json',{'boats':[self.f.boat,dict(self.f.boat,bid=2,name='다른호')]})
        self.a.register(self.p);self.a.register(dict(self.p,bid=2,date='2026-11-30'))
        self.assertEqual(self.a.target_pairs(),{'1':[self.f.ds],'2':['2026-11-30']})
        self.f.result();self.assertEqual(self.a.target_pairs(),{'2':['2026-11-30']})
    def test_notifications_batched_by_topic_and_date(self):
        self.f.write('boats.json',{'boats':[self.f.boat,dict(self.f.boat,bid=2,name='다른호')]})
        self.a.register(self.p);self.a.register(dict(self.p,bid=2))
        info={'status':'available','remaining':3,'checked_at':datetime.now(KST).isoformat()}
        self.f.write('status.json',{'by_boat_id':{self.f.ds:{'1':info,'2':info}}})
        with patch.object(self.a,'publish') as send:self.a.check();self.assertEqual(send.call_count,1)
        self.assertTrue(all(r['notified'] for r in self.a.state['subscriptions']))
    def test_long_alerts_cannot_starve_regular_jobs(self):
        with patch.object(ms,'SETTINGS',self.f.base+'/settings.json'):s=ms.Scheduler()
        s.alerts=self.a;self.a.register(self.p);s.next_alerts=0;s.next_fast=0;s.next_slow=0
        self.assertEqual(s.due_mode(),'range');s.next_alerts=0
        self.assertIn(s.due_mode(),('fast','slow'))
    def test_failure_and_skip_do_not_update_last_success(self):
        with patch.object(ms,'SETTINGS',self.f.base+'/settings.json'):s=ms.Scheduler()
        baseline=s.state['last_fast_at']
        for code,name in [(FAILED,'failed'),(PARTIAL,'partial'),(SKIPPED,'skipped')]:
            with patch.object(ms.subprocess,'run') as run,patch.object(ms,'atomic_json'):
                run.return_value.returncode=code;s.run_once('fast')
            self.assertEqual(s.state['last_result'],name);self.assertEqual(s.state['last_fast_at'],baseline)
    def test_lock_busy_is_explicit_skip(self):
        with patch.object(rc,'BASE',self.f.base),patch('sys.argv',['run_collection.py']),patch.object(rc.fcntl,'flock',side_effect=BlockingIOError),contextlib.redirect_stdout(io.StringIO()):self.assertEqual(rc.main(),SKIPPED)
    def test_health_partial_merge_preserves_other_boats_and_other_dates(self):
        old={'boats':['A','B'],'boat_ids':{'A':1,'B':2},'boat_notices':{'B':'점검'},'date_notices':{'A':{'2026-10-09':'전','2026-10-10':'후'},'B':{'2026-10-09':'별도'}}}
        new={'status':'ok','boats':['A'],'boat_ids':{'A':1},'date_notices':{'A':{'2026-10-09':'새'}},'missing_boats':[]}
        merged=ch.merge_site_health(old,new,{'boats':['A'],'boat_ids':{'A':1}},date(2026,10,9),date(2026,10,9))
        self.assertEqual(merged['boat_notices']['B'],'점검');self.assertEqual(merged['date_notices']['A']['2026-10-10'],'후');self.assertEqual(merged['date_notices']['B']['2026-10-09'],'별도')
    def test_sparse_homepage_targets_never_request_intermediate_days(self):
        g={'url':'https://example.test','boats':['A','B'],'boat_ids':{'A':1,'B':2}}
        calls=[]
        def collect(host,g,start,end,gap,client):
            calls.append((g['boats'],start,end));name=g['boats'][0];key=(name,start.isoformat())
            return {key:('full',0)},{key:'https://example.test'},{'status':'ok','boats':g['boats'],'entries':1}
        with patch.object(ch,'collect_site',side_effect=collect):out,_,_=ch.collect_target_site('example.test',g,date(2026,10,9),date(2026,11,30),0,{'1':['2026-10-09'],'2':['2026-11-30']})
        self.assertEqual(len(calls),2);self.assertEqual(calls[0][1],calls[0][2]);self.assertEqual(calls[1][1],calls[1][2]);self.assertEqual(len(out),2)
    def test_date_navigation_links_are_not_loaded_as_frames(self):
        class Client:
            def __init__(self):self.calls=[];self.requests=0;self.cache_hits=0
            def fetch(self,url):self.calls.append(url);self.requests+=1;return '<html></html>',url
        client=Client();g={'url':'https://example.test/index.php?mid=bk','boats':['A'],'boat_ids':{'A':1}}
        parsed=({('A','2026-10-09'):('full',0)},{'A'},{'2026-10-09'})
        nav=['https://example.test/index.php?day='+str(i) for i in range(1,31)]
        with patch.object(ch,'parse_booking',return_value=parsed),patch.object(ch,'booking_notices',return_value={}),patch.object(ch,'booking_links',return_value=nav):
            result,_,_=ch.collect_site('example.test',g,date(2026,10,9),date(2026,10,9),0,client)
        self.assertEqual(len(client.calls),2);self.assertEqual(len(result),1)
    def test_id_only_schema_removes_deleted_rows(self):
        d={'dates':{'2026-10-09':{'A':{'boat_id':1,'status':'full'}}},'by_boat_id':{'2026-10-09':{'99':{'status':'full'}}}}
        compact_status(d,[{'bid':1}]);self.assertNotIn('dates',d);self.assertEqual(set(d['by_boat_id']['2026-10-09']),{'1'})
    def test_all_sites_failed_returns_failure(self):
        self.assertEqual(collection_outcome([{'status':'fetch_failed','entries':0}]),FAILED)
        self.assertEqual(collection_outcome([{'status':'ok','entries':1},{'status':'fetch_failed','entries':0}]),PARTIAL)
        self.assertEqual(collection_outcome([{'status':'deferred_daily','entries':0}]),SKIPPED)
    def test_local_request_accepts_large_valid_selection(self):
        boats=[dict(self.f.boat,bid=i,name='선박'+str(i)) for i in range(1,318)]
        self.f.write('boats.json',{'boats':boats})
        payload=dict(self.p,bid=0,ports=['*'],bids=list(range(1,318)))
        raw=json.dumps(payload).encode();self.assertGreater(len(raw),1024)
        class FakeScheduler:
            csrf='test'
            alerts=self.a
            wake=threading.Event()
            def snapshot(self):return {}
        handler=object.__new__(ms.handler(FakeScheduler(),8788));responses=[]
        handler.path='/alerts';handler.headers={'Host':'127.0.0.1:8788','X-CSRF-Token':'test','Content-Length':str(len(raw))}
        handler.rfile=io.BytesIO(raw);handler.send=lambda status,value:responses.append(status)
        handler.do_POST();self.assertEqual(responses,[200]);self.assertEqual(len(self.a.state['subscriptions']),317)
    def test_preview_rejects_hidden_encoded_and_parent_paths(self):
        from preview_server import PublicHandler
        for path in ['/.github_token','/.ntfy/state.json','/%2entfy/state.json','/data/../.github_token','/.git/config','/mac_scheduler.py']:
            h=object.__new__(PublicHandler);h.path=path;errors=[];h.send_error=lambda code:errors.append(code)
            self.assertIsNone(h.send_head());self.assertEqual(errors,[404])
    def test_publish_heartbeat_state_is_file_and_unchanged_data_throttles(self):
        self.f.write('result.json',{'updated_at':'first','value':1})
        with patch.object(push,'BASE',self.f.base),patch.object(push,'DATA_FILES',('data/result.json',)),patch.object(push,'publish_files') as publish:
            push.main()
            with open(self.f.base+'/.publish_state.json') as f:state=json.load(f)
            self.assertIn('digest',state)
            self.f.write('result.json',{'updated_at':'second','value':1})
            push.main();self.assertEqual(publish.call_count,1)
            self.f.write('result.json',{'updated_at':'third','value':2})
            push.main();self.assertEqual(publish.call_count,2)
    def test_publisher_retries_conflict_without_force_or_lost_updates(self):
        path=self.f.base+'/data/result.json';self.f.write('result.json',{'ok':True});calls=[]
        def api(method,url,token,data=None):
            calls.append((method,url,data))
            if '/git/ref/heads/' in url:return {'object':{'sha':'new-head' if sum(c[0]=='PATCH' for c in calls) else 'head'}}
            if method=='GET' and '/git/commits/' in url:return {'tree':{'sha':'base'}}
            if method=='GET' and '/git/trees/' in url:return {'tree':[]}
            if method=='PATCH' and sum(c[0]=='PATCH' for c in calls)==1:raise HTTPError(url,422,'conflict',{},None)
            return {'sha':'created'}
        with patch.object(push,'BASE',self.f.base),patch.object(push,'get_token',return_value='mock'),patch.object(push,'api',side_effect=api),patch.object(push.time,'sleep'):
            push.publish_files({'data/result.json':path},'test')
        self.assertEqual(sum(c[0]=='PATCH' for c in calls),2)
        self.assertTrue(all(not c[2]['force'] for c in calls if c[0]=='PATCH'))
        self.assertEqual([c[2]['parents'] for c in calls if c[0]=='POST' and '/git/commits' in c[1]][-1],['new-head'])

if __name__=='__main__':unittest.main()
