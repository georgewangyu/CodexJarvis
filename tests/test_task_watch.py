import json
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import tempfile
import time
import threading
import unittest
from unittest.mock import patch
import uuid

import task_watch as w


ROOT, HELPER, WORKER, OTHER = [str(uuid.uuid4()) for _ in range(4)]
FREEFORM_OWNERSHIP = ('Added worker `' + WORKER + '` to the Runtime logical scope for on-request coordination: '
    'desktop ghost-browser diagnosis and minimally disruptive repair.\n\n'
    'Preserve apps and render jobs; no WindowServer kill or logout. '
    'Automatic reporting enrollment remains unproved. No duplicate dispatch or polling started.')
OWNERSHIP_ONLY = json.dumps({'type':'ownership_only','worker':WORKER,'workstream':'video'})


def turn(key, final='A review draft', status='completed', user='Please show a draft first', questions=None):
    items = [{'type': 'userMessage', 'id': 'u-' + key,
              'content': [{'type': 'text', 'text': user}]}]
    if final is not None:
        items.append({'type': 'agentMessage', 'id': 'a-' + key,
                      'phase': 'final_answer', 'text': final})
    if questions:
        items.append({'type': 'agentMessage', 'id': 'q-' + key, 'phase': 'commentary',
                      'text': '', 'questions': questions})
    return {'id': key, 'status': status, 'error': None, 'items': items}


