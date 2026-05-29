# Converge MVP Spec

This document defines the first implementable Converge system. It began as a
new managed workflow layer beside the current GoalFlow, `/verify`, and `/conv`
behavior. After C6, the C7 target is canonical command replacement and legacy
retirement, not long-term coexistence.

## Decision

Build Converge as a new recoverable workflow runtime with typed modes:

- `plan`: clarify and produce an executable plan.
- `goal`: drive an objective to completion against success criteria.
- `verify`: audit a target and produce an evidence-backed verdict.
- `conv`: iterate through analysis, accepted improvements, and re-checks until
  the target converges or a bounded stop condition is reached.

The existing GoalFlow plugin and current verification-convergence skill remain
available during MVP development. Converge can reference their concepts and test
cases, but it must not silently replace the current slash commands. Replacement
happens only through the C7 command-routing and retirement plan.

## Goals

- Make long work recoverable from durable workflow records, not from chat memory.
- Preserve implementation context across compaction, crash, stale waits, and
  watchdog recovery.
- Keep user-facing messages compact and familiar.
- Support the first four modes with one shared runtime and mode-specific gates.
- Make each mode resume from the last safe checkpoint without repeating risky
  side effects.
- Let one accepted `goal` own a full continuation plan and, after a scoped
  `plan_accepted` or equivalent owner decision, advance safe slices without
  asking for approval at every slice boundary.
- Separate automatic continuation gates, verification gates, and approval gates
  so long work stops only for real evidence, scope, safety, or budget reasons.
- Keep the MVP small enough to implement and test before adding broad platform
  abstractions.

## Non-Goals

- Do not auto-recover arbitrary prompts that were not started through Converge.
- Do not rewrite the current GoalFlow plugin in the early slices.
- Do not replace the current `/goal`, `/verify`, and `/conv` routes until
  Converge passes its own recovery, delivery-proof, and command-routing gates.
- Do not build a generic DSL, distributed job engine, database service, or
  multi-user workflow platform in the MVP.
- Do not make Converge own Telegram or external message delivery. It records
  visible delivery state after the caller sends messages.
- Do not auto-replay PR, merge, deploy, Gateway restart, browser side effects,
  external messages, destructive actions, or financial/legal actions after
  recovery.

## Core Model

Converge manages a workflow, not a single assistant reply. Every managed mode
creates a durable record before long work begins. The record states what the
workflow is trying to achieve, what phase it is in, what artifacts exist, what
has already been attempted, and what the next safe action is.

For long work, `next_safe_action` is only the current cursor. The durable owner
of the whole sequence is `continuation_plan`: a budgeted queue of slices or
steps, their evidence gates, stop conditions, and recovery cursor. A single
accepted `goal` can therefore cover an implementation slice queue when those
slices remain inside the accepted objective, non-goals, success criteria, and
approval boundaries. Slice boundaries are evidence and checkpoint boundaries,
not user approval boundaries by default.

Converge is distributed as the recovery/report-proof owner for managed
Converge workflows. Earlier MVP notes allowed a separate Work Ledger layer
during development, but that path is now retired locally and must not be
presented as a product fallback. The current boundary is:

- Converge owns mode semantics, workflow artifacts, round/slice context, and
  mode-specific recovery/report-proof instructions.
- Work Ledger is not a product dependency, fallback, watchdog, recovery source,
  or completion-proof authority for managed Converge workflows.
- Historical records may remain readable only as non-authoritative context.
- The MVP exposes a clean Converge CLI and state layout so mode behavior can be
  tested independently.

The runtime has four layers:

1. **Workflow Runtime**
   Creates records, appends events, validates transitions, lists active work,
   and emits recovery packets.
2. **Mode Handlers**
   Implement mode-specific intake, gates, visible wording, and completion logic.
3. **Context Artifacts**
   Preserve human-readable and machine-readable context for long work.
4. **Watchdog/Reconciler**
   Detects stale or unreported managed workflows and asks the main session to
   continue only the next safe action.

Current implementation boundary: the common CLI/runtime foundation provides
mode helper wrappers, cursor advancement, artifact registration, terminal
delivery reservation, report proof, and reported-state transition. These are
shared primitives, not a broad mode engine. Phase C0 hardened the shared mode
contract those modes must compose. Phase C1 added `plan` final artifact
behavior. Phase C2 added `verify` verdict, evidence, residual, and final report
behavior. Phase C2.5 stabilized terminal finalization invariants as reusable
validation helpers and smoke-test patterns. Phase C3 added the first iterative
mode behavior slice with `conv` round metadata, material-change follow-up,
original-target/delta gates, and evidence-sufficiency/max-round stops. Phase C4
added `goal` durable slice queues, accepted-plan validation, promoted goal-plan
artifacts, and child workflow references. Phase C4.5 consolidated smoke helpers
and documentation boundaries without changing production runtime behavior. Phase
C5 added local recovery inspection, watchdog packet emission, and recovery lease
acquisition. Phase C6 added local install wiring for the CLI and watchdog
runner. C7.0-C7.4 added command inventory, the route-free command dry-run
adapter contract, recovery/report-proof takeover metadata, route retirement
planning, and cleanup/removal planning. The next implementation boundary is a
separately owner-approved live route replacement readiness plan; live route
replacement and cleanup execution remain outside C7.4.

