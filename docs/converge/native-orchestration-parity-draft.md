# Native Orchestration Parity SOT

Status: Active source of truth for native orchestration parity; Phase A+B,
Phase C.1, Phase C.2, Phase C.3 session-store proof, Phase C.4 redacted
trajectory tool-event proof, Phase D native `/conv` panel/fix-runner/follow-up
proof, and Phase E.1 parent `/goal` native child evidence collection exist.
Remaining work is Phase F live route proof, which requires explicit owner
approval for install/apply, Gateway restart, and live route smoke.
Date: 2026-05-29
Scope: Converge `/goal`, `/verify`, and `/conv`

## Purpose

This document is the active source of truth for current Converge native
orchestration parity work. It fixes the target after the Phase 0-6
execution-parity work exposed an important gap: Converge now has much stronger
execution truth, evidence, recovery, and report-proof gates, but it is not yet
equivalent to the old operator-led `verification-convergence` experience or
Codex CLI-style goal orchestration.

The next target is not more scaffold parity. The target is native orchestration:
actual specialist launch, bounded result collection, arbitration, fix or improve
rounds when allowed, and parent `/goal` completion only after child execution
evidence exists.

Related documents:

- `docs/converge/execution-parity-plan.md` is foundation and history for the
  completed execution-parity substrate. It is not the implementation plan for
  native orchestration.
- `docs/converge/c7-live-route-ownership-patch-package.md` is a live-ops gate
  and ownership package. It is not the implementation plan for this native
  adapter work.

## Current Code State

What currently exists:

- `/goal` promotes a durable accepted goal plan and can create deterministic
  child `/verify` and `/conv` workflows through the same state root.
- `/goal` validates parent-child links, terminal child status, child residual
  rollup, workflow graph shape, evidence freshness, delivery mode, duplicate
  report guards, and parent completion gates.
- `/verify` blocks scaffold-only success by default and can set
  `execution_performed=true` only when trusted deterministic local file
  inspection records concrete file evidence.
- `/conv` blocks synthetic-only success by default and can set
  `execution_performed=true` only when trusted local round evidence inspects
  concrete file targets. Material repair or improve intent is deliberately not
  satisfied by file inspection alone.
- `/verify` and `/conv` can ingest runner-provided structured specialist
  findings through `--structured-findings-file`. These packets are validated,
  deduped, arbitrated, recorded as artifacts/events, and guarded against
  forbidden side effects.
- Converge has useful state substrate: execution truth markers, evidence
  contracts, evidence maps, agent request/result refs, idempotency keys,
  profile refs, workflow graph validation, recovery cursors, report-proof, and
  complete-reported gates.

What exists in the current Phase C implementation:

- `/verify` has an explicit opt-in OpenClaw CLI native panel path.
- `/conv` has the same explicit opt-in OpenClaw CLI native panel path for a
  single bounded specialist round.
- Each `/verify` native CLI child uses an explicit `agent:main:converge-<workflow>`
  style `session_key`.
- Native CLI child output is accepted only when the child reports passed
  tool-smoke evidence bound to that exact explicit session ref.
- Phase C.3 additionally requires OpenClaw session-store proof for the same
  exact child `session_key` before a CLI child result can satisfy
  `native_agent_panel`.
- Phase C.4 additionally requires a redacted `openclaw sessions
  export-trajectory` bundle for the same exact child `session_key`, with at
  least one `tool.call` and one `tool.result` event, before a CLI child result
  can satisfy `native_agent_panel`.
- Phase D.1 reuses the same session-store plus trajectory proof for `/conv`
  native panel results.
- Phase D records accepted `/conv` changes as coordinator-owned `fix_runner`
  requests, accepts bounded local fix-runner results, and requires follow-up
  original-target plus delta/regression evidence after material changes.
- Phase E.1 lets parent `/goal` pass the same opt-in native panel backend to
  child `/verify` and `/conv` workflows, then stores parent child refs with
  child-derived native session/request/tool-smoke proof.
- This proof shows OpenClaw persisted the explicit child session and exported
  redacted transcript-level tool events. It is not overclaimed as proof that
  every child-reported tool action happened or that raw tool output content was
  independently audited.

What does not yet exist:

- `agent_request_refs` and `agent_result_refs` currently model validated
  runner-provided packets or the current `/verify` native CLI path depending on
  source. Runner packets still do not prove that Converge launched agents.
