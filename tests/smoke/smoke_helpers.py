"""Shared helpers for CLI smoke tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
ROOT_STR = str(ROOT)
while ROOT_STR in sys.path:
    sys.path.remove(ROOT_STR)
sys.path.insert(0, ROOT_STR)

VISIBLE_DELIVERY = '{"channel":"telegram","target":"test"}'
TEST_VISIBLE_DELIVERY = VISIBLE_DELIVERY


def _cli_command(state_root: Path, args: tuple[str, ...]) -> list[str]:
    override = os.environ.get("CONVERGE_SMOKE_BIN")
    if override:
        return [override, "--state-root", str(state_root), *args]
    return [sys.executable, "-m", "converge.cli", "--state-root", str(state_root), *args]


def _json_output(result: subprocess.CompletedProcess[str], args: tuple[str, ...]) -> dict[str, Any]:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"command returned non-JSON output: {' '.join(args)}\n"
            f"returncode={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
        ) from exc


def run(*args: str, state_root: Path) -> dict[str, Any]:
    result = subprocess.run(
        _cli_command(state_root, args),
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"command failed: {' '.join(args)}\nstdout={result.stdout}\nstderr={result.stderr}")
    return _json_output(result, args)


def run_fail(*args: str, state_root: Path) -> dict[str, Any]:
    result = subprocess.run(
        _cli_command(state_root, args),
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        raise AssertionError(f"command unexpectedly succeeded: {' '.join(args)}\nstdout={result.stdout}")
    return _json_output(result, args)


def run_bin(*args: str, state_root: Path) -> dict[str, Any]:
    result = subprocess.run(
        [str(ROOT / "bin" / "converge"), "--state-root", str(state_root), *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"bin command failed: {' '.join(args)}\nstdout={result.stdout}\nstderr={result.stderr}")
    return _json_output(result, args)


def workflow(state_root: Path, workflow_id: str) -> dict[str, Any]:
    return json.loads((state_root / "workflows" / workflow_id / "workflow.json").read_text(encoding="utf-8"))


def write_workflow(state_root: Path, workflow_id: str, payload: dict[str, Any]) -> None:
    (state_root / "workflows" / workflow_id / "workflow.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def events(state_root: Path, workflow_id: str) -> list[dict[str, Any]]:
    path = state_root / "workflows" / workflow_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_events(state_root: Path, workflow_id: str, records: list[dict[str, Any]]) -> None:
    path = state_root / "workflows" / workflow_id / "events.jsonl"
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def assert_keys(value: dict[str, Any], expected: set[str], message: str) -> None:
    if set(value) != expected:
        raise AssertionError(f"{message}: expected={sorted(expected)} actual={sorted(value)}")


def current_cursor(workflow_payload: dict[str, Any]) -> str:
    return workflow_payload["continuation_plan"]["rolling_state"]["current_resume_cursor"]
