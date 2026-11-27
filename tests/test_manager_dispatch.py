import json
from pathlib import Path
import subprocess
import sys
import unittest

import manager_dispatch as m


class ManagerDispatchTests(unittest.TestCase):
    def test_friday_cli_requires_worker_dispatch_and_missing_capability_hold(self):
        r=subprocess.run([sys.executable,str(Path(m.__file__)),'--actor','friday','--category','implementation'],capture_output=True,text=True,check=True)
        plan=json.loads(r.stdout);self.assertEqual(plan['boundary'],'dispatch_worker')
        self.assertFalse(plan['execution_allowed']);self.assertIn('Hold',plan['missing_capability'])

    def test_review_and_reporting_are_coordination_but_substantive_work_is_not(self):
        self.assertEqual(m.plan_execution('friday',['review','reporting'])['boundary'],'coordinate')
        for category in ['research','production','debugging']:
            self.assertEqual(m.plan_execution('friday',[category])['boundary'],'dispatch_worker')
        self.assertTrue(m.plan_execution('worker',['implementation'])['execution_allowed'])
        self.assertEqual(m.plan_execution('friday',['unknown'])['boundary'],'hold')


if __name__=='__main__':unittest.main()
