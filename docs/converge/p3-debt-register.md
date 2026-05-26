# Converge P3 Debt Register

This register keeps non-blocking Converge cleanup items in one place. P0-P2
items do not belong here unless they are explicitly outside the current phase
and assigned to a later phase with a reason.

Current baseline: latest local C7.4 cleanup/removal planning on `main`. C7.2
keeps Converge-owned recovery, delivery reservation, report proof, and reported
completion inside Converge workflow state. C7.3 remains plan-only route
retirement/replacement work. Gateway restart, live traffic observation, shadow
routing, live slash-route replacement/removal, deploy/apply/install, external
action, deletion, movement, or archival of legacy files/data, legacy skill
disable/uninstall, push, PR, and release remain out of scope. C7.4 keeps
cleanup and removal execution out of scope while classifying legacy scripts,
docs, skills, aliases, and state paths for a later owner-approved operational
task.

## Maintenance Rule

- When a `/conv` or `/verify` run leaves a new P3, add it here in the same
  cleanup/documentation pass.
- Keep each item either `Open`, `Superseded`, or `Closed`.
- Do not let this register reopen completed phases by itself. Promote an item to
  active work only when it blocks the current phase or the user asks for a
  cleanup slice.

## Tracked Items

### P3-C25-001: Remove old `reserve-delivery` terminalization-era args

- Status: Open.
- Source: C2.5 cleanup convergence at `4d3e3ba`.
- Current shape: `reserve-delivery` still accepts `--terminal-evidence`,
  `--failure-reason`, and `--residuals`, even though terminalization now happens
  through terminal checkpoints before delivery reservation.
- Why deferred: removing the args touches CLI contract tests and older call
  fixtures, but does not currently create a duplicate write path because
  `reserve-delivery` validates existing terminal workflow material instead of
  creating terminal state.
- Suggested timing: optional CLI contract cleanup; not a C6 install wiring
  entry blocker.

### P3-C25-002: Consolidate larger smoke helper patterns

- Status: Superseded by C4.5.
- Source: C2.5 cleanup convergence at `4d3e3ba`.
- C4.5 result: high-duplication mode/runtime smokes now share
  `tests/smoke/smoke_helpers.py` for common CLI invocation, wrapper execution,
  workflow/event access, assertion helpers, and visible-delivery fixtures.
  Those smokes import the helpers instead of carrying local copies.
- Remaining shape: deeper semantic decomposition of the broad runtime smoke is
  separate optional test organization work, not a C4.5 blocker.
- Superseded by: `P3-C45-001`.

### P3-C25-003: Refactor `complete-reported` internal transition plumbing

- Status: Open.
- Source: C2.5 cleanup convergence at `4d3e3ba`.
- Current shape: `complete-reported` and report-proof recovery paths still have
  multiple branches that hydrate existing proof, append missing proof, and mark
  the workflow reported.
- Why deferred: current crash retry/idempotency behavior is intentional and
  covered by smoke tests. Refactoring it is broader than a C2.5 cleanup because
  it could change recovery semantics.
- Suggested timing: optional reporting cleanup; not a C6 install wiring entry
  blocker unless install smoke exposes a direct diagnostic failure.

### P3-C25-004: Mode artifact write/register crash window

- Status: Open.
- Assigned phase: optional post-C5 atomicity cleanup unless it becomes a direct
  install/runtime blocker.
- Source: 2026-05-25 memory flush after C1 global convergence; generalized by
  C3 final audit.
- Current shape: a mode artifact file can exist briefly before artifact
  registration if a crash occurs between file write and registration. This was
  first noticed for plan reports and also applies to conv reports and the C4
  goal plan artifact.
- Why deferred: this is a narrow atomicity cleanup, not a current terminal
  reporting blocker.
- Suggested timing: optional post-C5 atomicity cleanup; not a C6 install wiring
  entry blocker unless install smoke exposes a direct runtime failure.

### P3-C3-001: Keep synthetic conv scenario coverage out of production input paths

- Status: Open.
- Source: C3 final audit at `ab50ec7`.
- Current shape: C3 keeps material-change and max-round synthetic records as
  direct validation/render smoke coverage, while the production `conv` command
  now treats user text as normal input and does not expose magic strings or a
  hidden fixture CLI path.
- Why deferred: adding a hidden production test selector would preserve
  end-to-end fixture coverage but would create a worse C4/C5 structure precedent.
  The real end-to-end path should come from later specialist/adaptor execution,
  not from a permanent test-only runtime branch.
- Suggested timing: revisit when adapter-backed specialist execution exists, or
  in a post-C4.5 test harness cleanup if these scenarios can be exercised without
  adding runtime fixture selectors.

### P3-C4-001: Split runtime foundation smoke from `goal` command semantics

- Status: Closed.
- Source: C4 goal mode implementation at `1fff45b`; closed by the C4 broad
  audit after `9d66a93`.
- Current shape: `converge_runtime_foundation_smoke.py` now uses low-level
  `start --kind goal` fixtures for generic running workflow checks, while the
  real `goal` subcommand remains covered by focused C4 goal-mode smoke.
- Why closed: the broad smoke failure was promoted from P3 test-maintenance debt
  to P2 because it made the canonical package smoke red. The fix stayed narrow:
  no helper consolidation, no runtime fixture selector, and no next-phase work.

### P3-C4-002: Consolidate mode-owned acceptance event append path

- Status: Open.
- Source: C4 focused audit after `d61c22a`.
- Current shape: `goal` records its internal `plan_accepted` event through a
  mode-owned append path, while user-facing manual acceptance still goes through
  the generic `event` command validator.
