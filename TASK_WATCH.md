# Historical opt-in task tracking prototype

**Default Desktop policy: HOLD without a current reviewed host opt-in.** Normal setup uses pure planning and manual
official-app operations with record-only, on-request coordination. Commands in
this document describe the retained experimental opt-in implementation. They
are not a quickstart or authorization to run `tick`, `watch`, catchup, install
services, renew policies or send callbacks. Source updates do not deploy them.

## Complementary owner/state snapshot

`python3 task_board.py --root /absolute/private/current-policy` writes a private
`board-owners.html` view of the existing ledger. It reads SQLite in read-only
mode and does not change the original running `board.html`, observer, ownership,
claims or delivery. Owner, task-state and captured-native-activity filters work
locally. Native source holds remain unconfirmed; semantic working/completed
labels never supply native activity. Reasons, next actions, resume conditions,
decisions and delivery acknowledgment remain separate.

This is a saved snapshot: regenerate it to refresh data. Reloading the HTML
alone does not reread the ledger. It starts no server, watcher or model loop.

This engine is an experimental capability, not activation authority. Host
policy may hold observation and callbacks entirely. Source changes and tests
do not reactivate any installed service, old lease, bridge or enrolled fleet.
Resolve current host policy and exact human authorization before live use.

`task_watch.py` is a separate opt-in lifecycle from finite cooperative reports.
It reads native persisted turns through the supported `codex app-server
--stdio` API. It does not read private Desktop databases/sockets, resume tasks,
change models, or subscribe to another App Server's push stream. The fallback
is bounded local reconciliation every 30 seconds, with no model call for an
unchanged task. Queue notices can run the recipient's existing model/context.
Quiet completed turns use a compact supported summary/head read and compare
the exact final against the already captured full source. Active/question or
no-final heads still require full source; a same-head full fetch is limited to
one turn. A changed head uses bounded pagination to the saved watermark.

An explicit private authorization spec lists exact managed tasks, their stable
charters, owners, workspace and CLI binary. Initialization baselines their
current payloads without replay. A narrowly enumerated `catchup` exception may
capture a named real turn; it is never a historical drain. The root is excluded
from observation, helpers report directly to root, and every worker has one
owner. New human followups and changed final/question payloads create fresh
deterministic generations automatically. A pure native status change does not
duplicate a final. Bounded history gaps preserve the previous watermark.

The authorization is **persistent until revoked**. New capture leases last six
hours and reconciliation records a new epoch when necessary while the policy
is enabled. This is explicit automatic renewal of this new policy, never a
renewal of an old cooperative session. A backwards clock holds work. `disable`
prevents subsequent reads, renewal and dispatch; unload the exact launch job as
well to stop its process. Existing claimed queue attempts remain preserved.
Claim commit is the in-flight boundary: disable completes without waiting for
the external queue, but cannot cancel an already-claimed external attempt.

```sh
python3 /absolute/source/task_watch.py --root /absolute/private/new-policy init --input /absolute/private/spec.json
python3 /absolute/source/task_watch.py --root /absolute/private/new-policy tick
python3 /absolute/source/task_watch.py --root /absolute/private/new-policy watch
python3 /absolute/source/task_watch.py --root /absolute/private/new-policy board
python3 /absolute/source/task_watch.py --root /absolute/private/new-policy status
python3 /absolute/source/task_watch.py --root /absolute/private/new-policy disable
```

Freeze reviewed `task_watch.py` and `cooperate.py` together before installing
an authorized single local process. `watch` takes an exclusive file lock and
owns/cleans up its read-only stdio child. A process failure preserves database
state. It never starts the managed app-server daemon.
One in-process delivery thread runs every two seconds, independently of slow
API reads. It renews only this explicitly persistent lease, surfaces unexpected
failure and joins at shutdown. There is no second service or model poller.

