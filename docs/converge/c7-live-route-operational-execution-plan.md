# C7 Live Route Replacement Operational Execution Plan

This document is the pre-execution plan for a later owner-approved operation
that replaces the live `/goal`, `/verify`, and `/conv` route backends with the
Converge canonical backend.

This plan does not execute the route replacement. It does not authorize Gateway
restart, route config reload, live/shadow routing, cleanup/removal,
legacy deletion/movement/archive, deploy/apply/install, external action, push,
PR, or release. Those remain blocked until the owner gives separate explicit
approval at the final execution gate.

## Current Verdict

`No-Go` until every gate below is filled with real operational evidence:

- exact implementation route inventory, including the live route config path,
  config key, and handler ID for each command
- owner approval record using the pinned approval kind and text
- rollback record with expiry, log path, exact legacy route scope, and
  activation/deactivation entry format
- retention decision for legacy state and artifacts
- pre-change smoke evidence
- Gateway restart/reload preflight decision
- post-change smoke checklist and evidence recording path
- abort-condition acknowledgement

## Exact Route Scope

Only these route names are in scope:

| Route | Current owner | Target owner | Target Converge invocation | Execution status |
| --- | --- | --- | --- | --- |
| `/goal` | GoalFlow exact trigger and `workspace/scripts/goalflow_start_goal.py` draft intake | Converge `goal` | `converge goal --text <request> --owner-session-key <session> --visible-delivery <json>` | blocked until final approval |
| `/verify` | `verification-convergence` audit skill path | Converge `verify` | `converge verify --target <target> --owner-session-key <session> --visible-delivery <json>` | blocked until final approval |
| `/conv` | `verification-convergence` repair/improvement skill path | Converge `conv` | `converge conv --target <target> --owner-session-key <session> --visible-delivery <json>` | blocked until final approval |

Explicitly excluded:

- `/converge`: legacy alias boundary only; do not promote, silently include, or
  remove it in this operation
- `/plan`, `/cgoal`, `/cverify`, `/cconv`, and any unlisted slash command
- cleanup/removal routes or legacy archival/deletion actions

## Implementation Route Inventory

Known Converge target implementation inventory:

| Item | Path or key | Required proof |
| --- | --- | --- |
| CLI entrypoint | `package.json` `bin.converge` -> `bin/converge` | local command resolves before operation |
| CLI module | `converge/cli.py` | `converge goal`, `converge verify`, and `converge conv` command help or dry-run proof exists |
| Goal mode | `converge/modes/goal.py` | focused goal smoke passes |
| Verify mode | `converge/modes/verify.py` | focused verify smoke passes |
| Conv mode | `converge/modes/conv.py` | focused conv smoke passes |
| Recovery authority | `converge/recovery.py` | recovery smoke passes and recovery source-of-truth excludes legacy artifacts |
| Dry-run adapter contract | `converge/command_adapter.py` | command-adapter smoke passes and emits exact route scope |

Still required immediately before execution:

| Route | Live route config path | Live route config key | Live handler ID | Required decision |
| --- | --- | --- | --- | --- |
| `/goal` | TBD by operator from installed Gateway/OpenClaw route config | TBD | TBD | abort if not discovered exactly |
| `/verify` | TBD by operator from installed Gateway/OpenClaw route config | TBD | TBD | abort if not discovered exactly |
| `/conv` | TBD by operator from installed Gateway/OpenClaw route config | TBD | TBD | abort if not discovered exactly |

The live route inventory is intentionally not guessed in this repository. The
operation must stop if the exact route config path, key, or handler ID cannot be
proved from the installed environment before any change.

## Owner Approval Record

Required approval kind:

```text
operational_live_route_replacement
```

Required approval text:

```text
I explicitly approve the operational live route replacement for exact commands /goal, /verify, and /conv to the Converge canonical backend. I do not approve /converge promotion, cleanup/removal execution, legacy deletion/movement/archive, deploy/apply/install, external action, push/PR/release, or Gateway restart unless separately stated with preflight evidence.
```

