import copy
import json
import unittest

import incoming_results as incoming
import task_watch as w
import test_incoming_results as fixtures


class MultipleOutputsTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.IncomingResultsTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.second=self.f.workspace/'second-cut.mp4';self.second.write_bytes(b'other-video')
        self.packet=copy.deepcopy(self.f.packet)
        self.packet['artifact_links'].append(str(self.second))
        self.packet['review_artifacts']=[self.packet['review_artifact'],w.local_artifact(str(self.second),str(self.f.workspace))]
        self.judgment=copy.deepcopy(self.f.bundle['judgment'])
        self.judgment['artifact_paths']=[str(self.f.artifact),str(self.second)]

    def plan(self):
        return incoming.plan_result(self.packet,self.f.fact,self.f.bundle['source_read'],self.judgment,str(self.f.workspace))

    def test_both_requested_cuts_are_verified_and_linked(self):
        result=self.plan();self.assertEqual(result['disposition'],'surface')
        self.assertEqual(len(result['artifacts']),2);self.assertIn(str(self.second),result['reply'])

    def test_renamed_completed_video_review_preserves_human_pickups_and_dedup(self):
        self.packet['task_title']='◆ video-operator: current nighttime edit'
        self.judgment.update(state='review_ready',summary='Nighttime V02 is available. Full listening remains unverified; the CTA needs a pickup.')
        result=self.plan()
        self.assertEqual(result['disposition'],'surface');self.assertFalse(result['objective_completion_verified'])
        self.assertEqual(result['deliverable_state'],'review_ready')
        self.assertIn('listening remains unverified',result['reply']);self.assertIn('CTA needs a pickup',result['reply'])
        incoming.claim(self.f.root,result)
        self.packet['task_title']='◆ video-operator: refined nighttime title'
        self.assertEqual(self.plan()['presentation_id'],result['presentation_id'])
        self.assertEqual(incoming.claim(self.f.root,self.plan())['disposition'],'quiet')

    def test_second_only_revision_is_a_new_presentation(self):
        first=self.plan();incoming.claim(self.f.root,first)
        self.second.write_bytes(b'new-other-video')
        self.packet['review_artifacts'][1]=w.local_artifact(str(self.second),str(self.f.workspace))
        second=self.plan()
        self.assertNotEqual(first['presentation_id'],second['presentation_id'])
        self.assertEqual(incoming.claim(self.f.root,second)['disposition'],'surface')

    def test_changed_secondary_without_new_capture_is_verification_gap(self):
        self.second.write_bytes(b'changed-after-capture')
        result=self.plan();self.assertEqual(result['kind'],'verification_gap')
        self.assertNotIn('artifact',result);self.assertNotIn('Open deliverable',result['reply'])

    def test_unbound_extra_output_holds_instead_of_inheriting_primary_readiness(self):
        self.judgment['artifact_paths'].append('/outside/unverified.mp4')
        self.assertEqual(self.plan()['disposition'],'hold')


if __name__=='__main__':unittest.main()
