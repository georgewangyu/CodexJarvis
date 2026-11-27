#!/usr/bin/env python3
"""Pure sidebar planner. Inputs are saved official evidence; output never executes it."""
import argparse
import json
import os
from pathlib import Path
import platform
import pwd
import stat
import uuid
import time

PREFIX = 'codex:thread:local:'


def uid(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError('Exact canonical UUID required')
    return value


def worker_title(title):
    if not isinstance(title, str):
        raise ValueError('Worker title must be text')
    title = title.strip()
    while title.startswith('◆'):
        title = title[1:].lstrip()
    if not title:
        raise ValueError('Worker title missing')
    return '◆ ' + title


def validate_binding(project, thread, binding):
    """Check operator-attested official evidence, not cryptographic vendor identity."""
    create, execution = binding.get('create', {}), binding.get('execution', {})
    if (create.get('thread_id') != thread or create.get('project_id') != project['id']
            or create.get('host_id') != 'local' or create.get('environment') != 'local'
            or not create.get('item_id') or execution.get('thread_id') != thread
            or execution.get('runtime_session') != thread
            or execution.get('cwd') != project['cwd'] or not execution.get('item_id')
            or binding.get('charter_accepted') is not True or not binding.get('charter_item_id')):
        raise ValueError('Exact saved-project creation, actual execution cwd and accepted charter required')


def build_plan(config, inventory, bindings, projects):
    if config.get('version') != 1 or config.get('host_id') != 'local':
        raise ValueError('Version 1 and local host required')
    project = config['project']; uid(project['id'])
    if not Path(project['cwd']).is_absolute():
        raise ValueError('Absolute saved Workspace cwd required')
    matches = [p for p in projects if p.get('id') == project['id']]
    if (len(matches) != 1 or matches[0].get('name') != 'Workspace'
            or matches[0].get('cwd') != project['cwd'] or matches[0].get('host_id') != 'local'):
        raise ValueError('Exact saved local Workspace project inventory required')
    root = uid(config['controller']['id'])
    refs = uid(config['references_section_id'])
    sections = inventory['sections']
    section_ids = [s['sectionId'] for s in sections]
    if len(set(section_ids)) != len(section_ids):
        raise ValueError('Duplicate section identity')
    by_id = {s['sectionId']: s for s in sections}
    if refs not in by_id or by_id[refs]['name'] != 'References':
        raise ValueError('Existing exact References section required')
    if sum(s.get('name') == 'References' for s in sections) != 1:
        raise ValueError('Ambiguous References destination')
    priority = set(config.get('priority_section_ids', []))
    preserved = set(config.get('preserved_section_ids', [])) | priority | {refs, 'pinned', 'threads', 'chats'}
    if not priority <= set(by_id):
        raise ValueError('Priority section missing')
    pinned = {t['id'] for t in inventory.get('pinnedThreads', [])}
    protected = set(config.get('protected_thread_ids', [])) | set(config.get('recurring_thread_ids', [])) | {root}
    for tid in protected | set(config.get('direct_workers', [])):
        uid(tid)
    titles = {t['id']: t['title'] for t in inventory.get('threads', [])}
    locations, keys = {}, set()
    for section in sections:
        items = section['itemKeys']
        if not isinstance(items, list) or not all(isinstance(k, str) for k in items) or len(items) != len(set(items)):
            raise ValueError('Complete distinct string itemKeys required')
        for key in items:
            if key in keys:
                raise ValueError('Ambiguous cross-section membership')
            keys.add(key)
            if key.startswith(PREFIX):
                locations[uid(key[len(PREFIX):])] = section['sectionId']
    workstreams = config['workstreams']
    if len(workstreams) > 8:
        raise ValueError('At most eight explicitly configured managers')
    owners, manager_ids, work_sections, work_keys = {}, set(), set(), set()
    direct = set(config.get('direct_workers', []))
    if root in direct:
        raise ValueError('The controller cannot be its own direct worker')
    for work in workstreams:
        sid = uid(work['section_id'])
        if sid not in by_id or sid in preserved or sid in work_sections or work['key'] in work_keys:
            raise ValueError('Distinct existing workstream sections and keys required')
        work_sections.add(sid); work_keys.add(work['key'])
        manager = work.get('manager')
        if manager:
            mid = uid(manager['id'])
            if not isinstance(manager.get('title'), str) or not manager['title'].lstrip('◆ ').strip():
                raise ValueError('Manager title missing')
            if mid in manager_ids or mid in protected or mid in pinned or mid in direct:
                raise ValueError('Manager conflicts with another role or protected/pinned task')
            manager_ids.add(mid)
        for tid in work['workers']:
            uid(tid)
            if tid in owners or tid == root or tid in direct:
                raise ValueError('Each ordinary worker has exactly one explicit owner')
            owners[tid] = manager['id'] if manager else None
    if manager_ids & set(owners):
        raise ValueError('Nested manager ownership refused')
    if any(sid not in preserved | work_sections for sid in by_id):
        raise ValueError('Unconfigured section: classify it or explicitly preserve it')
    declared = set(owners) | manager_ids | protected | direct | pinned
    for sid in work_sections:
        if any(k.startswith(PREFIX) and k[len(PREFIX):] not in declared for k in by_id[sid]['itemKeys']):
            raise ValueError('Unclassified workstream member; refresh private configuration')

    operations, pending, holds, charters, placement_followups = [], [], [], [], []
    desired = {sid: list(s['itemKeys']) for sid, s in by_id.items()}
    before = {sid: list(items) for sid, items in desired.items()}

    def place(tid, sid, manager=None):
        key = PREFIX + tid
        current = locations.get(tid)
        if current != sid:
            if current is not None:
                desired[current].remove(key)
            args = {'threadId': tid, 'hostId': 'local', 'source': 'codex', 'sectionId': sid}
            if manager:
                manager_key = PREFIX + manager
                offset = desired[sid].index(manager_key) + 1
                following = [k for k in desired[sid][offset:] if ':project:' not in k]
                if following:
                    args['beforeThreadId'] = _thread_id(following[0])
                desired[sid].insert(offset, key)
                placement_followups.append({'section_id': sid, 'manager_id': manager, 'worker_id': tid,
                    'required': 'Fresh post-move worker-reconcile, then worker-verify; move receipt alone is incomplete'})
            else:
                desired[sid].append(key)
            operations.append({'tool': 'move_thread_to_sidebar_section', 'args': args})

    def rename(tid, title):
        if tid not in titles:
            raise ValueError('Fresh official title evidence missing')
        if titles[tid] != title:
            operations.append({'tool': 'set_thread_title', 'args': {'threadId': tid, 'source': 'codex', 'title': title}})

    for work in workstreams:
        manager = work.get('manager')
        if not manager:
            pending.append({'workstream': work['key'], 'stage': 'hold',
                            'next_operation': 'manager-create',
                            'reason': 'Only the verified controller may request a manager creation draft'})
            continue  # Never place/route children using a guessed creation result.
        mid, sid = manager['id'], work['section_id']
        validate_binding(project, mid, bindings.get(mid, {}))
        if mid in locations and locations[mid] in preserved - {'threads', 'chats'}:
            raise ValueError('Manager in preserved section; explicit resolution required')
        place(mid, sid)
        rename(mid, manager['title'].lstrip('◆ ').strip())
        for tid in work['workers']:
            if tid not in locations:
                raise ValueError('Worker membership missing; no implicit dispatch or creation')
            if locations[tid] == refs:
                raise ValueError('Stale completed worker in configuration; never revive References')
            if tid in protected | pinned:
                holds.append({'thread': tid, 'reason': 'Protected, recurring or pinned; preserve placement and title'})
                continue
            if locations[tid] not in priority:
                place(tid, sid, manager=mid)
            rename(tid, worker_title(titles.get(tid, '')))
        # Preserve all project/remote items and relative order of remaining rows.
        items = desired[sid]; items.remove(PREFIX + mid)
        offset = next((i for i, key in enumerate(items) if ':project:' not in key), len(items))
        items.insert(offset, PREFIX + mid)
        charters.append({'workstream': work['key'], 'manager': mid, 'root': root,
                         'logical_members': work['workers'], 'template': 'templates/manager-charter.md'})
    for tid in sorted(direct):
        if tid not in locations or locations[tid] == refs:
            raise ValueError('Direct worker missing or stale in References')
        owners[tid] = root
        if tid in protected | pinned:
            holds.append({'thread': tid, 'reason': 'Protected, recurring or pinned; preserve placement and title'})
        else:
            rename(tid, worker_title(titles.get(tid, '')))
    changed = {sid: items for sid, items in desired.items() if items != before[sid]}
    return {'mode': 'plan_only', 'operations': operations, 'pending_creation': pending,
            'section_orders': changed, 'before_orders': {sid: before[sid] for sid in changed},
            'ownership': owners, 'charters': charters, 'holds': holds,
            'placement_followups': placement_followups,
            'requires': ['Fresh official inventory immediately before each mutation',
                         'Actual controller authorization, exact creation/execution proof before binding',
                         'Post-move full affected section orders and final membership verification',
                         'Separate accepted ownership CAS; placement does not activate reporting'],
            'delivery': 'record_only; no watcher, callback, dispatch or app mutation executed'}


def _thread_id(key):
    if key.startswith('codex:thread:') or key.startswith('chatgpt:conversation:'):
        return uid(key.rsplit(':', 1)[-1])
    raise ValueError('Unknown thread item key; preserve it for manual full-order handling')


def _placement_snapshot(bundle, now=None):
    now = time.time() if now is None else now
    observed, pointer = bundle.get('observed_at'), bundle.get('official_item')
    if (not isinstance(observed, (int, float)) or isinstance(observed, bool)
            or not 0 <= now-observed <= 300 or not isinstance(pointer, str) or not pointer.strip()):
        raise ValueError('Fresh official sidebar inventory required')
    sections = bundle['inventory']['sections']
    if len({s['sectionId'] for s in sections}) != len(sections):
        raise ValueError('Ambiguous sections')
    seen = set()
    for section in sections:
        keys = section['itemKeys']
        if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
            raise ValueError('Complete string itemKeys required')
        for key in keys:
            if key in seen:
                raise ValueError('Duplicate membership')
            seen.add(key)
    return {s['sectionId']: s for s in sections}


def _section_threads(section):
    threads = [_thread_id(k) for k in section['itemKeys']
               if ':project:' not in k and not k.startswith('project:')]
    if len(set(threads)) != len(threads):
        raise ValueError('Ambiguous thread identity across backing kinds/hosts')
    return threads


def plan_worker_placement(bundle, now=None):
    """One incremental placement; existing manager/roles come from private proof."""
    sections = _placement_snapshot(bundle, now)
    sid = uid(bundle['section_id'])
    if sid in bundle['priority_section_ids']:
        return {'stage': 'preserve_priority', 'reason': 'Priority queue has its own human-selected order', 'operations': []}
    for field in ('binding_evidence', 'owner_evidence'):
        if not isinstance(bundle.get(field), str) or not bundle[field].strip():
            raise ValueError('Verified project/execution binding and accepted owner evidence required')
    manager, worker = uid(bundle['manager_id']), uid(bundle['worker_id'])
    if manager == worker:
        raise ValueError('Manager cannot be its own ordinary worker')
    if sid not in sections:
        return {'stage': 'hold', 'reason': 'Existing destination missing', 'operations': []}
    threads = _section_threads(sections[sid])
    if PREFIX + manager not in sections[sid]['itemKeys']:
        return {'stage': 'hold', 'reason': 'Existing manager missing; place/verify it first', 'operations': []}
    plan = {'stage': 'reconcile_required' if worker in threads else 'move_required',
            'section_id': sid, 'manager_id': manager, 'worker_id': worker,
            'planned_at': bundle['observed_at'],
            'before_item_keys': list(sections[sid]['itemKeys']),
            'unrelated_before': [t for t in threads if t not in {manager, worker}],
            'complete': False, 'operations': [],
            'requires': ['Fresh post-move inventory', 'Complete fresh reorder when needed',
                         'Final official manager-first/worker-second membership proof']}
    if worker not in threads:
        args = {'threadId': worker, 'hostId': 'local', 'source': 'codex', 'sectionId': sid}
        following = [t for t in threads[threads.index(manager)+1:] if t != worker]
        if following:
            args['beforeThreadId'] = following[0]
        # The API has no afterThreadId. With only a manager, insertion is a
        # compound move+fresh reconciliation, never a completed placement.
        plan['operations'] = [{'tool': 'move_thread_to_sidebar_section', 'args': args}]
        plan['compound_required'] = not bool(following)
    return plan


def reconcile_worker_placement(plan, bundle, verify=False, now=None):
    sections = _placement_snapshot(bundle, now)
    if bundle['observed_at'] < plan['planned_at']:
        return {'stage': 'hold', 'reason': 'Inventory predates placement intent', 'complete': False}
    sid, manager, worker = plan['section_id'], plan['manager_id'], plan['worker_id']
    if sid not in sections:
        return {'stage': 'hold', 'reason': 'Destination changed', 'complete': False}
    threads = _section_threads(sections[sid])
    if any(PREFIX + t not in sections[sid]['itemKeys'] for t in (manager, worker)):
        return {'stage': 'hold', 'reason': 'Manager/worker move not verified', 'complete': False}
    unrelated = [t for t in threads if t not in {manager, worker}]
    prior = plan['unrelated_before']
    if [t for t in unrelated if t in prior] != prior:
        return {'stage': 'hold', 'reason': 'Unrelated rows removed or manually reordered; refresh intent', 'complete': False}
    desired = [manager, worker] + unrelated
    if threads == desired:
        return {'stage': 'verified', 'complete': True, 'thread_ids': threads,
                'official_item': bundle['official_item'], 'operations': []}
    if verify:
        return {'stage': 'hold', 'reason': 'Final order is not manager-first/worker-second', 'complete': False}
    return {'stage': 'reorder_required', 'complete': False,
            'observed_at': bundle['observed_at'],
            'precondition_item_keys': list(sections[sid]['itemKeys']),
            'section_id': sid, 'operations': [{'tool': 'reorder_section',
                'args': {'sectionId': sid, 'threadIds': desired}}],
            'requires': 'Compare precondition with a fresh inventory before applying; then final readback'}


def check_worker_reorder(reconciliation, bundle, now=None):
    sections = _placement_snapshot(bundle, now)
    section = sections.get(reconciliation['section_id'])
    valid = (bundle['observed_at'] >= reconciliation['observed_at'] and section is not None
             and section['itemKeys'] == reconciliation['precondition_item_keys'])
    return {'ready': valid, 'complete': False,
            'reason': 'Apply exact reviewed reorder' if valid else 'Stale order; reconcile fresh membership again'}


def _creation_project(bundle):
    project = bundle['project']
    uid(project['id'])
    projects = bundle['projects']
    if isinstance(projects, dict):
        projects = [{'id': p.get('projectId'), 'name': p.get('label'),
                     'cwd': p.get('path'), 'host_id': p.get('hostId')}
                    for p in projects['projects'] if p.get('projectKind') == 'local']
    matches = [p for p in projects if p.get('id') == project['id']]
    if (len(matches) != 1 or matches[0].get('name') != 'Workspace'
            or matches[0].get('host_id') != 'local' or matches[0].get('cwd') != project['cwd']
            or not Path(project['cwd']).is_absolute()):
        raise ValueError('Exact saved local Workspace inventory required before creation')
    return project


def _has_text(value):
    return isinstance(value, str) and bool(value.strip())


def _distinct_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate key in private creation authority')
        result[key] = value
    return result


def read_creation_authority():
    """Read the host's private adopted file, without a caller-selected path."""
    account_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    path = account_home / '.config' / 'codexjarvis' / 'creation-authority.json'
    try:
        parent = path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                or parent.st_mode & 0o077):
            raise ValueError('Private creation authority directory required')
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor) as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_mode & 0o077 or not 0 < info.st_size <= 262144):
                raise ValueError('Private regular creation authority file required')
            authority = json.load(stream, object_pairs_hook=_distinct_keys)
    except FileNotFoundError as exc:
        raise ValueError('Host-local creation authority unavailable; hold setup') from exc
    return authority


