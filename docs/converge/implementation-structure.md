# Converge MVP File And CLI Structure

This document proposes the first implementation shape for the Converge MVP.
It is intentionally concrete enough to implement, but avoids forcing current
GoalFlow or verification-convergence internals to change immediately.

## Package Layout

Recommended source checkout:

```text
openclaw-converge/
  README.md
  package.json
  openclaw.plugin.json
  bin/
    converge
  converge/
    __init__.py
    cli.py
    schema.py
    store.py
    artifacts.py
    checkpoint.py
    continuation.py
    recovery.py
    messages.py
    approvals.py
    modes/
      __init__.py
      plan.py
      goal.py
      verify.py
      conv.py
    schemas/
      workflow.schema.json
      event.schema.json
      artifact.schema.json
      checkpoint_state_update.schema.json
    templates/
      worklog.md
      implementation-notes.md
      decision-log.md
  prompts/
    converge-watchdog.md
  scripts/
    deploy-local.sh
    install-local.sh
    converge_watchdog_runner.py
```

Python is the preferred MVP runtime because current Ledger and GoalFlow runtime
helpers already use Python for durable local state and watchdog logic. A later
JS plugin wrapper can call the Python CLI.

## Runtime State Layout

```text
${OPENCLAW_WORKSPACE:-~/.openclaw/workspace}/state/converge/
  events.jsonl
  workflows/
    <workflow_id>/
      workflow.json
      events.jsonl
      worklog.md
      artifacts/
      implementation-notes.md
      decision-log.md
```

Top-level `events.jsonl` is optional but useful for global scan performance.
Per-workflow `events.jsonl` is mandatory.

## CLI Surface

Primary executable:

```text
converge
```

Implemented commands through the common runtime foundation:

```bash
converge start --kind plan|goal|verify|conv --text '<request>' [--owner-session-key ...] [--visible-delivery JSON]
converge plan --text '<request>'
converge goal --text '<request>'
converge verify --text '<target>'
converge conv --text '<target>'
converge status --workflow-id <id> [--json]
converge checkpoint --workflow-id <id> --checkpoint-type checkpoint|advance|terminal --summary-file <path> --state-update JSON [--next-action JSON] [--evidence JSON]
converge advance --workflow-id <id> --summary-file <path> --evidence JSON [--next-action JSON]
converge artifact --workflow-id <id> --kind plan|evidence|patch|report|context --path <path>
converge reserve-delivery --workflow-id <id> --terminal-status completed|failed --visible-delivery JSON --final-status JSON [--terminal-evidence JSON] [--failure-reason '<text>'] [--json]
converge report-proof --workflow-id <id> --reservation-id <id> --delivery-message-id <id> --visible-delivery JSON [--manual-reconcile '<reason>']
converge complete-reported --workflow-id <id> --reservation-id <id> --delivery-message-id <id> --visible-delivery JSON [--manual-reconcile '<reason>']
converge append-round --workflow-id <id> --round <n> --summary-file <path>
converge event --workflow-id <id> --type <event_type> --event-id <id> [--note '<text>'] [--payload JSON]
converge validate [--workflow-id <id>] [--sample-docs]
```

Implemented recovery commands:

```bash
converge recover --workflow-id <id> [--json]
converge scan [--json]
converge watchdog-check [--json]
```

Mode helper commands are thin wrappers around shared runtime primitives. They
should not bypass the shared record creation, artifact registration, checkpoint,
or terminal delivery-proof paths.

Current implementation boundary: these helper commands create and move durable
workflow state, register artifacts, reserve terminal delivery, and record report
proof. Phase C0 hardened the shared mode contracts: `goal` and `conv` start
from a mode-neutral initialization contract instead of the temporary Slice 1-9
continuation plan, mode-state updates flow through the checkpoint path, CLI and
mode handlers share artifact registration, and checkpoint validation covers
evidence artifact refs plus local-file-only context manifest updates. Phase C1
implemented the first narrow `plan` behavior slice and final plan artifact on
top of those shared contracts. Phase C2 implemented the second short mode
slice: `verify` verdict, evidence, residual, and final report behavior. Phase
C2.5 stabilized terminal finalization invariants as reusable validation helpers
and smoke-test patterns for later modes. Phase C3 implemented the first
iterative mode behavior slice: `conv` round metadata, bounded convergence gates,
material-change follow-up, and evidence-sufficiency/max-round stops. Phase C4
implemented `goal` durable slice queue behavior. Phase C4.5 completed internal
smoke helper/docs cleanup; Phase C5 completed recovery commands, and Phase C6
completed local install wiring. The next boundary is Phase C7: Slash/Ledger
Adapter Routing.

The current executable Phase C todo is `docs/converge/phase-c-todo.md`.

## CLI Behavior By Command

### `start`

Creates:

- `workflow.json`
- per-workflow `events.jsonl`
- `worklog.md`
- first `start` event

Returns:

- `workflow_id`
- `kind`
- generic `phase`
- initial `continuation_plan` for long-capable modes
- generic structured `next_safe_action`
- `artifact_paths`

It must reject unknown `kind`, invalid visible delivery JSON, and unsafe
workflow ids.

Slice 1 does not return mode-specific message text or intelligent phase
planning. Mode-specific start text is introduced in the message formatter and
mode-handler slices.

For `goal`, `start` may create an empty continuation plan shell immediately.
The mode handler fills the ordered slices after intake. For short `plan` or
`verify`, the continuation plan can be absent.

### `checkpoint`

Records the atomic long-work checkpoint. It must acquire the workflow lock and
commit, as one operation:

- generated `checkpoint_id` and monotonic `checkpoint_seq`
- `cursor_before`, `cursor_after`, `event_id`, `worklog_block_id`, and
  `created_at`
- workflow status/phase updates
- `continuation_plan.rolling_state` and current cursor updates
- structured `next_safe_action`
- append-only event
- worklog block from `--summary-file`
- evidence keys and side-effect/idempotency metadata
- context manifest validation or refresh for touched local files

`--next-action` must validate against the structured `next_safe_action` schema.
`--evidence` must contain at least `evidence_key`, `kind`, `summary`, and
`artifact_refs`; empty evidence is allowed only when the checkpoint records a
wait, block, or explicit failure.

