import json
from pathlib import Path
import subprocess
import unittest

import task_board as board
import test_task_watch as fixtures


class OwnerBoardTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TrackingTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_read_only_snapshot_preserves_policy_facts_claims_and_owners(self):
        self.fixture.fresh()
        before = fixtures.w.status(self.fixture.root)
        value = board.snapshot(self.fixture.root)
        path = Path(board.write(self.fixture.root))
        after = fixtures.w.status(self.fixture.root)
        self.assertEqual(before, after)
        self.assertEqual(path.name, 'board-owners.html')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(value['task']), 2)
        text = path.read_text()
        self.assertIn('2 tracked chats', text)
        self.assertIn('coordinator stream', text)
        self.assertIn('data-owner="' + fixtures.HELPER + '"', text)
        self.assertIn('data-owner="' + fixtures.ROOT + '"', text)

    def test_native_activity_is_not_inferred_from_semantic_working_or_completed(self):
        data = board.snapshot(self.fixture.root)
        task = next(t for t in data['task'] if t['thread'] == fixtures.WORKER)
        state = next(s for s in data['task_state'] if s['thread'] == fixtures.WORKER)
        state['state'] = 'completed'
        task['error'] = 'Unconfirmed native shadow'
        text = board.render(data)
        self.assertIn('Unconfirmed source', text)
        self.assertIn('state-completed', text)
        self.assertIn('Owner judgment', text)
        self.assertNotIn('Observed active turn</span>', text)
        task['error'] = None
        state['state'] = 'working'
        self.assertIn('Completed turn</span>', board.render(data))
        state['state'] = 'paused'
        captured = json.loads(task['baseline'])
        captured[0]['native_status'] = 'inProgress'
        task['baseline'] = json.dumps(captured)
        self.assertIn('Observed active turn</span>', board.render(data))
        self.assertIn('state-paused', board.render(data))
        task['checked'] = 1
        self.assertIn('Stale observation</span>', board.render(data))

    def test_pauses_resume_conditions_blockers_and_next_actions_stay_visible(self):
        data = board.snapshot(self.fixture.root)
        state = data['task_state'][0]
        state.update(state='paused', reason='Explicit pause', resume_condition='Human supplies recording',
                     next_action='Review exact draft after recording')
        text = board.render(data)
        self.assertIn('Human supplies recording', text)
        self.assertIn('Review exact draft after recording', text)
        self.assertIn('value="blocked"', text)
        self.assertIn('value="waiting_for_owner"', text)

    def test_untrusted_metadata_is_escaped_and_page_has_no_network_or_action_forms(self):
        data = board.snapshot(self.fixture.root)
        data['task'][0]['charter'] = '<script>alert("name")</script>'
        data['task_state'][0]['next_action'] = '<img src=x onerror=alert(1)>'
        text = board.render(data)
        self.assertNotIn('<script>alert', text)
        self.assertIn('&lt;script&gt;', text)
        self.assertIn('&lt;img', text)
        self.assertIn("default-src 'none'", text)
        self.assertNotIn('<form', text)
        self.assertNotIn('fetch(', text)
        self.assertNotIn('innerHTML', text)
        self.assertIn('Snapshot', text)
        self.assertIn('regenerate', text)

    def test_javascript_filters_owner_state_and_native_without_changing_data(self):
        text = board.render(board.snapshot(self.fixture.root))
        script = text.split('<script>')[1].split('</script>')[0]
        harness = '''
const vm = require('vm');
const make = (state,native) => ({dataset:{state,native},hidden:false});
const a=make('paused','active'), b=make('review_ready','completed'), c=make('blocked','unknown');
const groups=[{dataset:{owner:'root'},querySelectorAll:()=>[a,b]}, {dataset:{owner:'helper'},querySelectorAll:()=>[c]}];
const nodes={owner:{value:'',addEventListener(){}},state:{value:'',addEventListener(){}},native:{value:'',addEventListener(){}},
  reset:{addEventListener(name,handler){this.click=handler}},visible:{textContent:''},empty:{hidden:true}};
const context={document:{getElementById:id=>nodes[id],querySelectorAll:()=>groups},setInterval:()=>{},console};
vm.createContext(context); vm.runInContext(SCRIPT,context);
nodes.owner.value='helper'; nodes.state.value='blocked'; vm.runInContext('applyFilters()',context);
if (!groups[0].hidden || groups[1].hidden || c.hidden || nodes.visible.textContent!=='1 chat shown') throw Error('owner/state filter');
nodes.native.value='active'; vm.runInContext('applyFilters()',context);
if (nodes.empty.hidden || nodes.visible.textContent!=='0 chats shown') throw Error('empty filter');
nodes.reset.click();
if (nodes.visible.textContent!=='3 chats shown' || a.hidden || b.hidden || c.hidden) throw Error('reset');
'''.replace('SCRIPT', json.dumps(script))
        result = subprocess.run(['node', '-e', harness], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_output_cannot_replace_original_board_or_database(self):
        with self.assertRaises(ValueError):
            board.write(self.fixture.root, self.fixture.root / 'board.html')
        with self.assertRaises(ValueError):
            board.write(self.fixture.root, self.fixture.root / 'tracking.sqlite')


if __name__ == '__main__':
    unittest.main()
