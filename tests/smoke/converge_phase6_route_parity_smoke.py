#!/usr/bin/env python3
"""Smoke coverage for Phase 6 route parity gate."""

from __future__ import annotations

import tempfile
from pathlib import Path

try:
    from smoke_helpers import VISIBLE_DELIVERY, assert_true, run, run_fail
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import VISIBLE_DELIVERY, assert_true, run, run_fail


OWNER_SESSION = "session:phase6-smoke"


def route_parity_check(state_root: Path, *extra: str) -> dict:
    return run(
        "route-parity-check",
        "--owner-session-key",
        OWNER_SESSION,
        "--visible-delivery",
        VISIBLE_DELIVERY,
        *extra,
        state_root=state_root,
    )


def assert_managed_command(result: dict, command: str) -> None:
    command_result = result["managed_commands"][command]
    assert_true(command_result["ok"] is True, f"{command} route parity check should pass")
    assert_true(command_result["workflow_state_unchanged"] is True, f"{command} should not create workflow state")
    for field, passed in command_result["route_free"].items():
        assert_true(passed is True, f"{command} should keep route-free flag {field}")
    for field, passed in command_result["metadata"].items():
        assert_true(passed is True, f"{command} should preserve route metadata {field}")


def assert_phase6_gate_is_bounded(result: dict) -> None:
    assert_true(result["ok"] is True, "Phase 6 route parity gate should pass for source dry-runs")
    assert_true(result["proof_level"] == "route_dry_run_gate", "Phase 6 gate should label dry-run proof level")
    assert_true(
        result["production_route_parity_proven"] is False,
        "dry-run gate must not claim real production route parity",
    )
    assert_true(result["route_change_performed"] is False, "Phase 6 gate should not change live routes")
    assert_true(result["gateway_restart_performed"] is False, "Phase 6 gate should not restart Gateway")
    assert_true(result["external_action_performed"] is False, "Phase 6 gate should not perform external action")
    assert_true(
        result["cleanup_or_legacy_removal_performed"] is False,
        "Phase 6 gate should not remove legacy artifacts",
    )
    completion_gate = result["completion_gate"]
    assert_true(
        completion_gate["ready_for_live_replacement_completion"] is False,
        "dry-run gate should still block live replacement completion",
    )
    for required in (
        "fresh-session exact /goal visible route proof",
        "fresh-session exact /verify visible route proof",
        "fresh-session exact /conv visible route proof",
        "single route owner proof with no duplicate legacy visible report",
        "reserve-delivery/report-proof/complete-reported proof in the real delivery channel",
    ):
        assert_true(required in completion_gate["blocked_until"], f"Phase 6 gate should require {required}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_root = Path(tmp) / "state"
        result = route_parity_check(state_root)
        assert_phase6_gate_is_bounded(result)
        for command in ("/goal", "/verify", "/conv"):
            assert_managed_command(result, command)

        alias = result["legacy_alias_boundary"]["/converge"]
        assert_true(alias["ok"] is True, "legacy alias boundary dry-run should pass")
        assert_true(alias["metadata"]["alias_status"] is True, "/converge should remain a deprecated alias")
        assert_true(not (state_root / "workflows").exists(), "Phase 6 gate should not create workflow state")

        missing_owner = run_fail(
            "route-parity-check",
            "--visible-delivery",
            VISIBLE_DELIVERY,
            state_root=state_root,
        )
        assert_true(
            "owner_session_key" in missing_owner["error"],
            "Phase 6 route parity gate should require owner session",
        )


if __name__ == "__main__":
    main()