- No independent proof verifies every claimed child tool call. Phase C.4 proves
  exact session-store presence plus redacted trajectory `tool.call` /
  `tool.result` existence for the child session.
- Live exact `/goal`, `/verify`, and `/conv` route proof has not been rerun for
  the new D.3/E.1 native evidence path.
- Phase F install/apply, Gateway restart, and live route smoke remain
  approval-gated operational work.

## Target Definition

Converge is parity-ready only when exact `/goal`, `/verify`, and `/conv` can
prove actual execution through machine-checkable state and user-visible reports.

Minimum target:

- The first native orchestration backend is fixed as an OpenClaw
  session/subagent adapter using `sessions_spawn`-style child sessions.
- Codex native `spawn_agent` is a future backend and reference model only, not
  the first product backend.
- `/verify` launches and collects a real panel of independent specialist
  reviewers when risk or user intent requires specialist review. The default
  panel is 3 specialists; use 5 only for high-risk targets or explicit owner
  request.
- Specialist review is not the default for every request. It is required only
  for high-risk targets, explicit owner request, material ambiguity, or
  execution-required work where deterministic local checks are insufficient.
- `/conv` launches the same kind of panel, arbitrates findings, applies accepted
  local fixes or improvements when authorized, and runs one follow-up round only
  after material changes.
- `/goal` uses Codex CLI-like goal behavior: draft and confirmation first; then
  execution-required goals create, run, wait for, and collect required child
  `/verify` and `/conv` workflows before parent success.
- Specialist agents cannot send visible messages, mutate workflow state, restart
  services, push, open PRs, deploy, or perform external actions.
- The coordinator owns arbitration and final decisions. Agents provide evidence,
  not commands.
- No success result is allowed without execution evidence, report proof, and
  completion proof appropriate to the mode.
- Each phase below must be implemented as an implementation -> improvement ->
  convergence loop, not as one direct patch. The first pass establishes the
  working capability, the improvement pass tightens correctness and ergonomics,
  and the convergence pass resolves remaining findings with proof.

Phase acceptance must be machine-checkable. Required proof includes workflow
state, child session refs or explicit local-check refs, structured findings,
idempotency state, tests, and a visible completion report. Native orchestration
phases must not be declared complete from documentation, scaffolding, planned
child refs, or runner-provided packets alone.

Token budget policy:

- Default to a 3-specialist panel and 1 round.
- Run one follow-up round only after a material change.
- Use isolated/light context by default; fork only when needed.
- Keep child output compact and structured.
- Avoid rereading full histories.
- Do not repeat `task_hash`/`context_hash`/`profile_id` executions unless
  invalidated.

## Non-Goals

- Do not build a broad agent marketplace or scheduler before native launch works.
- Do not generalize profile registries further until repeated real panels prove
  the duplication cost.
- Do not promote `/converge` as a primary command.
- Do not remove retained legacy skill/history as part of this slice.
- Do not mix watchdog/install documentation dirt into the native orchestration
  implementation PR.
- Do not keep compatibility fallback paths in runtime native orchestration.
  Internal development can replace incomplete native paths directly instead of
  preserving packet-only or fake-runtime substitutes.
- Do not allow specialists to perform external, destructive, deployment,
  Gateway, PR, release, or public-message actions.
- Do not call runner-provided packets native agent execution.

## Boundary Corrections

The current code should be described precisely:

- `runner_provided_packet`: structured findings supplied by an external trusted
  runner or test fixture and validated by Converge.
- `local_checks`: deterministic local file inspection or local round evidence
  performed by the current Converge helper.
- `native_agent_panel`: Converge-created specialist sessions with durable
  request ids, result ids, leases, timeout handling, and recovery-safe result
  collection.
- `fix_runner`: a bounded local mutation runner owned by Converge or the main
  coordinator, never by reviewer agents.

User-facing reports must not present `runner_provided_packet` as native agent
spawn. If existing fields such as `execution_capability="delegated_agents"`
remain for compatibility, add an explicit source field such as
`execution_source="runner_provided_packet"` until native launch exists.

## Native Adapter Contract

The first adapter implementation is `openclaw_session`. The contract should be
small enough to verify in Phase A+B before any mode depends on it.

Launch input:

