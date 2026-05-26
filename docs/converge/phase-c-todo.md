# Converge Phase C Todo

This is the current execution checklist for the next `/goal` work.
`docs/converge/implementation-structure.md` remains the general Phase C design
detail source. C7-specific planning is headed by
`docs/converge/c7-canonical-command-replacement.md`.

Current implementation baseline: C7.0 command inventory and synthetic dry-run
adapter is complete on top of C6 Install Wiring. The local implementation now
preserves the C0-C2.5 shared mode and terminal finalization contracts, the C3
iterative mode invariants, the C4 durable accepted-plan slice queue, the C4.5
shared smoke helper boundary, the C5 recovery commands, the C6 local
install/watchdog runner wiring, and the C7.0 route-free command dry-run
adapter. The next open phase is C7.1 Converge command adapter hardening.

Deferred non-blocking cleanup items are tracked in
`docs/converge/p3-debt-register.md`. New P3 findings from future convergence
runs should be added there instead of being left only in chat or ledger logs.

## Completed Preparation

- [x] Phase A: `final_status` object-only contract cleanup.
- [x] Phase B: `reserve-delivery` duplicate payload/helper consolidation and reporting state-machine cleanup.
- [x] Phase C concept convergence: shared primitives first, then small vertical mode slices.

## Current Phase C Order

- [x] C0 / Slice 4: Shared Mode Contract Hardening.
- [x] C1 / Slice 5: `plan`.
- [x] C2 / Slice 6: `verify`.
- [x] C2.5 / Slice 6.5: Terminal Finalization Invariants.
- [x] C3 / Slice 7: `conv`.
- [x] C4 / Slice 8: `goal`.
- [x] C4.5 / Slice 8.5: Smoke Helper Consolidation.
- [x] C5 / Slice 9: Recovery.
- [x] C6 / Slice 10: Install Wiring.
- [ ] C7 / Slice 11: Canonical Command Replacement + Legacy Retirement.
  - [x] C7.0: Entrypoint inventory + synthetic dry-run adapter.
  - [ ] C7.1: Converge command adapter hardening.

## Next Goal Command

```text
/goal Converge Phase C7.1: command adapter hardening을 진행해줘. C7의 목표는 기존 /goal, /verify, /conv의 canonical backend를 Converge로 교체하고 GoalFlow/Ledger/legacy verify-conv 경로를 retirement 대상으로 전환하는 것이야. 이번 C7.1은 live-route 변경 없이 C7.0 synthetic dry-run packet을 더 명확한 adapter contract로 강화하고, /goal draft/confirmation metadata, /verify audit intent, /conv round metadata, state-root/delivery/rollback fields를 검증 가능한 packet fields로 고정하는 데 한정해줘. 새 artifact 저장소나 라우팅 계층을 만들지 말고 기존 `command-dry-run` 경계를 작게 보강해줘. Gateway restart, live traffic observation, shadow routing, live slash routing replacement, deploy/apply/install, legacy data deletion, external action, push/PR/release는 제외해줘.
```

## C0 Completed Scope

- Replaced the temporary Slice 1-9 default continuation plan with a
  mode-neutral initialization contract for modes that need durable
  mode state, especially `goal` and `conv`.
- Routed mode-state mutations through one shared checkpoint/advance path under
  the workflow lock.
- Added a shared artifact registration helper usable by both CLI code and mode
  handlers.
- Kept terminal visible delivery authority in `reserve-delivery`.
- Added minimal validation for continuation plan shape, advance gate boundaries,
  evidence artifact references, and local-file-only context manifest updates.

## C0 Preserved Non-Goals

- No `plan`, `verify`, `conv`, or `goal` intelligence.
- No slash-command or Ledger adapter migration.
- No full recovery/watchdog implementation.
- No broad mode framework, plugin runtime, or subagent orchestration layer.
- No compatibility layer for temporary internal Slice 1-9 defaults.

## C0 Completion Checks

- No mode writes its own direct workflow mutation path for state C0 owns.
- No terminal report path bypasses `reserve-delivery`.
- New shared helpers have focused smoke coverage.
- Documentation still points to C0 before C1 mode behavior.

## C1 Completed Scope

- Implemented `plan` as the first real mode behavior slice on top of the C0
  shared contracts.
- Produces a durable `artifacts/plan.md` final plan artifact through shared
  artifact registration.
- Updates `plan_state` through `ModeOutcome` and the shared checkpoint path.
- Finishes as `completed_unreported`; visible delivery remains gated by
  `reserve-delivery`, `report-proof`, and `complete-reported`.
- Keeps `plan` short-workflow oriented with no recovery/watchdog, install
  wiring, slash adapter, or broad mode framework work.

## C2 Completed Scope

- Implemented `verify` as the second short mode behavior slice on the same
  shared contracts.