## Verdict And Closure Contract

Converge closure is based on unresolved material risk, not on finding zero more
things to say. This is intentionally industry-general: the evidence can be
source code tests, document inspection, operational runbooks, market research
sources, trading/backtest validation, legal review requests, or any other domain
appropriate proof. The contract is the same across domains:

- `pass`: no blocking remaining items. Material risks are resolved, explicitly
  accepted, or outside the accepted objective.
- `pass_with_risks`: no unresolved P0/P1 blocker remains, and no P2 ambiguity
  would force invention of safety, recovery, approval, evidence, or success
  semantics. Known risks are accepted, deferred, or assigned to implementation
  backlog without changing the current objective.
- `needs_fix`: a material ambiguity or blocker remains. This includes any P0/P1
  issue, or a P2 issue that would force the next implementer/operator to invent
  safety, recovery, approval, evidence, or success semantics.
- `blocked`: progress requires an owner decision, rescope, missing external
  input, or approval outside the recorded boundaries.
- `stopped`: the budget or stop condition is reached. Stopping is valid only if
  the final report carries the unresolved state explicitly.

Converge reports must not use a vague "remaining fix" bucket as the only closure
signal. Residual items are classified as:

- `blocking_remaining`: unresolved items that prevent `pass` or
  `pass_with_risks`.
- `accepted_risks`: known risks accepted inside the current objective and risk
  budget.
- `implementation_backlog`: details intentionally left for implementation,
  fixtures, domain validation, or follow-up execution because they do not change
  the objective, safety contract, or success criteria.
- `deferred_scope`: valid ideas outside the accepted objective or non-goals.

The user-facing completion layout may keep a `Remaining` section for continuity
with existing OpenClaw reports, but that section must contain these categories
instead of an unstructured list. A final report can close with non-empty
`accepted_risks`, `implementation_backlog`, or `deferred_scope`; it cannot close
as `pass` or `pass_with_risks` with non-empty `blocking_remaining`.

Converge must resist finding-chasing. Each follow-up round classifies new
findings by novelty, severity, objective impact, and evidence quality. Low-impact
or speculative items become accepted risk, implementation backlog, or deferred
scope unless they expose a material ambiguity in objective, safety, recovery,
approval, or evidence. The desired endpoint is not "no further comments"; it is
"no unaccepted material risk remains for the current objective."

## Durable State

Default state root:

```text
${OPENCLAW_WORKSPACE:-~/.openclaw/workspace}/state/converge
```

Each workflow gets:

```text
state/converge/workflows/<workflow_id>/
  workflow.json
  events.jsonl
  worklog.md
  artifacts/
```

Optional files:

```text
  implementation-notes.md
  decision-log.md
```

Optional files are created only when needed. A short verification or plan should
not pay the cost of extra documents.

## Workflow Record

`workflow.json` is the machine-readable replay contract. It has one small
common core plus one mode-specific block. Do not force `conv` round fields onto
a simple `plan` or `verify` record.

Common required fields:

- `schema_version`
- `workflow_id`
- `kind`: `plan | goal | verify | conv`
- `status`: `draft | running | waiting_user | waiting_subagent | verifying | completed_unreported | failed_unreported | reported | blocked | abandoned`
- `created_at`
- `updated_at`
- `last_activity_at`
- `last_visible_update_at`
- `stale_after_seconds`
- `reminder_after_seconds`
- `owner_session_key`
- `visible_delivery`
- `source_request`
- `objective`
- `non_goals`
- `success_criteria`
- `assumptions`
- `approval_boundaries`
- `approvals`
- `phase`
- `parent_workflow_id`
- `child_workflow_ids`
- `artifacts`
- `context_manifest`
- `context_artifacts`
- `decisions`
- `side_effects_performed`
- `verification`
- `active_recovery_lease`
- `active_delivery_reservation`
- `checkpoint_index`
- `continuation_plan`: object for long workflows, `goal`, and `conv`; `null`
  is allowed only for short `plan` or `verify` workflows that cannot outlive one
  response
- `next_safe_action`
- `visible_delivery_state`
- `final_status`

Mode-specific required blocks:

- `plan_state`: intake questions, answered/deferred decisions, assumptions,
  registered plan artifact id/path, and promotion recommendation.
- `goal_state`: current slice, slice list, evidence requirements, completion
  evidence, accepted risks, and optional child workflow references.
- `verify_state`: check plan, deterministic checks, reviewer findings,
  verdict, evidence, blocking remaining items, accepted risks, implementation
  backlog, and deferred scope.
- `conv_state`: round, max rounds, original-target lane, delta/regression lane,
  accepted findings, rejected/deferred findings, verdict, blocking remaining
  items, accepted risks, implementation backlog, deferred scope, and stop
  reason.

The schema files are normative. Smoke tests must validate representative bundled
fixtures against the same schemas; published snippets in this document are
illustrative unless explicitly named as fixtures.