- `mode`: `verify`, `conv`, or a later explicitly supported mode.
- `objective`: immutable task objective and acceptance boundary.
- `target_refs`: files, artifacts, workflow ids, or compact context packets.
- `profile_ref`: reviewer role/persona and allowed tool scope.
- `context_hash`: hash over objective, target refs, profile, and schema.
- `idempotency_key`: stable `task_hash/context_hash/profile_id` key.
- `output_schema`: structured finding schema expected from the child session.
- `tool_policy`: explicit read-only or mutation-forbidden child restrictions.
- `session_key`: explicit child session key returned by the OpenClaw session
  backend.
- `timeout_policy`: per-child lease, collection deadline, and cancellation rule.
- `budget_policy`: per-child token budget, maximum input/context size, maximum
  findings size, and truncation or summarization behavior.

Session identity and relay safety:

- The adapter must never depend on implicit session aliases such as `current`.
- Every launch, wait, status check, result collection, recovery attach, and
  completion report must use an explicit `session_key` or durable child
  session ref.
- Before a child is accepted as a native specialist, the adapter must run a
  minimal tool-smoke against that explicit session: at least one allowed file or
  artifact read and one harmless shell/status command when shell is in scope.
- Tool-smoke success must be recorded as child execution evidence with the
  session ref, command/read kind, timestamp, and result status.
- If tool-smoke fails, the native panel is blocked. Converge may still record
  packet-only or prompt-only review as degraded advisory evidence for explicit
  diagnostics, but it must not classify that result as `native_agent_panel`, use
  it to satisfy native execution parity, or silently fall back to success.
- Reports must distinguish actual file/tool inspection by a child session from
  review performed from pasted summaries, preloaded context, or
  runner-provided packets.

Child restrictions:

- Child sessions may inspect, reason, and return structured findings.
- Child sessions must not send visible messages, mutate workflow state, edit
  target files, restart Gateway, deploy, push, open PRs, release, or perform
  external actions.
- Restrictions must be enforced through `tool_policy`, not only documented.
- Mutation, if allowed by the user request, belongs to the coordinator-owned
  `fix_runner`, not reviewer agents.

Result schema:

- `request_id`
- `agent_session_ref`
- `session_key`
- `tool_smoke_status`: `passed`, `failed`, `not_applicable`, or `not_run`
- `profile_ref`
- `context_hash`
- `status`: `completed`, `failed`, `timed_out`, or `cancelled`
- `findings`: structured findings with severity, evidence refs, rationale, and
  recommended disposition
- `started_at`, `deadline_at`, and `completed_at`
- `error` or `timeout_reason` when status is not `completed`

Recovery and duplicate prevention:

- A matching completed `idempotency_key` must be reused, not relaunched.
- A matching active `idempotency_key` attaches to the existing lease instead of
  launching a duplicate child.
- Requested and pending children must have leases that recovery can inspect.
- Stale active leases expire through timeout policy and terminal timeout before
  any replacement launch.
- Timed-out or cancelled children must be terminal and visible to the parent
  collector.
- Late results from `timed_out` or `cancelled` children are recorded as late and
  cannot overwrite terminal coordinator state.
- Partial panel failure must degrade verdicts explicitly. A required native
  panel cannot silently pass from two successful children when the policy
  requires three collected results.
- Default required panel success needs all requested profiles completed.
  `failed`, `timed_out`, or `cancelled` blocks success unless the owner
  explicitly accepts degraded evidence.

Default policies:

- Default panel size is 3, default max rounds is 1, and default context mode is
  isolated/light.
- High-risk or explicit owner-requested panels may use 5 specialists.
- Timeout policy is one lease per child plus one panel collection deadline.
  Exact durations are configuration, but behavior is fixed and contract-tested.
- Budget policy rejects oversized context before launch and
  truncates/summarizes oversized findings before parent collection.
- Budget handling must preserve result schema validity and mark any context
  rejection, finding truncation, or summarization explicitly in result metadata.
  Truncation must not silently remove failure, timeout, cancellation, or source
  classification signals.
- A material change in `/conv` authorizes at most one follow-up round by
  default, producing a maximum of 2 rounds unless the owner explicitly asks for
  more.
  `max_rounds=1` means at most one authorized fix/improve cycle. If material
  changes are made, `/conv` must run one mandatory follow-up review round that
  checks original findings plus delta/regression evidence before completion.

## Implementation Plan

Each phase must run as an implementation -> improvement -> convergence loop and
must include the machine-checkable acceptance proof listed in Target Definition.
Phase C cannot begin until Phase A+B adapter contract tests pass, because native
launch depends on request creation, lifecycle state, idempotency, timeout,
result validation, and source classification.

### Phase C.2/C.3 Entry Boundary

