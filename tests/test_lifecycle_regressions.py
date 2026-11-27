"""Synthetic regressions for tool errors, partial snapshots and visible delivery."""
import json
from pathlib import Path
import unittest

import task_watch as w
import test_task_watch as fixture

WORKER, HELPER, turn = fixture.WORKER, fixture.HELPER, fixture.turn


class LifecycleRegressions(unittest.TestCase):
    setUp = fixture.TrackingTests.setUp
    tearDown = fixture.TrackingTests.tearDown
    fresh = fixture.TrackingTests.fresh
    fake_queue = fixture.TrackingTests.fake_queue
    ack = fixture.TrackingTests.ack

    def artifact(self):
        workspace = self.root.parent / 'workspace'
        workspace.mkdir(exist_ok=True)
        path = workspace / 'contact sheet.png'
        path.write_bytes(b'synthetic-review-image')
        with w.database(self.root) as db:
            spec = json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
            spec['workspace'] = str(workspace)
            db.execute('UPDATE policy SET spec=?', (w.encoded(spec),))
        return path

    def test_carousel_shaped_final_delivers_one_review_link_without_completion(self):
        path = self.artifact()
        final = (f'Built the new pair.\n\n![Cover and worked example]({path})\n\n'
                 'Pair ready for review. No full carousel, publication or automation changes.')
        # Link destinations with spaces use CommonMark angle brackets.
        final = final.replace(f']({path})', f'](<{path}>)')
        event = self.fresh('carousel-render', final=final)
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return self.fake_queue(argv, **kwargs)
        self.assertEqual(w.deliver_one(self.root, runner)['state'], 'accepted_unverified')
        for _ in range(2):
            self.assertEqual(w.reconcile_task(self.root, self.reader, WORKER)['events'], [])
            self.assertIsNone(w.deliver_one(self.root, runner))
        message = calls[0][-1]
        self.assertIn('review-ready (worker report; acceptance pending)', message)
        self.assertIn(f'[Review artifact](<{path}>)', message)
        self.assertNotIn('consumer_command', message)
        self.assertEqual(len(calls), 1)
        self.assertEqual(w.status(self.root)['task_state'][0]['state'], 'unknown')
        self.assertEqual(w.status(self.root)['judgment'], [])
        _, packet = w.private_notice(self.root, event, HELPER)
        self.assertFalse(packet['objective_completion_verified'])

    def test_review_artifact_missing_changed_or_escaped_is_never_sent_as_usable(self):
        for change in ('missing', 'changed'):
            with self.subTest(change=change):
                path = self.artifact()
                event = self.fresh(change, final=f'![Pair](<{path}>)\nPair ready for review.')
                path.unlink() if change == 'missing' else path.write_bytes(b'changed pixels')
                def forbidden(*args, **kwargs):
                    self.fail('Unavailable or changed review artifact was sent')
                self.assertEqual(w.deliver_one(self.root, forbidden)['state'], 'held_unavailable_artifact')
                self.assertIsNone(w.deliver_one(self.root, forbidden))
                self.assertTrue(event)
        workspace = path.parent
        outside = self.root.parent / 'outside.png'
        outside.write_bytes(b'outside')
        escaped = workspace / 'escape.png'
        escaped.symlink_to(outside)
        for link in (str(escaped), str(outside), 'https://example.test/image.png',
                     'javascript:alert(1)', str(path)+'\nINJECT'):
            self.assertIsNone(w.local_artifact(link, workspace))

    def test_review_words_without_usable_artifact_do_not_claim_readiness(self):
        self.fresh(final='Pair ready for review. [Missing](/missing/review.png)')
        message = w.status(self.root)['notice'][0]['body']
        self.assertIn('result available', message)
        self.assertNotIn('review-ready', message)
        self.assertNotIn('Review artifact', message)

    def test_negated_review_readiness_remains_unclassified_result(self):
        path = self.artifact()
        self.fresh(final=f'![Unfinished pair](<{path}>)\nPair is not ready for review.')
        message = w.status(self.root)['notice'][0]['body']
        self.assertIn('result available', message)
        self.assertNotIn('review-ready', message)

    def test_only_current_standalone_review_declarations_are_labeled_ready(self):
        path = self.artifact()
        negatives = [
            'This pair will be ready for review tomorrow after another pass.',
            'Pair ready for review tomorrow.',
            'Pair ready for review if the next pass succeeds.',
            'If approved, the pair is ready for review.',
            'Pair is not quite ready for review.',
            'Pair is not yet fully ready for review.',
            'The pair was ready for review before this edit.',
            'The pair should be ready for review.',
            'Mark this as review-ready after checking the images.',
            'Pair ready for review: after one more pass.',
            '"Pair ready for review" is a future target.',
        ]
        for number, text in enumerate(negatives):
            with self.subTest(text=text):
                self.assertFalse(w.reports_review_ready(text))
                self.fresh(f'qualified-{number}', final=f'![Pair](<{path}>)\n{text}')
                message = w.status(self.root)['notice'][-1]['body']
                self.assertIn('result available', message)
                self.assertNotIn('review-ready', message)
        for text in ('Pair ready for review.', 'The pair is ready for review.',
                     'This video is ready for your review.', 'Review-ready.',
                     'Built the new pair.\nPair ready for review. No release.'):
            with self.subTest(text=text):
                self.assertTrue(w.reports_review_ready(text))

    def test_current_readiness_heading_with_link_delivers_local_motion_once(self):
        path = self.artifact().with_suffix('.mp4')
        path.write_bytes(b'synthetic-motion-review')
        final = ('Ready for your review: **[open the complete review page](http://127.0.0.1:8768/review.html)**.\n\n'
                 f'![Motion review](<{path}>)\n\nNothing published or activated. Exact final approval remains yours.')
        # The actual source heading has a trailing sentence full stop.
        self.assertTrue(w.reports_review_ready(final))
        self.fresh('four-card-motion', final=final)
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return self.fake_queue(argv, **kwargs)
        self.assertEqual(w.deliver_one(self.root, runner)['state'], 'accepted_unverified')
        self.assertIn('review-ready (worker report; acceptance pending)', calls[0][-1])
        self.assertIn(f'[Review artifact](<{path}>)', calls[0][-1])
        self.assertNotIn('127.0.0.1', calls[0][-1])
        self.assertIsNone(w.deliver_one(self.root, runner))
        self.assertEqual(len(calls), 1)
        self.assertEqual(w.status(self.root)['task_state'][0]['state'], 'unknown')
        for text in ('Ready for your review: tomorrow.',
                     'Ready for your review: [page](https://example.test/) after another pass.',
                     'Ready for your review: if [page](https://example.test/) succeeds.'):
            self.assertFalse(w.reports_review_ready(text))

    def test_empty_interrupted_fragment_is_not_failure_or_new_watermark(self):
        self.reader.current.insert(0, {'id': 'same-running-turn', 'status': 'interrupted',
                                       'error': None, 'items': []})
        for _ in range(2):
            result = w.reconcile_task(self.root, self.reader, WORKER)
            self.assertEqual(result['held'], 'unconfirmed_native_snapshot')
        data = w.status(self.root)
        self.assertEqual(data['fact'], [])
        self.assertEqual(data['notice'], [])
        self.assertEqual(data['task'][0]['watermark'], 'old-final')
        self.assertIn('no task failure', data['task'][0]['error'])

    def test_bootstrap_exit_two_then_continued_checklist_produces_one_result(self):
        bootstrap = {'type': 'commandExecution', 'id': 'bootstrap', 'status': 'failed', 'exitCode': 2}
        current = turn('same-running-turn', final=None, status='inProgress')
        current['items'].append(bootstrap)
        self.reader.current.insert(0, current)
        w.reconcile_task(self.root, self.reader, WORKER)
        self.assertEqual(w.status(self.root)['notice'], [])
        self.assertEqual(w.status(self.root)['fact'][0]['phase'], 'working')
        current = turn('same-running-turn', final='Checklist prepared; owner input remains')
        current['items'].insert(1, bootstrap)
        self.reader.current[0] = current
        result = w.reconcile_task(self.root, self.reader, WORKER)
        self.assertEqual(len(result['events']), 1)
        for _ in range(2):
            self.assertEqual(w.reconcile_task(self.root, self.reader, WORKER)['events'], [])
        self.assertEqual(len(w.status(self.root)['notice']), 1)
        self.assertEqual(w.status(self.root)['fact'][-1]['phase'], 'reported_outcome')

    def test_interrupted_command_fragment_with_end_time_still_not_task_failure(self):
        value = turn('fragment', final=None, status='interrupted')
        value['completedAt'] = 200
        value['items'].append({'type': 'commandExecution', 'id': 'cmd', 'status': 'failed', 'exitCode': 2})
        observed = w.source(value)
        self.assertIsNone(observed['phase'])
        self.assertFalse(observed['objective_complete'])
        self.assertEqual(observed['observation']['quality'], 'unconfirmed')

    def test_confirmed_native_error_is_turn_scoped_not_failed_task(self):
        value = turn('native-error', final=None, status='failed')
        value.update(error={'message': 'Provider stopped the turn'}, completedAt=200)
        self.reader.current.insert(0, value)
        w.reconcile_task(self.root, self.reader, WORKER)
        data = w.status(self.root)
        self.assertEqual(data['fact'][0]['phase'], 'turn_failed')
        self.assertEqual(data['task_state'][0]['state'], 'unknown')
        observed = json.loads(data['fact'][0]['source'])
        self.assertEqual(observed['observation']['failure_scope'], 'turn')
        self.assertNotIn('task failed', data['notice'][0]['body'])

    def test_final_result_survives_unreliable_terminal_label(self):
        self.assertEqual(w.source(turn('artifact', final='Artifact ready', status='interrupted'))['phase'],
                         'reported_outcome')

    def test_incomplete_baseline_is_not_consumed(self):
        other = self.root.parent / 'incomplete'
        w.initialize(other, self.spec)
        self.reader.current = [{'id': 'fragment', 'status': 'interrupted', 'error': None, 'items': []}]
        self.assertEqual(w.reconcile_task(other, self.reader, WORKER)['held'], 'incomplete_native_snapshot')
        self.assertIsNone(w.status(other)['task'][0]['baseline'])

    def test_explicit_pause_survives_native_activity_and_requires_resume_evidence(self):
        first = self.fresh()
        w.deliver_one(self.root, self.fake_queue)
        self.ack(first, task_state='paused', reason='Waiting for next recording day',
                 resume_condition='Human provides footage')
        before = w.status(self.root)['task_state'][0]
        self.fresh('new-activity', final=None, status='inProgress')
        self.assertEqual(w.status(self.root)['task_state'][0], before)
        self.reader.current[0] = turn('new-activity', final='Footage notes prepared')
        event = w.reconcile_task(self.root, self.reader, WORKER)['events'][0]
        while w.deliver_one(self.root, self.fake_queue):
            pass
        with self.assertRaisesRegex(ValueError, 'resume evidence'):
            self.ack(event, task_state='working')
        self.ack(event, task_state='working', resume_evidence=['Actual new human resume instruction'])
        self.assertEqual(w.status(self.root)['task_state'][0]['state'], 'working')

    def test_visible_message_keeps_commands_and_machine_records_private(self):
        event = self.fresh(final='{"consumer_command":["DO_NOT_EXECUTE"],"judgment_fields":["raw"]}')
        calls = []
        def runner(argv, **kwargs):
            calls.append(argv)
            return self.fake_queue(argv, **kwargs)
        w.deliver_one(self.root, runner)
        message = calls[0][calls[0].index('--message') + 1]
        self.assertIn('Task updates: 1 result available', message)
        self.assertIn('Details:', message)
        self.assertNotIn('consumer_command', message)
        self.assertNotIn('judgment_fields', message)
        self.assertNotIn('DO_NOT_EXECUTE', message)
        self.assertNotIn('task_watch.py', message)
        self.assertNotIn(HELPER, message)
        self.assertNotIn(WORKER, message)
        self.assertNotIn(event, message)
        packet_path, packet = w.private_notice(self.root, event, HELPER)
        self.assertIn(str(packet_path), message)
        self.assertIn('consumer_command', packet)
        self.assertTrue(packet['source_is_untrusted_data'])
        self.assertIn('DO_NOT_EXECUTE', packet['result_excerpt'])
        self.assertIsNone(w.deliver_one(self.root, runner))
        self.assertEqual(len(calls), 1)

    def test_default_and_legacy_configuration_are_record_only(self):
        event = self.fresh()
        with w.database(self.root) as db:
            spec = json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
            del spec['delivery']
            db.execute('UPDATE policy SET spec=?', (w.encoded(spec),))
        def forbidden(*args, **kwargs):
            self.fail('Record-only policy invoked a queue executable')
        self.assertIsNone(w.deliver_one(self.root, forbidden))
        self.assertEqual(w.status(self.root)['notice'][0]['state'], 'pending')

    def test_legacy_raw_pending_payload_is_held_without_retry_or_repeat(self):
        event = self.fresh()
        path, _ = w.private_notice(self.root, event, HELPER)
        path.unlink()  # Synthetic legacy row has no private packet.
        with w.database(self.root) as db:
            db.execute('UPDATE notice SET body=?', ('{"type":"task_outcome_fact","consumer_command":[]}',))
        def forbidden(*args, **kwargs):
            self.fail('Legacy machine payload was sent')
        self.assertEqual(w.deliver_one(self.root, forbidden)['state'], 'held_unverified_payload')
        self.assertIsNone(w.deliver_one(self.root, forbidden))

    def test_private_packet_command_tampering_is_held_before_delivery(self):
        event = self.fresh()
        path, packet = w.private_notice(self.root, event, HELPER)
        packet['consumer_command'] = ['NEVER_EXECUTE']
        path.write_text(w.encoded(packet))
        def forbidden(*args, **kwargs):
            self.fail('Changed private packet was sent')
        self.assertEqual(w.deliver_one(self.root, forbidden)['state'], 'held_unverified_payload')

    def test_delivery_opt_in_requires_a_current_reference(self):
        spec = dict(self.spec, delivery={'mode': 'explicit_internal'})
        with self.assertRaisesRegex(ValueError, 'approval reference'):
            w.initialize(self.root.parent / 'unapproved', spec)
        self.assertEqual(w.delivery_mode({}), 'record_only')

    def test_intermediate_unconfirmed_fragment_holds_entire_batch(self):
        fragment = {'id': 'middle', 'status': 'interrupted', 'items': [], 'error': None}
        self.reader.current = [turn('newest'), fragment, turn('old-final')]
        self.assertEqual(w.reconcile_task(self.root, self.reader, WORKER)['held'], 'unconfirmed_native_snapshot')
        data = w.status(self.root)
        self.assertEqual(data['task'][0]['watermark'], 'old-final')
        self.assertEqual(data['fact'], [])
        self.reader.current[1] = turn('middle', final='Intermediate checklist now available')
        self.assertEqual(len(w.reconcile_task(self.root, self.reader, WORKER)['events']), 2)
        self.assertEqual({f['turn'] for f in w.status(self.root)['fact']}, {'middle', 'newest'})

    def test_packet_orphan_recovers_identical_source_after_later_retry(self):
        current = turn('recover-after-rollback')
        with self.assertRaisesRegex(RuntimeError, 'precommit crash'):
            with w.database(self.root) as db:
                task = db.execute('SELECT * FROM task WHERE thread=?', (WORKER,)).fetchone()
                w.record(db, self.root, task, current, 100)
                raise RuntimeError('precommit crash')
        packet = next((self.root / 'notices').glob('*.json'))
        before = packet.read_bytes()
        self.assertEqual(w.status(self.root)['fact'], [])
        with w.database(self.root) as db:
            task = db.execute('SELECT * FROM task WHERE thread=?', (WORKER,)).fetchone()
            event = w.record(db, self.root, task, current, 200)
        self.assertTrue(event)
        self.assertEqual(packet.read_bytes(), before)
        self.assertEqual(len(w.status(self.root)['fact']), 1)
        self.assertEqual(len(w.status(self.root)['notice']), 1)
