import copy
import json
from pathlib import Path
import time
import unittest
import uuid

import sidebar as s


class WorkerPlacementTests(unittest.TestCase):
    def setUp(self):
        sample=json.loads((Path(__file__).parents[1]/'examples/sidebar-bundle.json').read_text())
        config=sample['config'];work=config['workstreams'][0]
        self.manager=work['manager']['id'];self.worker=work['workers'][0]
        self.old,self.extra,self.later=[str(uuid.uuid4()) for _ in range(3)]
        self.sid=work['section_id']
        self.bundle={'inventory':{'sections':[{'sectionId':self.sid,'name':'Work',
            'itemKeys':[s.PREFIX+t for t in [self.manager,self.old]]}]},
            'observed_at':time.time(),'official_item':'synthetic-inventory',
            'section_id':self.sid,'manager_id':self.manager,'worker_id':self.worker,
            'priority_section_ids':[],'binding_evidence':'synthetic-saved-project-execution-proof',
            'owner_evidence':'synthetic-accepted-manager-charter'}

    def keys(self):return self.bundle['inventory']['sections'][0]['itemKeys']

    def set_ids(self,ids):self.bundle['inventory']['sections'][0]['itemKeys']=[s.PREFIX+t for t in ids]

    def apply_move(self,operation):
        args=operation['args'];key=s.PREFIX+args['threadId'];keys=self.keys()
        if key in keys:keys.remove(key)
        # Reproduces the official observed default-prepend behavior, without UI.
        before=args.get('beforeThreadId')
        offset=next((i for i,k in enumerate(keys) if k.rsplit(':',1)[-1]==before),0) if before else 0
        keys.insert(offset,key)

    def test_explicit_anchor_prevents_observed_default_prepend_regression(self):
        old={'args':{'threadId':self.worker,'sectionId':self.sid}}
        self.apply_move(old)
        self.assertEqual(self.keys()[0],s.PREFIX+self.worker)  # Actual old failure.
        self.set_ids([self.manager,self.old])
        plan=s.plan_worker_placement(self.bundle)
        self.assertEqual(plan['operations'][0]['args']['beforeThreadId'],self.old)
        self.apply_move(plan['operations'][0])
        self.assertTrue(s.reconcile_worker_placement(plan,self.bundle,verify=True)['complete'])
        self.assertEqual(self.keys(),[s.PREFIX+t for t in [self.manager,self.worker,self.old]])

    def test_single_manager_is_compound_and_not_complete_from_move_receipt(self):
        self.set_ids([self.manager]);plan=s.plan_worker_placement(self.bundle)
        self.assertTrue(plan['compound_required']);self.assertFalse(plan['complete'])
        self.apply_move(plan['operations'][0])
        self.assertFalse(s.reconcile_worker_placement(plan,self.bundle,verify=True)['complete'])
        reconciliation=s.reconcile_worker_placement(plan,self.bundle)
        self.assertEqual(reconciliation['operations'][0]['args']['threadIds'],[self.manager,self.worker])
        self.assertTrue(s.check_worker_reorder(reconciliation,self.bundle)['ready'])
        self.set_ids([self.manager,self.worker])
        self.assertTrue(s.reconcile_worker_placement(plan,self.bundle,verify=True)['complete'])

    def test_empty_managed_section_holds_until_existing_manager_verified(self):
        self.set_ids([]);plan=s.plan_worker_placement(self.bundle)
        self.assertEqual(plan['stage'],'hold');self.assertEqual(plan['operations'],[])

    def test_already_moved_above_manager_reconciles_only_related_prefix(self):
        self.set_ids([self.worker,self.manager,self.old,self.extra])
        plan=s.plan_worker_placement(self.bundle)
        self.assertEqual(plan['operations'],[])
        result=s.reconcile_worker_placement(plan,self.bundle)
        self.assertEqual(result['operations'][0]['args']['threadIds'],
                         [self.manager,self.worker,self.old,self.extra])

    def test_concurrent_addition_preserved_and_stale_reorder_refused(self):
        plan=s.plan_worker_placement(self.bundle)
        self.set_ids([self.extra,self.worker,self.manager,self.old])
        result=s.reconcile_worker_placement(plan,self.bundle)
        self.assertEqual(result['operations'][0]['args']['threadIds'],
                         [self.manager,self.worker,self.extra,self.old])
        self.keys().append(s.PREFIX+self.later)
        self.assertFalse(s.check_worker_reorder(result,self.bundle)['ready'])
        current=s.reconcile_worker_placement(plan,self.bundle)
        self.assertEqual(current['operations'][0]['args']['threadIds'][-1],self.later)

    def test_unrelated_manual_reorder_or_removal_is_not_overwritten(self):
        self.set_ids([self.manager,self.old,self.extra]);plan=s.plan_worker_placement(self.bundle)
        for ids in ([self.worker,self.manager,self.extra,self.old],[self.worker,self.manager,self.old]):
            self.set_ids(ids)
            self.assertEqual(s.reconcile_worker_placement(plan,self.bundle)['stage'],'hold')

    def test_priority_queue_keeps_explicit_human_order(self):
        self.bundle['priority_section_ids']=[self.sid]
        before=copy.deepcopy(self.keys());result=s.plan_worker_placement(self.bundle)
        self.assertEqual(result['stage'],'preserve_priority');self.assertEqual(result['operations'],[])
        self.assertEqual(self.keys(),before)

    def test_project_and_other_backing_rows_are_preserved(self):
        other=str(uuid.uuid4());keys=self.keys();keys.insert(0,'codex:project:synthetic')
        keys.append('chatgpt:conversation:'+other)
        plan=s.plan_worker_placement(self.bundle);self.apply_move(plan['operations'][0])
        self.assertTrue(s.reconcile_worker_placement(plan,self.bundle,verify=True)['complete'])
        self.assertIn('codex:project:synthetic',self.keys())
        self.assertEqual(self.keys()[-1],'chatgpt:conversation:'+other)

    def test_stale_or_unbound_inputs_and_nonlocal_manager_refused(self):
        original=copy.deepcopy(self.bundle)
        for field,value in [('observed_at',time.time()-600),('official_item',True),
                            ('binding_evidence',True),('owner_evidence','')]:
            self.bundle=copy.deepcopy(original);self.bundle[field]=value
            with self.assertRaises(ValueError):s.plan_worker_placement(self.bundle)
        self.bundle=copy.deepcopy(original)
        self.keys()[0]='codex:thread:remote:'+self.manager
        self.assertEqual(s.plan_worker_placement(self.bundle)['stage'],'hold')

    def test_pre_intent_inventory_cannot_verify_later_placement(self):
        plan=s.plan_worker_placement(self.bundle)
        self.set_ids([self.manager,self.worker,self.old])
        self.bundle['observed_at']=plan['planned_at']-1
        self.assertEqual(s.reconcile_worker_placement(plan,self.bundle,verify=True)['stage'],'hold')

    def test_bulk_layout_also_emits_anchor_and_required_reconciliation(self):
        sample=json.loads((Path(__file__).parents[1]/'examples/sidebar-bundle.json').read_text())
        work=sample['config']['workstreams'][0];new=str(uuid.uuid4())
        sample['inventory']['sections'][0]['itemKeys']=[s.PREFIX+self.manager,s.PREFIX+self.worker]
        work['workers'].append(new)
        sample['inventory']['sections'].append({'sectionId':'threads','name':'Tasks','itemKeys':[s.PREFIX+new]})
        sample['inventory']['threads'].append({'id':new,'title':'new worker'})
        result=s.build_plan(**sample)
        move=next(o for o in result['operations'] if o['tool']=='move_thread_to_sidebar_section')
        self.assertEqual(move['args']['beforeThreadId'],self.worker)
        self.assertEqual(result['placement_followups'][0]['worker_id'],new)


if __name__=='__main__':unittest.main()