- Produces a durable `artifacts/verify-report.md` final report artifact through
  shared artifact registration.
- Updates `verify_state` through `ModeOutcome` with target, check plan,
  deterministic checks, verdict, evidence, and categorized residuals.
- Finishes as `completed_unreported`; visible delivery remains gated by
  `reserve-delivery`, `report-proof`, and `complete-reported`.
- Keeps C2 limited to evidence verdict records and final report formatting:
  no adapter migration, Gateway actions, recovery daemon work, real specialist
  orchestration, or broad mode framework work.

## C2.5 Completed Scope

- Documented terminal finalization invariants that every terminal mode must pass
  before C3/C4 add more complex state.
- Kept `reserve-delivery` as the only send-authority gate. It must validate
  terminal material before visible delivery: terminal checkpoint, `final_status`,
  mode state update, registered report/plan artifact path, hash, and rendered
  content must agree.
- Confirmed `reserve-delivery` is not a fallback terminalization path for active
  `goal`, `conv`, or future modes. Active workflows must first create their
  terminal checkpoint through the mode/checkpoint path, then reserve visible
  delivery against that checkpoint and the workflow's original delivery route.
- Kept workflow `verification.evidence` bound to the ordered checkpoint-backed
  evidence sequence, ending with terminal checkpoint evidence. C2.5 must not
  regress to a short-mode-only `[terminal_evidence]` comparison, because future
  modes may have valid preterminal checkpoint evidence.
- Kept `report-proof` and `complete-reported` as post-send proof/reporting
  steps. They must validate reservation, checkpoint, delivery event, proof
  identity, and idempotency, but must not require live report artifacts to still
  exist after send authority was already granted.
- Extracted small terminal validation helpers to make this boundary obvious
  without introducing a broad terminal framework.
- Added a reusable smoke-test pattern for terminal modes covering final-status
  mismatch, mode-state/checkpoint drift, checkpoint-backed evidence sequence
  drift, uncheckpointed extra evidence, stale or missing material before
  `reserve-delivery`, proof after artifact deletion, duplicate proof, and wrong
  reservation/checkpoint identity.
- Updated docs so C3/C4 implement only mode-specific behavior and reuse these
  terminal finalization rules rather than copying `plan`/`verify` patches.

## C2.5 Preserved Non-Goals

- No `conv` or `goal` behavior.
- No slash-command or Ledger adapter migration.
- No Gateway restart, install wiring, deploy, external action, push, PR, or
  release.
- No broad terminal framework. Only small helpers and smoke templates that
  prevent duplicate write paths and clarify existing reporting boundaries.

## C3 Completed Scope

- Implemented `conv` as the first iterative mode behavior slice on the same
  shared contracts.
- Records bounded round metadata through mode-state checkpoint outcomes, not
  through `append-round`.
- Classifies findings through original-target gate, delta gate, novelty,
  severity, objective impact, evidence quality, disposition, and material-change
  flags.
- Requires material accepted changes to have a follow-up round or explicit stop
  proof.
- Supports both evidence-sufficiency stop and max-round stop.
- Reuses C2.5 terminal checkpoint, final_status, mode_state exact-match,
  checkpoint-backed evidence, artifact, reserve-delivery, report-proof, and
  complete-reported invariants.
- Keeps C3 limited to local runtime behavior: no real specialist orchestration,
  new adapter, recovery daemon, install wiring, slash routing, Gateway restart,
  external action, push, PR, or release.

## C4 Completed Scope

- Implemented `goal` as the durable accepted-plan slice queue behavior on the
  same shared contracts.
- Gates execution through explicit objective, non-goals, success criteria,
  assumptions, and approval boundaries.
- Represents goal slices in `continuation_plan.steps` and mirrors them into
  `goal_state.slice_queue`.
- Validates a scoped `plan_accepted` payload before terminal success.
- Requires evidence completion and a registered promoted plan artifact before
  terminal success.
- References future child `verify` and `conv` workflows by durable id fields
  without creating child workflows or copying their state.
- Reuses C2.5 terminal checkpoint, final_status, mode_state exact-match,
  checkpoint-backed evidence, artifact, reserve-delivery, report-proof, and
  complete-reported invariants.
- Keeps C4 limited to local runtime behavior: no C4.5 helper consolidation, C5
  recovery, adapter migration, install wiring, slash routing, Gateway restart,
  external action, push, PR, or release.

## Planned C4.5 Cleanup Scope

- Consolidate common smoke helper patterns after C3 `conv` and C4 `goal` reveal
  the real iterative-mode and goal-mode test shapes.
- Keep this as test/docs cleanup only: no runtime behavior, no recovery
  implementation, no adapter routing, no Gateway restart, no external action,
  no push/PR/release.
