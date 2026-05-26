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
