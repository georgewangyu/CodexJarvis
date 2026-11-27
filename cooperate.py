"""Private worker reports; record-only default and explicitly opted-in delivery.

This is cooperative reporting, never evidence of native Desktop completion.
The local user is trusted; environment identity is an accident guard, not auth.
"""
import argparse
from contextlib import closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import signal
import sqlite3
import subprocess
import sys
import time
import uuid

MAX_LEASE = 6 * 3600
STATUSES = ('needs_input', 'ready', 'failed')


def uid(value):
    return str(uuid.UUID(value))


def absolute(value):
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise ValueError('Expected absolute non-symlink path')
    if any(p.is_symlink() for p in path.parents):
        raise ValueError('Symlink path component')
    return path


@contextmanager
def connect(root, read_only=False):
    root = absolute(root)
    if not root.is_dir() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
        raise ValueError('Session root must be owned and private (0700)')
    database = root / 'outbox.sqlite'
    if database.is_symlink() or not database.is_file():
        raise ValueError('Missing session database')
    db = (sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=5)
          if read_only else sqlite3.connect(database, timeout=5))
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


def settings(db, now=None):
    row = db.execute('SELECT * FROM session').fetchone()
    now = time.time() if now is None else now
    if row['stopped'] or not row['created'] <= now < row['expires']:
        raise ValueError('Session stopped, expired, or clock moved backwards')
    return row


def initialize(root, target, queue_binary, lease):
    root = absolute(root)
    target = uid(target)
    binary = absolute(queue_binary)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError('Queue executable unavailable')
    if not 1 <= lease <= MAX_LEASE:
        raise ValueError('Lease must be 1..21600 seconds')
    # No resume or historical drain: every session requires a fresh directory.
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    now = time.time()
    with closing(sqlite3.connect(root / 'outbox.sqlite')) as db:
        db.executescript('''
        CREATE TABLE session (id TEXT, target TEXT, binary TEXT, created REAL,
                              expires REAL, stopped INTEGER DEFAULT 0);
        CREATE TABLE assignment (id TEXT PRIMARY KEY, worker TEXT, runtime_session TEXT, created REAL,
                                 expires REAL, receipt TEXT);
        CREATE TABLE event (assignment TEXT, phase TEXT, status TEXT, created REAL,
                            digest TEXT, outcome TEXT, attempted REAL,
                            PRIMARY KEY(assignment, phase));
        ''')
        db.execute('INSERT INTO session VALUES (?,?,?,?,?,0)',
                   (str(uuid.uuid4()), target, str(binary), now, now + lease))
        db.commit()
    (root / 'outbox.sqlite').chmod(0o600)
    (root / 'receipts').mkdir(mode=0o700)
    set_record_only(root)


def register(root, worker, lease, runtime_session=None):
    worker = uid(worker)
    runtime_session = uid(runtime_session or worker)
    now = time.time()
    assignment = str(uuid.uuid4())
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        session = settings(db, now)
        if worker == session['target'] or not 1 <= lease <= MAX_LEASE:
            raise ValueError('Invalid worker or lease')
        receipt = absolute(root) / 'receipts' / (assignment + '.json')
        db.execute('INSERT INTO assignment VALUES (?,?,?,?,?,?)',
                   (assignment, worker, runtime_session, now,
                    min(now + lease, session['expires']), str(receipt)))
        if has_table(db, 'worker_owner'):
            owner = db.execute('SELECT * FROM worker_owner WHERE worker=?', (worker,)).fetchone()
            target, coordinator = session['target'], None
            if owner:
                stored = db.execute('SELECT spec, enabled FROM coordinator WHERE thread=?',
                                    (owner['coordinator'],)).fetchone()
                if stored['enabled']:
                    config = json.loads(stored['spec'])
                    if now >= config['expires'] or runtime_session != owner['runtime_session']:
                        raise ValueError('Coordinator expired or worker runtime mismatched')
                    target, coordinator = owner['coordinator'], owner['coordinator']
                    db.execute('UPDATE assignment SET expires=MIN(expires,?) WHERE id=?',
                               (config['expires'], assignment))
            db.execute('INSERT INTO assignment_route VALUES (?,?,?)', (assignment, target, coordinator))
        db.commit()
    return {'assignment': assignment, 'worker': worker, 'runtime_session': runtime_session,
            'receipt': str(receipt)}


def artifact_digest(path):
    path = absolute(path)
    if not path.is_file():
        raise ValueError('Receipt must be a regular file')
    with path.open('rb') as handle:
        data = handle.read(1048577)
    if len(data) > 1048576:
        raise ValueError('Receipt exceeds 1 MiB')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('Receipt must be a JSON object')
    return hashlib.sha256(data).hexdigest()


def preserve_snapshot(path, data):
    """Recover only identical bytes stranded before the report DB commit."""
    path = absolute(path)
    try:
        with path.open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o600)
    except FileExistsError:
        if (path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077
                or artifact_digest(path) != hashlib.sha256(data).hexdigest()):
            raise ValueError('Stranded snapshot differs or is not private; preserve it and use fresh scope')