`--state-update` is the single structured mutation payload for checkpoint-owned
state. It validates against `checkpoint_state_update.schema.json` with
unknown-field rejection.

Required fields:

- `checkpoint_type`: `checkpoint | advance | terminal`
- `status_after`
- `phase_after`
- `cursor_before`
- `cursor_after`
- `event_type`
- `worklog_block_kind`: `checkpoint_summary | round_summary | slice_summary | terminal_summary | recovery_summary`
- `step_result`: `none | passed | failed | blocked | waiting | terminal`

Optional fields use exact object names:

- `event_status`
- `residuals`: `blocking_remaining`, `accepted_risks`,
  `implementation_backlog`, `deferred_scope`
- `side_effects`: performed side-effect keys and idempotency metadata
- `context_manifest_updates`: added, refreshed, or invalidated manifest entries
- `terminal_evidence`
- `failure_reason`

The CLI validates that `--checkpoint-type` matches
`state_update.checkpoint_type` before acquiring the workflow lock.

Example:

```bash
converge checkpoint --workflow-id conv-20260523-example \
  --checkpoint-type advance \
  --summary-file worklog/round-1.md \
  --state-update '{"checkpoint_type":"advance","status_after":"running","phase_after":"round","cursor_before":"round-1","cursor_after":"round-2","event_type":"advance","worklog_block_kind":"round_summary","step_result":"passed"}' \
  --next-action '{"action_type":"run_round","summary":"Run Round 2.","risk_class":"local_files","requires_approval":false,"approval_ref":null,"side_effect_key":"conv:round-2","idempotency_policy":"reconcile_first","expected_artifacts":["worklog.md"],"cursor":"round-2"}'
```

Partial checkpoints are validation errors. Recovery must never resume from a
checkpoint unless the workflow JSON, event, worklog block, and continuation
cursor agree.

Checkpoint validation must bind cursor changes to `continuation_plan`.
`cursor_before` must equal the current resume cursor/current step. `cursor_after`
must be either the same cursor for wait/block/failure or a valid next step named
by the current step's gate. When a step advances, the same atomic write updates
`current_step_index`, `rolling_state.completed_steps`,
`rolling_state.last_checkpoint_id`, and `next_safe_action`.

If a checkpoint succeeds for a cursor that has an `active_recovery_lease`, the
checkpoint must clear that lease or replace it with a lease for the next cursor
under the same workflow lock.

### `advance`

Evaluates the current continuation gate and moves the cursor only when the next
step is still inside the accepted objective, approval boundaries, and risk
class budget. It does not execute the next step. Any cursor, status, phase, or
`next_safe_action` mutation is implemented as an atomic checkpoint with
`checkpoint_type=advance`, not as a separate write path.

Result behavior:

- `advance_ready`: moves the cursor, writes an `advance` checkpoint, and returns
  the next structured safe action.
- `blocked_approval`: sets status `blocked`, writes an `advance` checkpoint, and
  records the exact approval boundary.
- `blocked_verification`: sets status `verifying` or `blocked`, writes an
  `advance` checkpoint, and records missing or failed evidence.
- `blocked_reconcile`: sets status `blocked`, writes an `advance` checkpoint,
  and records the inconsistent recovery state.
- `terminal_ready`: returns a read-only result that completion evidence is
  present and terminal delivery can be reserved. It does not set
  `completed_unreported` or `failed_unreported`.

`advance` must not create terminal workflow states. Terminal status transitions
are owned by the shared terminal checkpoint path: completion uses
`checkpoint_type=terminal` with `event_type=complete` and
`status_after=completed_unreported`; failure uses `checkpoint_type=terminal`
with `event_type=fail` and `status_after=failed_unreported`. Mode handlers may
produce terminal checkpoints through this shared path. `reserve-delivery` owns
visible-send authority and delivery reservation only after that terminal
checkpoint exists.

### `append-round`

Appends a round/slice block to `worklog.md` and records a `progress` event with
the same short summary. It must not overwrite prior round blocks.

`append-round` is progress-only. It must not update `phase`, `status`,
`continuation_plan.rolling_state`, or `next_safe_action`. Any recovery-relevant
round or slice boundary must use `checkpoint`. `append-round` is for
non-recovery notes only; it must not be used for the authoritative round or slice
handoff.

### `event`

Appends a supported manual event without acting as a second mutation path.
The current generic `event` command is limited to `progress`, `plan_accepted`,
`owner_decision`, and `rescope`. Report proof is a schema event type, but it is
owned by the dedicated `report-proof` command because it must bind delivery
proof to reservation state.

If the requested event implies a recovery-relevant state change, such as
`wait`, `complete`, `fail`, `advance`, status/phase/cursor movement, terminaling
an active workflow, or updating `next_safe_action`, the CLI must reject the
direct `event` call or delegate to the checkpoint implementation. It must not
mutate `workflow.json` status/phase outside the checkpoint path.

Current Slice 1-3 event validation rules:

- every event includes `created_at`, `event_type`, `workflow_id`, and
  `schema_version`
- every event includes a unique `event_id`
- direct `event` rejects checkpoint-owned state mutation event types
- direct `event` rejects payload fields that mutate workflow state
- `plan_accepted`, `owner_decision`, and `rescope` payloads must include the
  accepted scope fields, plan artifact identity, source, and accepted timestamp

Target validation rules for later event/report wrapper slices:

- checkpoint-produced events that change status must name the new status
- `wait` events must include `waiting_user` or `waiting_subagent`
- `verify` events must include a check state or verdict
- terminal events must include either evidence or an explicit failure reason
- report proof events must include a visible delivery route and delivery id
- `verify` and `round_summary` events with a terminal or closure verdict must
  include categorized residuals: `blocking_remaining`, `accepted_risks`,
  `implementation_backlog`, and `deferred_scope`

Schema event types must match `event.schema.json`. Not every schema event type
is valid through the generic `converge event` command. Schema event types are:

