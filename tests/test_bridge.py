import io
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import subprocess

from bridge import consume

TARGET = '11111111-1111-4111-8111-f11111111111'
WORKER = '22222222-2222-4222-8222-f22222222222'
TURN = '33333333-3333-4333-8333-f33333333333'
CONFIG = {'target': TARGET, 'watch': [[WORKER, TURN]]}


def event(method='turn/completed', status='completed', worker=WORKER, turn=TURN):
    return {'method': method, 'params': {'threadId': worker, 'turn': {'id': turn, 'status': status}}}


class BridgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = str(Path(self.temp.name) / 'state.sqlite')

    def run_events(self, *events):
        return consume(io.StringIO(''.join(json.dumps(e)+'\n' for e in events)), CONFIG, self.state)

    @patch('bridge.subprocess.run')
    def test_terminal_deduplicated_across_restarts(self, run):
        run.return_value.returncode = 0
        self.run_events(event(), event())
        self.run_events(event())
        self.assertEqual(run.call_count, 1)
        args = run.call_args.args[0]
        self.assertEqual(args[:4], ['codex', 'queue', '--thread', TARGET])
        self.assertNotIn('--resume', args)

    @patch('bridge.subprocess.run')
    def test_progress_foreign_historical_silent(self, run):
        self.run_events(event(method='item/agentMessage/delta'), event(status='inProgress'),
                        event(worker=TARGET), event(turn=TARGET))
        run.assert_not_called()

    @patch('bridge.subprocess.run')
    def test_failure_no_retry(self, run):
        run.return_value.returncode = 1
        self.assertEqual(self.run_events(event(status='failed')), 1)
        self.run_events(event(status='failed'))
        self.assertEqual(run.call_count, 1)

    @patch('bridge.subprocess.run', side_effect=subprocess.TimeoutExpired('codex', 15))
    def test_uncertain_delivery_not_retried(self, run):
        self.assertEqual(self.run_events(event()), 1)
        self.run_events(event())
        self.assertEqual(run.call_count, 1)
        with closing(sqlite3.connect(self.state)) as db:
            self.assertEqual(db.execute('SELECT outcome FROM delivery').fetchone()[0], 'uncertain_no_retry')

    def test_malformed_stops(self):
        with self.assertRaises(ValueError):
            consume(io.StringIO('oops\n'), CONFIG, self.state)

    def test_self_watch_rejected(self):
        with self.assertRaises(ValueError):
            consume(io.StringIO(''), {'target': TARGET, 'watch': [[TARGET, TURN]]}, self.state)

    def test_mixed_case_self_watch_rejected(self):
        task = 'abcdefab-abcd-4abc-8abc-abcdefabcdef'
        with self.assertRaises(ValueError):
            consume(io.StringIO(''), {'target': task, 'watch': [[task.upper(), TURN]]}, self.state)

    def test_fake_executable_boundary(self):
        fake = Path(self.temp.name) / 'queue'
        receipt = Path(self.temp.name) / 'argv.json'
        fake.write_text('#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\nPath('+repr(str(receipt))+').write_text(json.dumps(sys.argv[1:]))\n')
        fake.chmod(0o700)
        self.assertEqual(consume(io.StringIO(json.dumps(event())+'\n'), CONFIG, self.state, str(fake)), 0)
        self.assertEqual(json.loads(receipt.read_text())[:3], ['queue', '--thread', TARGET])


if __name__ == '__main__':
    unittest.main()
