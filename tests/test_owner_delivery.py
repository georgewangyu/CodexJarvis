"""Actual dispatcher seams: an owner review is not manager-conversation polling."""
import json
import os
import time
from contextlib import contextmanager
import unittest
from unittest.mock import patch

import task_watch as w
import test_task_watch as fixtures


class OwnerDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.TrackingTests();self.f.setUp();self.addCleanup(self.f.tearDown)
        with w.database(self.f.root) as db:
            spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
            spec['delivery'].update(routing='owner_reviewed_v1',direct_to_root=True)
            db.execute('UPDATE policy SET spec=?',(w.encoded(spec),))

    def test_implicit_scope_excludes_managers_and_never_reads_their_chatter(self):
        with w.database(self.f.root) as db:
            spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
            self.assertEqual(w.observed_threads(db,spec),[fixtures.WORKER])
        with patch.object(self.f.reader,'head',wraps=self.f.reader.head) as read:
            with self.assertRaisesRegex(ValueError,'observation scope'):
                w.reconcile_task(self.f.root,self.f.reader,fixtures.HELPER)
            read.assert_not_called()

    def test_normal_dispatcher_requires_friday_review_even_with_old_direct_flag_and_age(self):
        generation=self.f.fresh('fresh-final',user='')
        with w.database(self.f.root) as db:
            db.execute('UPDATE fact SET created=? WHERE id=?',(time.time()-130,generation))
        sent=w.deliver_one(self.f.root,self.f.fake_queue)
        self.assertEqual(sent['recipient'],fixtures.HELPER)
        self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))
        self.assertFalse(any(n['recipient']==fixtures.ROOT for n in w.status(self.f.root)['notice']))
        self.f.ack(generation)
        self.assertEqual(w.deliver_one(self.f.root,self.f.fake_queue)['recipient'],fixtures.ROOT)
        self.f.ack(generation)
        for _ in range(3):self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))

    def test_concrete_blocked_and_needs_input_judgments_reach_root_once(self):
        for state in ('blocked','waiting_for_owner'):
            with self.subTest(state=state):
                generation=self.f.fresh(state,user='')
                self.assertEqual(w.deliver_one(self.f.root,self.f.fake_queue)['recipient'],fixtures.HELPER)
                self.f.ack(generation,outcome_kind='needs_human_input',task_state=state,
                           reason='Approved source input is missing',next_action='Human supplies exact input',
                           open_decisions=[{'key':state,'reason':'Exact missing source'}])
                self.assertEqual(w.deliver_one(self.f.root,self.f.fake_queue)['recipient'],fixtures.ROOT)
                self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))

    def test_routine_owner_judgment_does_not_turn_a_worker_final_into_root_broadcast(self):
        generation=self.f.fresh(user='');w.deliver_one(self.f.root,self.f.fake_queue)
        self.f.ack(generation,outcome_kind='other',task_state='working',reason='Routine progress only')
        self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))
        self.assertFalse(any(n['recipient']==fixtures.ROOT for n in w.status(self.f.root)['notice']))

    def test_newer_work_prevents_pending_or_late_review_of_old_result(self):
        generation=self.f.fresh('old-outcome',user='');w.deliver_one(self.f.root,self.f.fake_queue)
        self.f.ack(generation)
        self.f.fresh('new-work',final=None,status='inProgress',user='Revise this first')
        self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))
        packet=w.notice_path(self.f.root,generation,fixtures.ROOT)
        before=packet.read_bytes()
        self.assertEqual(self.f.ack(generation)['duplicate'],True)
        self.assertEqual(packet.read_bytes(),before)

    def test_existing_root_packet_without_owner_review_is_not_attempted(self):
        generation=self.f.fresh(user='')
        with w.database(self.f.root) as db:
            fact=db.execute('SELECT * FROM fact WHERE id=?',(generation,)).fetchone()
            w.enqueue(db,self.f.root,fact,fixtures.ROOT)
        w.deliver_one(self.f.root,self.f.fake_queue)
        self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))
        with w.database(self.f.root) as db:
            row=db.execute('SELECT * FROM notice WHERE fact=? AND recipient=?',(generation,fixtures.ROOT)).fetchone()
            self.assertIsNone(row['attempted'])

    def test_quiet_sentinel_never_becomes_a_root_notice(self):
        generation=self.f.fresh(final='<DONT_NOTIFY/>',user='')
        self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))
        self.assertFalse(any(n['recipient']==fixtures.ROOT for n in w.status(self.f.root)['notice']))

    def test_retained_root_owned_source_is_held_without_reassigning_or_notifying(self):
        with w.database(self.f.root) as db:
            db.execute('UPDATE task SET owner=? WHERE thread=?',(fixtures.ROOT,fixtures.WORKER))
            spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
            self.assertEqual(w.observed_threads(db,spec),[])
        with self.assertRaisesRegex(ValueError,'observation scope'):
            self.f.fresh('root-owned',user='')
        self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))
        with w.database(self.f.root) as db:
            self.assertEqual(db.execute('SELECT owner FROM task WHERE thread=?',(fixtures.WORKER,)).fetchone()[0],fixtures.ROOT)

    def test_owner_change_holds_old_fact_and_prior_owner_review(self):
        generation=self.f.fresh(user='');w.deliver_one(self.f.root,self.f.fake_queue)
        with w.database(self.f.root) as db:db.execute('UPDATE task SET owner=? WHERE thread=?',(fixtures.ROOT,fixtures.WORKER))
        self.f.ack(generation)
        self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))
        self.assertFalse(any(n['recipient']==fixtures.ROOT for n in w.status(self.f.root)['notice']))

    def test_new_unconfirmed_head_holds_older_root_result(self):
        generation=self.f.fresh(user='');w.deliver_one(self.f.root,self.f.fake_queue);self.f.ack(generation)
        self.f.reader.current.insert(0,{'id':'new-shadow','status':'interrupted','error':None,'items':[]})
        self.assertEqual(w.reconcile_task(self.f.root,self.f.reader,fixtures.WORKER)['held'],'unconfirmed_native_snapshot')
        self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))

    def test_historical_catchup_cannot_hide_current_reviewed_result(self):
        old=fixtures.turn('authorized-old',final='Old history')
        self.f.reader.current.append(old)
        with w.database(self.f.root) as db:
            spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0]);spec['catchup']=[{'thread':fixtures.WORKER,'turn':'authorized-old'}]
            db.execute('UPDATE policy SET spec=?',(w.encoded(spec),))
        current=self.f.fresh(user='');w.deliver_one(self.f.root,self.f.fake_queue);self.f.ack(current)
        w.catchup(self.f.root,self.f.reader,fixtures.WORKER,'authorized-old')
        self.assertEqual(w.deliver_one(self.f.root,self.f.fake_queue)['generation'],current)

    def test_new_generation_after_claim_is_held_before_external_queue_without_retry(self):
        generation=self.f.fresh(user='');w.deliver_one(self.f.root,self.f.fake_queue);self.f.ack(generation)
        original=w.database;calls=0
        @contextmanager
        def interposed(root):
            nonlocal calls
            calls+=1
            if calls==2:self.f.fresh('new-after-claim',final=None,status='inProgress',user='New work')
            with original(root) as db:yield db
        with patch.object(w,'database',interposed),patch('task_watch.subprocess.run') as queue:
            result=w.deliver_one(self.f.root)
            self.assertEqual(result['state'],'held_changed_after_claim');queue.assert_not_called()
        with original(self.f.root) as db:
            notice=db.execute('SELECT * FROM notice WHERE fact=? AND recipient=?',(generation,fixtures.ROOT)).fetchone()
            self.assertIsNotNone(notice['attempted'])
        self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))

    def test_blank_blocker_judgment_is_audit_only(self):
        generation=self.f.fresh(user='');w.deliver_one(self.f.root,self.f.fake_queue)
        self.f.ack(generation,outcome_kind='needs_human_input',task_state='blocked',
                   reason=' ',next_action=' ',evidence=[],open_decisions=[])
        self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))

    def test_current_structured_native_question_is_concrete_input_without_invented_decision(self):
        generation=self.f.fresh(final=None,status='inProgress',user='',questions=[{'title':'Which cut?'}])
        w.deliver_one(self.f.root,self.f.fake_queue)
        self.f.ack(generation,outcome_kind='needs_human_input',task_state='waiting_for_owner',
                   reason='The exact native question needs a selection',next_action='Answer the source question',open_decisions=[])
        self.assertEqual(w.deliver_one(self.f.root,self.f.fake_queue)['recipient'],fixtures.ROOT)

    def test_owner_review_during_temporary_error_or_gap_survives_verified_same_head_recovery(self):
        for field in ('error','gap'):
            with self.subTest(field=field):
                generation=self.f.fresh('recover-'+field,user='')
                w.deliver_one(self.f.root,self.f.fake_queue)
                with w.database(self.f.root) as db:db.execute('UPDATE task SET '+field+'=? WHERE thread=?',('temporary uncertainty',fixtures.WORKER))
                self.f.ack(generation)
                self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))
                with w.database(self.f.root) as db:
                    notice=db.execute('SELECT * FROM notice WHERE fact=? AND recipient=?',(generation,fixtures.ROOT)).fetchone()
                    self.assertIsNotNone(notice);self.assertIsNone(notice['attempted'])
                self.assertTrue(w.reconcile_task(self.f.root,self.f.reader,fixtures.WORKER)['quiet_head'])
                self.f.ack(generation)
                self.assertEqual(w.deliver_one(self.f.root,self.f.fake_queue)['recipient'],fixtures.ROOT)
                self.assertIsNone(w.deliver_one(self.f.root,self.f.fake_queue))


if __name__=='__main__':unittest.main()