Native observations have their own ledger and retain origin/source evidence;
they do not masquerade as cooperative worker reports or change the worker's
runtime identity. Source facts and owner-stamped routing commit together. An
immutable private file stores the exact user request, final and structured
questions, without tool outputs. An attempted notice is claimed before queue;
accepted, failed, timeout and crash outcomes are not automatically retried.
Acceptance is distinct from actual recipient acknowledgment.

A chat receives a summary, a reminder to check its recipient, and a stable
private evidence pointer. Before reading source or treating a notice as work,
run `python3 /absolute/source/task_watch.py --root /absolute/private/current-policy
receive --notice /absolute/private/current-policy/notices/opaque.json` in the
actual recipient runtime using the notice's Details path.
It opens the ledger read-only, validates the runtime, attempted recipient and
immutable packet/source, and returns either the packet or `already_acknowledged`
without new work. A fork must ignore an inherited notice for another chat;
never override runtime identity to consume it. This also works for legacy
packets using their Details path, without rewriting or replaying
them. Reading a packet directly bypasses this consumer check; the acknowledgment
guard remains a separate last check. A copied conversation can still contain
historical prose, so this is a guarded consumer protocol, not context isolation.
Raw source records, judgment fields, hashes and source excerpts remain private.
Known manager ownership-only acknowledgments use `suppressed_ownership_only`
notice state: immutable source/packet evidence stays internal with no attempt.
The recognizer accepts only a whole JSON object with exactly `type`, `worker`
and `workstream`: type is `ownership_only`, worker is a canonical UUID, and
workstream exactly matches the registered manager's ledger key. There is no
free-prose field. Captured user text must be absent or the same exact metadata
object; every free-form user request stays visible, including mixed ownership
and deliverable instructions. Extra/duplicate keys, mixed prose, malformed data, questions,
errors and a real human deliverable request remain visible. Historical English
acknowledgments stay visible: keyword lists cannot prove the absence of a new
prerequisite. Valid legacy unattempted
notices are rechecked before dispatch; attempted history is never rewritten or
retried. The read-only receiver returns `record_only_ownership` for an already
attempted ownership-only notice, so consumers do not reannounce it as a result.
This does not change source fingerprints or accepted operational state.
Empty or exact DONT_NOTIFY-only results similarly use `suppressed_nonactionable`
and `record_only_nonactionable`. Capture, direct and manager relay, dispatch and
receive share the same predicate. Real text, structured questions, native errors
and substantive owner findings/decisions remain actionable. A legacy attempted
sentinel with an `other`/`unknown` owner judgment stays quiet upstream and cannot
reset accepted operational state. Immutable packets and attempted history remain
unchanged; source fixes cannot retract already queued notices.
When a final reports review readiness and includes a readable local artifact,
the notice says `review-ready (worker report; acceptance pending)` and links
directly to that artifact. Readiness requires a standalone present-tense
declaration such as `Pair ready for review.`; future, conditional, negated or
unrecognized wording conservatively stays `result available`.
A present-tense readiness heading followed only by a Markdown review link is
also accepted. The link's label and URL remain source data; the visible notice
still uses only its separately verified local artifact.
This does not set objective completion, owner
acceptance or delivery acknowledgment. Only regular files within the policy's
workspace, of a preview/document type and at most 32 MiB, qualify. Source link
labels and prose are never copied into the notice. Missing, changed, outside-
workspace or unverifiable artifacts are never sent as usable review links;
dispatch holds if a captured artifact changes or disappears. URL fetching and
image rendering are outside this transport. A final without a usable local
artifact remains `result available` with its private evidence pointer.
An explicitly trusted media policy may raise the individual limit, up to512MiB.
Capture deduplicates links and resolved targets before hashing and shares one
immutable evidence set across recipients. Total hashed bytes per fact capture
and each revalidation are capped at512MiB across at most six unique links.
Changed policy/evidence holds rather than rewriting captured packets.
The pointer is opaque: recipient and generation identifiers are not printed in
its filename. Packet bytes exclude volatile database row order and capture
time, so identical source can recover after a precommit rollback; changed
orphan evidence remains refused and preserved.
The transport never executes embedded source text or commands. Its recipient can
record an immutable judgment through `ack --input <judgment.json>`, naming the
generation, outcome kind, explicit task state/reason, next action, evidence,
and open decision keys. `paused` requires a resume condition; `completed`
requires objective acceptance evidence; `sent_verified` requires transport
evidence. A bare native final claiming it sent something remains an unverified
report. Later finals do not close prior decisions. Resolution requires named
keys and explicit resolution evidence. No acknowledgment authorizes a send,
approval, publication or sidebar move.

