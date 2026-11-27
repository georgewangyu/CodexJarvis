import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import cooperate as c

ROOT = '11111111-1111-4111-8111-f11111111111'
WORKER = '22222222-2222-4222-8222-f22222222222'
COORD = '33333333-3333-4333-8333-f33333333333'
OTHER = '44444444-4444-4444-8444-f44444444444'
PROJECT = '55555555-5555-4555-8555-f55555555555'


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name).resolve()
        self.root = self.base / 'session'
        fake = self.base / 'queue'
        fake.write_text('#!' + sys.executable + '\nraise SystemExit(91)\n')
        fake.chmod(0o700)
        c.initialize(self.root, ROOT, str(fake), 600)
        self.reg = c.register(self.root, WORKER, 500)
        self.receipt = Path(self.reg['receipt'])
        self.receipt.write_text('{"result":"synthetic first result"}')
        self.env = {'CODEX_THREAD_ID': WORKER, 'CODEX_SESSION_ID': WORKER}

    def report(self, status='ready', reg=None):
        with patch.dict(os.environ, self.env):
            return c.report(self.root, (reg or self.reg)['assignment'], status)

    def snapshot(self, phase='final'):
        return self.root / 'receipts' / (self.reg['assignment'] + '-' + phase + '.json')

    def bytes_on_disk(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file()}

    def configure(self):
        binding = self.base / 'binding.json'
        binding.write_text(json.dumps({'thread_id': COORD, 'project_id': PROJECT,
            'environment': 'local', 'host_id': 'local', 'execution': {'cwd': str(self.base),
            'runtime_thread': COORD, 'runtime_session': COORD, 'item_id': 'synthetic-exec'}}))
        charter, backlog = self.base / 'charter.md', self.base / 'backlog.md'
        charter.write_text('Synthetic owner scope.'); backlog.write_text('Synthetic backlog.')
        c.configure_coordinator(self.root, {'name': 'Synthetic owner', 'thread': COORD,
            'runtime_session': COORD, 'parent': ROOT, 'scope': 'Synthetic work only',
            'workers': [{'thread': WORKER, 'runtime_session': WORKER, 'scope': 'Synthetic task'}],
            'charter': str(charter), 'backlog': str(backlog), 'expires': time.time()+450,
            'project_id': PROJECT, 'workspace': str(self.base), 'binding': str(binding),
            'binding_sha256': c.artifact_digest(binding)})

    def test_changed_consumed_final_requires_fresh_assignment(self):
        self.report(); before = self.snapshot().read_bytes()
        self.receipt.write_text('{"result":"a new revision"}')
        with self.assertRaisesRegex(ValueError, 'fresh assignment'):
            self.report()
        self.assertEqual(self.snapshot().read_bytes(), before)
        fresh = c.register(self.root, WORKER, 400)
        Path(fresh['receipt']).write_bytes(self.receipt.read_bytes())
        self.assertEqual(self.report(reg=fresh)['result'], 'reported')
        self.assertEqual(len(c.inbox(self.root)['events']), 2)

    def test_input_duplicate_after_later_final_keeps_both_snapshots(self):
        self.report('needs_input'); first = self.snapshot('input').read_bytes()
        self.receipt.write_text('{"result":"resolved"}'); self.report()
        self.assertEqual(self.report('needs_input')['result'], 'duplicate')
        self.assertEqual(self.report()['result'], 'duplicate')
        self.assertEqual(self.snapshot('input').read_bytes(), first)

    def test_third_payload_after_input_and_final_cannot_reuse_input(self):
        self.report('needs_input'); self.receipt.write_text('{"result":"resolved"}'); self.report()
        self.receipt.write_text('{"result":"a third revision"}')
        with self.assertRaisesRegex(ValueError, 'fresh assignment'):
            self.report('needs_input')
        self.assertEqual(len(c.inbox(self.root)['events']), 2)

    def test_changed_repeated_input_is_not_silently_lost(self):
        self.report('needs_input'); self.receipt.write_text('{"result":"different decision"}')
        with self.assertRaisesRegex(ValueError, 'fresh assignment'):
            self.report('needs_input')
        self.assertEqual(len(c.inbox(self.root)['events']), 1)

    def test_crash_before_commit_recovers_identical_snapshot_and_fact_once(self):
        self.configure(); self.reg = c.register(self.root, WORKER, 400)
        self.receipt = Path(self.reg['receipt']); self.receipt.write_text('{"result":"fresh"}')
        with patch('cooperate.stage_parent_fact', side_effect=RuntimeError('simulated crash')):
            with self.assertRaises(RuntimeError): self.report()
        before = self.snapshot().read_bytes()
        self.assertEqual(c.inbox(self.root)['events'], [])
        self.assertEqual(self.report()['result'], 'reported')
        self.assertEqual(self.report()['result'], 'duplicate')
        self.assertEqual(self.snapshot().read_bytes(), before)
        self.assertEqual(len(c.parent_channel(self.root, COORD)), 1)

    def test_conflicting_orphan_is_preserved_not_overwritten(self):
        self.snapshot().write_text('{"result":"other bytes"}'); self.snapshot().chmod(0o600)
        before = self.snapshot().read_bytes()
        with self.assertRaisesRegex(ValueError, 'Stranded snapshot'):
            self.report()
        self.assertEqual(self.snapshot().read_bytes(), before)
        self.assertEqual(c.inbox(self.root)['events'], [])

    def test_nonprivate_orphan_cannot_be_adopted(self):
        self.snapshot().write_bytes(self.receipt.read_bytes()); self.snapshot().chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'not private'):
            self.report()
        self.assertEqual(c.inbox(self.root)['events'], [])

    def test_corrupt_recorded_snapshot_prevents_duplicate_success(self):
        self.report(); self.snapshot().write_text('{}')
        with self.assertRaisesRegex(ValueError, 'snapshot changed'):
            self.report()

    def test_expired_recovery_is_read_only_and_never_infers_completion(self):
        self.report('needs_input')
        reg = c.register(self.root, WORKER, 400); Path(reg['receipt']).write_text('{}')
        self.report(reg=reg); before = self.bytes_on_disk()
        with patch('cooperate.time.time', return_value=time.time()+601), patch('cooperate.subprocess.run') as run:
            view = c.recovery(self.root)
            run.assert_not_called()
        self.assertFalse(view['lease_active'])
        self.assertTrue(view['new_lease_requires_explicit_authorization'])
        self.assertTrue(any(x['needs_input_unresolved_by_final'] for x in view['assignments']))
        self.assertTrue(any(x['reported_final']=='ready' for x in view['assignments']))
        self.assertTrue(all(not x['can_record_later_final'] for x in view['assignments']))
        self.assertEqual(before, self.bytes_on_disk())

    def test_root_recovery_has_only_direct_assignments_and_counted_mate_facts(self):
        self.configure(); child = c.register(self.root, WORKER, 400)
        Path(child['receipt']).write_text('{}'); self.report(reg=child)
        root, mate = c.recovery(self.root), c.recovery(self.root, COORD)
        self.assertEqual([x['assignment'] for x in root['assignments']], [self.reg['assignment']])
        self.assertEqual([x['assignment'] for x in mate['assignments']], [child['assignment']])
        self.assertEqual(root['direct_coordinators'][0]['recorded_facts'], 1)
        self.assertEqual(root['direct_coordinators'][0]['facts_without_relay_claim'], 1)
        self.assertEqual(mate['direct_coordinators'], [])
        with self.assertRaisesRegex(ValueError, 'Unknown direct-report owner'):
            c.recovery(self.root, OTHER)

    def test_unknown_delivery_remains_sticky_after_recovery(self):
        self.report()
        with c.connect(self.root) as db:
            db.execute("UPDATE event SET outcome='uncertain_no_retry'"); db.commit()
        before = self.bytes_on_disk()
        with patch('cooperate.subprocess.run') as run:
            view = c.recovery(self.root); run.assert_not_called()
        event = view['assignments'][0]
        self.assertTrue(event['delivery_requires_reconciliation'])
        self.assertEqual(event['events']['final']['outcome'], 'uncertain_no_retry')
        self.assertEqual(before, self.bytes_on_disk())

    def test_recovery_reports_orphan_without_importing_it_and_bounds_output(self):
        self.snapshot().write_bytes(self.receipt.read_bytes()); self.snapshot().chmod(0o600)
        c.register(self.root, WORKER, 400)
        all_rows = c.recovery(self.root)
        orphan = next(x for x in all_rows['assignments'] if x['assignment']==self.reg['assignment'])
        self.assertEqual(orphan['uncommitted_snapshots'][0]['sha256'], c.artifact_digest(self.receipt))
        self.assertEqual(orphan['events'], {})
        self.assertTrue(c.recovery(self.root, limit=1)['truncated'])
        for limit in (0, 1001, 'bad'):
            with self.assertRaises(ValueError): c.recovery(self.root, limit=limit)

    def test_cli_recovery_works_after_expiry_without_worker_identity(self):
        with c.connect(self.root) as db:
            db.execute('UPDATE session SET expires=?', (time.time()-1,)); db.commit()
        result = subprocess.run([sys.executable, c.__file__, '--root', str(self.root),
                                 'recovery', '--owner', ROOT], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['lease_active'])

    def test_hot_journal_readonly_failure_preserves_crash_evidence(self):
        # Force journal/cache spill, then kill the fixture writer before commit.
        writer = self.base / 'crash_writer.py'
        writer.write_text('''import sqlite3,sys,os
db=sqlite3.connect(sys.argv[1])
db.execute('CREATE TABLE crash_fixture (id INTEGER PRIMARY KEY, payload BLOB)')
db.executemany('INSERT INTO crash_fixture(payload) VALUES (?)', [(b'a'*4096,)]*100)
db.commit()
db.execute('PRAGMA cache_size=1')
db.execute('BEGIN IMMEDIATE')
db.execute('UPDATE crash_fixture SET payload=?', (b'b'*4096,))
os._exit(0)
''')
        subprocess.run([sys.executable, str(writer), str(self.root/'outbox.sqlite')], check=True)
        journal = self.root / 'outbox.sqlite-journal'
        self.assertTrue(journal.exists())
        before = self.bytes_on_disk()
        with self.assertRaises(sqlite3.OperationalError): c.recovery(self.root)
        self.assertEqual(before, self.bytes_on_disk())


if __name__ == '__main__':
    unittest.main()
