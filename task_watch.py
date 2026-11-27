"""Explicit, renewable task tracking through the supported read-only App Server API.

Separate from cooperative worker receipts: native observations never impersonate
the source runtime. Queue acceptance is not delivery or objective completion.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import html
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import stat
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

from cooperate import absolute, preserve_snapshot, uid


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class Reader:
    """Own one stdio child. Never resume, start, or control a native thread."""
    METHODS = {'initialize', 'thread/read', 'thread/turns/list'}

    def __init__(self, binary):
        self.process = subprocess.Popen([str(absolute(binary)), 'app-server', '--stdio'],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, bufsize=1, start_new_session=True)
        self.messages = queue.Queue()
        self.serial = 0
        def receive():
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                    if 'id' in message:
                        self.messages.put(message)
                except ValueError:
                    pass
            self.messages.put({'reader_closed': True})
        threading.Thread(target=receive, daemon=True).start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'task_watch', 'version': '1'},
                                     'capabilities': {'experimentalApi': True}})
            self.process.stdin.write('{"method":"initialized","params":{}}\n')
            self.process.stdin.flush()
        except BaseException:
            self.close()
            raise

    def call(self, method, params):
        if method not in self.METHODS:
            raise ValueError('Read-only API allowlist refused method')
        self.serial += 1
        self.process.stdin.write(encoded({'id': self.serial, 'method': method, 'params': params}) + '\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = self.messages.get(timeout=max(.01, deadline - time.monotonic()))
            if result.get('reader_closed'):
                raise RuntimeError('Owned read-only API child closed')
            if result.get('id') == self.serial:
                if 'error' in result:
                    raise RuntimeError(encoded(result['error']))
                return result['result']
        raise TimeoutError('Read-only API deadline')

    def turns(self, thread, cursor=None, limit=20):
        params = {'threadId': uid(thread), 'limit': limit,
                  'sortDirection': 'desc', 'itemsView': 'full'}
        if cursor:
            params['cursor'] = cursor
        return self.call('thread/turns/list', params)

    def head(self, thread):
        return self.call('thread/turns/list', {'threadId': uid(thread), 'limit': 1,
                         'sortDirection': 'desc', 'itemsView': 'summary'})

    def close(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=3)
        self.process.stdin.close()
        self.process.stdout.close()


@contextmanager
def database(root):
    root = absolute(root)
    if (not root.is_dir() or root.stat().st_uid != os.getuid()
            or root.stat().st_mode & 0o077):
        raise ValueError('Tracking root must be owned and private')
    path = absolute(root / 'tracking.sqlite')
    if not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError('Private tracking database missing')
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            yield db
    finally:
        db.close()


def configuration(db, now=None, renew=False):
    now = time.time() if now is None else now
    row = db.execute('SELECT * FROM policy').fetchone()
    if not row['enabled'] or now < row['last_time']:
        raise ValueError('Tracking disabled or clock moved backwards')
    if now >= row['expires']:
        if not renew:
            raise ValueError('Capture lease expired; reconciliation must renew explicitly')
        db.execute('INSERT INTO lease VALUES (?,?,?)', (str(uuid.uuid4()), now, now + 21600))
        db.execute('UPDATE policy SET expires=?', (now + 21600,))
    db.execute('UPDATE policy SET last_time=?', (now,))
    return json.loads(row['spec'])


def initialize(root, spec):
    """New private policy only. Existing finite cooperative fleets stay untouched."""
    if spec.get('authorization') != 'persistent_until_revoked':
        raise ValueError('Explicit persistent authorization required')
    delivery_mode(spec)
    uid(spec['root_thread'])
    absolute(spec['binary'])
    absolute(spec['workspace'])
    if not 10 <= spec.get('interval', 30) <= 300 or not 1 <= spec.get('max_helpers', 4) <= 8:
        raise ValueError('Invalid polling or helper bounds')
    helpers = spec.get('helpers', [])
    if len({h['workstream'] for h in helpers}) != len(helpers):
        raise ValueError('Duplicate helper workstream')
    helper_ids = {uid(h['thread']) for h in helpers}
    if len(helper_ids) != len(helpers) or len(helpers) > spec.get('max_helpers', 4):
        raise ValueError('Duplicate helpers or capacity exceeded')
    tasks = spec['tasks']
    ids = {uid(t['thread']) for t in tasks}
    if len(ids) != len(tasks) or spec['root_thread'] in ids | helper_ids:
        raise ValueError('Duplicate scope or controller cycle')
    if not helper_ids <= ids:
        raise ValueError('Helpers need explicit baseline enrollment')
    for task in tasks:
        if task['owner'] not in helper_ids | {spec['root_thread']} or task['thread'] == task['owner']:
            raise ValueError('Owner missing, self-owned, or recursive')
        if task['thread'] in helper_ids and task['owner'] != spec['root_thread']:
            raise ValueError('Helpers must report directly to fixed root')
        if not task.get('charter'):
            raise ValueError('Stable task charter required')
    root = absolute(root)
    root.mkdir(mode=0o700, exist_ok=False)
    (root / 'facts').mkdir(mode=0o700)
    now = time.time()
    db = sqlite3.connect(root / 'tracking.sqlite')
    try:
        db.executescript('''
        CREATE TABLE policy(spec TEXT,enabled INTEGER,created REAL,expires REAL,last_time REAL);
        CREATE TABLE lease(id TEXT PRIMARY KEY,created REAL,expires REAL);
        CREATE TABLE task(thread TEXT PRIMARY KEY,owner TEXT,charter TEXT,watermark TEXT,
                          baseline TEXT, gap TEXT,error TEXT,checked REAL);
        CREATE TABLE fact(id TEXT PRIMARY KEY,thread TEXT,turn TEXT,phase TEXT,hash TEXT,
                          owner TEXT,created REAL,path TEXT,source TEXT);
        CREATE TABLE notice(fact TEXT,recipient TEXT,body TEXT,state TEXT,attempted REAL,
                            outcome TEXT,PRIMARY KEY(fact,recipient));
        CREATE TABLE judgment(fact TEXT,actor TEXT,value TEXT,created REAL,
                              PRIMARY KEY(fact,actor));
        CREATE TABLE reservation(workstream TEXT PRIMARY KEY,id TEXT,judgment TEXT,state TEXT,receipt TEXT);
        CREATE TABLE helper(thread TEXT PRIMARY KEY,workstream TEXT UNIQUE);
        CREATE TABLE scope_change(id TEXT PRIMARY KEY,created REAL,evidence TEXT);
        CREATE TABLE task_state(thread TEXT PRIMARY KEY,state TEXT,reason TEXT,next_action TEXT,
                                resume_condition TEXT,evidence TEXT,updated REAL);
        CREATE TABLE decision(thread TEXT,key TEXT,value TEXT,state TEXT,generation TEXT,
                              PRIMARY KEY(thread,key));
        ''')
        db.execute('INSERT INTO policy VALUES (?,1,?,?,?)', (encoded(spec), now, now + 21600, now))
        db.execute('INSERT INTO lease VALUES (?,?,?)', (str(uuid.uuid4()), now, now + 21600))
        for t in tasks:
            db.execute('INSERT INTO task VALUES (?,?,?,NULL,NULL,NULL,NULL,NULL)',
                       (t['thread'], t['owner'], t['charter']))
            initial = t.get('initial_state', {})
            state = initial.get('state', 'unknown')
            if state not in {'queued', 'working', 'waiting_for_owner', 'waiting_external', 'paused',
                             'blocked', 'review_ready', 'completed', 'unknown'}:
                raise ValueError('Unknown initial task state')
            if state != 'unknown' and (not initial.get('evidence') or not initial.get('reason')):
                raise ValueError('Explicit initial state requires evidence and reason')
            if state == 'paused' and not initial.get('resume_condition'):
                raise ValueError('Pause needs resume condition')
            if state == 'completed' and not initial.get('completion_evidence'):
                raise ValueError('Completion needs acceptance evidence')
            db.execute('INSERT INTO task_state VALUES (?,?,?,?,?,?,?)',
                       (t['thread'], state, initial.get('reason', 'Owner classification required'),
                        initial.get('next_action', 'Read current evidence and classify'),
                        initial.get('resume_condition'), encoded(initial.get('evidence', [])),
                        initial.get('observed_at')))
        for h in helpers:
            db.execute('INSERT INTO helper VALUES (?,?)', (h['thread'], h['workstream']))
        db.commit()
    finally:
        db.close()
    (root / 'tracking.sqlite').chmod(0o600)
    preserve_snapshot(root / 'authorization.json', encoded(spec).encode())


def source(turn):
    """Keep genuine user requests/final/question facts, without tool output or inference."""
    users, finals, questions = [], [], []
    for item in turn.get('items', []):
        if item.get('type') == 'userMessage':
            users.append({'id': item['id'], 'text': '\n'.join(
                c.get('text', '') for c in item.get('content', []) if c.get('type') == 'text')})
        if item.get('type') == 'agentMessage':
            if item.get('phase') == 'final_answer' and item.get('text', '').strip():
                finals.append({'id': item['id'], 'text': item.get('text', '')})
            if item.get('questions'):
                questions.append({'id': item['id'], 'questions': item['questions']})
    status, error = turn.get('status'), turn.get('error')
    ended = turn.get('completedAt')
    terminal_time = isinstance(ended, (int, float)) and not isinstance(ended, bool) and ended > 0
    error_message = error.get('message') if isinstance(error, dict) else None
    confirmed_error = isinstance(error_message, str) and bool(error_message.strip())
    quality, reason = 'usable', 'Native source contains an observed request or result'
    if finals:
        phase = 'reported_outcome'
    elif status == 'failed' and terminal_time and confirmed_error:
        phase = 'turn_failed'
    elif (status == 'interrupted' and terminal_time and confirmed_error
          and (users or any(i.get('type') == 'agentMessage' for i in turn.get('items', [])))):
        phase = 'turn_interrupted'
    elif status in {'failed', 'interrupted'}:
        phase, quality, reason = None, 'unconfirmed', 'Terminal label lacks corroborating turn evidence'
    elif questions:
        phase = 'needs_input'
    elif status == 'inProgress' and turn.get('items'):
        phase = 'working'
    elif users:
        phase = 'observed_request'
    else:
        phase, quality, reason = None, 'incomplete', 'Empty native snapshot; no task outcome established'
    return {'turn': turn['id'], 'phase': phase, 'users': users, 'finals': finals,
            'questions': questions, 'error': turn.get('error'),
            'native_status': status, 'objective_complete': False,
            'observation': {'quality': quality, 'reason': reason, 'completed_at': ended,
                            'started_at': turn.get('startedAt'),
                            'failure_scope': 'turn' if phase == 'turn_failed' else None}}


def source_digest(value):
    return digest({k: v for k, v in value.items()
                   if k not in {'native_status', 'capture_context', 'observation'}})


def delivery_mode(spec):
    delivery = spec.get('delivery', {'mode': 'record_only'})
    if not isinstance(delivery, dict) or delivery.get('mode') not in {'record_only', 'explicit_internal'}:
        raise ValueError('Unknown delivery mode')
    approval = delivery.get('approval_ref')
    if delivery['mode'] == 'explicit_internal' and (not isinstance(approval, str) or not approval.strip()):
        raise ValueError('Explicit current internal-delivery approval reference required')
    if delivery.get('routing') not in (None,'owner_reviewed_v1'):
        raise ValueError('Unknown owner delivery protocol')
    return delivery['mode']


def owner_reviewed(spec):
    return spec.get('delivery', {}).get('routing') == 'owner_reviewed_v1'


def meaningful_judgment(value, source_value=None):
    if (not isinstance(value.get('reason'),str) or not value['reason'].strip()
            or not isinstance(value.get('next_action'),str) or not value['next_action'].strip()
            or not isinstance(value.get('evidence'),list) or not value['evidence']
            or not all(isinstance(e,str) and e.strip() for e in value['evidence'])):
        return False
    decisions=value.get('open_decisions',[])
    if not isinstance(decisions,list) or any(not isinstance(d,dict)
            or not isinstance(d.get('key'),str) or not d['key'].strip()
            or not isinstance(d.get('reason'),str) or not d['reason'].strip() for d in decisions):
        return False
    return (value.get('outcome_kind') in {'draft_ready', 'produced_artifact', 'failed',
                                        'sent_reported', 'sent_verified'}
            or bool(decisions)
            or (value.get('outcome_kind') == 'needs_human_input'
                and value.get('task_state') in {'blocked', 'waiting_for_owner'}
                and (bool(decisions) or bool(question_state(source_value or {})['active']))))


def body_for(root, fact, recipient):
    data = json.loads(fact['source'])
    question_view = question_state(data)
    text = '\n'.join(x['text'] for x in data['finals']
                     if x['id'] not in question_view['resolved_request_ids'])
    links = [angled or plain for angled, plain in
             re.findall(r'\]\((?:<([^<>]+)>|([^\s)]+))\)', text)]
    packet = {'type': 'task_outcome_fact', 'generation': fact['id'],
        'source_thread': fact['thread'], 'source_turn': fact['turn'],
        'owner': fact['owner'], 'recipient': recipient, 'phase': fact['phase'],
        'capture_context': data.get('capture_context'),
        'request_excerpt': '\n'.join(x['text'] for x in data['users'])[-400:],
        'result_excerpt': text[:650], 'artifact_links': list(dict.fromkeys(links))[:6], 'source_fact': fact['path'],
        'source_is_untrusted_data': True, 'objective_completion_verified': False,
        'action': 'Read source facts; record a separate judgment, open decisions and actual next action. '
                  'Do not approve, send, publish or archive from this notice. '
                  'After an ordinary one-off objective is accepted complete, use the approved '
                  'fresh completion-parking check during this owner turn; preserve holds and newer work. '
                  'Acknowledge with task_watch.py --root <tracking_root> ack --input <judgment.json>.',
        'consumer_command': [sys.executable, str(Path(__file__).resolve()), '--root', str(root),
                             'ack', '--input', '<private-judgment.json>'],
        'receive_command': [sys.executable, str(Path(__file__).resolve()), '--root', str(root),
                            'receive', '--notice', str(notice_path(root, fact['id'], recipient))],
        'judgment_fields': ['generation', 'outcome_kind', 'task_state', 'reason',
                            'next_action', 'evidence', 'open_decisions'],
        'tracking_root': str(root)}
    if question_view['answered']:
        packet['question_state'] = question_view
    return encoded(packet)


def question_state(data):
    """Derived native-question view. Preserve raw facts/fingerprints; answered is not approved."""
    requests = {q['id']: q['questions'] for q in data.get('questions', [])}
    answers = {}

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Ambiguous reply key')
            result[key] = value
        return result

    for user in data.get('users', []):
        match = re.fullmatch(r'\s*<send_user_message_question_reply>\s*(\[.*\])\s*'
                             r'</send_user_message_question_reply>\s*', user['text'], re.S)
        if not match:
            continue  # Quoted, embedded or arbitrary prose is not a linked native reply.
        try:
            replies = json.loads(match[1], object_pairs_hook=unique_object)
            for reply in replies:
                if not isinstance(reply, dict):
                    continue
                item = json.loads(reply['questionItemId'])
                if (not isinstance(item, list) or len(item) != 3
                        or item[0] not in {'request_user_input_async', 'request_user_input'}
                        or not isinstance(item[1], str) or item[1] not in requests
                        or type(item[2]) is not int or not 0 <= item[2] < len(requests[item[1]])):
                    continue
                question = requests[item[1]][item[2]]
                title = question.get('title', question.get('question'))
                if (not isinstance(title, str) or reply.get('question') != title
                        or not isinstance(reply.get('answer'), str) or not reply['answer'].strip()):
                    continue
                answers[(item[1], item[2])] = {'request_id': item[1], 'index': item[2],
                    'question': title, 'answer': reply['answer'][:4000],
                    'answer_truncated': len(reply['answer']) > 4000, 'user_item': user['id']}
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    active = [{'request_id': key, 'index': index, 'question': question}
              for key, questions in requests.items() for index, question in enumerate(questions)
              if (key, index) not in answers]
    resolved = [key for key, questions in requests.items() if questions and
                all((key, index) in answers for index in range(len(questions)))]
    return {'active': active, 'answered': list(answers.values()), 'resolved_request_ids': resolved,
            'answers_are_approvals': False}


def observed_source(turn, reference):
    """Normalize a supported UI read's omitted question metadata without guessing.

    read_thread can retain the exact native UI message but omit its structured
    questions. Recover only known immutable structure whose id AND full rendered
    text still match the fresh read. Other changed payload stays changed.
    """
    current = source(turn)
    fresh_finals = {f['id']: f['text'] for f in current['finals']}
    captured_finals = {f['id']: f['text'] for f in reference['finals']}
    known = {q['id']: q for q in current['questions']}
    for question in reference.get('questions', []):
        key = question['id']
        if (key not in known and key in fresh_finals and key in captured_finals
                and fresh_finals[key] == captured_finals[key]):
            known[key] = json.loads(encoded(question))
    order = [q['id'] for q in reference.get('questions', []) if q['id'] in known]
    order += [q['id'] for q in current['questions'] if q['id'] not in order]
    current['questions'] = [known[key] for key in order]
    return current


def ownership_only(db, fact):
    """Only exact metadata-only manager acknowledgments are mechanically inert.

    Never infer absence of blockers from free prose. Historical prose remains
    visible; the supported form has no field capable of carrying a result.
    """
    helper = db.execute('SELECT workstream FROM helper WHERE thread=?', (fact['thread'],)).fetchone()
    if not helper:
        return False
    value = json.loads(fact['source'])
    if value['phase'] != 'reported_outcome' or value['questions'] or value['error'] or len(value['finals']) != 1:
        return False
    def unique_object(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError('Duplicate metadata key')
            result[key] = item
        return result

    try:
        metadata = json.loads(value['finals'][0]['text'], object_pairs_hook=unique_object)
        valid = (isinstance(metadata, dict) and set(metadata) == {'type', 'worker', 'workstream'}
                and metadata['type'] == 'ownership_only'
                and metadata['workstream'] == helper['workstream']
                and isinstance(metadata['worker'], str) and metadata['worker'] == uid(metadata['worker']))
        # A mixed "Accept ownership and deliver X" request is not bookkeeping.
        # Only an absent request or the same exact metadata object is inert.
        return valid and all(not u['text'].strip() or
            json.loads(u['text'], object_pairs_hook=unique_object) == metadata for u in value['users'])
    except (ValueError, TypeError, AttributeError):
        return False


def quiet_result(db, fact):
    """Classify only mechanically nonactionable results, at every delivery boundary.

    A quiet source never overrides a real owner finding, structured question or
    native error. Unknown prose is intentionally not classified as quiet.
    """
    value = json.loads(fact['source'])
    questions = question_state(value)
    if (value['phase'] != 'reported_outcome' or questions['active'] or value['error']
            or value.get('native_status') in {'failed', 'interrupted'}):
        return None
    judgment = db.execute('SELECT value FROM judgment WHERE fact=? AND actor=?',
                          (fact['id'], fact['owner'])).fetchone()
    if judgment:
        owner = json.loads(judgment['value'])
        if (owner.get('open_decisions') or owner.get('outcome_kind') != 'other'
                or owner.get('task_state', 'unknown') not in {'unknown', 'working', 'queued'}):
            return None
    if (questions['answered'] and not questions['active']
            and not any(f['id'] not in questions['resolved_request_ids'] and f['text'].strip()
                        for f in value['finals'])):
        return {'state': 'suppressed_nonactionable', 'disposition': 'record_only_nonactionable',
                'reason': 'Native choice already answered; no new result or pending question'}
    texts = [f['text'].strip() for f in value['finals'] if f['text'].strip()]
    if not texts or all(re.fullmatch(r'<DONT_NOTIFY\s*/>|<DONT_NOTIFY>\s*</DONT_NOTIFY>', t)
                        for t in texts):
        return {'state': 'suppressed_nonactionable', 'disposition': 'record_only_nonactionable',
                'reason': 'Empty or DONT_NOTIFY-only result; internal source retained'}
    if ownership_only(db, fact):
        return {'state': 'suppressed_ownership_only', 'disposition': 'record_only_ownership',
                'reason': 'Ownership-only manager bookkeeping; internal source retained'}
    return None


def enqueue(db, root, fact, recipient):
    if db.execute('SELECT 1 FROM notice WHERE fact=? AND recipient=?', (fact['id'], recipient)).fetchone():
        return  # Never reformat, resend or overwrite an already registered notice.
    if newer_same_turn_outcome(db, fact['id']):
        return  # A resolved error must not be resurrected by coordinator fallback.
    body = json.loads(body_for(root, fact, recipient))
    judgment = db.execute('SELECT value FROM judgment WHERE fact=? AND actor=?',
                          (fact['id'], fact['owner'])).fetchone()
    if judgment:
        body['owner_judgment'] = json.loads(judgment['value'])
    task = db.execute('SELECT charter FROM task WHERE thread=?', (fact['thread'],)).fetchone()
    body['task_title'] = task['charter']
    spec = json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
    body['task_title'] = next((t.get('display_title',task['charter']) for t in spec['tasks']
                               if t['thread']==fact['thread']),task['charter'])
    # Source prose is untrusted: never copy its commands or arbitrary link labels.
    # Only a bounded, readable local artifact inside the explicit workspace is usable.
    artifacts = capture_artifacts(root, fact, body['artifact_links'], spec)
    if artifacts:
        body['review_artifact'] = artifacts[0]
        body['review_artifacts'] = artifacts
    final_text = '\n'.join(x['text'] for x in json.loads(fact['source'])['finals'])
    body['review_readiness_reported'] = bool(reports_review_ready(final_text) and body.get('review_artifact'))
    directory = absolute(root) / 'notices'
    directory.mkdir(mode=0o700, exist_ok=True)
    packet = notice_path(root, fact['id'], recipient)
    body['source_sha256'] = hashlib.sha256(fact['source'].encode()).hexdigest()
    preserve_snapshot(packet, encoded(body).encode())
    summary = visible_notice(body, packet)
    quiet = quiet_result(db, fact)
    integrity = {'packet_sha256': hashlib.sha256(packet.read_bytes()).hexdigest()}
    if quiet:
        integrity['suppression_reason'] = quiet['reason']
    db.execute('INSERT OR IGNORE INTO notice VALUES (?,?,?,?,NULL,?)',
               (fact['id'], recipient, summary, quiet['state'] if quiet else 'pending', encoded(integrity)))


def visible_notice(packet, path):
    labels = {'reported_outcome': 'result available', 'needs_input': 'input requested',
              'turn_failed': 'turn error; task outcome unverified',
              'turn_interrupted': 'turn interrupted; task outcome unverified'}
    title = ' '.join(str(packet['task_title']).split())[:100]
    label = labels.get(packet['phase'], 'source update; review required')
    artifact = packet.get('review_artifact')
    if packet['phase'] == 'reported_outcome' and packet.get('review_readiness_reported'):
        label = 'review-ready (worker report; acceptance pending)'
    if (packet.get('capture_context') or {}).get('source_is_latest_at_capture') is False:
        label = 'historical evidence only; current outcome unverified'
    review = f'\n[Review artifact](<{artifact["path"]}>)' if artifact else ''
    if packet.get('receive_command'):
        return (f'Task updates: 1 {label}. Task: {title}.{review}\n'
                'Before acting, use the recipient check; a fork must ignore inherited notices.\n'
                f'Details: {path}')
    # Keep legacy packet/body integrity checks compatible. Never rewrite or replay old notices.
    return f'Task updates: 1 {label}. Task: {title}.{review}\nDetails: {path}'


def receive_notice(root, generation, recipient):
    """Read one attempted notice in its actual recipient; never change ledger state."""
    actor = uid(os.environ.get('CODEX_THREAD_ID', ''))
    if uid(os.environ.get('CODEX_SESSION_ID', '')) != actor:
        raise ValueError('Actual recipient runtime identity required')
    recipient, generation = uid(recipient), uid(generation)
    if actor != recipient:
        raise ValueError('Notice belongs to another chat; ignore inherited notice')
    root = absolute(root)
    path = absolute(root / 'tracking.sqlite')
    if (not root.is_dir() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077
            or not path.is_file() or path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077):
        raise ValueError('Private owned tracking state required')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        policy = db.execute('SELECT * FROM policy').fetchone()
        spec = json.loads(policy['spec'])
        fact = db.execute('SELECT * FROM fact WHERE id=?', (generation,)).fetchone()
        notice = db.execute('SELECT * FROM notice WHERE fact=? AND recipient=?',
                            (generation, recipient)).fetchone()
        if (not fact or actor not in {fact['owner'], spec['root_thread']}
                or not notice or notice['attempted'] is None):
            raise ValueError('No attempted notice for this actual recipient')
        packet_path, packet = private_notice(root, generation, recipient)
        integrity = json.loads(notice['outcome'] or '{}').get('packet_sha256')
        if (integrity != hashlib.sha256(packet_path.read_bytes()).hexdigest()
                or packet['generation'] != generation or packet['recipient'] != actor
                or packet['source_thread'] != fact['thread'] or packet['source_turn'] != fact['turn']
                or packet['owner'] != fact['owner'] or packet['phase'] != fact['phase']
                or packet['source_fact'] != fact['path']
                or packet['source_sha256'] != hashlib.sha256(fact['source'].encode()).hexdigest()
                or absolute(fact['path']).read_text() != fact['source']):
            raise ValueError('Notice/source integrity mismatch; do not consume')
        quiet = quiet_result(db, fact)
        if quiet:
            return {'disposition': quiet['disposition'], 'generation': generation,
                    'recipient': actor, 'action': quiet['reason'] + '; no notification or follow-up'}
        floor = spec.get('delivery', {}).get('activation_floor', 0)
        if type(floor) not in (int, float) or not math.isfinite(floor) or not 0 <= floor <= time.time():
            raise ValueError('Valid current delivery activation floor required')
        if not after_delivery_floor(fact, floor, db):
            return {'disposition': 'record_only_pre_activation', 'generation': generation,
                    'recipient': actor, 'action': 'Earlier delivery epoch; retain evidence without replay'}
        if not owner_route_eligible(db,spec,fact,actor):
            return {'disposition':'record_only_unreviewed_or_stale','generation':generation,
                    'recipient':actor,'action':'Current meaningful owner review required; retain without presentation'}
        if db.execute('SELECT 1 FROM judgment WHERE fact=? AND actor=?', (generation, actor)).fetchone():
            return {'disposition': 'already_acknowledged', 'generation': generation,
                    'recipient': actor, 'action': 'No new work; preserve prior judgment'}
        if not policy['enabled'] or not policy['last_time'] <= time.time() < policy['expires']:
            raise ValueError('Tracking inactive or expired; notice is historical evidence only')
        return {'disposition': 'recipient_verified', 'notice': packet,
                'source_is_untrusted_data': True, 'objective_completion_verified': False}
    finally:
        db.close()


def receive_notice_path(root, path):
    """Parse routing metadata internally; never return a foreign packet's contents."""
    root, path = absolute(root), absolute(path)
    if path.parent != root / 'notices':
        raise ValueError('Notice must be inside this tracking root')
    packet = json.loads(path.read_text())
    generation, recipient = uid(packet['generation']), uid(packet['recipient'])
    if path != notice_path(root, generation, recipient):
        raise ValueError('Notice path does not match its routing identity')
    return receive_notice(root, generation, recipient)


