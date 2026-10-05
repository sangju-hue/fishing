import unittest
from unittest.mock import patch
from tests import test_ntfy_alerts as fixtures
from ntfy_alerts import Alerts
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
if __name__=='__main__':unittest.main()
