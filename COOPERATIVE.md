# Cooperative report prototype

Historical opt-in implementation reference. Current Desktop watcher policy is
HOLD; use the [package README](README.md) for normal record-only/manual setup.
Operational examples below are not authorization to activate services or delivery.

Private cooperative worker reports. **Record-only is the default.** Explicit
human opt-in can enable short visible notices for future reports within the
original finite lease. Quiet local checks make no model calls; handling a
queued notice runs the receiving model and uses its conversation context.
There is no hidden wake or quota-percentage/free-wake claim.

## Record-only workflow

```text
explicit worker + runtime session + finite assignment
  -> worker writes its fixed receipt and reports status
  -> private immutable input/final snapshots and SQLite record
  -> read-only inbox when a human asks for status
```

The worker's `CODEX_THREAD_ID` and `CODEX_SESSION_ID` must each match their
registration. A fixed target remains session metadata and becomes the message destination
only after explicit visible-mode opt-in. Environment identity is a local accident guard, not
hostile-process authentication.

`needs_input`, `ready` and `failed` retain their original meaning. One input
phase and one final phase may be recorded per assignment; input never suppresses
a later final. Ready/failed are mutually exclusive final states. Worker-reported
ready is not native turn completion or independent approval of the objective.

Reports are deduplicated, snapshotted and hashed before a successful return.
Record-only reports have outcome `recorded_only`; fresh visible-mode reports
start `pending`. Earlier `pending`, `claimed`,
`accepted_unverified`, `failed_no_retry` and `uncertain_no_retry` records are
preserved exactly and never retried or replayed. Reports are retained for
inspection even after their finite session lease expires; new reports and
registrations still require a valid lease. No automatic renewal is provided.

## Use

Use a fresh, private absolute directory outside tracked source. Existing
registration commands remain compatible:

```sh
python3 /absolute/checkout/cooperate.py --root /absolute/private/new-session init \
  --target 11111111-1111-4111-8111-f11111111111 \
  --queue-binary /absolute/real/executable --lease 3600
python3 /absolute/checkout/cooperate.py --root /absolute/private/new-session register \
  --worker 22222222-2222-4222-8222-f22222222222 \
  --runtime-session 22222222-2222-4222-8222-f22222222222 --lease 3600
```

`--queue-binary` is not executed in record-only mode. Explicitly enabled visible
delivery uses it for one bounded native queue attempt per batch. Registration returns the exact assignment and fixed receipt
path. Include these in the worker's authorized assignment brief. The worker
writes a JSON object at the receipt path (max1MiB), containing its actual result,
artifacts, verification, limitations and any new decision needed, then runs:

```sh
python3 /absolute/private/session/watcher.py --root /absolute/private/session report \
  --assignment 33333333-3333-4333-8333-f33333333333 --status ready
```

Use `needs_input` for a concrete decision/blocker and `failed` for terminal
failure. Preserve actual runtime environment values. Do not wake tasks only to
reacknowledge enrollment. A later assignment still needs fresh registration.

The existing private frozen-runner filename may remain `watcher.py` so enrolled
workers keep their reporting path. It does not imply a running watcher.

## Read on demand

```sh
python3 /absolute/private/session/watcher.py --root /absolute/private/session inbox
python3 /absolute/private/session/watcher.py --root /absolute/private/session inbox --after 4
python3 /absolute/private/session/watcher.py --root /absolute/private/session status
```

`inbox` returns exact worker/assignment/phase identities, delivery history,
immutable receipt paths and hash-validation flags, plus a cursor. It does not
send anything, mark records consumed, change delivery outcomes, or advance a
stored cursor. A caller may keep its own cursor after a successful explicit
read. A missing or modified snapshot is flagged; it is not silently accepted.
Old records remain readable for reconciliation without becoming a delivery
source. `deliver_once()` does nothing in record-only mode. In visible mode it may claim
and queue a batch of qualifying future reports; it never drains history.

When the human asks for status, a receiving assistant can inspect this private
state and exact workers, then give a concise answer. A separately authorized
background task may read within its own scope and keep results privately.
There is no live invisible reasoning in the main chat: passive files do not
wake its model. Do not claim unconfigured background-hook support.

## Explicit visible delivery

