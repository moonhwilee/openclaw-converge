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
archival, and repository removal require a later separately owner-approved
operational cleanup execution task outside C7.4.

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

## C7.3 Route Replacement Plan Contract

C7.3 fixes the route replacement plan as verifiable dry-run metadata, not as a
live route change. `converge command-dry-run` now emits
`route_retirement_plan.version: c7.3` with the following contract:

- **Route scope:** managed `/goal`, `/verify`, and `/conv` are the replacement
  candidates. `/converge` is a legacy alias and must either retire or remain an
  explicit alias message only.
- **Route classification:** each command records whether the default should be
  replaced after a separately owner-approved live-routing operation, or retired
  as an alias.
- **Source of truth after the gate:** Converge workflow state remains the
  source of truth for Converge-owned workflow recovery, delivery reservation,
  `report-proof`, and `complete-reported`.
- **Execution boundary:** C7.3 is `plan_and_dry_run_only`; it must not observe,
  intercept, route, or replace live Gateway traffic.

The approval gate must be exact and evidence-backed:

- owner approval is required
- an approval reference is required
- exact route scope is required
- evidence must include the C7.3 dry-run packet, command adapter smoke,
  recovery/report-proof smoke, and rollback switch plan
- stop conditions include missing exact approval, missing rollback expiry or log
  path, any live route change request inside C7.3, cleanup/removal execution,
  legacy deletion, legacy file movement or archival, and legacy skill
  disable/uninstall requests inside C7.3

The rollback switch must be a bounded operational safety switch, not a normal
fallback:

- explicit owner approval required
- logged with a required log path
- time-bounded with a required expiry
- scoped to the exact legacy route
- valid only for a later separately approved live-routing operational task
- automatic fallback is forbidden

The logging/proof requirement must preserve C7.2 source-of-truth ownership:

- route plan, dry-run packet, approval record, and rollback record are required
  before any later live route change
- delivery reservation, `report-proof`, and `complete-reported` remain Converge
  proof authorities for Converge-owned workflows
- GoalFlow, Work Ledger, chat memory, and verification-convergence artifacts are
  not authoritative for Converge-owned workflow completion after the C7.2/C7.3
  gates

The cleanup/removal boundary now records C7.4 as complete while preserving the
next operational boundary:

- `route_retirement_plan.cleanup_removal_boundary.status` is `completed`
- `completed_slice` is `C7.4 cleanup and removal plan`
- `next_operational_slice` is `C7 live route replacement readiness plan`
- `plan_only` is true
- `legacy_deletion_allowed` is false
- `live_route_removal_allowed` is false
- `separate_owner_approval_required` is true

## C7.4 Cleanup And Removal Plan Contract

C7.4 turns the C7.3 boundary into an explicit cleanup/removal plan. It is still
classification and planning only. The dry-run packet now emits
`route_retirement_plan.cleanup_removal_plan.version: c7.4` with an exact
inventory of legacy surfaces, their classification, the reason for that
classification, and the later-action boundary.

Allowed classifications are fixed:

- `retired`
- `archived`
- `still-active-for-non-Converge`
- `requires-owner-approval`

Current C7.4 cleanup/removal inventory:

| Category | Surface | Classification | Reason | Later-action boundary |
| --- | --- | --- | --- | --- |
| scripts | `workspace/scripts/goalflow_start_goal.py` | `requires-owner-approval` | It remains the owner of exact `/goal` draft intake until a separate live-routing task replaces `/goal` with Converge. | Retire or narrow only after owner-approved live route replacement and migration evidence. |
| docs | `workspace/AGENTS.md` and `docs/context/goalflow.md` exact `/goal` policy | `requires-owner-approval` | Workspace policy still defines the active `/goal` intake contract and cannot be rewritten by a plan-only cleanup slice. | Update policy only in the separately approved route replacement operation that actually changes the live owner. |
| skills | `workspace/skills/verification-convergence/SKILL.md` | `still-active-for-non-Converge` | The skill may remain useful for non-Converge audits while managed `/verify` and `/conv` route ownership is migrated. | Remove managed-command ownership only after Converge handles live `/verify` and `/conv` with owner-approved routing proof. |
| aliases | `/converge` legacy alias | `retired` | The alias has no independent state or delivery contract and must not become the primary product route. | Execute alias removal or replacement wording only in a later owner-approved live route removal task. |
| state paths | `workspace/state/goalflow/*` | `archived` | Historical GoalFlow records remain readable, but they are not authoritative for Converge-owned workflow recovery or completion. | Archive, move, or delete records only after explicit retention approval and migration checks. |
| state paths | `workspace/state/work-ledger/*` | `still-active-for-non-Converge` | Work Ledger remains valid for outer session recovery and non-Converge work, but not as Converge workflow source of truth. | Do not remove or narrow until non-Converge ledger use is separately inventoried and approved. |
| state paths | verification-convergence artifacts and chat-derived records | `requires-owner-approval` | Past verification artifacts can support audit history but their exact storage roots are not fixed by C7.4. | Discover exact paths before any retention, archive, move, or delete decision. |

C7.4 keeps the same source-of-truth boundary as C7.2/C7.3:

- Converge-owned workflow authority: workflow state, checkpoint cursor,
  delivery reservation, `report-proof`, and `complete-reported`.
- Legacy sources that are not authoritative for Converge work: GoalFlow, Work
  Ledger, chat memory, and verification-convergence artifacts.

