import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import task_watch as w
import test_task_watch as fixtures


class DeliveryUpgradeTests(unittest.TestCase):
    def test_pre_epoch_active_turn_final_becomes_eligible_only_after_full_terminal_confirmation(self):
        owner=fixtures.TrackingTests();owner.setUp();self.addCleanup(owner.tearDown)
        floor=time.time()
        with w.database(owner.root) as db:
            spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
            spec['delivery']['activation_floor']=floor;spec['delivery']['direct_to_root']=True
            db.execute('UPDATE policy SET spec=?',(w.encoded(spec),))
        native=fixtures.turn('pre-epoch-active',status='inProgress',user='',final='New artifact; review needed')
        native['startedAt']=floor-100;native['completedAt']=None
        owner.reader.current.insert(0,native)
        event=w.reconcile_task(owner.root,owner.reader,fixtures.WORKER)['events'][0]
        packet=w.notice_path(owner.root,event,fixtures.ROOT);original_packet=packet.read_bytes()
        with w.database(owner.root) as db:
            original=dict(db.execute('SELECT * FROM fact WHERE id=?',(event,)).fetchone())
        self.assertIsNone(w.deliver_one(owner.root,owner.fake_queue))
        native['status']='completed';native['completedAt']=time.time()
        result=w.reconcile_task(owner.root,owner.reader,fixtures.WORKER)
        self.assertNotIn('quiet_head',result);self.assertEqual(result['events'],[])
        with w.database(owner.root) as db:
            after=dict(db.execute('SELECT * FROM fact WHERE id=?',(event,)).fetchone())
            self.assertEqual(original,after)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fact_terminal').fetchone()[0],1)
            self.assertTrue(w.after_delivery_floor(after,floor,db))
        self.assertEqual(packet.read_bytes(),original_packet)
        self.assertEqual(w.deliver_one(owner.root,owner.fake_queue)['generation'],event)
        self.assertEqual(w.deliver_one(owner.root,owner.fake_queue)['recipient'],fixtures.ROOT)
        with patch.dict(os.environ,CODEX_THREAD_ID=fixtures.ROOT,CODEX_SESSION_ID=fixtures.ROOT):
            self.assertEqual(w.receive_notice_path(owner.root,packet)['disposition'],'recipient_verified')
        for _ in range(3):
            self.assertTrue(w.reconcile_task(owner.root,owner.reader,fixtures.WORKER)['quiet_head'])
            self.assertIsNone(w.deliver_one(owner.root,owner.fake_queue))

    def test_post_epoch_final_waits_for_terminal_proof_before_its_only_queue_attempt(self):
        owner=fixtures.TrackingTests();owner.setUp();self.addCleanup(owner.tearDown)
        floor=time.time()
        with w.database(owner.root) as db:
            spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0]);spec['delivery']['activation_floor']=floor
            db.execute('UPDATE policy SET spec=?',(w.encoded(spec),))
        native=fixtures.turn('new-active-final',status='inProgress',user='');native['startedAt']=floor+0.001
        owner.reader.current.insert(0,native)
        event=w.reconcile_task(owner.root,owner.reader,fixtures.WORKER)['events'][0]
        packet=w.notice_path(owner.root,event,fixtures.HELPER);before=packet.read_bytes()
        with patch('task_watch.subprocess.run') as execute:
            self.assertIsNone(w.deliver_one(owner.root));execute.assert_not_called()
        with w.database(owner.root) as db:
            notice=db.execute('SELECT * FROM notice WHERE fact=?',(event,)).fetchone()
            self.assertEqual(notice['state'],'pending');self.assertIsNone(notice['attempted'])
        native['status']='completed';native['completedAt']=time.time()
        self.assertEqual(w.reconcile_task(owner.root,owner.reader,fixtures.WORKER)['events'],[])
        self.assertEqual(w.deliver_one(owner.root,owner.fake_queue)['generation'],event)
        self.assertEqual(before,packet.read_bytes())
        for _ in range(3):self.assertIsNone(w.deliver_one(owner.root,owner.fake_queue))

    def test_current_active_questions_and_errors_still_dispatch_without_terminal_proof(self):
        for kind in ('question','error'):
            with self.subTest(kind=kind):
                owner=fixtures.TrackingTests();owner.setUp()
                try:
                    floor=time.time()
                    with w.database(owner.root) as db:
                        spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0]);spec['delivery']['activation_floor']=floor
                        db.execute('UPDATE policy SET spec=?',(w.encoded(spec),))
                    native=fixtures.turn('active-'+kind,status='inProgress',user='',questions=[{'title':'Which cut?'}] if kind=='question' else None)
                    native['startedAt']=floor+0.001
                    if kind=='error':native['error']={'message':'Concrete native error'}
                    owner.reader.current.insert(0,native)
                    event=w.reconcile_task(owner.root,owner.reader,fixtures.WORKER)['events'][0]
                    self.assertEqual(w.deliver_one(owner.root,owner.fake_queue)['generation'],event)
                finally:owner.tearDown()

    def test_repeated_links_and_aliases_hash_once_for_owner_and_root(self):
        owner=fixtures.TrackingTests();owner.setUp();self.addCleanup(owner.tearDown)
        workspace=owner.root.parent;artifact=workspace/'cut.mp4';artifact.write_bytes(b'v'*20)
        alias=workspace/'alias.mp4';alias.symlink_to(artifact)
        with w.database(owner.root) as db:
            spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
            spec.update(workspace=str(workspace));spec['delivery']['direct_to_root']=True
            db.execute('UPDATE policy SET spec=?',(w.encoded(spec),))
        text=' '.join(f'[cut](<{p}>)' for p in [artifact,artifact,alias,artifact,alias])
        with patch.object(w,'local_artifact',wraps=w.local_artifact) as hash_file:
            event=owner.fresh('same-artifact',final=text,user='')
            self.assertEqual(hash_file.call_count,1)
            with w.database(owner.root) as db:
                fact=db.execute('SELECT * FROM fact WHERE id=?',(event,)).fetchone()
                w.enqueue(db,owner.root,fact,fixtures.OTHER)
            self.assertEqual(hash_file.call_count,1)
        a=w.private_notice(owner.root,event,fixtures.HELPER)[1]
        b=w.private_notice(owner.root,event,fixtures.ROOT)[1]
        self.assertEqual(a['review_artifacts'],b['review_artifacts'])
        self.assertEqual(len(a['review_artifacts']),1)

    def test_total_capture_and_revalidation_bytes_are_bounded_across_distinct_outputs(self):
        owner=fixtures.TrackingTests();owner.setUp();self.addCleanup(owner.tearDown)
        workspace=owner.root.parent
        files=[workspace/(str(i)+'.mp4') for i in range(3)]
        for p in files:p.write_bytes(b'v'*8)
        with w.database(owner.root) as db:
            spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0]);spec['workspace']=str(workspace)
            db.execute('UPDATE policy SET spec=?',(w.encoded(spec),))
        text=' '.join(f'[cut](<{p}>)' for p in files)
        with patch.object(w,'ARTIFACT_TOTAL_BYTES',20):
            event=owner.fresh('budgeted-cuts',final=text,user='')
            captured=w.private_notice(owner.root,event,fixtures.HELPER)[1]['review_artifacts']
            self.assertEqual(len(captured),2);self.assertEqual(sum(a['size_bytes'] for a in captured),16)
            self.assertTrue(w.verify_artifacts(captured,str(workspace)))
            extra=w.local_artifact(str(files[2]),str(workspace))
            with patch.object(w,'local_artifact',wraps=w.local_artifact) as read:
                self.assertFalse(w.verify_artifacts(captured+[extra],str(workspace)))
                read.assert_not_called()

    def test_external_media_requires_explicit_trusted_root_and_uses_streamed_hash(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);workspace=root/'workspace';workspace.mkdir();media=root/'media';media.mkdir()
            video=media/'cut.mp4';video.write_bytes(b'video'*1000)
            policy={'roots':[str(media)],'max_bytes':10000}
            self.assertIsNone(w.local_artifact(str(video),str(workspace)))
            result=w.local_artifact(str(video),str(workspace),policy)
            self.assertEqual(result['sha256'],hashlib.sha256(video.read_bytes()).hexdigest())
            self.assertEqual(result['size_bytes'],5000)
            for bad in [{'roots':['/'],'max_bytes':10000},{'roots':[str(media)],'max_bytes':True},
                        {'roots':[str(media)],'max_bytes':1024},
                        {'roots':[str(media)],'max_bytes':513*1024*1024}]:
                self.assertIsNone(w.local_artifact(str(video),str(workspace),bad))
            outside=root/'outside.mp4';outside.write_bytes(b'outside')
            (media/'escape.mp4').symlink_to(outside)
            self.assertIsNone(w.local_artifact(str(media/'escape.mp4'),str(workspace),policy))

    def test_activation_floor_preserves_old_pending_and_attempts_only_new_fact(self):
        owner=fixtures.TrackingTests();owner.setUp();self.addCleanup(owner.tearDown)
        old=owner.fresh('old-pending',final='Old review available',user='')
        now=time.time()
        with w.database(owner.root) as db:
            spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
            spec['delivery']['activation_floor']=now
            db.execute('UPDATE policy SET spec=?',(w.encoded(spec),))
        with patch('task_watch.subprocess.run') as execute:
            self.assertIsNone(w.deliver_one(owner.root));execute.assert_not_called()
        native=fixtures.turn('post-upgrade',final='New review available',user='')
        native['completedAt']=time.time();owner.reader.current.insert(0,native)
        new=w.reconcile_task(owner.root,owner.reader,fixtures.WORKER)['events'][0]
        result=w.deliver_one(owner.root,owner.fake_queue)
        self.assertEqual(result['generation'],new)
        with w.database(owner.root) as db:
            row=db.execute('SELECT state,attempted FROM notice WHERE fact=?',(old,)).fetchone()
            self.assertEqual(row['state'],'pending');self.assertIsNone(row['attempted'])
            self.assertFalse(db.execute('SELECT 1 FROM notice WHERE fact=? AND recipient=?',
                                       (old,fixtures.ROOT)).fetchone())

    def test_old_final_captured_after_upgrade_is_not_historical_replay(self):
        owner=fixtures.TrackingTests();owner.setUp();self.addCleanup(owner.tearDown)
        floor=time.time()
        with w.database(owner.root) as db:
            spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0]);spec['delivery']['activation_floor']=floor
            db.execute('UPDATE policy SET spec=?',(w.encoded(spec),))
        native=fixtures.turn('old-final-late-capture',user='');native['completedAt']=floor-100
        owner.reader.current.insert(0,native)
        event=w.reconcile_task(owner.root,owner.reader,fixtures.WORKER)['events'][0]
        with patch('task_watch.subprocess.run') as execute:
            self.assertIsNone(w.deliver_one(owner.root));execute.assert_not_called()
        self.assertTrue(event)

    def test_retained_unselected_task_is_not_observed_or_delivered(self):
        owner=fixtures.TrackingTests();owner.setUp();self.addCleanup(owner.tearDown)
        owner.fresh('retained-but-unselected',user='')
        with w.database(owner.root) as db:
            spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0]);spec['observation_threads']=[fixtures.HELPER]
            db.execute('UPDATE policy SET spec=?',(w.encoded(spec),))
        with self.assertRaises(ValueError):w.reconcile_task(owner.root,owner.reader,fixtures.WORKER)
        with patch('task_watch.subprocess.run') as execute:
            self.assertIsNone(w.deliver_one(owner.root));execute.assert_not_called()

    def test_already_queued_old_notice_is_quiet_after_epoch_change_without_rewrite(self):
        owner=fixtures.TrackingTests();owner.setUp();self.addCleanup(owner.tearDown)
        old=owner.fresh('old-queued',user='');w.deliver_one(owner.root,owner.fake_queue)
        packet=w.notice_path(owner.root,old,fixtures.HELPER);before=packet.read_bytes()
        with w.database(owner.root) as db:
            spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0]);spec['delivery']['activation_floor']=time.time()
            db.execute('UPDATE policy SET spec=?',(w.encoded(spec),))
        with patch.dict(os.environ,CODEX_THREAD_ID=fixtures.HELPER,CODEX_SESSION_ID=fixtures.HELPER):
            self.assertEqual(w.receive_notice_path(owner.root,packet)['disposition'],'record_only_pre_activation')
        self.assertEqual(before,packet.read_bytes())

    def test_invalid_floor_holds_before_any_queue_attempt(self):
        owner=fixtures.TrackingTests();owner.setUp();self.addCleanup(owner.tearDown)
        owner.fresh('new',user='')
        for value in (True,-1,time.time()+1000,float('nan')):
            with w.database(owner.root) as db:
                spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0]);spec['delivery']['activation_floor']=value
                db.execute('UPDATE policy SET spec=?',(w.encoded(spec),))
            with patch('task_watch.subprocess.run') as execute:
                with self.assertRaises(ValueError):w.deliver_one(owner.root)
                execute.assert_not_called()

    def test_new_active_ui_question_does_not_need_a_completed_turn(self):
        floor=time.time()
        native=fixtures.turn('new-ui-choice',final='Which title?',status='inProgress',
                             questions=[{'title':'Which title?'}])
        native['startedAt']=floor+1
        fact={'created':floor+2,'source':w.encoded(w.source(native))}
        self.assertTrue(w.after_delivery_floor(fact,floor))
        native['startedAt']=floor-1;fact['source']=w.encoded(w.source(native))
        self.assertFalse(w.after_delivery_floor(fact,floor))

    def test_final_item_before_terminal_metadata_is_not_permanently_lost(self):
        floor=time.time();native=fixtures.turn('new-turn-final',status='inProgress')
        native['startedAt']=floor+1
        fact={'created':floor+2,'source':w.encoded(w.source(native))}
        self.assertTrue(w.after_delivery_floor(fact,floor))

    def test_explicit_direct_root_routing_preserves_owner_without_fallback_delay(self):
        owner=fixtures.TrackingTests();owner.setUp();self.addCleanup(owner.tearDown)
        with w.database(owner.root) as db:
            spec=json.loads(db.execute('SELECT spec FROM policy').fetchone()[0]);spec['delivery']['direct_to_root']=True
            db.execute('UPDATE policy SET spec=?',(w.encoded(spec),))
        generation=owner.fresh('new-material-direct-root',user='')
        with w.database(owner.root) as db:
            fact=db.execute('SELECT * FROM fact WHERE id=?',(generation,)).fetchone()
            self.assertEqual(fact['owner'],fixtures.HELPER)
            self.assertEqual({n['recipient'] for n in db.execute('SELECT * FROM notice WHERE fact=?',(generation,))},
                             {fixtures.HELPER,fixtures.ROOT})


if __name__=='__main__':unittest.main()