`events.jsonl` is append-only. It records starts, progress, waits, verification,
round summaries, completions, failures, visible updates, decisions, rescope
events, and report proof.

`parent_workflow_id` and `child_workflow_ids` let a `goal` reference a child
`verify` or `conv` workflow without merging their evidence or round state into
one overloaded record.

`continuation_plan` is required for long workflows, `goal`, and `conv`. It may
be `null` only for short `plan` or `verify` records that cannot outlive one
response. The minimum object shape is:

- `plan_id`
- `current_step_index`
- `steps`: ordered slices or steps with `step_id`, `objective`,
  `expected_artifacts`, `gate`, `allowed_risk_classes`,
  `verification_commands`, `next_on_pass`, and `next_on_fail`
- `budgets`: `max_steps_per_wake`, `max_rounds`, `max_retries_per_step`
- `stop_conditions`: approval boundary, rescope needed, evidence failure,
  ambiguous recovery, stale context, retry budget exceeded, or owner stop
- `rolling_state`: completed steps, open decisions, evidence map, categorized
  residual items, active child workflows, current resume cursor, and last
  checkpoint id

`next_safe_action` must be derived from the current continuation step. Recovery
must block if the cursor and the continuation plan disagree.

For `goal` workflows, `slice_queue` is the mode-handler name for
`continuation_plan.steps`. It is not a second top-level durable field. This
keeps one authoritative continuation cursor for recovery.

Long workflows also track:

- `active_recovery_lease`: the currently valid recovery lease, or `null`
- `active_delivery_reservation`: the currently valid visible delivery
  reservation, or `null`
- `checkpoint_index`: compact metadata for completed checkpoints keyed by
  `checkpoint_id`

`checkpoint_index[checkpoint_id]` must include:

- `checkpoint_id`
- `checkpoint_seq`
- `checkpoint_type`: `checkpoint | advance | terminal`
- `cursor_before`
- `cursor_after`
- `event_id`
- `worklog_block_id`
- `created_at`
- `status_after`
- `phase_after`

`checkpoint_state_update.schema.json` is normative for checkpoint-owned writes.
Checkpoint callers provide one structured state update payload rather than
scattered status flags. Unknown fields are rejected.

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

Implementations must reject a checkpoint if the CLI `checkpoint_type` and payload
checkpoint type disagree.

`context_manifest` stores source files, URLs, or user-provided artifacts needed
to understand the workflow. It should point to context instead of copying large
source material into `workflow.json`, but it must still make recovery freshness
checkable.

Each manifest entry must include:

- `kind`: `file | url | artifact | user_text | external_ref`
- `ref`: path, URL, artifact id, or stable local reference
- `captured_at`
- `hash`, unless the entry is explicitly `mutable`
- `snapshot_path` for volatile user text, uploaded artifacts, and unstable URLs
- `recovery_policy`: `block_on_change | revalidate_on_change | accept_mutable`

Recovery must block or explicitly revalidate when a `block_on_change` or
`revalidate_on_change` reference no longer matches its recorded hash.

## Context Artifacts

`worklog.md` is mandatory for managed workflows that can outlive one response.
It is append-only and receives one compact block per meaningful round or slice.

Round block:

```markdown
## Round <n> / <slice>

- Objective:
- Scope:
- Files or surfaces touched:
- Key decisions:
- Accepted findings/fixes:
- Rejected or deferred:
- Evidence/checks:
- Blocking remaining:
- Accepted risks:
- Implementation backlog:
- Deferred scope:
- Next safe action:
```

Use meaningful slices, not tool noise. Append a block when there is a changed
target artifact, accepted decision, verification result, risky boundary, or
recovery handoff.

For long refactors, create `implementation-notes.md` when implementation detail
would make the round block too dense. For many design decisions, create
`decision-log.md`. These are opt-in artifacts, not default ceremony.

Recovery must read, in order:

1. `workflow.json`
2. `continuation_plan.rolling_state` and the current resume cursor
3. latest relevant `events.jsonl` events
4. latest `worklog.md` block
5. `context_manifest` entries needed for the current phase
6. referenced artifacts only

This avoids relying on chat history or compressed summaries.

## Continuation Gates And Checkpoints

Converge has three gate classes:

- **Continuation gate**: automatic. The previous step has recorded evidence, the
  next step is inside the accepted objective and approval boundaries, and the
  next action is local, safe, and idempotent or reconcile-first.
- **Verification gate**: automatic inside the accepted workflow. It runs
  deterministic checks or invokes a child `verify` or `conv` workflow when the
  risk warrants deeper evidence.
- **Approval gate**: owner decision required. It is used only for rescope,
  ambiguous recovery, target-changing evidence failure, budget extension, or a
  side effect outside the recorded approval boundaries.

The checkpoint primitive is the minimum long-work safety unit. Under one
per-workflow lock it must:

1. generate a `checkpoint_id` and monotonic `checkpoint_seq`
2. record `cursor_before`, `cursor_after`, `event_id`, `worklog_block_id`, and
   `created_at`
3. update `workflow.json` status, phase, `continuation_plan.rolling_state`, and
   `next_safe_action`