Required approval record fields:

| Field | Requirement |
| --- | --- |
| `approval_kind` | exactly `operational_live_route_replacement` |
| `approval_text` | exactly the pinned approval text above |
| `approver` | owner identity |
| `approved_at` | ISO-8601 timestamp |
| `approval_ref` | stable approval reference used in rollback log path |
| `exact_route_scope` | exactly `/goal`, `/verify`, `/conv` |
| `explicit_exclusions` | must include `/converge`, cleanup/removal, legacy deletion/movement/archive, deploy/apply/install, external action, push/PR/release |
| `rollback_expires_at` | ISO-8601 UTC timestamp, max 24 hours after activation |
| `rollback_log_path` | exact path using the template below |
| `retention_decision_ref` | reference to the retention decision table below |
| `pre_change_smoke_evidence` | reference to recorded pre-change smoke evidence |
| `post_change_smoke_plan` | reference to the post-change smoke checklist below |
| `stop_condition_acknowledgement` | explicit acknowledgement of all abort conditions |

## Rollback Record

Rollback is an explicit, owner-approved safety switch. It is not automatic
fallback and it is not normal routing posture.

Required rollback log path template:

```text
/Users/moon/.openclaw/state/converge/route-replacement/rollback-{approval_ref}-{yyyyMMddTHHmmssZ}.jsonl
```

Required rollback fields:

| Field | Requirement |
| --- | --- |
| `approval_ref` | same value as the owner approval record |
| `expires_at` | ISO-8601 UTC timestamp, max 24 hours |
| `legacy_route_scope` | exact legacy handlers for `/goal`, `/verify`, and `/conv` only |
| `activation_entry` | timestamp, route, previous handler, Converge handler, operator/session |
| `deactivation_entry` | timestamp, route, restored handler, operator/session |
| `rollback_reason` | required if activated |
| `post_rollback_smoke` | must pass for `/goal`, `/verify`, and `/conv` |

## Retention Decision

No legacy deletion, movement, archival, cleanup/removal, or skill disable is
authorized by this operational route replacement plan.

| Source | Decision for route replacement | Later boundary |
| --- | --- | --- |
| GoalFlow state | retain in place as historical, non-authoritative state | archive/delete only in separate cleanup/removal approval |
| Work Ledger state | retain in place as outer coordination/history | archive/delete only in separate cleanup/removal approval |
| verification-convergence artifacts | retain in place as historical audit artifacts | exact path discovery required before later retention action |
| chat-derived records | retain in place; never replay as completion proof | exact path discovery required before later retention action |
| `/converge` alias history | retain in place; no route removal in this operation | route removal/rewording requires separate approval |
| Converge workflow state | authoritative for new Converge-owned work after approved route replacement | do not import legacy state without separate migration approval |

## Pre-Change Smoke Evidence

Before any operational change, record evidence for:

- `npm run smoke`
- `npm run smoke:command-adapter`
- `python3 -m converge.cli validate --sample-docs`
- `python3 -m py_compile converge/command_adapter.py converge/cli.py converge/recovery.py`
- command dry-runs for `/goal`, `/verify`, and `/conv` proving exact
  owner/session/delivery/state-root propagation
- route inventory proof for the live route config path/key/handler ID
- no dirty worktree in the Converge source checkout except the approved
  operational plan commit

Failure of any pre-change smoke is an abort condition.

## Gateway Restart Or Route Config Reload Decision

Readiness and planning do not run Gateway preflight and do not authorize
restart/reload.

Before the later operation, the operator must record one of these decisions:

- `not_required`: no Gateway restart or route config reload is needed
- `required`: run
  `python3 /Users/moon/.openclaw/workspace/scripts/gateway_restart_preflight.py`
  immediately before restart/reload and require exact output
  `Gateway restart preflight: OK`

