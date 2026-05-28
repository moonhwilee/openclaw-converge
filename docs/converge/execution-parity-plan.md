# Converge Execution Parity Plan

## Objective

Bring Converge `/goal`, `/verify`, and `/conv` from plan-only or synthetic
records to execution parity with the retained `verification-convergence` skill,
without building a broad orchestration platform first.

The first priority is truthfulness: if a requested workflow requires real
execution, Converge must not mark it complete from scaffold-only artifacts,
planned child references, or synthetic reports.

This document is planning only. It does not authorize code changes, commits,
pull requests, deployment, Gateway restart, cron registration, live route
replacement, or cleanup of legacy surfaces.

## Current Implementation Diagnosis

Converge already has a useful durable substrate:

- Workflow state root, events, checkpoints, artifacts, and schema validation.
- Recovery and visible-report proof primitives:
  `reserve-delivery`, `report-proof`, and `complete-reported`.
- Mode-specific state records for `goal`, `verify`, and `conv`.
- Command-route inventory and dry-run adapter documents for exact `/goal`,
  `/verify`, and `/conv`.

The weak part is execution:

- `/goal` promotes a plan artifact, records acceptance metadata, and writes
  `child_workflow_refs` with `planned_reference` status. It does not create,
  run, await, or collect child workflows.
- `/verify` records a fixed verification report shape. Its own residual says
  domain-specific audit execution belongs to later integrations. It does not
  run deterministic checks, launch reviewers, inspect target-specific evidence,
  or bind verdicts to actual findings.
- `/conv` records convergence semantics with a synthetic evidence-sufficient
  round. It does not run repair/improve rounds, apply accepted changes, perform
  original-target plus delta review, or recheck after material changes.
- The command adapter is intentionally dry-run/classification oriented in C7
  documents. That protects route safety, but it can mislead users if mode
  terminal reports read like completed execution.

The result is a mismatch between durable workflow records and actual operating
behavior: Converge can prove that it produced a plan/report artifact, but it
cannot yet prove that the requested verification, convergence, or goal execution
actually happened.

## Gap Against The Retained Verification-Convergence Skill

The retained `verification-convergence` skill is not just a report format. It is
an execution protocol:

- Infer mode: audit, repair, or improve.
- Lock objective, non-goals, success criteria, risk level, approval boundaries,
  context sources, and maximum rounds.
- Run deterministic checks before specialist review where possible.
- Generate a dynamic panel of 3-5 reviewer profiles from artifact type, domain,
  risk, and likely failure modes.
- Collect independent findings with evidence anchors.
- Deduplicate by failure mode and arbitrate each finding as `block`, `fix`,
  `accept risk`, `defer`, or `reject`.
- Apply accepted fixes or improvements when mode allows edits.
- Recheck original-target and delta/regression lanes after material changes.
- Stop only on explicit evidence: pass, pass with risks, needs fix, or stopped.

Current Converge mode handlers preserve some vocabulary from that protocol, but
they do not execute the protocol. Execution parity means Converge must either
perform those steps directly or record that execution was delegated and proven.

## Parity Definition

Converge reaches parity only when a retained-skill capability can be mapped to
machine-checkable Converge evidence. A passing route smoke is not enough if it
does not exercise the intelligent orchestration path.

Minimum parity matrix:

- Mode inference: audit, repair, or improve is recorded with rationale and
  approval boundaries.
- Deterministic-first execution: available local checks run before reviewer
  work, or the reason for skipping them is recorded.
- Reviewer panel: 3-5 specialist profiles are generated or supplied with
  artifact type, domain, risk level, failure modes, prohibited actions, and
  output schema.
- Structured findings: every raw finding has an id, evidence anchor, severity,
  risk, confidence, and suggested minimal fix or test.
- Dedupe and arbitration: every raw finding maps exactly once to a failure-mode
  group and an arbitration decision.
- Accepted work boundary: only the coordinator or an explicitly designated fix
  runner may apply accepted changes after approval-boundary checks.
- Round lanes: original-target and delta/regression gates are present after any
  accepted material change.
- Follow-up gate: a material change requires another round unless a waiver
  includes direct check evidence and narrow-change rationale.