4. append the matching event to `events.jsonl`
5. append the compact block to `worklog.md`
6. refresh or validate context manifest hashes for touched context
7. record evidence keys, side-effect keys, and idempotency metadata

Checkpoint writes must be atomic from the workflow user's perspective. Recovery
can resume only from a completed checkpoint. If a checkpoint is partially written
or inconsistent, `recover` must block and ask for reconciliation instead of
guessing.

Every recovery-relevant event and worklog block produced by a checkpoint must
carry the same `checkpoint_id`. `continuation_plan.rolling_state.last_checkpoint_id`
must match the latest completed checkpoint. A mismatch between workflow JSON,
event log, worklog block, and checkpoint index is a recovery blocker.

Progress-only notes are allowed, but they must not move the continuation cursor.
Any command that changes `phase`, `status`, `continuation_plan.rolling_state`,
or `next_safe_action` must use the checkpoint path.

Checkpoint validation binds cursor movement to `continuation_plan`: `cursor_before`
must equal the current resume cursor/current step; `cursor_after` must be the
same cursor for wait/block/failure or a valid next step named by the current
step's gate. When a step advances, the same atomic write updates
`current_step_index`, `rolling_state.completed_steps`,
`rolling_state.last_checkpoint_id`, and `next_safe_action`.

The generic event path is append-only for non-recovery metadata. It must not
directly mutate workflow status, phase, continuation cursor, recovery leases,
checkpoint index, or next safe action. If an event request implies a state
transition, the CLI must reject it or route it through `checkpoint`.

`advance` is a checkpoint subtype. If it moves a cursor, changes status or
phase, or updates `next_safe_action`, it performs an atomic checkpoint with
`checkpoint_type=advance`. It must not maintain a separate mutation path.
`advance` must not create terminal workflow states; if the continuation gate
finds completion evidence, it returns a read-only `terminal_ready` result and
leaves terminalization to a shared terminal checkpoint path.

Terminal checkpoints use event types and statuses separately:

- successful terminal checkpoint: `event_type=complete`,
  `status_after=completed_unreported`
- failed terminal checkpoint: `event_type=fail`,
  `status_after=failed_unreported`

`completed_unreported` and `failed_unreported` are workflow statuses, never event
types.

A successful checkpoint for a cursor protected by `active_recovery_lease` must
clear that lease or replace it with a new lease for the next cursor under the
same lock. A completed checkpoint must not leave an old active recovery lease
blocking future recovery for a cursor that has already advanced.

## Mode Semantics

### `plan`

Purpose: turn a rough request into an executable plan.

Core behavior:

- Ask only unresolved questions.
- Identify objective, non-goals, assumptions, approval-sensitive actions,
  success criteria, risks, and first implementation slices.
- Produce a plan that can either be accepted as final planning output or promoted
  into a `goal` workflow.

Completion gate:

- executable plan exists
- open owner decisions are either answered or explicitly deferred
- next action is clear
- approval boundaries are explicit

`plan` may write an artifact such as `artifacts/plan.md`. It should not perform
implementation work unless promoted or explicitly authorized.

`plan` is also the reusable intake engine for `goal`. A `goal` can start by
creating or referencing a `plan` artifact, then promote the accepted objective,
non-goals, criteria, assumptions, and approval boundaries into the goal record.
This keeps goal interviews from becoming a separate, divergent mechanism.

### `goal`

Purpose: reach an objective, not merely track a task.

Core behavior:

- Confirm target and success criteria before broad work.
- Break implementation into a durable `continuation_plan` or `slice_queue`.
- Keep original target and latest delta both visible internally.
- Invoke `verify` or `conv` as a sub-loop when risk justifies it.
- Automatically advance through safe continuation gates without per-slice owner
  approval.
- Finish only when success criteria are met and all residual items are
  categorized with no blocking remaining item.

Completion gate:

- success criteria satisfied with evidence
- non-goals and approval boundaries still hold
- required verification passed or residual items are categorized with no
  blocking remaining item

Reporting gate:

- after objective completion, status becomes `completed_unreported`
- caller sends the visible final report
- caller records report proof, then marks the workflow `reported` through the
  reported-state transition

Important: `goal` includes convergence toward completion. It does not mean every
goal must run a full `conv` specialist loop. The mode handler chooses the
cheapest gate that protects the objective.

### `verify`

Purpose: evidence-first audit.

Core behavior:

- Read target and relevant artifacts.
- Run deterministic checks when available.
- Optionally collect specialist review when risk warrants it.
- Produce `pass`, `pass_with_risks`, `needs_fix`, `blocked`, or `stopped`
  verdict.

Completion gate:

- target inspected against success criteria
- evidence and categorized residual items recorded
- no edits by default unless explicitly converted to repair/convergence work

### `conv`

Purpose: active convergence loop.

Core behavior:

- Round 1 establishes original target, non-goals, likely failure modes, and
  current artifact state.
- Accepted findings become fixes or improvements.
- Follow-up rounds check both original target and delta/regression risk.
- Stop when the evidence is sufficient, not when another round would merely add
  opinions.

Completion gate:

- original target still passes or residual items are categorized
- accepted findings are handled or intentionally deferred
- delta/regression lane passes or risk is carried
- stop reason is recorded

Default `max_rounds` is 5 unless the user sets a different limit.

## End-To-End Flows

### Plan Flow

1. `converge plan --text ...` creates a `plan` workflow.
2. Runtime writes `workflow.json`, `events.jsonl`, and `worklog.md`.
3. Handler identifies missing decisions and assumptions.
4. If a real owner decision is missing, status becomes `waiting_user`.
5. If enough information exists, handler writes `artifacts/plan.md`.
6. Handler registers the artifact through the shared artifact helper, updates
   `plan_state`, and records a terminal checkpoint as `completed_unreported`.
7. Visible delivery must still use `reserve-delivery`, `report-proof`, and
   `complete-reported`; plan mode does not send or mark reported directly.
8. Validation binds `plan_state` back to the registered `plan` artifact so
   recovery and later goal promotion do not rely on stale path metadata.

The plan flow is successful only when another agent could use the plan without
reconstructing hidden chat context.

### Goal Flow

1. `converge goal --text ... --native-panel-openclaw-cli` creates a `goal` workflow on the user-facing execution path.
2. Handler either runs the plan intake path or imports an accepted plan artifact.
3. Handler records objective, non-goals, success criteria, assumptions, and
   approval boundaries.
4. Handler records a `plan_accepted` event before automatic continuation can
   begin. A draft plan must never advance slices by implication. An
   `owner_decision` event can substitute only when it carries the same required
   acceptance payload.

The required acceptance payload is: accepted `objective`, accepted `non_goals`,
accepted `success_criteria`, accepted `assumptions`, accepted
`approval_boundaries`, `plan_artifact_ref`, `plan_artifact_hash`, `source_ref`,
and `accepted_at`.
5. Work is divided into a `continuation_plan` or `slice_queue` and logged in
   `worklog.md`.
6. Each slice records changed surfaces, evidence, categorized residual items,
   checkpoint id, continuation gate result, and next safe action.
7. If risk warrants deeper review, the goal can reference a child `verify` or
   `conv` workflow.
8. Completion is blocked until criteria have evidence and risky boundaries are
   either avoided or explicitly approved.
9. Caller sends the visible final report, then records report proof.

The goal flow is successful only when the objective is actually satisfied with
no blocking remaining item. If the objective is not satisfied, the final report
can still be useful, but its result is `needs_fix`, `blocked`, or `stopped` and
the remaining gap must be explicitly classified.

The goal flow must not ask the owner to approve every slice. It stops for the
owner only when an approval gate is reached: approval boundary, objective
rescope, evidence failure that changes the agreed target, ambiguous recovery,
or exhausted budget/retry policy.

### Verify Flow

1. `converge verify --text ... --native-panel-openclaw-cli` creates a `verify` workflow on the user-facing execution path.
2. Handler records target, criteria, context artifacts, and check plan.
3. Deterministic checks run first when available.
4. Review findings are recorded as evidence, blocking remaining items, accepted
   risks, implementation backlog, or deferred scope.
5. Final verdict is one of `pass`, `pass_with_risks`, `needs_fix`, `blocked`,
   or `stopped`.
6. Current C2 implementation writes `artifacts/verify-report.md`, registers it
   as a shared `report` artifact, updates `verify_state`, and stops at
   `completed_unreported` so visible delivery still flows through
   `reserve-delivery`, `report-proof`, and `complete-reported`.

The verify flow is successful only when the verdict is supported by evidence
and does not imply hidden repair work.

### Conv Flow

1. `converge conv --text ... --native-panel-openclaw-cli` creates a `conv` workflow on the user-facing execution path.
2. Round 1 records the original target, non-goals, failure modes, and baseline
   findings.
3. Accepted findings are fixed, improved, or explicitly deferred.
4. If Round 1 changes the recommendation, plan, policy, prompt, operating
   contract, success criteria, risk posture, or artifact content, Round 2 is
   required unless the change is narrow wording directly inspected.
5. Round 2+ checks both original target and delta/regression risk.
6. Final status records verdict, stop reason, and categorized residual items.

The conv flow is successful only when convergence is evidenced, not merely when
the first review produced a better draft.

## State Transitions

Allowed MVP transitions:

- `draft` -> `running`
- `running` -> `waiting_user`
- `running` -> `waiting_subagent`
- `running` -> `verifying`
- `waiting_user` -> `running`
- `waiting_subagent` -> `running`
- `verifying` -> `running`
- `running` -> `completed_unreported`
- `running` -> `failed_unreported`
- any active non-reported state -> `failed_unreported`
- `running` -> `blocked`
- `blocked` -> `running` only through explicit `rescope` or `owner_decision`
  event
- `completed_unreported` -> `reported`
- `failed_unreported` -> `reported`
- any non-reported active state -> `abandoned`

Blocked transitions:

- `reported` must not return to active states.
- `blocked` must not continue without a new explicit decision or rescope event.
- `completed_unreported` must not perform more work; it can only record report
  proof or be reconciled as a reporting failure.
