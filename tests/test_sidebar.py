import copy
import json
from pathlib import Path
import unittest

import sidebar as s


class SidebarTests(unittest.TestCase):
    def setUp(self):
        self.bundle = json.loads((Path(__file__).parents[1] / 'examples/sidebar-bundle.json').read_text())
        self.config = self.bundle['config']
        self.work = self.config['workstreams'][0]
        self.manager = self.work['manager']['id']
        self.worker = self.work['workers'][0]

    def plan(self):
        return s.build_plan(**self.bundle)

    def test_plan_has_complete_order_inverse_and_idempotent_marker(self):
        result = self.plan()
        self.assertEqual(result['mode'], 'plan_only')
        sid = self.work['section_id']
        self.assertEqual(result['section_orders'][sid], [s.PREFIX+self.manager, s.PREFIX+self.worker])
        self.assertEqual(result['before_orders'][sid], list(reversed(result['section_orders'][sid])))
        self.assertEqual(result['ownership'], {self.worker:self.manager})
        self.assertEqual(result['operations'][0]['args']['title'], '◆ research-operator: sample study')
        for section in self.bundle['inventory']['sections']:
            section['itemKeys'] = result['section_orders'].get(section['sectionId'], section['itemKeys'])
        self.bundle['inventory']['threads'][1]['title'] = '◆ research-operator: sample study'
        self.assertEqual(self.plan()['operations'], [])
        self.assertEqual(self.plan()['section_orders'], {})
        self.assertEqual(s.worker_title('◆ ◆ example'), '◆ example')

    def test_every_binding_field_and_actual_execution_identity_is_required(self):
        binding = self.bundle['bindings'][self.manager]
        for group, key in [('create','project_id'), ('create','thread_id'), ('create','host_id'),
                           ('create','environment'), ('create','item_id'), ('execution','cwd'),
                           ('execution','runtime_session'), ('execution','item_id')]:
            old = binding[group][key]; binding[group][key] = 'wrong'
            # Evidence pointers are opaque but must be nonempty; their official source is verified manually.
            if key == 'item_id': binding[group][key] = ''
            with self.assertRaises(ValueError): self.plan()
            binding[group][key] = old

    def test_summary_cwd_cannot_replace_execution_cwd(self):
        binding = self.bundle['bindings'][self.manager]
        binding['summary_cwd'] = self.config['project']['cwd']
        binding['execution']['cwd'] = '/'
        with self.assertRaises(ValueError): self.plan()

    def test_missing_project_or_foreign_host_holds(self):
        for field, value in [('id', self.manager), ('cwd','/'), ('host_id','remote'), ('name','Other')]:
            b = copy.deepcopy(self.bundle); b['projects'][0][field] = value
            with self.assertRaises(ValueError): s.build_plan(**b)

    def test_layout_cannot_bypass_root_only_manager_creation(self):
        self.work['manager'] = None
        self.bundle['inventory']['sections'][0]['itemKeys'].remove(s.PREFIX+self.manager)
        result = self.plan()
        self.assertEqual(result['operations'], [])
        self.assertEqual(result['section_orders'], {})
        pending = result['pending_creation'][0]
        self.assertEqual('hold', pending['stage'])
        self.assertEqual('manager-create', pending['next_operation'])
        self.assertNotIn('args', pending)
        self.assertIsNone(result['ownership'][self.worker])

    def test_priority_worker_keeps_location_and_logical_owner(self):
        sections = self.bundle['inventory']['sections']
        sections[0]['itemKeys'].remove(s.PREFIX+self.worker)
        sections[2]['itemKeys'].append(s.PREFIX+self.worker)
        result = self.plan()
        self.assertFalse(any(o['tool']=='move_thread_to_sidebar_section' for o in result['operations']))
        self.assertNotIn(sections[2]['sectionId'], result['section_orders'])
        self.assertEqual(result['ownership'][self.worker], self.manager)

    def test_references_is_never_revived_or_given_manager(self):
        sections = self.bundle['inventory']['sections']
        sections[0]['itemKeys'].remove(s.PREFIX+self.worker)
        sections[1]['itemKeys'].append(s.PREFIX+self.worker)
        with self.assertRaisesRegex(ValueError, 'References'): self.plan()
        self.work['section_id'] = self.config['references_section_id']
        with self.assertRaises(ValueError): self.plan()

    def test_protected_recurring_and_pinned_workers_preserved(self):
        for kind in ('protected_thread_ids','recurring_thread_ids'):
            self.config[kind] = [self.worker]
            result = self.plan()
            self.assertEqual(len(result['holds']), 1)
            self.assertEqual(result['operations'], [])
            self.config[kind] = []
        self.bundle['inventory']['pinnedThreads'].append({'id':self.worker})
        self.assertEqual(self.plan()['operations'], [])

    def test_manager_cycles_overlap_unknown_members_and_ambiguous_inventory_refused(self):
        original = copy.deepcopy(self.bundle)
        for mutation in ('cycle','duplicate_owner','unknown','duplicate_key','section_duplicate'):
            self.bundle = copy.deepcopy(original); self.config=self.bundle['config']; self.work=self.config['workstreams'][0]
            if mutation == 'cycle': self.work['workers'].append(self.manager)
            elif mutation == 'duplicate_owner': self.work['workers'].append(self.worker)
            elif mutation == 'unknown': self.work['workers']=[]
            elif mutation == 'duplicate_key': self.bundle['inventory']['sections'][1]['itemKeys'].append(s.PREFIX+self.worker)
            else: self.bundle['inventory']['sections'].append(copy.deepcopy(self.bundle['inventory']['sections'][0]))
            with self.assertRaises(ValueError): self.plan()

    def test_controller_cannot_own_itself_as_a_pinned_direct_worker(self):
        root=self.config['controller']['id']
        self.config['direct_workers']=[root]
        self.bundle['inventory']['sections'].append({'sectionId':'pinned','name':'Pinned',
            'itemKeys':[s.PREFIX+root]})
        self.bundle['inventory']['threads'].append({'id':root,'title':'Jarvis'})
        with self.assertRaisesRegex(ValueError,'controller'): self.plan()

    def test_full_orders_preserve_project_rows_and_unrelated_history(self):
        section = self.bundle['inventory']['sections'][0]
        section['itemKeys'].insert(0,'codex:project:synthetic')
        result = self.plan()
        self.assertEqual(result['section_orders'][section['sectionId']][0], 'codex:project:synthetic')
        self.assertNotIn(self.config['references_section_id'],result['section_orders'])

    def test_six_workstreams_keep_one_owner_each_and_direct_priority_worker(self):
        from uuid import UUID
        def synthetic(n): return str(UUID(int=(1 << 126) + n))
        for n in range(1,6):
            mid,wid,sid=map(synthetic,(100*n,100*n+1,100*n+2))
            binding=copy.deepcopy(self.bundle['bindings'][self.manager])
            binding['create']['thread_id']=mid
            binding['execution'].update(thread_id=mid,runtime_session=mid)
            self.bundle['bindings'][mid]=binding
            self.config['workstreams'].append(dict(key='sample'+str(n),name='Sample '+str(n),
                section_id=sid,manager={'id':mid,'title':'Friday: Sample '+str(n)},workers=[wid]))
            self.bundle['inventory']['sections'].append(dict(sectionId=sid,name='Sample '+str(n),
                itemKeys=[s.PREFIX+wid,s.PREFIX+mid]))
            self.bundle['inventory']['threads'].extend([{'id':mid,'title':'Friday: Sample '+str(n)},
                                                       {'id':wid,'title':'sample worker'}])
        direct=synthetic(999)
        self.config['direct_workers']=[direct]
        self.bundle['inventory']['sections'][2]['itemKeys'].append(s.PREFIX+direct)
        self.bundle['inventory']['threads'].append({'id':direct,'title':'direct worker'})
        result=self.plan()
        self.assertEqual(len(result['charters']),6)
        self.assertEqual(len(result['ownership']),7)
        self.assertEqual(result['ownership'][direct],self.config['controller']['id'])
        self.assertEqual(len(result['section_orders']),6)
        self.assertTrue(any(o['args'].get('title')=='◆ direct worker' for o in result['operations']))


if __name__ == '__main__':
    unittest.main()