- Stop proof: terminal states are proven by status-specific evidence contracts,
  not by a valid report scaffold.
- Visible reporting: start, round summary, final report, report-proof, and
  complete-reported use the real delivery path when the route is user-visible.

Legacy skill retirement is blocked until audit, repair, and improve parity
fixtures pass this matrix through exact `/goal`, `/verify`, and `/conv` routes.

## Required Behavior By Mode

### `/goal`

Required behavior:

- Intake or consume an accepted GoalSpec-style plan with objective, non-goals,
  success criteria, approval boundaries, and slice queue.
- Decide whether the accepted goal is plan-only or execution-required.
- For execution-required goals, create actual child workflow records for the
  planned verification and convergence work, not only planned reference ids.
- Track child lifecycle: queued, running, terminal-unreported,
  completed-reported, blocked, or failed.
- Collect child outcomes before parent terminal completion.
- Produce parent final status only from real child evidence and parent gates.

Completion rule:

- A `/goal` that requested implementation, improvement, convergence, or
  verification must not report `pass` or `pass_with_risks` until required child
  workflow evidence has been created, run, and collected.
- If Converge cannot execute children yet, the correct terminal state is
  `failed_unreported` with `final_status.result="blocked"` and schema-valid
  `final_status.stop_reason`, for example `needs_execution` or
  `blocked_missing_executor`, not completed.

### `/verify`

Required behavior:

- Parse target, scope, read-only boundary, risk level, and evidence gates.
- Run target-specific deterministic checks when available: file inspection,
  diff inspection, tests, lint, smoke commands, logs, status commands, or
  external-safe fetches.
- Optionally request specialist findings when risk warrants it.
- Bind every verdict to evidence records.
- Reserve `pass` for enough evidence, not for a valid report scaffold.

Completion rule:

- If no target-specific check or reviewer execution was performed, `/verify`
  must not report a passing verdict. It must report
  `failed_unreported` with `final_status.result="blocked"`,
  `final_status.stop_reason="blocked_no_execution_evidence"`, and a clear
  residual in `residuals.blocking_remaining`.

### `/conv`

Required behavior:

- Infer repair or improve mode unless explicit read-only text downshifts to
  audit.
- Lock original objective and non-goals before round work begins.
- Run bounded rounds. Each round has an original-target lane and, after accepted
  changes, a delta/regression lane.
- Apply only accepted fixes or improvements within approval boundaries.
- Start another round after material changes unless the change is narrow,
  directly checked, and creates no plausible new risk.
- Stop with explicit proof of evidence sufficiency, max round, blocked state, or
  owner stop.

Completion rule:

- A synthetic no-delta round is not enough to complete `/conv` unless Converge
  can prove real round execution happened.

## Minimal Execution Architecture

Do not build a generic distributed workflow engine first. Add the smallest
execution layer that lets Converge distinguish real execution from scaffolding.

Minimum components:

- `execution_required`: whether the request semantics require real execution.
- `execution_capability`: one of `plan_only`, `local_checks`,
  `delegated_agents`, or `full_loop`.
- `execution_performed`: boolean set only by a runner that performed checks,
  spawned/collected agents, applied changes, or collected child workflow output.
- `synthetic_report`: boolean for reports produced from static fixtures or
  scaffold records.
- `execution_evidence_refs`: artifact, event, check, child workflow, or agent
  result references that justify completion.
- `completion_blocker`: a shared terminal validation function that rejects
  success states when execution was required but not performed.

Recommended shape:

- Keep mode handlers responsible for mode-specific records.
- Add a thin execution coordinator used by `goal`, `verify`, and `conv`.
- Keep actual tool execution outside state validation. State validators should
  verify evidence presence and consistency, not run checks themselves.
- Let the main OpenClaw session or a future safe adapter perform tool-heavy work
  initially. Converge only marks it complete when that runner records proof.
- Default all managed workflows to `execution_performed=false` and
  `synthetic_report=true` until trusted execution proof is recorded.
- Treat missing or unknown execution truth markers as unsafe. Success is allowed
  only after the classifier and trusted runner have both recorded their fields.
