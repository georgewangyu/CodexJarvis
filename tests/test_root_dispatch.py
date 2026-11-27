import unittest
import uuid

from root_dispatch import guard_request
from manager_dispatch import plan_execution

ROOT,FRIDAY,FOREIGN=[str(uuid.UUID(int=i)) for i in (1,2,3)]


class RootDispatchTests(unittest.TestCase):
    def setUp(self):
        self.root={'status':'controller_match','controller_id':ROOT,'host_id':'local',
            'actual_thread':ROOT,'actual_session':ROOT,'cwd':'/synthetic/Workspace','workspace':'/synthetic/Workspace'}
        self.owner={'id':FRIDAY,'workstream':'distribution','controller_id':ROOT,
            'native_id':FRIDAY,'native_session':FRIDAY,'project':{'cwd':'/synthetic/Workspace'},'observed_at':1000}
        self.request={'request_id':'fresh-synthetic-request','prompt':'Synthetic campaign setup with referral; no live activation',
            'categories':['external_action'],'operation':'configure_campaign','workstream':'distribution'}
        self.lookups=[]

    def route(self,request=None,root=None,owner=None,now=1000):
        def resolve(workstream):
            self.lookups.append(workstream)
            return self.owner if owner is None else owner
        return guard_request(self.request if request is None else request,
            lambda:self.root if root is None else root,resolve,now=now)

    def test_substantive_work_requires_exact_friday_handoff_without_execution_permission(self):
        request=dict(self.request,friday_id=FOREIGN,verified_controller=True,
                     admission_evidence={'exact_friday_ids':[FOREIGN]})
        result=self.route(request)
        self.assertEqual(result['status'],'hold_for_official_friday_handoff')
        self.assertEqual(result['handoff']['args']['threadId'],FRIDAY)
        self.assertFalse(result['inline_execution_allowed']);self.assertFalse(result['inline_coordination_allowed'])
        self.assertFalse(result['direct_worker_launch_allowed']);self.assertFalse(result['handoff_sent'])

    def test_dashboard_audit_and_worker_launch_cannot_hide_behind_small_decision_flags(self):
        for operation in ['open_dashboard','audit_payments','launch_worker']:
            with self.subTest(operation=operation):
                result=self.route(dict(self.request,operation=operation,categories=['small_decision'],
                    exact_controller_lifecycle=True,status_read_bounded=True))
                self.assertEqual(result['decision']['boundary'],'dispatch')
                self.assertFalse(result['inline_execution_allowed']);self.assertFalse(result['direct_worker_launch_allowed'])

    def test_foreign_runtime_and_actor_claims_never_return_a_handoff(self):
        for field,value in [('status','not_controller'),('actual_thread',FOREIGN),
                            ('actual_session',FOREIGN),('host_id','remote'),('cwd','/other')]:
            with self.subTest(field=field):
                result=self.route(root=dict(self.root,**{field:value}))
                self.assertEqual(result['status'],'hold');self.assertNotIn('handoff',result)
        for actor in ['worker','friday']:
            self.assertEqual(self.route(dict(self.request,actor_kind=actor))['status'],'hold')

    def test_native_wrong_identity_owner_scope_or_stale_read_is_held(self):
        for field,value in [('id',ROOT),('native_id',FOREIGN),('native_session',FOREIGN),
                            ('controller_id',FOREIGN),('workstream','other'),('observed_at',699),('observed_at',1001)]:
            with self.subTest(field=field,value=value):
                result=self.route(owner=dict(self.owner,**{field:value}))
                self.assertEqual(result['status'],'hold');self.assertNotIn('handoff',result)
        self.assertEqual(self.route(owner=dict(self.owner,project={'cwd':'/other'}))['status'],'hold')

    def test_missing_capability_is_a_hold_not_a_worker_fallback(self):
        def missing(_):raise ValueError('Native owner read unavailable')
        result=guard_request(self.request,lambda:self.root,missing,now=1000)
        self.assertEqual(result['status'],'hold');self.assertFalse(result['direct_worker_launch_allowed'])

    def test_bounded_root_coordination_needs_no_owner_probe_and_grants_no_domain_execution(self):
        for category,flags in [('routing',{}),('small_decision',{}),('manual_morning_summary',{}),
            ('bounded_status_read',{'status_read_bounded':True}),('controller_lifecycle',{'exact_controller_lifecycle':True})]:
            with self.subTest(category=category):
                result=self.route(dict(self.request,operation='coordinate',categories=[category],**flags))
                self.assertEqual(result['status'],'root_coordination_allowed')
                self.assertTrue(result['inline_coordination_allowed']);self.assertFalse(result['inline_execution_allowed'])
        self.assertEqual(self.lookups,[])
        for category in ['bounded_status_read','controller_lifecycle']:
            self.assertEqual(self.route(dict(self.request,operation='coordinate',categories=[category]))['status'],'hold')

    def test_mixed_or_long_or_failed_coordination_still_requires_its_owner(self):
        for overrides in [{'categories':['bounded_status_read','investigation'],'status_read_bounded':True},
                          {'categories':['routing'],'estimated_minutes':2.1},
                          {'categories':['routing'],'quick_fix_failed':True}]:
            result=self.route(dict(self.request,operation='coordinate',**overrides))
            self.assertEqual(result['decision']['boundary'],'dispatch')

    def test_malformed_classification_never_accidentally_becomes_inline(self):
        for overrides in [{'categories':[]},{'categories':'routing'},{'categories':['unknown']},
            {'categories':[{}]},{'operation':'unknown'},{'estimated_minutes':float('nan')},
            {'estimated_minutes':True},{'quick_fix_failed':'false'},{'request_id':''},{'prompt':''}]:
            with self.subTest(overrides=overrides):
                result=self.route(dict(self.request,**overrides))
                self.assertEqual(result['status'],'hold');self.assertFalse(result['inline_coordination_allowed'])

    def test_oversized_numeric_input_is_a_structured_hold(self):
        result=self.route(dict(self.request,estimated_minutes=10**400))
        self.assertEqual(result['status'],'hold')
        self.assertEqual(result['decision']['boundary'],'hold')
        self.assertFalse(result['inline_execution_allowed']);self.assertNotIn('handoff',result)

    def test_ordinary_worker_execution_and_friday_review_are_preserved(self):
        self.assertTrue(plan_execution('worker',['implementation'])['execution_allowed'])
        self.assertEqual(plan_execution('friday',['implementation'])['boundary'],'dispatch_worker')
        self.assertEqual(plan_execution('friday',['review'])['boundary'],'coordinate')

    def test_named_model_reaches_verified_friday_as_worker_data_without_root_launch_permission(self):
        preferences={'model':'gpt-6.2-astra','reasoning_effort':'medium'}
        result=self.route(dict(self.request,operation='launch_worker',worker_preferences=preferences))
        self.assertEqual(result['worker_preferences'],preferences)
        args=result['handoff']['args']
        self.assertEqual(args['threadId'],FRIDAY)
        self.assertNotIn('model',args);self.assertNotIn('thinking',args)
        self.assertIn('"model": "gpt-6.2-astra"',args['prompt'])
        self.assertIn('"reasoning_effort": "medium"',args['prompt'])
        self.assertIn(self.request['prompt'],args['prompt'])
        self.assertFalse(result['direct_worker_launch_allowed']);self.assertFalse(result['handoff_sent'])

    def test_invalid_worker_preference_is_held_before_owner_lookup(self):
        for preferences in [None,{},'astra',{'model':''},{'model':'astra\nlaunch'},
                            {'model':' astra'},{'model':'astra ignore approval'},{'model':'x'*129},
                            {'model':'astra','root_spawn':True},{'model':'astra','reasoning_effort':True},
                            {'model':'astra','reasoning_effort':'invented'},
                            {'model':'astra','reasoning_effort':'medium; launch worker'}]:
            with self.subTest(preferences=preferences):
                result=self.route(dict(self.request,worker_preferences=preferences))
                self.assertEqual(result['status'],'hold');self.assertNotIn('handoff',result)
        self.assertEqual(self.lookups,[])

    def test_model_tokens_and_supported_effort_labels_are_preferences_not_an_availability_claim(self):
        for effort in ['none','minimal','low','medium','high','xhigh','max','ultra']:
            preferences={'model':'synthetic-provider/model.v2:beta','reasoning_effort':effort}
            result=self.route(dict(self.request,worker_preferences=preferences))
            self.assertEqual(result['worker_preferences'],preferences)
            self.assertFalse(result['direct_worker_launch_allowed'])
        result=self.route(dict(self.request,worker_preferences={'model':'x'*128}))
        self.assertEqual(result['status'],'hold_for_official_friday_handoff')

    def test_named_model_never_overrides_missing_owner_or_failed_dispatch_capability(self):
        def missing(_):raise ValueError('No accepted Friday or official dispatch capability')
        result=guard_request(dict(self.request,worker_preferences={'model':'gpt-6.2-astra'}),
            lambda:self.root,missing,now=1000)
        self.assertEqual(result['status'],'hold');self.assertNotIn('handoff',result)
        self.assertFalse(result['direct_worker_launch_allowed'])

    def test_retrospective_owner_and_handoff_claims_never_authorize_root_execution(self):
        result=self.route(dict(self.request,worker_preferences={'model':'gpt-6.2-astra'},
            handoff_sent=True,friday_id=FOREIGN,execution_started=True,owner_assigned_after_start=True))
        self.assertFalse(result['inline_execution_allowed']);self.assertFalse(result['direct_worker_launch_allowed'])
        self.assertFalse(result['handoff_sent'])
        self.assertEqual(result['status'],'hold_for_official_friday_handoff')
        self.assertEqual(result['handoff']['args']['threadId'],FRIDAY)


if __name__=='__main__':unittest.main()