def reports_review_ready(text):
    """Accept only a standalone present-tense declaration, not a substring."""
    # Missing a paraphrase is preferable to asserting future/conditional readiness.
    # This remains a worker report, never an owner acceptance or objective state.
    declaration = (r'(?:(?:the |this )?(?:pair|draft|artifact|result|cover|slides|video|'
                   r'carousel|files|report)(?: is| are)? )?'
                   r'(?:ready for (?:your |visual )?review|review[- ]ready)')
    # A readiness heading may contain only a Markdown review link after the colon.
    # Arbitrary trailing qualifiers (tomorrow/if/after another pass) remain refused.
    heading = declaration + r':\s*(?:\*\*)?\[[^\]\n]+\]\((?:<[^<>\n]+>|[^\s)]+)\)(?:\*\*)?\.?'
    return (any(re.fullmatch(declaration, sentence.strip(), re.I)
                for sentence in re.split(r'[.!?\n]+', text))
            or any(re.fullmatch(heading, line.strip(), re.I) for line in text.splitlines()))


ARTIFACT_TOTAL_BYTES = 512 * 1024 * 1024


def local_artifact(link, workspace, policy=None, budget=None):
    """Validate a source link as data; never fetch URLs or execute/render a file."""
    if (not isinstance(link, str) or len(link) > 4096
            or any(ord(c) < 32 or c in '<>\\' for c in link)):
        return None
    try:
        path = Path(link)
        if not path.is_absolute():
            return None
        path = path.resolve(strict=True)
        if any(ord(c) < 32 or c in '<>\\' for c in str(path)):
            return None
        roots = [Path(workspace).resolve(strict=True)]
        limit = 32 * 1024 * 1024
        if policy is not None:
            if (not isinstance(policy, dict) or set(policy) != {'roots', 'max_bytes'}
                    or type(policy['max_bytes']) is not int
                    or not 1 <= policy['max_bytes'] <= 512 * 1024 * 1024
                    or not isinstance(policy['roots'], list) or len(policy['roots']) > 16):
                return None
            for root in policy['roots']:
                if not isinstance(root, str) or not Path(root).is_absolute():
                    return None
                resolved = Path(root).resolve(strict=True)
                if not resolved.is_dir() or resolved == Path(resolved.anchor):
                    return None
                roots.append(resolved)
            limit = policy['max_bytes']
        if not any(path.is_relative_to(root) for root in roots):
            return None
        if path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.pdf',
                                      '.mp4', '.mov', '.webm', '.html', '.md', '.txt'}:
            return None
        if budget is not None:
            limit = min(limit, budget['remaining'])
        if not path.is_file() or not 0 < path.stat().st_size <= limit:
            return None
        size = 0
        fingerprint = hashlib.sha256()
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= limit:
                return None
            while size < before.st_size:
                chunk = stream.read(min(1024 * 1024, before.st_size-size))
                if not chunk:break
                size += len(chunk)
                if budget is not None:budget['remaining'] -= len(chunk)
                fingerprint.update(chunk)
            after = os.fstat(stream.fileno())
        current = path.stat()
        if (path.resolve(strict=True) != path or size != before.st_size or not 0 < size <= limit
                or any(getattr(before, k) != getattr(after, k)
                or getattr(after, k) != getattr(current, k)
                for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))):
            return None
        return {'path': str(path), 'size_bytes': size, 'sha256': fingerprint.hexdigest()}
    except (OSError, ValueError, RuntimeError):
        return None