def report(root, assignment, status):
    if status not in STATUSES:
        raise ValueError('Unknown report status')
    # Never accept caller-supplied worker identity or target arguments.
    worker = uid(os.environ.get('CODEX_THREAD_ID', ''))
    runtime_session = uid(os.environ.get('CODEX_SESSION_ID', ''))
    assignment = uid(assignment)
    now = time.time()
    phase = 'input' if status == 'needs_input' else 'final'
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        settings(db, now)
        row = db.execute('SELECT * FROM assignment WHERE id=?', (assignment,)).fetchone()
        if (row is None or worker != row['worker'] or runtime_session != row['runtime_session']
                or not row['created'] <= now < row['expires']):
            raise ValueError('Unregistered, foreign or expired assignment')
        snapshot = absolute(root) / 'receipts' / (assignment + '-' + phase + '.json')
        previous = db.execute('SELECT status,digest FROM event WHERE assignment=? AND phase=?',
                              (assignment, phase)).fetchone()
        if previous:
            if previous['status'] != status:
                raise ValueError('Conflicting final status')
            if artifact_digest(snapshot) != previous['digest']:
                raise ValueError('Recorded snapshot changed; reconcile integrity before proceeding')
            later_final = phase == 'input' and db.execute(
                "SELECT digest FROM event WHERE assignment=? AND phase='final'", (assignment,)).fetchone()
            # The current receipt may legitimately contain the later final.
            # Otherwise changed payloads are NEW work, not duplicate success.
            allowed = {previous['digest']}
            if later_final:
                final_snapshot = absolute(root) / 'receipts' / (assignment + '-final.json')
                if artifact_digest(final_snapshot) != later_final['digest']:
                    raise ValueError('Recorded final snapshot changed; reconcile integrity')
                allowed.add(later_final['digest'])
            if (absolute(row['receipt']).exists()
                    and artifact_digest(row['receipt']) not in allowed):
                raise ValueError('Consumed phase has a new receipt; register a fresh assignment')
            return {'assignment': assignment, 'status': status, 'result': 'duplicate', 'delivery': 'unchanged'}
        if phase == 'input' and db.execute(
                "SELECT 1 FROM event WHERE assignment=? AND phase='final'", (assignment,)).fetchone():
            raise ValueError('Assignment already final')
        digest = artifact_digest(row['receipt'])
        # Snapshot the receipt so later updates cannot change what an event attests.
        data = absolute(row['receipt']).read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('Receipt changed during report')
        preserve_snapshot(snapshot, data)
        config = policy(db)
        visible = config['mode'] == 'visible' and now >= config['changed']
        delivery = 'visible' if visible else 'record_only'
        db.execute('INSERT INTO event VALUES (?,?,?,?,?,?,NULL)',
                   (assignment, phase, status, now, digest,
                    'pending' if visible else 'recorded_only'))
        stage_parent_fact(db, assignment, phase, status, digest)
        db.commit()
    return {'assignment': assignment, 'status': status, 'result': 'reported',
            'delivery': delivery}


def policy(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='delivery_policy'").fetchone():
        return {'mode': 'record_only', 'changed': 0, 'prior_max_rowid': 0}
    return db.execute('SELECT * FROM delivery_policy WHERE singleton=1').fetchone()


def has_table(db, name):
    return db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (name,)).fetchone() is not None


def assignment_owner(db, assignment):
    if has_table(db, 'assignment_route'):
        row = db.execute('SELECT target, coordinator FROM assignment_route WHERE assignment=?',
                         (assignment,)).fetchone()
        if row:
            return row['target'], row['coordinator']
    return db.execute('SELECT target FROM session').fetchone()[0], None


def source_fingerprint(event):
    return hashlib.sha256(json.dumps({k: event[k] for k in
        ('worker', 'assignment', 'phase', 'status', 'sha256')}, sort_keys=True).encode()).hexdigest()


def stage_parent_fact(db, assignment, phase, status, digest):
    """Publish durable facts transactionally, independently of model judgment."""
    target, coordinator = assignment_owner(db, assignment)
    if coordinator is None:
        return
    worker = db.execute('SELECT worker FROM assignment WHERE id=?', (assignment,)).fetchone()[0]
    event = {'worker': worker, 'assignment': assignment, 'phase': phase, 'status': status, 'sha256': digest}
    db.execute('INSERT OR IGNORE INTO parent_fact VALUES (?,?,?,?)',
               (coordinator, source_fingerprint(event), json.dumps(event, sort_keys=True), time.time()))