- `start`
- `progress`
- `round_start`
- `round_summary`
- `wait`
- `verify`
- `checkpoint`
- `advance`
- `complete`
- `fail`
- `visible_update_sent`
- `report_sent`
- `delivery_reserved`
- `recovery_lease_acquired`
- `lease_released`
- `abandon`
- `recovery_wake_delivered`
- `plan_accepted`
- `report_proof`
- `owner_decision`
- `rescope`
- `artifact`

Blocked workflow recovery depends on `rescope` and `owner_decision`, so these
must be emitted either by `converge event` or by dedicated thin wrapper commands.
Automatic goal continuation depends on `plan_accepted` or an equivalent
`owner_decision` event that records the accepted objective, non-goals, success
criteria, and approval boundaries.

`plan_accepted` payload must include:

- accepted `objective`
- accepted `non_goals`
- accepted `success_criteria`
- accepted `assumptions`
- accepted `approval_boundaries`
- `plan_artifact_ref`
- `plan_artifact_hash`
- `source_ref`
- `accepted_at`

An `owner_decision` can unlock automatic continuation only if it carries the same
acceptance payload. Otherwise it is only a generic decision or unblock event.

### `reserve-delivery`

Reserves visible terminal delivery authority before the caller sends the final
message. It must:

1. acquire the workflow lock
2. verify the workflow is already `completed_unreported` or `failed_unreported`
   with an indexed terminal checkpoint; active workflows must be terminalized by
   their mode/checkpoint path before this command grants send authority
3. reject or reconcile if another unexpired delivery reservation exists
4. if an expired delivery reservation exists for the same terminal workflow,
   return `send_authorized=false` with `reconcile_required=true`; do not create
   a new reservation until manual proof or deterministic channel reconciliation
   establishes whether the original authorized send happened
5. reject active continuation workflows without creating a terminal checkpoint;
   `reserve-delivery` must not become a fallback terminalization path for
   `goal`, `conv`, or future modes
6. require the caller `--final-status` to match the stored terminal workflow
   `final_status` before authorizing delivery
7. bind the requested `visible_delivery` to the workflow's original
   `visible_delivery` route when one was recorded
8. create `active_delivery_reservation` with `reservation_id`, terminal status,
   visible route, `checkpoint_id`, `acquired_at`, and `lease_expires_at`
9. append a `delivery_reserved` event
10. return `send_authorized=true` for exactly one caller

Callers that do not receive send authority must not send a visible terminal
report. The current common runtime creates the terminal checkpoint before the
delivery reservation through mode/checkpoint code, not through
`reserve-delivery`; if recovery observes an unreported terminal workflow with no
active reservation and no historical `delivery_reserved` event for that terminal
checkpoint, `reserve-delivery` may create the first delivery reservation for
that checkpoint. If a historical reservation already exists, it must reconcile or
use explicit manual proof instead of authorizing another send.

The JSON result shape is stable:

```json
{
  "workflow_id": "conv-20260523-example",
  "send_authorized": true,
  "reconcile_required": false,
  "reservation_id": "delivery-20260523-example",
  "terminal_status": "completed_unreported",
  "visible_delivery": {"channel": "telegram", "target": "example-chat"},
  "checkpoint_id": "chk-terminal-20260523-example",
  "lease_expires_at": "2026-05-23T05:05:00Z",
  "reason": null
}
```

When `send_authorized=false`, `reservation_id` is `null` unless the caller is
being directed to reconcile an existing reservation, and `reason` must be one of
`active_reservation_exists`, `expired_reservation_requires_reconcile`,
`invalid_state`, `visible_delivery_mismatch`, `terminal_status_mismatch`, or
`validation_error`.

### `report-proof`

Records visible report proof after the caller successfully sends a completion or
failure report. In the current common runtime this command is proof-only:
`complete-reported` is the state transition from `completed_unreported` or
`failed_unreported` to `reported`.

It must:

1. acquire the workflow lock
2. verify the workflow has a matching `active_delivery_reservation`, or require
   `--manual-reconcile` with a non-empty reason and a matching historical
   `delivery_reserved` event for the same reservation id and visible route
3. append an idempotent `report_proof` event using workflow id, reservation id,
   visible route, delivery message id, and reconciliation reason when supplied;
   the referenced `delivery_reserved` event carries the terminal status and
   checkpoint binding
4. leave workflow status unchanged

Duplicate proof with the same proof key is a no-op. Conflicting proof keys are
validation errors. Manual reconciliation is only for recording proof that
already exists or resolving a missing active reservation after send authority
was already recorded; it must not authorize a new send or invent a
reservation/route.

### `complete-reported`

Post-send reporting state transition that records proof when needed, then marks
the workflow reported:

```bash
converge report-proof --reservation-id <id> ...
converge complete-reported --reservation-id <id> ...
```

It runs after a visible message send succeeds and only when a matching
`active_delivery_reservation` already exists. It does not reserve delivery
authority and does not send the message. Without an active matching reservation
it must fail or require `--manual-reconcile` with a non-empty reason plus a
matching historical `delivery_reserved` event. It appends the `report_sent`
event, marks workflow status `reported`, and clears the delivery reservation.

### `scan`

Lists active, waiting, verifying, unreported, or stale workflows. It is read-only.

### `watchdog-check`

Produces deterministic wake input:

- `clean`
- `needs_wake` with stale/recovery packets
- `needs_wake` with unreported completion packets
- `error`

It should not call LLMs, send messages, restart services, or perform recovery
actions directly. It should not mutate workflow state except optional read-only
scan metadata outside the workflow record.

The runner can be installed as a LaunchAgent later, but the MVP should first
test it as a deterministic local command. Gateway restart is not part of this
command.

### `recover`

Reads `workflow.json`, recent events, latest `worklog.md` block, and referenced
artifacts. It outputs the next safe recovery instruction plus a short-lived
lease. It does not perform risky side effects.

For workflows with a continuation plan, `recover` must read the rolling state and
resume cursor before inspecting the latest worklog block. The latest worklog
block is supporting context, not the source of truth for which slice comes next.

`recover` must persist an exclusive `active_recovery_lease` under the workflow
lock before returning an executable recovery packet. If another unexpired lease
exists for the same cursor, it returns a no-action reconcile result. If the lease
expires, a later `recover` may acquire a new lease after reconciling that the
cursor and checkpoint still match.