def capture_artifacts(root, fact, links, spec):
    """One immutable capture per fact, shared by owner/root/fallback packets."""
    directory = absolute(root) / 'artifact-evidence'
    directory.mkdir(mode=0o700,exist_ok=True)
    path = absolute(directory / (uid(fact['id'])+'.json'))
    binding = {'source_sha256':hashlib.sha256(fact['source'].encode()).hexdigest(),
               'links':links,'policy_sha256':digest({'workspace':spec['workspace'],
                                                    'policy':spec.get('artifact_policy')})}
    if path.exists():
        if (path.stat().st_uid != os.getuid() or path.stat().st_mode & 0o077
                or path.stat().st_size > 65536):
            raise ValueError('Private bounded artifact evidence required')
        saved = json.loads(path.read_text())
        if any(saved.get(k) != v for k,v in binding.items()) or not artifact_set_bounded(saved['artifacts']):
            raise ValueError('Artifact evidence binding changed; preserve and hold')
        return saved['artifacts']
    artifacts,seen,budget = [],set(),{'remaining':ARTIFACT_TOTAL_BYTES}
    for link in links:
        try: target = str(Path(link).resolve(strict=True))
        except (OSError,ValueError,RuntimeError):continue
        if target in seen:continue
        seen.add(target)
        artifact = local_artifact(link,spec['workspace'],spec.get('artifact_policy'),budget)
        if artifact:artifacts.append(artifact)
    preserve_snapshot(path,encoded(dict(binding,artifacts=artifacts)).encode())
    return artifacts


