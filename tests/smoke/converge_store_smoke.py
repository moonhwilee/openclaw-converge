#!/usr/bin/env python3
"""Smoke coverage for store and schema behavior."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def run(*args: str, state_root: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "converge.cli", "--state-root", str(state_root), *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"command failed: {' '.join(args)}\nstdout={result.stdout}\nstderr={result.stderr}")
    return json.loads(result.stdout)


def run_fail(*args: str, state_root: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "converge.cli", "--state-root", str(state_root), *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        raise AssertionError(f"command unexpectedly succeeded: {' '.join(args)}\nstdout={result.stdout}")
    return json.loads(result.stdout)


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="converge-store-smoke-") as tmp:
        state_root = Path(tmp)
        bin_help = subprocess.run(
            [str(ROOT / "bin" / "converge"), "--help"],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        assert_true(bin_help.returncode == 0 and "usage:" in bin_help.stdout, "packaged converge bin should run")
        created = run(
            "start",
            "--kind",
            "goal",
            "--text",
            "Implement a slice",
            "--workflow-id",
            "goal-smoke",
            "--json",
            "--visible-delivery",
            '{"channel":"telegram","target":"test"}',
            state_root=state_root,
        )
        workflow = created["workflow"]
        assert_true(workflow["workflow_id"] == "goal-smoke", "workflow id mismatch")
        assert_true(workflow["continuation_plan"]["rolling_state"]["current_resume_cursor"] == "objective-gate", "cursor missing")
        workflow_dir = state_root / "workflows" / "goal-smoke"
        assert_true((workflow_dir / "workflow.json").exists(), "workflow.json missing")
        assert_true((workflow_dir / "events.jsonl").exists(), "events.jsonl missing")
        assert_true((workflow_dir / "worklog.md").exists(), "worklog missing")

        duplicate = run_fail(
            "start",
            "--kind",
            "goal",
            "--text",
            "again",
            "--workflow-id",
            "goal-smoke",
            state_root=state_root,
        )
        assert_true(duplicate["ok"] is False, "duplicate create should fail")

        invalid = run_fail("start", "--kind", "goal", "--text", "bad", "--workflow-id", "../bad", state_root=state_root)
        assert_true(invalid["ok"] is False, "unsafe workflow id should fail")

        run("validate", "--workflow-id", "goal-smoke", "--sample-docs", state_root=state_root)

        def append(index: int) -> dict:
            return run("append-round", "--workflow-id", "goal-smoke", "--round", str(index), "--summary", f"progress {index}", state_root=state_root)

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(append, range(1, 9)))
        assert_true(all(item["ok"] for item in results), "parallel append failed")
        after = run("status", "--workflow-id", "goal-smoke", state_root=state_root)["workflow"]
        assert_true(
            after["continuation_plan"]["rolling_state"]["current_resume_cursor"] == "objective-gate",
            "append-round must not move cursor",
        )
        worklog = (workflow_dir / "worklog.md").read_text(encoding="utf-8")
        events = [json.loads(line) for line in (workflow_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        progress_events = [event for event in events if event["event_type"] == "progress"]
        assert_true(len(progress_events) == 8, "append-round should record all progress events")
        assert_true(worklog.count("## Progress ") == 8, "append-round should record matching worklog blocks")
        (workflow_dir / ".pending-chk-manual.json").write_text('{"checkpoint_id":"chk-manual"}\n', encoding="utf-8")
        pending_append = run_fail("append-round", "--workflow-id", "goal-smoke", "--round", "99", "--summary", "blocked progress", state_root=state_root)
        assert_true("pending checkpoint transaction" in pending_append["error"], "append-round should block during pending checkpoint")

    print(
        json.dumps(
            {
                "ok": True,
                "checked": [
                    "bin help",
                    "create",
                    "duplicate reject",
                    "unsafe id reject",
                    "schema",
                    "parallel append",
                    "append-round event/worklog parity",
                    "append-round pending checkpoint guard",
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