- Use `docs/converge/p3-debt-register.md` as the input list, especially
  P3-C25-002. Do not pull C5 recovery transaction work into C4.5.

## C4.5 Completed Scope

- Added a shared smoke helper module for CLI invocation, workflow/event file
  access, assertion helpers, wrapper execution, and common visible-delivery
  fixtures.
- Migrated the high-duplication mode/runtime smokes to the shared helper without
  changing production runtime behavior.
- Kept C4.5 limited to test/docs cleanup: no C5 recovery, adapter routing,
  install wiring, slash routing, Gateway restart, external action, push, PR, or
  release.

## C5 Completed Scope

- Added local recovery commands: `scan`, `watchdog-check`, and `recover`.
- Kept `scan` read-only and limited to local workflow classification:
  active/stale, waiting-user, terminal-unreported, clean terminal-reported, and
  blocked recovery candidates.
- Kept `watchdog-check` to wake-needed recovery packets only; workflows that
  already hold an active recovery lease do not generate duplicate wake packets.
- Kept `recover` inside C5 recovery semantics: it acquires one recovery lease for
  a safe stale active cursor, rejects duplicate or inconsistent leases, and routes
  terminal-unreported workflows back to `reserve-delivery` instead of acquiring a
  recovery lease.
- Added recovery blocking for cursor/checkpoint mismatch, pending checkpoint,
  pending recovery lease, recovery lease transaction mismatch, stale context
  manifest, repeated side effects requiring reconcile, and risky side effects
  requiring approval.
- Extended smoke coverage for stale/interrupted `plan`/`verify`/`conv`/`goal`
  resume through a recovery lease, terminal-unreported/report-proof/reported
  boundaries, waiting-user reminders, context-manifest changes,
  cursor/checkpoint disagreement, side-effect policy blocking, recovery lease
  exclusivity, leased-workflow no-wake, and recovery transaction mismatch cases.
- Kept C5 limited to recovery: no install wiring, adapter routing, slash routing,
  Gateway restart, external action, push, PR, or release.

## C6 Completed Scope

- Added local install wiring for the standalone `converge` CLI, deterministic
  watchdog runner, prompt file, package manifest, and disabled OpenClaw plugin
  source tree.
- Kept installed and development CLI paths on the same `converge.cli` contract:
  development runs through `bin/converge` or `python3 -m converge.cli`, and the
  installed wrapper exports `OPENCLAW_CONVERGE_SOURCE_ROOT` before invoking
  `python3 -m converge.cli`.
- Kept `deploy-local.sh` as a local install wrapper only. It does not restart
  Gateway, route slash commands, push, open a PR, or release.
- Added install-path smoke coverage for package file hygiene, clean reinstall,
  installed validate/goal/scan/recover, watchdog runner success and failure
  packets, plugin manifest `main` existence, and Python cache exclusion.
- Kept C6 limited to install wiring: no adapter routing, slash routing, Gateway
  restart, external action, push, PR, or release.

## C7 Planned Scope

- Replace the older Slash/Ledger Adapter Routing concept with Canonical Command
  Replacement + Legacy Retirement.
- Use `docs/converge/c7-canonical-command-replacement.md` as the C7 design
  target.
- Make Converge the canonical backend for managed `/goal`, `/verify`, and
  `/conv` work after acceptance gates and a separately approved live-routing
  operation pass.
- Treat GoalFlow, Work Ledger orchestration, and verification-convergence skill
  paths as migration/retirement surfaces for Converge-owned workflows, not as
  performance baselines or default fallbacks.
- Start with C7.0 command inventory and synthetic, live-route-free dry-run
  adapter work. C7.0 must classify stale source-checkout or installed-copy
  Slash/Ledger wording as non-canonical until those copies are explicitly
  synchronized.
- Keep Gateway restart, live slash routing replacement, external action, push,
  PR, release, and development-server apply outside C7 unless a later explicit
  owner-approved operational task is requested.
- Keep live traffic observation and shadow routing outside C7 by default unless
  a later explicit owner-approved operational task enables them.
- Keep legacy or historical data deletion outside C7 entirely; it requires a
  separate owner-approved cleanup task.

## C7.0 Completed Scope

- Added `converge command-dry-run` as a synthetic adapter for `/goal`,
  `/verify`, `/conv`, and the legacy `/converge` alias.
- Kept the adapter strictly route-free: it does not create workflows, observe
  live traffic, enable shadow routing, restart Gateway, perform external
  actions, delete legacy data, deploy, push, open PRs, or release.
- Recorded the command ownership inventory in
  `docs/converge/c7-entrypoint-inventory.md`.
- Added smoke coverage proving command mapping, owner/session and
  visible-delivery metadata preservation, no workflow state materialization, and
  `/converge` deprecated-alias handling.