A successful checkpoint for the leased cursor must clear
`active_recovery_lease` or replace it with a lease for the next cursor under the
same lock. Failed or abandoned recovery may append `lease_released`, but lease
expiry alone is not evidence that the prior holder made no changes; recovery
must reconcile cursor and checkpoint before issuing another executable packet.

### `validate`

Validates `workflow.json`, `events.jsonl`, artifact references, status
transitions, locks/lease metadata, context manifest freshness, report-proof
idempotency keys, and required mode fields. It is read-only and intended for
smoke tests and recovery debugging.

For terminal reporting, validation must bind `visible_delivery_state.report_proof`
and `visible_delivery_state.reported` to exactly one matching `report_proof` and
`report_sent` event. A workflow must not validate as reported if workflow JSON
claims proof without the append-only event trail.

For `plan`, validation must also bind `plan_state.final_plan_artifact_id` and
`plan_state.final_plan_artifact_path` to exactly one registered `plan` artifact.
This prevents later goal promotion from relying on stale or invented plan
artifact state.

`validate --sample-docs` validates representative bundled fixtures so schema
contracts do not drift. Fenced JSON snippets in this document are illustrative
unless they are explicitly wired as fixtures.

## Mode Handler Responsibilities

### `modes/plan.py`

Owns:

- intake questions
- answered and deferred intake decisions
- assumptions
- plan artifact structure
- promotion recommendation

Does not own:

- implementation
- risky approval execution

### `modes/goal.py`

Owns:

- objective and success criteria
- slice planning
- evidence requirements
- completion gate
- invoking verify/conv as sub-workflows when needed

Does not own:

- worker/task engine
- public/external side-effect execution

### `modes/verify.py`

Owns:

- audit checklist
- evidence verdict
- categorized residual format

Does not own:

- repairs by default

### `modes/conv.py`

Owns:

- round loop metadata
- original-target and delta gate
- accepted/rejected finding format
- closure verdict and categorized residuals
- stop reason

Does not own:

- unlimited rounds
- automatic scope expansion

## Message Formatter

`messages.py` owns visible text generation and linting. It should provide:

```python
format_start(workflow) -> str
format_round_start(workflow, round_spec) -> str
format_round_summary(workflow, round_result) -> str
format_final(workflow) -> str
lint_visible(text) -> None
```

Lint rules:

- no markdown tables
- no raw stack traces or raw logs
- first line must be one of the known markers
- Telegram-safe length budget
- final reports include `Status`, `Done`, `Checked`, `Remaining`
- Converge final reports render `Remaining` as a structured layout
  container with `Blocking remaining`, `Accepted risks`,
  `Implementation backlog`, and `Deferred scope`
- final first lines are mode-specific: `Plan final`, `Goal final`,
  `Verification final`, or `Convergence final`
- `pass` and `pass_with_risks` final reports fail lint when
  `blocking_remaining` is non-empty
- low-impact findings must not be rendered as blocking unless the mode handler
  classified them as objective, safety, approval, recovery, or evidence blockers

## Workflow Schema Draft

Minimal JSON shape:

```json
{
  "schema_version": 1,
  "workflow_id": "conv-20260523-example",
  "kind": "conv",
  "status": "running",
  "created_at": "2026-05-23T05:00:00Z",
  "updated_at": "2026-05-23T05:00:00Z",
  "last_activity_at": "2026-05-23T05:00:00Z",
  "last_visible_update_at": null,
  "stale_after_seconds": 900,
  "reminder_after_seconds": 86400,
  "phase": "round",
  "owner_session_key": "session:telegram-example",
  "visible_delivery": {"channel": "telegram", "target": "example-chat"},
  "source_request": "...",
  "objective": "...",
  "non_goals": [],
  "success_criteria": [],
  "assumptions": [],
  "approval_boundaries": [
    "No push, merge, deploy, or gateway restart without explicit approval."
  ],
  "approvals": [],
  "parent_workflow_id": null,
  "child_workflow_ids": [],
  "artifacts": [],
  "context_manifest": [
    {
      "kind": "file",
      "ref": "docs/converge/mvp-spec.md",
      "captured_at": "2026-05-23T05:00:00Z",
      "hash": "sha256:...",
      "snapshot_path": null,
      "recovery_policy": "block_on_change"
    }
  ],
  "context_artifacts": [],
  "decisions": [],
  "side_effects_performed": [],
  "verification": {},
  "active_recovery_lease": null,
  "active_delivery_reservation": null,
  "checkpoint_index": {},
  "continuation_plan": {
    "plan_id": "cp-conv-20260523-example",
    "current_step_index": 0,
    "steps": [
      {
        "step_id": "round-1",
        "objective": "Complete baseline convergence review.",
        "expected_artifacts": ["worklog.md"],
        "gate": {"type": "continuation", "requires_evidence": true},
        "allowed_risk_classes": ["read_only"],
        "verification_commands": [],
        "next_on_pass": "round-2-if-material-change",
        "next_on_fail": "blocked_verification"
      }
    ],
    "budgets": {
      "max_steps_per_wake": 1,
      "max_rounds": 5,
      "max_retries_per_step": 1
    },
    "stop_conditions": [
      "approval_boundary",
      "rescope_needed",
      "evidence_failure",
      "ambiguous_recovery",
      "retry_budget_exceeded"
    ],
    "rolling_state": {
      "completed_steps": [],
      "open_decisions": [],
      "evidence_map": {},
      "residuals": {
        "blocking_remaining": [],
        "accepted_risks": [],
        "implementation_backlog": [],
        "deferred_scope": []
      },
      "active_child_workflows": [],
      "current_resume_cursor": "round-1",
      "last_checkpoint_id": null
    }
  },
  "next_safe_action": {
    "action_type": "continue_round_review",
    "summary": "Continue Round 1 review.",
    "risk_class": "read_only",
    "requires_approval": false,
    "approval_ref": null,
    "side_effect_key": "read:docs-converge",
    "idempotency_policy": "repeatable",
    "expected_artifacts": ["worklog.md"],
    "cursor": "round-1"
  },
  "visible_delivery_state": {},
  "final_status": null,
  "conv_state": {
    "round": 1,
    "max_rounds": 5,
    "verdict": null,
    "original_target_lane": {},
    "delta_regression_lane": {},
    "accepted_findings": [],
    "rejected_or_deferred": [],
    "blocking_remaining": [],
    "accepted_risks": [],
    "implementation_backlog": [],
    "deferred_scope": [],
    "stop_reason": null
  }
}
```

