"""Read-only owner/state snapshot view of the existing task-watch ledger.

No native API calls, model calls, queue attempts, policy writes or background loop.
Run again to refresh the HTML; browser controls only filter the saved snapshot.
"""
import argparse
from collections import Counter
from datetime import datetime
import html
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from zoneinfo import ZoneInfo

from cooperate import absolute


STATES = {'working': 'Working', 'queued': 'Queued', 'waiting_for_owner': 'Waiting for you',
          'waiting_external': 'Waiting external', 'review_ready': 'Ready for review',
          'paused': 'Paused', 'blocked': 'Blocked', 'completed': 'Completed', 'unknown': 'Unknown'}


def snapshot(root):
    root = absolute(root)
    path = absolute(root / 'tracking.sqlite')
    if (root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077
            or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077):
        raise ValueError('Existing private owned tracking ledger required')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        data = {table: [dict(r) for r in db.execute('SELECT * FROM ' + table)]
                for table in ('policy', 'task', 'task_state', 'decision')}
        data['fact'] = [dict(r) for r in db.execute("SELECT f.* FROM fact f WHERE rowid IN "
            "(SELECT MAX(rowid) FROM fact WHERE COALESCE(json_extract(source, "
            "'$.capture_context.source_is_latest_at_capture'),1)!=0 GROUP BY thread)")]
        data['delivery'] = {}
        for fact in data['fact']:
            actors = {r[0] for r in db.execute('SELECT actor FROM judgment WHERE fact=?', (fact['id'],))}
            data['delivery'][fact['id']] = [
                {'recipient': r['recipient'], 'state': 'acknowledged' if r['recipient'] in actors else r['state']}
                for r in db.execute('SELECT recipient,state FROM notice WHERE fact=?', (fact['id'],))]
        return data
    finally:
        db.rollback()
        db.close()


def native_activity(task, fact, now, interval):
    if task.get('error') or task.get('gap'):
        return 'unknown', 'Unconfirmed source', 'Reporting is held; no current activity is established.'
    if not task.get('checked') or now - task['checked'] > interval * 2:
        return 'unknown', 'Stale observation', 'A fresh successful read is required.'
    source = json.loads(fact['source']) if fact else None
    if source is None:
        baseline = json.loads(task.get('baseline') or '[]')
        source = next((v for v in baseline if v['turn'] == task['watermark']), {})
    native = source.get('native_status')
    if native == 'inProgress':
        return 'active', 'Observed active turn', 'Last captured native turn; not a CPU or objective-completion claim.'
    if native == 'completed':
        return 'completed', 'Completed turn', 'Turn completion does not establish objective completion.'
    return 'unknown', 'Unknown activity', 'Native activity is not established by the captured source.'


def coverage(task, policy, now, interval):
    if not policy['enabled']:
        return 'Disabled'
    if now >= policy['expires']:
        return 'Lease expired'
    if task.get('error'):
        return 'Source held: ' + task['error']
    if task.get('gap'):
        return 'History gap; watermark held'
    if task.get('baseline') is None:
        return 'Not baselined'
    if not task.get('checked') or now - task['checked'] > interval * 2:
        return 'Stale; no fresh successful read'
    return 'Connected; recent supported read'


