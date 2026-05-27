#!/usr/bin/env python3
"""Reusable C2.5 terminal finalization invariant smoke coverage."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from converge.modes.goal import build_goal_record, render_goal_plan  # noqa: E402
from converge.modes.evidence_contract import attach_phase5a_evidence_contract  # noqa: E402

try:
    from terminal_invariant_helpers import (  # noqa: E402
        VISIBLE_DELIVERY,
        artifact_path,
        assert_true,
        events,
        finalize_mode,
        reserve_delivery,
        run,
        run_fail,
        workflow,
        write_workflow,
    )
except ModuleNotFoundError:
    from tests.smoke.terminal_invariant_helpers import (  # noqa: E402
        VISIBLE_DELIVERY,
        artifact_path,
        assert_true,
        events,
        finalize_mode,
        reserve_delivery,
        run,
        run_fail,
        workflow,
        write_workflow,
    )


MODE_CASES = (
    {
        "kind": "plan",
        "state_key": "plan_state",
        "artifact_path_key": "final_plan_artifact_path",
    },
    {
        "kind": "verify",
        "state_key": "verify_state",
        "artifact_path_key": "final_report_artifact_path",
    },
)


def assert_terminal_final_status_exact_match(state_root: Path, case: dict[str, str]) -> None:
    workflow_id = f"{case['kind']}-terminal-final-status-drift"
    wf = finalize_mode(state_root, kind=case["kind"], workflow_id=workflow_id)
    wf["final_status"] = {**wf["final_status"], "result": "pass"}
    write_workflow(state_root, workflow_id, wf)
    result = run_fail("validate", "--workflow-id", workflow_id, state_root=state_root)
    assert_true(
        "final_status must match terminal checkpoint final_status" in result["error"],
        f"{case['kind']} terminal final_status must remain checkpoint-backed",
    )


def assert_terminal_mode_state_exact_match(state_root: Path, case: dict[str, str]) -> None:
    workflow_id = f"{case['kind']}-terminal-mode-state-drift"
    wf = finalize_mode(state_root, kind=case["kind"], workflow_id=workflow_id)
    wf[case["state_key"]]["unexpected_c25_drift"] = "drift"
    write_workflow(state_root, workflow_id, wf)
    result = run_fail("validate", "--workflow-id", workflow_id, state_root=state_root)
    assert_true(
        f"{case['state_key']} must match terminal checkpoint {case['state_key']}" in result["error"],
        f"{case['kind']} terminal mode_state must exactly match checkpoint snapshot",
    )


def assert_checkpoint_backed_evidence_sequence(state_root: Path, case: dict[str, str]) -> None:
    workflow_id = f"{case['kind']}-terminal-evidence-sequence"
    run(
        "start",
        "--kind",
        case["kind"],
        "--text",
        f"plan-only Checkpoint-backed evidence fixture for {case['kind']}",
        "--workflow-id",
        workflow_id,
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    preterminal_evidence = {
        "evidence_key": f"{case['kind']}-preterminal-checkpoint",
        "kind": "contract",
        "summary": f"{case['kind']} preterminal checkpoint-backed evidence.",
        "artifact_refs": [],
    }
    run(
        "checkpoint",
        "--workflow-id",
        workflow_id,
        "--checkpoint-type",
        "checkpoint",
        "--state-update",
        json.dumps(
            {
                "checkpoint_type": "checkpoint",
                "status_after": "running",
                "phase_after": "preterminal-evidence",
                "cursor_before": "start",
                "cursor_after": "start",
                "event_type": "checkpoint",
                "worklog_block_kind": "slice_summary",
                "step_result": "passed",
                "residuals": {},
            }
        ),
        "--summary",
        "record preterminal checkpoint-backed evidence",
        "--evidence",
        json.dumps(preterminal_evidence),
        state_root=state_root,
    )
    wf = run(
        case["kind"],
        "--text",
        "plan-only ignored retry text",
        "--workflow-id",
        workflow_id,
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    assert_true(wf["verification"]["evidence"][0] == preterminal_evidence, f"{case['kind']} should preserve preterminal evidence")
    run("validate", "--workflow-id", workflow_id, state_root=state_root)

    wf["verification"]["evidence"] = [
        {
            "evidence_key": f"{case['kind']}-uncheckpointed-extra",
            "kind": "contract",
            "summary": "Valid-looking evidence not represented by a checkpoint.",
            "artifact_refs": [],
        },
        *wf["verification"]["evidence"],
    ]
    write_workflow(state_root, workflow_id, wf)
    result = run_fail("validate", "--workflow-id", workflow_id, state_root=state_root)
    assert_true(
        "verification evidence must match checkpoint-backed terminal evidence sequence" in result["error"],
        f"{case['kind']} should reject uncheckpointed workflow evidence",
    )


def assert_reserve_validates_terminal_material_before_send(state_root: Path, case: dict[str, str]) -> None:
    workflow_id = f"{case['kind']}-terminal-material-presend"
    wf = finalize_mode(state_root, kind=case["kind"], workflow_id=workflow_id)
    artifact_path(wf, state_key=case["state_key"], path_key=case["artifact_path_key"]).unlink()
    result = run_fail(
        "reserve-delivery",
        "--workflow-id",
        workflow_id,
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "must not send missing terminal artifact",
        "--final-status",
        json.dumps(wf["final_status"]),
        state_root=state_root,
    )
    assert_true(result["send_authorized"] is False, f"{case['kind']} reserve-delivery should reject missing material")
    assert_true(result["reason"] == "validation_error", f"{case['kind']} missing material should require validation reconciliation")

    stale_workflow_id = f"{case['kind']}-terminal-material-stale"
    stale_wf = finalize_mode(state_root, kind=case["kind"], workflow_id=stale_workflow_id)
    artifact_path(stale_wf, state_key=case["state_key"], path_key=case["artifact_path_key"]).write_text("stale terminal material\n", encoding="utf-8")
    stale_result = run_fail(
        "reserve-delivery",
        "--workflow-id",
        stale_workflow_id,
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "must not send stale terminal artifact",
        "--final-status",
        json.dumps(stale_wf["final_status"]),
        state_root=state_root,
    )
    assert_true(stale_result["send_authorized"] is False, f"{case['kind']} reserve-delivery should reject stale material")
    assert_true("artifact hash is stale" in stale_result["error"], f"{case['kind']} stale material should be hash-checked")


def assert_reserve_rejects_non_positive_lease(state_root: Path, case: dict[str, str]) -> None:
    workflow_id = f"{case['kind']}-terminal-positive-lease"
    wf = finalize_mode(state_root, kind=case["kind"], workflow_id=workflow_id)
    for lease_seconds in ("0", "-1"):
        result = run_fail(
            "reserve-delivery",
            "--workflow-id",
            workflow_id,
            "--terminal-status",
            "completed",
            "--visible-delivery",
            VISIBLE_DELIVERY,
            "--summary",
            "must not send with invalid lease",
            "--final-status",
            json.dumps(wf["final_status"]),
            "--lease-seconds",
            lease_seconds,
            state_root=state_root,
        )
        assert_true("lease-seconds must be positive" in result["error"], f"{case['kind']} reserve-delivery should reject invalid lease")
    assert_true(
        [event["event_type"] for event in events(state_root, workflow_id)].count("delivery_reserved") == 0,
        f"{case['kind']} invalid lease should not append delivery_reserved",
    )


def assert_report_proof_is_post_send_identity(state_root: Path, case: dict[str, str]) -> None:
    workflow_id = f"{case['kind']}-terminal-post-send-proof"
    wf = finalize_mode(state_root, kind=case["kind"], workflow_id=workflow_id)
    reservation = reserve_delivery(state_root, wf)
    artifact_path(wf, state_key=case["state_key"], path_key=case["artifact_path_key"]).unlink()

    proof = run(
        "report-proof",
        "--workflow-id",
        workflow_id,
        "--reservation-id",
        reservation["reservation_id"],
        "--delivery-message-id",
        f"telegram-{case['kind']}-proof",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    duplicate = run(
        "report-proof",
        "--workflow-id",
        workflow_id,
        "--reservation-id",
        reservation["reservation_id"],
        "--delivery-message-id",
        f"telegram-{case['kind']}-proof",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    assert_true(proof["proof"]["event_id"] == duplicate["proof"]["event_id"], f"{case['kind']} duplicate report-proof should reconcile")
    assert_true(
        [event["event_type"] for event in events(state_root, workflow_id)].count("report_proof") == 1,
        f"{case['kind']} duplicate report-proof should not append duplicate proof events",
    )

    wrong_reservation = run_fail(
        "report-proof",
        "--workflow-id",
        workflow_id,
        "--reservation-id",
        "wrong-reservation",
        "--delivery-message-id",
        f"telegram-{case['kind']}-proof",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    assert_true("different report proof" in wrong_reservation["error"], f"{case['kind']} report-proof should bind reservation identity")

    reported = run(
        "complete-reported",
        "--workflow-id",
        workflow_id,
        "--reservation-id",
        reservation["reservation_id"],
        "--delivery-message-id",
        f"telegram-{case['kind']}-proof",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    assert_true(reported["status"] == "reported", f"{case['kind']} complete-reported should allow post-send proof after material deletion")


def assert_terminal_checkpoint_generic_evidence_is_enforced(state_root: Path) -> None:
    workflow_id = "conv-terminal-generic-evidence-drift"
    run(
        "start",
        "--kind",
        "conv",
        "--text",
        "Generic terminal evidence fixture",
        "--workflow-id",
        workflow_id,
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    terminal_evidence = {
        "evidence_key": "conv-terminal-generic-evidence",
        "kind": "contract",
        "summary": "Generic terminal evidence carried through checkpoint payload evidence.",
        "artifact_refs": [],
    }
    final_status = {"result": "pass", "stop_reason": "evidence_sufficient"}
    run(
        "checkpoint",
        "--workflow-id",
        workflow_id,
        "--checkpoint-type",
        "terminal",
        "--state-update",
        json.dumps(
            {
                "checkpoint_type": "terminal",
                "status_after": "completed_unreported",
                "phase_after": "terminal",
                "cursor_before": "baseline",
                "cursor_after": "baseline",
                "event_type": "complete",
                "worklog_block_kind": "terminal_summary",
                "step_result": "terminal",
                "residuals": {},
                "mode_state_update": {
                    "final_report_artifact_id": "conv-terminal-generic-report",
                    "final_report_artifact_path": "artifacts/conv-terminal-generic-report.md",
                    "target": "Generic terminal evidence fixture",
                    "max_rounds": 1,
                    "round_count": 1,
                    "rounds": [
                        {
                            "round_index": 1,
                            "target_ref": "Generic terminal evidence fixture",
                            "original_target_gate": "within_original_target",
                            "delta_gate": "no_delta",
                            "findings": [],
                            "material_changes": False,
                            "follow_up_required": False,
                            "evidence_sufficient": True,
                            "summary": "Generic evidence fixture reached terminal state.",
                        }
                    ],
                    "stop_condition": "evidence_sufficient",
                    "stop_reason": "Generic evidence fixture has checkpoint evidence.",
                    "explicit_stop_proof": "Checkpoint evidence is supplied through the generic --evidence path.",
                    "material_change_accepted": False,
                    "follow_up_required": False,
                    "evidence_sufficient": True,
                    "residuals": {},
                    "final_report_summary": "Generic checkpoint evidence contract fixture.",
                },
                "final_status": final_status,
            }
        ),
        "--summary",
        "complete with generic checkpoint evidence",
        "--evidence",
        json.dumps(terminal_evidence),
        state_root=state_root,
    )
    wf = workflow(state_root, workflow_id)
    wf["verification"]["evidence"] = []
    write_workflow(state_root, workflow_id, wf)
    result = run_fail("validate", "--workflow-id", workflow_id, state_root=state_root)
    assert_true(
        "verification evidence must match checkpoint-backed terminal evidence sequence" in result["error"],
        "generic terminal checkpoint evidence should remain exact-match enforced",
    )


def assert_terminal_checkpoint_requires_mode_state_snapshot(state_root: Path) -> None:
    workflow_id = "conv-terminal-missing-mode-state"
    run(
        "start",
        "--kind",
        "conv",
        "--text",
        "Generic terminal mode state fixture",
        "--workflow-id",
        workflow_id,
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    terminal_evidence = {
        "evidence_key": "conv-terminal-mode-state",
        "kind": "contract",
        "summary": "Terminal evidence for missing mode state fixture.",
        "artifact_refs": [],
    }
    run(
        "checkpoint",
        "--workflow-id",
        workflow_id,
        "--checkpoint-type",
        "terminal",
        "--state-update",
        json.dumps(
            {
                "checkpoint_type": "terminal",
                "status_after": "completed_unreported",
                "phase_after": "terminal",
                "cursor_before": "baseline",
                "cursor_after": "baseline",
                "event_type": "complete",
                "worklog_block_kind": "terminal_summary",
                "step_result": "terminal",
                "residuals": {},
                "terminal_evidence": terminal_evidence,
                "final_status": {"result": "pass"},
            }
        ),
        "--summary",
        "complete without terminal mode state snapshot",
        state_root=state_root,
    )
    result = run_fail("validate", "--workflow-id", workflow_id, state_root=state_root)
    assert_true(
        "terminal checkpoint requires conv_state snapshot" in result["error"],
        "terminal checkpoint should require active mode state snapshot",
    )


def assert_terminal_checkpoint_replaces_mode_state_snapshot(state_root: Path) -> None:
    workflow_id = "goal-terminal-mode-state-replacement"
    run(
        "start",
        "--kind",
        "goal",
        "--text",
        "Generic terminal mode state replacement fixture",
        "--workflow-id",
        workflow_id,
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    wf = workflow(state_root, workflow_id)
    wf["goal_state"] = {"stale_patch_key": "must not survive terminal snapshot"}
    write_workflow(state_root, workflow_id, wf)
    record = build_goal_record(wf)
    plan_path = state_root / "workflows" / workflow_id / "artifacts" / "goal-plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(render_goal_plan(record), encoding="utf-8")
    artifact = run(
        "artifact",
        "--workflow-id",
        workflow_id,
        "--artifact-id",
        "goal-promoted-plan",
        "--kind",
        "plan",
        "--path",
        str(plan_path),
        state_root=state_root,
    )["artifact"]
    terminal_snapshot = record.as_state(
        artifact_id=artifact["artifact_id"],
        artifact_path=artifact["path"],
        artifact_hash=artifact["sha256"],
    )
    terminal_evidence = {
        "evidence_key": "goal-terminal-mode-state-replacement",
        "kind": "artifact",
        "summary": "Terminal evidence for mode state replacement.",
        "artifact_refs": ["goal-promoted-plan"],
    }
    terminal_snapshot = attach_phase5a_evidence_contract(
        "goal",
        workflow=workflow(state_root, workflow_id),
        state=terminal_snapshot,
        terminal_evidence=terminal_evidence,
    )
    run(
        "event",
        "--workflow-id",
        workflow_id,
        "--type",
        "plan_accepted",
        "--event-id",
        "evt-goal-terminal-mode-state-plan-accepted",
        "--payload",
        json.dumps(terminal_snapshot["plan_accepted"]),
        state_root=state_root,
    )
    run(
        "checkpoint",
        "--workflow-id",
        workflow_id,
        "--checkpoint-type",
        "terminal",
        "--state-update",
        json.dumps(
            {
                "checkpoint_type": "terminal",
                "status_after": "completed_unreported",
                "phase_after": "terminal",
                "cursor_before": "objective-gate",
                "cursor_after": "objective-gate",
                "event_type": "complete",
                "worklog_block_kind": "terminal_summary",
                "step_result": "terminal",
                "residuals": terminal_snapshot["residuals"],
                "mode_state_update": terminal_snapshot,
                "terminal_evidence": terminal_evidence,
                "final_status": {"result": "pass_with_risks", "residuals": terminal_snapshot["residuals"]},
            }
        ),
        "--summary",
        "complete with exact terminal mode state snapshot",
        state_root=state_root,
    )
    terminal_workflow = workflow(state_root, workflow_id)
    assert_true(terminal_workflow["goal_state"] == terminal_snapshot, "terminal mode_state_update should replace stale mode state")
    run("validate", "--workflow-id", workflow_id, state_root=state_root)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="converge-terminal-finalization-smoke-") as tmp:
        state_root = Path(tmp)
        for case in MODE_CASES:
            assert_terminal_final_status_exact_match(state_root, case)
            assert_terminal_mode_state_exact_match(state_root, case)
            assert_checkpoint_backed_evidence_sequence(state_root, case)
            assert_reserve_validates_terminal_material_before_send(state_root, case)
            assert_reserve_rejects_non_positive_lease(state_root, case)
            assert_report_proof_is_post_send_identity(state_root, case)
        assert_terminal_checkpoint_generic_evidence_is_enforced(state_root)
        assert_terminal_checkpoint_requires_mode_state_snapshot(state_root)
        assert_terminal_checkpoint_replaces_mode_state_snapshot(state_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
