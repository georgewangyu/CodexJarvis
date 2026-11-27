#!/usr/bin/env python3
"""Pure sidebar parking planner. Official tools perform all reads and mutations.

Semantic assessments are trusted operator inputs, not event/closure attestation.
No daemon, API client, callback, hook, queue or archive executor exists here.
"""
import argparse
import hashlib
import json
from pathlib import Path

# Protected identities come from explicit private configuration.
PROTECTED = set()


def sections(inventory):
    return inventory.get("sections", [])


def members(section):
    return [k.split(":")[-1] for k in section.get("itemKeys", [])
            if k.startswith("codex:thread:local:")]


def destination(inventory):
    found = [s for s in sections(inventory) if s.get("name") == "References"]
    if len(found) != 1:
        raise ValueError("existing References destination missing or ambiguous")
    return found[0]["sectionId"]


def plan(inventory, prior=None, protected=()):
    dest = destination(inventory)
    pinned = {t["id"] for t in inventory.get("pinnedThreads", [])}
    excluded = PROTECTED | pinned | set(protected)
    current = list(dict.fromkeys(t for s in sections(inventory)
                   if s["sectionId"] not in {dest, "pinned", "threads"}
                   for t in members(s) if t not in excluded))
    cursor = (prior or {}).get("completion_review_cursor", {})
    # The outstanding tail leads; reviewed pending work rotates to the back.
    previous = cursor.get("remainingQueue", [])
    ordered = list(dict.fromkeys([t for t in previous if t in current] + current))
    n = len(ordered)
    budget = min(120, max(24, 2 * n))
    count = min(n, budget // 2)
    return {"destination": dest, "member_count": n, "read_budget": budget,
            "review_ids": ordered[:count], "remainingQueue": ordered[count:],
            "revalidation_reserved": count, "max_seconds": 900}


def advance(planning, reviewed, parked=()):
    reviewed, parked = set(reviewed), set(parked)
    selected = planning["review_ids"]
    if not reviewed <= set(selected) or not parked <= reviewed:
        raise ValueError("review/parking outside current bounded plan")
    queue = ([t for t in selected if t not in reviewed]
             + planning["remainingQueue"]
             + [t for t in selected if t in reviewed and t not in parked])
    return {"remainingQueue": queue, "reviewedIds": [t for t in selected if t in reviewed],
            "nextThreadId": queue[0] if queue else None}


def fingerprint(read):
    return hashlib.sha256(json.dumps(read, sort_keys=True).encode()).hexdigest()


def eligible(bundle):
    """Verify mechanical gates; never infer semantic completion from phrasing."""
    read, assessment = bundle["read"], bundle["assessment"]
    current = bundle["current_read"]
    tid = read["thread"]["id"]
    inventory = bundle["inventory"]
    dest = destination(inventory)
    pinned = {t["id"] for t in inventory.get("pinnedThreads", [])}
    if tid in PROTECTED | pinned | set(bundle.get("protected", [])):
        return {"eligible": False, "reason": "protected"}
    locations = [s["sectionId"] for s in sections(inventory) if tid in members(s)]
    if locations == [dest]:
        return {"eligible": False, "reason": "already parked; preserve existing receipt and placement"}
    if locations != [bundle["source_section"]] or locations[0] == dest:
        return {"eligible": False, "reason": "source placement changed or ambiguous"}
    if current.get("thread", {}).get("id") != tid:
        return {"eligible": False, "reason": "wrong current task"}
    # Ignore runtime quiet as positive evidence; actively running is a veto.
    for observation in (read, current):
        status = observation.get("thread", {}).get("status", {})
        kind = status.get("type") if isinstance(status, dict) else status
        if kind not in {"idle", "notLoaded"}:
            return {"eligible": False, "reason": "active, waiting or unknown runtime status"}
    before, now = read.get("turns", []), current.get("turns", [])
    if not before or not now:
        return {"eligible": False, "reason": "missing terminal evidence"}
    a, b = before[0], now[0]  # read_thread is newest-first.
    if a.get("id") != b.get("id") or fingerprint(a) != fingerprint(b):
        return {"eligible": False, "reason": "resumed or changed turn"}
    finals = [i.get("text", "").strip() for i in b.get("items", [])
              if i.get("type") == "agentMessage" and i.get("phase") == "final_answer"]
    ended = b.get("completedAt")
    if (b.get("status") != "completed" or b.get("error") is not None
            or not isinstance(ended, (int, float)) or isinstance(ended, bool) or ended <= 0
            or not any(finals)):
        return {"eligible": False, "reason": "no normal completed final"}
    required_true = ("objective_complete", "return_contract_fulfilled", "ordinary_nonrecurring")
    required_false = ("required_gate_pending", "needs_input", "unfinished_backlog", "ambiguous")
    if (any(assessment.get(k) is not True for k in required_true)
            or any(assessment.get(k) is not False for k in required_false)):
        return {"eligible": False, "reason": "incomplete, pending, recurring or unknown"}
    if assessment.get("turn_id") != b["id"] or not assessment.get("reason") or not assessment.get("result_pointer"):
        return {"eligible": False, "reason": "unbound assessment or missing retained result"}
    # optional_future_offer is intentionally not a gate. Required work above is.
    return {"eligible": True, "thread_id": tid, "turn_id": b["id"],
            "source": locations[0], "destination": dest,
            "result_pointer": assessment["result_pointer"], "reason": assessment["reason"]}


def orders(inventory, batch, sources):
    dest = destination(inventory)
    if len(set(batch)) != len(batch):
        raise ValueError("duplicate moved task")
    affected = set(sources) | {dest}
    present = {s["sectionId"]: members(s) for s in sections(inventory) if s["sectionId"] in affected}
    if set(present) != affected:
        raise ValueError("affected section missing")
    if any([s["sectionId"] for s in sections(inventory) if t in members(s)] != [dest]
           for t in batch):
        raise ValueError("move unverified or duplicate source/destination placement")
    result = dict(present)
    result[dest] = list(batch) + [t for t in present[dest] if t not in batch]
    for sid, ids in result.items():
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate section membership")
        original = next(s for s in sections(inventory) if s["sectionId"] == sid)
        if len(ids) != len(original.get("itemKeys", [])):
            raise ValueError("mixed/remote/project membership needs manual full-order handling")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["plan", "check", "orders", "advance"])
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    b = json.loads(args.bundle.read_text())
    if args.operation == "plan":
        result = plan(b["inventory"], b.get("prior"), b.get("protected", []))
    elif args.operation == "check":
        result = eligible(b)
    elif args.operation == "orders":
        result = orders(b["inventory"], b["batch"], b["sources"])
    else:
        result = advance(b["planning"], b["reviewed"], b.get("parked", []))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
