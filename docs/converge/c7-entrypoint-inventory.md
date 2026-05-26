# C7.0 Entrypoint Inventory And Synthetic Dry-Run Adapter

C7.0 is a local-only implementation slice. It records the current command
surfaces and adds a synthetic adapter that shows the Converge invocation a later
approved routing layer may use. It does not change live routes.

## Inventory

| Command | Current owner | C7 owner | State root | Delivery behavior | Rollback switch | C7.0 behavior |
| --- | --- | --- | --- | --- | --- | --- |
| `/goal` | GoalFlow exact trigger plus `scripts/goalflow_start_goal.py` draft intake. | `converge goal` | Legacy GoalFlow state during C7.0; future Converge workflow state only after approved live routing. | Draft and confirmation first; visible completion remains bound to the original Telegram delivery route. | Keep existing `/goal` route until owner-approved replacement; disable the C7 adapter route to fall back. | Produce a dry-run invocation with owner session and visible-delivery metadata. No GoalFlow record import, Gateway route change, or live handling. |
| `/verify` | `verification-convergence` skill audit path. | `converge verify` | Legacy verification-convergence artifacts during C7.0; future Converge workflow state only after approved live routing. | One visible audit report through the original delivery route after evidence/report material is reserved. | Keep existing `/verify` handler until owner-approved replacement; disable the C7 adapter route to fall back. | Produce a dry-run invocation. No specialist execution, live observation, shadow routing, or duplicate report. |
| `/conv` | `verification-convergence` skill repair/improvement path. | `converge conv` | Legacy verification-convergence artifacts during C7.0; future Converge workflow state only after approved live routing. | Round summaries and final report through the original delivery route; material changes need follow-up proof. | Keep existing `/conv` handler until owner-approved replacement; disable the C7 adapter route to fall back. | Produce a dry-run invocation. No round execution beyond the existing local Converge mode command. |
| `/converge` | Legacy alias for `/conv`. | Temporary alias to `converge conv`, or retirement message. | No independent state root; alias must reuse `/conv` state or retire. | No independent delivery contract; alias maps to `/conv` dry-run and is marked deprecated. | Retire alias or keep explicit message only; never make it the primary route. | Dry-run maps it to `conv` and marks it as `deprecated_alias`; it is not promoted as a primary product route. |

## Adapter Contract

The C7 adapter is exposed through:

```bash
converge command-dry-run --raw-message '/goal example' \
  --owner-session-key session:test \
  --visible-delivery '{"channel":"telegram","target":"test"}'
```

The command returns JSON with:

- `dry_run: true`
- `workflow_created: false`
- `live_route_changed: false`
- `live_traffic_observed: false`
- `shadow_routing_enabled: false`
- `external_action_performed: false`
- `gateway_restart_required: false`
- `legacy_data_deleted: false`
- `converge_invocation.argv`: the intended local `converge goal|verify|conv`
  invocation for a later approved routing layer
- `inventory`: the command ownership matrix above, including current owner, C7
  owner, state root, delivery behavior, rollback switch, transitional behavior,
  and final behavior
- `adapter_contract.version: c7.1`
- `adapter_contract.shared_metadata`: fixed field locations for state root,
  delivery metadata, and rollback metadata
- `adapter_contract.command_metadata`: command-specific metadata:
  - `/goal`: goal intake intent plus draft/confirmation metadata requirements
  - `/verify`: audit intent plus evidence/residual metadata requirements
  - `/conv`: repair/improve intent plus round, original-target, and delta
    metadata requirements
  - `/converge`: deprecated alias metadata mapped to `conv`
- `route_retirement_plan.version: c7.3`
- `route_retirement_plan.scope`: managed `/goal`, `/verify`, and `/conv`
  default-route replacement scope, plus `/converge` as a legacy alias
- `route_retirement_plan.approval_gate`: exact owner approval, approval
  reference, route scope, evidence, and stop conditions required before any
  live route change
- `route_retirement_plan.rollback_switch`: explicit, logged, time-bounded,
  exact-scope rollback metadata; automatic fallback is forbidden
- `route_retirement_plan.logging_proof`: required dry-run packet, route plan,
  approval, rollback, delivery reservation, report-proof, and
  complete-reported evidence
- `route_retirement_plan.cleanup_removal_boundary`: C7.4 cleanup/removal remains
  plan-only, forbids legacy deletion and live route removal, and requires a
  separate owner approval
- `route_retirement_plan.cleanup_removal_plan`: C7.4 legacy surface inventory
  for scripts, docs, skills, aliases, and state paths, with fixed
  classifications, reasons, later-action boundaries, source-of-truth boundary,
  and later execution requirements

## Explicit Non-Goals

C7.0-C7.4 do not restart Gateway, observe traffic, enable shadow routing,
replace slash routes, deploy, apply, install, push, open a PR, release, delete
legacy data, or send external messages.

Live route replacement remains a later owner-approved operational task after
the synthetic adapter and the C7 verification gates pass.
