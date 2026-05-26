# Converge Watchdog

The Converge watchdog runner is a deterministic local check.

It should execute `converge watchdog-check --json`, inspect the returned
recovery packets, and keep all risky work behind the existing approval
boundaries. It must not restart Gateway, change slash routing, push, open PRs,
release, or perform external delivery on its own.