Phase C.2 and C.3 are deliberately narrow `/verify` slices:

- Wire one minimum native `/verify` vertical slice before touching `/conv` or
  `/goal`.
- Use explicit `session_key` launch/wait/collect only. No implicit `current`
  session alias is allowed.
- Coordinator-verified tool-smoke is required before any child output can set
  `satisfies_native_agent_panel=true`.
- The in-memory backend remains contract-test infrastructure only and must not
  be used as runtime fallback.
- Runner-provided packets remain advisory for native parity and must not upgrade
  to `native_agent_panel`.
- The CLI command-shape seam remains non-parity until the coordinator verifies
  child tool-smoke against the explicit child session, proves that exact session
  key exists in the OpenClaw session store, and proves a redacted trajectory
  export contains at least one `tool.call` and `tool.result`.
- Phase C.4 trajectory proof is required for native CLI panel success. This is
  transcript-level tool-event presence proof, not a complete audit of every
  claimed child action or raw tool output.
- If native launch, wait, collect, or tool-smoke is unavailable, the mode should
  fail/block with an explicit reason rather than silently using packet-only or
  fake evidence.

Phase C.2/C.3 is complete only when a focused smoke proves requested child
session refs, passed coordinator-bound tool-smoke evidence, exact OpenClaw
session-store proof, collected structured findings, and blocked behavior when
any of those proofs are missing.

### Phase A+B: Adapter Foundation

- Add a narrow adapter boundary under `converge/agents/` with
  `backend=openclaw_session` as the first implementation.
- Mark Codex native `spawn_agent` as `codex_native` future backend/reference
  only, not a product backend for this phase.
- Add explicit execution source classification:
  `local_checks`, `runner_provided_packet`, `native_agent_panel`, `fix_runner`,
  `child_workflows`, or `plan_only`.
- Replace ambiguous compatibility behavior in native paths. If an older field
  still exists in state, reports and validators must bind to explicit
  `execution_source` and reject packet-only evidence for native success.
- Clean up misleading source names and test descriptions that imply native agent
  launch when they only validate structured packet ingestion.
- Adapter inputs:
  - mode: `verify` or `conv`
  - target and objective lock
  - artifact paths or context packet
  - reviewer profiles
  - approval boundaries
  - output schema
- Adapter outputs:
  - `request_id`
  - `profile_ref`
  - `context_hash`
  - `agent_session_ref`
  - `result_id`
  - structured findings
  - terminal status: completed, failed, timed_out, cancelled
- Persist request/result refs, context hashes, idempotency keys, timeout state,
  and collection status.
- Do not fall back to fake native results or packet-only native success.

Acceptance:

- Adapter contract tests prove request creation, idempotency keys, lifecycle
  states, timeout/cancellation handling, result validation, and source
  classification.
- Adapter contract tests prove no lifecycle path resolves child sessions through
  the implicit `current` alias. All launch, wait, collect, and recovery paths
  use explicit `session_key` or durable child session refs.
- Adapter contract tests prove child tool-smoke is required before a result can
  satisfy `native_agent_panel`, and failed tool-smoke produces a blocked or
  degraded-advisory outcome rather than a silent packet-only pass.
- Until exact timeout and budget values are decided, Phase A+B acceptance tests
  assert configured policy presence and behavior hooks. Numeric threshold tests
  become required before Phase C live native launch is enabled.
- Source classification distinguishes execution source from backend: e.g.
  `execution_source=runner_provided_packet` versus
  `execution_source=native_agent_panel` with `backend=openclaw_session`;
  `codex_native_reference` remains advisory only, and `local_checks` stays a
  separate deterministic execution source.
- Recovery can see requested, pending, completed, failed, and timed-out agent
  requests.
- Re-running recovery does not relaunch completed requests.
- A packet-only `/verify` or `/conv` can pass packet-validation tests, but cannot
  satisfy `native_agent_panel` parity fixtures.
- User-facing summaries expose the source accurately.
- User-facing summaries distinguish child-session file/tool inspection from
  review based only on pasted summaries, preloaded context, or
  runner-provided packets.
- Real OpenClaw child session launch is not required to complete Phase A+B; that
  proof belongs to Phase C.

### Phase C: Native `/verify` Panel Execution

- Preserve deterministic-check-first behavior.
- If specialist review is required, launch real OpenClaw child sessions and
  collect results.
- Default to 3 specialists; use 5 only for high-risk targets or explicit owner
  request.