def _creation_authority(project):
    """Validate private operator-adopted authority against an exact project."""
    authority = read_creation_authority()
    if (not isinstance(authority, dict) or type(authority.get('version')) is not int
            or authority['version'] != 1 or authority.get('host') != platform.node()
            or authority.get('project') != project):
        raise ValueError('Creation authority for exact host and saved Workspace required')

    root = uid(authority.get('controller_id'))
    managers, pending = authority.get('managers'), authority.get('pending')
    if not isinstance(managers, dict) or not isinstance(pending, dict) or set(managers) & set(pending):
        raise ValueError('Unique accepted and pending workstream ownership required')
    ids = {root}
    for workstream, record in managers.items():
        if not isinstance(record, dict):
            raise ValueError('Accepted Friday record must be an object')
        manager_id = uid(record.get('id'))
        if (not _has_text(workstream) or manager_id in ids or record.get('controller_id') != root
                or not _has_text(record.get('placement_item_id'))):
            raise ValueError('Exact flat accepted Friday ownership and placement required')
        validate_binding(project, manager_id, record.get('binding', {}))
        ids.add(manager_id)
    for workstream, pending_id in pending.items():
        pending_id = uid(pending_id)
        if not _has_text(workstream) or pending_id in ids:
            raise ValueError('Duplicate pending manager identity')
        ids.add(pending_id)
    return root, managers, pending