Only enable this when the human accepts visible queued messages. The command
records both a timestamp and event-row activation floor. Existing records,
including old pending/claimed/quiet-period reports, never become eligible.
An idempotent mode call does not advance an existing visible cutoff. Switching
back to record-only and later opting in again creates a fresh cutoff.

```sh
python3 /absolute/private/session/watcher.py --root /absolute/private/session delivery --mode visible
python3 /absolute/private/session/watcher.py --root /absolute/private/session install
```

Install refuses an existing legacy delivery service. It freezes reviewed code
as `delivery.py` under a new `.visible` launchd label, with absolute executable
paths, private logs, no KeepAlive and no login/reboot persistence. It preserves
the original lease and all worker registrations. `watch` requires visible mode;
CLI `drain` stays disabled. Checks run every two seconds without model calls.

Ready results coalesce for up to20seconds; unresolved input/failure gets a
shorter2-second window. Both phases stay together when already pending, and a
final resolves its earlier input in the headline without losing either record.
Each batch includes at most64assignments. The visible message contains only
counts and one short private inbox pointer. Full worker/assignment/phase ids,
receipt hashes and immutable paths live in the indexed JSON. No worker prose is
copied into the visible message.

```sh
python3 /absolute/private/session/watcher.py --root /absolute/private/session notice --id <12-hex-notice-id>
```

`notice` verifies the index hash, exact registered membership and original
receipt hashes. Inspect exact workers through official tools before describing
objective completion. A notice never authorizes approvals, dispatch or archival.

All batch membership claims commit before one bounded native queue attempt,
without `--resume`. Queue acceptance is not verified delivery. Crash/timeout
or failure is sticky for every member; no blind retry. A later final after a
sent input remains a distinct event. There are no model calls for unchanged
state or routine progress. The heartbeat, if separately configured, is separate
usage and must not be duplicated by another model-based loop.

## Change an existing deployment safely

Unload and verify absence of its exact old delivery service/process first,
without marking the reporting session stopped. Back up the old producer file,
then atomically replace the enrolled `watcher.py` reporting path with reviewed
compatible code. Keep snapshots, states, registrations and original expiry.
Do not wake workers merely to acknowledge a mode change. Only then choose the
mode explicitly; installing visible delivery uses the new guarded service,
never the legacy unbounded outbox consumer.

```sh
python3 /absolute/private/session/watcher.py --root /absolute/private/session record-only
python3 /absolute/private/session/watcher.py --root /absolute/private/session status
```

Record-only stops new claims; a visible watcher exits at its next deterministic
check. Unload its exact service if needed. An in-flight claimed message cannot
be recalled. Full `stop` also sets session.stopped and blocks reports, so it is
not the command for silencing delivery while preserving recording.

Rollback preserves all state and the original expiry. Returning to record-only
is safe; never reset claims, import history, restore a legacy outbound job or
silently renew a lease. Already visible messages are not hidden or deleted.
The old stdin `bridge.py` remains an inactive manual prototype with fake-queue
tests, never a fallback for bypassing the mode/cutover guard.

## Verify

```sh
python3 -m unittest discover -s tests -v
```

Synthetic subprocess tests cover record/duplicate durability, concurrent
reporters, exact runtime identity, private immutable receipts, input followed by
final, expiry, historical-state preservation, read-only cursors, default-mode rejection
of delivery, explicit opt-in cutoffs, burst/priority coalescing, index validation,
concurrent batch claims, and sticky queue failure/crash outcomes. A fake queue executable is used for visible-mode tests and must never be
invoked by the record-only path. Legacy bridge tests remain isolated and
use fake delivery.