- Use isolated/light context by default.
- Validate every finding using the existing structured specialist schema.
- Require structured JSON findings.
- Deduplicate and arbitrate all findings.
- Verdicts must bind to deterministic evidence and/or native panel evidence.

Acceptance:

- High-risk `/verify` smoke proves deterministic check evidence plus collected
  native specialist results.
- Acceptance also covers default-risk native `/verify` launching exactly 3
  requested specialist profiles, and high-risk or owner-requested `/verify`
  launching exactly 5.
- Each launched child has explicit `session_key` evidence and passed tool-smoke
  before its findings count toward the native panel.
- Weak or unsupported findings are rejected with reasons.
- Success is blocked without collected child findings when a native panel is
  required.

### Phase D: Native `/conv` Round Runner

- Use the same native panel adapter for convergence rounds. D.1 covers this for
  one bounded read-only specialist round.
- Add round state for original-target lane and delta/regression lane.
- Add arbitration and a coordinator-owned fix/improve runner.
- Apply accepted fixes or improvements only through the coordinator or
  fix/improve runner.
- Require a follow-up round after material changes.
- Default to 1 round when no material change occurs. Material changes permit one
  follow-up round, for a default maximum of 2 rounds unless the owner explicitly
  asks for more.

Acceptance:

- `/conv` with material accepted changes cannot complete after one round.
- Round 2 checks both original target and delta/regression lanes.
- Reviewer agents never mutate target files or workflow state directly.
- Round evidence separates native child file/tool inspection from degraded
  advisory review that used summaries or runner-provided packets.

### Phase E: `/goal` Child Orchestration

- Keep draft/intake/explicit confirmation semantics.
- For execution-required goals, create, run, wait for, and collect child
  `/verify` and `/conv` workflows.
- Parent waits for terminal child evidence and visible-report requirements.
- Parent propagates child blockers, accepted risks, residuals, and evidence refs.
- Parent cannot complete reported while required children are running,
  failed-unreported, terminal-unreported, blocked, or missing proof.

Acceptance:

- A local or integration `/goal` smoke shows child workflow ids, child
  report/evidence refs, and parent collection proof.
- Minimum child execution evidence for parent success includes
  `agent_session_ref`, explicit `session_key`, `request_id`, `profile_ref`,
  passed tool-smoke, terminal child status, structured findings, timestamps,
  and parent collection proof.
- A plan-only `/goal` remains allowed and clearly marked `plan_only`.
- An execution-required `/goal` cannot pass with only planned child references,
  documentation, scaffolding, runner-provided packets, or packet-only
  pseudo-agent evidence.
- Parent success requires child execution evidence.

### Phase F: Live Route Proof

Phase F is operational live proof, not a prerequisite for adapter/core parity.

- Install the implementation into the development runtime.
- Run Gateway restart only after preflight and explicit owner approval.
- Smoke exact `/goal`, `/verify`, and `/conv` through the live Telegram route.
- Prove visible start, round summary, final report, report-proof, and
  complete-reported in the real delivery path.
- Live smoke must be harmless and minimal. It proves routing and evidence
  delivery, not production cleanup, legacy deletion, deployment automation, or
  release.

Acceptance:

- Live `/verify` and `/conv` include actual native agent/session refs.
- Live `/goal` includes actual child workflow evidence.
- No duplicate visible reports.
- Legacy retained skill is not used for exact `/verify` or `/conv`.

## Future Extension: Watchdog Notification And Safe Resume

This is a post-parity operational extension. It must not be mixed into the
native execution proof slices above.

Core concept:

- Converge workflow state remains the source of truth for `/goal`, `/verify`,
  and `/conv` work.
- A dedicated heartbeat runner periodically calls `converge watchdog-check`
  against the real state root and records deterministic heartbeat metadata.
- A notifier layer may turn changed `needs_wake=true` recovery packets into
  user-visible alerts. Notification is separate from recovery and must not
  imply that work was resumed.
- A resumer layer may acquire a Converge recovery lease, inspect
  `next_safe_action`, and continue only resume-safe work from the stored
  workflow id, cursor, owner session, and visible-delivery route.
- Risky actions such as external delivery beyond the recovery notice, Gateway
  restart, deploy, PR/release, destructive changes, or public/financial actions
  remain approval-gated and must not be auto-resumed.

Implementation intent:

- First ship log-only heartbeat observation.
- Then add notification-only behavior and verify false positives/duplicates.
- Only after notification behavior is stable, evaluate a bounded auto-resume
  runner for read-only or clearly idempotent `next_safe_action` cases.
