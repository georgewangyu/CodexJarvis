import copy
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import uuid
import unittest
import sidebar as s
import test_worker_placement as fixtures


class WorkerAdmissionTests(unittest.TestCase):
    def setUp(self):
        f=fixtures.WorkerPlacementTests();f.setUp();self.f=f;self.bundle=f.bundle
        self.bundle['title_read']={'thread_id':f.worker,'title':'video-operator: synthetic edit',
            'observed_at':self.bundle['observed_at'],'official_item':'synthetic-exact-title'}

    def test_actual_startup_missing_prefix_is_repaired_and_readback_required(self):
        p=s.plan_worker_admission(self.bundle)
        self.assertEqual([o['tool'] for o in p['operations']],['set_thread_title','move_thread_to_sidebar_section'])
        self.f.apply_move(p['operations'][1])
        self.assertFalse(s.verify_worker_admission(p,self.bundle)['complete'])
        self.bundle['title_read']['title']=p['display_title']
        self.assertTrue(s.verify_worker_admission(p,self.bundle)['complete'])
        self.bundle['title_read']['title']='video-operator: synthetic edit'
        self.assertFalse(s.verify_worker_admission(p,self.bundle)['complete'])

    def test_priority_order_untouched_while_marker_is_fixed(self):
        self.f.set_ids([self.f.old,self.f.worker,self.f.manager])
        self.bundle['priority_section_ids']=[self.f.sid];before=copy.deepcopy(self.f.keys())
        p=s.plan_worker_admission(self.bundle)
        self.assertEqual([o['tool'] for o in p['operations']],['set_thread_title'])
        self.bundle['title_read']['title']=p['display_title']
        self.assertTrue(s.verify_worker_admission(p,self.bundle)['complete'])
        self.assertEqual(self.f.keys(),before)

    def test_stale_or_foreign_title_is_refused(self):
        self.bundle['title_read']['thread_id']=self.f.old
        with self.assertRaises(ValueError):s.plan_worker_admission(self.bundle)
        self.bundle['title_read']['thread_id']=self.f.worker
        self.bundle['title_read']['observed_at']-=600
        with self.assertRaises(ValueError):s.plan_worker_admission(self.bundle)

    def test_existing_adoption_verifies_without_moving_older_siblings(self):
        self.f.set_ids([self.f.manager,self.f.old,self.f.worker])
        self.bundle['title_read']['title']='◆ video-operator: synthetic edit'
        before=copy.deepcopy(self.f.keys())
        result=s.verify_existing_worker_admission(self.bundle)
        self.assertTrue(result['complete']);self.assertEqual(result['operations'],[])
        self.assertEqual(self.f.keys(),before)