def _current_desktop_actor():
    actor = uid(os.environ.get('CODEX_THREAD_ID'))
    if os.environ.get('CODEX_SESSION_ID') != actor or any(
            os.environ.get(key) for key in ('HERDR_ENV', 'HERDR_PANE_ID', 'HERDR_SOCKET_PATH',
                                          'CMUX_WORKSPACE_ID', 'CMUX_SURFACE_ID', 'CMUX_SOCKET_PATH')):
        raise ValueError('Matching current Desktop thread and runtime session required')
    return actor


def _create_target(project):
    return {'type': 'project', 'projectId': project['id'], 'environment': {'type': 'local'}}


def manager_create(bundle, now=None):
    """Draft one root-owned peer manager after independent ownership checks."""
    project = _creation_project(bundle)
    root, managers, pending = _creation_authority(project)
    if _current_desktop_actor() != root:
        raise ValueError('Actual registered controller required for new Friday creation')
    workstream = bundle.get('workstream')
    if not _has_text(workstream):
        raise ValueError('Exact workstream key required')
    if workstream in managers:
        return {'stage': 'reuse', 'manager_id': managers[workstream]['id'], 'complete': False}
    if workstream in pending:
        return {'stage': 'resume_pending', 'manager_id': pending[workstream], 'complete': False}
    admission = bundle.get('admission', {})
    if not isinstance(admission, dict):
        raise ValueError('Admission evidence must be an object')
    observed_at = admission.get('observed_at')
    now = time.time() if now is None else now
    if (type(observed_at) not in (int, float) or not 0 <= now - observed_at <= 300
            or not _has_text(admission.get('official_owner_lookup'))
            or admission.get('exact_owner_candidates') != [] or admission.get('pending_candidates') != []
            or not _has_text(admission.get('scope_evidence'))
            or admission.get('coherent_workstream') is not True
            or admission.get('clear_routing_boundary') is not True
            or admission.get('material_responsiveness_benefit') is not True
            or not (admission.get('recurring_coordination') is True
                    or admission.get('independent_continuity') is True)):
        return {'stage': 'hold', 'complete': False, 'reason': 'Current ownership and qualitative admission evidence required'}
    name, prompt = bundle.get('name'), bundle.get('prompt')
    if not _has_text(name) or name.strip().startswith(('Friday:', '◆')) or not _has_text(prompt):
        raise ValueError('Unprefixed workstream name and manager charter required')
    return {
        'stage': 'root_manager_create_draft', 'tool': 'create_thread', 'complete': False,
        'args': {'target': _create_target(project), 'title': 'Friday: ' + name.strip(), 'prompt': prompt},
        'next': 'Recheck native ownership, create once, and preserve the returned id before verification',
    }