- Auto-resume must be recovery-lease based, idempotent, and visible-report
  backed. It must not rely on best-effort subagent completion events as the
  sole continuation path.
- The final design should be reviewed by multiple agents or independent
  reviewers. Use that review to compare alternatives, find the simplest safe
  architecture, and reject overengineered scheduler/platform designs.

Acceptance for this future extension:

- Heartbeat detects stale active workflows, waiting-user reminders, and
  terminal-unreported workflows without sending duplicate reports.
- Notification includes workflow id, reason, owner route, and next safe action
  summary, but performs no mutation beyond the notification proof.
- Auto-resume, if enabled, is limited to explicitly safe cases and records the
  recovery lease, resumed command, final visible report, report-proof, and
  residual risks.
- Multi-agent review explicitly assesses safety, false positives, duplicate
  reporting, idempotency, approval boundaries, and implementation complexity.

## Current Work Classification

Keep:

- execution truth gate
- deterministic local check evidence
- child workflow creation and parent collection validation
- evidence contracts and freshness
- report-proof and complete-reported gates
- recovery cursors and idempotency checks
- structured specialist packet validation
- workflow graph and child residual rollup

Reduce or clarify:

- user-facing `delegated_agents` wording before native spawn exists
- profile registry expansion before real launch
- route parity claims that do not prove native execution
- tests named as if they prove native agent launch

Defer:

- broad agent marketplace
- generalized scheduler
- automatic PR/deploy/release/Gateway actions

Remove or reject:

- any synthetic native-agent result path
- any success path that lacks concrete execution evidence
- any report wording that equates runner-provided packets with actual spawned
  specialists
- any runtime fallback from native child session failure to in-memory, fake,
  prompt-only, or runner-packet success

## Fixed Decisions

- First backend is OpenClaw session/subagent adapter using `sessions_spawn`-style
  child sessions.
- Codex native `spawn_agent` is a future backend/reference model only.
- Phase work must proceed as implementation -> improvement -> convergence loops
  with machine-checkable acceptance proof before moving to the next phase.
- Timeout, lease, partial-failure, retry, and budget behavior is fixed by the
  Native Adapter Contract. Only exact duration and size values remain
  configuration choices.
- Controlled `fix_runner` starts after Phase C proves native `/verify` panel
  launch/collection and belongs to Phase D before material `/conv` changes, not
  after Phase F.

## Open Decisions

- What exact timeout durations and token/size limits should be used for default
  3-specialist panels and explicit high-risk 5-specialist panels?

## Immediate Next Step

Phase A+B now has a narrow source foundation:

- `converge/agents/contracts.py` defines the OpenClaw session launch/result
  contract, explicit source classifications, default `tool_policy`,
  `timeout_policy`, `budget_policy`, native result validation, and the
  coordinator-owned `fix_runner` boundary.
- `converge/agents/classifier.py` provides the first small risk/intent
  classifier for panel requirement and 3-vs-5 specialist defaults.
- Runner-provided specialist packets now carry
  `execution_source="runner_provided_packet"`,
  `satisfies_native_agent_panel=false`, explicit mutation-forbidden
  `tool_policy`, default timeout/budget policies, and `tool_smoke_status` of
  `not_applicable` instead of inventing session refs.
- `tests/smoke/converge_agent_contracts_smoke.py` is the executable adapter
  contract smoke. Existing verify/conv structured-packet smokes assert that
  packet ingestion remains advisory for native parity.

Current Phase C.4 status: `converge/agents/openclaw_cli.py` has a narrow
`/verify` native CLI panel path. It proves explicit live-safe
`agent:<id>:<key>` session keys, prompt shape, structured result parsing,
child-reported passed tool-smoke binding, and exact OpenClaw session-store
presence plus redacted trajectory `tool.call` / `tool.result` events for the
same child key before `native_agent_panel` success is allowed.

Current Phase D/E.1 status: `/conv` now records bounded fix-runner requests,
accepts completed local fix-runner results, and requires a follow-up evidence
round after material changes. `/goal` now propagates the opt-in native panel
backend to child `/verify` and `/conv` workflows and validates parent
`native_agent_panel_proof` by recomputing it from the child workflow state.

This is still source-local proof. The honest next step is Phase F live route
proof after explicit owner approval for install/apply, Gateway restart, and
harmless exact `/goal`, `/verify`, and `/conv` route smokes.