If restart or reload is required, the operation must stop until the owner gives
separate explicit restart/reload approval after seeing the preflight evidence.

## Post-Change Smoke Checklist

The operation cannot be reported complete until post-change smoke evidence
exists and passes:

- `/goal` route packet reaches Converge only
- `/verify` route packet reaches Converge only
- `/conv` route packet reaches Converge only
- `/converge` is not promoted to a primary route
- legacy handlers are suppressed or rollback-only for the managed route scope
- no duplicate visible report is emitted from GoalFlow, Work Ledger, chat
  memory, or verification-convergence artifacts
- Converge delivery reservation, `report-proof`, and `complete-reported`
  evidence exist for any completed Converge-owned workflow
- rollback activation path is still available until `expires_at`

## Abort Conditions

Abort before any operational change if any condition is true:

- exact owner approval record is missing or differs from the pinned kind/text
- exact route scope includes anything other than `/goal`, `/verify`, `/conv`
- live route config path/key/handler ID inventory is incomplete
- `/converge` promotion, route removal, or cleanup is requested
- rollback expiry or rollback log path is missing
- automatic fallback is requested
- retention decision is missing or requests deletion/movement/archive
- pre-change smoke fails
- Gateway restart/reload is needed but preflight is missing or failed
- Gateway restart/reload approval is missing after successful preflight
- owner/session/delivery/state-root propagation does not match the dry-run
  contract
- duplicate visible report risk is unresolved
- unexpected live traffic is observed before the approved operation starts
- post-change smoke evidence is missing or fails
- cleanup/removal, legacy deletion/movement/archive, deploy/apply/install,
  external action, push, PR, or release is requested

## Execution Sequence For The Later Approved Operation

1. Confirm owner approval record using the exact kind and text in this document.
2. Discover and record the exact live route config path/key/handler ID inventory.
3. Record retention decision and rollback record with expiry and log path.
4. Run and record pre-change smoke.
5. Decide whether Gateway restart/reload is required.
6. If restart/reload is required, run preflight and request separate
   restart/reload approval.
7. Stop and report the complete gate package to the owner.
8. Only after the owner gives final execution approval, replace the exact
   `/goal`, `/verify`, and `/conv` route backends.
9. Run post-change smoke.
10. Report completion only after post-change smoke evidence passes.

## Next Owner Approval

The next request may prepare the final execution gate package, but must still
stop before live replacement:

```text
/goal Converge /goal /verify /conv live route replacement final execution gate package를 작성해줘. 이 문서의 approval record, exact live route inventory, rollback record, retention decision, pre-change smoke evidence, Gateway restart/reload preflight decision, post-change smoke checklist, and abort conditions를 실제 값으로 채우고, live route replacement 직전 다시 보고해줘. Gateway restart/reload와 live route replacement는 내 별도 최종 승인 전까지 실행하지 마라.
```

## Final Execution Gate Package - 2026-05-27

This package fills the pre-execution gate with the installed environment
evidence available on 2026-05-27. It still does not execute live route
replacement, Gateway restart/reload, cleanup/removal, deploy/apply/install,
external action, push, PR, or release.

### Installed Route Inventory

Installed OpenClaw/Gateway inspection found that these commands are not
registered as Gateway/plugin slash routes today. They are currently text-level
orchestrator triggers in the main Codex/OpenClaw agent context.

