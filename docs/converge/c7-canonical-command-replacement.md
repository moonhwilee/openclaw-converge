# C7 Canonical Command Replacement And Legacy Retirement

This document is the implementation-ready design target for Converge C7. It
replaces the older C7 idea of adding reversible `/c*` adapter commands beside
the legacy flows.

## Objective

C7 defines and validates the replacement path that makes Converge the
canonical backend for managed `/goal`, `/verify`, and `/conv` work. It also
defines the retirement boundary for the legacy GoalFlow, Work Ledger
orchestration surface, and verification-convergence skill paths that are
replaced by Converge-owned state, recovery, and report-proof contracts.

The goal is not parity with the legacy systems. The goal is a cleaner,
recoverable Converge command path with better evidence, recovery, approval, and
delivery semantics than GoalFlow, Work Ledger, or the current `/verify` and
`/conv` protocol provide separately.

## Current Code Baseline

C0-C6 already provide the required local foundation:

- `converge goal`, `converge verify`, and `converge conv` create and finalize
  durable mode workflows through the same `WorkflowStore` and shared checkpoint
  path.
- `converge scan`, `converge watchdog-check`, and `converge recover` classify
  stale, blocked, waiting-user, and terminal-unreported workflows without using
  chat memory as the source of truth.
- `converge reserve-delivery`, `converge report-proof`, and
  `converge complete-reported` own terminal visible-delivery authority inside
  Converge.
- `scripts/install-local.sh` installs a standalone `converge` CLI and a disabled
  plugin source tree, but it deliberately does not restart Gateway or route slash
  commands.
- `scripts/converge_watchdog_runner.py` is local-only; it emits recovery packets
  but does not wake sessions, restart Gateway, or send external messages.

These facts mean C7 does not need a long-lived compatibility command family.
It needs a small routing/replacement layer that points the existing user-facing
commands at Converge and then removes the replaced legacy ownership.

## Difference From The Previous C7 Result

Previous C7 framing:

- Add Slash/Ledger Adapter Routing after C6.
- Keep existing `/goal`, `/verify`, and `/conv` unchanged.
- Introduce separate `/cplan`, `/cgoal`, `/cverify`, and `/cconv` commands.
- Decide later whether existing slash commands should migrate.
- Treat rollback/fallback to old paths as a normal operating posture.

Current C7 framing:

- Replace canonical `/goal`, `/verify`, and `/conv` backends with Converge.
- Do not add `/c*` commands as a product path.
- Treat legacy GoalFlow, Work Ledger orchestration, and verification-convergence
  skill routing as retirement targets, not quality baselines.
- Keep rollback available only as a bounded migration safety switch until C7
  verification passes.
- Move recovery and report-proof source of truth into Converge-owned records
  wherever Converge owns the workflow.

## Canonical Command Contract

C7 must define one canonical path for each managed command:

- `/goal <text>` starts Converge goal intake, creates the durable workflow before
  long work, records objective, non-goals, success criteria, assumptions, and
  approval boundaries, and uses Converge recovery/report-proof for the resulting
  workflow.
- `/verify <target>` starts Converge verify mode, defaults to audit semantics,
  records evidence and residuals in the Converge workflow, and emits the final
  report through Converge delivery reservation.
- `/conv <target>` starts Converge conv mode, defaults to repair or improvement
  semantics according to the accepted command policy, records round metadata and
  original-target/delta gates in the Converge workflow, and uses Converge
  recovery for interrupted rounds.

The compatibility rule is narrow: during migration, a separately
owner-approved operational rollback switch may force a legacy route for an
emergency. That switch must be explicit, logged, and time-bounded. It is not an
automatic fallback, and the normal route must be Converge after the relevant
replacement gate passes.

## Command Ownership Matrix