- Mode handlers may classify intent and write scaffolds, but they cannot set
  `execution_performed=true`. That authority belongs only to an explicit
  trusted runner event that references concrete evidence.

## Scaffold-Only Completion Blocker

This is the first implementation priority.

Shared terminal gate:

- If `execution_required`, `execution_performed`, or `synthetic_report` is
  missing or unknown for a managed `/goal`, `/verify`, or `/conv` workflow,
  terminal success is blocked. The blocker fails closed.
- `execution_required=false` is valid only for explicit plan-only or
  scaffold-only requests, with a recorded rationale.
- If `execution_required=true` and `execution_performed=false`, terminal result
  cannot be `pass`, `pass_with_risks`, `evidence_sufficient`, or
  `completed_unreported`.
- If `synthetic_report=true`, terminal result cannot be a success result unless
  the request is explicitly plan-only or scaffold-only.
- If a `goal` has child references with `planned_reference` and the goal is
  execution-required, the parent cannot complete successfully.
- If `verify` has only contract/scaffold evidence and no target-specific
  evidence, verdict must be `final_status.result="blocked"` with a concrete
  `stop_reason` and `residuals.blocking_remaining`.
- If `conv` has synthetic findings only, stop condition must not be
  `evidence_sufficient` for execution-required requests.

Suggested blocked encoding:

- Keep the current terminal schema stable unless a separate schema migration is
  explicitly approved. Do not add ad hoc `final_status.blocker_code`.
- For terminal blockers, use `event_type="fail"`,
  `status_after="failed_unreported"`, `final_status.result="blocked"`,
  a schema-valid `final_status.stop_reason`, and
  `residuals.blocking_remaining` entries explaining the missing proof.
- Use active workflow `status_after="blocked"` only for owner-decision,
  rescope, or waiting states that are not terminal final reports.
- Initial blocker stop reasons:
  - `needs_execution`
  - `blocked_missing_executor`
  - `blocked_no_execution_evidence`
  - `blocked_child_workflows_not_run`
  - `blocked_missing_execution_truth_markers`

The visible report must say exactly which execution proof is missing.

## Child Workflow Creation And Collection

Goal child workflow design:

- Parent `/goal` creates real child workflows using the same state root:
  one or more `verify` children and, when changes are required, one or more
  `conv` children.
- Child ids are deterministic for a parent, child role, canonicalized scope
  hash, and attempt number. Canonicalized scope is produced from normalized JSON
  with stable key order and without volatile fields such as timestamps or
  delivery attempts.
- Attempt `0` is reused for idempotent retries of the same parent/role/scope.
  Attempt increments only after a durable child terminal state is collected and
  a new child is intentionally requested for a new round or changed scope.
  Recovered creation retries resolve to the same child instead of creating
  duplicates.
- Parent stores `child_workflow_ids`, not synthetic ids, and each child stores
  the reciprocal `parent_workflow_id`.
- Child creation is recorded as an idempotent event before parent collection
  starts. Parent metadata update and child workflow creation must be recoverable
  as one logical operation even if interruption happens between them.
- Each child records:
  - `parent_workflow_id`
  - `child_role`
  - `required_for_parent_completion`
  - `scope`
  - `approval_boundaries`
  - `visible_delivery_policy`
  - `terminal_status`
  - `report_proof_ref`
- Parent collection waits for all required children to reach a terminal state
  and have visible report proof when user-visible reporting is required.
- Parent final status summarizes child statuses and carries residuals forward.

Recovery behavior:

- Parent-child creation recovery order:
  1. Reserve deterministic child id and append parent `child_creation_intent`.
  2. Create or load the child workflow with matching `parent_workflow_id`.
  3. Append child `parent_linked`.
  4. Append parent `child_workflow_created` with the child id.
  5. Begin parent collection only after both reciprocal records are present.
  Recovery repeats this sequence idempotently from the first missing step.
- If a parent is interrupted after creating children, recovery scans both parent
  `child_workflow_ids` and child-side `parent_workflow_id` indexes, reconciles
  missing reciprocal links, and resumes from the latest durable checkpoint.
- If a child is terminal-unreported, parent cannot complete reported until the
  child report proof is recorded or explicitly waived by a safe, visible
  owner-approved decision.