## Event Schema Draft

Minimal JSONL event shape:

```json
{
  "schema_version": 1,
  "event_id": "evt-20260523-example",
  "workflow_id": "conv-20260523-example",
  "created_at": "2026-05-23T05:00:00Z",
  "event_type": "progress",
  "note": "Round 1 baseline review started.",
  "payload": {
    "round": 1,
    "artifact_paths": ["worklog.md"]
  }
}
```

Core event types:

- `start`
- `progress`
- `round_start`
- `round_summary`
- `wait`
- `verify`
- `checkpoint`
- `advance`
- `complete`
- `fail`
- `visible_update_sent`
- `report_sent`
- `delivery_reserved`
- `recovery_lease_acquired`
- `lease_released`
- `abandon`
- `recovery_wake_delivered`
- `report_proof`
- `rescope`
- `owner_decision`
- `plan_accepted`
- `artifact`

The generic `converge event` command is narrower than the schema enum. In the
current foundation it accepts only manually supplied metadata events with current
validators: `progress`, `plan_accepted`, `owner_decision`, and `rescope`. Other
core event types are owned by dedicated commands or later runtime slices.

`round_start` and `round_summary` are first-class events because Converge's
round boundaries are part of its user-facing and recovery behavior.

Closure event payloads must use the fixed verdict vocabulary:

- `pass`
- `pass_with_risks`
- `needs_fix`
- `blocked`
- `stopped`

They must also include the four residual buckets. The schema rejects a `pass` or
`pass_with_risks` closure when `blocking_remaining` is non-empty. Non-empty
`accepted_risks`, `implementation_backlog`, or `deferred_scope` are allowed on
`pass_with_risks` and on stopped/blocked reports as long as the final message
does not hide them.

`rescope` and `owner_decision` are the only MVP events allowed to move a blocked
workflow back to running. They must include the owner-facing decision text or a
reference to the visible message that supplied it.

`plan_accepted` records the boundary between draft planning and executable goal
continuation. Without `plan_accepted` or an equivalent `owner_decision`, a
`goal` workflow may stay in planning or waiting state but must not auto-advance
implementation slices.

Checkpoint events include `checkpoint_id`, `checkpoint_seq`, `cursor_before`,
`cursor_after`, and `worklog_block_id`. Recovery validates these against
`checkpoint_index` and `continuation_plan.rolling_state.last_checkpoint_id`.

Checkpoint index entries are keyed by `checkpoint_id`:

```json
{
  "checkpoint_id": "chk-20260523-example",
  "checkpoint_seq": 1,
  "checkpoint_type": "advance",
  "cursor_before": "round-1",
  "cursor_after": "round-2",
  "event_id": "evt-20260523-example",
  "worklog_block_id": "worklog-20260523-example",
  "created_at": "2026-05-23T05:01:00Z",
  "status_after": "running",
  "phase_after": "round"
}
```

Terminal checkpoint state update fixtures:

```json
{
  "checkpoint_type": "terminal",
  "status_after": "completed_unreported",
  "phase_after": "terminal",
  "cursor_before": "round-2",
  "cursor_after": "round-2",
  "event_type": "complete",
  "event_status": "completed_unreported",
  "worklog_block_kind": "terminal_summary",
  "step_result": "terminal",
  "terminal_evidence": {
    "evidence_key": "final-report-ready",
    "kind": "verification",
    "summary": "Final report content is ready for visible delivery.",
    "artifact_refs": ["worklog.md#terminal-summary"]
  }
}
```

```json
{
  "checkpoint_type": "terminal",
  "status_after": "failed_unreported",
  "phase_after": "terminal",
  "cursor_before": "round-2",
  "cursor_after": "round-2",
  "event_type": "fail",
  "event_status": "failed_unreported",
  "worklog_block_kind": "terminal_summary",
  "step_result": "terminal",
  "failure_reason": "verification_blocker_unresolved"
}
```

Negative fixture: this JSON is syntactically valid but must fail
`checkpoint_state_update.schema.json` because workflow status names are not event
types.

```json
{
  "checkpoint_type": "terminal",
  "status_after": "completed_unreported",
  "phase_after": "terminal",
  "cursor_before": "round-2",
  "cursor_after": "round-2",
  "event_type": "completed_unreported",
  "worklog_block_kind": "terminal_summary",
  "step_result": "terminal"
}
```

Report proof and reported events store proof details under `payload` and bind
back to the delivery reservation checkpoint:

```json
{
  "schema_version": 1,
  "event_id": "evt-report-20260523-example",
  "workflow_id": "conv-20260523-example",
  "event_type": "report_sent",
  "created_at": "2026-05-23T12:00:00Z",
  "checkpoint_id": "chk-terminal-example",
  "status_after": "reported",
  "phase_after": "reported",
  "payload": {
    "reservation_id": "delivery-20260523-example",
    "visible_delivery": {"channel": "telegram", "target": "example-chat"},
    "delivery_message_id": "19809",
    "reported_at": "2026-05-23T12:00:00Z"
  }
}
```

## First Build Slices

### Slice 1: Store and Schema

Implement:

- workflow id generation
- state directory creation
- atomic workflow JSON update
- append-only event writes
- per-workflow lock around JSON update, event append, and worklog append
- first-class atomic checkpoint operation
- worklog initialization
- `continuation_plan` schema and rolling state basics
- `active_recovery_lease`, `active_delivery_reservation`, and checkpoint index
  schema basics
- JSON schema validation command
- context manifest hash capture/validation for local files
- generic structured `next_safe_action` defaults
- smoke tests for create/update/append, checkpoint atomicity, parallel append,
  continuation cursor consistency, and sample JSON