class FakeReader:
    def __init__(self, turns):
        self.current = turns

    def call(self, method, params):
        return {'thread': {'id': params['threadId'], 'cwd': '/synthetic/workspace',
                           'status': {'type': 'notLoaded'}}}

    def turns(self, thread, cursor=None, limit=20):
        offset = int(cursor or 0)
        end = offset + limit
        return {'data': self.current[offset:end],
                'nextCursor': str(end) if end < len(self.current) else None}

    def head(self, thread):
        return {'data': self.current[:1]}


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / 'tracking'
        self.spec = {'authorization': 'persistent_until_revoked', 'root_thread': ROOT,
            'delivery': {'mode': 'explicit_internal', 'approval_ref': 'synthetic-test-approval'},
            'project_id': str(uuid.uuid4()),
            'workspace': '/synthetic/workspace', 'binary': '/synthetic/codex', 'interval': 30,
            'helpers': [{'thread': HELPER, 'workstream': 'video', 'name': 'Creator'}],
            'tasks': [{'thread': WORKER, 'owner': HELPER, 'charter': 'An enduring video task'},
                      {'thread': HELPER, 'owner': ROOT, 'charter': 'Creator coordination'}],
            'catchup': [{'thread': WORKER, 'turn': 'authorized-old-draft'}]}
        w.initialize(self.root, self.spec)
        self.reader = FakeReader([turn('old-final')])
        w.reconcile_task(self.root, self.reader, WORKER)

    def tearDown(self):
        self.temp.cleanup()

    def fresh(self, key='new-human-followup', **kwargs):
        self.reader.current.insert(0, turn(key, **kwargs))
        return w.reconcile_task(self.root, self.reader, WORKER)['events'][0]

    def helper_final(self, final=OWNERSHIP_ONLY, user=None, questions=None):
        reader = FakeReader([turn('helper-baseline')])
        w.reconcile_task(self.root, reader, HELPER)
        reader.current.insert(0, turn('helper-ownership', final=final,
            user='' if user is None else user, questions=questions))
        return w.reconcile_task(self.root, reader, HELPER)['events'][0], reader

    def test_sentinel_result_is_quiet_for_direct_and_managed_workers(self):
        before = next(s for s in w.status(self.root)['task_state'] if s['thread'] == WORKER)
        for owner in (HELPER, ROOT):
            with w.database(self.root) as db:
                db.execute('UPDATE task SET owner=? WHERE thread=?', (owner, WORKER))
            event = self.fresh('quiet-' + owner, final='  <DONT_NOTIFY/>\n', user='')
            state = w.status(self.root)
            notice = next(n for n in state['notice'] if n['fact'] == event)
            self.assertEqual(notice['state'], 'suppressed_nonactionable')
            self.assertIsNone(notice['attempted'])
            self.assertTrue(Path(next(f for f in state['fact'] if f['id'] == event)['path']).is_file())
            self.assertEqual(before, next(s for s in state['task_state'] if s['thread'] == WORKER))
        with patch('task_watch.subprocess.run') as queue:
            self.assertIsNone(w.deliver_one(self.root))
            queue.assert_not_called()

    def test_legacy_sentinel_other_unknown_ack_does_not_relay_result_or_reset_state(self):
        # Simulate a pre-fix attempted manager notice, as in the reproduced incident.
        with patch.object(w, 'quiet_result', return_value=None):
            event = self.fresh(final='<DONT_NOTIFY/>', user='')
            w.deliver_one(self.root, self.fake_queue)
        with w.database(self.root) as db:
            db.execute("UPDATE task_state SET state='working',reason='Retained human work' WHERE thread=?", (WORKER,))
        result = self.ack(event, outcome_kind='other', task_state='unknown',
                          reason='Sentinel only', next_action='Preserve; no wake or polling')
        state = w.status(self.root)
        upstream = next(n for n in state['notice'] if n['fact'] == event and n['recipient'] == ROOT)
        self.assertEqual(upstream['state'], 'suppressed_nonactionable')
        self.assertIsNone(upstream['attempted'])
        self.assertEqual(next(s for s in state['task_state'] if s['thread'] == WORKER)['state'], 'working')
        self.assertEqual(result['completion_handling']['action'], 'preserve')
        with patch.dict(os.environ, CODEX_THREAD_ID=HELPER, CODEX_SESSION_ID=HELPER):
            received = w.receive_notice_path(self.root, w.notice_path(self.root, event, HELPER))
        self.assertEqual(received['disposition'], 'record_only_nonactionable')
        self.assertNotIn('notice', received)
        with patch('task_watch.subprocess.run') as queue:
            self.assertIsNone(w.deliver_one(self.root))
            queue.assert_not_called()

    def test_legacy_unattempted_sentinel_is_rechecked_without_rewriting_packet(self):
        with patch.object(w, 'quiet_result', return_value=None):
            event = self.fresh(final='<DONT_NOTIFY/>')
        path = w.notice_path(self.root, event, HELPER)
        original = path.read_bytes()
        with patch('task_watch.subprocess.run') as queue:
            self.assertEqual(w.deliver_one(self.root)['state'], 'suppressed_nonactionable')
            queue.assert_not_called()
        self.assertEqual(path.read_bytes(), original)

    def test_sentinel_does_not_hide_material_text_structured_question_or_failure(self):
        for i, changes in enumerate([
                {'final': '<DONT_NOTIFY/>\nArtifact delivered: /synthetic/result.txt'},
                {'final': '<DONT_NOTIFY/>', 'questions': [{'question': 'Which result should I use?'}]},
                {'final': '<DONT_NOTIFY/>', 'status': 'failed',
                 'error': {'message': 'Actual failure'}, 'completedAt': 100},
                {'final': '<DONT_NOTIFY/>', 'status': 'interrupted',
                 'error': {'message': 'Actual interruption'}, 'completedAt': 100}]):
            extra = {k: changes.pop(k) for k in ('error', 'completedAt') if k in changes}
            value = turn('substantive-' + str(i), **changes); value.update(extra)
            self.reader.current.insert(0, value)
            event = w.reconcile_task(self.root, self.reader, WORKER)['events'][0]
            self.assertEqual(next(n for n in w.status(self.root)['notice'] if n['fact'] == event)['state'], 'pending')

    def test_real_owner_blocker_survives_quiet_source_at_manager_relay(self):
        with patch.object(w, 'quiet_result', return_value=None):
            event = self.fresh(final='<DONT_NOTIFY/>')
            w.deliver_one(self.root, self.fake_queue)
        self.ack(event, outcome_kind='needs_human_input', task_state='blocked',
                 reason='Owner found a missing approved input', next_action='Human supplies input',
                 open_decisions=[{'key': 'input', 'reason': 'Actual owner blocker'}])
        notice = next(n for n in w.status(self.root)['notice'] if n['fact'] == event and n['recipient'] == ROOT)
        self.assertEqual(notice['state'], 'pending')

    def test_sentinel_does_not_resolve_an_existing_same_turn_failure(self):
        error = self.confirmed_error()
        self.reader.current[0].update(status='completed', error=None,
            items=[{'type':'agentMessage','id':'sentinel-final','phase':'final_answer','text':'<DONT_NOTIFY/>'}])
        self.assertEqual(len(w.reconcile_task(self.root, self.reader, WORKER)['events']), 1)
        state = w.status(self.root)
        self.assertEqual(next(n for n in state['notice'] if n['fact']==error)['state'], 'pending')
        self.assertEqual(w.deliver_one(self.root, self.fake_queue)['generation'], error)

    def test_empty_source_result_is_quiet_but_current_human_request_is_retained(self):
        event = self.fresh(final='  ', user='Please continue the requested implementation')
        self.assertEqual(w.status(self.root)['notice'], [])
        fact = next(f for f in w.status(self.root)['fact'] if f['id'] == event)
        self.assertIn('Please continue', fact['source'])

    def test_ownership_only_notification_is_recorded_quietly_without_state_change_or_duplicate(self):
        before = next(s for s in w.status(self.root)['task_state'] if s['thread'] == HELPER)
        event, reader = self.helper_final(user='')
        state = w.status(self.root)
        notice = next(n for n in state['notice'] if n['fact'] == event)
        self.assertEqual(notice['state'], 'suppressed_ownership_only')
        self.assertIsNone(notice['attempted'])
        self.assertTrue(Path(next(f for f in state['fact'] if f['id'] == event)['path']).is_file())
        self.assertEqual(before, next(s for s in state['task_state'] if s['thread'] == HELPER))
        self.assertEqual(w.reconcile_task(self.root, reader, HELPER)['events'], [])
        with patch('task_watch.subprocess.run') as queue:
            self.assertIsNone(w.deliver_one(self.root))
            queue.assert_not_called()
        self.assertEqual(len(w.status(self.root)['notice']), 1)

    def test_ownership_words_do_not_hide_material_result_question_or_real_user_request(self):
        for final, user, questions in [
            (OWNERSHIP_ONLY + '\n\nPreview ready: [Review](/synthetic/output.mp4)', None, None),
            (OWNERSHIP_ONLY + '\n\nApproval required: choose a repair.', None, None),
            (FREEFORM_OWNERSHIP.replace('minimally disruptive repair.', 'repair; found a new fault.'), None, None),
            (OWNERSHIP_ONLY, 'Produce the actual repair report', None),
            (OWNERSHIP_ONLY, None, [{'question': 'Approve the next step?'}])]:
            with self.subTest(final=final[-35:], user=user, questions=questions):
                # Independent fixture to avoid baseline/notice state carrying across cases.
                other = TrackingTests(); other.setUp()
                try:
                    event, _ = other.helper_final(final, user, questions)
                    notice = next(n for n in w.status(other.root)['notice'] if n['fact'] == event)
                    self.assertEqual(notice['state'], 'pending')
                finally:
                    other.tearDown()
        event = self.fresh(final=OWNERSHIP_ONLY, user='Record logical scope ownership')
        self.assertEqual(next(n for n in w.status(self.root)['notice'] if n['fact'] == event)['state'], 'pending')

    def test_ownership_acknowledgment_cannot_hide_a_new_prerequisite(self):
        cases = ['Accepted ownership in logical scope, but waiting for the signed contract.',
                 FREEFORM_OWNERSHIP.replace('minimally disruptive repair.', 'repair, waiting for a signed contract.'),
                 FREEFORM_OWNERSHIP.replace('minimally disruptive repair.', 'repair pending a signed contract.'),
                 FREEFORM_OWNERSHIP.replace('Preserve apps and render jobs;', 'Preserve apps, waiting for a signed contract;')]
        for final in cases:
            other=TrackingTests();other.setUp()
            try:
                event,_=other.helper_final(final=final)
                notice=next(n for n in w.status(other.root)['notice'] if n['fact']==event)
                self.assertEqual(notice['state'],'pending',final)
            finally:
                other.tearDown()

    def test_ownership_protocol_never_infers_nonactionability_from_free_prose(self):
        metadata=json.loads(OWNERSHIP_ONLY)
        cases=[FREEFORM_OWNERSHIP,
               FREEFORM_OWNERSHIP.replace('minimally disruptive repair.', 'signed contract outstanding.'),
               json.dumps(dict(metadata,note='Signed contract outstanding')),
               json.dumps(dict(metadata,workstream='wrong-workstream')),
               json.dumps(dict(metadata,worker='signed contract outstanding')),
               OWNERSHIP_ONLY+'\nSigned contract outstanding.',
               '```json\n'+OWNERSHIP_ONLY+'\n```',
               OWNERSHIP_ONLY[:-1]+', "type":"ownership_only"}',
               '[]']
        for final in cases:
            other=TrackingTests();other.setUp()
            try:
                event,_=other.helper_final(final=final)
                self.assertEqual(next(n for n in w.status(other.root)['notice'] if n['fact']==event)['state'],
                                 'pending',final)
            finally:
                other.tearDown()

    def test_metadata_ack_does_not_hide_a_mixed_human_deliverable_request(self):
        event,_=self.helper_final(user='Accept ownership and produce the required artifact for this logical scope')
        self.assertEqual(next(n for n in w.status(self.root)['notice'] if n['fact']==event)['state'],'pending')

    def test_same_exact_metadata_request_can_be_acknowledged_quietly(self):
        event,_=self.helper_final(user=OWNERSHIP_ONLY)
        self.assertEqual(next(n for n in w.status(self.root)['notice'] if n['fact']==event)['state'],
                         'suppressed_ownership_only')

    def test_legacy_unattempted_ownership_notice_is_suppressed_but_attempted_history_is_sticky(self):
        with patch.object(w, 'ownership_only', return_value=False):
            event, _ = self.helper_final()
        path = w.notice_path(self.root, event, ROOT)
        original = path.read_bytes()
        with patch('task_watch.subprocess.run') as queue:
            self.assertEqual(w.deliver_one(self.root)['state'], 'suppressed_ownership_only')
            queue.assert_not_called()
        self.assertEqual(path.read_bytes(), original)
        with w.database(self.root) as db:
            db.execute("UPDATE notice SET state='accepted_unverified',attempted=? WHERE fact=?", (time.time(), event))
        self.assertIsNone(w.deliver_one(self.root))
        self.assertEqual(w.status(self.root)['notice'][0]['state'], 'accepted_unverified')
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            received = w.receive_notice_path(self.root, path)
        self.assertEqual(received['disposition'], 'record_only_ownership')
        self.assertNotIn('notice', received)

    def test_completed_oneoff_returns_owner_parking_review_on_first_and_duplicate_ack(self):
        event = self.fresh(final='Repair worked after hide/show; human confirmed apps and tabs intact.')
        w.deliver_one(self.root, self.fake_queue)
        result = self.ack(event, task_state='completed', completion_evidence=['Human confirmed repair after restoring app'])
        self.assertEqual(result['completion_handling']['action'], 'review_references_parking')
        again = self.ack(event, task_state='completed', completion_evidence=['Human confirmed repair after restoring app'])
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['completion_handling'], result['completion_handling'])
        self.fresh('newer-user-request', final=None, status='inProgress', user='Revise it before closing')
        resumed = self.ack(event, task_state='completed', completion_evidence=['Human confirmed repair after restoring app'])
        self.assertEqual(resumed['completion_handling']['action'], 'preserve')
        self.assertEqual(next(s for s in w.status(self.root)['task_state'] if s['thread'] == WORKER)['state'], 'working')

    def test_review_pending_and_newer_turn_preserve_location_in_completion_handling(self):
        event = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        self.assertEqual(self.ack(event)['completion_handling']['action'], 'preserve')
        newer = self.fresh('newer-user-revision', final=None, status='inProgress', user='Actually revise this first')
        old = self.ack(event)
        self.assertEqual(old['completion_handling']['action'], 'preserve')
        self.assertNotEqual(newer, event)

    def test_manager_completion_never_returns_a_parking_candidate(self):
        event, _ = self.helper_final(final='The bounded coordination review is delivered.')
        w.deliver_one(self.root, self.fake_queue)
        value = {'generation': event, 'outcome_kind': 'other', 'task_state': 'completed',
                 'reason': 'Bounded turn complete', 'next_action': 'Preserve durable manager',
                 'completion_evidence': ['Exact bounded coordination result'], 'open_decisions': []}
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            result = w.acknowledge(self.root, value)
        self.assertEqual(result['completion_handling']['action'], 'preserve')

    def fake_queue(self, *args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, 'accepted', '')

    def ack(self, generation, **kwargs):
        value = dict(generation=generation, outcome_kind='draft_ready', task_state='review_ready',
                     reason='Exact source contains a draft', next_action='Human reviews exact draft',
                     evidence=['native://source'], open_decisions=[])
        value.update(kwargs)
        with patch.dict(os.environ, CODEX_THREAD_ID=HELPER, CODEX_SESSION_ID=HELPER):
            return w.acknowledge(self.root, value)

    def placement(self, helper=HELPER, members=None):
        members = [WORKER] if members is None else members
        section = str(uuid.uuid4())
        return {'authorization_evidence': 'synthetic-current-human-redistribution',
            'placement': {'surface': 'codex_desktop_sidebar', 'host_id': 'local',
            'thread_id': helper, 'section_id': section, 'observed_at': time.time(),
            'inventory_section': {'sectionId': section, 'name': 'Synthetic workstream',
                'itemKeys': ['codex:thread:local:' + t for t in [helper] + members]}},
            'expected_owners': {m: HELPER for m in members},
            'official_evidence': {'placement': 'official-exact-move', 'inventory': 'official-fresh-inventory',
                'charter_acceptance': 'official-charter-acceptance'}}

    def test_baseline_does_not_replay_final(self):
        self.assertEqual(w.status(self.root)['fact'], [])
        self.assertEqual(w.reconcile_task(self.root, self.reader, WORKER)['events'], [])

    def confirmed_error(self, key='recovering-turn'):
        value = turn(key, final=None, status='failed')
        value.update(error={'message': 'Synthetic confirmed error'}, completedAt=100)
        self.reader.current.insert(0, value)
        return w.reconcile_task(self.root, self.reader, WORKER)['events'][0]

    def test_pending_same_turn_error_is_superseded_by_final_before_queue(self):
        failure = self.confirmed_error()
        before = next(n for n in w.status(self.root)['notice'] if n['fact'] == failure)
        self.reader.current[0] = turn('recovering-turn', final='Successful final')
        w.reconcile_task(self.root, self.reader, WORKER)
        calls = []
        def capture(args, **kwargs):
            calls.append(args[-1])
            return self.fake_queue(args, **kwargs)
        w.deliver_one(self.root, runner=capture)
        old = next(n for n in w.status(self.root)['notice'] if n['fact'] == failure)
        self.assertEqual(old['state'], 'superseded_before_attempt')
        self.assertIsNone(old['attempted'])
        self.assertEqual(old['body'], before['body'])
        self.assertEqual(len(calls), 1)
        self.assertNotIn('turn error', calls[0])

    def test_attempted_error_history_is_preserved_after_recovery(self):
        failure = self.confirmed_error()
        w.deliver_one(self.root, runner=self.fake_queue)
        before = next(n for n in w.status(self.root)['notice'] if n['fact'] == failure)
        self.reader.current[0] = turn('recovering-turn', final='Successful final')
        w.reconcile_task(self.root, self.reader, WORKER)
        after = next(n for n in w.status(self.root)['notice'] if n['fact'] == failure)
        self.assertEqual(after, before)

    def test_root_fallback_does_not_resurrect_superseded_error(self):
        failure = self.confirmed_error()
        self.reader.current[0] = turn('recovering-turn', final='Successful final')
        w.reconcile_task(self.root, self.reader, WORKER)
        with w.database(self.root) as db:
            db.execute('UPDATE fact SET created=?', (time.time()-130,))
        w.deliver_one(self.root, runner=self.fake_queue)
        self.assertFalse(any(n['fact'] == failure and n['recipient'] == ROOT
                             for n in w.status(self.root)['notice']))

    def deferred_floor(self):
        shadow = {'id': 'active-floor', 'status': 'interrupted', 'error': None, 'items': []}
        value = w.source(shadow)
        with w.database(self.root) as db:
            spec = json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
            task = next(t for t in spec['tasks'] if t['thread'] == WORKER)
            task['active_baseline_floor'] = {'turn': shadow['id'], 'source_sha256': w.digest(value),
                                             'official_evidence': 'synthetic official active read'}
            db.execute('UPDATE policy SET spec=?', (w.encoded(spec),))
            db.execute('UPDATE task SET baseline=?,watermark=?,checked=NULL WHERE thread=?',
                       (w.encoded([value]), shadow['id'], WORKER))
        self.reader.current = [shadow]
        return shadow

    def test_newer_usable_turn_crosses_only_exact_authorized_shadow_floor(self):
        shadow = self.deferred_floor()
        self.assertEqual(w.reconcile_task(self.root, self.reader, WORKER)['held'], 'unconfirmed_native_snapshot')
        row = next(t for t in w.status(self.root)['task'] if t['thread'] == WORKER)
        self.assertIsNone(row['checked'])
        self.reader.current = [turn('newer-success'), shadow]
        self.assertEqual(len(w.reconcile_task(self.root, self.reader, WORKER)['events']), 1)
        self.assertEqual(w.reconcile_task(self.root, self.reader, WORKER)['events'], [])
        row = next(t for t in w.status(self.root)['task'] if t['thread'] == WORKER)
        self.assertEqual(row['watermark'], 'newer-success')
        self.assertEqual(len(w.status(self.root)['fact']), 1)

    def test_changed_or_new_unconfirmed_shadow_still_holds_entire_batch(self):
        shadow = self.deferred_floor()
        changed = {**shadow, 'items': [{'type': 'userMessage', 'id': 'changed',
                    'content': [{'type': 'text', 'text': 'New request'}]}]}
        self.reader.current = [turn('newer-success'), changed]
        self.assertEqual(w.reconcile_task(self.root, self.reader, WORKER)['held'], 'unconfirmed_native_snapshot')
        self.assertEqual(w.status(self.root)['fact'], [])
        self.reader.current = [turn('newer-success'), {**shadow, 'id': 'unapproved-shadow'}, shadow]
        self.assertEqual(w.reconcile_task(self.root, self.reader, WORKER)['held'], 'unconfirmed_native_snapshot')
        self.assertEqual(w.status(self.root)['fact'], [])

    def test_direct_human_followup_after_final_and_restart(self):
        generation = self.fresh()
        again = w.reconcile_task(self.root, FakeReader(self.reader.current), WORKER)
        self.assertEqual(again['events'], [])
        self.assertEqual(len(w.status(self.root)['fact']), 1)
        self.assertEqual(len(w.status(self.root)['notice']), 1)

    def test_changed_payload_creates_new_revision(self):
        first = self.fresh()
        self.reader.current[0]['items'][-1]['text'] = 'Revised review draft'
        second = w.reconcile_task(self.root, self.reader, WORKER)['events'][0]
        self.assertNotEqual(first, second)
        self.assertEqual(len(w.status(self.root)['fact']), 2)

    def test_native_status_alone_does_not_duplicate_final(self):
        first = self.fresh(status='inProgress')
        self.reader.current[0]['status'] = 'completed'
        self.assertEqual(w.reconcile_task(self.root, self.reader, WORKER)['events'], [])
        self.assertEqual(len(w.status(self.root)['fact']), 1)

    def test_baseline_in_progress_later_final_is_new(self):
        self.reader.current = [turn('old-final', final=None, status='inProgress')]
        # Fresh policy to baseline this unfinished native turn.
        other = Path(self.temp.name).resolve() / 'other'
        w.initialize(other, self.spec)
        w.reconcile_task(other, self.reader, WORKER)
        self.reader.current[0] = turn('old-final', final='Now a draft')
        self.assertEqual(len(w.reconcile_task(other, self.reader, WORKER)['events']), 1)

    def test_multiple_quick_followups_preserved_in_order(self):
        self.reader.current = [turn(str(i)) for i in range(22)] + self.reader.current
        events = w.reconcile_task(self.root, self.reader, WORKER)['events']
        self.assertEqual(len(events), 22)
        self.assertEqual(w.status(self.root)['fact'][0]['turn'], '21')

    def test_history_gap_holds_watermark_without_partial_drain(self):
        self.reader.current = [turn(str(i)) for i in range(65)] + self.reader.current
        self.assertTrue(w.reconcile_task(self.root, self.reader, WORKER)['gap'])
        data = w.status(self.root)
        self.assertEqual(data['fact'], [])
        self.assertEqual(data['task'][0]['watermark'], 'old-final')

    def test_explicit_exact_turn_catchup_only(self):
        self.reader.current += [turn('authorized-old-draft')]
        event = w.catchup(self.root, self.reader, WORKER, 'authorized-old-draft')
        self.assertEqual(w.status(self.root)['fact'][0]['id'], event)
        with self.assertRaisesRegex(ValueError, 'not explicitly'):
            w.catchup(self.root, self.reader, WORKER, 'old-final')

    def test_input_then_final_distinct_never_objective_complete(self):
        first = self.fresh(final=None, status='inProgress', questions=[{'title': 'Review now?'}])
        self.reader.current[0] = turn('new-human-followup')
        second = w.reconcile_task(self.root, self.reader, WORKER)['events'][0]
        self.assertNotEqual(first, second)
        facts = [json.loads(x['source']) for x in w.status(self.root)['fact']]
        self.assertEqual([v['phase'] for v in facts], ['needs_input', 'reported_outcome'])
        self.assertTrue(all(not v['objective_complete'] for v in facts))

    def test_queue_no_resume_and_acceptance_not_ack(self):
        event = self.fresh()
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return self.fake_queue(argv, **kwargs)
        result = w.deliver_one(self.root, runner)
        self.assertEqual(result['generation'], event)
        self.assertNotIn('--resume', calls[0])
        self.assertEqual(result['state'], 'accepted_unverified')
        self.assertEqual(w.status(self.root)['judgment'], [])
        self.assertIsNone(w.deliver_one(self.root, runner))

    def test_fork_cannot_consume_inherited_notice_or_read_source(self):
        event = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        before = (self.root / 'tracking.sqlite').read_bytes()
        with patch.dict(os.environ, CODEX_THREAD_ID=OTHER, CODEX_SESSION_ID=OTHER):
            with patch.object(w, 'private_notice') as read_packet:
                with self.assertRaisesRegex(ValueError, 'another chat'):
                    w.receive_notice_path(self.root, w.notice_path(self.root, event, HELPER))
                read_packet.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'No attempted notice'):
                w.receive_notice(self.root, event, OTHER)
        self.assertEqual(before, (self.root / 'tracking.sqlite').read_bytes())
        self.assertEqual(w.status(self.root)['judgment'], [])

    def test_receive_is_read_only_and_prior_judgment_is_not_new_work(self):
        event = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        before = (self.root / 'tracking.sqlite').read_bytes()
        with patch.dict(os.environ, CODEX_THREAD_ID=HELPER, CODEX_SESSION_ID=HELPER):
            value = w.receive_notice_path(self.root, w.notice_path(self.root, event, HELPER))
        self.assertEqual(value['disposition'], 'recipient_verified')
        self.assertEqual(value['notice']['recipient'], HELPER)
        self.assertFalse(value['objective_completion_verified'])
        self.assertEqual(before, (self.root / 'tracking.sqlite').read_bytes())
        self.ack(event)
        with patch.dict(os.environ, CODEX_THREAD_ID=HELPER, CODEX_SESSION_ID=HELPER):
            value = w.receive_notice(self.root, event, HELPER)
        self.assertEqual(value['disposition'], 'already_acknowledged')
        self.assertNotIn('notice', value)

    def test_receive_path_rejects_foreign_root_and_renamed_packet(self):
        event = self.fresh()
        path = w.notice_path(self.root, event, HELPER)
        outside = self.root / 'copied-packet.json'
        outside.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'inside this tracking root'):
            w.receive_notice_path(self.root, outside)
        renamed = path.parent / 'copied-packet.json'
        renamed.write_bytes(path.read_bytes())
        with self.assertRaisesRegex(ValueError, 'routing identity'):
            w.receive_notice_path(self.root, renamed)

    def test_receive_refuses_missing_identity_and_unattempted_notice(self):
        event = self.fresh()
        with patch.dict(os.environ, CODEX_THREAD_ID=HELPER, CODEX_SESSION_ID=OTHER):
            with self.assertRaisesRegex(ValueError, 'runtime identity'):
                w.receive_notice(self.root, event, HELPER)
        with patch.dict(os.environ, CODEX_THREAD_ID=HELPER, CODEX_SESSION_ID=HELPER):
            with self.assertRaisesRegex(ValueError, 'No attempted notice'):
                w.receive_notice(self.root, event, HELPER)

    def test_receive_refuses_packet_and_source_tampering(self):
        event = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        packet = w.notice_path(self.root, event, HELPER)
        old_packet = packet.read_text()
        value = json.loads(old_packet)
        value['recipient'] = OTHER
        packet.write_text(w.encoded(value))
        with patch.dict(os.environ, CODEX_THREAD_ID=HELPER, CODEX_SESSION_ID=HELPER):
            with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
                w.receive_notice(self.root, event, HELPER)
            packet.write_text(old_packet)
            source = Path(value['source_fact'])
            source.write_text(source.read_text() + ' ')
            with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
                w.receive_notice(self.root, event, HELPER)

    def test_receive_refuses_inactive_policy(self):
        event = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        with w.database(self.root) as db:
            db.execute('UPDATE policy SET enabled=0')
        with patch.dict(os.environ, CODEX_THREAD_ID=HELPER, CODEX_SESSION_ID=HELPER):
            with self.assertRaisesRegex(ValueError, 'inactive or expired'):
                w.receive_notice(self.root, event, HELPER)

    def test_legacy_attempted_notice_remains_readable_without_reformat_or_replay(self):
        event = self.fresh()
        path = w.notice_path(self.root, event, HELPER)
        packet = json.loads(path.read_text())
        packet.pop('receive_command')
        path.write_text(w.encoded(packet))
        body = w.visible_notice(packet, path)
        self.assertNotIn('recipient check', body)
        with w.database(self.root) as db:
            db.execute('UPDATE notice SET body=?,outcome=? WHERE fact=? AND recipient=?',
                       (body, w.encoded({'packet_sha256': w.hashlib.sha256(path.read_bytes()).hexdigest()}),
                        event, HELPER))
        sent = []
        def runner(argv, **kwargs):
            sent.append(argv)
            return self.fake_queue(argv, **kwargs)
        w.deliver_one(self.root, runner)
        self.assertEqual(sent[0][-1], body)
        with patch.dict(os.environ, CODEX_THREAD_ID=HELPER, CODEX_SESSION_ID=HELPER):
            self.assertEqual(w.receive_notice(self.root, event, HELPER)['disposition'], 'recipient_verified')
        self.assertEqual(path.read_text(), w.encoded(packet))
        self.assertIsNone(w.deliver_one(self.root, runner))

    def test_uncertain_claim_never_retried(self):
        self.fresh()
        def fail(*a, **k):
            raise subprocess.TimeoutExpired('fake', 15)
        self.assertEqual(w.deliver_one(self.root, fail)['state'], 'uncertain_no_retry')
        self.assertIsNone(w.deliver_one(self.root, fail))

    def test_crash_claim_remains_sticky(self):
        self.fresh()
        with self.assertRaises(RuntimeError):
            w.deliver_one(self.root, lambda *a, **k: (_ for _ in ()).throw(RuntimeError('crash')))
        self.assertEqual(w.status(self.root)['notice'][0]['state'], 'claimed')
        self.assertIsNone(w.deliver_one(self.root, self.fake_queue))

    def test_ack_routes_one_root_notice_with_judgment(self):
        event = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        self.ack(event)
        self.ack(event)
        notices = w.status(self.root)['notice']
        self.assertEqual(len(notices), 2)
        packet = w.private_notice(self.root, notices[1]['fact'], notices[1]['recipient'])[1]
        self.assertEqual(packet['owner_judgment']['outcome_kind'], 'draft_ready')

    def test_root_fallback_independent_of_model_ack(self):
        self.fresh()
        future = time.time() + 121
        with patch('task_watch.time.time', return_value=future):
            w.deliver_one(self.root, self.fake_queue)
            w.deliver_one(self.root, self.fake_queue)
            self.assertIsNone(w.deliver_one(self.root, self.fake_queue))
        self.assertEqual({r['recipient'] for r in w.status(self.root)['notice']}, {ROOT, HELPER})

    def test_exact_self_notice_suppressed_but_added_human_work_preserved(self):
        self.fresh()
        notice = w.status(self.root)['notice'][0]['body']
        helper_reader = FakeReader([turn('helper-baseline')])
        w.reconcile_task(self.root, helper_reader, HELPER)
        helper_reader.current.insert(0, turn('self', user=notice))
        self.assertEqual(w.reconcile_task(self.root, helper_reader, HELPER)['events'], [])
        helper_reader.current[0]['items'].append({'type': 'userMessage', 'id': 'human',
            'content': [{'type': 'text', 'text': 'Also revise my actual work'}]})
        self.assertEqual(len(w.reconcile_task(self.root, helper_reader, HELPER)['events']), 1)

    def test_sent_requires_separate_transport_evidence(self):
        event = self.fresh(final='Sent to sponsor')
        self.assertEqual(w.status(self.root)['fact'][0]['phase'], 'reported_outcome')
        w.deliver_one(self.root, self.fake_queue)
        with self.assertRaisesRegex(ValueError, 'transport evidence'):
            self.ack(event, outcome_kind='sent_verified')
        self.ack(event, outcome_kind='sent_reported')

    def test_open_hold_survives_later_final(self):
        first = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        self.ack(first, open_decisions=[{'key': 'approve', 'reason': 'Exact copy approval required'}])
        self.fresh('next-final')
        self.assertEqual(w.status(self.root)['decision'][0]['state'], 'open')

    def test_paused_requires_resume_condition(self):
        event = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        with self.assertRaisesRegex(ValueError, 'resume condition'):
            self.ack(event, task_state='paused')
        self.ack(event, task_state='paused', resume_condition='Human resumes recording')
        path = w.render_board(self.root)
        self.assertIn('Human resumes recording', Path(path).read_text())

    def test_disabled_policy_no_read_no_send_no_renewal(self):
        self.fresh()
        with w.database(self.root) as db:
            db.execute('UPDATE policy SET enabled=0')
        with self.assertRaises(ValueError):
            w.reconcile_task(self.root, self.reader, WORKER)
        with self.assertRaises(ValueError):
            w.deliver_one(self.root, self.fake_queue)
        self.assertEqual(len(w.status(self.root)['lease']), 1)

    def test_disable_commits_while_queue_attempt_is_in_flight(self):
        self.fresh()
        started, finish = threading.Event(), threading.Event()
        errors = []
        def runner(*args, **kwargs):
            started.set()
            finish.wait(3)
            return self.fake_queue(*args, **kwargs)
        def deliver():
            try:
                w.deliver_one(self.root, runner)
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=deliver)
        thread.start()
        try:
            self.assertTrue(started.wait(2))
            with w.database(self.root) as db:
                db.execute('UPDATE policy SET enabled=0')
            self.assertEqual(w.status(self.root)['policy'][0]['enabled'], 0)
        finally:
            finish.set()
            thread.join(4)
        self.assertEqual(errors, [])
        with self.assertRaisesRegex(ValueError, 'disabled'):
            w.deliver_one(self.root, self.fake_queue)

    def test_replayed_old_ack_does_not_reopen_newer_resolution(self):
        first = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        open_hold = [{'key': 'approve', 'reason': 'Exact copy approval required'}]
        self.ack(first, open_decisions=open_hold)
        second = self.fresh('approved-revision')
        w.deliver_one(self.root, self.fake_queue)  # root first-generation notice
        w.deliver_one(self.root, self.fake_queue)  # helper second-generation notice
        self.ack(second, resolved_decisions=['approve'], resolution_evidence=['actual-human-approval'])
        self.ack(first, open_decisions=open_hold)
        self.assertEqual(w.status(self.root)['decision'][0]['state'], 'resolved')

    def test_new_request_invalidates_completed_state_without_model_notice(self):
        first = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        self.ack(first, task_state='completed', completion_evidence=['accepted-final-artifact'])
        self.assertEqual(w.status(self.root)['task_state'][0]['state'], 'completed')
        self.fresh('new-user-turn', final=None, status='inProgress')
        data = w.status(self.root)
        self.assertEqual(data['task_state'][0]['state'], 'working')
        self.assertEqual(data['fact'][-1]['phase'], 'working')
        self.assertEqual(len(data['notice']), 2)  # only original helper+root notices
        self.reader.current[0] = turn('new-user-turn', final='A different new outcome')
        w.reconcile_task(self.root, self.reader, WORKER)
        self.assertEqual(w.status(self.root)['task_state'][0]['state'], 'unknown')

    def test_ack_rejects_changed_immutable_source(self):
        event = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        Path(w.status(self.root)['fact'][0]['path']).write_text('{"tampered":true}')
        with self.assertRaisesRegex(ValueError, 'source fact changed'):
            self.ack(event)
        self.assertEqual(w.status(self.root)['judgment'], [])

    def test_admission_rejects_wrong_actual_session(self):
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=OTHER):
            with self.assertRaisesRegex(ValueError, 'Fixed root'):
                w.admission(self.root, {'workstream': 'video', 'name': 'Creator',
                    'coherent_scope': 'Video', 'observed_pressure': 'Repeated missed handoffs'})

    def test_disable_during_read_io_returns_and_blocks_postread_commit(self):
        for kind in ('baseline', 'reconcile', 'catchup'):
            with self.subTest(kind=kind):
                root = Path(self.temp.name).resolve() / kind
                w.initialize(root, self.spec)
                reader = FakeReader([turn('old-final')])
                if kind != 'baseline':
                    w.reconcile_task(root, reader, WORKER)
                reader.current.insert(0, turn('authorized-old-draft'))
                started, finish = threading.Event(), threading.Event()
                errors = []
                original = reader.turns
                def slow(*args, **kwargs):
                    started.set()
                    finish.wait(3)
                    return original(*args, **kwargs)
                reader.turns = slow
                def run():
                    try:
                        if kind == 'catchup':
                            w.catchup(root, reader, WORKER, 'authorized-old-draft')
                        else:
                            w.reconcile_task(root, reader, WORKER)
                    except BaseException as exc:
                        errors.append(exc)
                thread = threading.Thread(target=run)
                thread.start()
                try:
                    self.assertTrue(started.wait(2))
                    with w.database(root) as db:
                        db.execute('UPDATE policy SET enabled=0')
                    self.assertEqual(w.status(root)['policy'][0]['enabled'], 0)
                finally:
                    finish.set()
                    thread.join(4)
                self.assertEqual(len(errors), 1)
                self.assertIn('disabled', str(errors[0]))
                self.assertEqual(w.status(root)['fact'], [])
                self.assertEqual(w.status(root)['notice'], [])

    def test_delayed_first_ack_cannot_reopen_newer_resolution(self):
        first = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        self.ack(first, open_decisions=[{'key': 'approve', 'reason': 'Original approval hold'}])
        delayed = self.fresh('delayed-ack')
        newest = self.fresh('newest-ack')
        while w.deliver_one(self.root, self.fake_queue):
            pass
        self.ack(newest, resolved_decisions=['approve', 'late-key'],
                 resolution_evidence=['Actual newer human resolution'])
        self.ack(delayed, open_decisions=[{'key': 'approve', 'reason': 'Old first acknowledgment'},
                                         {'key': 'late-key', 'reason': 'Late first open'}])
        decisions = w.status(self.root)['decision']
        self.assertEqual({d['state'] for d in decisions}, {'resolved'})
        self.assertEqual({d['generation'] for d in decisions}, {newest})

    def test_named_historical_catchup_does_not_replace_current_state(self):
        event = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        self.ack(event, task_state='completed', completion_evidence=['Actual acceptance'])
        self.reader.current += [turn('authorized-old-draft')]
        historical = w.catchup(self.root, self.reader, WORKER, 'authorized-old-draft')
        while w.deliver_one(self.root, self.fake_queue):
            pass
        self.ack(historical, open_decisions=[{'key': 'old-approval', 'reason': 'Old unsent draft'}])
        self.assertEqual(w.status(self.root)['task_state'][0]['state'], 'completed')
        self.assertEqual(w.status(self.root)['decision'], [])

    def test_official_root_helper_binding_changes_future_routes_only(self):
        old = self.fresh()
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            reservation = w.admission(self.root, {'workstream': 'other-video', 'name': 'Video followups',
                'coherent_scope': 'Repeated sponsor review handoffs',
                'observed_pressure': 'Two missed coordinated handoffs', 'members': [WORKER]})
            receipt = {'reservation': reservation['reservation'], 'thread': OTHER,
                'created_project_id': self.spec['project_id'], 'actual_execution_cwd': '/synthetic/workspace'}
            receipt.update(self.placement(OTHER))
            receipt['official_evidence'].update(create='official-create-result',
                                                execution_cwd='actual-command-cwd-item')
            bound = w.bind_helper(self.root, receipt)
            self.assertTrue(bound['prior_fact_routes_unchanged'])
            self.assertEqual(w.bind_helper(self.root, receipt)['action'], 'already_bound')
        self.fresh('future-owned-turn')
        facts = w.status(self.root)['fact']
        self.assertEqual(next(f for f in facts if f['id'] == old)['owner'], HELPER)
        self.assertEqual(facts[-1]['owner'], OTHER)
        helper = next(t for t in w.status(self.root)['task'] if t['thread'] == OTHER)
        self.assertIsNone(helper['baseline'])
        self.assertEqual(helper['owner'], ROOT)

    def test_pin_only_receipt_cannot_bind_a_new_manager(self):
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            reservation = w.admission(self.root, {'workstream': 'new', 'name': 'Publisher',
                'coherent_scope': 'Publishing handoffs', 'observed_pressure': 'Explicit redistribution',
                'members': [WORKER]})
            with self.assertRaisesRegex(ValueError, 'placement'):
                w.bind_helper(self.root, {'reservation': reservation['reservation'], 'thread': OTHER,
                    'created_project_id': self.spec['project_id'], 'actual_execution_cwd': self.spec['workspace'],
                    'pinned_index': 3, 'official_evidence': {'create': 'create', 'execution_cwd': 'cwd', 'pin': 'pin'}})
        self.assertEqual(len(w.status(self.root)['helper']), 1)

    def cutover(self, **changes):
        value = {'operation_id': str(uuid.uuid4()), 'helper': HELPER, 'members': [WORKER],
                 **self.placement()}
        value.update(changes)
        return value

    def test_existing_helper_cutover_is_atomic_idempotent_and_preserves_history(self):
        old = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        self.ack(old)
        with w.database(self.root) as db:
            db.execute('UPDATE task SET owner=? WHERE thread=?', (ROOT, WORKER))
        before = w.status(self.root)
        value = self.cutover(expected_owners={WORKER: ROOT})
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            result = w.cutover_members(self.root, value)
            self.assertTrue(result['prior_fact_routes_unchanged'])
            with patch('task_watch.time.time', return_value=time.time() + 1000):
                self.assertEqual(w.cutover_members(self.root, value)['action'], 'already_applied')
            with self.assertRaisesRegex(ValueError, 'immutable'):
                w.cutover_members(self.root, dict(value, expected_owners={WORKER: HELPER}))
        after = w.status(self.root)
        for table in ('fact', 'notice', 'judgment', 'task_state', 'decision', 'lease'):
            self.assertEqual(before[table], after[table], table)
        self.assertEqual(next(t for t in after['task'] if t['thread'] == WORKER)['owner'], HELPER)
        self.fresh('after-member-cutover')
        self.assertEqual(w.status(self.root)['fact'][-1]['owner'], HELPER)

    def test_concurrent_owner_change_refuses_whole_binding(self):
        value = self.cutover(expected_owners={WORKER: ROOT})
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            with self.assertRaisesRegex(ValueError, 'Owner changed'):
                w.cutover_members(self.root, value)
        self.assertEqual(next(t for t in w.status(self.root)['task'] if t['thread'] == WORKER)['owner'], HELPER)
        with w.database(self.root) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM scope_change').fetchone()[0], 0)

    def test_one_member_conflict_leaves_every_member_unchanged(self):
        extra = str(uuid.uuid4())
        spec = dict(self.spec, tasks=self.spec['tasks'] + [
            {'thread': extra, 'owner': ROOT, 'charter': 'Another ordinary work item'}])
        root = Path(self.temp.name).resolve() / 'multi-member'
        w.initialize(root, spec)
        value = self.cutover(members=[WORKER, extra], **self.placement(members=[WORKER, extra]))
        before = w.status(root)['task']
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            with self.assertRaisesRegex(ValueError, 'Owner changed'):
                w.cutover_members(root, value)
        self.assertEqual(before, w.status(root)['task'])

    def test_cutover_refuses_foreign_actor_unknown_helper_and_recursive_members(self):
        value = self.cutover()
        with patch.dict(os.environ, CODEX_THREAD_ID=HELPER, CODEX_SESSION_ID=HELPER):
            with self.assertRaisesRegex(ValueError, 'root runtime'):
                w.cutover_members(self.root, value)
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            with self.assertRaisesRegex(ValueError, 'registered helper'):
                w.cutover_members(self.root, dict(value, helper=OTHER))
            for members in ([HELPER], [ROOT], [WORKER, WORKER], [OTHER]):
                with self.subTest(members=members), self.assertRaises(ValueError):
                    w.cutover_members(self.root, dict(value, members=members))

    def test_metadata_section_id_stale_or_nonfirst_placement_refused(self):
        for bad in ('backend_section_id', 'backend_shape', 'stale', 'wrong_helper', 'not_first',
                    'missing_evidence', 'missing_authorization', 'remote_host', 'pinned'):
            value = self.cutover()
            if bad == 'backend_section_id':
                value['placement']['section_id'] = str(uuid.uuid4())
            elif bad == 'backend_shape':
                value['placement']['inventory_section'] = {'id': value['placement']['section_id'], 'name': 'Backend section'}
            elif bad == 'stale':
                value['placement']['observed_at'] -= 301
            elif bad == 'wrong_helper':
                value['placement']['thread_id'] = OTHER
            elif bad == 'not_first':
                value['placement']['inventory_section']['itemKeys'].reverse()
            elif bad == 'missing_evidence':
                del value['official_evidence']['inventory']
            elif bad == 'missing_authorization':
                del value['authorization_evidence']
            elif bad == 'remote_host':
                value['placement']['host_id'] = 'remote-host'
            else:
                value['placement']['section_id'] = 'pinned'
            with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    w.cutover_members(self.root, value)

    def test_today_location_exception_needs_exact_independent_inventory(self):
        value = self.cutover()
        value['placement']['inventory_section']['itemKeys'].pop()
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            with self.assertRaisesRegex(ValueError, 'location exception'):
                w.cutover_members(self.root, value)
            value['placement']['location_exceptions'] = {WORKER: {
                'reason': 'Keep human-selected Today priority', 'official_inventory_evidence': 'official-Today',
                'inventory_section': {'sectionId': str(uuid.uuid4()), 'name': 'Today',
                                      'itemKeys': ['codex:thread:local:' + WORKER]}}}
            section = value['placement']['location_exceptions'][WORKER]['inventory_section']
            key = 'codex:thread:local:' + WORKER
            for invalid in ('x' + key + 'y', key, [key, key], [key, 1], {'member': key}):
                section['itemKeys'] = invalid
                with self.subTest(itemKeys=invalid), self.assertRaises(ValueError):
                    w.cutover_members(self.root, value)
            section['itemKeys'] = [key]
            self.assertEqual(w.cutover_members(self.root, value)['action'], 'members_cut_over')

    def test_ownership_changes_preserve_disabled_delivery_and_lease(self):
        with w.database(self.root) as db:
            db.execute('UPDATE policy SET enabled=0,expires=1')
            before = dict(db.execute('SELECT * FROM policy').fetchone())
            leases = [dict(r) for r in db.execute('SELECT * FROM lease')]
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            value = self.cutover()
            self.assertEqual(w.cutover_members(self.root, value)['action'], 'members_cut_over')
            reservation = w.admission(self.root, {'workstream': 'disabled-new', 'name': 'Publisher',
                'coherent_scope': 'Publishing handoffs', 'observed_pressure': 'Explicit redistribution',
                'members': [WORKER]})
            receipt = dict(reservation=reservation['reservation'], thread=OTHER,
                           created_project_id=self.spec['project_id'], actual_execution_cwd=self.spec['workspace'],
                           **self.placement(OTHER))
            receipt['official_evidence'].update(create='create', execution_cwd='cwd')
            self.assertEqual(w.bind_helper(self.root, receipt)['action'], 'bound_by_root_attestation')
        with w.database(self.root) as db:
            after = dict(db.execute('SELECT * FROM policy').fetchone())
            self.assertEqual({k:v for k,v in before.items() if k != 'spec'},
                             {k:v for k,v in after.items() if k != 'spec'})
            self.assertEqual(leases, [dict(r) for r in db.execute('SELECT * FROM lease')])

    def test_helper_binding_requires_official_operation_evidence(self):
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            reservation = w.admission(self.root, {'workstream': 'new', 'name': 'New helper',
                'coherent_scope': 'Explicit coherent scope', 'observed_pressure': 'Missed handoffs',
                'members': [WORKER]})
            with self.assertRaisesRegex(ValueError, 'saved project'):
                w.bind_helper(self.root, {'reservation': reservation['reservation'], 'thread': OTHER})
        self.assertEqual(len(w.status(self.root)['helper']), 1)

    def test_admission_capacity_counts_pending_reservations(self):
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            for name in ('a', 'b', 'c'):
                w.admission(self.root, {'workstream': name, 'name': name,
                    'coherent_scope': 'Semantic scope', 'observed_pressure': 'Repeated coordination delay', 'members': [WORKER]})
            with self.assertRaisesRegex(ValueError, 'capacity'):
                w.admission(self.root, {'workstream': 'd', 'name': 'd',
                    'coherent_scope': 'Semantic scope', 'observed_pressure': 'Repeated delay', 'members': [WORKER]})

    def test_explicit_six_workstream_capacity_preserves_disabled_delivery(self):
        with w.database(self.root) as db:
            db.execute('UPDATE policy SET enabled=0,expires=1')
            before = dict(db.execute('SELECT * FROM policy').fetchone())
            leases = [dict(r) for r in db.execute('SELECT * FROM lease')]
        receipt = {'operation_id': str(uuid.uuid4()), 'capacity': 6, 'expected_capacity': 4,
                   'observed_at': time.time(), 'authorization_evidence': 'Explicit all-six-workstreams approval'}
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            self.assertEqual(w.set_helper_capacity(self.root, receipt)['capacity'], 6)
            self.assertEqual(w.set_helper_capacity(self.root, receipt)['action'], 'already_applied')
            with self.assertRaisesRegex(ValueError, 'immutable'):
                w.set_helper_capacity(self.root, dict(receipt, capacity=7))
            for name in ('builder', 'publishing', 'vlogs', 'oncall', 'runtime'):
                w.admission(self.root, {'workstream': name, 'name': name, 'coherent_scope': 'Exact workstream',
                    'observed_pressure': 'Explicit ownership request', 'members': [WORKER]})
        with w.database(self.root) as db:
            after = dict(db.execute('SELECT * FROM policy').fetchone())
            self.assertEqual({k:v for k,v in before.items() if k != 'spec'},
                             {k:v for k,v in after.items() if k != 'spec'})
            self.assertEqual(leases, [dict(r) for r in db.execute('SELECT * FROM lease')])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM helper').fetchone()[0], 1)

    def test_capacity_change_refuses_foreign_stale_conflicting_or_overfull_request(self):
        receipt = {'operation_id': str(uuid.uuid4()), 'capacity': 6, 'expected_capacity': 4,
                   'observed_at': time.time(), 'authorization_evidence': 'Explicit approval'}
        with patch.dict(os.environ, CODEX_THREAD_ID=HELPER, CODEX_SESSION_ID=HELPER):
            with self.assertRaisesRegex(ValueError, 'root runtime'):
                w.set_helper_capacity(self.root, receipt)
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            for changes in ({'capacity': 9}, {'capacity': True}, {'observed_at': 1},
                            {'authorization_evidence': ''}, {'expected_capacity': 3}):
                with self.subTest(changes=changes), self.assertRaises(ValueError):
                    w.set_helper_capacity(self.root, dict(receipt, **changes))
            w.admission(self.root, {'workstream': 'pending', 'name': 'Pending', 'coherent_scope': 'Scope',
                'observed_pressure': 'Explicit request', 'members': [WORKER]})
            with self.assertRaisesRegex(ValueError, 'discard'):
                w.set_helper_capacity(self.root, dict(receipt, capacity=1))

    def test_new_admission_rejects_unbindable_members_before_creation_packet(self):
        value = {'workstream': 'new', 'name': 'New helper', 'coherent_scope': 'Coherent scope',
                 'observed_pressure': 'Repeated missed handoffs'}
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            for members in (None, [], [WORKER, WORKER], [HELPER], [ROOT], [OTHER]):
                with self.subTest(members=members), self.assertRaises(ValueError):
                    w.admission(self.root, dict(value, members=members))
            self.assertEqual(w.status(self.root)['reservation'], [])

    def test_new_admission_requires_saved_project_before_creation_packet(self):
        with w.database(self.root) as db:
            spec = json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
            del spec['project_id']
            db.execute('UPDATE policy SET spec=?', (w.encoded(spec),))
        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            with self.assertRaisesRegex(ValueError, 'Saved project'):
                w.admission(self.root, {'workstream': 'new', 'name': 'New', 'coherent_scope': 'Scope',
                    'observed_pressure': 'Repeated coordination delay', 'members': [WORKER]})
        self.assertEqual(w.status(self.root)['reservation'], [])

    def test_explicit_renewal_epoch_and_clock_guard(self):
        future = time.time() + 21601
        with patch('task_watch.time.time', return_value=future):
            with self.assertRaisesRegex(ValueError, 'expired'):
                w.deliver_one(self.root, self.fake_queue)
            w.reconcile_task(self.root, self.reader, WORKER)
        self.assertEqual(len(w.status(self.root)['lease']), 2)
        with self.assertRaisesRegex(ValueError, 'clock'):
            w.reconcile_task(self.root, self.reader, WORKER)

    def test_immutable_owner_per_generation(self):
        old = self.fresh()
        with w.database(self.root) as db:
            db.execute('UPDATE task SET owner=? WHERE thread=?', (ROOT, WORKER))
        new = self.fresh('next')
        facts = {f['id']: f for f in w.status(self.root)['fact']}
        self.assertEqual(facts[old]['owner'], HELPER)
        self.assertEqual(facts[new]['owner'], ROOT)

    def test_admission_reuses_then_reserves_without_duplicate_or_count_only(self):
        value = {'workstream': 'video', 'coherent_scope': 'Video backlog',
                 'observed_pressure': 'Repeated unacknowledged review handoffs', 'name': 'Creator', 'members': [WORKER]}

        with patch.dict(os.environ, CODEX_THREAD_ID=ROOT, CODEX_SESSION_ID=ROOT):
            self.assertEqual(w.admission(self.root, value)['thread'], HELPER)
            value['workstream'] = 'research'
            first = w.admission(self.root, value)
            second = w.admission(self.root, value)
            self.assertEqual(first['reservation'], second['id'])
            with self.assertRaises(ValueError):
                w.admission(self.root, {'workstream': 'count-only', 'name': 'Extra'})

    def test_recursive_owner_and_controller_observation_refused(self):
        self.spec['tasks'][1]['owner'] = WORKER
        with self.assertRaises(ValueError):
            w.initialize(Path(self.temp.name) / 'cycle', self.spec)
        self.spec['tasks'][1]['owner'] = ROOT
        self.spec['tasks'].append({'thread': ROOT, 'owner': HELPER, 'charter': 'Bad'})
        with self.assertRaises(ValueError):
            w.initialize(Path(self.temp.name) / 'root-cycle', self.spec)

    def test_reader_allowlist_refuses_mutation_before_write(self):
        reader = object.__new__(w.Reader)
        with self.assertRaisesRegex(ValueError, 'allowlist'):
            reader.call('thread/resume', {})

    def test_board_escapes_source_names_and_shows_unknown_and_coverage(self):
        path = w.render_board(self.root)
        text = Path(path).read_text()
        self.assertIn('unknown', text)
        self.assertIn('Reporting coverage', text)
        self.assertIn('codex://threads/', text)
        self.assertIn('Stale; no fresh successful read', text)

    def test_unchanged_completed_head_avoids_full_turn_fetch(self):
        def forbidden(*args, **kwargs):
            raise AssertionError('Quiet completed turn fetched full history')
        self.reader.turns = forbidden
        self.assertTrue(w.reconcile_task(self.root, self.reader, WORKER)['quiet_head'])

    def test_active_same_head_fetches_only_one_full_turn(self):
        self.fresh(final=None, status='inProgress')
        limits = []
        original = self.reader.turns
        def track(*args, **kwargs):
            limits.append(kwargs.get('limit'))
            return original(*args, **kwargs)
        self.reader.turns = track
        w.reconcile_task(self.root, self.reader, WORKER)
        self.assertEqual(limits, [1])

    def test_delivery_tick_is_independent_of_slow_native_read(self):
        self.fresh()
        started, finish, delivered, stop = [threading.Event() for _ in range(4)]
        original = self.reader.head
        def slow(*args):
            started.set(); finish.wait(3)
            return original(*args)
        self.reader.head = slow
        errors = []
        def read():
            try:w.reconcile_task(self.root, self.reader, WORKER)
            except BaseException as exc:errors.append(exc)
        def queue_runner(*args, **kwargs):
            delivered.set()
            return self.fake_queue(*args, **kwargs)
        read_thread = threading.Thread(target=read)
        delivery = threading.Thread(target=w.delivery_loop, args=(self.root, stop, queue_runner))
        read_thread.start()
        try:
            self.assertTrue(started.wait(2))
            delivery.start()
            self.assertTrue(delivered.wait(1), 'Slow API read starved outbox delivery')
            self.assertFalse(finish.is_set())
        finally:
            finish.set();stop.set();read_thread.join(4);delivery.join(4)
        self.assertEqual(errors, [])

    def test_completed_question_head_still_reads_full_source(self):
        self.fresh(questions=[{'title': 'A question remains'}])
        calls = []
        original = self.reader.turns
        def track(*args, **kwargs):
            calls.append(kwargs.get('limit'))
            return original(*args, **kwargs)
        self.reader.turns = track
        w.reconcile_task(self.root, self.reader, WORKER)
        self.assertEqual(calls, [1])

    def test_board_reports_uncertain_delivery_separate_from_fresh_read(self):
        self.fresh()
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired('fake', 15)
        w.deliver_one(self.root, timeout)
        text = Path(w.render_board(self.root)).read_text()
        self.assertIn('uncertain_no_retry', text)
        self.assertIn('recent supported read', text)

    def test_delivery_thread_failure_surfaces_and_stops(self):
        stop = threading.Event()
        errors = []
        with patch('task_watch.deliver_one', side_effect=RuntimeError('Synthetic delivery failure')):
            w.delivery_loop(self.root, stop, self.fake_queue, errors)
        self.assertTrue(stop.is_set())
        self.assertIn('Synthetic delivery failure', errors[0])

    def test_disabled_delivery_loop_exits_cleanly(self):
        stop, errors = threading.Event(), []
        with w.database(self.root) as db:
            db.execute('UPDATE policy SET enabled=0')
        w.delivery_loop(self.root, stop, self.fake_queue, errors)
        self.assertTrue(stop.is_set())
        self.assertEqual(errors, [])

    def test_delivery_clock_sample_is_taken_after_database_lock(self):
        self.fresh()
        original, attempted, errors = w.database, threading.Event(), []
        @contextmanager
        def announced(root):
            if threading.current_thread().name == 'delivery-clock-test':
                attempted.set()
            with original(root) as db:
                yield db
        def deliver():
            try:w.deliver_one(self.root, self.fake_queue)
            except BaseException as exc:errors.append(exc)
        with original(self.root) as db:
            with patch('task_watch.database', announced):
                thread = threading.Thread(target=deliver, name='delivery-clock-test')
                thread.start()
                self.assertTrue(attempted.wait(2))
                db.execute('UPDATE policy SET last_time=?', (time.time(),))
        thread.join(3)
        self.assertEqual(errors, [])
        self.assertEqual(w.status(self.root)['notice'][0]['state'], 'accepted_unverified')


if __name__ == '__main__':
    unittest.main()