def configure_coordinator(root, spec):
    """Operator configuration, not controller promotion or a worker-chosen route."""
    required = {'name', 'thread', 'runtime_session', 'parent', 'scope', 'workers',
                'charter', 'backlog', 'expires', 'project_id', 'workspace', 'binding', 'binding_sha256'}
    if not isinstance(spec, dict) or set(spec) != required:
        raise ValueError('Incomplete coordinator configuration')
    thread, runtime, parent = (uid(spec[k]) for k in ('thread', 'runtime_session', 'parent'))
    if thread == parent or not spec['name'].strip() or not spec['scope'].strip():
        raise ValueError('Coordinator needs a distinct identity and charter scope')
    for path in ('charter', 'backlog'):
        if not absolute(spec[path]).is_file():
            raise ValueError('Missing private charter/backlog')
    if artifact_digest(spec['binding']) != spec['binding_sha256']:
        raise ValueError('Official binding receipt changed')
    binding = json.loads(absolute(spec['binding']).read_text())
    if (binding['thread_id'] != thread or binding['project_id'] != uid(spec['project_id'])
            or binding['environment'] != 'local' or binding['host_id'] != 'local'
            or binding['execution']['cwd'] != str(absolute(spec['workspace']))
            or binding['execution']['runtime_thread'] != thread
            or binding['execution']['runtime_session'] != runtime
            or not binding['execution']['item_id']):
        raise ValueError('Exact saved-project/execution binding is unverified')
    workers = spec['workers']
    if not isinstance(workers, list) or not 1 <= len(workers) <= 8:
        raise ValueError('Select a bounded direct worker group (1..8)')
    worker_ids = [uid(w['thread']) for w in workers]
    if len(set(worker_ids)) != len(worker_ids) or {thread, parent}.intersection(worker_ids):
        raise ValueError('Self, parent, duplicate or cyclic worker ownership')
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        session = settings(db)
        if parent != session['target'] or not time.time() < spec['expires'] <= session['expires']:
            raise ValueError('Parent or coordinator expiry exceeds session authority')
        db.execute('''CREATE TABLE IF NOT EXISTS coordinator
            (thread TEXT PRIMARY KEY, spec TEXT, enabled INTEGER DEFAULT 1)''')
        db.execute('''CREATE TABLE IF NOT EXISTS worker_owner
            (worker TEXT PRIMARY KEY, runtime_session TEXT, coordinator TEXT)''')
        db.execute('''CREATE TABLE IF NOT EXISTS assignment_route
            (assignment TEXT PRIMARY KEY, target TEXT, coordinator TEXT)''')
        db.execute('''CREATE TABLE IF NOT EXISTS parent_fact
            (coordinator TEXT, fingerprint TEXT, event TEXT, created REAL,
             PRIMARY KEY(coordinator,fingerprint))''')
        db.execute('''CREATE TABLE IF NOT EXISTS relay_claim
            (coordinator TEXT, fingerprint TEXT, parent_assignment TEXT,
             PRIMARY KEY(coordinator,fingerprint))''')
        previous = db.execute('SELECT spec FROM coordinator WHERE thread=?', (thread,)).fetchone()
        encoded = json.dumps(spec, sort_keys=True)
        if previous:
            if previous[0] != encoded:
                raise ValueError('Existing coordinator requires an explicit separate reconfiguration')
            return {'result': 'unchanged', 'coordinator': thread}
        if db.execute('SELECT 1 FROM worker_owner WHERE worker=?', (thread,)).fetchone():
            raise ValueError('Only one coordinator layer is supported')
        for w, worker in zip(workers, worker_ids):
            if not w['scope'].strip() or db.execute('SELECT 1 FROM coordinator WHERE thread=?', (worker,)).fetchone():
                raise ValueError('Nested coordinator or missing direct-worker scope')
            if not db.execute('SELECT 1 FROM assignment WHERE worker=? AND runtime_session=?',
                              (worker, uid(w['runtime_session']))).fetchone():
                raise ValueError('Direct worker runtime is not registered')
            if db.execute('SELECT 1 FROM worker_owner WHERE worker=?', (worker,)).fetchone():
                raise ValueError('Direct worker already has an owner')
        db.execute('INSERT INTO coordinator (thread,spec) VALUES (?,?)', (thread, encoded))
        for w, worker in zip(workers, worker_ids):
            db.execute('INSERT INTO worker_owner VALUES (?,?,?)', (worker, uid(w['runtime_session']), thread))
        db.commit()
    return {'result': 'configured', 'coordinator': thread, 'direct_workers': worker_ids,
            'ownership': 'future registrations only; existing assignments unchanged'}


def withdraw_coordinator(root, coordinator):
    """Explicit rollback for future work only; outstanding routes/facts stay owned."""
    with connect(root) as db:
        changed = db.execute('UPDATE coordinator SET enabled=0 WHERE thread=?', (uid(coordinator),)).rowcount
        db.commit()
        if not changed:
            raise ValueError('Unknown coordinator')
    return {'coordinator': uid(coordinator), 'new_registrations': 'root',
            'existing_assignments': 'unchanged; settle through original owner'}


def parent_channel(root, coordinator=None):
    """Direct-report facts only; no subtree/API scanner or model call."""
    with connect(root) as db:
        if not has_table(db, 'parent_fact'):
            return []
        query = 'SELECT * FROM parent_fact'
        args = ()
        if coordinator is not None:
            query += ' WHERE coordinator=?'
            args = (uid(coordinator),)
        return [dict(row) for row in db.execute(query + ' ORDER BY created', args)]


