# OpenClaw Converge

Converge is a recoverable workflow runtime for managed `plan`, `goal`,
`verify`, and `conv` work. The current build includes the local durable runtime,
the C0 shared mode contract, C1 `plan` mode, C2 `verify` mode, C2.5
terminal-finalization invariants, C3 `conv` mode, and the common
runtime-reporting/reconciliation foundation: JSON schemas, append-only events,
worklog initialization, atomic checkpoints, visible message formatting, shared
mode-handler primitives, terminal delivery reservation, report proof, manual
reconcile guards, C4 `goal` mode, C4.5 smoke helper/docs cleanup, C5 Recovery
commands, C6 local install wiring for the standalone CLI and deterministic
watchdog runner, C7.0 command inventory plus a synthetic dry-run adapter, C7.1
command adapter contract hardening, C7.2 recovery/report-proof ownership for
Converge-owned workflows, and C7.3 canonical route replacement / legacy route
retirement planning, and C7.4 cleanup/removal planning for legacy scripts, docs,
skills, aliases, and state paths. The C7 live route operational execution plan
now records the pre-execution gate package for a later separately approved
route replacement; it is still not live route replacement.

The package distributes Converge only. Do not bundle or install the retired
`verification-convergence` skill as the active `/verify` or `/conv` owner.
Agent or Gateway bootstrap rules that expose exact `/goal`, `/verify`, or
`/conv` must route those commands to the installed `converge` CLI first; they
must not silently fall back to a manual skill, direct shell audit, or duplicate
legacy report path when Converge is missing. `/converge` is a legacy alias
boundary and is not a primary product command.

C7's target is to make Converge the canonical backend for managed `/goal`,
`/verify`, and `/conv` work after command-routing, recovery, delivery-proof, and
separately approved live-routing gates pass; it is not a permanent `/c*`
coexistence path. C7.0 starts with source inventory and a synthetic dry-run
adapter only, C7.1 fixes the dry-run packet contract, C7.2 keeps
Converge-owned recovery/report proof inside Converge workflow state, and C7.3
fixes the route replacement plan, approval gate, rollback switch, and
logging/proof requirements as dry-run-verifiable metadata. C7.4 fixes the
cleanup/removal inventory, classifications, source-of-truth boundary, and later
execution requirements as dry-run-verifiable metadata. The operational execution
plan fixes the exact route scope, approval text, rollback, retention, smoke,
Gateway preflight, and abort gates that must be filled before a later
owner-approved live operation.
Gateway restart, live traffic observation, shadow routing, live slash-route
replacement/removal, deploy/apply/install, external action, deletion or movement
or archival of legacy files/data, legacy skill disable/uninstall, push, PR, and
release remain separate owner-approved operational actions.

```bash
python -m converge.cli start --kind goal --text "demo" --json
python -m converge.cli validate --sample-docs
python -m converge.cli --state-root /tmp/converge-dry-run command-dry-run \
  --raw-message "/goal demo" \
  --owner-session-key session:demo \
  --visible-delivery '{"channel":"telegram","target":"demo"}' \
  --json
npm run smoke:command-adapter
```

## Distribution contract

For GitHub/package installs, the expected user-facing contract is:

- Install or expose the `converge` CLI.
- Keep exact `/goal`, `/verify`, and `/conv` as Converge-managed commands.
- Hide the retired `verification-convergence` skill from normal user-facing
  command routing.
- If the Converge CLI is unavailable, report a routing failure. Do not perform
  the request by manually interpreting `/verify` or `/conv`.
- Do not introduce `/cplan`, `/cgoal`, `/cverify`, `/cconv`, or other `/c*`
  product commands. That proposal was retired.

Portable bootstrap rule for agents that support repository-provided
instructions:

```text
Treat only case-sensitive ^/goal(?:\s+|$), ^/verify(?:\s+|$), and
^/conv(?:\s+|$) as Converge-managed commands. Route them through the installed
converge CLI with explicit --state-root, --owner-session-key, and
--visible-delivery before doing any manual shell checks, panels, subagents, or
direct fixes. If the CLI is unavailable, report a routing failure instead of
falling back to a legacy verification skill.
```

Install smoke should prove all three exact commands produce Converge route
packets:

```bash
converge --state-root /tmp/converge-install-smoke command-dry-run \
  --raw-message "/goal demo" \
  --owner-session-key session:demo \
  --visible-delivery '{"channel":"telegram","target":"demo"}' \
  --json
converge --state-root /tmp/converge-install-smoke command-dry-run \
  --raw-message "/verify demo" \
  --owner-session-key session:demo \
  --visible-delivery '{"channel":"telegram","target":"demo"}' \
  --json
converge --state-root /tmp/converge-install-smoke command-dry-run \
  --raw-message "/conv demo" \
  --owner-session-key session:demo \
  --visible-delivery '{"channel":"telegram","target":"demo"}' \
  --json
```

Existing C6 local install wiring is available for the standalone CLI and
deterministic watchdog runner only. Do not run it as part of C7.4 or route
replacement readiness unless separately requested:

```bash
scripts/install-local.sh
~/.openclaw/bin/converge validate --sample-docs
~/.openclaw/bin/converge --state-root /tmp/converge-install-smoke command-dry-run --raw-message "/verify demo" --owner-session-key session:demo --visible-delivery '{"channel":"telegram","target":"demo"}' --json
~/.openclaw/bin/converge --state-root /tmp/converge-install-smoke command-dry-run --raw-message "/conv demo" --owner-session-key session:demo --visible-delivery '{"channel":"telegram","target":"demo"}' --json
OPENCLAW_CONVERGE_BIN=~/.openclaw/bin/converge ~/.openclaw/converge/scripts/converge_watchdog_runner.py --json
```

The install script does not restart Gateway, route slash commands, push, open a
PR, or release. It also does not install the retired `verification-convergence`
skill.
