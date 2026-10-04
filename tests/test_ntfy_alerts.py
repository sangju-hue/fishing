import base64
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
from ntfy_alerts import Alerts,OPENSSL
from season import KST
import mac_scheduler

class NtfyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.base=self.tmp.name;os.mkdir(self.base+'/data')
        self.boat={'bid':1,'name':'화니호','port':'오천항','channels':{'homepage':'https://example.com'}}
        self.write('boats.json',{'boats':[self.boat]});self.a=Alerts(self.base)
        self.ds=(datetime.now(KST)+timedelta(days=1)).date().isoformat()
        self.p={'label':'홍길동','action':'add','owner':'1'*32,'topic':'fishing-user-'+('a'*16),'bid':1,'date':self.ds}
    def tearDown(self):self.tmp.cleanup()
    def write(self,path,obj):
        with open(self.base+'/data/'+path,'w') as f:json.dump(obj,f)
    def result(self,state='available',checked=None,remaining=2):
        self.write('status.json',{'by_boat_id':{self.ds:{'1':{'status':state,'remaining':remaining,'checked_at':checked or datetime.now(KST).isoformat(),'source_url':'https://example.com/day'}}}})
    def test_two_people_separate_delivery_and_duplicate_suppression(self):
        self.a.register(self.p);self.a.register(dict(self.p,owner='2'*32,topic='fishing-user-'+('b'*16)))
        self.result()
        with patch.object(self.a,'publish') as send:
            self.a.check();self.assertEqual(send.call_count,2)
            self.assertEqual({c.args[0] for c in send.call_args_list},{self.p['topic'],'fishing-user-'+('b'*16)})
            self.a.check();self.assertEqual(send.call_count,2)
            self.result('full');self.a.check();self.result();self.a.check();self.assertEqual(send.call_count,4)
        restored=Alerts(self.base)
        self.assertTrue(all(r['notified'] for r in restored.state['subscriptions']))
        self.assertNotIn('owner',restored.snapshot()['subscriptions'][0])
    def test_stale_and_unknown_do_not_send_or_rearm(self):
        self.a.register(self.p)
        with patch.object(self.a,'publish') as send:
            self.result(checked=(datetime.now(KST)-timedelta(hours=1)).isoformat());self.a.check();send.assert_not_called()
            self.result();self.a.check();self.assertEqual(send.call_count,1)
            self.write('status.json',{});self.a.check();self.result();self.a.check();self.assertEqual(send.call_count,1)
    def test_collision_wrong_owner_and_retry(self):
        row=self.a.register(self.p)
        with self.assertRaises(ValueError):self.a.register(dict(self.p,owner='2'*32))
        self.a.remove(dict(self.p,owner='2'*32));self.assertEqual(len(self.a.state['subscriptions']),1)
        self.result()
        with patch.object(self.a,'publish',side_effect=OSError):self.a.check()
        self.assertFalse(row['notified']);self.assertIn('전송 실패',row['error'])
        with patch.object(self.a,'publish') as send:self.a.check();send.assert_called_once()
    def test_group_cancel_preserves_other_registrant(self):
        self.write('boats.json',{'boats':[self.boat,dict(self.boat,bid=2,name='두번째호')]})
        self.a.register_request(dict(self.p,bid=0,port='오천항'))
        self.a.register(dict(self.p,owner='2'*32,topic='other-person'))
        rows=self.a.snapshot()['subscriptions'];key=rows[0]['group_id']
        self.assertEqual(key,rows[1]['group_id'])
        self.assertNotEqual(key,rows[2]['group_id'])
        self.a.admin('group_pause',key)
        self.assertFalse(self.a.state['subscriptions'][0]['enabled'])
        self.a.admin('group_resume',key)
        self.assertTrue(self.a.state['subscriptions'][0]['enabled'])
        self.a.admin('group_delete',key)
        self.assertEqual(len(self.a.state['subscriptions']),1)
        self.assertEqual(self.a.state['subscriptions'][0]['topic'],'other-person')

    def test_name_required(self):
        for label in ('','   ',None):
            with self.assertRaises(ValueError):self.a.register(dict(self.p,label=label))

    def test_re_register_updates_display_name(self):
        self.a.register(self.p)
        self.a.register(dict(self.p,label='홍길동'))
        self.assertEqual(self.a.snapshot()['subscriptions'][0]['label'],'홍길동')
        self.assertEqual(len(self.a.state['subscriptions']),1)

    def test_owner_pause_resume_update_rollback(self):
        self.a.register(self.p)
        with self.assertRaises(ValueError):self.a.manage(dict(self.p,action='pause',owner='2'*32))
        self.a.manage(dict(self.p,action='pause'));self.assertEqual(self.a.targets(),([],[]))
        self.a.manage(dict(self.p,action='resume'));self.assertEqual(self.a.targets()[0],[1])
        old=[1,self.ds,self.p['topic'],'오천항']
        self.a.manage(dict(self.p,action='update',old=old,min_seats=4))
        self.assertEqual(self.a.state['subscriptions'][0]['min_seats'],4)
        with self.assertRaises(ValueError):self.a.manage(dict(self.p,action='update',old=old,min_seats=0))
        self.assertEqual(self.a.state['subscriptions'][0]['min_seats'],4)

    def test_minimum_seats_crossing_and_unknown_count(self):
        self.a.register(dict(self.p,min_seats=4))
        with patch.object(self.a,'publish') as send:
            self.result(remaining=2);self.a.check();send.assert_not_called()
            self.result(remaining=None);self.a.check();send.assert_not_called()
            self.result(remaining=4);self.a.check();self.assertEqual(send.call_count,1)
            self.result(remaining=5);self.a.check();self.assertEqual(send.call_count,1)
            self.result(remaining=3);self.a.check()
            self.result(remaining=4);self.a.check();self.assertEqual(send.call_count,2)
        for invalid in (0,101,True,1.5):
            with self.assertRaises(ValueError):self.a.register(dict(self.p,min_seats=invalid))

    def test_short_custom_topic(self):
        self.assertEqual(self.a.register(dict(self.p,topic='a'))['topic'],'a')
        self.assertEqual(self.a.register(dict(self.p,topic='sam9'))['topic'],'sam9')
        for topic in ('','한글','two words','a/b'):
            with self.assertRaises(ValueError):self.a.register(dict(self.p,topic=topic))

    def test_port_all_and_boat_all_registration_and_removal(self):
        self.write('boats.json',{'boats':[self.boat,dict(self.boat,bid=2,name='다른호',port='무창포항'),dict(self.boat,bid=3,name='별칭',canonical_bid=1)]})
        selected=self.a.register_request(dict(self.p,bid=0,port='오천항'))
        self.assertEqual([r['bid'] for r in selected],[1])
        all_boats=self.a.register_request(dict(self.p,bid=0,port='*'))
        self.assertEqual({r['bid'] for r in all_boats},{1,2})
        self.assertEqual(len(self.a.state['subscriptions']),2)
        self.a.remove(dict(self.p,bid=0,port='오천항'))
        self.assertEqual([r['bid'] for r in self.a.state['subscriptions']],[2])
        self.a.remove(dict(self.p,bid=0,port='*'))
        self.assertEqual(self.a.state['subscriptions'],[])

    def test_rsa_round_trip_and_reject_invalid_ciphertext(self):
        pub=self.base+'/pub.pem'
        subprocess.run([OPENSSL,'pkey','-in',self.a.key,'-pubout','-out',pub],check=True,capture_output=True)
        result=subprocess.run([OPENSSL,'pkeyutl','-encrypt','-pubin','-inkey',pub,'-pkeyopt','rsa_padding_mode:oaep','-pkeyopt','rsa_oaep_md:sha256','-pkeyopt','rsa_mgf1_md:sha256'],input=json.dumps(self.p).encode(),capture_output=True,check=True)
        self.assertEqual(self.a.decrypt('fish1:'+base64.b64encode(result.stdout).decode()),self.p)
        with self.assertRaises(ValueError):self.a.decrypt('fish1:bad!')
    def test_alert_target_collection_does_not_change_regular_settings(self):
        self.a.register(self.p)
        with patch.object(mac_scheduler,'SETTINGS',self.base+'/settings.json'),patch.object(mac_scheduler,'RUNTIME',self.base+'/runtime.json'):
            s=mac_scheduler.Scheduler();s.alerts=self.a;s.paused=True;s.next_alerts=0
            self.assertEqual(s.due_mode(),'range');self.assertEqual(s.active_boat_ids,[1]);self.assertEqual(s.next_collection_kind,'alerts_auto')
            with patch.object(mac_scheduler.subprocess,'run') as run,patch.object(mac_scheduler,'atomic_json'):
                run.return_value.returncode=0;s.run_once('range')
                self.assertEqual(run.call_args.args[0][-2:],['--boat-ids','1'])
            self.assertEqual(s.state['last_collection_kind'],'alerts_auto');self.assertTrue(s.paused)
    def test_notice_and_cancellation_block_alert(self):
        self.a.register(self.p);self.result()
        self.write('site_health.json',{'sites':{'site':{'boat_ids':{'화니호':1},'boat_notices':{'화니호':'27년 시즌에 뵙겠습니다'}}}})
        with patch.object(self.a,'publish') as send:self.a.check();send.assert_not_called()
        self.assertEqual(self.a.state['subscriptions'][0]['last_status'],'maintenance')

if __name__=='__main__':unittest.main()