def worker_create(bundle):
    """Draft native worker arguments only for the site's exact admitted Friday."""
    project = _creation_project(bundle)
    root, managers, _ = _creation_authority(project)
    actor = _current_desktop_actor()
    workstream = bundle.get('workstream')
    if not _has_text(workstream):
        raise ValueError('Exact workstream key required')
    manager = managers.get(workstream)
    if actor == root or manager is None or manager['id'] != actor:
        raise ValueError('Ordinary worker creation requires the exact owning Friday')
    title = worker_title(bundle['title'])
    prompt = bundle['prompt']
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('Worker brief required')
    result = {'tool': 'create_thread', 'owner_id': actor, 'workstream': workstream, 'args': {
        'target': _create_target(project),
        'title': title,
        'prompt': ('Ordinary managed worker: keep the ◆ display prefix exactly once on every '
                   'startup/resume rename; your logical agent name remains unchanged.\n\n' + prompt)},
        'complete': False, 'next': 'Verify execution binding and exact References membership; root registers when applicable'}
    if 'model' in bundle or 'thinking' in bundle:
        if not isinstance(bundle.get('model_approval_ref'), str) or not bundle['model_approval_ref'].strip():
            raise ValueError('Explicit human model/effort request required')
        for key in ('model','thinking'):
            if key in bundle:
                if not isinstance(bundle[key],str) or not bundle[key].strip():
                    raise ValueError('Exact requested model/effort must be text')
                result['args'][key] = bundle[key]
    return result


