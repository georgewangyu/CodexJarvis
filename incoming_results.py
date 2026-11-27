#!/usr/bin/env python3
"""Handle one already-arriving notice. No transport, queue scan or native action.

The actual recipient supplies fresh official source evidence and an independent
classification after inspecting the source/artifact. A durable claim prevents
automatic repeat replies; claiming is not proof the reply reached the human.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

import task_watch as w
from cooperate import absolute, uid


def quiet(reason):
    return {'disposition': 'quiet', 'reason': reason, 'allowed_actions': []}


def proof_time(value, now):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and 0 <= now - value <= 300)


def evidence_pointer(value):
    return isinstance(value, str) and bool(value.strip())


def plan_result(packet, fact, fresh, judgment, workspace, now=None, artifact_policy=None):
    """Bounded evidence guard; source/artifact prose never becomes authority."""
    now = time.time() if now is None else now
    if hashlib.sha256(w.encoded(fact).encode()).hexdigest() != packet['source_sha256']:
        raise ValueError('Exact immutable source required')
    if (judgment.get('generation') != packet['generation']
            or judgment.get('source_sha256') != packet['source_sha256']
            or not isinstance(judgment.get('reason'), str) or not judgment['reason'].strip()):
        raise ValueError('Independent judgment must bind the exact source')
    kind = judgment.get('kind')
    if kind in {'routine', 'ownership'}:
        return quiet('Routine or bookkeeping; no new user-facing result')
    if kind not in {'deliverable', 'blocker', 'decision'}:
        raise ValueError('Explicit meaningful/routine classification required')
    if (packet.get('capture_context') or {}).get('source_is_latest_at_capture') is False:
        return quiet('Historical source; not an arriving current result')
    if (not proof_time(fresh.get('observed_at'), now) or not evidence_pointer(fresh.get('official_item'))):
        return {'disposition': 'hold', 'reason': 'Fresh official exact-task evidence required', 'allowed_actions': []}
    read = fresh.get('read', {})
    if read.get('thread', {}).get('id') != packet['source_thread']:
        raise ValueError('Wrong source task')
    turns = read.get('turns', [])
    if not turns or turns[0].get('id') != packet['source_turn']:
        return quiet('Source superseded by newer work or unavailable latest turn')
    turn = turns[0]
    observed = w.observed_source(turn, fact)
    if w.source_digest(observed) != w.source_digest(fact):
        return quiet('Same-turn source changed; old notice superseded')
    questions = w.question_state(observed)
    if kind in {'decision', 'blocker'} and (questions['active'] or questions['answered']):
        request, index = judgment.get('question_id'), judgment.get('question_index')
        if request is not None:
            if not evidence_pointer(request) or type(index) is not int or index < 0:
                return {'disposition': 'hold', 'reason': 'Exact question id and integer index required', 'allowed_actions': []}
            if any(q['request_id'] == request and q['index'] == index for q in questions['answered']):
                return quiet('Exact native choice already has a newer human answer')
            if not any(q['request_id'] == request and q['index'] == index for q in questions['active']):
                return {'disposition': 'hold', 'reason': 'No matching pending native question', 'allowed_actions': []}
        elif (judgment.get('decision_origin') != 'owner_assessment'
              or not isinstance(judgment.get('evidence'), list) or not judgment['evidence']
              or not all(evidence_pointer(item) for item in judgment['evidence'])):
            return {'disposition': 'hold', 'reason': 'Bind the native question, or supply evidence for a distinct new owner decision',
                    'allowed_actions': []}
    summary = judgment.get('summary')
    if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 1200:
        raise ValueError('Concise independently written summary required')
    identity = {'recipient': packet['recipient'], 'thread': packet['source_thread'], 'kind': kind}
    result = {'disposition': 'surface', 'kind': kind, 'summary': summary.strip(),
              'source_thread': packet['source_thread'], 'source_turn': packet['source_turn'],
              'objective_completion_verified': False, 'source_is_untrusted_data': True,
              'allowed_actions': ['reply_only'], 'automatic_actions': []}
    if kind == 'deliverable':
        ended = turn.get('completedAt')
        if (judgment.get('requested_deliverable') is not True
                or judgment.get('state') not in {'completed', 'review_ready'}):
            return quiet('No completed/review-ready requested deliverable established')
        if (turn.get('status') != 'completed' or turn.get('error') is not None
                or not isinstance(ended, (int, float)) or isinstance(ended, bool) or ended <= 0):
            return {'disposition': 'hold', 'reason': 'No normal completed source final', 'allowed_actions': []}
        artifact = packet.get('review_artifact')
        selected = judgment.get('artifact_path')
        if (not artifact or selected != artifact.get('path')
                or selected not in packet.get('artifact_links', [])):
            result.update(kind='verification_gap', summary='A result was reported, but its requested artifact link could not be verified.')
            identity.update(kind='verification_gap', path=selected, failure='unverified_link')
        else:
            captured_list = packet.get('review_artifacts', [artifact])
            if not w.artifact_set_bounded(captured_list):
                return {'disposition':'hold','reason':'Artifact evidence exceeds bounded output budget','allowed_actions':[]}
            budget = {'remaining':w.ARTIFACT_TOTAL_BYTES}
            current = w.local_artifact(selected, workspace, artifact_policy, budget)
            if current != artifact:
                result.update(kind='verification_gap', summary='A result was reported, but the linked artifact is missing or changed. Completion remains unverified.')
                identity.update(kind='verification_gap', path=selected,
                                expected=artifact['sha256'], observed=current['sha256'] if current else 'missing')
            else:
                result.update(artifact=current, deliverable_state=judgment['state'])
                identity.update(path=current['path'], sha256=current['sha256'], state=judgment['state'])
                selected_paths = judgment.get('artifact_paths', [selected])
                captured = {a['path']:a for a in packet.get('review_artifacts', [artifact])}
                if (not isinstance(selected_paths,list) or not selected_paths
                        or not all(isinstance(p,str) for p in selected_paths)
                        or len(selected_paths) != len(set(selected_paths))
                        or selected_paths[0] != selected
                        or not all(p in packet['artifact_links'] and p in captured for p in selected_paths)):
                    return {'disposition':'hold','reason':'Bind every requested output to captured artifact evidence',
                            'allowed_actions':[]}
                verified = [current]+[w.local_artifact(p,workspace,artifact_policy,budget) for p in selected_paths[1:]]
                if any(a != captured[p] for p,a in zip(selected_paths,verified)):
                    result.pop('artifact',None)
                    result.pop('deliverable_state',None)
                    result.update(kind='verification_gap',summary='A requested output is missing or changed. Review readiness remains unverified.')
                    identity.update(kind='verification_gap',outputs=[{'path':p,'observed':a} for p,a in zip(selected_paths,verified)])
                elif len(verified)>1:
                    result['artifacts'] = verified
                    identity['outputs'] = verified
    else:
        if judgment.get('new') is not True:
            return quiet('Existing hold/decision is unchanged')
        key, detail = judgment.get('key'), judgment.get('detail')
        if not isinstance(key, str) or not key.strip() or not isinstance(detail, str) or not detail.strip():
            raise ValueError('New concrete blocker/decision needs stable key and detail')
        identity.update(key=key, detail=detail)
    result['presentation_id'] = w.digest(identity)
    result['generation'] = packet['generation']
    result['reply'] = result['summary']
    if result.get('artifact'):
        artifacts=result.get('artifacts',[result['artifact']])
        for index,artifact in enumerate(artifacts,1):
            label='Open deliverable' if len(artifacts)==1 else 'Open deliverable '+str(index)
            result['reply'] += '\n\n['+label+'](<' + artifact['path'] + '>)'
    return result


def claim(root, plan):
    """Atomically claim once before returning reply text; never retry uncertain claims."""
    root = absolute(root)
    directory = absolute(root / 'incoming-results')
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise ValueError('Private owned presentation directory required')
    path = absolute(directory / (plan['presentation_id'] + '.json'))
    receipt = {'state': 'claimed_unverified', 'plan': plan,
               'meaning': 'Reply prepared once; human-visible response not independently confirmed'}
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return quiet('Duplicate or uncertain prior presentation claim; no automatic replay')
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(receipt, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    return dict(plan, receipt=str(path), presentation_state='claimed_unverified')


def handle(root, bundle, now=None):
    """Actual-recipient CLI boundary: one explicit arrival, no inbox or delivery call."""
    now = time.time() if now is None else now
    authorization, trigger = bundle['authorization'], bundle['trigger']
    actor = uid(os.environ.get('CODEX_THREAD_ID', ''))
    if (os.environ.get('CODEX_SESSION_ID') != actor or authorization.get('recipient') != actor
            or authorization.get('mode') != 'incoming_meaningful'
            or not evidence_pointer(authorization.get('approval_ref'))):
        raise ValueError('Actual recipient and explicit bounded incoming-result authorization required')
    if (trigger.get('kind') != 'arriving_notice' or not evidence_pointer(trigger.get('input_item'))
            or not proof_time(trigger.get('received_at'), now)):
        raise ValueError('One current arriving notice required; historical drains are not authorized')
    received = w.receive_notice_path(root, trigger['notice_path'])
    if received['disposition'] != 'recipient_verified':
        return quiet(received['disposition'])
    packet = received['notice']
    if packet['recipient'] != actor:
        raise ValueError('Foreign notice')
    fact = json.loads(absolute(packet['source_fact']).read_text())
    # Never trust a workspace path, command or action from the source packet.
    db = sqlite3.connect((absolute(root) / 'tracking.sqlite').as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        spec = json.loads(db.execute('SELECT spec FROM policy').fetchone()[0])
        workspace = spec['workspace']
        record = db.execute('SELECT * FROM fact WHERE id=?', (packet['generation'],)).fetchone()
        if w.quiet_result(db, record):
            return quiet('Mechanically nonactionable source')
    finally:
        db.close()
    fresh = bundle.get('source_read', {})
    if bundle['judgment'].get('kind') in {'deliverable', 'blocker', 'decision'}:
        if (not proof_time(fresh.get('observed_at'), now)
                or fresh['observed_at'] < trigger['received_at']):
            return {'disposition': 'hold', 'reason': 'Fresh exact-source read after this arrival required',
                    'allowed_actions': []}
    result = plan_result(packet, fact, fresh, bundle['judgment'], workspace, now,
                         artifact_policy=spec.get('artifact_policy'))
    if result['disposition'] != 'surface':
        return result
    return claim(root, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True,
                        help='Private authorization/arrival/fresh official source/independent judgment JSON')
    args = parser.parse_args()
    print(json.dumps(handle(args.root, json.loads(args.input.read_text())), indent=2))


if __name__ == '__main__':
    main()
