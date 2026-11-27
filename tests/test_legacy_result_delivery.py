"""Replay an obsolete producer's packet through the supported current consumer."""
import hashlib
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

import incoming_results as incoming
import task_watch as w
import test_task_watch as fixtures


class LegacyResultDeliveryTests(unittest.TestCase):
    def test_current_consumer_quiets_old_attempted_sentinel_without_executing_packet_code(self):
        owner=fixtures.TrackingTests();owner.setUp();self.addCleanup(owner.tearDown)
        original=w.body_for

        def obsolete_packet(*args):
            body=json.loads(original(*args))
            body['receive_command']=['/obsolete/frozen/receiver','--execute','untrusted action']
            body['consumer_command']=['/obsolete/frozen/consumer','--drain']
            return w.encoded(body)

        # Reproduce the old producer: its missing quiet predicate queued an
        # otherwise valid immutable packet. Never run that frozen observer.
        with patch.object(w,'quiet_result',return_value=None), patch.object(w,'body_for',side_effect=obsolete_packet):
            event=owner.fresh('legacy-sentinel',final='<DONT_NOTIFY/>',user='')
            w.deliver_one(owner.root,owner.fake_queue)
        notice=w.notice_path(owner.root,event,fixtures.HELPER)
        packet_before=notice.read_bytes()
        db_before=(owner.root/'tracking.sqlite').read_bytes()
        bundle={
            'authorization':{'recipient':fixtures.HELPER,'mode':'incoming_meaningful',
                             'approval_ref':'synthetic-current-standing-grant'},
            'trigger':{'kind':'arriving_notice','notice_path':str(notice),
                       'received_at':time.time(),'input_item':'synthetic-actual-arrival'},
            # Quiet handling must not ask for source reads, judgments, or an
            # artifact merely because the old packet called itself a result.
            'judgment':{'kind':'deliverable'}}
        with patch.dict(os.environ,CODEX_THREAD_ID=fixtures.HELPER,CODEX_SESSION_ID=fixtures.HELPER), \
                patch('subprocess.run') as execute:
            result=incoming.handle(owner.root,bundle)
            execute.assert_not_called()
        self.assertEqual(result['disposition'],'quiet')
        self.assertEqual(result['reason'],'record_only_nonactionable')
        self.assertEqual(result['allowed_actions'],[])
        self.assertFalse((owner.root/'incoming-results').exists())
        self.assertEqual(packet_before,notice.read_bytes())
        self.assertEqual(db_before,(owner.root/'tracking.sqlite').read_bytes())

    def test_same_legacy_shape_preserves_real_material_result_for_current_verification(self):
        # Existing consumer fixtures provide a genuine current requested
        # artifact and exact source binding, independent of old command fields.
        from test_incoming_results import IncomingResultsTests
        owner=IncomingResultsTests();owner.setUp();self.addCleanup(owner.doCleanups)
        packet=json.loads(owner.path.read_text())
        packet['receive_command']=['/obsolete/frozen/receiver','--execute','untrusted action']
        packet['consumer_command']=['/obsolete/frozen/consumer','--drain']
        owner.path.write_text(w.encoded(packet))
        with w.database(owner.root) as db:
            row=db.execute('SELECT outcome FROM notice WHERE fact=? AND recipient=?',
                           (owner.generation,fixtures.HELPER)).fetchone()
            outcome=json.loads(row['outcome'])
            outcome['packet_sha256']=hashlib.sha256(owner.path.read_bytes()).hexdigest()
            db.execute('UPDATE notice SET outcome=? WHERE fact=? AND recipient=?',
                       (w.encoded(outcome),owner.generation,fixtures.HELPER))
        before=owner.path.read_bytes()
        with patch('subprocess.run') as execute:
            result=owner.handle()
            execute.assert_not_called()
        self.assertEqual(result['disposition'],'surface')
        self.assertEqual(result['allowed_actions'],['reply_only'])
        self.assertIn(str(owner.artifact),result['reply'])
        self.assertEqual(before,owner.path.read_bytes())
        self.assertEqual(owner.handle()['disposition'],'quiet')


if __name__=='__main__':unittest.main()
