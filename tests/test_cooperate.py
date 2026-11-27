import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import cooperate as c

TARGET = '11111111-1111-4111-8111-f11111111111'
WORKER = '22222222-2222-4222-8222-f22222222222'
SCRIPT = str(Path(c.__file__).resolve())


class CooperativeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'session'
        self.log = self.base / 'argv.jsonl'
        self.fake = self.base / 'fake-queue'
        self.fake.write_text('#!' + sys.executable + '\nimport json,sys\n'
                             'with open(' + repr(str(self.log)) + ', "a") as f:\n'
                             ' f.write(json.dumps(sys.argv[1:])+"\\n")\n')
        self.fake.chmod(0o700)
        c.initialize(self.root, TARGET, str(self.fake), 600)
        self.reg = c.register(self.root, WORKER, 500)
        self.receipt = Path(self.reg['receipt'])
        self.receipt.write_text(json.dumps({'result': 'synthetic'}))
        self.env = dict(os.environ, CODEX_THREAD_ID=WORKER, CODEX_SESSION_ID=WORKER)
        self.home_patch = patch('cooperate.Path.home', return_value=self.base)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def report(self, status='ready'):
        with patch.dict(os.environ, self.env, clear=True):
            return c.report(self.root, self.reg['assignment'], status)

    def outcomes(self):
        with c.connect(self.root) as db:
            return [row[0] for row in db.execute('SELECT outcome FROM event ORDER BY rowid')]

    def stop_results(self, bootout_code=0):
        with c.connect(self.root) as db:
            label = 'local.codexjarvis.' + db.execute('SELECT id FROM session').fetchone()[0] + '.visible'
        return [subprocess.CompletedProcess([], bootout_code, b'', b''),
                subprocess.CompletedProcess([], 113, '', f'Could not find service "{label}"')]

    def test_record_only_durable_report_and_duplicate(self):
        self.assertEqual(self.report()['delivery'], 'record_only')
        self.assertEqual(self.report()['result'], 'duplicate')
        self.assertIsNone(c.deliver_once(self.root))
        view = c.inbox(self.root)
        self.assertFalse(view['automatic_delivery'])
        self.assertEqual(len(view['events']), 1)
        self.assertTrue(view['events'][0]['receipt_valid'])
        self.assertEqual(view['events'][0]['worker'], WORKER)
        self.assertEqual(self.outcomes(), ['recorded_only'])
        self.assertFalse(self.log.exists())

    def test_input_then_final_preserved_and_latest_summary(self):
        self.report('needs_input')
        first = self.receipt.read_bytes()
        self.receipt.write_text('{"result":"fixed"}')
        self.report('ready')
        view = c.inbox(self.root)
        self.assertEqual([e['phase'] for e in view['events']], ['input', 'final'])
        self.assertEqual(view['summary'], {'needs_input': 0, 'ready': 1, 'failed': 0})
        snapshot = self.root / 'receipts' / (self.reg['assignment'] + '-input.json')
        self.assertEqual(snapshot.read_bytes(), first)
        self.assertTrue(all(e['receipt_valid'] for e in view['events']))
        with self.assertRaises(ValueError):
            self.report('failed')
        self.assertFalse(self.log.exists())

    def test_final_rejects_later_input(self):
        self.report()
        with self.assertRaises(ValueError):
            self.report('needs_input')

    def test_runtime_identity_guard(self):
        for env in ({}, {'CODEX_THREAD_ID': WORKER, 'CODEX_SESSION_ID': TARGET},
                    {'CODEX_THREAD_ID': TARGET, 'CODEX_SESSION_ID': TARGET}):
            with patch.dict(os.environ, env, clear=True), self.assertRaises(ValueError):
                c.report(self.root, self.reg['assignment'], 'ready')
        self.assertEqual(c.inbox(self.root)['events'], [])

    def test_explicit_subagent_parent_session_identity(self):
        reg = c.register(self.root, WORKER, 400, runtime_session=TARGET)
        Path(reg['receipt']).write_text('{}')
        with patch.dict(os.environ, dict(self.env, CODEX_SESSION_ID=TARGET), clear=True):
            c.report(self.root, reg['assignment'], 'ready')
        self.assertEqual(c.inbox(self.root)['events'][0]['runtime_session'], TARGET)

    def test_foreign_assignment_and_bad_status(self):
        with patch.dict(os.environ, self.env, clear=True):
            for assignment, status in ((TARGET, 'ready'), (self.reg['assignment'], 'completed')):
                with self.assertRaises(ValueError):
                    c.report(self.root, assignment, status)

    def test_no_historic_session_or_self_watch(self):
        with self.assertRaises(FileExistsError):
            c.initialize(self.root, TARGET, str(self.fake), 600)
        with self.assertRaises(ValueError):
            c.register(self.root, TARGET, 600)

    def test_expiry_blocks_reports_but_keeps_on_demand_history(self):
        self.report()
        with patch('cooperate.time.time', return_value=time.time() + 601):
            with self.assertRaises(ValueError):
                c.register(self.root, WORKER, 100)
            with self.assertRaises(ValueError):
                self.report()
            view = c.inbox(self.root)
        self.assertFalse(view['lease_active'])
        self.assertEqual(len(view['events']), 1)
        self.assertFalse(self.log.exists())

    def test_stop_disables_reports_without_erasing_history(self):
        self.report()
        with patch('cooperate.subprocess.run', side_effect=self.stop_results()):
            self.assertTrue(c.stop(self.root)['job_unloaded'])
        with self.assertRaises(ValueError):
            self.report()
        self.assertEqual(len(c.inbox(self.root)['events']), 1)

    def test_private_root_and_receipt_symlink_rejected(self):
        self.root.chmod(0o755)
        with self.assertRaises(ValueError):
            self.report()
        self.root.chmod(0o700)
        self.receipt.unlink()
        self.receipt.symlink_to(self.fake)
        with self.assertRaises(ValueError):
            self.report()

    def test_receipt_required_bounded_and_json_object(self):
        for value in ('bad json', '[]', ' ' * 1048577):
            self.receipt.write_text(value)
            with self.assertRaises(ValueError):
                self.report()
        self.receipt.unlink()
        with self.assertRaises(ValueError):
            self.report()

    def test_tampering_flagged_without_delivery_or_state_change(self):
        self.report()
        snapshot = self.root / 'receipts' / (self.reg['assignment'] + '-final.json')
        snapshot.write_text('{}')
        self.assertFalse(c.inbox(self.root)['events'][0]['receipt_valid'])
        self.assertEqual(self.outcomes(), ['recorded_only'])
        snapshot.unlink()
        self.assertFalse(c.inbox(self.root)['events'][0]['receipt_valid'])
        self.assertFalse(self.log.exists())

    def test_old_outcomes_and_leases_preserved_never_replayed(self):
        states = ['pending', 'claimed', 'accepted_unverified', 'failed_no_retry', 'uncertain_no_retry']
        for state in states:
            reg = c.register(self.root, WORKER, 400)
            Path(reg['receipt']).write_text('{}')
            with patch.dict(os.environ, self.env, clear=True):
                c.report(self.root, reg['assignment'], 'failed')
            with c.connect(self.root) as db:
                db.execute('UPDATE event SET outcome=? WHERE assignment=?', (state, reg['assignment']))
                db.commit()
        with c.connect(self.root) as db:
            before_session = dict(db.execute('SELECT * FROM session').fetchone())
            before_assignments = [dict(r) for r in db.execute('SELECT * FROM assignment')]
        first_policy = c.set_record_only(self.root)
        with patch('cooperate.subprocess.run') as run:
            c.deliver_once(self.root)
            c.inbox(self.root)
            self.assertEqual(c.set_record_only(self.root), first_policy)
            run.assert_not_called()
        self.assertEqual(self.outcomes(), states)
        with c.connect(self.root) as db:
            self.assertEqual(dict(db.execute('SELECT * FROM session').fetchone()), before_session)
            self.assertEqual([dict(r) for r in db.execute('SELECT * FROM assignment')], before_assignments)
        self.assertFalse(self.log.exists())

    def test_concurrent_reporters_claim_one_record_without_delivery(self):
        args = [sys.executable, SCRIPT, '--root', str(self.root), 'report',
                '--assignment', self.reg['assignment'], '--status', 'ready']
        processes = [subprocess.Popen(args, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                     for _ in range(4)]
        results = []
        for process in processes:
            out, err = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, (out, err))
            results.append(json.loads(out)['result'])
        self.assertEqual(results.count('reported'), 1)
        self.assertEqual(results.count('duplicate'), 3)
        self.assertEqual(len(c.inbox(self.root)['events']), 1)
        self.assertFalse(self.log.exists())

    def test_cli_private_inbox_cursor_and_no_read_mutation(self):
        subprocess.run([sys.executable, SCRIPT, '--root', str(self.root), 'report',
                        '--assignment', self.reg['assignment'], '--status', 'ready'],
                       env=self.env, check=True, capture_output=True, timeout=5)
        args = [sys.executable, SCRIPT, '--root', str(self.root), 'inbox']
        view = json.loads(subprocess.check_output(args, text=True, timeout=5))
        cursor = view['next_cursor']
        next_view = json.loads(subprocess.check_output(args + ['--after', str(cursor)], text=True, timeout=5))
        self.assertEqual(next_view['events'], [])
        self.assertEqual(len(c.inbox(self.root)['events']), 1)
        with self.assertRaises(ValueError):
            c.inbox(self.root, -1)
        self.assertFalse(self.log.exists())

    def test_no_delivery_job_or_legacy_drain_can_start(self):
        self.report()
        with patch('cooperate.subprocess.run') as run:
            for method in (c.watch, c.install):
                with self.assertRaises(ValueError):
                    method(self.root)
            c.deliver_once(self.root)
            run.assert_not_called()
        for command in ('watch', 'install', 'drain'):
            result = subprocess.run([sys.executable, SCRIPT, '--root', str(self.root), command],
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 2)
        self.assertEqual(self.outcomes(), ['recorded_only'])
        self.assertFalse(self.log.exists())

    def test_failed_stop_never_claims_unloaded(self):
        for probe in (subprocess.CompletedProcess([], 0, 'still loaded', ''),
                      subprocess.CompletedProcess([], 1, '', 'Permission denied')):
            with (patch('cooperate.subprocess.run', side_effect=[subprocess.CompletedProcess([], 1), probe]),
                  patch('cooperate.time.monotonic', side_effect=[0, 6]), self.assertRaises(RuntimeError)):
                c.stop(self.root)

    def test_stop_idempotent_when_exact_service_absent(self):
        with patch('cooperate.subprocess.run', side_effect=self.stop_results(113)):
            self.assertTrue(c.stop(self.root)['job_unloaded'])

    def test_stop_waits_for_async_service_disappearance(self):
        results = self.stop_results()
        results.insert(1, subprocess.CompletedProcess([], 0, 'state = stopping', ''))
        with patch('cooperate.subprocess.run', side_effect=results) as run, patch('cooperate.time.sleep') as sleep:
            self.assertTrue(c.stop(self.root)['job_unloaded'])
        self.assertEqual(run.call_count, 3)
        self.assertEqual(sum(call.args[0][1] == 'bootout' for call in run.call_args_list), 1)
        sleep.assert_called_once_with(0.1)

    def fresh_report(self, status='ready'):
        reg = c.register(self.root, WORKER, 400)
        Path(reg['receipt']).write_text('{"result":"DO_NOT_COPY"}')
        with patch.dict(os.environ, self.env, clear=True):
            c.report(self.root, reg['assignment'], status)
        return reg

    def send_after(self, seconds=30):
        with patch('cooperate.time.time', return_value=time.time() + seconds):
            return c.deliver_once(self.root)

    def queue_calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_explicit_activation_floor_never_replays_old_or_quiet_reports(self):
        self.report()
        with c.connect(self.root) as db:
            db.execute("UPDATE event SET outcome='pending'")
            db.commit()
        config = c.set_delivery_mode(self.root, 'visible')
        new = self.fresh_report()
        self.assertEqual(c.set_delivery_mode(self.root, 'visible'), config)
        result = self.send_after()
        self.assertEqual(result['events'], 1)
        argv = self.queue_calls()[0]
        self.assertEqual(argv[:3], ['queue', '--thread', TARGET])
        self.assertNotIn('--resume', argv)
        self.assertLess(len(argv[-1]), 180)
        for raw in (WORKER, new['assignment'], 'DO_NOT_COPY', 'sha256'):
            self.assertNotIn(raw, argv[-1])
        notice = c.read_notice(self.root, result['notice'])
        self.assertEqual(notice['events'][0]['assignment'], new['assignment'])
        self.assertEqual(self.outcomes(), ['pending', 'accepted_unverified'])

    def test_burst_coalesces_both_phases_without_losing_final(self):
        c.set_delivery_mode(self.root, 'visible')
        self.report('needs_input')
        self.report('ready')
        self.fresh_report()
        self.assertIsNone(self.send_after(1))
        result = self.send_after()
        self.assertEqual(len(self.queue_calls()), 1)
        notice = c.read_notice(self.root, result['notice'])
        self.assertEqual(len(notice['events']), 3)
        self.assertEqual(notice['summary'], {'needs_input': 0, 'ready': 2, 'failed': 0})
        self.assertEqual(self.outcomes(), ['accepted_unverified'] * 3)

    def test_input_priority_and_later_final_still_delivered(self):
        c.set_delivery_mode(self.root, 'visible')
        self.fresh_report()
        self.report('needs_input')
        self.assertIsNone(self.send_after(1))
        first = self.send_after(3)
        self.assertEqual(c.read_notice(self.root, first['notice'])['events'][0]['status'], 'needs_input')
        self.report('ready')
        second = self.send_after()
        self.assertEqual(c.read_notice(self.root, second['notice'])['summary']['ready'], 1)
        self.assertEqual(len(self.queue_calls()), 2)

    def test_visible_queue_failures_and_claim_crash_are_sticky(self):
        c.set_delivery_mode(self.root, 'visible')
        for failure in ('failed', 'timeout', 'crash'):
            self.fresh_report()
            with patch('cooperate.subprocess.run') as run:
                if failure == 'failed':
                    run.return_value.returncode = 1
                elif failure == 'timeout':
                    run.side_effect = subprocess.TimeoutExpired('fake', 15)
                else:
                    run.side_effect = KeyboardInterrupt()
                if failure == 'crash':
                    with self.assertRaises(KeyboardInterrupt):
                        self.send_after()
                else:
                    self.send_after()
                self.assertIsNone(self.send_after())
                self.assertEqual(run.call_count, 1)
        self.assertEqual(self.outcomes(), ['failed_no_retry', 'uncertain_no_retry', 'claimed'])

    def test_notice_or_source_tampering_fails_verification(self):
        c.set_delivery_mode(self.root, 'visible')
        self.report()
        result = self.send_after()
        with c.connect(self.root) as db:
            index = Path(db.execute('SELECT path FROM notice').fetchone()[0])
        content = index.read_bytes()
        index.write_text('{}')
        with self.assertRaises(ValueError):
            c.read_notice(self.root, result['notice'])
        index.write_bytes(content)
        Path(c.inbox(self.root)['events'][0]['receipt']).write_text('{}')
        with self.assertRaises(ValueError):
            c.read_notice(self.root, result['notice'])

    def test_concurrent_watch_checks_do_not_duplicate_batch(self):
        c.set_delivery_mode(self.root, 'visible')
        self.report()
        with patch('cooperate.time.time', return_value=time.time() + 30), ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: c.deliver_once(self.root), range(4)))
        self.assertEqual(len(self.queue_calls()), 1)

    def test_quiet_toggle_preserves_lease_and_reactivation_skips_waiting_records(self):
        with c.connect(self.root) as db:
            expiry = db.execute('SELECT expires FROM session').fetchone()[0]
        c.set_delivery_mode(self.root, 'visible')
        self.report()
        c.set_record_only(self.root)
        self.assertIsNone(self.send_after())
        c.set_delivery_mode(self.root, 'visible')
        self.assertIsNone(self.send_after())
        self.assertEqual(self.outcomes(), ['pending'])
        with c.connect(self.root) as db:
            self.assertEqual(db.execute('SELECT expires FROM session').fetchone()[0], expiry)
        with patch('cooperate.time.time', return_value=time.time() + 601), self.assertRaises(ValueError):
            c.set_delivery_mode(self.root, 'visible')

    def test_install_requires_optin_and_absent_legacy_service(self):
        c.set_delivery_mode(self.root, 'visible')
        with patch('cooperate.subprocess.run', return_value=subprocess.CompletedProcess([], 0, '', '')):
            with self.assertRaises(ValueError):
                c.install(self.root)
        with c.connect(self.root) as db:
            legacy = 'local.codexjarvis.' + db.execute('SELECT id FROM session').fetchone()[0]
        absent = subprocess.CompletedProcess([], 113, '', f'Could not find service "{legacy}"')
        with patch('cooperate.subprocess.run', side_effect=[absent, subprocess.CompletedProcess([], 0)]) as run:
            installed = c.install(self.root)
        self.assertTrue(installed['label'].endswith('.visible'))
        self.assertEqual(run.call_args.args[0][:2], ['/bin/launchctl', 'bootstrap'])
        self.assertEqual((self.root / 'delivery.py').read_bytes(), Path(SCRIPT).read_bytes())

    def test_visible_expired_or_corrupt_receipts_never_queue(self):
        c.set_delivery_mode(self.root, 'visible')
        self.report()
        Path(c.inbox(self.root)['events'][0]['receipt']).write_text('{}')
        self.assertIsNone(self.send_after())
        self.assertEqual(self.outcomes(), ['invalid_receipt_no_delivery'])
        self.fresh_report()
        self.assertIsNone(self.send_after(401))
        self.assertEqual(self.outcomes(), ['invalid_receipt_no_delivery', 'expired_no_delivery'])
        self.assertFalse(self.log.exists())

    def test_report_started_before_cutover_stays_quiet_but_later_final_qualifies(self):
        clock = time.time()
        with patch('cooperate.time.time', return_value=clock + 1):
            c.set_delivery_mode(self.root, 'visible')
        with patch('cooperate.time.time', return_value=clock + .5):
            result = self.report('needs_input')
        self.assertEqual(result['delivery'], 'record_only')
        self.assertEqual(self.outcomes(), ['recorded_only'])
        self.assertIsNone(self.send_after())
        with patch('cooperate.time.time', return_value=clock + 2):
            self.assertEqual(self.report('ready')['delivery'], 'visible')
        sent = self.send_after()
        self.assertEqual(sent['events'], 1)
        self.assertEqual(c.read_notice(self.root, sent['notice'])['events'][0]['phase'], 'final')
        self.assertEqual(self.outcomes(), ['recorded_only', 'accepted_unverified'])


if __name__ == '__main__':
    unittest.main()
