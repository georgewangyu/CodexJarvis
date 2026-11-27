# Completed-worker References disposition

`task_parking.py` is pure functions plus a JSON CLI. Official tools perform all
reads, moves and ordering; an already-active authorized owner supplies semantic
assessments. It starts no job, callback or observer.

```sh
python3 task_parking.py plan --bundle /absolute/private/review-plan.json
python3 task_parking.py check --bundle examples/parking-bundle.json
python3 task_parking.py orders --bundle /absolute/private/post-move-orders.json
python3 task_parking.py advance --bundle /absolute/private/review-cursor.json
```

| Operation | Bundle fields | Meaning |
| --- | --- | --- |
| plan | `inventory`, optional `prior`, explicit `protected` ids | Bounded review ids, read/revalidation budgets, remaining queue |
| check | `read`, `current_read`, `assessment`, `inventory`, `source_section`, `protected` | Fresh completed-objective gates; no move |
| orders | post-move `inventory`, moved `batch` ids, `sources` section ids | Complete verified local-only source/destination orders |
| advance | `planning`, `reviewed`, `parked` ids | Fair next cursor; unchecked work first |

Use official newest-first latest-turn reads for `read` and `current_read`.
Re-read immediately before moving. Exact thread/turn and complete latest-turn
fingerprints must agree. A normal completed final, nonerror terminal time,
unchanged membership and no active/waiting runtime are mechanical gates.
Idle/notLoaded is only a veto check, never positive completion evidence.

Supply `assessment.turn_id`, retained `result_pointer`, concrete `reason`, and
explicit booleans: `objective_complete`, `return_contract_fulfilled` and
`ordinary_nonrecurring` true; `required_gate_pending`, `needs_input`,
`unfinished_backlog` and `ambiguous` false. Unknown/missing booleans preserve
the worker. Assess the complete durable objective and latest human direction,
not just its last turn. An optional future offer alone is not a required gate.

Always pass the union of controller, manager, recurring and other protected ids
from private configuration in `protected`; the reusable module hardcodes none.
Pinned chats are independently protected. References must be the single existing
destination. Already parked requires no second move and proves nothing about
newer work. Preserve review/input holds, blockers, pauses, unfinished backlogs,
ambiguity and resumed work.

After an eligible check, apply only the authorized exact official move, refresh
inventory and verify unique destination membership. `orders` refuses mixed
remote/project memberships rather than dropping them; handle those using manual
complete official item-key ordering. Preserve remaining relative order, retain
before/after evidence and verify final full orders. Parking does not archive,
delete, mark read, close a runtime worker, publish or settle an approval.

The optional fair planner reserves one revalidation read per candidate within
`min(120,max(24,2*N))` total reads and a 15-minute ceiling. This bounds an
already-authorized reconciliation, not permission for a scheduled scan. If no
owner is already handling the actual result, leave evidence for an existing
separately authorized fallback. Create no watcher or wake for this disposition.