def artifact_set_bounded(artifacts):
    return (isinstance(artifacts,list) and len(artifacts)<=6
            and all(isinstance(a,dict) and isinstance(a.get('path'),str)
                    and type(a.get('size_bytes')) is int and a['size_bytes']>0
                    and isinstance(a.get('sha256'),str) for a in artifacts)
            and len({a['path'] for a in artifacts}) == len(artifacts)
            and sum(a['size_bytes'] for a in artifacts)<=ARTIFACT_TOTAL_BYTES)


def verify_artifacts(artifacts,workspace,policy=None):
    if not artifact_set_bounded(artifacts):return False
    budget={'remaining':ARTIFACT_TOTAL_BYTES}
    return all(local_artifact(a['path'],workspace,policy,budget)==a for a in artifacts)


def private_notice(root, generation, recipient):
    path = notice_path(root, generation, recipient)
    return path, json.loads(path.read_text())


def notice_path(root, generation, recipient):
    opaque = digest({'generation': uid(generation), 'recipient': uid(recipient)})[:32]
    return absolute(absolute(root) / 'notices' / (opaque + '.json'))


def newer_same_turn_outcome(db, fact_id):
    candidates = db.execute("SELECT newer.* FROM fact older JOIN fact newer "
                      "ON newer.thread=older.thread AND newer.turn=older.turn AND newer.rowid>older.rowid "
                      "WHERE older.id=? AND older.phase IN ('turn_failed','turn_interrupted') "
                      "AND newer.phase='reported_outcome' ORDER BY newer.rowid DESC",
                      (fact_id,)).fetchall()
    return next((fact for fact in candidates if not quiet_result(db, fact)
                 and not json.loads(fact['source']).get('error')), None)


def supersede_unattempted_errors(db):
    # Preserve source facts, packets, bodies and all attempted outcomes unchanged.
    for notice in db.execute("SELECT * FROM notice WHERE state='pending' AND attempted IS NULL").fetchall():
        newer = newer_same_turn_outcome(db, notice['fact'])
        if newer:
            try:
                outcome = json.loads(notice['outcome'] or '{}')
            except ValueError:
                outcome = None
            if not isinstance(outcome, dict):
                outcome = {'previous_outcome': notice['outcome']}
            outcome['superseded_by'] = newer['id']
            outcome['reason'] = 'Later successful source final for the exact same native turn'
            db.execute("UPDATE notice SET state='superseded_before_attempt',outcome=? "
                       "WHERE fact=? AND recipient=? AND state='pending' AND attempted IS NULL",
                       (encoded(outcome), notice['fact'], notice['recipient']))


def authorized_baseline_fragment(spec, task, value):
    declared = next((t.get('active_baseline_floor', {}) for t in spec['tasks']
                     if t['thread'] == task['thread']), {})
    return (value['observation']['quality'] == 'unconfirmed'
            and value['turn'] == declared.get('turn') and bool(declared.get('official_evidence'))
            and digest(value) == declared.get('source_sha256')
            and any(value == old for old in json.loads(task['baseline'])))


def record(db, root, task, turn, now, capture_context=None):
    spec = json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
    if owner_reviewed(spec) and db.execute('SELECT 1 FROM helper WHERE thread=?',(task['thread'],)).fetchone():
        raise ValueError('Friday conversations are not worker observation sources')
    if owner_reviewed(spec) and not db.execute('SELECT 1 FROM helper WHERE thread=?',(task['owner'],)).fetchone():
        raise ValueError('Current registered Friday assignment required; preserved legacy source is held')
    value = source(turn)
    if capture_context:
        value['capture_context'] = capture_context
    if not value['phase']:
        return None
    # Suppress ONLY exact, persisted self-generated inputs, never arbitrary prose.
    user_texts = [u['text'] for u in value['users']]
    if user_texts and all(db.execute('SELECT 1 FROM notice WHERE recipient=? AND body=?',
                                   (task['thread'], text)).fetchone() for text in user_texts):
        return None
    fingerprint = source_digest(value)
    generation = str(uuid.uuid5(uuid.NAMESPACE_URL, task['thread'] + ':' + fingerprint))
    previous = db.execute('SELECT * FROM fact WHERE id=?', (generation,)).fetchone()
    if previous:
        if absolute(previous['path']).read_text() != previous['source']:
            raise ValueError('Immutable source fact changed; preserve and hold')
        confirm_terminal(db, previous, value, now)
        return None
    raw = encoded(value).encode()
    if len(raw) > 524288:
        raise ValueError('Source fact exceeds 512 KiB; preserve watermark for recovery')
    path = absolute(root) / 'facts' / (generation + '.json')
    preserve_snapshot(path, raw)
    db.execute('INSERT INTO fact VALUES (?,?,?,?,?,?,?,?,?)',
               (generation, task['thread'], turn['id'], value['phase'], fingerprint,
                task['owner'], now, str(path), encoded(value)))
    fact = db.execute('SELECT * FROM fact WHERE id=?', (generation,)).fetchone()
    if value['phase'] not in {'working', 'observed_request'}:
        enqueue(db, root, fact, fact['owner'])
        spec = json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
        if not owner_reviewed(spec) and spec.get('delivery', {}).get('direct_to_root') is True:
            enqueue(db, root, fact, spec['root_thread'])
    if value['phase'] == 'reported_outcome':
        supersede_unattempted_errors(db)
    if capture_context and not capture_context.get('source_is_latest_at_capture'):
        return generation  # Historical proof must not replace current operational state.
    if quiet_result(db, fact):
        return generation  # Quiet bookkeeping/sentinels do not change accepted operational state.
    previous_state = db.execute('SELECT state FROM task_state WHERE thread=?', (task['thread'],)).fetchone()
    if previous_state and previous_state['state'] in {'paused', 'waiting_for_owner', 'waiting_external'}:
        return generation  # Runtime activity cannot resolve an explicit hold or pause.
    state = {'working': 'working', 'needs_input': 'waiting_for_owner'}.get(value['phase'], 'unknown')
    reason = {'working': 'New native request is in progress; prior objective state superseded',
              'needs_input': 'New structured native question requires a human response',
              'turn_failed': 'Native turn error confirmed; overall task outcome requires owner judgment',
              'turn_interrupted': 'Native turn interrupted; this is not a task failure or an established pause'}.get(
                  value['phase'], 'New source outcome/request supersedes prior classification; owner judgment required')
    db.execute('UPDATE task_state SET state=?,reason=?,next_action=?,resume_condition=NULL,evidence=?,updated=? WHERE thread=?',
               (state, reason, 'Owner reviews current generation and any unresolved decisions',
                encoded([str(path)]), now, task['thread']))
    return generation


def read_snapshot(root, thread, now=None):
    with database(root) as db:
        spec = configuration(db, now, renew=True)
        if thread not in observed_threads(db, spec):
            raise ValueError('Task outside current observation scope; retained history is not enrollment')
        task = db.execute('SELECT * FROM task WHERE thread=?', (thread,)).fetchone()
        if task is None:
            raise ValueError('Task outside exact authorized scope')
        return spec, dict(task)


def validate_read(db, task, now=None):
    configuration(db, now)
    current = db.execute('SELECT * FROM task WHERE thread=?', (task['thread'],)).fetchone()
    if current is None or any(current[k] != task[k] for k in ('baseline', 'watermark', 'owner', 'charter')):
        raise ValueError('Task scope or watermark changed during read; no commit')


def reconcile_task(root, reader, thread, now=None):
    spec, task = read_snapshot(root, thread, now)
    if task['baseline'] is None:
        # Every API request is outside SQLite locks. Check revocation between reads
        # and again before consuming their results; an in-flight read is not cancelled.
        metadata = reader.call('thread/read', {'threadId': thread, 'includeTurns': False})['thread']
        if metadata.get('cwd') != spec['workspace']:
            raise ValueError('Official execution workspace metadata mismatch; enrollment held')
        with database(root) as db:
            validate_read(db, task, now)
        turns = reader.turns(thread, limit=1)['data']
        with database(root) as db:
            validate_read(db, task, now)
            if not turns or source(turns[0])['observation']['quality'] != 'usable':
                db.execute('UPDATE task SET error=? WHERE thread=?',
                           ('Incomplete native snapshot; baseline held, no task failure inferred', thread))
                return {'thread': thread, 'events': [], 'held': 'incomplete_native_snapshot'}
            baseline = [source(t) for t in turns]
            db.execute('UPDATE task SET baseline=?,watermark=?,error=NULL,checked=? WHERE thread=?',
                       (encoded(baseline), turns[0]['id'] if turns else '',
                        time.time() if now is None else now, thread))
            return {'thread': thread, 'baselined': True, 'events': []}
    head = reader.head(thread)['data']
    same_head = bool(head and head[0]['id'] == task['watermark'])
    if same_head and head[0].get('status') == 'completed':
        with database(root) as db:
            validate_read(db, task, now)
            previous = db.execute('SELECT * FROM fact WHERE thread=? AND turn=? ORDER BY rowid DESC LIMIT 1',
                                  (thread, task['watermark'])).fetchone()
            if previous:
                if absolute(previous['path']).read_text() != previous['source']:
                    raise ValueError('Immutable source changed; quiet check held')
                known = json.loads(previous['source'])
            else:
                known = next((s for s in json.loads(task['baseline']) if s['turn'] == task['watermark']), {})
            summary = source(head[0])
            # Completed input is immutable in the native turn model: followups
            # start a new turn. Final/question revisions remain summary-visible.
            keys = ('finals', 'questions', 'error')
            awaiting_terminal = (previous is not None and known.get('native_status') != 'completed'
                                 and terminal_confirmation(db, previous) is None)
            if (not awaiting_terminal and known.get('finals') and not known.get('questions') and not summary.get('questions')
                    and all(known.get(k) == summary.get(k) for k in keys)):
                db.execute('UPDATE task SET checked=?,error=NULL,gap=NULL WHERE thread=?',
                           (time.time() if now is None else now, thread))
                return {'thread': thread, 'events': [], 'quiet_head': True}
    # Read bounded pages to the saved head. On a gap do not consume any page.
    turns, cursor, found = [], None, False
    for _ in range(3):
        with database(root) as db:
            validate_read(db, task, now)
        page = reader.turns(thread, cursor, limit=1 if same_head else 20)
        for turn in page['data']:
            turns.append(turn)
            if turn['id'] == task['watermark']:
                found = True
                break
        if found or not page.get('nextCursor'):
            found = found or task['watermark'] == ''
            break
        cursor = page['nextCursor']
    with database(root) as db:
        validate_read(db, task, now)
        observed = time.time() if now is None else now
        # An exact authorized shadow is a no-replay floor, not a usable head.
        # It may be crossed only when a newer head is itself usable.
        if (turns and source(turns[0])['observation']['quality'] != 'usable') or any(
                source(turn)['observation']['quality'] != 'usable'
                and not authorized_baseline_fragment(spec, task, source(turn)) for turn in turns):
            db.execute('UPDATE task SET error=? WHERE thread=?',
                       ('Unconfirmed native snapshot in batch; watermark held, no task failure inferred', thread))
            return {'thread': thread, 'events': [], 'held': 'unconfirmed_native_snapshot'}
        if not found:
            db.execute('UPDATE task SET gap=? WHERE thread=?',
                       (encoded({'watermark': task['watermark'], 'pages': 3, 'cursor': cursor}), thread))
            return {'thread': thread, 'gap': True, 'events': []}
        baseline = json.loads(task['baseline'])
        events = []
        for turn in reversed(turns):
            # Baseline suppresses old payload only: its later question/final revision is new work.
            if any(source_digest(source(turn)) == source_digest(old) for old in baseline):
                continue
            event = record(db, root, task, turn, observed)
            if event:
                events.append(event)
        db.execute('UPDATE task SET watermark=?,gap=NULL,error=NULL,checked=? WHERE thread=?',
                   (turns[0]['id'] if turns else task['watermark'], observed, thread))
        return {'thread': thread, 'events': events}