| Command | Current owner | C7 owner | Transitional behavior | Final behavior |
| --- | --- | --- | --- | --- |
| `/goal` | Exact trigger in workspace policy and `scripts/goalflow_start_goal.py`; GoalFlow creates goal records after final draft confirmation. | Converge `goal`. | Synthetic dry-run adapter proves the exact Converge invocation and preserves the current draft/confirmation gates before live routing changes. Active legacy GoalFlow records may finish under GoalFlow unless explicitly imported. | New managed `/goal` work creates Converge workflows after a separately approved live-routing operation. GoalFlow intake is retired for this command after that operation. |
| `/verify` | Verification-convergence skill, default audit mode. | Converge `verify`. | Synthetic shadow/dry-run packets capture owner/session/delivery metadata and verify Converge report material without observing or handling live traffic unless a separate owner-approved operational task enables shadow routing. | New managed `/verify` work records evidence, residuals, terminal material, and report proof in Converge after a separately approved live-routing operation. |
| `/conv` | Verification-convergence skill, default repair/improve convergence mode. | Converge `conv`. | Dry-run routing verifies round metadata, original-target lane, delta lane, and report-proof behavior without live replacement. | New managed `/conv` work records convergence rounds and recovery cursor state in Converge. |
| `/converge` | Legacy alias for `/conv`. | No primary product owner. | May map to Converge only as a temporary alias with clear deprecation wording. | Retired or replaced with a clear `/conv`/Converge message. |
| Work Ledger recovery/proof for Converge work | Work Ledger outer session ledger. | Converge `scan`, `watchdog-check`, `recover`, `reserve-delivery`, `report-proof`, and `complete-reported`. | Work Ledger may record the outer C7 migration task and non-Converge work, but it must not be the completion source of truth for Converge-owned workflows after the C7.2 takeover gate passes. | Converge-owned workflow recovery and report proof are Converge-owned. Ledger remains only for non-Converge work unless separately retired. |

## Legacy Retirement Map

| Legacy surface | C7 outcome |
| --- | --- |
| `scripts/goalflow_start_goal.py` exact `/goal` envelope | Replaced by Converge goal intake once Converge can ask unresolved intake questions, persist accepted criteria, and block risky scope changes. |
| GoalFlow runtime state | No longer the active state owner for new Converge-managed `/goal` work. Historical records remain readable until archived. |
| Work Ledger as outer orchestration/proof for Converge work | Replaced by Converge recovery, delivery reservation, report-proof, and reported-state records for Converge-owned workflows. Ledger may remain for non-Converge work until separately retired. |
| Verification-convergence skill `/verify` and `/conv` execution path | Replaced by Converge verify/conv routing after Converge supports the required reviewer orchestration policy, evidence capture, repair loop state, and visible report formatting. |
| `/converge` legacy alias | Do not promote. Either map to Converge only as a temporary alias or retire with a clear replacement message. |

Retirement does not mean deleting historical data in C7. Data deletion,
archival, and repository removal require a later approved cleanup phase.

## Legacy State Policy

C7 must classify existing legacy state before any live route replacement:

- **Allowed to finish:** active GoalFlow or Work Ledger work that began before
  C7 may complete under its original owner when importing would create more risk
  than value.
- **Imported:** only workflows with a clear objective, owner/session route,
  terminal proof state, and no risky pending side effect may be imported into a
  Converge workflow.
- **Blocked for manual reconciliation:** stale, partially reported, or
  side-effect-sensitive legacy records must not be auto-replayed by Converge.
- **Archived:** terminal historical records remain readable and may be indexed
  for audit, but they are not active recovery state.

For Converge-owned workflows, watchdog precedence belongs to Converge. Legacy
watchdogs may report observations during migration, but they must not duplicate
visible reports or replay side effects.

## Activation Stages

1. **Local dry-run**
   Produce the intended Converge CLI invocation and visible-delivery metadata
   for each command without changing live routes.

2. **Installed CLI smoke**
   Verify installed `converge validate --sample-docs`, mode smoke, `scan`, and
   watchdog runner behavior.

3. **Disabled plugin smoke**
   Verify the copied plugin source and manifest without enabling live routing.