- Failed children become parent blockers unless classified as deferred or
  accepted risk with evidence.

## Agent And Check Loop Design

Minimal loop:

1. Build an execution plan from mode, target, risk, and approval boundaries.
2. Run deterministic checks first where safe and relevant.
3. For higher-risk targets, request 3-5 specialist findings through an adapter
   that returns structured findings only. Agents must not send visible messages
   or perform external actions.
4. Main coordinator arbitrates findings.
5. In `conv`, apply accepted local fixes only when permitted.
6. Re-run proportional checks.
7. Record evidence, findings, decisions, and stop proof.

The first Converge-native implementation can support deterministic checks and
manual runner-provided findings before full agent spawning exists. The critical
requirement is that Converge records whether real execution occurred.

## State And Schema Changes

Add mode-neutral execution fields to workflow or mode state.

Phase 0 implementation subset:

- `execution_required`
- `execution_capability`
- `execution_performed`
- `synthetic_report`
- `execution_evidence_refs`
- `execution_blockers`
- `runner_ref`
- shared terminal gate using `final_status.result="blocked"` plus
  schema-valid `stop_reason` and `residuals.blocking_remaining` for
  execution-proof failures

Phase 1+ execution timing:

- `execution_started_at`
- `execution_completed_at`

Add parent-child fields.

Phase 3+:

- `parent_workflow_id`
- `child_workflow_ids`
- `child_role`
- `required_for_parent_completion`
- `child_collection_status`

Add events.

Phase 0+:

- `execution_required_classified`
- `execution_blocked`

Phase 1+:

- `execution_started`
- `deterministic_check_recorded`

Phase 3+:

- `child_workflow_created`
- `child_workflow_collected`

Phase 4+:

- `agent_panel_requested`
- `agent_findings_recorded`
- `finding_arbitrated`
- `accepted_change_applied`
- `round_completed`

Phase 5+:

- `evidence_map_updated`
- `agent_result_collected`

Existing report-proof events remain the source of truth for visible completion.

## Failure, Interruption, And Recovery

Failure handling rules:

- Missing executor is a blocked state, not a successful residual.
- Execution-proof blockers use workflow status `completed_unreported` only when
  execution actually completed and visible proof is pending. When execution
  could not run but the workflow has a final verdict, use checkpoint
  `status_after="failed_unreported"` with `final_status.result="blocked"` and a
  concrete schema-valid `stop_reason`. Use nonterminal
  `status_after="blocked"` only when the workflow is waiting for owner decision,
  rescope, or missing approval and should not yet emit a final report.
- Interrupted execution preserves the last completed event and recovery cursor.
- Risky side effects are never replayed automatically after interruption.
- External actions, deploy, Gateway restart, push, PR, release, and destructive
  operations remain approval-gated and outside this parity plan.
- If a visible delivery attempt fails after execution succeeds, workflow remains
  `completed_unreported` until report proof is recorded.
- If a child workflow fails, the parent records a blocker and stops collection
  unless the owner explicitly accepts a narrower plan-only result.

Recovery smoke must prove parent-child reconciliation, not just standalone
workflow scanning.

## Smoke And Acceptance Tests

Required smoke tests:

- `/goal` with execution language creates or requires child workflow execution;
  it cannot complete with only `planned_reference` child refs.
- `/goal` with explicit plan-only language may complete with a plan artifact and
  no child execution.
- `/verify` with only scaffold evidence returns `failed_unreported` with
  `final_status.result="blocked"`, a concrete `stop_reason`, and
  `residuals.blocking_remaining`, not `pass_with_risks`.
- `/verify` with a deterministic check records target-specific evidence and can
  pass only when that evidence satisfies the gate.
- `/conv` with synthetic-only evidence cannot stop on `evidence_sufficient`.
- `/conv` with an accepted material change requires a follow-up round unless
  directly checked and explicitly justified as narrow.
- Parent `/goal` cannot complete reported while required child workflows are
  running, blocked, failed, or terminal-unreported.
- Parent-child recovery smoke covers partial creation after
  `child_creation_intent`, orphan child with `parent_workflow_id` but missing
  parent child id, duplicate retry with the same deterministic id, attempt
  increment after collected terminal child, and owner/state-root mismatch.