def catchup(root, reader, thread, turn_id):
    """Explicit exact-turn exception only, fixed in the fresh authorization file."""
    spec, task = read_snapshot(root, thread)
    if {'thread': thread, 'turn': turn_id} not in spec.get('catchup', []):
        raise ValueError('Historical catchup not explicitly authorized')
    if task['baseline'] is None:
        raise ValueError('Baseline enrollment required')
    cursor = None
    for _ in range(3):
        with database(root) as db:
            validate_read(db, task)
        page = reader.turns(thread, cursor)
        for turn in page['data']:
            if turn['id'] == turn_id:
                with database(root) as db:
                    validate_read(db, task)
                    return record(db, root, task, turn, time.time(),
                        {'kind': 'explicit_named_catchup', 'latest_turn_at_capture': task['watermark'],
                         'source_is_latest_at_capture': task['watermark'] == turn_id,
                         'instruction': 'Past source evidence only; later turns may supersede any action or hold.'})
        cursor = page.get('nextCursor')
        if not cursor:
            break
    raise ValueError('Exact authorized turn absent from bounded pages; no drain')


def observed_threads(db, spec):
    registered = [r['thread'] for r in db.execute('SELECT thread FROM task')]
    selected = spec.get('observation_threads', registered)
    if (not isinstance(selected, list) or not selected or len(selected) != len(set(selected))
            or any(not isinstance(t, str) or t not in registered for t in selected)):
        raise ValueError('Distinct exact registered observation scope required')
    if owner_reviewed(spec):
        managers = {r['thread'] for r in db.execute('SELECT thread FROM helper')}
        owners = {r['thread']:r['owner'] for r in db.execute('SELECT thread,owner FROM task')}
        selected = [t for t in selected if t not in managers and owners[t] in managers]
    return selected


def terminal_confirmation(db, fact):
    if db is None or not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='fact_terminal'").fetchone():
        return None
    row = db.execute('SELECT * FROM fact_terminal WHERE fact=?',(fact['id'],)).fetchone()
    if (row and row['turn']==fact['turn'] and row['source_sha256']==hashlib.sha256(fact['source'].encode()).hexdigest()):
        return row['completed']
    return None


def confirm_terminal(db, fact, value, now):
    ended = value.get('observation',{}).get('completed_at')
    if (value.get('native_status') != 'completed' or value.get('error') is not None
            or type(ended) not in (int,float) or not math.isfinite(ended) or not 0 < ended <= now
            or source_digest(value) != fact['hash']):
        return
    # Separate append-only proof: never rewrite captured source, packets or attempts.
    db.execute('''CREATE TABLE IF NOT EXISTS fact_terminal
                  (fact TEXT PRIMARY KEY,turn TEXT,source_sha256 TEXT,completed REAL,observed REAL)''')
    db.execute('INSERT OR IGNORE INTO fact_terminal VALUES (?,?,?,?,?)',
               (fact['id'],fact['turn'],hashlib.sha256(fact['source'].encode()).hexdigest(),ended,now))


def after_delivery_floor(fact, floor, db=None):
    if not floor:
        return True
    if fact['created'] < floor:
        return False
    data = json.loads(fact['source'])
    observation = data.get('observation', {})
    when = observation.get('completed_at')
    if type(when) not in (int,float) or not math.isfinite(when):
        when = terminal_confirmation(db,fact)
    if type(when) not in (int, float) or not math.isfinite(when):
        # A post-epoch turn is current even if its final item precedes the
        # terminal timestamp. The recipient still needs fresh normal completion
        # before certifying a deliverable; this is only the no-replay gate.
        when = observation.get('started_at')
    return type(when) in (int, float) and math.isfinite(when) and when >= floor


def dispatch_eligible(db, fact, floor):
    if not after_delivery_floor(fact,floor,db):return False
    if floor:
        value=json.loads(fact['source'])
        if (value['phase']=='reported_outcome' and value.get('native_status')=='inProgress'
                and not value.get('error') and not question_state(value)['active']
                and terminal_confirmation(db,fact) is None):
            # A one-shot notice must not arrive before its deliverable can pass
            # the canonical normal-completion guard. Keep the original pending
            # packet; a fresh full read releases it, without retry or rewriting.
            return False
    return True


def reviewed_route(db, spec, fact, recipient):
    if not owner_reviewed(spec):return True
    if (db.execute('SELECT 1 FROM helper WHERE thread=?',(fact['thread'],)).fetchone()
            or json.loads(fact['source']).get('capture_context',{}).get('source_is_latest_at_capture') is False):
        return False
    managers={r['thread'] for r in db.execute('SELECT thread FROM helper')}
    task = db.execute('SELECT owner,watermark,error,gap FROM task WHERE thread=?',(fact['thread'],)).fetchone()
    latest=next((row for row in db.execute('SELECT * FROM fact WHERE thread=? ORDER BY rowid DESC',(fact['thread'],))
                 if json.loads(row['source']).get('capture_context',{}).get('source_is_latest_at_capture') is not False),None)
    if (not latest or latest['id']!=fact['id'] or not task or task['watermark']!=fact['turn']
            or task['owner']!=fact['owner'] or fact['owner'] not in managers):
        return False
    if recipient==spec['root_thread'] and fact['owner']!=spec['root_thread']:
        row=db.execute('SELECT value FROM judgment WHERE fact=? AND actor=?',
                       (fact['id'],fact['owner'])).fetchone()
        return bool(row and meaningful_judgment(json.loads(row['value']),json.loads(fact['source'])))
    return recipient==fact['owner']


def owner_route_eligible(db,spec,fact,recipient):
    if not reviewed_route(db,spec,fact,recipient):return False
    if not owner_reviewed(spec):return True
    task=db.execute('SELECT error,gap FROM task WHERE thread=?',(fact['thread'],)).fetchone()
    return bool(task and not task['error'] and not task['gap'])


def deliver_one(root, runner=subprocess.run, now=None):
    with database(root) as db:
        # Sample after serialization: a concurrent reader may have advanced
        # last_time while this delivery attempt waited to acquire the writer lock.
        now = time.time() if now is None else now
        spec = configuration(db, now)
        if delivery_mode(spec) != 'explicit_internal':
            return None
        supersede_unattempted_errors(db)  # Recheck under the same transaction as the claim.
        # Independent source facts are already a durable direct coordinator channel.
        # Root fallback is a single fact notice, never a scan of child chats.
        floor = spec.get('delivery', {}).get('activation_floor', 0)
        if type(floor) not in (int, float) or not math.isfinite(floor) or floor < 0 or floor > now:
            raise ValueError('Valid delivery activation floor required')
        selected = set(observed_threads(db, spec))
        for fact in ([] if owner_reviewed(spec) else db.execute("SELECT f.* FROM fact f WHERE created>=? AND created<=? AND owner!=? "
                               "AND phase NOT IN ('working','observed_request') AND NOT EXISTS "
                               "(SELECT 1 FROM notice n WHERE n.fact=f.id AND n.recipient=?)",
                               (floor, now - 120, spec['root_thread'], spec['root_thread'])).fetchall()):
            if fact['thread'] in selected and dispatch_eligible(db, fact, floor):
                enqueue(db, root, fact, spec['root_thread'])
        notice = next((n for n in db.execute("SELECT n.* FROM notice n JOIN fact f ON f.id=n.fact "
                            "WHERE n.state='pending' AND f.created>=? ORDER BY n.rowid", (floor,))
                       if (lambda f: f['thread'] in selected and dispatch_eligible(db, f, floor)
                           and owner_route_eligible(db,spec,f,n['recipient']))(
                           db.execute('SELECT * FROM fact WHERE id=?',(n['fact'],)).fetchone())), None)
        if not notice:
            return None
        fact = db.execute('SELECT * FROM fact WHERE id=?', (notice['fact'],)).fetchone()
        expected_packet_hash = None
        try:
            path, packet = private_notice(root, notice['fact'], notice['recipient'])
            expected_packet_hash = json.loads(notice['outcome'] or '{}').get('packet_sha256')
            valid_packet = (packet['generation'] == fact['id'] and packet['recipient'] == notice['recipient']
                and expected_packet_hash == hashlib.sha256(path.read_bytes()).hexdigest()
                and packet['phase'] == fact['phase'] and packet['source_fact'] == fact['path']
                and packet['source_thread'] == fact['thread'] and packet['source_turn'] == fact['turn']
                and packet['owner'] == fact['owner']
                and packet['source_sha256'] == hashlib.sha256(fact['source'].encode()).hexdigest()
                and visible_notice(packet, path) == notice['body']
                and fact['phase'] in {'reported_outcome', 'needs_input', 'turn_failed', 'turn_interrupted'})
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            valid_packet = False
        if not valid_packet:
            db.execute("UPDATE notice SET state='held_unverified_payload',outcome=? WHERE fact=? AND recipient=?",
                       ('Legacy or mismatched payload held; no alert sent', notice['fact'], notice['recipient']))
            return {'generation': notice['fact'], 'state': 'held_unverified_payload'}
        artifacts = packet.get('review_artifacts', [packet['review_artifact']] if packet.get('review_artifact') else [])
        if not verify_artifacts(artifacts, spec['workspace'], spec.get('artifact_policy')):
            db.execute("UPDATE notice SET state='held_unavailable_artifact' WHERE fact=? AND recipient=?",
                       (notice['fact'], notice['recipient']))
            return {'generation': notice['fact'], 'state': 'held_unavailable_artifact'}
        if absolute(fact['path']).read_text() != fact['source']:
            raise ValueError('Source integrity mismatch; dispatch held')
        quiet = quiet_result(db, fact)
        if quiet:
            outcome = encoded({'packet_sha256': expected_packet_hash,
                               'suppression_reason': quiet['reason']})
            db.execute("UPDATE notice SET state=?,outcome=? "
                       'WHERE fact=? AND recipient=? AND attempted IS NULL',
                       (quiet['state'], outcome, notice['fact'], notice['recipient']))
            return {'generation': notice['fact'], 'state': quiet['state']}
        # Commit claim BEFORE calling the external queue. Never retry uncertain claims.
        db.execute("UPDATE notice SET state='claimed',attempted=? WHERE fact=? AND recipient=?",
                   (now, notice['fact'], notice['recipient']))
    with database(root) as db:
        # Commit the final dispatch gate without holding a writer lock across I/O.
        # This claimed attempt is in flight; disable blocks subsequent attempts,
        # but cannot retract a command already handed to the external queue.
        spec = configuration(db)
        if owner_reviewed(spec) and (fact['thread'] not in observed_threads(db,spec)
                or not dispatch_eligible(db,fact,spec['delivery'].get('activation_floor',0))
                or not owner_route_eligible(db,spec,fact,notice['recipient'])):
            db.execute("UPDATE notice SET state='held_changed_after_claim',outcome=? WHERE fact=? AND recipient=?",
                       (encoded({'packet_sha256':expected_packet_hash,'reason':'Source, scope or owner changed before queue handoff'}),
                        fact['id'],notice['recipient']))
            return {'generation':fact['id'],'state':'held_changed_after_claim'}
    try:
        result = runner([spec['binary'], 'queue', '--thread', notice['recipient'],
                         '--message', notice['body']], capture_output=True, text=True, timeout=15)
        state = 'accepted_unverified' if result.returncode == 0 else 'failed_no_retry'
        outcome = encoded({'packet_sha256': expected_packet_hash,
                           'returncode': result.returncode, 'stdout': result.stdout[:2000],
                           'stderr': result.stderr[:2000]})
    except (OSError, subprocess.TimeoutExpired) as exc:
        state, outcome = 'uncertain_no_retry', encoded({'packet_sha256': expected_packet_hash, 'error': str(exc)})
    with database(root) as db:
        db.execute('UPDATE notice SET state=?,outcome=? WHERE fact=? AND recipient=?',
                   (state, outcome, notice['fact'], notice['recipient']))
    return {'generation': notice['fact'], 'recipient': notice['recipient'], 'state': state}