- `failed_unreported` must not auto-retry; it can only report failure, be
  abandoned, or be restarted by a new owner request.

`report-proof` records delivery proof without changing workflow status.
`complete-reported` records proof when needed, then performs the
`completed_unreported` or `failed_unreported` -> `reported` transition. It runs
after `reserve-delivery` has granted send authority and after the caller has
sent the visible message. It must not reserve delivery authority, terminalize an
active workflow, or send a message. Without a matching
`active_delivery_reservation`, it fails or requires explicit manual
reconciliation mode through `--manual-reconcile '<reason>'` plus a matching
historical `delivery_reserved` event for the same reservation id and visible
route. Manual reconciliation may record already-delivered proof or resolve a
missing active reservation after send authority was recorded, but it must not
authorize a new send.

If a workflow is already `completed_unreported` or `failed_unreported` because
terminal checkpoint creation succeeded but no `delivery_reserved` event was ever
recorded for that terminal checkpoint, `reserve-delivery` may create the first
delivery reservation for the existing terminal checkpoint. Once any historical
`delivery_reserved` event exists for that checkpoint, later attempts must
reconcile instead of authorizing another send.

`reserve-delivery` must not create the terminal checkpoint for an active
continuation workflow. `goal`, `conv`, and future modes must reach
`completed_unreported` or `failed_unreported` through their mode/checkpoint path
first, then ask `reserve-delivery` only for visible-send authority bound to the
workflow's original visible route.

## Visible Message Contract

Keep the best current layout. Converge messages use compact, predictable blocks:

```text
▶ <Mode> start
Boundary: ...

▶ Round N start

Target:
- ...

Focus:
- ...

Gate:
- ...
```

Round summary:

```text
■ Round N summary

Status:
- Verification result: continuing | complete_pass | complete_pass_with_risks | complete_needs_fix | stopped
- Original target: pass | pass_with_risks | needs_fix | blocked
- Patch regression: pass | pass_with_risks | needs_fix | blocked | none

Found:
- ...

Accepted:
- ...

Rejected / Deferred:
- ...

Checked:
- ...

Next:
- ...
```

Final report:

```text
■ <Mode> final

Status:
- Result: pass | pass_with_risks | needs_fix | blocked | stopped

Done:
Checked:
Remaining:
- Blocking remaining:
- Accepted risks:
- Implementation backlog:
- Deferred scope:
```

Examples: `■ Plan final`, `■ Goal final`, `■ Verification final`, and
`■ Convergence final`. The compact layout is shared; the first line stays
mode-specific so the user can scan the result correctly.

The top-level `Remaining` label is a layout container, not a verdict.
For Converge-managed work, its contents must be categorized. If all four
categories are empty, render `Remaining: none`. If `blocking_remaining` is
non-empty, the result cannot be `pass` or `pass_with_risks`.

Do not paste raw logs or raw agent output into chat. Put detailed context in
artifacts and summarize the arbitration.

## Recovery Behavior

Recovery is allowed only for Converge-managed workflows.

MVP actor model:

- `watchdog-check`, `scan`, and `recover` are deterministic packet emitters.
- They do not send messages, call LLMs, restart services, mutate external state,
  or execute risky actions.
- The main session executes any allowed next action, sends visible messages, and
  records proof events.

On watchdog wake:

1. Load active or unreported Converge records.
2. Classify stale, waiting, terminal-unreported, or blocked state.
3. Inspect `continuation_plan`, rolling state, current resume cursor, current
   artifacts, context manifest freshness, and referenced tasks/subagents.
4. Do not repeat risky side effects.
5. Emit a recovery packet only when `next_safe_action` is local, safe, still
   valid, and still matches the current continuation cursor.
6. The main session verifies and executes only that next safe action.
7. Send at most one visible report or waiting reminder.
8. Record visible delivery proof through an idempotent report-proof event.

If `next_safe_action` is missing or ambiguous, recovery must block and ask for a
real owner decision instead of guessing.

Converge recovery packets should be safe for the current main session to use.
They are instructions and context, not autonomous execution authority. The main
session still performs visible reporting while Converge records delivery and
report proof. After the C7.2 takeover gate, Converge-owned workflow delivery and
recovery proof must be authoritative in Converge records, with no duplicate
visible reports and no Work Ledger fallback.

Recovery and report proof must be concurrency-safe:

- all workflow JSON updates, event appends, worklog appends, and report-proof
  writes happen under a per-workflow lock
- recovery packets require an exclusive `active_recovery_lease` for the current
  workflow cursor. The lease includes `lease_id`, `lease_type=recovery`,
  `cursor`, `holder`, `acquired_at`, `lease_expires_at`, and `checkpoint_id`.
  A competing unexpired lease returns a no-action reconcile result.
- visible terminal reports require an exclusive `active_delivery_reservation`
  before sending. The reservation includes `reservation_id`,
  `lease_type=delivery`, terminal status, visible route, `acquired_at`,
  `lease_expires_at`, and `checkpoint_id`. The normal `complete-reported` path
  records proof when needed and completes this reservation after delivery.
