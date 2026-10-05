import unittest
from unittest.mock import patch
from tests import test_ntfy_alerts as fixtures
from ntfy_alerts import Alerts,OPENSSL
class RelayTests(unittest.TestCase):
    setUp=fixtures.NtfyTests.setUp
    tearDown=fixtures.NtfyTests.tearDown
    write=fixtures.NtfyTests.write
    def test_relay_idempotent_and_no_ntfy_registration_message(self):
        rid='a'*32;p=dict(self.p,request_id=rid)
        with patch.object(self.a,'decrypt',return_value=p),patch.object(self.a,'validate_public_name'),patch.object(self.a,'publish') as send:
            self.assertTrue(self.a.process_relay_request(rid,'cipher')['ok'])
            self.assertTrue(self.a.process_relay_request(rid,'cipher')['ok'])
            self.assertEqual(len(self.a.state['subscriptions']),1);send.assert_not_called()
        restored=Alerts(self.base)
        with patch.object(restored,'decrypt',side_effect=AssertionError('duplicate decrypted')):
            self.assertTrue(restored.process_relay_request(rid,'cipher')['ok'])
    def test_relay_rejects_public_name_and_unsupported_admin(self):
        for action in ['add','admin_update']:
            rid=('a' if action=='add' else 'b')*32
            with patch.object(self.a,'decrypt',return_value=dict(self.p,request_id=rid,action=action,label='outside-user')):
                self.assertFalse(self.a.process_relay_request(rid,'cipher')['ok'])
        self.assertEqual(self.a.state['subscriptions'],[])
    def test_relay_cannot_remove_other_owner(self):
        self.a.register(self.p);rid='c'*32
        with patch.object(self.a,'decrypt',return_value=dict(self.p,request_id=rid,action='delete',owner='2'*32)):
            self.a.process_relay_request(rid,'cipher')
        self.assertEqual(len(self.a.state['subscriptions']),1)
    def test_stale_browser_group_update_recovers_owned_group(self):
        self.a.register_request(dict(self.p,ports=['*'],bids=[],group='a'*16))
        self.a.manage(dict(self.p,action='update',ports=['*'],bids=[],group='b'*16,min_seats=4,old=[0,self.ds,self.p['topic'],'*','c'*16]))
        self.assertEqual(len(self.a.state['subscriptions']),1)
        self.assertEqual(self.a.state['subscriptions'][0]['min_seats'],4)
        with self.assertRaisesRegex(ValueError,'찾지 못했습니다'):
            self.a.manage(dict(self.p,action='update',owner='2'*32,ports=['*'],bids=[],old=[0,self.ds,self.p['topic'],'*','c'*16]))
    def test_admin_edit_preserves_group_identity(self):
        rows=self.a.register_request(dict(self.p,ports=['*'],bids=[],group='a'*16))
        self.a.admin_update(dict(self.p,id=self.a.group_key(rows[0]),ports=['*'],bids=[],group='b'*16,min_seats=3))
        self.assertEqual(self.a.state['subscriptions'][0]['request_group'],'a'*16)
    def test_ambiguous_stale_group_cannot_edit_multiple_requests(self):
        self.write('boats.json',{'boats':[self.boat,dict(self.boat,bid=2,name='다른배')]})
        self.a.register_request(dict(self.p,ports=['*'],bids=[1],group='a'*16))
        self.a.register_request(dict(self.p,ports=['*'],bids=[2],group='b'*16))
        with self.assertRaisesRegex(ValueError,'찾지 못했습니다'):
            self.a.manage(dict(self.p,action='update',ports=['*'],bids=[],old=[0,self.ds,self.p['topic'],'*','c'*16]))
        self.assertEqual(len(self.a.state['subscriptions']),2)
    def test_owner_sync_uses_current_ports_and_hides_others(self):
        rows=self.a.register_request(dict(self.p,ports=['*'],bids=[],group='a'*16))
        self.a.register(dict(self.p,owner='2'*32,topic='fishing-user-'+('b'*16)))
        import base64,hashlib,hmac,json,subprocess
        key='d'*128;rid='e'*32
        with patch.object(self.a,'decrypt',return_value={'action':'list','owner':self.p['owner'],'request_id':rid,'response_key':key}):
            result=self.a.process_relay_request(rid,'cipher')
        self.assertTrue(result['ok']);self.assertNotIn(self.p['topic'],result['data'])
        iv,cipher,mac=[base64.b64decode(x) for x in result['data'].split('.')]
        secret=bytes.fromhex(key)
        self.assertEqual(mac,hmac.new(secret[32:],iv+cipher,hashlib.sha256).digest())
        raw=subprocess.run([OPENSSL,'enc','-d','-aes-256-cbc','-K',secret[:32].hex(),'-iv',iv.hex()],input=cipher,capture_output=True,check=True).stdout
        data=json.loads(raw);self.assertEqual(len(data),1);self.assertEqual(data[0]['ports'],['*']);self.assertEqual(data[0]['group'],'a'*16)
if __name__=='__main__':unittest.main()