def completion_handling(db, spec, fact, value, actor):
    """An active owner gets a parking-review obligation, never permission to move blindly."""
    if value.get('task_state') != 'completed' or not value.get('completion_evidence'):
        return {'action': 'preserve', 'reason': 'Objective not accepted complete'}
    helper_ids = {r['thread'] for r in db.execute('SELECT thread FROM helper')}
    metadata = next((t for t in spec['tasks'] if t['thread'] == fact['thread']), {})
    if fact['thread'] in helper_ids | {spec['root_thread']} or metadata.get('recurring') or metadata.get('protected'):
        return {'action': 'preserve', 'reason': 'Manager, recurring or protected task'}
    latest = next((f for f in db.execute('SELECT * FROM fact WHERE thread=? ORDER BY rowid DESC', (fact['thread'],))
                   if json.loads(f['source']).get('capture_context', {}).get('source_is_latest_at_capture') is not False), None)
    current = db.execute('SELECT s.state,t.owner FROM task_state s JOIN task t ON s.thread=t.thread '
                         'WHERE s.thread=?', (fact['thread'],)).fetchone()
    if (not latest or latest['id'] != fact['id'] or not current or current['state'] != 'completed'
            or actor not in {current['owner'], spec['root_thread']}
            or db.execute("SELECT 1 FROM decision WHERE thread=? AND state='open'", (fact['thread'],)).fetchone()):
        return {'action': 'preserve', 'reason': 'Newer work, retained hold or historical judgment'}
    return {'action': 'review_references_parking', 'thread_id': fact['thread'], 'turn_id': fact['turn'],
            'source_fact': fact['path'], 'executor': 'Already-active authorized owner using official tools',
            'requires': ['Fresh latest normal completed turn and full objective/return-contract assessment',
                         'Existing References, current membership, protected/recurring/manager and review gates',
                         'Fresh pre-move revalidation, complete affected orders and final membership proof'],
            'already_in_references': 'No move; preserve prior receipt and newer work',
            'fallback': 'Existing nightly completion reconciliation; no new wake, callback or poll'}


def acknowledge(root, value):
    actor = uid(os.environ.get('CODEX_THREAD_ID', ''))
    if uid(os.environ.get('CODEX_SESSION_ID', '')) != actor:
        raise ValueError('Actual recipient runtime identity required')
    with database(root) as db:
        spec = configuration(db)
        fact = db.execute('SELECT * FROM fact WHERE id=?', (uid(value['generation']),)).fetchone()
        if not fact or actor not in {fact['owner'], spec['root_thread']}:
            raise ValueError('Foreign source or recipient')
        if absolute(fact['path']).read_text() != fact['source']:
            raise ValueError('Immutable source fact changed; acknowledgment held')
        if not db.execute('SELECT 1 FROM notice WHERE fact=? AND recipient=? AND attempted IS NOT NULL',
                          (fact['id'], actor)).fetchone():
            raise ValueError('Notice not attempted to this recipient')
        if value['outcome_kind'] not in {'draft_ready', 'produced_artifact', 'needs_human_input',
                                       'failed', 'sent_reported', 'sent_verified', 'other'}:
            raise ValueError('Unknown outcome judgment')
        if value['outcome_kind'] == 'sent_verified' and not value.get('transport_evidence'):
            raise ValueError('SENT requires actual transport evidence pointers')
        if not value.get('next_action') or not isinstance(value.get('open_decisions'), list):
            raise ValueError('Explicit next action and open decision list required')
        old = db.execute('SELECT value FROM judgment WHERE fact=? AND actor=?', (fact['id'], actor)).fetchone()
        if old:
            if old['value'] != encoded(value):
                raise ValueError('Judgment immutable; preserve earlier decision evidence')
            return {'acknowledged': fact['id'], 'actor': actor, 'duplicate': True,
                    'completion_handling': completion_handling(db, spec, fact, value, actor)}
        db.execute('INSERT OR IGNORE INTO judgment VALUES (?,?,?,?)',
                   (fact['id'], actor, encoded(value), time.time()))
        states = {'queued', 'working', 'waiting_for_owner', 'waiting_external', 'paused',
                  'blocked', 'review_ready', 'completed', 'unknown'}
        state = value.get('task_state', 'unknown')
        if state not in states or not value.get('reason'):
            raise ValueError('Explicit task state and evidence-based reason required')
        if state == 'paused' and not value.get('resume_condition'):
            raise ValueError('Paused tasks require a discoverable resume condition')
        if state == 'completed' and not value.get('completion_evidence'):
            raise ValueError('Objective completion requires explicit acceptance evidence')
        is_history = json.loads(fact['source']).get('capture_context', {}).get('source_is_latest_at_capture') is False
        current_facts = [f for f in db.execute('SELECT * FROM fact WHERE thread=? ORDER BY rowid DESC',
                                             (fact['thread'],))
                         if json.loads(f['source']).get('capture_context', {}).get('source_is_latest_at_capture') is not False]
        if not quiet_result(db, fact) and not is_history and current_facts and current_facts[0]['id'] == fact['id']:
            prior_state = db.execute('SELECT state FROM task_state WHERE thread=?', (fact['thread'],)).fetchone()
            if prior_state['state'] == 'paused' and state != 'paused' and not value.get('resume_evidence'):
                raise ValueError('Changing an explicit pause requires resume evidence')
            db.execute('UPDATE task_state SET state=?,reason=?,next_action=?,resume_condition=?,evidence=?,updated=? WHERE thread=?',
                       (state, value['reason'], value['next_action'], value.get('resume_condition'),
                        encoded(value.get('evidence', [])), time.time(), fact['thread']))
        sequence = db.execute('SELECT rowid FROM fact WHERE id=?', (fact['id'],)).fetchone()[0]
        def put_decision(key, decision, decision_state):
            previous = db.execute('SELECT f.rowid FROM decision d JOIN fact f ON f.id=d.generation '
                                  'WHERE d.thread=? AND d.key=?', (fact['thread'], key)).fetchone()
            if is_history or (previous and previous[0] > sequence):
                return  # Preserve old judgment, never roll current decisions backwards.
            db.execute('INSERT INTO decision VALUES (?,?,?,?,?) ON CONFLICT(thread,key) '
                       'DO UPDATE SET value=excluded.value,state=excluded.state,generation=excluded.generation',
                       (fact['thread'], key, encoded(decision), decision_state, fact['id']))
        for decision in value['open_decisions']:
            if not isinstance(decision, dict) or not decision.get('key') or not decision.get('reason'):
                raise ValueError('Open decision needs stable key and reason')
            put_decision(decision['key'], decision, 'open')
        for key in value.get('resolved_decisions', []):
            if not value.get('resolution_evidence'):
                raise ValueError('Resolving a hold requires explicit evidence')
            # Resolution tombstones also prevent a late first open from creating
            # a key whose newer resolution arrived before its original open.
            put_decision(key, {'key': key, 'reason': 'Explicit evidence resolves this decision',
                              'evidence': value['resolution_evidence']}, 'resolved')
        if (actor != spec['root_thread'] and (not owner_reviewed(spec)
                or (meaningful_judgment(value,json.loads(fact['source'])) and reviewed_route(db,spec,fact,spec['root_thread'])))):
            enqueue(db, root, fact, spec['root_thread'])
        handling = completion_handling(db, spec, fact, value, actor)
    return {'acknowledged': fact['id'], 'actor': actor, 'completion_handling': handling}


def helper_members(db, members):
    if not isinstance(members, list) or not members:
        raise ValueError('Explicit distinct existing workstream members required')
    canonical = [uid(member) for member in members]
    if len(set(canonical)) != len(canonical):
        raise ValueError('Distinct workstream members required')
    helper_ids = {r['thread'] for r in db.execute('SELECT thread FROM helper')}
    for member in canonical:
        if member in helper_ids or not db.execute('SELECT 1 FROM task WHERE thread=?', (member,)).fetchone():
            raise ValueError('Members must be existing ordinary scoped tasks; depth remains one')
    return canonical