- smoke test that `append-round` cannot move the continuation cursor
- smoke tests for checkpoint id mismatch, recovery lease exclusivity, blocked
  owner-decision matching, and `plan_accepted` payload validation

No mode intelligence yet.

### Slice 2: Message Formatter

Implement visible start, round start, round summary, and final formatting. Add
lint smoke tests. This protects the existing message layout before workflow
logic grows.

Slice 2 also implements verdict rendering and residual bucket linting:

- accepted verdicts: `pass`, `pass_with_risks`, `needs_fix`, `blocked`,
  `stopped`
- `Remaining` renders as `Blocking remaining`, `Accepted risks`,
  `Implementation backlog`, and `Deferred scope`
- `pass` and `pass_with_risks` are rejected when blocking remaining items exist
- low-severity or speculative findings are rendered as backlog/deferred/accepted
  risk unless the mode handler marks a material blocker

### Slice 3: Shared Mode Base

Implement shared mode-handler primitives that route mode-owned transitions
through the checkpoint path instead of mutating workflow JSON or event logs
directly. This slice does not implement full mode-specific intelligence yet.

### Common Runtime Foundation

Implement common CLI/runtime commands that every later mode needs before full
mode intelligence is added:

- `advance` as a bounded continuation cursor movement through the checkpoint
  path, returning `terminal_ready` without creating terminal workflow state
- `artifact` registration under workflow lock with artifact events and optional
  file hashes
- `reserve-delivery`, `report-proof`, and `complete-reported` as the terminal
  delivery-proof family, keeping terminal checkpoint creation, visible delivery
  proof, and final `reported` transition separate
- `plan`, `goal`, `verify`, and `conv` as thin wrappers around `start --kind`
- focused smoke coverage for the shared runtime foundation

This foundation is not a full mode slice. It exists to prevent later mode
handlers from inventing separate write paths for cursor advancement, artifacts,
or terminal report proof.

### Internal Development Cleanup Rules

Converge is still an internal development runtime. Until there is a released
runtime or external user data to preserve, implementation should prefer replacing
temporary contracts over adding compatibility layers.

- Do not keep legacy/fallback paths for temporary development shapes.
- Store each durable concept in one canonical shape. For example,
  `final_status` is an object, not an object-or-string union.
- Use one storage field for one meaning. If a durable field uses `result`, do
  not also support `verdict` as an equivalent storage alias.
- Mode handlers must not create separate workflow write paths. Status, phase,
  cursor, checkpoint, artifact, and report-proof changes go through the shared
  runtime primitives.
- Repeated no-send, reconcile, reservation, and report result payloads should be
  centralized as helpers instead of re-assembled in each branch.
- Keep cleanup, state-machine refactors, and feature behavior in separate
  commits or slices unless they are inseparable for correctness.
- At the end of each implementation slice, run a short cleanup scan for stale
  `legacy`, `compat`, `fallback`, duplicate payload construction, and direct
  workflow mutation paths before starting the next feature slice.
- Record deferred P3 cleanup in `docs/converge/p3-debt-register.md`; do not
  leave it only in chat or ledger logs.

### Completed A/B Cleanup

Before Phase C mode-specific behavior slices started, these cleanup phases were
completed:

- [x] Phase A: make `final_status` object-only. Remove string fallback support,
  remove `verdict` as a storage alias where it only duplicates `result`, update
  schemas, smoke tests, and docs.
- [x] Phase B: consolidate `reserve-delivery` reporting helpers. Keep behavior
  unchanged while centralizing active-reservation no-send payloads, historical
  reservation reconcile payloads, reservation creation, and send-authorized
  result payloads.

### Historical Boundary: Shared Mode Contract First

After Phase A and Phase B, the next implementation boundary was Phase C0. It did
not reopen the common CLI/runtime foundation. The only allowed pre-mode
hardening was a small shared mode contract pass that prevented the first real
mode from becoming a template for duplicate write paths.

### Phase C0 / Slice 4: Shared Mode Contract Hardening

Harden only the contracts that every mode will otherwise need to invent. This is
not a new orchestration framework, not a recovery implementation, and not a
rewrite of the completed runtime foundation.

Required C0 changes:

- replace the hard-coded Converge implementation Slice 1-9 default
  continuation plan with a mode-neutral initialization contract for `goal` and
  `conv`. Until mode-owned steps exist, use a single non-executing intake or
  baseline cursor that cannot auto-advance implementation work.
- add one shared mode-state update path, restricted to the active
  `{kind}_state`, persisted only through the existing checkpoint/advance outcome
  path under workflow lock, so mode handlers do not mutate workflow JSON
  directly
- extract artifact registration into a shared helper used by both CLI commands
  and mode handlers
- keep terminal delivery authority in `reserve-delivery`; mode handlers may
  prepare terminal-ready data, but must not create a separate terminal-report
  path
- add minimal validation for continuation plan shape, advance gate boundaries,
  and evidence artifact references before modes depend on them. Evidence
  artifact refs must resolve to registered workflow artifact ids or explicitly
  supported workflow-local refs such as existing `worklog.md#...` heading
  anchors or registered artifact-relative paths; dangling refs fail smoke.
- reject `context_manifest_updates` outside the currently implemented
  local-file manifest shape. C0 must not add `scan`, `recover`, new context
  providers, or hash-change recovery behavior.

C0 must include targeted smoke coverage for these contracts and a cleanup scan
for direct workflow mutation, stale `legacy`/`compat`/`fallback` paths, and
duplicated artifact or terminal-report write logic.

Explicit C0 non-goals:

- no plan, verify, conv, or goal intelligence
- no slash-command or Ledger adapter migration
- no full recovery/watchdog implementation
- no broad mode framework, plugin runtime, or subagent orchestration layer
- no compatibility layer for the temporary Converge Slice 1-9 default plan

### Phase C1 / Slice 5: `plan`

Implemented the first short mode as a narrow user-facing vertical slice:

`converge plan --text ...` creates a plan workflow, writes `artifacts/plan.md`,
registers that artifact through the shared helper, updates `plan_state` through
`ModeOutcome`, and stops at `completed_unreported`. Visible output still must go
through the existing `reserve-delivery`, `report-proof`, and
`complete-reported` family.