An accepted completed one-off judgment returns a `completion_handling` review
step, also on an identical acknowledgment replay. Only a current ordinary
result without retained open decisions is a candidate; managers, protected or
recurring work, newer source generations and changed owners are preserved.
The already-active owner must use the approved completion-parking protocol:
fresh official latest-turn/full-objective checks, current membership, settled
review/input gates, and complete move/order/final verification. The response
does not move a task or infer completion from idle/turn completion. An already
parked task is not moved again; existing nightly reconciliation remains the
fallback. No new observer, wake, model prompt, callback or polling is added.

After acknowledgment the root gets one direct coordinator fact notice with
the separate judgment. If a helper never acknowledges, a 120-second fallback
enqueues the same root fact once independently of model memory. The root reads
its channel, not child chat trees. Each recipient/fingerprint is unique. A
helper turn containing only exact persisted self-notice inputs is suppressed;
additional genuine human input is preserved. Failures remain visible, and this
is not an exactly-once eventual delivery guarantee.
User-only native turns create working/request generations and supersede an
earlier completed classification without notifying a model about routine
progress. A new final remains unclassified until its owner reviews it. Named
catchup notices identify the latest native turn at capture so past drafts or
holds are not presented as current instructions.
Explicit paused/waiting state is preserved across unrelated native activity.
Changing a current explicit pause through owner judgment requires resume
evidence; unrelated tool activity never erases its reason or resume condition.

Shell-command exit status is not a turn or task outcome. Empty or uncorroborated
`interrupted`/`failed` snapshots are held without advancing the watermark or
emitting an alert. Confirmed native error plus terminal evidence is labeled
`turn_failed`, with overall objective state unknown; interruption is distinct.
An available final is reported for review, never automatically converted into
whole-task failure. Observation metadata is excluded from semantic fingerprints
so added diagnostic evidence alone does not replay prior outcomes.
An unconfirmed intermediate snapshot holds the entire fetched batch, not just
the current head, so later completion of that turn cannot be skipped by a newer
watermark.

An explicitly authorized active baseline may carry an exact source digest and
official evidence pointer as `active_baseline_floor` in its task spec. Its
unchanged unconfirmed fragment is a no-replay floor that a newer usable head
may cross. That shadow alone never earns a clean read; new or changed
unconfirmed fragments still hold the whole batch. A later successful final of
the same native turn supersedes only pending, unattempted error notices.
Facts, packets and attempted delivery history stay preserved; fallback cannot
resurrect a superseded error.

Delivery defaults to **record only**, including legacy configurations without
a delivery setting. A fresh policy may name
`delivery: {"mode":"explicit_internal", "approval_ref":"<current approved scope>"}`
only after current host/human authorization is established. There is no
supported command to silently enable old pending events. Pending legacy raw
payloads or unverifiable packets are held rather than sent; attempted/accepted
history stays immutable and is never retried. Known bad alerts must be retained
as invalid historical evidence, not re-sent or automatically acknowledged.

`board.html` is a private standalone snapshot with native task links, artifact
links, owner, explicit state/reason, next action, pause/resume conditions, open
decisions and generation evidence. Reporting connectivity/lease is a separate
column. A small local clock marks old successful reads stale even if the
writer stops. Reload to see new file contents. Unknown states remain unknown;
native idle/notLoaded/turn completion never supplies objective state.
Latest-generation queue failure/uncertainty and actual acknowledgments are
shown separately from recent successful API reads.

