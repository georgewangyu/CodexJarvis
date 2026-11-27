# Sidebar configuration contract

`sidebar.py` accepts a JSON bundle with `config`, `inventory`, `bindings` and
`projects`. It has no network, queue or native mutation. Normalize official
output explicitly; preserve original official receipts privately and verify
their actual contents before trusting the bundle.

| Input | Fields |
| --- | --- |
| config | `version:1`, `host_id:local`, `project:{id,cwd}`, `controller:{id,title}`, `references_section_id`, `priority_section_ids`, `preserved_section_ids`, `protected_thread_ids`, `recurring_thread_ids`, `direct_workers`, `workstreams` |
| workstream | unique `key`, `name`, existing custom `section_id`, `manager:{id,title}` or null, exact `workers` UUID list |
| inventory | `sections:[{sectionId,name,itemKeys}]`, `threads:[{id,title}]`, `pinnedThreads:[{id}]` from fresh official local sidebar inventory |
| projects | normalized `[{id,name,cwd,host_id}]`; exact configured saved **Workspace** local project required |
| bindings | keyed by exact manager id, with the fields below |

Identity fields use canonical UUIDs. Local thread keys use
`codex:thread:local:<uuid>`. Built-in sections may use official string ids.
Project/remote item keys are retained in full orders. Never substitute backend
section metadata for Desktop `sectionId`/`itemKeys`.

Bindings contain `create:{thread_id,project_id,host_id,environment,item_id}`,
`execution:{thread_id,runtime_session,cwd,item_id}`, `charter_accepted:true`,
and `charter_item_id`. Host/environment must be local, actual execution cwd
must equal the saved project path, and the runtime session must match the
created thread. Opaque pointers are not authenticated by this script. Verify
exact official create/execution/acceptance items before supplying them. Summary
cwd cannot replace execution evidence. Existing manager reuse needs its retained
exact binding proof; missing proof holds setup rather than creating a duplicate.

Configured workstream sections must exist. Create sections manually through
supported tools when separately authorized, then refresh inventory. Explicitly
preserve all other sections. Every local workstream row must be classified as
a manager, owned/direct worker or protected/pinned task. Unknown rows, duplicate
ownership, manager cycles, ambiguous membership, missing titles and stale
References workers fail closed. Titles/locations never determine roles.
Protected, recurring and pinned workers keep placement/title and appear in `holds`.

A null manager in the layout produces a hold pointing to the separate
`manager-create` operation. Layout supplies no manager create arguments.
The exact controller uses the guarded operation after observed admission and
ownership-absence checks. It preserves the single returned id and verifies
native admission before recording an accepted owner.

`operations` contains draft supported title/move arguments. `section_orders`
contains complete desired item keys for affected sections; `before_orders`
retains their inverse evidence. Ordering is a separate official-app step after
moves, using the host's current full-order tool schema; this map is not itself a
tool argument object. Preserve project, remote and concurrent rows. Refresh and
replan when inventory changes, then verify final membership/orders. Do not blindly
replay an inverse after new work.

`ownership` is desired routing, not a committed ledger. Charter acceptance and
any authorized expected-owner CAS remain separate. Today exceptions preserve
logical managers and priority selection. No event enrollment or service activation
follows from planner output.

## Guarded creation

The verified root provisions one private `~/.config/codexjarvis/creation-authority.json`
from checked official controller, project, admitted manager and pending records.
The path uses the OS account home. Use an owner-only directory and an
owner-only regular, non-symlink file. Preserve
all accepted scopes and known pending ids when refreshing it. It is deployment
configuration, separate from an individual request. This repository neither
writes the live file nor supplies real ids.

The snapshot contains `version:1`, OS hostname `host`, the exact saved
`project:{id,cwd}`, exact `controller_id`, unique `managers` entries keyed by
canonical workstream, and `pending` mapping workstream to pending exact id.
Each accepted manager has `id`, matching `controller_id`, official
`placement_item_id` and the `binding` described above. Root reviews and
confirms exact official create, actual execution, charter acceptance and
placement contents before adopting any record. Missing or conflicting evidence
holds that scope. This snapshot and the current thread/session environment
form the local trust boundary. A same-account process can modify local state,
and the planner does not authenticate vendor pointers or intercept other app
operations. Use the verified Desktop bootstrap independently for root actions.