| Command | Installed route path/key | Current handler ID | Current owner | Replacement target |
| --- | --- | --- | --- | --- |
| `/goal` | no installed Gateway/plugin route; exact text trigger `^/goal(?:\s+|$)` in `/Users/moon/.openclaw/workspace/AGENTS.md` | `workspace-contract.exact-goal-trigger` -> `/Users/moon/.openclaw/workspace/scripts/goalflow_start_goal.py` draft intake, then GoalFlow tools when owner-bound context exists | Workspace AGENTS policy plus GoalFlow tool surface | `converge goal --text <request> --owner-session-key <session> --visible-delivery <json>` |
| `/verify` | no installed Gateway/plugin route; skill trigger in `/Users/moon/.openclaw/workspace/skills/verification-convergence/SKILL.md` | `verification-convergence.verify` | `verification-convergence` skill audit path | `converge verify --target <target> --owner-session-key <session> --visible-delivery <json>` |
| `/conv` | no installed Gateway/plugin route; skill trigger in `/Users/moon/.openclaw/workspace/skills/verification-convergence/SKILL.md` | `verification-convergence.conv` | `verification-convergence` skill repair/improve path | `converge conv --target <target> --owner-session-key <session> --visible-delivery <json>` |
| `/converge` | no installed Gateway/plugin route; legacy alias in `/Users/moon/.openclaw/workspace/skills/verification-convergence/SKILL.md` | `verification-convergence.converge_alias` | legacy alias for `/conv` | excluded from this replacement; do not promote |

Supporting installed plugin/config facts:

- `/Users/moon/.openclaw/openclaw.json` has `commands.native=auto` and the
  `goalflow` plugin enabled.
- `openclaw plugins inspect goalflow --json` reports `commands=[]`,
  `httpRouteCount=0`, and GoalFlow contracts/tools
  `goal_create`, `goal_status`, `goal_hook_review`, `goal_run`,
  `goal_advance`, and `goal_cancel`.
- `/Users/moon/.openclaw/extensions/goalflow/openclaw.plugin.json` exposes
  GoalFlow tools only; it does not register `/goal`.
- `/Users/moon/.openclaw/plugin-sources/openclaw-converge.clone/openclaw.plugin.json`
  has `enabled=false`; `openclaw plugins inspect openclaw-converge --json`
  reports the plugin is not installed as an active OpenClaw plugin.
- The installed Converge CLI exists at `/Users/moon/.openclaw/bin/converge`,
  backed by `/Users/moon/.openclaw/converge/bin/converge`.

Operational implication: the live replacement is a workspace agent routing and
trigger ownership change, not a simple Gateway route-table edit. If a later
implementation introduces real Gateway/plugin slash routes for these commands,
that implementation must first update this inventory with the new route
path/key/handler IDs and re-run this gate.

### Approval Record

Required approval kind:

```text
operational_live_route_replacement
```

Required approval text for the actual execution, not for this package:

```text
I explicitly approve the operational live route replacement for exact commands /goal, /verify, and /conv to the Converge canonical backend. I do not approve /converge promotion, cleanup/removal execution, legacy deletion/movement/archive, deploy/apply/install, external action, push/PR/release, or Gateway restart unless separately stated with preflight evidence.
```

This package has owner approval only for read-only inventory, preflight, smoke,
and artifact writing. It does not contain the final execution approval above.

### Rollback Record

Rollback mode: explicit owner-approved safety switch only. Automatic fallback is
not allowed.

Rollback record for execution must use:

```text
/Users/moon/.openclaw/state/converge/route-replacement/rollback-{approval_ref}-{yyyyMMddTHHmmssZ}.jsonl
```

Required execution values:

| Field | Value for the later operation |
| --- | --- |
| `legacy_route_scope` | `/goal` workspace AGENTS exact trigger + GoalFlow draft intake; `/verify` verification-convergence audit trigger; `/conv` verification-convergence repair/improve trigger |
| `excluded_route_scope` | `/converge` alias, `/plan`, `/cgoal`, `/cverify`, `/cconv`, all unlisted slash commands |
| `expires_at` | required, max 24h after route replacement activation |
| `activation_entry` | timestamp, command, previous trigger/handler, Converge trigger/handler, operator session, approval ref |
| `deactivation_entry` | timestamp, command, restored trigger/handler, operator session, rollback reason |
| `post_rollback_smoke` | `/goal`, `/verify`, and `/conv` route packets return to legacy handlers without duplicate visible reports |

### Retention Decision