def _worker_title_read(bundle, worker, now):
    read = bundle['title_read']
    observed = read.get('observed_at')
    now = time.time() if now is None else now
    if (read.get('thread_id') != worker or type(observed) not in (int, float)
            or not 0 <= now - observed <= 300
            or not isinstance(read.get('official_item'), str) or not read['official_item'].strip()
            or not isinstance(read.get('title'), str) or not read['title'].strip()):
        raise ValueError('Fresh exact official worker title required')
    return read['title']


def plan_worker_admission(bundle, now=None):
    worker = uid(bundle['worker_id'])
    for field in ('binding_evidence', 'owner_evidence'):
        if not isinstance(bundle.get(field), str) or not bundle[field].strip():
            raise ValueError('Verified project/execution and ownership evidence required')
    current = _worker_title_read(bundle, worker, now)
    plan = plan_worker_placement(bundle, now)
    if plan['stage'] == 'hold':
        return plan
    plan.update(worker_id=worker, display_title=worker_title(current),
                title_observed_at=bundle['title_read']['observed_at'])
    if plan['stage'] == 'preserve_priority':
        section = _placement_snapshot(bundle, now)[bundle['section_id']]
        plan.update(section_id=bundle['section_id'], before_item_keys=list(section['itemKeys']),
                    planned_at=bundle['observed_at'], complete=False)
    if current != plan['display_title']:
        plan['operations'].insert(0, {'tool': 'set_thread_title',
                                     'args': {'threadId': worker, 'title': plan['display_title']}})
    return plan