def ownership_configuration(db):
    """Root-only metadata changes do not require or enable event delivery."""
    spec = json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
    if (os.environ.get('CODEX_THREAD_ID') != spec['root_thread'] or
            os.environ.get('CODEX_SESSION_ID') != spec['root_thread']):
        raise ValueError('Fixed root runtime required; actual thread and session must match')
    return spec


def placement_attestation(value, thread, members, now=None):
    """Root attests exact desktop-tool results, never backend section metadata."""
    placement = value.get('placement', {})
    if not isinstance(placement, dict):
        raise ValueError('Desktop placement object required')
    observed = placement.get('observed_at')
    now = time.time() if now is None else now
    if (placement.get('surface') != 'codex_desktop_sidebar'
            or placement.get('host_id') != 'local' or placement.get('thread_id') != thread
            or not isinstance(observed, (int, float)) or isinstance(observed, bool)
            or not 0 <= now - observed <= 300):
        raise ValueError('Fresh exact local desktop placement attestation required')
    section = placement.get('inventory_section', {})
    if not isinstance(section, dict):
        raise ValueError('Desktop section inventory object required')
    section_id = uid(placement.get('section_id', ''))  # Built-in pinned is not a custom section.
    keys = section.get('itemKeys', [])
    manager_key = 'codex:thread:local:' + thread
    if (section.get('sectionId') != section_id or not isinstance(keys, list) or not keys
            or not all(isinstance(k, str) for k in keys) or len(set(keys)) != len(keys)
            or next((k for k in keys if ':project:' not in k), None) != manager_key):
        raise ValueError('Desktop section inventory must place the exact manager first')
    evidence = value.get('official_evidence', {})
    if (not isinstance(value.get('authorization_evidence'), str)
            or not value['authorization_evidence'].strip()
            or not isinstance(evidence, dict) or not all(
            isinstance(evidence.get(k), str) and evidence[k].strip()
            for k in ('placement', 'inventory', 'charter_acceptance'))):
        raise ValueError('Human authorization, official placement, inventory and charter acceptance pointers required')
    exceptions = placement.get('location_exceptions', {})
    if not isinstance(exceptions, dict) or set(exceptions) - set(members):
        raise ValueError('Only exact member location exceptions are allowed')
    for member in members:
        key = 'codex:thread:local:' + member
        if key in keys:
            if member in exceptions:
                raise ValueError('Location exception conflicts with manager section')
            continue
        exception = exceptions.get(member, {})
        if not isinstance(exception, dict):
            raise ValueError('Exact location exception object required')
        other = exception.get('inventory_section', {})
        other_keys = other.get('itemKeys') if isinstance(other, dict) else None
        if (not isinstance(other, dict) or not isinstance(exception.get('reason'), str) or not exception['reason'].strip()
                or not isinstance(exception.get('official_inventory_evidence'), str)
                or not exception['official_inventory_evidence'].strip()
                or other.get('sectionId') == section_id
                or not isinstance(other_keys, list) or not all(isinstance(k, str) for k in other_keys)
                or len(set(other_keys)) != len(other_keys) or key not in other_keys):
            raise ValueError('Each member needs exact desktop placement or an explicit location exception')
        uid(other.get('sectionId', ''))


def expected_member_owners(db, members, value):
    expected = value.get('expected_owners')
    if not isinstance(expected, dict) or set(expected) != set(members):
        raise ValueError('Exact expected owner for every cutover member required')
    current = {member: db.execute('SELECT owner FROM task WHERE thread=?',
                                 (member,)).fetchone()['owner'] for member in members}
    if any(current[member] != uid(expected[member]) for member in members):
        raise ValueError('Owner changed; preserve state and refresh the exact cutover')
    return current


def assign_member_owners(db, spec, members, owner):
    for member in members:
        db.execute('UPDATE task SET owner=? WHERE thread=?', (owner, member))
    for task in spec['tasks']:
        if task['thread'] in members:
            task['owner'] = owner
    db.execute('UPDATE policy SET spec=?', (encoded(spec),))


def admission(root, value):
    """Durable admission/reuse request; root executes official saved-project tools."""
    with database(root) as db:
        spec = ownership_configuration(db)
        if not value.get('coherent_scope') or not value.get('observed_pressure') or not value.get('name'):
            raise ValueError('Scope, observed coordination pressure and name required')
        key = value['workstream']
        helper = db.execute('SELECT thread FROM helper WHERE workstream=?', (key,)).fetchone()
        if helper:
            return {'action': 'reuse_existing', 'thread': helper['thread']}
        if not spec.get('project_id'):
            raise ValueError('Saved project configuration required before creation packet')
        uid(spec['project_id'])
        helper_members(db, value.get('members'))
        old = db.execute('SELECT * FROM reservation WHERE workstream=?', (key,)).fetchone()
        if old:
            if old['judgment'] != encoded(value):
                raise ValueError('Reservation scope immutable; reconcile the existing reservation')
            return dict(old)
        count = db.execute('SELECT count(*) FROM helper').fetchone()[0]
        pending = db.execute("SELECT count(*) FROM reservation WHERE state='official_tools_required'").fetchone()[0]
        if count + pending >= spec.get('max_helpers', 4):
            raise ValueError('Helper capacity reached; no count-only automatic creation')
        reservation = str(uuid.uuid4())
        db.execute('INSERT INTO reservation VALUES (?,?,?,\'official_tools_required\',NULL)',
                   (key, reservation, encoded(value)))
        return {'action': 'official_tools_required', 'reservation': reservation,
                'workstream': key, 'max_depth': 1,
                'steps': ['create_thread in exact saved project', 'verify actual execution cwd',
                          'place manager first in its exact custom section',
                          'record fresh desktop placement and expected owners; never duplicate on timeout']}


def bind_helper(root, value):
    """Commit the fixed root's official-tool attestation; never fabricate API proof."""
    with database(root) as db:
        spec = ownership_configuration(db)
        pending = db.execute('SELECT * FROM reservation WHERE id=?',
                             (uid(value['reservation']),)).fetchone()
        if not pending:
            raise ValueError('Unknown admission reservation')
        if pending['receipt']:
            if pending['receipt'] != encoded(value):
                raise ValueError('Reservation already bound; never create a replacement')
            return {'action': 'already_bound', 'thread': value['thread']}
        thread = uid(value['thread'])
        if not spec.get('project_id') or value.get('created_project_id') != spec['project_id']:
            raise ValueError('Exact saved project creation evidence required')
        if value.get('actual_execution_cwd') != spec['workspace']:
            raise ValueError('Actual execution cwd evidence required')
        evidence = value.get('official_evidence', {})
        if not isinstance(evidence, dict) or not all(
                isinstance(evidence.get(k), str) and evidence[k].strip() for k in ('create', 'execution_cwd')):
            raise ValueError('Exact official operation pointers required; metadata alone insufficient')
        if thread == spec['root_thread'] or db.execute('SELECT 1 FROM task WHERE thread=?', (thread,)).fetchone():
            raise ValueError('Existing task cannot be promoted or replaced')
        members = helper_members(db, json.loads(pending['judgment']).get('members'))
        placement_attestation(value, thread, members)
        expected_member_owners(db, members, value)
        charter = json.loads(pending['judgment'])['coherent_scope']
        name = json.loads(pending['judgment'])['name']
        db.execute('INSERT INTO helper VALUES (?,?)', (thread, pending['workstream']))
        db.execute('INSERT INTO task VALUES (?,?,?,NULL,NULL,NULL,NULL,NULL)',
                   (thread, spec['root_thread'], charter))
        db.execute('INSERT INTO task_state VALUES (?,\'unknown\',?,?,NULL,?,?)',
                   (thread, 'New helper bound by root official-tool attestation; baseline pending',
                    'Baseline helper, then review owned workstream', encoded(evidence), time.time()))
        spec['tasks'].append({'thread': thread, 'owner': spec['root_thread'],
                              'title': name, 'charter': charter})
        spec['helpers'].append({'thread': thread, 'name': name, 'workstream': pending['workstream']})
        assign_member_owners(db, spec, members, thread)
        db.execute("UPDATE reservation SET state='bound_by_root_attestation',receipt=? WHERE id=?",
                   (encoded(value), pending['id']))
        db.execute('INSERT INTO scope_change VALUES (?,?,?)',
                   (pending['id'], time.time(), encoded(value)))
        return {'action': 'bound_by_root_attestation', 'thread': thread,
                'future_members': members, 'prior_fact_routes_unchanged': True,
                'delivery_state_unchanged': True}


def cutover_members(root, value):
    """Move only named future member routes to an existing helper, atomically."""
    with database(root) as db:
        spec = ownership_configuration(db)
        operation = 'membership-' + uid(value['operation_id'])
        prior = db.execute('SELECT evidence FROM scope_change WHERE id=?', (operation,)).fetchone()
        if prior:
            if json.loads(prior['evidence'])['receipt'] != value:
                raise ValueError('Cutover receipt immutable; never reuse its operation id')
            return {'action': 'already_applied', 'operation_id': value['operation_id']}
        owner = uid(value['helper'])
        if not db.execute('SELECT 1 FROM helper WHERE thread=?', (owner,)).fetchone():
            raise ValueError('Existing registered helper required; no promotion or creation')
        members = helper_members(db, value.get('members'))
        placement_attestation(value, owner, members)
        before = expected_member_owners(db, members, value)
        assign_member_owners(db, spec, members, owner)
        db.execute('INSERT INTO scope_change VALUES (?,?,?)',
                   (operation, time.time(), encoded({'receipt': value, 'prior_owners': before})))
        return {'action': 'members_cut_over', 'helper': owner, 'future_members': members,
                'prior_fact_routes_unchanged': True, 'delivery_state_unchanged': True}


def set_helper_capacity(root, value):
    """Apply an explicitly authorized root capacity change, never auto-split work."""
    with database(root) as db:
        spec = ownership_configuration(db)
        operation = 'capacity-' + uid(value['operation_id'])
        prior = db.execute('SELECT evidence FROM scope_change WHERE id=?', (operation,)).fetchone()
        if prior:
            if json.loads(prior['evidence'])['receipt'] != value:
                raise ValueError('Capacity receipt immutable; never reuse its operation id')
            return {'action': 'already_applied', 'operation_id': value['operation_id']}
        capacity, expected = value.get('capacity'), value.get('expected_capacity')
        observed = value.get('observed_at')
        if (type(capacity) is not int or not 1 <= capacity <= 8 or type(expected) is not int
                or not isinstance(observed, (int, float)) or isinstance(observed, bool)
                or not 0 <= time.time() - observed <= 300
                or not isinstance(value.get('authorization_evidence'), str)
                or not value['authorization_evidence'].strip()):
            raise ValueError('Bounded capacity and fresh explicit human authorization required')
        old = spec.get('max_helpers', 4)
        if old != expected:
            raise ValueError('Capacity changed; refresh the exact request')
        occupied = db.execute('SELECT COUNT(*) FROM helper').fetchone()[0]
        occupied += db.execute("SELECT COUNT(*) FROM reservation WHERE state='official_tools_required'").fetchone()[0]
        if capacity < occupied:
            raise ValueError('Capacity cannot discard registered or reserved helpers')
        spec['max_helpers'] = capacity
        db.execute('UPDATE policy SET spec=?', (encoded(spec),))
        db.execute('INSERT INTO scope_change VALUES (?,?,?)',
                   (operation, time.time(), encoded({'receipt': value, 'prior_capacity': old})))
        return {'action': 'capacity_changed', 'capacity': capacity, 'delivery_state_unchanged': True}


