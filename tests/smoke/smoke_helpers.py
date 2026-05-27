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


def assert_phase5a_contract(wf: dict[str, Any], state_key: str) -> None:
    state = wf[state_key]
    contract = state.get("required_evidence_contract")
    evidence_map = state.get("evidence_map")
    freshness = state.get("evidence_freshness_status")
    assert_true(isinstance(contract, dict), f"{state_key} should carry Phase 5A required_evidence_contract")
    assert_true(isinstance(evidence_map, dict), f"{state_key} should carry Phase 5A evidence_map")
    assert_true(isinstance(freshness, dict), f"{state_key} should carry Phase 5A evidence_freshness_status")
    assert_true(freshness.get("fresh") is True, f"{state_key} Phase 5A evidence should be fresh")
    for item in contract.get("required") or []:
        gate_id = item.get("gate_id")
        assert_true(gate_id in evidence_map, f"{state_key} Phase 5A required gate should be mapped: {gate_id}")
        assert_true(evidence_map[gate_id].get("valid_for_stop_status") is True, f"{state_key} Phase 5A gate should be valid: {gate_id}")


def assert_phase5a_missing_gate_rejected(state_root: Path, workflow_id: str, state_key: str, gate_id: str) -> None:
    original = workflow(state_root, workflow_id)
    original_events = events(state_root, workflow_id)
    corrupt = json.loads(json.dumps(original))
    del corrupt[state_key]["evidence_map"][gate_id]
    write_workflow(state_root, workflow_id, corrupt)
    _write_terminal_state_update(state_root, workflow_id, state_key, corrupt[state_key])
    try:
        result = run_fail("validate", "--workflow-id", workflow_id, state_root=state_root)
        assert_true(
            "required evidence gate is missing" in result["error"] or "execution evidence ref is missing from evidence_map" in result["error"],
            f"Phase 5A should reject missing required evidence-map gate: {result['error']}",
        )
    finally:
        write_workflow(state_root, workflow_id, original)
        write_events(state_root, workflow_id, original_events)


def assert_phase5a_stale_hash_rejected(state_root: Path, workflow_id: str, state_key: str, gate_id: str) -> None:
    original = workflow(state_root, workflow_id)
    original_events = events(state_root, workflow_id)
    corrupt = json.loads(json.dumps(original))
    corrupt[state_key]["evidence_map"][gate_id]["artifact_hash_or_revision"] = "stale-sha256"
    write_workflow(state_root, workflow_id, corrupt)
    _write_terminal_state_update(state_root, workflow_id, state_key, corrupt[state_key])
    try:
        result = run_fail("validate", "--workflow-id", workflow_id, state_root=state_root)
        assert_true("artifact hash is stale" in result["error"], "Phase 5A should reject stale evidence artifact hashes")
    finally:
        write_workflow(state_root, workflow_id, original)
        write_events(state_root, workflow_id, original_events)


def assert_phase5a_freshness_rejected(state_root: Path, workflow_id: str, state_key: str) -> None:
    original = workflow(state_root, workflow_id)
    original_events = events(state_root, workflow_id)
    corrupt = json.loads(json.dumps(original))
    corrupt[state_key]["evidence_freshness_status"]["fresh"] = False
    corrupt[state_key]["evidence_freshness_status"]["stale_evidence_refs"] = ["phase5a-stale-probe"]
    write_workflow(state_root, workflow_id, corrupt)
    _write_terminal_state_update(state_root, workflow_id, state_key, corrupt[state_key])
    try:
        result = run_fail("validate", "--workflow-id", workflow_id, state_root=state_root)
        assert_true("evidence freshness is stale" in result["error"], "Phase 5A should reject stale evidence freshness")
    finally:
        write_workflow(state_root, workflow_id, original)
        write_events(state_root, workflow_id, original_events)


def assert_phase5a_terminal_status_rejected(state_root: Path, workflow_id: str, state_key: str) -> None:
    original = workflow(state_root, workflow_id)
    original_events = events(state_root, workflow_id)
    corrupt = json.loads(json.dumps(original))
    corrupt[state_key]["required_evidence_contract"]["terminal_status"] = "stale-terminal-status"
    write_workflow(state_root, workflow_id, corrupt)
    _write_terminal_state_update(state_root, workflow_id, state_key, corrupt[state_key])
    try:
        result = run_fail("validate", "--workflow-id", workflow_id, state_root=state_root)
        assert_true("terminal status contract is stale" in result["error"], "Phase 5A should reject stale terminal status contracts")
    finally:
        write_workflow(state_root, workflow_id, original)
        write_events(state_root, workflow_id, original_events)


def assert_phase5a_accepted_change_stale_rejected(state_root: Path, workflow_id: str, state_key: str) -> None:
    original = workflow(state_root, workflow_id)
    original_events = events(state_root, workflow_id)
    corrupt = json.loads(json.dumps(original))
    corrupt[state_key]["accepted_change_refs"] = [{"change_ref": "phase5a-material-change"}]
    corrupt[state_key]["evidence_freshness_status"]["accepted_change_ids"] = ["phase5a-material-change"]
    write_workflow(state_root, workflow_id, corrupt)
    _write_terminal_state_update(state_root, workflow_id, state_key, corrupt[state_key])
    try:
        result = run_fail("validate", "--workflow-id", workflow_id, state_root=state_root)
        assert_true("evidence predates accepted material changes" in result["error"], "Phase 5A should reject evidence predating accepted changes")
    finally:
        write_workflow(state_root, workflow_id, original)
        write_events(state_root, workflow_id, original_events)


def _write_terminal_state_update(state_root: Path, workflow_id: str, state_key: str, state: dict[str, Any]) -> None:
    records = events(state_root, workflow_id)
    for record in reversed(records):
        state_update = _find_state_update(record, state_key)
        if state_update is not None:
            if "__mode_state_parent__" in state_update:
                state_update["__mode_state_parent__"]["mode_state_update"] = state
            else:
                state_update[state_key] = state
            write_events(state_root, workflow_id, records)
            return
    raise AssertionError(f"terminal checkpoint state_update missing {state_key}")


def _find_state_update(value: Any, state_key: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if isinstance(value.get("mode_state_update"), dict):
            return {"__mode_state_parent__": value}
        if state_key in value and any(key.endswith("_state") for key in value):
            return value
        for child in value.values():
            found = _find_state_update(child, state_key)
            if found is not None:
                return found
    if isinstance(value, list):
        for child in value:
            found = _find_state_update(child, state_key)
            if found is not None:
                return found
    return None


def assert_keys(value: dict[str, Any], expected: set[str], message: str) -> None:
    if set(value) != expected:
        raise AssertionError(f"{message}: expected={sorted(expected)} actual={sorted(value)}")


def current_cursor(workflow_payload: dict[str, Any]) -> str:
    return workflow_payload["continuation_plan"]["rolling_state"]["current_resume_cursor"]
