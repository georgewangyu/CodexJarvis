"""Tokenless, opt-in event filter; transport ownership is deliberately separate."""
import argparse
from contextlib import closing
import json
import sqlite3
import subprocess
import sys
import uuid


def uid(value):
    return str(uuid.UUID(value))


def event_key(event, config):
    if event.get('method') != 'turn/completed':
        return None
    params = event.get('params', {})
    if not isinstance(params, dict):
        raise ValueError('Invalid event params')
    thread = params.get('threadId')
    turn = params.get('turn', {})
    if not isinstance(turn, dict):
        raise ValueError('Invalid turn')
    turn_id, status = turn.get('id'), turn.get('status')
    if not isinstance(thread, str) or not isinstance(turn_id, str):
        raise ValueError('Missing task/turn identity')
    thread, turn_id = uid(thread), uid(turn_id)
    if [thread, turn_id] not in config['watch']:
        return None
    if status not in ('completed', 'failed', 'interrupted'):
        return None
    return thread, turn_id, status


def consume(stream, config, database, queue_binary='codex'):
    target = uid(config['target'])
    watch = [[uid(thread), uid(turn)] for thread, turn in config['watch']]
    if not watch:
        raise ValueError('Explicit worker/turn allowlist required')
    config = dict(config, watch=watch)
    for thread, turn in watch:
        if thread == target:
            raise ValueError('Cannot watch the receiving task')
    with closing(sqlite3.connect(database, timeout=5)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS delivery (thread TEXT, turn TEXT, status TEXT, outcome TEXT, PRIMARY KEY(thread, turn))')
        while True:
            line = stream.readline(1048577)
            if not line:
                break
            if len(line) > 1048576:
                raise ValueError('Event exceeds 1 MiB')
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('Event must be an object')
            key = event_key(event, config)
            if key is None:
                continue
            # Commit the claim BEFORE the external call. Crash/timeout means
            # uncertain delivery, never an automatic duplicate retry.
            inserted = db.execute('INSERT OR IGNORE INTO delivery VALUES (?, ?, ?, ?)', (*key, 'claimed')).rowcount
            db.commit()
            if not inserted:
                continue
            thread, turn, status = key
            message = (f'[CODEX_JARVIS_EVENT] Worker turn {status}. '
                       f'Task: codex://threads/{thread}; turn: {turn}. '
                       'This reports turn status only, not objective completion. '
                       'Inspect the result before claiming an artifact is ready. '
                       'Do not dispatch further work from this notice.')
            try:
                result = subprocess.run([queue_binary, 'queue', '--thread', target,
                                         '--message', message],
                                        capture_output=True, text=True, timeout=15)
                outcome = 'accepted_unverified' if result.returncode == 0 else 'failed_no_retry'
            except (OSError, subprocess.TimeoutExpired):
                outcome = 'uncertain_no_retry'
            db.execute('UPDATE delivery SET outcome=? WHERE thread=? AND turn=?', (outcome, thread, turn))
            db.commit()
            print(json.dumps({'thread': thread, 'turn': turn, 'outcome': outcome}), flush=True)
            if outcome != 'accepted_unverified':
                return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--state', required=True)
    args = parser.parse_args()
    with open(args.config) as handle:
        config = json.load(handle)
    return consume(sys.stdin, config, args.state)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(f'Bridge stopped: {type(exc).__name__}', file=sys.stderr)
        sys.exit(2)