def status(root, board_view=False):
    with database(root) as db:
        data = {table: [dict(row) for row in db.execute('SELECT * FROM ' + table)]
                for table in ('policy', 'lease', 'task', 'fact', 'notice', 'judgment', 'helper',
                              'reservation', 'task_state', 'decision', 'scope_change') if not board_view or
                table in {'policy', 'task', 'helper', 'task_state', 'decision'}}
        if board_view:
            # Board work is bounded by current task count, not accumulated receipts.
            # JSON1 is part of the bundled SQLite; exclude explicitly historical proof.
            data['fact'] = [dict(row) for row in db.execute("SELECT f.* FROM fact f WHERE rowid IN "
                "(SELECT MAX(rowid) FROM fact WHERE COALESCE(json_extract(source, "
                "'$.capture_context.source_is_latest_at_capture'),1)!=0 GROUP BY thread)")]
            data['delivery'] = {}
            for fact in data['fact']:
                acknowledgments = {r['actor'] for r in db.execute('SELECT actor FROM judgment WHERE fact=?', (fact['id'],))}
                data['delivery'][fact['id']] = [
                    {'recipient': r['recipient'], 'state': 'acknowledged' if r['recipient'] in acknowledgments else r['state']}
                    for r in db.execute('SELECT recipient,state FROM notice WHERE fact=?', (fact['id'],))]
        return data


def render_board(root):
    """Standalone private HTML with a local staleness clock; no remote dependency."""
    data = status(root, board_view=True)
    policy = data['policy'][0]
    spec = json.loads(policy['spec'])
    now = time.time()
    states = {s['thread']: s for s in data['task_state']}
    metadata = {t['thread']: t for t in spec['tasks']}
    names = {spec['root_thread']: spec.get('root_name', 'Root')}
    names.update({h['thread']: h.get('name', h['workstream']) for h in spec.get('helpers', [])})
    esc = lambda v: html.escape(str(v if v is not None else 'Unknown'))
    rows = []
    for task in data['task']:
        state = states[task['thread']]
        facts = [f for f in data['fact'] if f['thread'] == task['thread']]
        latest = max(facts, key=lambda f: f['created']) if facts else None
        decisions = [json.loads(d['value'])['reason'] for d in data['decision']
                     if d['thread'] == task['thread'] and d['state'] == 'open']
        coverage = ('Disabled' if not policy['enabled'] else 'Lease expired' if now >= policy['expires'] else
                    'Read error: ' + task['error'] if task['error'] else
                    'History gap' if task['gap'] else 'Not baselined' if task['baseline'] is None else
                    'Stale; last successful read exceeds two intervals' if not task['checked'] or
                    now - task['checked'] > 2 * spec.get('interval', 30) else 'Connected; recent supported read')
        updated = time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(state['updated'])) if state['updated'] else 'Unknown'
        link = 'codex://threads/' + task['thread']
        artifact_links = []
        if latest:
            value = json.loads(latest['source'])
            for message in value['finals']:
                for url in re.findall(r'\]\((https?://[^\s)]+)\)', message['text'])[:6]:
                    artifact_links.append('<a href="' + esc(url) + '">Artifact</a>')
        checked = task['checked'] or 0
        coverage_cell = '<span data-checked="' + str(checked) + '">' + esc(coverage) + '</span>'
        if latest:
            delivered = data['delivery'].get(latest['id'], [])
            coverage_cell += '<br>' + esc('; '.join(names.get(n['recipient'], 'Owner') + ': ' + n['state']
                for n in delivered) or 'Working/request observation; no result notice due')
        rows.append('<tr>' + ''.join('<td>' + cell + '</td>' for cell in [
            '<a href="' + link + '">' + esc(metadata[task['thread']].get('title', task['charter'])) + '</a>',
            esc(state['state']), esc(state['reason']), esc(state['next_action']),
            esc(state['resume_condition'] or '—'), esc(names.get(task['owner'], 'Registered owner')),
            esc(updated), coverage_cell, esc('; '.join(decisions) or 'None recorded; not proof of none'),
            (('<a href="' + esc(Path(latest['path']).as_uri()) + '">Current generation facts</a><br>'
              + '<br>'.join(artifact_links)) if latest else 'Baseline only')
        ]) + '</tr>')
    page = '''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Managed work board</title><style>body{font:15px system-ui;margin:24px;color:#18202c;background:#f5f7fa}
h1{font-size:26px}table{border-collapse:collapse;background:white;width:100%}td,th{text-align:left;padding:12px;border-bottom:1px solid #dae0e8;vertical-align:top}th{position:sticky;top:0;background:#e9eef5}a{color:#155cba}p{max-width:1000px}</style>
<h1>Managed work</h1><p>Private operational snapshot. Unknown states stay visible. Native idle, notLoaded and turn completion do not determine task completion or pause. Reload this file to see updates.</p>'''
    page += '<p>Generated ' + esc(time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(now))) + ' · ' + str(len(rows)) + ' managed chats · six-hour capture leases renew while this explicit standing policy is enabled. Queue acceptance is not recipient acknowledgment.</p>'
    page += '<p>Delivery: ' + ('record only; no chat callbacks' if delivery_mode(spec) == 'record_only'
                              else 'explicit internal opt-in; acceptance is not acknowledgment') + '</p>'
    counts = {}
    for state in states.values():
        counts[state['state']] = counts.get(state['state'], 0) + 1
    page += '<p>' + esc(' · '.join(str(v) + ' ' + k for k, v in sorted(counts.items()))) + '</p>'
    page += '<div style="overflow:auto"><table><thead><tr>' + ''.join('<th>' + s + '</th>' for s in ['Task', 'State', 'Reason', 'Next action', 'Resume condition', 'Owner', 'Meaningful update', 'Reporting coverage', 'Open decisions', 'Generation']) + '</tr></thead><tbody>' + ''.join(rows) + '</tbody></table></div>'
    # Constants/numeric policy values only in executable code; source prose is HTML escaped.
    page += '<script>setInterval(()=>{for(const e of document.querySelectorAll("[data-checked]")){if(Date.now()/1000-Number(e.dataset.checked)>' + str(2 * spec.get('interval', 30)) + ' && e.textContent.startsWith("Connected"))e.textContent="Stale; no fresh successful read"}},1000);</script>'
    path = absolute(root) / 'board.html'
    temp = absolute(root) / 'board.html.tmp'
    with temp.open('w') as handle:
        handle.write(page)
    temp.chmod(0o600)
    os.replace(temp, path)
    return str(path)


def delivery_loop(root, stop, runner=subprocess.run, failures=None):
    try:
        while not stop.is_set():
            with database(root) as db:
                if not db.execute('SELECT enabled FROM policy').fetchone()['enabled']:
                    stop.set()
                    break
                configuration(db, renew=True)
            for _ in range(8):
                if stop.is_set() or deliver_one(root, runner) is None:
                    break
            stop.wait(2)
    except Exception as exc:
        stop.set()
        disabled = False
        try:
            with database(root) as db:
                disabled = not db.execute('SELECT enabled FROM policy').fetchone()['enabled']
        except Exception:
            pass
        if not disabled:
            if failures is not None:
                failures.append(str(exc))
            print('Delivery held: ' + str(exc), file=sys.stderr, flush=True)


def watch(root):
    lock = open(absolute(root) / 'watch.lock', 'a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    reader = None
    failures = []
    delivery = threading.Thread(target=delivery_loop, args=(root, stop, subprocess.run, failures), daemon=True)
    delivery.start()
    try:
        while not stop.is_set():
            with database(root) as db:
                if not db.execute('SELECT enabled FROM policy').fetchone()['enabled']:
                    break
                spec = configuration(db, renew=True)
                threads = observed_threads(db, spec)
            if reader is None:
                reader = Reader(spec['binary'])
            for thread in threads:
                if stop.is_set():
                    break
                try:
                    reconcile_task(root, reader, thread)
                except Exception as exc:
                    with database(root) as db:
                        db.execute('UPDATE task SET error=? WHERE thread=?', (str(exc)[:2000], thread))
                    if reader.process.poll() is not None:
                        reader.close()
                        reader = None
                        break
            render_board(root)
            stop.wait(spec.get('interval', 30))
    finally:
        stop.set()
        if reader:
            reader.close()
        delivery.join(timeout=30)
        lock.close()
    if failures:
        raise RuntimeError('Delivery thread held: ' + failures[0])


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'ack', 'admit', 'bind-helper', 'cutover-members', 'set-helper-capacity'):
        commands.add_parser(name).add_argument('--input', required=True)
    for name in ('status', 'watch', 'tick', 'disable', 'board'):
        commands.add_parser(name)
    catch = commands.add_parser('catchup')
    catch.add_argument('--thread', required=True)
    catch.add_argument('--turn', required=True)
    receive = commands.add_parser('receive')
    receive.add_argument('--notice', required=True)
    args = parser.parse_args()
    root = absolute(args.root)
    if args.command == 'init':
        result = initialize(root, json.loads(absolute(args.input).read_text()))
    elif args.command == 'receive':
        result = receive_notice_path(root, args.notice)
    elif args.command == 'status':
        result = status(root)
    elif args.command == 'board':
        result = render_board(root)
    elif args.command in ('ack', 'admit', 'bind-helper', 'cutover-members', 'set-helper-capacity'):
        value = json.loads(absolute(args.input).read_text())
        result = {'ack': acknowledge, 'admit': admission, 'bind-helper': bind_helper,
                  'cutover-members': cutover_members,
                  'set-helper-capacity': set_helper_capacity}[args.command](root, value)
    elif args.command == 'disable':
        with database(root) as db:
            db.execute('UPDATE policy SET enabled=0')
        result = {'disabled': True, 'external_claims_preserved': True}
    elif args.command == 'watch':
        result = watch(root)
    else:
        with database(root) as db:
            spec = configuration(db, renew=True)
            threads = observed_threads(db, spec)
        reader = Reader(spec['binary'])
        try:
            result = (catchup(root, reader, args.thread, args.turn) if args.command == 'catchup' else
                      [reconcile_task(root, reader, t) for t in threads])
        finally:
            reader.close()
    print(encoded(result))


if __name__ == '__main__':
    main()