def render(data, now=None, timezone='UTC'):
    now = time.time() if now is None else now
    policy = data['policy'][0]
    spec = json.loads(policy['spec'])
    interval = spec.get('interval', 30)
    stamp = lambda value: datetime.fromtimestamp(value, ZoneInfo(timezone)).strftime('%Y-%m-%d %H:%M %Z')
    esc = lambda value: html.escape(str(value if value is not None else '—'), quote=True)
    names = {spec['root_thread']: spec.get('root_name', 'Jarvis')}
    names.update({h['thread']: h.get('name', h['workstream']) for h in spec.get('helpers', [])})
    helper_ids = {h['thread'] for h in spec.get('helpers', [])}
    states = {s['thread']: s for s in data['task_state']}
    metadata = {t['thread']: t for t in spec['tasks']}
    facts = {f['thread']: f for f in data['fact']}
    groups = {owner: [] for owner in names}
    counts = Counter()
    for task in data['task']:
        state = states[task['thread']]
        fact = facts.get(task['thread'])
        kind, native, explanation = native_activity(task, fact, now, interval)
        counts[kind] += 1
        current_coverage = coverage(task, policy, now, interval)
        decisions = [json.loads(d['value'])['reason'] for d in data['decision']
                     if d['thread'] == task['thread'] and d['state'] == 'open']
        deliveries = data['delivery'].get(fact['id'], []) if fact else []
        delivery = '; '.join(names.get(n['recipient'], 'Owner') + ': ' + n['state'] for n in deliveries)
        title = metadata.get(task['thread'], {}).get('title', task['charter'])
        role = 'Coordinator stream' if task['thread'] in helper_ids else 'Ordinary chat'
        details = '<details><summary>Reason and evidence</summary><p>' + esc(state['reason']) + '</p>'
        details += '<p><strong>Resume condition:</strong> ' + esc(state.get('resume_condition') or 'None recorded') + '</p>'
        details += '<p><strong>Open decisions:</strong> ' + esc('; '.join(decisions) or 'None recorded; not proof of none') + '</p>'
        details += '<p><strong>Delivery:</strong> ' + esc(delivery or 'No result notice recorded') + '</p>'
        details += '<p><strong>Accepted state updated:</strong> ' + esc(
            stamp(state['updated']) if state.get('updated') else 'Baseline classification') + '</p>'
        details += '<p><strong>Generation:</strong> ' + esc(fact['id'] if fact else 'Baseline only') + '</p>'
        source_path = Path(fact.get('path', '')) if fact else None
        if (source_path and source_path.is_absolute() and source_path.parent.name == 'facts'
                and source_path.name == fact['id'] + '.json'):
            details += '<p><a href="' + esc(source_path.as_uri()) + '">Captured source facts</a></p>'
        details += '</details>'
        row = ('<tr data-task data-state="' + esc(state['state']) + '" data-native="' + kind + '">'
               '<td><a href="codex://threads/' + esc(task['thread']) + '">' + esc(title) + '</a>'
               '<small>' + role + '</small>' + details + '</td>'
               '<td><span data-native-label>' + native + '</span><small>' + explanation + '</small></td>'
               '<td><span class="badge state-' + esc(state['state']) + '">' + esc(STATES.get(state['state'], state['state'])) + '</span></td>'
               '<td>' + esc(state.get('next_action') or 'Owner classification required') + '</td>'
               '<td><span data-checked="' + str(task.get('checked') or 0) + '">' + esc(current_coverage) + '</span></td></tr>')
        groups.setdefault(task['owner'], []).append((row, task['thread'] in helper_ids))
    options = lambda values: ''.join('<option value="' + esc(k) + '">' + esc(v) + '</option>' for k, v in values)
    body = '<header><h1>Work board</h1><p>Owner groups from the existing policy. Codex chats remain peers.</p></header>'
    body += '<p class="snapshot">Snapshot · ' + esc(stamp(now))
    body += ' · ' + str(len(data['task'])) + ' tracked chats · ' + str(counts['active']) + ' observed active · ' + str(counts['unknown']) + ' unconfirmed or stale</p>'
    body += '<p class="note">Native activity, owner judgment and reporting coverage are separate. This saved page does not refresh the ledger; regenerate it for new data. Filters only change this view.</p>'
    body += '<div class="filters"><label>Owner<select id="owner"><option value="">All owners</option>' + options(names.items()) + '</select></label>'
    body += '<label>Task state<select id="state"><option value="">All task states</option>' + options(STATES.items()) + '</select></label>'
    body += '<label>Native activity<select id="native"><option value="">All observations</option>' + options([('active', 'Observed active'), ('completed', 'Completed turn'), ('unknown', 'Unconfirmed or stale')]) + '</select></label>'
    body += '<button id="reset" type="button">Reset filters</button><span id="visible" role="status" aria-live="polite"></span></div>'
    for index, (owner, rows) in enumerate(groups.items()):
        ordinary = sum(not helper for _, helper in rows)
        coordinator = len(rows) - ordinary
        body += '<section data-owner="' + esc(owner) + '" aria-labelledby="group-' + str(index) + '"><h2 id="group-' + str(index) + '">' + esc(names.get(owner, 'Registered owner'))
        body += '<span>' + str(ordinary) + (' ordinary chat' if ordinary == 1 else ' ordinary chats') + (' · ' + str(coordinator) + (' coordinator stream' if coordinator == 1 else ' coordinator streams') if coordinator else '') + '</span></h2>'
        body += '<div class="scroll"><table><thead><tr><th>Work</th><th>Native activity <small>Last captured</small></th><th>Task state <small>Owner judgment</small></th><th>Next action</th><th>Reporting coverage</th></tr></thead><tbody>'
        body += ''.join(row for row, _ in rows) + '</tbody></table></div></section>'
    body += '<p id="empty" hidden>No chats match these filters. Reset filters to see the full snapshot.</p>'
    body += '<footer>Read-only view. No approvals, messages, enrollment or ownership changes. Reloading this file preserves the same snapshot until it is regenerated.</footer>'
    return ('''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Work board · owners and states</title><style>
:root{--ink:#17232d;--muted:#536474;--paper:#f4f6f8;--line:#d7dfe6;--accent:#155d93;--surface:#fff;--held:#765208}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.5 system-ui,sans-serif}main{max-width:1600px;margin:auto;padding:24px}
h1{font-size:26px;margin:0}header p,.snapshot{margin:4px 0 12px;color:var(--muted)}.note,footer{font-size:13px;color:var(--muted);max-width:1000px}
.filters{display:flex;gap:16px;align-items:end;flex-wrap:wrap;margin:24px 0}label{display:flex;flex-direction:column;gap:4px;font-weight:600}select,button{font:inherit;padding:8px 12px;border:1px solid var(--line);border-radius:4px;background:var(--surface);color:var(--ink)}button{cursor:pointer}
:focus-visible{outline:3px solid var(--accent);outline-offset:3px}section{margin:24px 0}h2{font-size:19px;margin:0 0 8px}h2 span{font-size:13px;font-weight:400;color:var(--muted);margin-left:12px}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:4px}table{width:100%;border-collapse:collapse;background:var(--surface);min-width:850px}th,td{text-align:left;padding:12px;vertical-align:top;border-bottom:1px solid var(--line)}th{font-size:13px;background:var(--paper)}th:first-child{width:30%}th:nth-child(4){width:28%}td{font-size:14px}tbody tr:last-child td{border-bottom:0}
a{color:var(--accent)}small{display:block;font-size:12px;color:var(--muted);font-weight:400;margin-top:4px}.badge{display:inline-block;padding:2px 7px;background:var(--paper);border:1px solid var(--line);border-radius:4px;white-space:nowrap}.state-blocked,.state-paused,.state-waiting_for_owner{color:var(--held)}details{margin-top:10px;font-size:13px}summary{cursor:pointer;color:var(--accent)}details p{margin:8px 0;overflow-wrap:anywhere}[hidden]{display:none!important}#visible{font-size:13px;color:var(--muted)}footer{margin-top:24px}
@media(max-width:600px){main{padding:16px}.filters{gap:12px}label{flex:1;min-width:140px}h2 span{display:block;margin:4px 0 0}}
</style></head><body><main>''' + body + '''</main><script>
const controls = ['owner','state','native'].map(id => document.getElementById(id));
function applyFilters() {
  const [owner,state,native] = controls.map(control => control.value);
  let visible = 0;
  document.querySelectorAll('section[data-owner]').forEach(group => {
    let count = 0;
    group.querySelectorAll('tr[data-task]').forEach(row => {
      const show = (!owner || group.dataset.owner === owner) && (!state || row.dataset.state === state) && (!native || row.dataset.native === native);
      row.hidden = !show; if (show) count++;
    });
    group.hidden = count === 0; visible += count;
  });
  const counter = document.getElementById('visible'), message = visible + (visible === 1 ? ' chat shown' : ' chats shown');
  if (counter.textContent !== message) counter.textContent = message;
  document.getElementById('empty').hidden = visible !== 0;
}
controls.forEach(control => control.addEventListener('change', applyFilters));
document.getElementById('reset').addEventListener('click', () => { controls.forEach(control => control.value = ''); applyFilters(); });
applyFilters();
setInterval(() => {
  document.querySelectorAll('[data-checked]').forEach(cell => {
    if (Date.now()/1000 - Number(cell.dataset.checked) > ''' + str(interval * 2) + ''') {
      if (cell.textContent.startsWith('Connected')) cell.textContent = 'Stale; no fresh successful read';
      const row = cell.closest('tr[data-task]');
      if (row.dataset.native !== 'unknown') { row.dataset.native = 'unknown'; row.querySelector('[data-native-label]').textContent = 'Stale observation'; }
    }
  });
  applyFilters();
}, 1000);
</script></body></html>''')


def write(root, output=None, timezone='UTC'):
    root = absolute(root).resolve()
    output = absolute(Path(output)).resolve() if output else root / 'board-owners.html'
    if (output.suffix.lower() != '.html' or output.parent.stat().st_uid != os.getuid()
            or output.parent.stat().st_mode & 0o077):
        raise ValueError('HTML output in an existing private owned directory required')
    # The original board remains owned by its running renderer.
    if output == root / 'board.html':
        raise ValueError('Use the complementary owner view; do not replace the running board')
    page = render(snapshot(root), timezone=timezone)
    fd, temporary = tempfile.mkstemp(prefix='.board-owners-', dir=output.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(page)
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return str(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--timezone', default='UTC')
    args = parser.parse_args()
    print(write(args.root, args.output, args.timezone))