Official capability references:
[queue accepts a text message](https://learn.chatgpt.com/docs/developer-commands#codex-queue),
[background hooks wait for a safe point and do not start an idle turn](https://learn.chatgpt.com/docs/hooks#how-background-hooks-run).
Neither establishes a supported hidden cross-task wake for this Desktop setup.
No private Desktop sockets/databases, global hooks, account changes, automatic
approvals/archive, model polling or public remote are used.

## Recovery and fresh result boundaries

```sh
python3 /absolute/checkout/cooperate.py --root /absolute/private/session recovery
python3 /absolute/checkout/cooperate.py --root /absolute/private/session recovery --owner <exact-owner-uuid> --limit 100
```

This read-only view works after expiry without renewing or replaying anything.
It returns only that owner's direct assignments, with input/final phase,
snapshot integrity, uncommitted snapshots and unresolved delivery flags. The
root gets direct coordinator charter/backlog pointers and fact/relay counts,
not a recursive child-chat scan. `ready` remains a reported final, never proof
of objective completion or approval. Results are bounded; `truncated` signals
that a larger explicit limit is needed. There are no subprocess/model calls.

A consumed phase repeated with identical evidence is a duplicate. Changed
receipt bytes are refused with fresh-assignment guidance, so a new revision
cannot silently disappear as an old success. A duplicate earlier input after
its later final remains harmless; both original snapshots stay intact.

If a process dies after writing a private snapshot but before committing the
report, an identical retry may reuse that exact snapshot. A mismatched,
nonprivate or corrupt orphan is preserved and refused. This repairs only the
local recording transaction: external claimed/failed/uncertain deliveries
remain sticky and are never retried by recovery.

## Named coordinator ownership

A coordinator is an ordinary saved-project task with a private charter and
backlog, idle until routed work or an owned event. It is not a new root
controller or permission role. Task creation, exact saved-project/execution
binding and any explicitly authorized pin use official app tools outside this
engine. A title or pin never establishes controller authority.

The operator supplies a private JSON spec with exact `thread`, `runtime_session`,
fixed `parent` (the existing session target), `name`, natural-language `scope`,
absolute `charter`/`backlog`, bounded `expires`, `project_id`/`workspace`, and
`binding`/`binding_sha256`. The binding receipt must contain the actual official
create project/thread/host/environment and execution cwd/runtime ids/item id.
The local receipt is not vendor authentication: the operator must first verify
its actual official sources. Public examples/tests use only synthetic ids.

`workers` contains a bounded explicit list of registered direct worker `thread`,
`runtime_session` and `scope` entries. No root/self/nested/overlapping ownership
is allowed. The 1..8 group bound is a safety limit, not an automatic capacity or
spawn threshold. Existing assignments are never moved or replayed: ownership
is stamped only when a fresh assignment is registered. Those assignments cannot
outlive the coordinator scope or original session expiry.

```sh
python3 /absolute/private/session/watcher.py --root /absolute/private/session coordinator-config --spec /absolute/private/coordinator.json
python3 /absolute/private/session/watcher.py --root /absolute/private/session parent-channel --coordinator <exact-coordinator-uuid>
```

The same watcher batches separately by immutable assignment owner. A child
report has exactly one delivery target; no parallel subtree scanner is created.
At record time the producer transactionally publishes exact source facts into
a receipt-fingerprint-deduplicated private parent channel. Facts survive even
if the coordinator model fails before responding. The parent can inspect its
direct coordinator channel without scanning every worker chat.

When a coordinator receives its owned index, it verifies the source and writes
its judgment/artifacts into a private JSON object. It then runs:

```sh
python3 /absolute/private/session/watcher.py --root /absolute/private/session relay \
  --notice <source-notice-id> --status ready --judgment /absolute/private/judgment.json
```

Relay checks the actual coordinator runtime, fixed parent, source index target,
exact registered facts and original hashes. Its transaction claims each source
fingerprint and records one upstream reporting assignment, preserving script
facts separately from model judgment. Repeated relay/restart cannot duplicate
that upstream event; unknown queue outcomes remain sticky. A later child final
after input is a distinct fingerprint. Coordinator reports route only to the
fixed parent, never back down the tree. Readiness is not editorial approval.

Upstream *notification* remains cooperative: the coordinator must call relay.
Source facts are already durable independently of that model action. No
invisible autonomous reasoning, automatic approvals/archival, full subtree
control or guaranteed eventual delivery is claimed.

Explicit rollback affects future assignments only:

```sh
python3 /absolute/private/session/watcher.py --root /absolute/private/session coordinator-withdraw --coordinator <exact-coordinator-uuid>
```

Existing routes and facts retain their owner until settled; the command does
not tear down, unpin, delete or orphan the coordinator task. Do not silently
transfer workers to another coordinator, create another scheduler, or renew a
lease. A persistent task/charter survives; its current event lease is finite.