Any later cleanup execution requires a separate explicit owner approval, exact
surface list, retention decision for historical state, rollback switch with
expiry and log path, and post-change smoke evidence. C7.4 itself forbids
cleanup/removal execution, live route removal, Gateway restart, shadow routing,
deploy/apply/install, external action, legacy data deletion, legacy file
deletion, file movement, file archival, skill disable/uninstall, push, PR, and
release.

## Implementation Slices

1. **C7.0 command inventory and routing spec**
   Completed. Identified every installed or documented `/goal`, `/verify`,
   `/conv`, and `/converge` route, recorded the current owner, intended C7
   owner, state root, delivery behavior, rollback switch, transitional
   behavior, and final behavior, and added a route-free `command-dry-run`
   adapter.

2. **C7.1 Converge command adapter**
   Completed. Hardened the existing synthetic `command-dry-run` adapter
   contract with fixed packet fields and validation for `/goal`
   draft/confirmation metadata, `/verify` audit intent, `/conv` round metadata,
   and state-root, delivery, and rollback fields. This did not create a new
   artifact store, routing layer, mode semantics implementation, live route, or
   Gateway traffic path.

3. **C7.2 Converge recovery/report-proof takeover**
   Completed. Converge-owned workflows now expose explicit Converge
   source-of-truth metadata in `scan` and `watchdog-check` recovery packets,
   and explicit Converge authority metadata in `reserve-delivery`,
   `report-proof`, and `complete-reported` outputs/state. Work Ledger may
   observe transition evidence during migration, but it is not the source of
   truth for Converge-owned workflow completion.

4. **C7.3 canonical route replacement / legacy route retirement plan**
   Completed. The existing dry-run adapter now emits a C7.3
   `route_retirement_plan` that fixes the managed command scope, legacy alias
   scope, route classifications, exact approval gate, explicit/logged/
   time-bounded rollback switch, and logging/proof requirements. This is
   dry-run-verifiable only; it does not execute live route replacement or route
   removal.

5. **C7.4 cleanup and removal plan**
   Completed. The dry-run route plan now includes a C7.4
   `cleanup_removal_plan` with exact legacy surface categories, classifications,
   reasons, later-action boundaries, source-of-truth boundaries, and later
   execution requirements. C7.4 is classification and planning only: it did not
   delete, move, archive, disable, uninstall, reroute, or remove any legacy
   script, doc, skill, alias, route, or state path.

### C7.4 Readiness Boundary

C7.4 may produce only:

- an inventory of legacy scripts, docs, skills, aliases, and state paths
- a classification for each surface: retired, archived, still active for
  non-Converge work, or requires separate owner approval
- a cleanup/removal plan and verification criteria for a later approved task

C7.4 must not execute cleanup/removal. Approval inside a C7.4 goal cannot
authorize execution. It must not restart Gateway, observe live traffic, enable
shadow routing, replace or remove live routes, deploy, apply, install, delete,
move, or archive legacy data/files, disable or uninstall legacy skills, send
external messages, push, open a PR, or release.

## Non-Goals

- No Gateway restart, live traffic observation, shadow routing, live route
  replacement, or live route removal inside C7. Those belong outside C7.4 and
  require a later separately approved operational task after C7 verification.
- No external action, PR, push, release, deploy/apply/install, deletion, move,
  archive, disable, uninstall, or development-server apply inside C7. Those
  belong outside C7.4 and require a separate explicit owner-approved
  operational task.
- No deletion of GoalFlow, Ledger, or skill history in C7.
- No `/c*` product command family.
- No broad platform rewrite, database migration, or distributed job engine.
- No silent fallback from a failed Converge workflow to legacy execution.

## Verification Gates

C7.0 inventory, C7.1 dry-run adapter contract, C7.2 recovery/report-proof
ownership, C7.3 route retirement/replacement planning, and C7.4
cleanup/removal planning work are complete.
C7.2 proves Converge-owned workflows recover and finalize visible report proof
from Converge workflow state without changing live routes. C7.3 proves the
route replacement plan, approval gate, rollback switch, and logging/proof
requirements as dry-run metadata. C7.4 proves the cleanup/removal inventory,
classifications, source-of-truth boundary, and later execution requirements as
dry-run metadata. Live route replacement is still not
authorized until a separate owner-approved operational task:

- Command inventory proves every current `/goal`, `/verify`, `/conv`, and
  `/converge` entrypoint has one intended C7 owner.
- A dry-run command adapter can show the exact `converge ...` invocation,
  command-specific metadata, and visible-delivery metadata without changing live
  routes.
- Converge mode smoke still passes after any adapter code is added.
- Recovery smoke proves an interrupted Converge-owned command resumes from the
  Converge workflow cursor, not from GoalFlow, Work Ledger, chat memory, or
  verification-convergence artifacts.
- Terminal-unreported smoke proves visible-send authority comes from
  `reserve-delivery` and report proof is finalized through `complete-reported`.
- Rollback switch is explicitly owner-approved, logged, time-bounded, scoped to
  an exact legacy route, and never an automatic fallback.
- Documentation no longer describes adapter coexistence as the C7 product goal.

## Readiness Verdict

C7.0, C7.1, C7.2, C7.3, and C7.4 are complete. C7.2 proved that Converge
recovery and delivery proof can own interrupted and terminal-unreported
Converge workflows without leaving GoalFlow, Work Ledger, chat memory, or
verification-convergence artifacts as the source of truth. C7.3 fixed the
replacement plan, approval gate, rollback switch, and logging/proof
requirements without changing live routes. C7.4 fixed the cleanup/removal
inventory and planning boundary without deleting, moving, archiving, disabling,
uninstalling, or rerouting legacy surfaces. Live replacement and cleanup/removal
execution remain separate owner-approved operational tasks.