- an expired terminal delivery reservation is not proof that no message was
  sent. If a reservation expires without report proof, the next
  `reserve-delivery` attempt must return a reconcile/manual-proof result and
  must not authorize another send until a human or deterministic channel check
  proves the original message was not delivered or records delivered proof.
- `reserve-delivery --json` returns the stable fields `workflow_id`,
  `send_authorized`, `reconcile_required`, `reservation_id`, `terminal_status`,
  `visible_delivery`, `checkpoint_id`, `lease_expires_at`, and `reason`. When
  send is not authorized, `reason` must be one of
  `active_reservation_exists`, `expired_reservation_requires_reconcile`,
  `invalid_state`, or `validation_error`.
- manual reconciliation is explicit: `report-proof` and `complete-reported` may
  bypass the active-reservation requirement only with
  `--manual-reconcile '<reason>'` plus a matching historical
  `delivery_reserved` event, and only to record existing proof or resolve a
  missing active reservation after send authority was recorded. It must not send
  or authorize a message.
- terminal checkpoint/reservation gaps are handled by `reserve-delivery`: if no
  historical `delivery_reserved` event exists for the terminal checkpoint, it
  may create the first delivery reservation; otherwise it must require
  reconciliation instead of authorizing another send.
- validation must bind `visible_delivery_state.report_proof` and reported state
  back to exactly one matching `report_proof` and `report_sent` event, so
  workflow JSON cannot claim terminal delivery proof without append-only event
  evidence.
- report proof is idempotent by workflow id, terminal status,
  delivery reservation id, visible route, and delivery message id
- two concurrent recover/report attempts must not produce duplicate visible
  report actions

Recovery and delivery leases solve different problems and must not be collapsed:
recovery leases authorize one actor to continue a cursor; delivery reservations
authorize exactly one actor to send a terminal visible report or reminder.
Converge reserves and records delivery authority, but it still does not send the
message itself.

`next_safe_action` must be structured, not free text:

- `action_type`
- `summary`
- `risk_class`: `read_only | local_files | repo_changes | external | destructive | gateway_runtime | public`
- `requires_approval`
- `approval_ref`
- `side_effect_key`
- `idempotency_policy`: `repeatable | reconcile_first | never_repeat_without_approval`
- `expected_artifacts`

Recovery must enforce `side_effect_key` and `idempotency_policy`. A repeated
side-effect key is allowed only when the policy is `repeatable`. A
`reconcile_first` action requires fresh reconcile evidence before continuing.
`never_repeat_without_approval` blocks until an exact, unexpired approval covers
the repeated side-effect key.

## Approval Boundaries

The following actions are always blocked without explicit approval:

- Gateway restart or service control
- package manager changes
- production SSH/deploy/service exposure
- database writes, migrations, destructive data operations
- browser automation with external side effects
- public posts, emails, third-party messages
- git push, PR creation, merge, release, tag, remote branch/ref mutation
- destructive filesystem operations

Recovered workflows must not infer approval from earlier broad intent unless the
approval was explicitly recorded for the exact side-effect boundary.

Recorded approvals are structured:

- `approval_id`
- `scope`
- `source_ref`
- `approved_at`
- `approved_by`
- `expires_at`
- `consumed_by_event_id`

Recovery may use an approval only when the `next_safe_action.side_effect_key`
matches the recorded approval scope and the approval is unexpired and
unconsumed, unless the action is explicitly marked reusable.

## MVP Acceptance Tests

Minimum deterministic tests:

- JSON schema validation for workflow and event records
- published sample workflow JSON validates against the schema
- `plan`, `goal`, `verify`, and `conv` helper commands create workflows through
  the same start path as `start --kind`
- `artifact` records a workflow artifact and artifact event without mutating
  cursor state
- create `plan` workflow, append worklog block, complete, record report proof
- create `goal` from a plan artifact and preserve accepted intake fields
- create accepted plan/goal boundary event and allow automatic continuation;
  verify that an unaccepted draft plan cannot advance
- reject `plan_accepted` or equivalent owner decision when required acceptance
  payload fields or plan artifact hash are missing
- create `goal` with the C0 baseline initialization contract and verify that it
  cannot auto-advance implementation work before accepted mode-owned steps exist
- create a mode-owned `goal` continuation queue, pass a safe slice, and advance
  to the next slice only through the shared checkpoint path
- `advance` uses the checkpoint path and returns `terminal_ready` instead of
  creating terminal state when the next continuation target is terminal
- create `goal` with continuation gate failure and block without moving the
  cursor
- create `verify` workflow, record evidence verdict, complete
- create `conv` workflow with Round 1 material change, require Round 2 gate
- create `goal` workflow with success criteria, block on missing evidence, then
  complete after evidence
- create parent `goal` with child `verify` or `conv` reference without merging
  child state into parent
- stale running workflow produces a recovery packet with `next_safe_action`
- two concurrent recover calls for the same cursor produce one recovery lease
  and one no-action reconcile result
- stale long workflow recovers from the continuation cursor, not merely the last
  chat summary or latest worklog text
- terminal-unreported workflow asks for visible report proof, not duplicate work
- two concurrent terminal-unreported recovery/report agents produce one delivery
  reservation and one no-send result