def policy_mode(root):
    with connect(root) as db:
        return policy(db)['mode']


def set_record_only(root):
    return set_delivery_mode(root, 'record_only')


def set_delivery_mode(root, mode):
    """Record the quiet cutover without altering events, registration or lease.

    An older deployed delivery job must be unloaded before running this command.
    Visible mode is explicit opt-in and starts strictly beyond this cutover.
    """
    if mode not in ('record_only', 'visible'):
        raise ValueError('Invalid delivery mode')
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        if mode == 'visible':
            settings(db)
        db.execute('''CREATE TABLE IF NOT EXISTS delivery_policy
            (singleton INTEGER PRIMARY KEY CHECK(singleton=1), mode TEXT,
             changed REAL, prior_max_rowid INTEGER)''')
        previous = db.execute('SELECT * FROM delivery_policy WHERE singleton=1').fetchone()
        if previous is None or previous['mode'] != mode:
            floor = db.execute('SELECT COALESCE(MAX(rowid),0) FROM event').fetchone()[0]
            db.execute('INSERT OR REPLACE INTO delivery_policy VALUES (1,?,?,?)', (mode, time.time(), floor))
        db.execute('''CREATE TABLE IF NOT EXISTS notice
            (id TEXT PRIMARY KEY, created REAL, outcome TEXT, path TEXT, digest TEXT)''')
        db.execute('''CREATE TABLE IF NOT EXISTS notice_event
            (notice TEXT, assignment TEXT, phase TEXT, PRIMARY KEY(assignment, phase))''')
        db.commit()
        return dict(db.execute('SELECT * FROM delivery_policy WHERE singleton=1').fetchone())


def inbox(root, after=0):
    """Read exact immutable reports; no send, replay, claim, or read-cursor write."""
    if not isinstance(after, int) or after < 0:
        raise ValueError('Invalid inbox cursor')
    with connect(root) as db:
        session = dict(db.execute('SELECT * FROM session').fetchone())
        rows = db.execute('''SELECT e.rowid AS cursor, e.*, a.worker, a.runtime_session
            FROM event e JOIN assignment a ON e.assignment=a.id
            WHERE e.rowid>? ORDER BY e.rowid''', (after,)).fetchall()
        events = []
        latest = {}
        for row in rows:
            item = dict(row)
            snapshot = absolute(root) / 'receipts' / (row['assignment'] + '-' + row['phase'] + '.json')
            item['receipt'] = str(snapshot)
            try:
                item['receipt_valid'] = artifact_digest(snapshot) == row['digest']
            except (OSError, ValueError):
                item['receipt_valid'] = False
            events.append(item)
            latest[row['assignment']] = row['status']
        summary = {status: sum(v == status for v in latest.values()) for status in STATUSES}
        # Old accepted/failed/uncertain/claimed outcomes are evidence, never reset.
        mode = policy(db)['mode']
        return {'mode': mode, 'automatic_delivery': mode == 'visible',
                'lease_active': not session['stopped'] and session['created'] <= time.time() < session['expires'],
                'session': session, 'events': events, 'summary': summary,
                'next_cursor': events[-1]['cursor'] if events else after}