def verify_worker_admission(plan, bundle, now=None):
    current = _worker_title_read(bundle, plan['worker_id'], now)
    if (bundle['title_read']['observed_at'] < plan['title_observed_at']
            or current != plan['display_title']):
        return {'stage': 'hold', 'complete': False, 'reason': 'Worker display title not verified'}
    if plan['stage'] == 'preserve_priority':
        sections = _placement_snapshot(bundle, now)
        section = sections.get(plan['section_id'])
        valid = (bundle['observed_at'] >= plan['planned_at'] and section is not None
                 and section['itemKeys'] == plan['before_item_keys']
                 and PREFIX + plan['worker_id'] in section['itemKeys'])
        return {'stage': 'verified' if valid else 'hold', 'complete': valid,
                'reason': 'Priority order preserved' if valid else 'Priority membership/order changed'}
    return reconcile_worker_placement(plan, bundle, verify=True, now=now)


def verify_existing_worker_admission(bundle, now=None):
    """Read-only adoption of already-placed workers; do not move each to row two."""
    worker = uid(bundle['worker_id'])
    current = _worker_title_read(bundle, worker, now)
    sections = _placement_snapshot(bundle, now)
    for field in ('binding_evidence','owner_evidence'):
        if not isinstance(bundle.get(field),str) or not bundle[field].strip():
            raise ValueError('Exact retained binding and owner proof required')
    section = sections.get(bundle['section_id'])
    if section is None or PREFIX+worker not in section['itemKeys'] or current != worker_title(current):
        return {'complete':False,'stage':'hold','reason':'Current marked title/membership not verified'}
    priority = bundle['section_id'] in bundle['priority_section_ids']
    threads = _section_threads(section)
    if not priority and bundle.get('manager_id') == worker:
        raise ValueError('Manager cannot be admitted as its own ordinary worker')
    if not priority and (not bundle.get('manager_id') or threads[0] != uid(bundle['manager_id'])):
        return {'complete':False,'stage':'hold','reason':'Accepted manager is not first'}
    return {'complete':True,'stage':'verified','worker_id':worker,'title':current,
            'official_item':bundle['official_item'],'operations':[],
            'priority_order_preserved':priority,'thread_ids':threads}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True,
                        help='Private JSON containing config, inventory, bindings and normalized projects')
    parser.add_argument('--operation', choices=['layout', 'worker-create', 'manager-create', 'worker-admit', 'worker-admit-verify', 'existing-admit-verify', 'worker-plan', 'worker-reconcile', 'worker-verify', 'worker-check'], default='layout')
    args = parser.parse_args()
    bundle = json.loads(args.bundle.read_text())
    if args.operation == 'existing-admit-verify':
        result = verify_existing_worker_admission(bundle)
    elif args.operation == 'worker-create':
        result = worker_create(bundle)
    elif args.operation == 'manager-create':
        result = manager_create(bundle)
    elif args.operation == 'worker-admit':
        result = plan_worker_admission(bundle)
    elif args.operation == 'worker-admit-verify':
        result = verify_worker_admission(bundle['plan'], bundle)
    elif args.operation == 'layout':
        result = build_plan(bundle['config'], bundle['inventory'], bundle['bindings'], bundle['projects'])
    elif args.operation == 'worker-plan':
        result = plan_worker_placement(bundle)
    elif args.operation == 'worker-check':
        result = check_worker_reorder(bundle['reconciliation'], bundle)
    else:
        result = reconcile_worker_placement(bundle['plan'], bundle, verify=args.operation == 'worker-verify')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (OSError, KeyError, ValueError) as exc:
        raise SystemExit(f'codexjarvis-sidebar: {exc}') from exc