- `report-proof` records delivery proof without marking a workflow `reported`;
  `complete-reported` requires the matching reservation and then clears it
- expired terminal delivery reservation without report proof returns
  reconcile/manual-proof and does not authorize a duplicate send
- `reserve-delivery --json` returns the stable authorization/reconcile payload
- `report-proof --manual-reconcile` records existing proof without authorizing a
  new send
- direct `event` calls that attempt to change status, phase, cursor, recovery
  lease, checkpoint index, or `next_safe_action` are rejected or routed through a
  checkpoint
- `checkpoint` rejects missing or mismatched structured state update payloads
  and records status/phase/cursor/event/worklog linkage atomically when valid
- `checkpoint_state_update.schema.json` rejects unknown fields and invalid
  `worklog_block_kind`, `step_result`, residual, side-effect, context-update, and
  terminal payload shapes
- checkpoint advancement rejects invalid cursor transitions and verifies
  `current_step_index`, `completed_steps`, `last_checkpoint_id`, and
  `next_safe_action` update in the same atomic write
- terminal checkpoint accepts `event_type=complete` or `event_type=fail` with the
  matching terminal status and rejects `event_type=completed_unreported` or
  `event_type=failed_unreported`
- terminal checkpoint fixtures include successful, failed, and negative
  event/status examples, and every fixture validates or fails for the expected
  reason
- `advance` returns `terminal_ready` when completion evidence is present and does
  not set `completed_unreported` or `failed_unreported`; mode-owned terminal
  state is created through `ModeOutcome` plus a `checkpoint_type=terminal`
  checkpoint, then `reserve-delivery` grants visible-send authority only after
  terminal material validation
- waiting-user workflow does not spam reminders before stale threshold
- blocked workflow cannot resume without explicit owner decision or rescope event
- risky side-effect action is blocked after recovery without recorded approval
- repeated side-effect keys obey `repeatable`, `reconcile_first`, and
  `never_repeat_without_approval`
- broad approval is rejected for a narrower exact side-effect key mismatch
- changed context manifest hash blocks or triggers explicit revalidation
- concurrent report-proof / complete-reported attempts produce one proof action
  and one final `reported` state
- two concurrent checkpoint attempts preserve one valid cursor and append
  events/worklog blocks consistently under the workflow lock
- corrupt or mismatched `checkpoint_id` across workflow JSON, event log,
  worklog, or checkpoint index blocks recovery
- successful checkpoint clears or advances the matching recovery lease
- stale and reminder thresholds behave deterministically before and after the
  configured boundary
- visible message formatter rejects raw logs and preserves compact layout

Manual smoke tests:

- start a `conv` workflow, interrupt after Round 1, recover and continue Round 2
- start a `goal` workflow, interrupt after a slice, recover from latest worklog
- start a `plan` workflow and promote it to `goal`

## Implementation Order

1. Create the new Converge package skeleton.
2. Implement workflow record schema, append-only event store, and
   `continuation_plan` schema.
3. Implement context artifact creation, append helpers, and the atomic
   checkpoint primitive.
4. Implement shared mode-handler primitives that route mode-owned transitions
   through the checkpoint path.
5. Implement `plan` mode.
6. Implement `verify` mode. Completed through the C2 verdict/report slice.
7. Stabilize terminal finalization invariants. C2.5 fixed the handoff between
   terminal mode state, checkpoint-backed evidence sequence, report/plan
   artifacts, `reserve-delivery`, `report-proof`, and `complete-reported`
   before adding more complex modes.
8. Implement `conv` mode with original-target and delta gates. Completed
   through the C3 iterative convergence slice.
9. Implement `goal` mode with durable slice queue, success criteria, evidence
   gates, plan-artifact promotion, and child workflow reference fields.
   Completed through the C4 goal slice.
10. Implement local recovery inspection and repair. Completed through the C5
    recovery slice: `scan`, `watchdog-check`, `recover`, recovery leases,
    cursor/checkpoint/context/side-effect blocking, recovery transaction
    mismatch blocking, terminal-delivery routing, and focused recovery smoke.
11. Add install/bootstrap wiring for the CLI and watchdog runner. Completed
    through the C6 install wiring slice: local CLI install, development/local
    deploy wrapper, plugin manifest copy, deterministic local watchdog runner,
    post-install verification commands, and install-path smoke coverage.
12. Replace canonical managed `/goal`, `/verify`, and `/conv` routing with
    Converge only after the new runtime passes smoke tests and a separate
    owner-approved live route replacement readiness plan. C7.0-C7.4 are command
    replacement and legacy retirement planning, not a long-term `/c*` adapter
    family and not live routing execution.

## Open Questions

- Command replacement rollout: C7.0-C7.4 have fixed inventory, dry-run adapter,
  recovery/report-proof authority, route retirement, and cleanup/removal
  planning; live routes change only in a later explicit owner-approved
  operational task.
- Package home: independent package is cleaner for MVP; integration into Ledger
  or GoalFlow should wait until the runtime proves stable.
- Subagent orchestration: MVP should record subagent ids/results, but not own a
  full task router.