def recovery(root, owner=None, limit=100):
    """Read direct ownership and recovery gaps, even after expiry; never repair/send."""
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError('Recovery limit must be 1..1000')
    now = time.time()
    with connect(root, read_only=True) as db:
        session = dict(db.execute('SELECT * FROM session').fetchone())
        owner = uid(owner or session['target'])
        coordinators = []
        if has_table(db, 'coordinator'):
            coordinators = [dict(row) for row in db.execute('SELECT * FROM coordinator ORDER BY thread')]
        if owner != session['target'] and owner not in {row['thread'] for row in coordinators}:
            raise ValueError('Unknown direct-report owner')
        active = not session['stopped'] and session['created'] <= now < session['expires']
        if has_table(db, 'assignment_route'):
            clause = 'LEFT JOIN assignment_route r ON r.assignment=a.id WHERE COALESCE(r.target,?)=?'
            args = (session['target'], owner)
        else:
            clause, args = '', ()
        total = db.execute('SELECT COUNT(*) FROM assignment a ' + clause, args).fetchone()[0]
        rows = db.execute('SELECT a.* FROM assignment a ' + clause +
                          ' ORDER BY a.created DESC,a.id LIMIT ?', (*args, limit)).fetchall()
        reports = []
        for row in rows:
            events = {e['phase']: dict(e) for e in db.execute(
                'SELECT * FROM event WHERE assignment=?', (row['id'],))}
            orphans = []
            for phase in ('input', 'final'):
                path = absolute(root) / 'receipts' / (row['id'] + '-' + phase + '.json')
                if phase in events:
                    events[phase]['receipt'] = str(path)
                    try:
                        events[phase]['receipt_valid'] = artifact_digest(path) == events[phase]['digest']
                    except (OSError, ValueError):
                        events[phase]['receipt_valid'] = False
                elif path.exists() or path.is_symlink():
                    try:
                        digest = artifact_digest(path)
                    except (OSError, ValueError):
                        digest = None
                    orphans.append({'phase': phase, 'receipt': str(path), 'sha256': digest})
            lease = active and row['created'] <= now < row['expires']
            reports.append({'assignment': row['id'], 'worker': row['worker'],
                'runtime_session': row['runtime_session'], 'expires': row['expires'],
                'lease_active': lease, 'events': events, 'uncommitted_snapshots': orphans,
                'needs_input_unresolved_by_final': 'input' in events and 'final' not in events,
                'reported_final': events.get('final', {}).get('status'),
                'can_record_later_final': lease and 'final' not in events,
                'new_scope_requires_fresh_assignment': True,
                'delivery_requires_reconciliation': any(e['outcome'] in
                    ('claimed', 'accepted_unverified', 'failed_no_retry', 'uncertain_no_retry')
                    for e in events.values())})
        direct_coordinators = []
        if owner == session['target']:
            for row in coordinators:
                spec = json.loads(row['spec'])
                facts = db.execute('SELECT COUNT(*) FROM parent_fact WHERE coordinator=?',
                                   (row['thread'],)).fetchone()[0]
                unrelayed = db.execute('''SELECT COUNT(*) FROM parent_fact f WHERE coordinator=?
                    AND NOT EXISTS (SELECT 1 FROM relay_claim c WHERE c.coordinator=f.coordinator
                                    AND c.fingerprint=f.fingerprint)''', (row['thread'],)).fetchone()[0]
                direct_coordinators.append({'thread': row['thread'], 'name': spec['name'],
                    'scope': spec['scope'], 'charter': spec['charter'], 'backlog': spec['backlog'],
                    'enabled': bool(row['enabled']), 'expires': spec['expires'],
                    'recorded_facts': facts, 'facts_without_relay_claim': unrelayed})
        return {'owner': owner, 'mode': policy(db)['mode'], 'session_id': session['id'],
            'session_expires': session['expires'], 'lease_active': active,
            'new_lease_requires_explicit_authorization': not active,
            'assignment_count': total, 'truncated': total > limit, 'assignments': reports,
            'direct_coordinators': direct_coordinators,
            'meaning': 'Reported facts only; no objective/turn completion or approval inferred. '
                       'No retry, replay, steering, renewal, parking or external action performed.'}