- Why deferred: C4 focused audit tightened duplicate/conflicting acceptance
  invariants and terminal manual-event guards, so the current shape is not a
  delivery or terminalization blocker. A shared append helper would be broader
  helper consolidation.
- Suggested timing: optional post-C5 helper cleanup; not a C6 install wiring
  entry blocker.

### P3-C4-003: Add one independent goal artifact oracle smoke

- Status: Open.
- Source: C4 final broad audit after `376aea4`.
- Current shape: goal smoke tests use production helpers such as
  `build_goal_record`, `render_goal_plan`, and `validate_goal_state` for several
  fixture and artifact assertions.
- Why deferred: current CLI-level smokes and tamper tests cover the important
  runtime/terminal guards. This is a test-strengthening cleanup, not a C4
  contract blocker.
- Suggested timing: optional test-strengthening cleanup; not a C6 install wiring
  entry blocker.

### P3-C4-004: Refresh historical cleanup wording in implementation docs

- Status: Closed.
- Source: C4 final broad audit after `376aea4`.
- Current shape: the implementation structure document now marks the A/B cleanup
  and shared-mode boundary sections as historical/completed instead of current
  next-step work.
- Why closed: the C4.5 broad convergence pass refreshed this wording after
  C4.5 completion, without changing runtime behavior.

### P3-C4-005: Map C5 recovery fixture prerequisites

- Status: Closed.
- Source: C4 final broad audit after `376aea4`.
- Current shape: C5 recovery smoke now covers stale/interrupted fixtures across
  plan, verify, conv, and goal, plus terminal-unreported, waiting-user,
  context-manifest, side-effect-policy, checkpoint mismatch, and lease
  exclusivity cases.
- Why closed: C5 made the fixture boundary executable in
  `tests/smoke/converge_recovery_smoke.py`.

### P3-C45-001: Optional runtime smoke semantic split

- Status: Open.
- Source: C4.5 smoke helper/docs cleanup after
  `2465654 Consolidate C4.5 smoke helpers`.
- Current shape: `converge_runtime_foundation_smoke.py` now reuses common smoke
  helpers, but it still intentionally covers a broad mix of runtime foundation,
  artifact, delivery, context manifest, and guardrail checks in one file.
- Why deferred: splitting the file by semantic area would be test organization
  cleanup only. It is not required for C4.5 closure because the duplicate CLI
  helper surface has been extracted and canonical smoke remains the behavioral
  gate.
- Suggested timing: optional test organization cleanup; not a C6 install wiring
  entry blocker.

### P3-C5-001: Recovery transaction gap deferred from post-C2.5 audit

- Status: Closed.
- Assigned phase: C5 Recovery.
- Source: `conv-converge-global-post-c25-20260525` visible report.
- Current shape: `scan`, `watchdog-check`, and `recover` now classify recovery
  candidates, bind recovery packets to the current cursor/checkpoint, acquire or
  reject a single active recovery lease, and block on stale context,
  checkpoint/event mismatch, repeated side-effect keys, or risky side effects
  without approval.
- Why closed: C5 added focused smoke coverage for recovery lease exclusivity,
  terminal-unreported classification, context-manifest hash changes,
  side-effect policy blocking, and checkpoint mismatch blocking.

### P3-C5-002: Broaden terminal-unreported recovery smoke across modes

- Status: Open.
- Source: C5 recovery convergence after `43a5b7c`.
- Current shape: terminal-unreported recovery routing is covered through the
  `plan` command and its report-proof/reported boundary. Other modes share the
  same terminal-delivery path but do not yet have identical terminal-unreported
  recovery fixtures.
- Why deferred: this is test-strengthening only; C5 recovery must route all
  terminal-unreported workflows to `reserve-delivery`, and the shared reporting
  invariant smoke remains the behavioral gate.
- Suggested timing: optional post-C5 test strengthening; not a C6 install wiring
  entry blocker.

### P3-C5-003: Snapshot scan/watchdog read-only behavior

- Status: Open.
- Source: C5 recovery convergence after `43a5b7c`.
- Current shape: `scan` and `watchdog-check` are implemented as read-only
  classifiers and recovery packet emitters, and existing smoke covers their
  behavior through observable workflow state. There is not yet a focused
  before/after filesystem snapshot smoke proving they leave state files
  untouched.
- Why deferred: this is diagnostic hardening, not a functional recovery blocker.
  The commands currently do not mutate workflows as part of the recovery
  contract.
- Suggested timing: optional post-C5 test strengthening; not a C6 install wiring
  entry blocker.

## Superseded Or Closed Notes From Logs

These were found in ledger or memory searches, but they are not current open
cleanup work:

- `C2 verify mode remains next phase`: Closed by C2 implementation.
- `checkpoint_state_update schema package layout`: Closed; schema now exists in
  `openclaw-converge/converge/schemas/`.
- Optional payload examples: Superseded by implemented schema/smoke coverage;
  reopen only if documentation examples become a real usability blocker.
- Non-terminal payload fixtures for side effects, context manifest updates, and
  residuals: Superseded by later checkpoint/schema/mode smoke coverage.
- Active reservation manual-reconcile nuance: Superseded by later reporting
  boundary hardening and manual reconcile proof smoke.
- Local GoalFlow final-draft artifacts remain untracked: accepted operational
  artifact state, not Converge runtime debt.
- Real mode terminal evidence should prefer registered artifacts/worklog anchors:
  Superseded by C2/C2.5 terminal invariant work; keep as implementation guidance,
  not an open P3.
