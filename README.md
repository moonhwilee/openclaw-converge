# OpenClaw Converge

Converge is a recoverable workflow runtime for managed `plan`, `goal`,
`verify`, and `conv` work. The current build includes the local durable runtime,
the C0 shared mode contract, C1 `plan` mode, C2 `verify` mode, C2.5
terminal-finalization invariants, C3 `conv` mode, and the common
runtime-reporting/reconciliation foundation: JSON schemas, append-only events,
worklog initialization, atomic checkpoints, visible message formatting, shared
mode-handler primitives, terminal delivery reservation, report proof, manual
reconcile guards, C4 `goal` mode, C4.5 smoke helper/docs cleanup, and C5
Recovery commands, and C6 local install wiring for the standalone CLI and
deterministic watchdog runner. The next planned boundary is C7 Slash/Ledger
Adapter Routing.

The package is intentionally not wired into existing slash commands yet.
Development uses the local CLI:

```bash
python -m converge.cli start --kind goal --text "demo" --json
python -m converge.cli validate --sample-docs
```

Local install wiring is available for the standalone CLI and deterministic
watchdog runner only:

```bash
scripts/install-local.sh
~/.openclaw/bin/converge validate --sample-docs
OPENCLAW_CONVERGE_BIN=~/.openclaw/bin/converge ~/.openclaw/converge/scripts/converge_watchdog_runner.py --json
```

The install script does not restart Gateway, route slash commands, push, open a
PR, or release.
