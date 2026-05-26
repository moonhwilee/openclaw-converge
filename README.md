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
skills, aliases, and state paths. The next planned boundary is a separately
approved live route replacement readiness plan, not live route replacement.

The package is intentionally not wired into existing slash commands yet.
Development uses the local CLI. C7's target is to make Converge the canonical
backend for managed `/goal`, `/verify`, and `/conv` work after command-routing,
recovery, delivery-proof, and separately approved live-routing gates pass; it is
not a permanent `/c*` coexistence path. C7.0 starts with source inventory and a
synthetic dry-run adapter only, C7.1 fixes the dry-run packet contract, C7.2
keeps Converge-owned recovery/report proof inside Converge workflow state, and
C7.3 fixes the route replacement plan, approval gate, rollback switch, and
logging/proof requirements as dry-run-verifiable metadata. C7.4 fixes the
cleanup/removal inventory, classifications, source-of-truth boundary, and later
execution requirements as dry-run-verifiable metadata.
Gateway restart, live traffic observation, shadow routing, live slash-route
replacement/removal, deploy/apply/install, external action, deletion or movement
or archival of legacy files/data, legacy skill disable/uninstall, push, PR, and
release belong outside C7.4 and require a later separately approved operational
task.

```bash
python -m converge.cli start --kind goal --text "demo" --json
python -m converge.cli validate --sample-docs
python -m converge.cli command-dry-run --raw-message "/goal demo" --json
npm run smoke:command-adapter
```

Existing C6 local install wiring is available for the standalone CLI and
deterministic watchdog runner only. Do not run it as part of C7.4 or route
replacement readiness unless separately requested:

```bash
scripts/install-local.sh
~/.openclaw/bin/converge validate --sample-docs
OPENCLAW_CONVERGE_BIN=~/.openclaw/bin/converge ~/.openclaw/converge/scripts/converge_watchdog_runner.py --json
```

The install script does not restart Gateway, route slash commands, push, open a
PR, or release.