This slice proves the shared artifact, mode-state, final-status, and report
formatting contract without also carrying verify verdict semantics.

### Phase C2 / Slice 6: `verify`

Implemented evidence verdict records, categorized residuals, `verify_state`, and
final report formatting through the same shared mode contract.

`converge verify --text ...` creates or resumes a verify workflow, writes
`artifacts/verify-report.md`, registers that artifact as a `report`, updates
`verify_state` through `ModeOutcome`, and stops at `completed_unreported`.
Visible output still must go through the existing `reserve-delivery`,
`report-proof`, and `complete-reported` family.

`verify` remains separate from `plan` so each short mode has its own completion
gate and smoke evidence.

### Phase C2.5 / Slice 6.5: Terminal Finalization Invariants

Before implementing the more complex `conv` and `goal` modes, C2.5 stabilizes
the terminal finalization boundary exposed by C1/C2. This is not a feature
slice; it is a small contract-hardening slice for terminal reporting behavior.

C2.5 turns the C2 hardening lessons into reusable rules:

- terminal modes finish through `ModeOutcome` and a terminal checkpoint, not by
  mutating workflow JSON directly
- the workflow `final_status` must match the terminal checkpoint
  `final_status`
- workflow verification evidence must match the ordered checkpoint-backed
  evidence sequence, ending with the terminal checkpoint evidence
- the active terminal `{kind}_state` snapshot must match the terminal checkpoint
  `mode_state_update` exactly; uncheckpointed extra terminal keys are drift
- registered terminal artifacts must match their mode state before
  `reserve-delivery` grants send authority, including path, hash, kind, and
  rendered content when the mode has deterministic rendering
- `reserve-delivery` remains the pre-send material validation and
  send-authority gate
- `report-proof` and `complete-reported` remain post-send proof/reporting
  steps; they validate reservation, checkpoint, delivery event, proof identity,
  and idempotency, but do not require live terminal artifacts to still exist
  after send authority was granted

C2.5 cleans up the C2 patch surface where those rules were spread across
`cli.py`, but only to make the boundary explicit. Allowed cleanup is limited to
small helper extraction, clearer helper names, and shared smoke-test
fixtures/templates for terminal modes. It must not introduce a broad terminal
framework or start C3/C4 behavior.

The reusable smoke pattern covers:

- caller `final_status` mismatch before `reserve-delivery`
- workflow/checkpoint final-status drift
- mode-state/checkpoint drift
- checkpoint-backed evidence sequence drift
- uncheckpointed extra evidence
- stale or missing terminal artifact before `reserve-delivery`
- terminal evidence refs remain checkpoint-backed; dangling refs are covered by
  the shared checkpoint evidence validator
- successful post-send proof after local artifact deletion
- duplicate proof idempotency
- wrong reservation, checkpoint, or visible-delivery identity

### Phase C3 / Slice 7: `conv`

Implemented round metadata, material-change follow-up requirement,
original-target gate, delta gate, and max-round stop.

The `conv` mode must stop on evidence sufficiency, not on "no possible further
comments." New findings after a convergence round are classified by novelty,
severity, objective impact, and evidence quality. Findings that do not alter the
accepted objective, safety contract, recovery semantics, approval boundary, or
evidence sufficiency are carried as accepted risk, implementation backlog, or
deferred scope rather than forcing another round.

`conv` persists `conv_state` through the shared `ModeOutcome` checkpoint path
and registers `artifacts/conv-report.md` as the terminal report artifact.
Terminal `conv_state`, `final_status`, and checkpoint evidence must satisfy the
C2.5 exact-match and checkpoint-backed evidence invariants before
`reserve-delivery` can authorize a visible report.

`append-round` remains a progress helper. It must not become the conv state
machine. Conv round semantics are persisted through mode-state checkpoint
outcomes.

### Phase C4 / Slice 8: `goal`

Implemented objective/success criteria gate, `continuation_plan.steps` as the
goal-mode slice queue, evidence completion check, plan-artifact promotion,
`plan_accepted` validation, and child workflow reference fields.

`goal` persists `goal_state` through the shared `ModeOutcome` checkpoint path,
registers `artifacts/goal-plan.md` as the promoted plan artifact, and mirrors
the durable slice queue from `continuation_plan.steps` into
`goal_state.slice_queue`. Terminal `goal_state`, `final_status`, and checkpoint
evidence reuse the C2.5 exact-match and checkpoint-backed evidence invariants
before `reserve-delivery` can authorize a visible report.

The C4 slice records child `verify` and `conv` workflow reference fields by
durable ids. It does not create child workflows, copy child state, consolidate
test helpers, implement recovery, wire adapters, restart Gateway, perform
external actions, route slash commands, push, open PRs, or release.

### Phase C4.5 / Slice 8.5: Smoke Helper Consolidation

C4.5 ran a bounded test/docs cleanup slice that consolidated repeated smoke
setup and assertion patterns into small helpers.

This slice was intentionally after `conv` and `goal` because C2.5 only proves the
terminal-finalization helper shape. C3 adds iterative round/delta/follow-up
patterns, and C4 adds objective criteria, slice queue, and completion-gate
patterns. Helper extraction before those shapes exist risks freezing the wrong
abstraction.

C4.5 added shared smoke helpers for the high-duplication mode/runtime smokes:
CLI invocation, workflow/event access, assertion helpers, wrapper execution,
and visible-delivery fixtures. It did not implement recovery, install wiring,
adapter routing, Gateway restart, external action, push, PR, or release work.
The C5 recovery transaction item is now closed. The artifact write/register
crash-window item remains optional post-C5 cleanup unless it becomes a direct
install/runtime blocker.

### Phase C5 / Slice 9: Recovery

C5 implements `scan`, `watchdog-check`, and `recover`. `scan` is read-only and
classifies active, stale, waiting-user, terminal-unreported, and blocked recovery
records. `watchdog-check` converts that classification into clean versus
wake-needed recovery packets. `recover` acquires one active recovery lease for
the current cursor and returns the next safe recovery packet, or blocks without
mutation when recovery is ambiguous.