def deliver_once(root):
    """Deliver only fresh, explicitly opted-in reports, with one sticky batch claim."""
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        config = policy(db)
        if config['mode'] != 'visible':
            db.commit()
            return None
        now = time.time()
        session = settings(db, now)
        rows = db.execute('''SELECT e.*, a.worker, a.expires FROM event e
            JOIN assignment a ON a.id=e.assignment WHERE e.outcome='pending'
            AND e.rowid>? AND e.created>=? ORDER BY e.rowid''',
                          (config['prior_max_rowid'], config['changed'])).fetchall()
        groups = {}
        for row in rows:
            snapshot = absolute(root) / 'receipts' / (row['assignment'] + '-' + row['phase'] + '.json')
            outcome = None
            if now >= row['expires']:
                outcome = 'expired_no_delivery'
            else:
                try:
                    if artifact_digest(snapshot) != row['digest']:
                        outcome = 'invalid_receipt_no_delivery'
                except (OSError, ValueError):
                    outcome = 'invalid_receipt_no_delivery'
            if outcome:
                db.execute('UPDATE event SET outcome=?, attempted=? WHERE assignment=? AND phase=?',
                           (outcome, now, row['assignment'], row['phase']))
            else:
                item = dict(row)
                item['target'], item['coordinator'] = assignment_owner(db, row['assignment'])
                groups.setdefault(row['assignment'], []).append(item)
        if not groups:
            db.commit()
            return None

        def effective(group):
            return next((e['status'] for e in group if e['phase'] == 'final'), 'needs_input')

        priority = {'needs_input': 0, 'failed': 1, 'ready': 2}
        ordered = sorted(groups.values(), key=lambda g: (priority[effective(g)], min(e['created'] for e in g)))
        destination = ordered[0][0]['target']
        ordered = [g for g in ordered if g[0]['target'] == destination]
        urgent = [e['created'] for g in ordered if effective(g) != 'ready' for e in g]
        oldest = min(urgent) if urgent else min(e['created'] for g in ordered for e in g)
        # A fixed window avoids starvation under a continuing stream of reports.
        if now < oldest + (2 if urgent else 20):
            db.commit()
            return None
        selected = ordered[:64]  # Never split the two phases of an assignment.
        summary = dict.fromkeys(STATUSES, 0)
        for group in selected:
            summary[effective(group)] += 1
        members = [event for group in selected for event in group]
        notice_id = uuid.uuid4().hex[:12]
        folder = (Path.home() / '.local/state/codexjarvis/inbox').resolve()
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        if folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o077:
            raise ValueError('Notice inbox must be private (0700)')
        index = folder / (notice_id + '.json')
        data = {'notice_id': notice_id, 'root': str(absolute(root)), 'session': session['id'],
                'target': destination, 'summary': summary,
                'events': [{'worker': e['worker'], 'assignment': e['assignment'], 'phase': e['phase'],
                            'status': e['status'], 'sha256': e['digest'],
                            'receipt': str(absolute(root) / 'receipts' /
                                           (e['assignment'] + '-' + e['phase'] + '.json'))}
                           for e in members],
                'meaning': 'Worker-reported status, not objective approval. Verify exact workers and receipts. No dispatch, approval answers or archival from this notice.',
                'verification': 'Use the registered session runner: --root <root> notice --id <notice_id>.'}
        encoded = (json.dumps(data, indent=2) + '\n').encode()
        with index.open('xb') as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        index.chmod(0o600)
        db.execute('INSERT INTO notice VALUES (?,?,?,?,?)',
                   (notice_id, now, 'claimed', str(index), hashlib.sha256(encoded).hexdigest()))
        for event in members:
            db.execute('INSERT INTO notice_event VALUES (?,?,?)',
                       (notice_id, event['assignment'], event['phase']))
            db.execute("UPDATE event SET outcome='claimed', attempted=? WHERE assignment=? AND phase=?",
                       (now, event['assignment'], event['phase']))
        db.commit()  # Claim all members BEFORE the one external attempt.
        counts = [f'{summary[key]} {label}' for key, label in
                  (('needs_input', 'need input'), ('failed', 'failed'), ('ready', 'ready')) if summary[key]]
        message = 'Task updates: ' + '; '.join(counts) + f'. Details: ~/.local/state/codexjarvis/inbox/{notice_id}.json'
        try:
            result = subprocess.run([session['binary'], 'queue', '--thread', destination,
                                     '--message', message], stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=15)
            outcome = 'accepted_unverified' if result.returncode == 0 else 'failed_no_retry'
        except (OSError, subprocess.TimeoutExpired):
            outcome = 'uncertain_no_retry'
        db.execute('UPDATE notice SET outcome=? WHERE id=?', (outcome, notice_id))
        for event in members:
            db.execute('UPDATE event SET outcome=? WHERE assignment=? AND phase=?',
                       (outcome, event['assignment'], event['phase']))
        db.commit()
        return {'notice': notice_id, 'events': len(members), 'outcome': outcome}


def read_notice(root, notice_id):
    if not re.fullmatch(r'[0-9a-f]{12}', notice_id):
        raise ValueError('Invalid notice id')
    with connect(root) as db:
        row = db.execute('SELECT * FROM notice WHERE id=?', (notice_id,)).fetchone()
        if row is None or artifact_digest(row['path']) != row['digest']:
            raise ValueError('Notice missing or changed')
        data = json.loads(absolute(row['path']).read_text())
        expected = db.execute('''SELECT e.*, a.worker FROM notice_event n
            JOIN event e ON n.assignment=e.assignment AND n.phase=e.phase
            JOIN assignment a ON a.id=e.assignment WHERE n.notice=?''', (notice_id,)).fetchall()
        if len(data['events']) != len(expected):
            raise ValueError('Notice membership mismatch')
        by_key = {(e['assignment'], e['phase']): e for e in data['events']}
        for event in expected:
            item = by_key[(event['assignment'], event['phase'])]
            snapshot = absolute(root) / 'receipts' / (event['assignment'] + '-' + event['phase'] + '.json')
            if (item['worker'] != event['worker'] or item['status'] != event['status']
                    or item['receipt'] != str(snapshot) or item['sha256'] != event['digest']
                    or data['target'] != assignment_owner(db, event['assignment'])[0]
                    or artifact_digest(snapshot) != event['digest']):
                raise ValueError('Notice identity or snapshot changed')
        return data