4. **Synthetic shadow packet**
   Produce a Converge dry-run packet that matches the metadata shape of
   `/goal`, `/verify`, and `/conv` requests beside the legacy handling, with no
   user-visible duplicate report. This stage is synthetic/local by default. It
   must not observe, intercept, or route live chat/Gateway traffic unless a
   separate owner-approved operational task explicitly enables shadow routing.

5. **Canonical route plan**
   Design the exact live replacement operation and rollback switch. Executing
   live Gateway or slash-route replacement is not authorized by this document;
   it requires a later explicit owner-approved operational task after C7
   verification.

6. **Retirement plan**
   Define the docs and cleanup needed to remove the legacy default route for the
   replaced command. Executing route removal is a later explicit
   owner-approved operational task. Historical records remain readable.

## Implementation Slices

1. **C7.0 command inventory and routing spec**
   Identify every installed or documented `/goal`, `/verify`, `/conv`, and
   `/converge` route. Record the current owner, state root, delivery behavior,
   and rollback switch.

2. **C7.1 Converge command adapter**
   Add the smallest synthetic command layer that invokes `converge goal`,
   `converge verify`, or `converge conv` with owner session, visible delivery,
   and state-root metadata. The adapter must not implement mode semantics
   itself, must not replace live routes, and must not touch Gateway traffic
   unless a separate owner-approved operational task later enables shadow or
   live routing.

3. **C7.2 Converge recovery/report-proof takeover**
   Route Converge-owned workflows through `scan`, `watchdog-check`, `recover`,
   `reserve-delivery`, `report-proof`, and `complete-reported`. Work Ledger may
   observe transition evidence during migration, but it must not be the source
   of truth for Converge-owned workflow completion.

4. **C7.3 legacy route retirement plan**
   Specify how default routing to GoalFlow and the verification-convergence
   skill will stop for new managed `/goal`, `/verify`, and `/conv` requests.
   Do not execute live route replacement or route removal inside C7 without a
   separate explicit owner-approved operational task.

5. **C7.4 cleanup and removal plan**
   Mark replaced legacy scripts, docs, skills, and state paths as retired,
   archived, or still active for non-Converge work. Do not delete historical
   records without a separate approved cleanup.

## Non-Goals

- No Gateway restart, live traffic observation, shadow routing, or live route
  replacement inside C7 by default; those require a later separately approved
  operational task after C7 verification.
- No external action, PR, push, release, or development-server apply unless
  separately requested.
- No deletion of GoalFlow, Ledger, or skill history in C7.
- No `/c*` product command family.
- No broad platform rewrite, database migration, or distributed job engine.
- No silent fallback from a failed Converge workflow to legacy execution.

## Verification Gates

C7.0 inventory and dry-run adapter work is the next implementable slice. Live
route replacement and route retirement are not ready until these gates are
documented or tested:

- Command inventory proves every current `/goal`, `/verify`, `/conv`, and
  `/converge` entrypoint has one intended C7 owner.
- A dry-run command adapter can show the exact `converge ...` invocation and
  visible-delivery metadata without changing live routes.
- Converge mode smoke still passes after any adapter code is added.
- Recovery smoke proves an interrupted Converge-owned command resumes from the
  Converge workflow cursor, not from GoalFlow, Work Ledger, or chat memory.
- Terminal-unreported smoke proves visible-send authority comes from
  `reserve-delivery` and report proof is finalized through `complete-reported`.
- Rollback switch, if present, is explicitly owner-approved, logged,
  time-bounded, and never an automatic fallback.
- Documentation no longer describes adapter coexistence as the C7 product goal.

## Readiness Verdict

C7.0 is ready for an implementation `/goal` only when this document, the Phase
C todo, and the project index agree that C7 means canonical command replacement
plus legacy retirement. The next implementation should begin with source-of-
truth inventory and synthetic adapter dry-run work, not live slash-route
replacement. Live replacement can be planned by C7, but execution remains a
separate owner-approved operational task.