`--operation worker-create` runs in the owning Friday's current Desktop
session. Supply `workstream`, `project:{id,cwd}`, normalized current `projects`
inventory, logical `title` and `prompt`. The planner compares the actor's
actual matching `CODEX_THREAD_ID` and `CODEX_SESSION_ID` with the private
accepted owner, refuses root and other actors, and emits the marked title and
startup-preservation brief only for the owner.

`--operation manager-create` accepts the same project and workstream
fields plus unprefixed `name`, charter `prompt` and `admission` evidence.
Only root may request it. It reuses exact accepted owners or resumes the same
known pending id before considering a new draft. A new draft needs a fresh
`observed_at` timestamp, `official_owner_lookup` pointer, empty
`exact_owner_candidates` and `pending_candidates` lists, `scope_evidence`
pointer, true `coherent_workstream`, `clear_routing_boundary`,
`material_responsiveness_benefit`, plus `recurring_coordination` or
`independent_continuity`. Root independently verifies this supplied evidence
and serializes admissions by scope before applying any draft.

## Incremental worker placement

After exact project/official execution proof, put durable workers in existing
References and read back exact membership and marked title. Preserve Today
priority and independently verify accepted ownership. The following
co-location path applies only on an explicit request.

After native create/execution verification, use `worker-admit` with the placement
bundle plus fresh `title_read:{thread_id,title,observed_at,official_item}`. It
drafts both the title repair and anchored move. Finish with `worker-admit-verify`
against a fresh title read and section inventory. Order-only proof cannot pass a
missing/stripped marker. Today can repair a marker while retaining its entire
human-selected order. Model instructions adopt this path; it is not a native
interceptor that can stop a client bypassing the supported tools.

For a new worker beneath an existing verified workstream manager, use the
incremental protocol rather than a bare default move:

```sh
python3 sidebar.py --operation worker-plan --bundle /absolute/private/placement.json
python3 sidebar.py --operation worker-reconcile --bundle /absolute/private/post-move.json
python3 sidebar.py --operation worker-check --bundle /absolute/private/pre-reorder.json
python3 sidebar.py --operation worker-verify --bundle /absolute/private/final-readback.json
```

The plan bundle contains fresh `inventory`, epoch-second `observed_at`, a
nonempty `official_item` pointer, exact `section_id`, local `manager_id` and
`worker_id`, explicit `priority_section_ids`, and nonempty `binding_evidence`
and `owner_evidence` pointers. The controller must already have verified saved
Workspace creation/actual execution binding and accepted logical ownership.
These are operator attestations, not authenticated vendor receipts or native
mutation authority. Reconcile/verify bundles carry the saved `plan` plus fresh
inventory/time/pointer. Check bundles carry `reconciliation` plus fresh evidence.

With an existing worker after the manager, the move explicitly supplies that
worker's id as `beforeThreadId`. The API has no `afterThreadId`; when the manager
is the only thread, insertion is a **compound** move followed by fresh complete
reconciliation. A move receipt never completes it. An empty managed section
without its verified manager holds until that manager is placed and verified.
Already-moved workers are reconciled without a duplicate move.

Reconciliation orders only the related manager/worker prefix, preserving every
other thread's relative order, including concurrent additions. It emits the
supported `reorder_section` arguments with every current thread/conversation
exactly once; projects remain in place. A new membership change makes an old
reorder fail `worker-check`; recompute from fresh membership instead of dropping
the new row. Unrelated manual reorders/removals hold rather than being overwritten.
Unknown keys or ambiguous identities require manual full-order handling.

`complete:true` requires official readback with manager first and worker second
among threads. The fresh check and mutation are separate API calls; a concurrent
change in that gap remains a runtime race, so failed final proof is partial/held,
never a reported success or blind retry. Priority sections such as Today return
`preserve_priority`; they keep their separately selected order. Bulk layout
plans now also emit worker anchors and mandatory `placement_followups` instead
of treating a default insert as proof.