For the live route replacement operation, retain all legacy state and artifacts
in place. Do not delete, move, archive, disable, or uninstall anything.

| Source | Decision | Reason |
| --- | --- | --- |
| GoalFlow state | retain in place | historical records; not authoritative for new Converge-owned work after replacement |
| Work Ledger state | retain in place | outer session recovery and non-Converge coordination remain valid |
| verification-convergence skill/artifacts | retain in place | still useful for non-managed audits until a separate cleanup/removal approval |
| chat-derived records | retain in place | context only; never authoritative completion proof |
| `/converge` alias history | retain in place | alias promotion/removal is explicitly out of scope |

Cleanup/removal remains a separate later operation after successful live
replacement, post-change smoke, and owner-approved retention policy.

### Pre-Change Smoke Evidence

Recorded 2026-05-27 before any live route change:

| Check | Result |
| --- | --- |
| `npm run smoke:command-adapter` | pass |
| `npm run smoke` | pass |
| `/goal` command dry-run through `python3 -m converge.cli ... command-dry-run` | pass; packet preserves owner session, visible delivery, state root, route retirement plan, approval gate, rollback switch, cleanup/removal boundary |
| `python3 /Users/moon/.openclaw/workspace/scripts/gateway_restart_preflight.py` | pass; `Gateway restart preflight: OK`; no running cron tasks and no enabled cron jobs due within 3m |
| `openclaw plugins inspect goalflow --json` | pass; GoalFlow active, exposes tools, no commands or HTTP routes |
| `openclaw plugins inspect openclaw-converge --json` | expected not installed; Converge is currently a local CLI/install tree, not active plugin route |

### Gateway Restart Or Reload Decision

Current package decision: no Gateway restart or reload is performed.

Preflight result is available and passing, but the final execution must still
ask for separate explicit restart/reload approval if the implementation step
requires Gateway reload or service restart. If the later implementation only
changes workspace AGENTS/skill trigger ownership and is picked up by new agent
sessions, restart may be `not_required`, but session restart/new session testing
must still be included in post-change smoke.

### Post-Change Smoke Checklist

After the later owner-approved replacement, completion is blocked until all pass:

- `/goal` in a fresh session routes to Converge goal intake and no GoalFlow
  draft-intake handler emits a duplicate report.
- `/verify` in a fresh session routes to Converge verify mode and no
  verification-convergence audit handler emits a duplicate report.
- `/conv` in a fresh session routes to Converge conv mode and no
  verification-convergence repair handler emits a duplicate report.
- `/converge` is not promoted; it remains excluded or produces an explicit
  legacy/deprecated alias boundary.
- Owner session key, visible delivery, and state root propagate into the
  Converge packet.
- Converge delivery reservation, report proof, and complete-reported evidence
  are present before any completed workflow is reported.
- Rollback activation remains available until `expires_at`.

### Abort Conditions

Abort before execution if any of these are true:

- final owner approval text is missing or differs from the pinned text
- replacement scope includes anything beyond `/goal`, `/verify`, and `/conv`
- `/converge` is promoted or silently changed
- a real Gateway/plugin route appears during implementation but is not added to
  this inventory with exact path/key/handler ID
- rollback expiry or log path is missing
- automatic fallback is requested
- retention decision changes from retain-in-place to delete/move/archive/disable
- pre-change smoke fails or becomes stale after implementation changes
- Gateway restart/reload is required but explicit restart/reload approval is not
  obtained after passing preflight
- post-change smoke fails
- cleanup/removal, deploy/apply/install, external action, push, PR, or release
  is requested in the same operation

### Current Go/No-Go

`No-Go for execution`: the final execution approval has not been given.

`Go for next step`: prepare the concrete route ownership patch that changes only
the exact `/goal`, `/verify`, and `/conv` workspace trigger ownership to
Converge, with `/converge` excluded, then stop again before applying any Gateway
restart/reload or live operational replacement.
