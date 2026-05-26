# C7.0 Entrypoint Inventory And Synthetic Dry-Run Adapter

C7.0 is a local-only implementation slice. It records the current command
surfaces and adds a synthetic adapter that shows the Converge invocation a later
approved routing layer may use. It does not change live routes.

## Inventory

| Command | Current owner | C7 owner | C7.0 behavior |
| --- | --- | --- | --- |
| `/goal` | GoalFlow exact trigger plus `scripts/goalflow_start_goal.py` draft intake. | `converge goal` | Produce a dry-run invocation with owner session and visible-delivery metadata. No GoalFlow record import, Gateway route change, or live handling. |
| `/verify` | `verification-convergence` skill audit path. | `converge verify` | Produce a dry-run invocation. No specialist execution, live observation, shadow routing, or duplicate report. |
| `/conv` | `verification-convergence` skill repair/improvement path. | `converge conv` | Produce a dry-run invocation. No round execution beyond the existing local Converge mode command. |
| `/converge` | Legacy alias for `/conv`. | Temporary alias to `converge conv`, or retirement message. | Dry-run maps it to `conv` and marks it as `deprecated_alias`; it is not promoted as a primary product route. |

## Adapter Contract

The C7.0 adapter is exposed through:

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
- `inventory`: the command ownership matrix above

## Explicit Non-Goals

C7.0 does not restart Gateway, observe traffic, enable shadow routing, replace
slash routes, deploy, apply, install, push, open a PR, release, delete legacy
data, or send external messages.

Live route replacement remains a later owner-approved operational task after
the synthetic adapter and the C7 verification gates pass.
