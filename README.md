# CodexJarvis

CodexJarvis packages a controller/workstream arrangement for Codex Desktop:
one verified **Jarvis** controller, explicitly configured **Friday** managers,
and ordinary **◆ workers**. It provides pure sidebar and completion planners,
manager/config templates, local evidence ledgers and synthetic regression tests.
Personal identities, host configuration and live receipts stay outside this repo.

Part of the [JarvisAIGeorge](https://github.com/georgewangyu/JarvisAIGeorge)
family; the Claude Code counterpart is
[ClaudeJarvis](https://github.com/georgewangyu/ClaudeJarvis). MIT licensed.

**Default mode: manual official-app operations and record-only, on-request
coordination. The Desktop completion watcher is on HOLD without a current reviewed host opt-in.** Cloning, testing or
generating a plan starts no observer, notification service or model. Historical
opt-in delivery implementations remain experimental source; their presence does
not authorize installation, polling or callbacks.

## Architecture and shipped behavior

```text
private config + saved official inventory/project/execution evidence
  -> sidebar.py: validate roles, project binding, ◆ titles and full orders
  -> verified controller: admit a missing eligible Friday and register exact tasks
  -> accepted owning Friday: draft and apply ordinary worker creation through official app tools
  -> local final + durable result: active owner assesses full completion
  -> task_parking.py: fresh evidence check -> official move to References
```

| Component | Shipped behavior | Boundary |
| --- | --- | --- |
| [sidebar.py](sidebar.py) | Pure plan, create/title/move argument drafts, full affected orders and before orders | Executes no app calls; evidence is operator-attested |
| [manager_dispatch.py](manager_dispatch.py) | Friday coordination versus worker execution boundary | Missing worker dispatch is a hold; no native actions |
| [root_dispatch.py](root_dispatch.py) | Guarded Root coordination or exact Friday handoff requirement | Trusted private adapter supplies runtime/owner probes; sends nothing and intercepts no other tools |
| [task_parking.py](task_parking.py) | Pure bounded review plan, completion gates, post-move orders, fair cursor | No semantic completion inference, moves or archival |
| [incoming_results.py](incoming_results.py) | One arriving notice: recipient/source/artifact checks, bounded reply plan and durable dedup claim | No transport, queue drain, action execution or idle wake |
| [templates](templates) / [examples](examples) | Manager charter, ownership template, runnable synthetic bundles | No personal enrollment or live state |
| [task_watch.py](task_watch.py) | Experimental source ledger, owner CAS, binding/capacity gates, guarded receive/ack, quiet-result handling | Native observation/delivery is historical opt-in; currently HOLD |
| [cooperate.py](cooperate.py) | Finite reports, immutable receipts, read-only recovery | Record-only default; separate legacy opt-in lifecycle |
| [task_board.py](task_board.py) | Private saved owner/state HTML snapshot | Regenerate to refresh; no live subscription/server |
| bridge.py / doctor.py | Earlier isolated experiments | No automatic fallback or production installation |

These are local tools, not an app extension that creates sidebar sections or
attaches subscriptions. Source tests do not prove that a running frozen release
has adopted the source. Native setup, saved-project verification, charter
acceptance and final placement proof remain official-app actions. No private
build-proof service or repository is required.

## Prerequisites and quickstart

Use Python 3.11+ and its standard library. Node.js is needed for the full test
suite's HTML/JavaScript checks. Native setup also requires Codex Desktop with
supported project/thread/sidebar tools, a saved **Workspace** local project,
and an independently verified controller authorized for that host. A title,
pin, quoted id or working directory never establishes controller identity.

Run from a checkout or unpacked source copy; no pip install is needed:

```sh
python3 -m unittest discover -s tests -v
python3 sidebar.py --bundle examples/sidebar-bundle.json
python3 task_parking.py check --bundle examples/parking-bundle.json
```

The example commands only print JSON. All example identities, evidence and paths
are synthetic; never apply their output to a real sidebar. For manual use,
keep reviewed source at a stable location and copy templates/examples into a
separate owner-only private directory. No install step, hook or launch service
is required. Never use live runtime state as a source checkout.

## Guarded Root intake

`root_dispatch.guard_request` is the generic Root intake gate used by the host's
trusted private entrypoint. The adapter must obtain current controller identity,
the adopted accepted-Friday authority and a fresh native read independently of
request JSON. A caller's actor, controller or owner claim grants no capability.
The shared `sidebar.read_creation_authority` reads the same protected adopted
file used by the existing creation gates; their validation and creator checks
remain unchanged.

Root coordination is allowed for explicit bounded classification. Domain
execution, dashboard configuration, payment audits and native worker launches
require the current accepted Friday, even when labeled a small decision or
combined with a bounded status request. Unknown, pending, conflicting, foreign
or unavailable evidence holds the request with no inline execution or worker
fallback. Assigned workers and Friday review retain their existing boundaries.

The successful routing result is `hold_for_official_friday_handoff`, with exact
official `send_message_to_thread` arguments. It is a pending requirement, not
a send receipt. Actual Root performs its authorized single official handoff,
then returns without polling. The live adapter must retain one private host/root
ledger, recheck selected ownership after native I/O and reject ambiguous raw
JSON; diagnostics cannot reset pending-request dedup. This gate does not execute vendor, money, account,
campaign or worker actions, and grants no additional approval.

An explicit model request travels as `worker_preferences`, for example
`{"model":"gpt-6.2-astra","reasoning_effort":"medium"}`. The gate includes this
data in the verified Friday's brief. It never sets the handoff tool's `model`
or `thinking`, which would change the receiving manager. Friday checks worker
model availability and reports a blocker if the preference cannot be honored.
Model tokens may contain letters, digits, dots, underscores, colons, slashes
and hyphens, beginning with a letter or digit, up to 128 characters. Optional
effort uses the Desktop dispatch labels: none, minimal, low, medium, high,
xhigh, max or ultra. Whitespace, directives and unknown effort labels hold.
The preference never grants Root worker-launch permission. A claimed prior send
or later owner assignment cannot turn a pending plan into execution authority.
Official owner lookup and an accepted official handoff must precede Friday's
worker dispatch; retrospective assignment does not prove that order.

This is enforcement inside the supported private entrypoint. Bootstrap merely
discovers instructions unless a runtime explicitly calls the gate. Direct
tools, dashboards, native subagents, misclassified operations or an advisory
planner invoked separately are not universally intercepted. Source tests and
instruction adoption must be reported separately from a genuine Root invocation
and an actual Friday handoff. No hook, watcher or global tool filter is installed.

## Private configuration and official setup

[SIDEBAR.md](SIDEBAR.md) defines the complete input contract and setup sequence.
Supply exact controller, saved project and section ids; explicit workstreams,
managers and workers; protected/recurring ids and priority locations; and fresh
normalized official inventory, titles, project records and execution evidence.
Start with `examples/sidebar-bundle.json`, replacing synthetic data privately.
`sidebar.py --bundle /absolute/private/sidebar-bundle.json` prints a plan only.

1. Resolve the existing saved Workspace project through official inventory.
   Use its exact project id and local environment for every new manager.
2. Reuse an accepted Friday where one exists. An unmatched one-off stays
   held. For qualified missing ownership, the verified root uses the guarded
   `manager-create` planner, applies the draft once through the official tool,
   and retains the exact returned id.
3. Verify actual execution cwd, exact thread/runtime ids and charter acceptance
   before placement or binding. Summary cwd alone is insufficient. If proof is
   missing/conflicting, hold and diagnose that same task; create no duplicate.
4. Refresh official sidebar inventory/titles, rerun the planner, apply only
   authorized operations, complete full affected orders and verify them.
   Managers are first and unmarked; ordinary worker titles use ◆ exactly once.
5. Record logical ownership separately from physical location. A Today worker
   can remain owned by its workstream manager. Preserve priority selections
   until the human changes them. Use existing References for durable worker
   chats by default. Evaluate delivery enrollment separately. Historical layout
   plans cannot revive a worker from References.

One manager owns each explicit worker. No manager cycles or duplicate owners;
no extra manager for Today or References. About five actively supervised
assignments prompts review, never automatic splitting based on open/idle rows.
The planner supports up to eight explicit workstreams; the historical ledger
defaults to four with a separately authorized capacity CAS within 1..8.
Capacity is not permission to create managers.

### Guarded creation

Use the canonical checkout's `sidebar.py --operation worker-create --bundle …`
in the exact admitted Friday session. Root, nonowners and unresolved creator
identity receive no create arguments. Only the exact verified root can draft
a new peer through `--operation manager-create`; legacy layout emits holds for
missing managers. A saved project name, display title, or bundle-supplied actor
cannot grant creation authority.

This read-only planner uses a fixed host-local authority snapshot provisioned
by the verified root. See [the format and proof requirements](SIDEBAR.md#guarded-creation).
Missing or conflicting authority holds creation. The source runs from the
checkout and requires no installer. Its guard governs supported plans.
The app's separate `create_thread` tool still requires policy adherence when
an operator applies a plan. Review and provision the snapshot on the correct
host before claiming active use.

`templates/ownership-change.json` shows the existing-helper ledger cutover input.
Root-only metadata commands require actual runtime identity, fresh exact Desktop
placement/charter evidence and expected-owner CAS. They preserve old facts,
attempts, judgments, cursors, leases and disabled delivery. New binding also
requires a reservation, exact create project id and actual execution cwd.
See [TASK_WATCH.md](TASK_WATCH.md). None of these metadata changes enables monitoring.

## Completion, quiet bookkeeping and delivery

For a reviewed host cutover, `delivery.routing: owner_reviewed_v1` uses the
actual normal dispatcher: exact workers notify their admitted Friday, which
reviews and acknowledges meaningful results or concrete input/blocker events.
Only that current meaningful judgment relays once to root. The old direct-root
flag and age fallback cannot bypass the owner. Manager conversation rows are
excluded from reads and dispatch, including when retained in the ledger.
Newer work makes an older pending outcome stale. Existing packets and attempts
remain immutable. This protocol takes effect only through the root's reviewed
single-observer cutover; source tests do not activate the running observer.

Native children may be enrolled only by exact id with supported parent provenance
and actual execution evidence, separately from durable saved-project chats.
No project/title/sidebar claim is invented for a projectless child. Children
without that proof retain parent-local behavior. Their Friday may report a
bounded reviewed aggregate through a separately authorized finite cooperative
assignment; that receipt is an owner report, never native completion proof.
Do not subscribe to manager prose or dynamically discover every descendant.

Managers also follow [ONE_SHOT_ACTIONS.md](ONE_SHOT_ACTIONS.md): temporary
disruptive host requests expire with their exact job/attempt and cannot replay
after compaction or unrelated work. Recover current human scope and attempt
evidence first. This is a workflow rule, not a shell interceptor or native
compactor fix; read-only scenario evaluation never executes a power action.

An already-active authorized owner accepting a one-off objective complete
finishes its normal References disposition in that bounded turn. Follow
[LIFECYCLE.md](LIFECYCLE.md): full-objective/return-contract assessment, separate
fresh latest-turn revalidation, existing References/current membership,
protection/hold checks, complete affected orders and final proof. Parking keeps
the chat/history; it does not close a runtime worker, archive/delete, approve
work or mark it read. Idle/notLoaded or a completed turn alone never proves done.

Preserve managers, recurring/protected workers, pauses, pending input/review,
blockers, unfinished backlogs, ambiguity and resumed work. Already parked
requires no second move. An optional future offer alone does not hold a fulfilled
objective. Existing separately authorized nightly reconciliation may be a
fallback; this package schedules no job or wake.

Ownership-only manager acknowledgments and empty/DONT_NOTIFY-only outcomes
stay private source evidence, not new results. The experimental ledger checks
capture, direct/manager relay, dispatch and recipient consumption. Substantive
text, structured questions, native errors and real owner blockers remain
actionable. Suppression preserves immutable facts/packets and attempted history;
it neither retracts old messages nor retries claims. An owner judgment of
`other`/`unknown` cannot turn a sentinel into a result announcement.
Ownership suppression uses the exact metadata-only JSON protocol in the manager
charter, with no free-prose fields and an exact registered workstream key.
Historical English acknowledgments, malformed metadata and additional prose
remain visible; the engine does not guess whether prose contains a new wait.

Default reporting is local final plus durable private evidence, read when
requested. Queue acceptance differs from verified delivery and objective
acceptance. No automatic approval, worker control, unsolicited callback, model
polling, historical drain or hidden wake is part of setup. Historical delivery
experiments are documented in [COOPERATIVE.md](COOPERATIVE.md) and
[TASK_WATCH.md](TASK_WATCH.md); their operational commands require fresh separate
authorization and host-policy review.

[DELIVERY_BOUNDARY.md](DELIVERY_BOUNDARY.md) distinguishes registration, capture,
routing, transport and presentation. Creation/placement alone never establishes
automatic result delivery; reviewed source is not proof of frozen-runtime adoption.

When the host explicitly authorizes handling **arriving meaningful notices**,
use [INCOMING_RESULTS.md](INCOMING_RESULTS.md). An actual incoming result can
trigger one fresh exact-source read, artifact verification and a useful linked
summary or new blocker/decision without requiring a second human check request.
Routine, ownership-only, sentinel, duplicate and superseded events stay quiet.
This narrow consumer exception grants reply-only scope; it neither enables
delivery nor starts a model. Artifact verification failures are surfaced honestly.

## Verify, update and rollback

Run the full unittest command, `git diff --check`, both synthetic examples and
CLI `--help` after changes. Tests use temporary synthetic state and fake queues.
They cover project/execution binding, roles, priority exceptions, full/inverse
orders, idempotent markers, fresh completion/holds/newer work, duplicate parking,
quiet outcomes, real blockers and sticky delivery history. They do not mutate a
native sidebar or prove a live service deployment.

For clean-copy validation, copy tracked source plus intended package files to
a temporary directory without `.git`, caches or runtime data and run those
commands there. No Git worktree or feature branch is needed. Review staged
files for private identities, local paths, credentials and real payloads before
commit/push. Use only a destination you chose explicitly; the package does not
create or choose a remote.

For updates, retain the previous source commit, private before orders, ownership
receipts and runtime backups. Verify the new source in a clean copy. Regenerate
plans from current official evidence; never replay stale create/move packets.
Changing files does not replace a running frozen release. Any service deployment
is a separate authorized stop/replacement/verification operation with a compatible
state backup.

Roll back source to a reviewed commit in the existing checkout after preserving
other edits. Roll back sidebar changes only against fresh membership with the
retained before orders, preserving concurrent human changes. Ownership rollback
is a new explicit expected-owner CAS for future routes; never reset old claims,
facts or judgments. Do not restore a historical watcher, renew leases, replay
notices or wake workers as a rollback shortcut.

## Limits and private boundary

Evidence pointers and semantic assessments are trusted operator inputs, not
cryptographic vendor attestations. Environment identities are local accident
guards, not protection against a hostile process under the same account.
Check native tool schemas on the actual host; plans cannot bypass missing APIs.

Keep personal ids, absolute host paths, accounts, personal charters, receipt
history, live config, notification payloads, databases and generated boards
outside tracked source. Examples use synthetic data. `.gitignore` is a guardrail,
not a privacy audit. Reusable code depends on no private overlay; a private
adapter may load these modules and inject protected ids.

## Licence

MIT. See [LICENSE](LICENSE).