def relay_notice(root, notice_id, status, judgment_path):
    """Coordinator judgment plus script-owned facts, deduplicated by source receipt."""
    if status not in STATUSES:
        raise ValueError('Invalid relay status')
    coordinator = uid(os.environ.get('CODEX_THREAD_ID', ''))
    runtime = uid(os.environ.get('CODEX_SESSION_ID', ''))
    source = read_notice(root, notice_id)
    artifact_digest(judgment_path)
    judgment = json.loads(absolute(judgment_path).read_text())
    now = time.time()
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        session = settings(db, now)
        row = db.execute('SELECT spec FROM coordinator WHERE thread=?', (coordinator,)).fetchone()
        if row is None:
            raise ValueError('Runtime is not a configured coordinator')
        spec = json.loads(row[0])
        if (runtime != spec['runtime_session'] or source['target'] != coordinator
                or now >= spec['expires'] or spec['parent'] != session['target']):
            raise ValueError('Coordinator binding, parent or source channel mismatched')
        fresh = []
        for event in source['events']:
            fingerprint = source_fingerprint(event)
            fact = db.execute('SELECT event FROM parent_fact WHERE coordinator=? AND fingerprint=?',
                              (coordinator, fingerprint)).fetchone()
            if fact is None or json.loads(fact[0]) != {k: event[k] for k in
                    ('worker', 'assignment', 'phase', 'status', 'sha256')}:
                raise ValueError('Source event is outside this coordinator channel')
            if not db.execute('SELECT 1 FROM relay_claim WHERE coordinator=? AND fingerprint=?',
                              (coordinator, fingerprint)).fetchone():
                fresh.append((fingerprint, event))
        if not fresh:
            return {'result': 'duplicate', 'fresh_sources': 0}
        assignment = str(uuid.uuid4())
        phase = 'input' if status == 'needs_input' else 'final'
        receipt = absolute(root) / 'receipts' / (assignment + '.json')
        snapshot = absolute(root) / 'receipts' / (assignment + '-' + phase + '.json')
        value = {'coordinator': coordinator, 'parent': spec['parent'], 'name': spec['name'],
                 'scope': spec['scope'], 'source_notice': notice_id,
                 'source_facts': [event for _, event in fresh], 'judgment': judgment,
                 'meaning': 'Deterministic source facts plus coordinator judgment; no editorial approval or automatic follow-on authority.'}
        data = (json.dumps(value, indent=2) + '\n').encode()
        if len(data) > 1048576:
            raise ValueError('Reduce judgment to artifact pointers; relay exceeds 1 MiB')
        for path in (receipt, snapshot):
            with path.open('xb') as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            path.chmod(0o600)
        db.execute('INSERT INTO assignment VALUES (?,?,?,?,?,?)',
                   (assignment, coordinator, runtime, now, min(spec['expires'], session['expires']), str(receipt)))
        db.execute('INSERT INTO assignment_route VALUES (?,?,NULL)', (assignment, spec['parent']))
        config = policy(db)
        visible = config['mode'] == 'visible' and now >= config['changed']
        db.execute('INSERT INTO event VALUES (?,?,?,?,?,?,NULL)',
                   (assignment, phase, status, now, hashlib.sha256(data).hexdigest(),
                    'pending' if visible else 'recorded_only'))
        for fingerprint, _ in fresh:
            db.execute('INSERT INTO relay_claim VALUES (?,?,?)', (coordinator, fingerprint, assignment))
        db.commit()
        return {'result': 'reported', 'assignment': assignment, 'fresh_sources': len(fresh),
                'delivery': 'visible' if visible else 'record_only'}


def watch(root, interval=2):
    if policy_mode(root) != 'visible':
        raise ValueError('Visible delivery is not opted in')
    halted = False

    def stop_signal(*_):
        nonlocal halted
        halted = True

    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    while not halted:
        with connect(root) as db:
            row = db.execute('SELECT * FROM session').fetchone()
            if row['stopped'] or time.time() >= row['expires'] or policy(db)['mode'] != 'visible':
                return
        result = deliver_once(root)
        if result is not None:
            print(json.dumps(result), flush=True)
        time.sleep(interval)


def install(root):
    with connect(root) as db:
        session = settings(db)
        if policy(db)['mode'] != 'visible':
            raise ValueError('Visible delivery is not opted in')
    legacy = f'local.codexjarvis.{session["id"]}'
    probe = subprocess.run(['/bin/launchctl', 'print', f'gui/{os.getuid()}/{legacy}'],
                           capture_output=True, text=True, timeout=10)
    if probe.returncode != 113 or f'Could not find service "{legacy}"' not in probe.stderr:
        raise ValueError('Legacy delivery service absence must be verified before install')
    root = absolute(root)
    runner = root / 'delivery.py'
    with runner.open('xb') as handle:
        handle.write(Path(__file__).read_bytes())
    runner.chmod(0o600)
    label = legacy + '.visible'
    plist = root / 'delivery.plist'
    value = {'Label': label, 'ProgramArguments': [str(Path(sys.executable).resolve()), str(runner),
             '--root', str(root), 'watch'], 'RunAtLoad': True, 'Umask': 0o077,
             'WorkingDirectory': str(root), 'StandardOutPath': str(root / 'delivery.log'),
             'StandardErrorPath': str(root / 'delivery.err')}
    with plist.open('xb') as handle:
        plistlib.dump(value, handle)
    plist.chmod(0o600)
    subprocess.run(['/bin/launchctl', 'bootstrap', f'gui/{os.getuid()}', str(plist)],
                   capture_output=True, check=True, timeout=10)
    return {'label': label, 'expires': session['expires'], 'plist': str(plist)}