class CreationCommandTests(unittest.TestCase):
    def setUp(self):
        self.sandbox = tempfile.TemporaryDirectory(prefix='codexjarvis-creation-')
        self.addCleanup(self.sandbox.cleanup)
        self.home = Path(self.sandbox.name)
        self.root, self.manager, self.worker, self.project_id = (
            str(uuid.UUID(int=value)) for value in range(1, 5)
        )
        project = {'id': self.project_id, 'cwd': '/synthetic/Workspace'}
        directory = self.home / '.config' / 'codexjarvis'
        directory.mkdir(parents=True, mode=0o700)
        self.authority_path = directory / 'creation-authority.json'
        self.binding = {
            'create': {'thread_id': self.manager, 'project_id': self.project_id, 'host_id': 'local',
                       'environment': 'local', 'item_id': 'test-create'},
            'execution': {'thread_id': self.manager, 'runtime_session': self.manager,
                          'cwd': project['cwd'], 'item_id': 'test-execution'},
            'charter_accepted': True, 'charter_item_id': 'test-charter',
        }
        self.authority = {
            'version': 1, 'host': platform.node(), 'project': project, 'controller_id': self.root,
            'managers': {'edits': {'id': self.manager, 'controller_id': self.root,
                                   'placement_item_id': 'test-placement', 'binding': self.binding}},
            'pending': {},
        }
        self.save_authority()
        self.bundle = {
            'project': project, 'projects': [{**project, 'name': 'Workspace', 'host_id': 'local'}],
            'workstream': 'edits', 'title': '◆ ◆ synthetic edit', 'prompt': 'Finish requested edit.',
        }

    def save_authority(self):
        self.authority_path.write_text(json.dumps(self.authority))
        self.authority_path.chmod(0o600)

    def run_cli(self, operation, actor, session=None):
        bundle = self.home / 'request.json'
        bundle.write_text(json.dumps(self.bundle))
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('HERDR_', 'CMUX_', 'CODEX_'))}
        env.update(HOME=str(self.home), CODEX_THREAD_ID=actor, CODEX_SESSION_ID=session or actor)
        # Substitute only the OS account home in a synthetic subprocess.
        # Execute the real checkout entry point and its argument parser.
        harness = (
            'import pwd, runpy, sys, types; '
            'home = sys.argv.pop(1); '
            'pwd.getpwuid = lambda uid: types.SimpleNamespace(pw_dir=home); '
            'sys.argv.pop(0); runpy.run_path(sys.argv[0], run_name="__main__")'
        )
        return subprocess.run(
            [sys.executable, '-c', harness, str(self.home), str(Path(s.__file__)),
             '--operation', operation, '--bundle', str(bundle)],
            env=env, text=True, capture_output=True, timeout=5,
        )

    def test_worker_create_refuses_root_and_direct_worker_even_if_bundle_claims_owner(self):
        self.bundle.update(actor_kind='friday', actor_id=self.manager, owner_id=self.manager)
        for actor in (self.root, self.worker):
            with self.subTest(actor=actor):
                result = self.run_cli('worker-create', actor)
                self.assertNotEqual(0, result.returncode)
                self.assertIn('exact owning Friday', result.stderr)
                self.assertEqual('', result.stdout)

    def test_actual_owning_friday_emits_exact_saved_project_and_worker_marker(self):
        result = self.run_cli('worker-create', self.manager)
        self.assertEqual(0, result.returncode, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(self.manager, plan['owner_id'])
        self.assertEqual('◆ synthetic edit', plan['args']['title'])
        self.assertEqual(self.project_id, plan['args']['target']['projectId'])
        self.assertEqual({'type': 'local'}, plan['args']['target']['environment'])
        self.assertIn('every startup/resume rename', plan['args']['prompt'])
        self.bundle.update(model='synthetic-explicit-model', thinking='medium')
        self.assertNotEqual(0, self.run_cli('worker-create', self.manager).returncode)
        self.bundle['model_approval_ref'] = 'synthetic-human-model-request'
        self.assertEqual(self.bundle['model'], json.loads(self.run_cli('worker-create', self.manager).stdout)['args']['model'])

    def test_missing_or_mismatched_owner_project_or_runtime_proof_holds(self):
        self.assertNotEqual(0, self.run_cli('worker-create', self.manager, session=self.root).returncode)
        self.binding['execution']['cwd'] = '/invalid'
        self.save_authority()
        self.assertNotEqual(0, self.run_cli('worker-create', self.manager).returncode)
        self.authority_path.unlink()
        self.assertIn('authority unavailable', self.run_cli('worker-create', self.manager).stderr)

    def qualifying_missing_owner(self):
        self.bundle.update(workstream='new-edits', name='New edits', admission={
            'observed_at': time.time(), 'official_owner_lookup': 'test-current-lookup',
            'exact_owner_candidates': [], 'pending_candidates': [], 'scope_evidence': 'test-durable-backlog',
            'coherent_workstream': True, 'clear_routing_boundary': True,
            'independent_continuity': True, 'material_responsiveness_benefit': True,
        })

    def test_only_exact_root_can_emit_qualified_manager_create_arguments(self):
        self.qualifying_missing_owner()
        for actor in (self.worker, self.manager):
            with self.subTest(actor=actor):
                refused = self.run_cli('manager-create', actor)
                self.assertIn('Actual registered controller required', refused.stderr)
                self.assertEqual('', refused.stdout)
        plan = json.loads(self.run_cli('manager-create', self.root).stdout)
        self.assertEqual('root_manager_create_draft', plan['stage'])
        self.assertEqual('Friday: New edits', plan['args']['title'])
        self.assertEqual(self.project_id, plan['args']['target']['projectId'])

    def test_one_off_reuses_exact_owner_or_holds_and_pending_keeps_exact_id(self):
        reuse = json.loads(self.run_cli('manager-create', self.root).stdout)
        self.assertEqual({'stage': 'reuse', 'manager_id': self.manager, 'complete': False}, reuse)
        self.qualifying_missing_owner()
        self.bundle['admission']['independent_continuity'] = False
        self.assertEqual('hold', json.loads(self.run_cli('manager-create', self.root).stdout)['stage'])
        self.authority['pending']['new-edits'] = self.worker
        self.save_authority()
        resume = json.loads(self.run_cli('manager-create', self.root).stdout)
        self.assertEqual({'stage': 'resume_pending', 'manager_id': self.worker, 'complete': False}, resume)


if __name__ == '__main__':
    unittest.main()