Helper admission is deliberately split at the supported API boundary. The
fixed root's `admit --input <judgment.json>` requires a named coherent scope
and observed coordination pressure, reuses an existing workstream helper, or
reserves one unique workstream under a configurable helper cap (default four).
The single helper level is fixed. Counts alone are not an admission judgment.
The returned packet requires the root to use official app tools to create in
the saved project, verify actual execution cwd, and place the exact manager
first in its workstream's custom section. Managers are unmarked; the visible
worker convention is `◆ <existing title>`. Neither titles nor pins set ownership.
Before any new create packet, admission checks the configured saved project and
distinct existing ordinary members; it refuses an unbindable reservation.
Then `bind-helper --input <receipt.json>` records the root's attestation with
the reservation, exact created task/project id, actual execution cwd, current
human-authorization pointer and exact official create/execution/charter/placement
and inventory evidence. `placement` names `surface: codex_desktop_sidebar`,
`host_id: local`, `thread_id`, `section_id`, `observed_at` (within five minutes)
and the exact `inventory_section` object from official `list_threads` sections.
Its `sectionId` must match, with the manager as the first task in `itemKeys`.
Do not substitute App Server metadata ids. Every member must be in that section
or have an explicit `location_exceptions` entry with its reason, official
inventory evidence and other section object (for example, selected Today work).
The `expected_owners` map must name every exact member and match current owners
under the same transaction; a concurrent ownership change holds the whole bind.
Pin-only evidence cannot bind a new manager. The operation enrolls
the helper for baseline and changes only future member generations. Repeated
identical binding is inert; conflicting replacement ids are refused.

For an existing registered helper, `cutover-members --input <receipt.json>`
requires a unique `operation_id`, exact `helper` and `members`, fresh placement
and charter/authorization evidence as above, and the exact `expected_owners`.
Only named future captured generations change owner. Omitted members stay as
they are; moving a member out requires a separate explicit cutover to its new
helper. Prior facts, notices, attempts, judgments, task states, decisions and
watermarks remain unchanged. The scope-change receipt preserves prior owners.
Identical operation replay is inert; changing an old receipt is refused.

These root-only metadata operations also work on a disabled or expired ledger.
They do not enable delivery, renew its lease, advance its clock or start a service.
Binding is ownership metadata, not proof of active supervision or notification
delivery. Source baseline and any delivery authorization remain separate gates.

`set-helper-capacity --input <receipt.json>` makes an explicitly authorized
root capacity adjustment without creating helpers or enabling delivery. It
requires a unique `operation_id`, `expected_capacity`, integer `capacity` within
one to eight, a fresh `observed_at` and explicit `authorization_evidence`.
Registered helpers plus pending reservations must fit. The prior capacity and
receipt are audited; identical replay is inert and conflicting replay refused.
This is a manual policy setting, never an automatic split at a worker count.

This is a **root-tool adapter protocol**, not a local saved-project creation
API. The script cannot independently verify the official tool pointers; it
records the fixed actual root's attestation and labels that boundary. Do not
substitute `thread/start`, invent receipts, or describe a reservation as a
provisioned helper. Existing helpers can be reused now. New creation needs a
normal root model turn with official app tools, never a quiet model poller.

Scope is explicit rather than inferred from titles. Parked historical tasks
and disposable scheduler runs stay excluded unless a current human-authorized
task list names them. Discovering and admitting newly created chats still
requires the root to update the policy through a fresh reviewed scope; this
implementation automatically tracks followups within enrolled chats.

API calls run outside write transactions. Each page has a fresh authorization
gate and consumption rechecks enabled state, lease, baseline, owner and saved
watermark. Disable during an in-flight read commits promptly and prevents that
read from committing facts or advancing cursors. Named past catchup facts do
not replace current task state or create current decisions. Decision updates
are ordered by source generation, with resolution tombstones, so a delayed
first acknowledgment cannot reopen a newer resolved hold.
