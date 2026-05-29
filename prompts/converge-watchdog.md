# Converge Watchdog

The Converge watchdog runner is a deterministic local heartbeat observer.

It should execute `converge watchdog-check --json`, inspect the returned
recovery packets, append a JSONL heartbeat record, and persist a small
latest-check state file.

Behavior is log-only. It must not notify, wake sessions, restart Gateway, change
slash routing, push, open PRs, release, or perform external delivery on its own.
