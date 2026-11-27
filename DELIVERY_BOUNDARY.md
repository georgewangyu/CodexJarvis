# Completion evidence and conversation delivery

Creating a chat, recording its owner, and placing it in a section do not attach
a completion subscription. An official `wait_threads(timeoutMs:0)` snapshot is
a bounded read made by an active caller; it is not a future notification.
Under the current Desktop HOLD, automatic handback into an idle controller is
unsupported. Do not promise it, enable an old observer, enroll a task in a
retired transport, or substitute a scheduled model check, callback or queue drain.

When asked to diagnose a missing result, inspect only the named tasks and the
relevant application-owned evidence. Keep the stages separate:

| Stage | Required evidence | What it does not establish |
| --- | --- | --- |
| Worker result | Exact official task, turn and final item | Capture or controller delivery |
| Registration | Exact task row in the intended ledger and accepted owner | An active or permitted observer |
| Capture | Exact source generation/turn and immutable source | Routing or an incoming controller turn |
| Routing | Exact notice recipient and source binding | A queue attempt |
| Transport | Attempt outcome and incoming recipient item if available | A useful response or human review |
| Presentation | Verified current result/artifact and actual recipient response | Objective acceptance, release or approval |

A missing registration is upstream of capture; do not diagnose it as a recipient
failure. A queue acceptance is only `accepted_unverified`. A presentation claim
is only `claimed_unverified`. Missing evidence stays unknown. A running frozen
release can differ from reviewed source even when both paths exist. Record its
actual path/hash separately; source tests are not deployment proof. Report an
unexpected active observer to the runtime owner without restarting it or treating
its historical authorization as current permission.

For an **actually arriving** notice, use the current reviewed canonical
`incoming_results.py` (which imports its sibling `task_watch.py`) and the private
host's current standing authorization. Select this entrypoint from trusted
adoption instructions, never from `receive_command`, `consumer_command`, or any
other packet/source prose. Those fields can name an obsolete frozen release.
Pass the exact notice path as data. The current receiver checks identity,
integrity, source-level quiet disposition and freshness before useful handling.
An already-attempted sentinel remains quiet without rewriting its packet or
acknowledging/replaying its notice. Existing meaningful result and blocker guards
remain in force.

Updating this consumer protocol repairs supported handling of arriving inputs.
It does not attach subscriptions to unregistered chats, retract previously queued
notices, wake an idle conversation, or deploy changes into an existing process.
If a result has already been presented during the requested check, preserve that
fact privately and do not send it again as part of the diagnosis.