- Recovery after interruption can identify active child workflows and pending
  report proof.
- Visible report proof remains required before `complete-reported`.
- Existing plan-only and dry-run C7 tests still pass with explicit
  `execution_required=false` or `execution_capability=plan_only`.

Acceptance criteria:

- No execution-required mode can produce a successful final status without
  execution evidence.
- Synthetic/scaffold reports are machine-identifiable.
- User-visible reports clearly distinguish planned, blocked, executed, and
  reported states.
- Existing safe route boundaries remain unchanged until separately approved.

## Phased Implementation Order

### Phase 0: Truth Markers And Completion Blocker

- Add execution-required classification and synthetic/scaffold markers.
- Fail closed when execution truth markers are missing or unknown.
- Default new managed workflows to `execution_performed=false`.
- Allow `execution_required=false` only for explicit plan-only/scaffold-only
  requests with rationale.
- Add shared terminal gate that blocks success when execution evidence is
  missing.
- Keep result vocabulary schema-compatible by using
  `final_status.result="blocked"` plus `stop_reason` and
  `residuals.blocking_remaining` unless a schema migration is separately
  approved.
- Do not let mode handlers set `execution_performed=true`; only trusted runner
  events with concrete evidence may do that.
- Update smoke tests for false-completion prevention.
- No agent integration yet.

### Phase 1: Minimal `/verify` Execution Evidence

- Support deterministic check records supplied by the current runner or a safe
  local adapter.
- Bind verdicts to target-specific evidence.
- Keep specialist agents optional and out of scope for the first pass.

### Phase 2: Minimal `/conv` Round Loop

- Record real round start, findings, arbitration, accepted changes, and stop
  proof.
- Support manual/main-runner applied changes first.
- Require follow-up round after material changes unless a narrow-change
  exception is explicitly recorded.

### Phase 3: `/goal` Child Workflow Creation

- Replace planned child ids with real child workflow creation.
- Define canonical scope hashing, attempt increment rules, and the idempotent
  parent-child creation sequence before enabling successful parent completion
  from child evidence.
- Add parent collection and blocker propagation.
- Parent goal final status depends on required child outcomes.

### Phase 4: Verification-Convergence Skill Parity

This phase is the minimum bar for replacing the retained
`verification-convergence` execution protocol. A Converge implementation that
does less than this must not be described as parity.

Required behavior:

- Build a dynamic panel of 3-5 specialist reviewers from artifact type, domain,
  risk level, required expertise, likely failure modes, and owner-stated
  priorities.
- Run deterministic checks before agent review whenever there is a relevant
  local check, file inspection, diff inspection, smoke command, log inspection,
  status command, or safe read-only external fetch.
- Give each reviewer a bounded context packet and require structured findings:
  `finding`, `severity`, `evidence`, `why_it_matters`,
  `minimal_fix_or_test`, `scope_risk`, and `confidence`.
- Collect findings without allowing agents to send visible messages, perform
  external actions, restart services, push, open PRs, mutate target artifacts,
  or mutate workflow state. Specialist agents are review-only by default.
- Deduplicate findings by failure mode, not wording.
- Arbitrate each finding as `block`, `fix`, `accept_risk`, `defer`, or
  `reject`.
- Convert accepted findings into concrete work items with `fix`, `evidence`,
  and `check` fields.
- Apply accepted fixes or improvements only through the coordinator or an
  explicitly designated fix runner, inside the current objective, non-goals, and
  approval boundaries.
- Preserve an explicit original-target lane in every round.
- Add a delta/regression lane after accepted changes and record whether the
  change affects shared behavior, state, contracts, security, data assumptions,
  approval boundaries, runtime/deployment paths, or the evidence standard.
- Require a follow-up round after material changes unless the change is narrow,
  directly checked, and recorded as creating no plausible new objective or
  regression risk.
- Stop only when the current round has enough evidence for `pass`,
  `pass_with_risks`, `needs_fix`, `stopped_max_rounds`, `stopped_owner`, or a
  blocked state.

Implementation slices:

- Phase 4A: accept deterministic check evidence and runner-provided structured
  reviewer findings. This proves the protocol before native agent spawning.
  Phase 4A is protocol-shape compatibility only, not retained-skill parity.
  Runner-provided findings must include profile metadata, independence or
  source-provenance markers, and evidence anchors; otherwise they are classified
  as manual evidence rather than specialist-panel evidence.
- Phase 4B: add native Converge specialist panel launch and recovery-safe result
  collection.
- Phase 4C: add fix-runner application for accepted changes, followed by
  original-target and delta/regression recheck.
- Phase 4 is retirement-ready only after 4A, 4B, and 4C acceptance tests pass.
  Phase 4A alone is a bootstrapping slice, not full parity.
- Phase 4 does not introduce an agent marketplace, scheduler, or reusable
  profile registry. Those belong to later phases only if repeated panels prove
  they are needed.

State requirements:

- `review_panel_spec`
- `deterministic_check_results`
- `agent_finding_refs`
- `raw_finding_to_group_map`
- `finding_arbitration`
- `accepted_change_refs`
- `original_target_gate`
- `delta_regression_gate`
- `follow_up_round_required`
- `max_rounds`
- `max_rounds_default=5` unless the user explicitly supplies a narrower or
  wider bound.
- `round_index`
- `stop_reason`
- `owner_stop_ref`
- `round_stop_proof`

Smoke and acceptance tests:

- A `/conv` run with accepted material changes cannot complete after one round
  without a follow-up-round waiver and direct check evidence.
- A `/verify` run cannot claim parity unless deterministic checks or reviewer
  findings are bound to the verdict.
- Runner-provided structured findings can be bound to a `/verify` verdict before
  native agent spawning exists.
- Agent findings with weak evidence can be rejected, but the rejection must be
  recorded with a reason.
- Every raw finding is classified exactly once into a dedupe group and
  arbitration decision; conflicting severities and duplicate wording do not
  drop minority findings.
- Specialist agents cannot create `accepted_change_refs`, report proof, or file
  mutations directly.
- Accepted fixes that touch shared behavior or approval boundaries force a
  follow-up round.
- A retained-skill parity fixture defaults to five rounds when no user-specified
  round limit is present.
- Max-round and owner-stop outcomes produce explicit stop proof.
- Specialist agents cannot create visible delivery proof or mark workflows
  reported; only Converge report-proof and complete-reported can do that.

### Phase 5: Converge-Native Orchestration Beyond Legacy Skill

This phase makes Converge stronger than the retained skill by combining the
legacy protocol's intelligence with Converge's durable workflow, recovery, and
report-proof model.

Do not implement Phase 5 without separate owner approval after Phase 0-4 parity
evidence passes. Phase 5 work without an approval record is rejected even if the
design is otherwise correct.

This PR records Phase 5/6 as an owner-requested source-local continuation of the
same execution-parity work. Future independent Phase 5 starts still require a
separate owner approval record before implementation.

Required behavior:

- Represent orchestration as a parent-child workflow graph, not as a flat chat
  transcript. Graph nodes and edges record `workflow_id`, `parent_id`, `role`,
  `required`, `state_root`, `owner_session`, `visible_delivery_policy`,
  `terminal_status`, `report_proof_ref`, and edge collection status. Graphs are
  acyclic and cannot mix state roots or owner sessions.
- Maintain an evidence map that links objective gates, deterministic checks,
  reviewer findings, accepted changes, child workflows, artifacts, and final
  stop proof. Evidence entries include `gate_id`, `evidence_kind`,
  `artifact_ref`, `artifact_hash_or_revision`, `round_id`,
  `produced_after_change_refs`, `valid_for_stop_status`, and
  `stale_if_change_refs`. For material changes, evidence entries also include
  `accepted_change_id`, `artifact_before_hash`, `artifact_after_hash`,
  `affected_gate_ids`, `invalidates_evidence_refs`, `stale_evidence_refs`, and
  `produced_by`.
- Make stop proof machine-checkable: a workflow cannot stop on evidence
  sufficiency unless the required evidence map entries are present and current.
  The `required_evidence_contract` is terminal-specific for `pass`,
  `pass_with_risks`, `needs_fix`, `stopped_max_rounds`, `stopped_owner`,
  `blocked_missing_executor`, and `blocked_approval_boundary`.