Recovery uses the continuation cursor for long workflows and blocks if the
cursor, event log, latest checkpoint, context manifest, or side-effect policy
disagrees. Repeated recover calls for the same unexpired lease return a
deterministic no-action block instead of acquiring a second lease.

C5 added smoke coverage for:

- stale/interrupted recovery fixtures across plan, verify, conv, and goal;
- terminal-unreported records and terminal report-pipeline events;
- reported workflows staying clean after report proof completion;
- waiting-user reminder thresholds;
- missing checkpoint worklog blocks;
- changed context manifest hashes;
- repeated `side_effect_key` policies;
- risky-side-effect approval blocking;
- checkpoint/event-log disagreement;
- recovery lease exclusivity, transaction mismatch blocking, and leased-workflow
  no-wake behavior.

C5 does not send visible reports, restart Gateway, execute external actions,
install runtime files, route slash commands, push, open PRs, or release. Visible
delivery remains delegated to `reserve-delivery`, `report-proof`, and
`complete-reported`.

### Phase C6 / Slice 10: Install Wiring

Implement local install and development deployment scripts:

- [x] install CLI executable
- [x] install plugin manifest if needed by OpenClaw
- [x] install watchdog runner files
- [x] do not restart Gateway automatically
- [x] print exact post-install verification commands

The installed CLI uses the same package entrypoint as the development wrapper:
`python3 -m converge.cli`. `scripts/install-local.sh` copies the standalone
package into `${OPENCLAW_CONVERGE_INSTALL_ROOT:-~/.openclaw/converge}`, writes
`${OPENCLAW_CONVERGE_BIN_DIR:-~/.openclaw/bin}/converge`, installs the manifest
under `${OPENCLAW_CONVERGE_PLUGIN_DIR:-~/.openclaw/plugin-sources/openclaw-converge}`,
and prints post-install verification commands. `scripts/deploy-local.sh`
currently delegates to `install-local.sh` only.

The installed watchdog runner executes `converge watchdog-check --json` and
emits the resulting packet with a local-only policy marker. It does not wake
sessions, restart Gateway, route slash commands, or perform external delivery.

Existing `/verify`, `/conv`, `/goal`, or Ledger adapters are not part of Phase
C1-C6. Add them only after recovery smoke and install wiring pass, and only
through explicit routing/migration work.

### Phase C7 / Slice 11: Slash/Ledger Adapter Routing

Add explicit routing only after the CLI modes, recovery, and install wiring are
stable. Existing slash commands remain unchanged until this phase. Adapter work
must be explicit, testable, and reversible.

## Integration Strategy

MVP command names should avoid clashing with current slash commands:

- Use CLI `converge ...` for development.
- Keep current `/goal`, `/verify`, and `/conv` unchanged.
- Later, add explicit routing only after Converge passes recovery smoke:
  - `/cplan`
  - `/cgoal`
  - `/cverify`
  - `/cconv`

After confidence grows, decide whether existing slash commands should migrate.
Migration must be explicit and reversible.

## Verification Checklist Before First PR

- `python -m py_compile converge/*.py converge/modes/*.py`
- schema validation smoke passes
- published sample JSON validates
- store smoke passes
- checkpoint atomicity smoke passes
- `checkpoint_state_update.schema.json` rejects unknown fields and invalid
  `worklog_block_kind`, `step_result`, residual, side-effect, context-update, or
  terminal payload shapes
- checkpoint rejects invalid cursor transitions and updates `current_step_index`,
  `completed_steps`, `last_checkpoint_id`, and `next_safe_action` atomically
- parallel append/lock smoke passes
- recovery lease exclusivity smoke passes
- successful checkpoint clears or advances the matching recovery lease
- delivery reservation exclusivity smoke passes
- expired delivery reservation without proof returns reconcile/no-send
- after scoped `plan_accepted` or an equivalent owner decision, continuation
  gate auto-advances safe local slices only within accepted boundaries
- missing `plan_accepted` or equivalent owner decision blocks goal
  auto-continuation
- `plan_accepted` missing required payload fields cannot unlock continuation
- approval gate blocks rescope, risky side effects, and budget extension
- message formatter smoke passes
- message formatter rejects unknown verdicts
- final report smoke verifies `Remaining` bucket rendering
- `pass` and `pass_with_risks` with non-empty blocking remaining fail lint
- `needs_fix` is required when a P0/P1 blocker or semantics-inventing P2 remains
- one smoke per mode passes
- `verify` and `conv` closure events include categorized residuals
- low-impact repeat findings can close as backlog/deferred scope without
  starting an endless convergence round
- recovery smoke passes for stale and unreported states
- no command sends Telegram directly
- no command restarts Gateway
- risky actions are blocked unless explicitly recorded as approved
- repeated `side_effect_key` with `repeatable` passes without extra approval
- repeated `side_effect_key` with `reconcile_first` blocks until fresh reconcile
  evidence is recorded
- repeated `side_effect_key` with `never_repeat_without_approval` blocks until
  exact unexpired approval is recorded
- broad approval is rejected when exact `side_effect_key` does not match
- changed context manifest hashes block or force explicit revalidation
- duplicate report-proof attempts are idempotent
- `complete-reported` without a matching reservation fails or requires manual
  reconciliation mode
- concurrent terminal-unreported recoveries produce one delivery reservation and
  one no-send result before visible delivery
- terminal checkpoints reject `event_type=completed_unreported` and
  `event_type=failed_unreported`; they accept only `complete` or `fail` with the
  matching terminal workflow status
- `advance` returns `terminal_ready` for completion readiness and must not set
  `completed_unreported` or `failed_unreported`; terminal state is created by the
  mode/checkpoint path, and `reserve-delivery` only reserves delivery after an
  indexed `checkpoint_type=terminal` checkpoint already exists
- `converge event --type owner_decision` or `rescope` against a blocked workflow
  must append decision metadata only or delegate to checkpoint; it must not
  directly mutate status back to `running`
- blocked workflows require `rescope` or `owner_decision` to continue
- parent/child workflow references validate without copying child state
- existing GoalFlow/verification-convergence files are untouched unless the task
  explicitly asks for integration
