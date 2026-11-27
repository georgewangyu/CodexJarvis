import copy
import json
import os
from pathlib import Path
import subprocess
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


class HierarchyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'session'
        self.log = self.base / 'queue.jsonl'
        fake = self.base / 'fake-queue'
        fake.write_text('#!' + sys.executable + '\nimport json,sys\nwith open(' + repr(str(self.log)) + ',"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n')
        fake.chmod(0o700)
        c.initialize(self.root, ROOT, str(fake), 600)
        self.old = c.register(self.root, WORKER, 500)
        self.home = patch('cooperate.Path.home', return_value=self.base)
        self.home.start()
        self.addCleanup(self.home.stop)
        (self.base / 'charter.md').write_text('Synthetic bounded creator coordination only.')
        (self.base / 'backlog.md').write_text('Idle until explicitly routed work.')
        self.judgment = self.base / 'judgment.json'
        self.judgment.write_text('{"result":"routing proof only","editorial_approval":false}')
        self.spec = self.make_spec(COORD)

    def make_spec(self, coordinator):
        binding = self.base / (coordinator + '-binding.json')
        binding.write_text(json.dumps({'thread_id': coordinator, 'project_id': PROJECT,
            'environment': 'local', 'host_id': 'local', 'execution': {'cwd': str(self.base),
            'runtime_thread': coordinator, 'runtime_session': coordinator, 'item_id': 'synthetic-exec'}}))
        return {'name': 'Creator coordinator', 'thread': coordinator, 'runtime_session': coordinator,
            'parent': ROOT, 'scope': 'Two selected synthetic creator outcomes only.',
            'workers': [{'thread': WORKER, 'runtime_session': WORKER, 'scope': 'Synthetic editing task'}],
            'charter': str(self.base/'charter.md'), 'backlog': str(self.base/'backlog.md'),
            'expires': time.time()+500, 'project_id': PROJECT, 'workspace': str(self.base),
            'binding': str(binding), 'binding_sha256': c.artifact_digest(binding)}

    def configure(self):
        return c.configure_coordinator(self.root, self.spec)

    def report(self, reg, status='ready', worker=WORKER):
        Path(reg['receipt']).write_text('{"result":"synthetic technical result"}')
        with patch.dict(os.environ, {'CODEX_THREAD_ID': worker, 'CODEX_SESSION_ID': worker}):
            return c.report(self.root, reg['assignment'], status)

    def send(self, seconds=30):
        with patch('cooperate.time.time', return_value=time.time()+seconds):
            return c.deliver_once(self.root)

    def relay(self, notice, status='ready', runtime=COORD):
        with patch.dict(os.environ, {'CODEX_THREAD_ID': COORD, 'CODEX_SESSION_ID': runtime}):
            return c.relay_notice(self.root, notice, status, str(self.judgment))

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_binding_requires_actual_project_execution_and_runtime(self):
        for kind in ('cwd', 'runtime', 'project'):
            spec = copy.deepcopy(self.spec)
            b = json.loads(Path(spec['binding']).read_text())
            if kind == 'cwd': b['execution']['cwd'] = '/wrong'
            if kind == 'runtime': b['execution']['runtime_thread'] = OTHER
            if kind == 'project': b['project_id'] = OTHER
            Path(spec['binding']).write_text(json.dumps(b))
            spec['binding_sha256'] = c.artifact_digest(spec['binding'])
            with self.assertRaises(ValueError): c.configure_coordinator(self.root, spec)
        self.assertEqual(c.parent_channel(self.root), [])

    def test_reject_parent_self_duplicates_and_unregistered_workers(self):
        for workers in ([{'thread': ROOT, 'runtime_session': ROOT, 'scope': 'wrong'}],
                        self.spec['workers'] * 2,
                        [{'thread': OTHER, 'runtime_session': OTHER, 'scope': 'unknown'}]):
            spec = copy.deepcopy(self.spec);spec['workers'] = workers
            with self.assertRaises(ValueError): c.configure_coordinator(self.root, spec)
        spec = copy.deepcopy(self.spec);spec['parent'] = OTHER
        with self.assertRaises(ValueError): c.configure_coordinator(self.root, spec)

    def test_configuration_idempotent_overlap_and_nested_refused(self):
        self.configure()
        self.assertEqual(self.configure()['result'], 'unchanged')
        with self.assertRaises(ValueError): c.configure_coordinator(self.root, self.make_spec(OTHER))
        nested = self.make_spec(OTHER);nested['workers'] = [{'thread': COORD, 'runtime_session': COORD, 'scope': 'nested'}]
        with self.assertRaises(ValueError): c.configure_coordinator(self.root, nested)

    def test_handoff_only_future_assignments_have_one_owner(self):
        c.set_delivery_mode(self.root, 'visible');self.configure()
        new = c.register(self.root, WORKER, 400)
        self.report(self.old);self.report(new)
        first=self.send();second=self.send()
        self.assertEqual([x[2] for x in self.calls()], [ROOT, COORD])
        self.assertEqual(c.read_notice(self.root, first['notice'])['target'], ROOT)
        self.assertEqual(c.read_notice(self.root, second['notice'])['target'], COORD)
        self.assertEqual(len(c.parent_channel(self.root, COORD)), 1)

    def test_facts_publish_at_record_time_without_model_relay(self):
        self.configure();reg=c.register(self.root, WORKER, 400)
        with patch('cooperate.subprocess.run') as run:
            self.report(reg);self.report(reg)
            facts=c.parent_channel(self.root, COORD)
            run.assert_not_called()
        self.assertEqual(len(facts), 1)
        self.assertEqual(json.loads(facts[0]['event'])['assignment'], reg['assignment'])
        self.assertFalse(self.log.exists())

    def test_nested_worker_to_coordinator_to_root_and_fingerprint_dedup(self):
        self.configure();c.set_delivery_mode(self.root, 'visible')
        reg=c.register(self.root, WORKER, 400);self.report(reg)
        child=self.send();up=self.relay(child['notice']);parent=self.send()
        self.assertEqual([x[2] for x in self.calls()], [COORD, ROOT])
        self.assertEqual(self.relay(child['notice'])['result'], 'duplicate')
        self.assertIsNone(self.send())
        parent_index=c.read_notice(self.root,parent['notice'])
        self.assertEqual(parent_index['events'][0]['worker'],COORD)
        receipt=json.loads(Path(parent_index['events'][0]['receipt']).read_text())
        self.assertEqual(receipt['source_facts'][0]['assignment'],reg['assignment'])
        self.assertFalse(receipt['judgment']['editorial_approval'])
        self.assertEqual(len(c.parent_channel(self.root)),1)  # No coordinator/root loop.

    def test_input_and_later_final_have_distinct_parent_fingerprints(self):
        self.configure();c.set_delivery_mode(self.root,'visible');reg=c.register(self.root,WORKER,400)
        self.report(reg,'needs_input');first=self.send(3);self.relay(first['notice'],'needs_input');self.send(3)
        self.report(reg,'ready');second=self.send();self.relay(second['notice']);self.send()
        self.assertEqual([x[2] for x in self.calls()],[COORD,ROOT,COORD,ROOT])
        self.assertEqual(len(c.parent_channel(self.root)),2)

    def test_wrong_runtime_or_other_channel_cannot_relay(self):
        self.configure();c.set_delivery_mode(self.root,'visible');reg=c.register(self.root,WORKER,400)
        self.report(reg);notice=self.send()
        with self.assertRaises(ValueError):self.relay(notice['notice'],runtime=OTHER)
        self.report(self.old);root_notice=self.send()
        with self.assertRaises(ValueError):self.relay(root_notice['notice'])

    def test_parent_uncertain_queue_is_never_retried_or_relayed_again(self):
        self.configure();c.set_delivery_mode(self.root,'visible');reg=c.register(self.root,WORKER,400)
        self.report(reg);notice=self.send();self.relay(notice['notice'])
        with patch('cooperate.subprocess.run',side_effect=subprocess.TimeoutExpired('fake',15)) as run:
            self.assertEqual(self.send()['outcome'],'uncertain_no_retry')
            self.assertIsNone(self.send());self.assertEqual(run.call_count,1)
        self.assertEqual(self.relay(notice['notice'])['result'],'duplicate')
        self.assertEqual(len(c.parent_channel(self.root)),1)

    def test_withdraw_changes_only_future_registrations_no_lease_renewal(self):
        self.configure();before=c.register(self.root,WORKER,400)
        with c.connect(self.root) as db:expiry=db.execute('SELECT expires FROM session').fetchone()[0]
        c.withdraw_coordinator(self.root,COORD);after=c.register(self.root,WORKER,400)
        with c.connect(self.root) as db:
            self.assertEqual(c.assignment_owner(db,before['assignment'])[0],COORD)
            self.assertEqual(c.assignment_owner(db,after['assignment'])[0],ROOT)
            self.assertEqual(db.execute('SELECT expires FROM session').fetchone()[0],expiry)

    def test_expired_coordinator_cannot_accept_new_routes(self):
        self.configure()
        with patch('cooperate.time.time',return_value=time.time()+501),self.assertRaises(ValueError):
            c.register(self.root,WORKER,100)

    def test_existing_child_assignment_cannot_outlive_coordinator_scope(self):
        self.spec['expires']=time.time()+100;self.configure()
        reg=c.register(self.root,WORKER,500)
        with c.connect(self.root) as db:
            expiry=db.execute('SELECT expires FROM assignment WHERE id=?',(reg['assignment'],)).fetchone()[0]
        self.assertEqual(expiry,self.spec['expires'])
        with patch('cooperate.time.time',return_value=time.time()+101),self.assertRaises(ValueError):
            self.report(reg)


if __name__ == '__main__':unittest.main()