- Collect agent results through recovery-safe events. If the session is
  interrupted after agent launch or result collection, recovery must know which
  results were requested, received, ignored, accepted, or still pending.
  Agent requests record `request_id`, `profile_ref`, `context_hash`,
  `requested_at`, lease/status, `expected_result_count`, `result_ids`,
  `collection_cursor`, and terminal decision.
  Agent results record `result_id`, `request_id`, `profile_ref`, `attempt`,
  `context_hash`, `idempotency_key`, `received_at`, terminal status, evidence
  refs, and rejection or acceptance reason.
- Prevent duplicate visible reports by keeping visible delivery reservation,
  report-proof, and complete-reported as the only reported-state path.
- Define child delivery modes: `visible_child_report_required`,
  `parent_summary_only`, and `waived_with_owner_proof`.
  In `visible_child_report_required`, the child must reserve delivery, report,
  and complete-reported itself; the parent includes proof refs and must not
  duplicate the child report. In `parent_summary_only`, the child emits no
  direct user-visible message and the parent owns the visible proof and residual
  rollup. In `waived_with_owner_proof`, missing child visible proof is allowed
  only when a durable owner waiver event names the child, reason, and residual
  handling.
- Resume interrupted parent and child workflows from state root and checkpoint
  cursor without replaying risky side effects.
- Carry child residuals into the parent final report so blocked or accepted-risk
  child findings cannot disappear.
- Preserve owner/session/visible-delivery metadata across all children.
- Store reviewer, check, and runner profiles as reusable profile specs only
  after the evidence contract and recovery behavior are proven. Profile specs
  include `profile_id`, `version`, `kind`, `capabilities`, `artifact_types`,
  `risk_levels`, `required_context`, `prohibited_actions`, `output_schema`,
  `selection_reason`, and `context_hash`.

Implementation slices:

- Phase 5A: `required_evidence_contract`, `evidence_map`, evidence freshness,
  and stop-proof validation.
- Phase 5B: child residual rollup, child delivery modes, and duplicate-report
  guard.
- Phase 5C: recovery-safe agent request/result collection.
- Phase 5D: reusable reviewer/check/runner profiles, only after repeated panels
  show the duplication cost is real.

State requirements:

- `workflow_graph`
- `profile_registry_refs`
- `evidence_map`
- `evidence_freshness_status`
- `required_evidence_contract`
- `agent_request_refs`
- `agent_result_refs`
- `agent_result_idempotency_keys`
- `agent_result_collection_status`
- `child_residual_rollup`
- `child_delivery_mode_transitions`
- `duplicate_report_guard`
- `recovery_resume_cursor`

Smoke and acceptance tests:

- Phase 5 implementation is blocked unless a separate owner approval record
  exists after Phase 0-4 parity evidence.
- A parent workflow with unfinished required children cannot complete.
- Orphan, cyclic, cross-owner, and cross-state-root child graph entries are
  rejected.
- Evidence produced before a material accepted change becomes stale for affected
  stop gates until refreshed.
- Evidence freshness records accepted change ids, before/after artifact hashes,
  affected gates, and invalidated evidence refs.
- Evidence sufficiency is rejected when a required evidence-map entry is missing
  or stale.
- A recovered workflow does not re-launch already completed agent requests.
- A recovered workflow does not replay accepted fixes or external side effects.
- Agent result collection can resume from partial results.
- Late duplicate agent results are ignored or linked idempotently by
  `idempotency_key`, not counted twice.
- Recovery covers interruption after child creation, child terminal-unreported,
  child failed or accepted-risk rollup, and parent collected-before-report.
- Child delivery mode transitions are explicit: `visible_child_report_required`
  cannot silently become `parent_summary_only`, and either mode still rolls
  residuals into the parent.
- Child residuals appear in the parent final status.
- Duplicate visible report attempts are blocked.

### Phase 6: Production Route Parity

This phase proves exact `/goal`, `/verify`, and `/conv` use the execution-capable
Converge path end to end. It remains separate from legacy cleanup or deletion.

Required behavior:

- Exact `/goal`, `/verify`, and `/conv` route to Converge with owner session,
  visible delivery, state root, and recovery metadata preserved.
- Production parity tests must invoke exact slash commands through the installed
  or fresh route context. CLI-only or command-adapter-only evidence cannot prove
  route parity.
- Requests that are explicitly plan-only may complete as plan-only workflows.
- Requests that imply implementation, audit, repair, improvement, verification,
  convergence, or child execution cannot fall back to scaffold/synthetic
  completion.
- Visible start, round summary, final report, report-proof, and
  complete-reported are exercised in the real delivery channel.
- Visible delivery proof records target/session identity and content proof. A
  mismatched target/session, missing proof, or pre-proof `complete-reported`
  transition is blocked.
- Post-route smoke proves one route owner, no duplicate visible reports, no
  legacy handler replay, and no accidental `/converge` promotion.
- Rollback remains explicit, owner-approved, logged, scoped, and time-bounded.
- Gateway restart or config reload remains a separate approval-gated operation
  when needed; it is not implied by this phase.

Preconditions before any live route change:

- Explicit owner approval for route scope and timing.
- Exact route list, with `/converge` excluded from primary UX promotion.
- Retention decision for the legacy skill path.
- Rollback expiry, log path, and operator command documented.
- Pre-change smoke, fresh-session route smoke, and post-route smoke plan written
  before changing live routing.

Smoke and acceptance tests:

- Exact `/verify` on a concrete fixture target runs real file inspection,
  command output capture, or deterministic check evidence; records artifact hash
  or command evidence; and records visible final report proof.
- High-risk exact `/verify` or `/conv` exercises deterministic-check-first,
  3-5 reviewer panel, structured findings, dedupe, arbitration, and final
  report proof through the route layer.
- Exact `/conv` on a target requiring a material plan/doc change applies or
  records a real accepted artifact change, then runs a second round that checks
  original-target and delta/regression lanes. One-round completion is allowed
  only with a recorded narrow-change waiver and direct check evidence.
- Exact `/goal` that requires implementation creates or collects real child
  workflow evidence before parent completion.
- Exact `/goal` that is explicitly plan-only can complete without child
  execution, but must mark the plan-only capability clearly.
- Golden parity fixtures for audit, repair, and improve modes map every retained
  skill requirement to Converge events, state fields, and visible message output.
- Synthetic/scaffold reports are blocked for execution-required requests.
- `reserve-delivery`, `report-proof`, and `complete-reported` form the only
  successful visible completion path.
- Live route parity tests prove legacy skill parity or better before the legacy
  execution path is retired.

## Non-Goals

- No code change, commit, PR, deploy, Gateway restart, cron registration, or live
  route replacement from this planning request.
- No generic distributed workflow engine.
- No automatic external actions.
- No automatic PR, release, or deployment.
- No legacy data deletion, movement, archive, or skill removal.
- No promotion of `/converge` as a primary UX.
- No agent marketplace or broad plugin abstraction.
- No automatic replay of side effects during recovery.

## Open Decisions

- Which runner records the first real execution evidence: the current main
  OpenClaw session, a local Converge adapter, or a later ACP/OpenClaw session
  adapter?
- Before Phase 1 implementation, choose one runner path and add a fixture
  proving only that trusted runner can set `execution_performed=true`. This is
  not optional for execution-capable verdicts.
- Should `execution_required` be inferred only from command text, or can callers
  override it explicitly for test and plan-only flows?
- What is the minimum acceptable deterministic check interface for `/verify`
  before agent integration exists?
- How should child visible reports be summarized in the parent report without
  duplicating user-visible messages?

## Recommended First Implementation Target

Start with Phase 0 only:

- Add truth markers.
- Add the shared scaffold-only completion blocker.
- Add smoke tests proving that execution-required `/goal`, `/verify`, and
  `/conv` cannot complete from scaffold-only data.

This gives immediate safety without overengineering. It also makes every later
phase measurable: each new executor earns the right to set
`execution_performed=true` only when it records concrete evidence.

Phase 5 needs a separate approval checkpoint before implementation. It is the
first phase that intentionally goes beyond retained skill parity into durable
native orchestration, so it must not delay Phase 0-4 false-completion safety.