def status(root):
    with connect(root) as db:
        row = dict(db.execute('SELECT * FROM session').fetchone())
        events = [dict(e) for e in db.execute('SELECT * FROM event')]
        assignments = [dict(a) for a in db.execute('SELECT * FROM assignment')]
        config = dict(policy(db))
    result = subprocess.run(['/bin/launchctl', 'print', f'gui/{os.getuid()}/local.codexjarvis.{row["id"]}.visible'],
                            capture_output=True, text=True, timeout=10)
    row['mode'] = config['mode']
    row['activation'] = config
    row['automatic_delivery'] = config['mode'] == 'visible'
    row['job_loaded'] = result.returncode == 0
    row['lease_active'] = not row['stopped'] and time.time() < row['expires']
    row['events'] = events
    row['assignments'] = assignments
    return row


def stop(root):
    with connect(root) as db:
        row = db.execute('SELECT id FROM session').fetchone()
        db.execute('UPDATE session SET stopped=1')
        db.commit()
    service = f'gui/{os.getuid()}/local.codexjarvis.{row["id"]}.visible'
    bootout = subprocess.run(['/bin/launchctl', 'bootout', service],
                             capture_output=True, timeout=20)
    label = 'local.codexjarvis.' + row['id'] + '.visible'
    # launchctl bootout can return while the service is still stopping. Poll
    # only this exact service, never retry bootout or any queued message.
    deadline = time.monotonic() + 5
    while True:
        probe = subprocess.run(['/bin/launchctl', 'print', service],
                               capture_output=True, text=True, timeout=1)
        absent = (probe.returncode == 113 and f'Could not find service "{label}"' in probe.stderr)
        if absent:
            return {'stopped': True, 'job_unloaded': True, 'service': service,
                    'bootout_returncode': bootout.returncode}
        if probe.returncode != 0 or time.monotonic() >= deadline:
            raise RuntimeError(f'Session disabled but exact job unload unverified; bootout={bootout.returncode}, '
                               f'probe={probe.returncode}; service={service}')
        time.sleep(0.1)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--target', required=True)
    init.add_argument('--queue-binary', required=True)
    init.add_argument('--lease', type=int, default=3600)
    reg = commands.add_parser('register')
    reg.add_argument('--worker', required=True)
    reg.add_argument('--runtime-session', help='Exact expected runtime session; defaults to worker id')
    reg.add_argument('--lease', type=int, default=3600)
    rep = commands.add_parser('report')
    rep.add_argument('--assignment', required=True)
    rep.add_argument('--status', choices=STATUSES, required=True)
    view = commands.add_parser('inbox')
    view.add_argument('--after', type=int, default=0)
    recover = commands.add_parser('recovery')
    recover.add_argument('--owner', help='Exact direct-report owner; defaults to fixed root')
    recover.add_argument('--limit', type=int, default=100)
    commands.add_parser('record-only')
    mode = commands.add_parser('delivery')
    mode.add_argument('--mode', required=True, choices=('record_only', 'visible'))
    notice = commands.add_parser('notice')
    notice.add_argument('--id', required=True)
    hierarchy = commands.add_parser('coordinator-config')
    hierarchy.add_argument('--spec', required=True)
    withdraw = commands.add_parser('coordinator-withdraw')
    withdraw.add_argument('--coordinator', required=True)
    channel = commands.add_parser('parent-channel')
    channel.add_argument('--coordinator')
    relay = commands.add_parser('relay')
    relay.add_argument('--notice', required=True)
    relay.add_argument('--status', choices=STATUSES, required=True)
    relay.add_argument('--judgment', required=True)
    for name in ('drain', 'watch', 'install', 'status', 'stop'):
        commands.add_parser(name)
    args = parser.parse_args()
    if args.command == 'init':
        result = initialize(args.root, args.target, args.queue_binary, args.lease)
    elif args.command == 'register':
        result = register(args.root, args.worker, args.lease, args.runtime_session)
    elif args.command == 'report':
        result = report(args.root, args.assignment, args.status)
    elif args.command == 'inbox':
        result = inbox(args.root, args.after)
    elif args.command == 'recovery':
        result = recovery(args.root, args.owner, args.limit)
    elif args.command == 'record-only':
        result = set_record_only(args.root)
    elif args.command == 'delivery':
        result = set_delivery_mode(args.root, args.mode)
    elif args.command == 'notice':
        result = read_notice(args.root, args.id)
    elif args.command == 'coordinator-config':
        artifact_digest(args.spec)
        result = configure_coordinator(args.root, json.loads(absolute(args.spec).read_text()))
    elif args.command == 'coordinator-withdraw':
        result = withdraw_coordinator(args.root, args.coordinator)
    elif args.command == 'parent-channel':
        result = parent_channel(args.root, args.coordinator)
    elif args.command == 'relay':
        result = relay_notice(args.root, args.notice, args.status, args.judgment)
    elif args.command == 'drain':
        raise ValueError('Outbound drain is disabled; use inbox for a read-only view')
    else:
        result = {'drain': deliver_once, 'watch': watch, 'install': install,
                  'status': status, 'stop': stop}[args.command](args.root)
    if result is not None:
        print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, KeyError, TypeError, OSError, sqlite3.Error, subprocess.SubprocessError) as exc:
        print(f'Cooperative command stopped: {type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(2)
