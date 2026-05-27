#!/usr/bin/env python3
"""Smoke coverage for C4 goal mode."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


try:
    from smoke_helpers import VISIBLE_DELIVERY, assert_true, events, run, run_fail, workflow, write_workflow
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import VISIBLE_DELIVERY, assert_true, events, run, run_fail, workflow, write_workflow
from converge.modes.goal import build_goal_record, render_goal_plan, validate_goal_state  # noqa: E402


def finalize_goal(state_root: Path, *, workflow_id: str, text: str) -> dict[str, Any]:
    return run(
        "goal",
        "--text",
        f"plan-only {text}",
        "--workflow-id",
        workflow_id,
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]


def assert_default_goal_contract(state_root: Path) -> None:
    text = "Implement C4 goal mode behavior"
    wf = finalize_goal(state_root, workflow_id="goal-c4-smoke", text=text)
    goal_state = wf["goal_state"]
    assert_true(wf["kind"] == "goal", "goal command should create a goal workflow")
    assert_true(wf["status"] == "completed_unreported", "goal mode should stop at terminal unreported")
    assert_true(isinstance(wf["continuation_plan"], dict), "goal mode should preserve continuation metadata")
    assert_true(len(wf["continuation_plan"]["steps"]) == 3, "goal mode should create a durable slice queue")
    assert_true(
        [step["step_id"] for step in wf["continuation_plan"]["steps"]]
        == [item["step_id"] for item in goal_state["slice_queue"]],
        "goal_state slice_queue should mirror continuation_plan.steps",
    )
    assert_true(
        {item["status"] for item in goal_state["slice_queue"]} == {"pending"},
        "goal_state slice_queue should not mark unexecuted continuation slices satisfied",
    )
    assert_true(
        wf["continuation_plan"]["rolling_state"]["current_resume_cursor"] == "objective-gate",
        "goal terminal plan artifact should preserve the durable queue cursor",
    )
    assert_true(goal_state["plan_accepted"]["objective"] == goal_state["objective"], "plan_accepted objective should bind goal state")
    assert_true(goal_state["plan_accepted"]["success_criteria"] == goal_state["success_criteria"], "plan_accepted criteria should bind goal state")
    assert_true(goal_state["evidence_completion_check"]["complete"] is True, "goal terminal success should require completed evidence")
    assert_true(goal_state["plan_artifact_promotion"]["promoted"] is True, "goal should promote a plan artifact")
    assert_true(
        {item["kind"] for item in goal_state["child_workflow_refs"]} == {"verify", "conv"},
        "goal should carry child workflow reference fields",
    )
    assert_true(wf["final_status"]["result"] == "pass_with_risks", "goal should preserve deferred child execution risk")
    assert_true(wf["verification"]["evidence"][-1]["artifact_refs"] == ["goal-promoted-plan"], "terminal evidence should reference goal plan")
    assert_true(wf["next_safe_action"]["action_type"] == "report_terminal_status", "goal terminal state should require report flow")
    goal_events = events(state_root, "goal-c4-smoke")
    assert_true(
        [event["event_type"] for event in goal_events] == ["start", "artifact", "plan_accepted", "complete"],
        "goal should record plan_accepted before terminal complete",
    )
    assert_true(
        goal_events[2]["payload"] == goal_state["plan_accepted"],
        "goal plan_accepted event should match accepted goal state",
    )
    assert_true(
        goal_events[2]["event_id"] == "evt-plan-accepted-goal-c4-smoke",
        "goal plan_accepted event should use a deterministic idempotency key",
    )

    artifact_path = Path(goal_state["final_plan_artifact_path"])
    assert_true(artifact_path.is_file(), "goal plan artifact should be materialized")
    assert_true(artifact_path.read_text(encoding="utf-8") == render_goal_plan(build_goal_record(wf)), "goal plan artifact should match rendered goal state")
    run("validate", "--workflow-id", "goal-c4-smoke", state_root=state_root)

    missing_event = json.loads(json.dumps(wf))
    (state_root / "workflows" / "goal-c4-smoke" / "events.jsonl").write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in goal_events if event["event_type"] != "plan_accepted") + "\n",
        encoding="utf-8",
    )
    write_workflow(state_root, "goal-c4-smoke", missing_event)
    result = run_fail("validate", "--workflow-id", "goal-c4-smoke", state_root=state_root)
    assert_true("matching plan_accepted event" in result["error"], "terminal goal should require plan_accepted event")
    (state_root / "workflows" / "goal-c4-smoke" / "events.jsonl").write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in goal_events) + "\n",
        encoding="utf-8",
    )

    reservation = run(
        "reserve-delivery",
        "--workflow-id",
        "goal-c4-smoke",
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "reserve final goal delivery",
        "--final-status",
        json.dumps(wf["final_status"]),
        state_root=state_root,
    )
    assert_true(reservation["send_authorized"] is True, "reserve-delivery should authorize terminal goal report")
    reported = run(
        "complete-reported",
        "--workflow-id",
        "goal-c4-smoke",
        "--reservation-id",
        reservation["reservation_id"],
        "--delivery-message-id",
        "telegram-message-goal",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    assert_true(reported["status"] == "reported", "complete-reported should mark goal workflow reported")


def assert_execution_required_goal_blocks_planned_children(state_root: Path) -> None:
    wf = run(
        "goal",
        "--text",
        "Implement execution-required goal workflow",
        "--workflow-id",
        "goal-execution-required-blocked",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    assert_true(wf["status"] == "failed_unreported", "execution-required goal should fail closed")
    assert_true(wf["final_status"]["result"] == "blocked", "execution-required goal should be blocked")
    assert_true(
        wf["final_status"]["stop_reason"] == "blocked_child_workflows_not_run",
        "goal should block on planned child refs",
    )
    assert_true(
        wf["goal_state"]["execution_required"] is True and wf["goal_state"]["execution_performed"] is False,
        "goal should record execution truth markers",
    )
    assert_true(
        wf["goal_state"]["execution_blocked"] is True,
        "goal blocked state should record execution_blocked",
    )
    assert_true(
        [event["event_type"] for event in events(state_root, "goal-execution-required-blocked")] == ["start", "artifact", "plan_accepted", "fail"],
        "execution-required goal should fail terminally instead of completing",
    )
    run("validate", "--workflow-id", "goal-execution-required-blocked", state_root=state_root)


def assert_completion_criteria_plan_only_wording_does_not_downgrade_goal(state_root: Path) -> None:
    wf = run(
        "goal",
        "--text",
        (
            "Converge execution parity Phase 1 구현-검증-수렴 진행해줘. "
            "목표는 Phase 1만 완료하는 것이다. "
            "완료 기준: plan-only 케이스는 execution_required=false로 유지된다."
        ),
        "--workflow-id",
        "goal-plan-only-criteria-still-execution-required",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    assert_true(
        wf["status"] == "failed_unreported",
        "goal mentioning a plan-only test case should still fail closed",
    )
    assert_true(
        wf["goal_state"]["execution_required"] is True,
        "plan-only wording in completion criteria must not downgrade the whole goal",
    )
    assert_true(
        wf["final_status"]["result"] == "blocked",
        "execution-required goal with only planned child refs should be blocked",
    )
    run("validate", "--workflow-id", "goal-plan-only-criteria-still-execution-required", state_root=state_root)


def assert_plan_accepted_requires_objective(state_root: Path) -> None:
    run("start", "--kind", "goal", "--text", "Manual acceptance validation", "--workflow-id", "goal-acceptance-validation", state_root=state_root)
    payload = {
        "objective": "",
        "non_goals": [],
        "success_criteria": ["accepted criteria"],
        "assumptions": [],
        "approval_boundaries": [],
        "plan_artifact_ref": "goal-promoted-plan",
        "plan_artifact_hash": "sha256-demo",
        "source_ref": "goal-acceptance-validation",
        "accepted_at": "2026-05-25T00:00:00Z",
    }
    result = run_fail(
        "event",
        "--workflow-id",
        "goal-acceptance-validation",
        "--type",
        "plan_accepted",
        "--event-id",
        "evt-empty-objective-plan-accepted",
        "--payload",
        json.dumps(payload),
        state_root=state_root,
    )
    assert_true("objective must be a non-empty string" in result["error"], "plan_accepted should reject empty objective")


def assert_goal_retry_reuses_existing_plan_accepted(state_root: Path) -> None:
    workflow_id = "goal-retry-plan-accepted"
    run("start", "--kind", "goal", "--text", "plan-only Retry accepted plan", "--workflow-id", workflow_id, state_root=state_root)
    wf = workflow(state_root, workflow_id)
    record = build_goal_record(wf, accepted_at="2026-05-25T00:00:00Z")
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
    accepted_state = record.as_state(
        artifact_id=artifact["artifact_id"],
        artifact_path=artifact["path"],
        artifact_hash=artifact["sha256"],
    )
    run(
        "event",
        "--workflow-id",
        workflow_id,
        "--type",
        "plan_accepted",
        "--event-id",
        "evt-retry-plan-accepted",
        "--payload",
        json.dumps(accepted_state["plan_accepted"]),
        state_root=state_root,
    )
    retry = run("goal", "--text", "plan-only Retry accepted plan", "--workflow-id", workflow_id, state_root=state_root)["workflow"]
    assert_true(retry["status"] == "completed_unreported", "goal retry should complete from existing plan_accepted event")
    assert_true(
        retry["goal_state"]["plan_accepted"] == accepted_state["plan_accepted"],
        "goal retry should reuse the durable plan_accepted payload",
    )


def assert_goal_rejects_duplicate_preterminal_acceptance(state_root: Path) -> None:
    workflow_id = "goal-duplicate-preterminal-accepted"
    run("start", "--kind", "goal", "--text", "plan-only Duplicate accepted plan", "--workflow-id", workflow_id, state_root=state_root)
    wf = workflow(state_root, workflow_id)
    record = build_goal_record(wf, accepted_at="2026-05-25T00:00:00Z")
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
    accepted_state = record.as_state(
        artifact_id=artifact["artifact_id"],
        artifact_path=artifact["path"],
        artifact_hash=artifact["sha256"],
    )
    for event_id in ("evt-duplicate-plan-accepted-a", "evt-duplicate-plan-accepted-b"):
        run(
            "event",
            "--workflow-id",
            workflow_id,
            "--type",
            "plan_accepted",
            "--event-id",
            event_id,
            "--payload",
            json.dumps(accepted_state["plan_accepted"]),
            state_root=state_root,
        )
    result = run_fail("goal", "--text", "plan-only Duplicate accepted plan", "--workflow-id", workflow_id, state_root=state_root)
    assert_true("duplicated" in result["error"], "goal should reject duplicate preterminal plan_accepted events before terminalizing")
    assert_true(workflow(state_root, workflow_id)["status"] == "running", "duplicate acceptance must not create terminal goal state")


def assert_goal_rejects_conflicting_acceptance_without_dirtying_artifact(state_root: Path) -> None:
    workflow_id = "goal-conflicting-preterminal-accepted"
    run("start", "--kind", "goal", "--text", "plan-only Conflicting accepted plan", "--workflow-id", workflow_id, state_root=state_root)
    wf = workflow(state_root, workflow_id)
    record = build_goal_record(wf, accepted_at="2026-05-25T00:00:00Z")
    conflicting_payload = dict(record.plan_accepted)
    conflicting_payload["objective"] = "conflicting objective"
    run(
        "event",
        "--workflow-id",
        workflow_id,
        "--type",
        "plan_accepted",
        "--event-id",
        "evt-conflicting-plan-accepted",
        "--payload",
        json.dumps(conflicting_payload),
        state_root=state_root,
    )
    result = run_fail("goal", "--text", "plan-only Conflicting accepted plan", "--workflow-id", workflow_id, state_root=state_root)
    assert_true("does not match current accepted plan" in result["error"], "goal should reject conflicting acceptance before artifact registration")
    failed = workflow(state_root, workflow_id)
    assert_true(failed["status"] == "running", "conflicting acceptance must not create terminal goal state")
    assert_true(failed["artifacts"] == [], "conflicting acceptance must not leave a partial goal artifact")
    assert_true([event["event_type"] for event in events(state_root, workflow_id)] == ["start", "plan_accepted"], "failed goal retry must not append artifact or terminal events")
    run("validate", "--workflow-id", workflow_id, state_root=state_root)


def assert_terminal_goal_rejects_late_or_conflicting_acceptance(state_root: Path) -> None:
    workflow_id = "goal-terminal-acceptance-guard"
    terminal = finalize_goal(state_root, workflow_id=workflow_id, text="Terminal acceptance guard")
    conflicting_payload = json.loads(json.dumps(terminal["goal_state"]["plan_accepted"]))
    conflicting_payload["objective"] = "conflicting objective"

    result = run_fail(
        "event",
        "--workflow-id",
        workflow_id,
        "--type",
        "plan_accepted",
        "--event-id",
        "evt-late-conflicting-plan-accepted",
        "--payload",
        json.dumps(conflicting_payload),
        state_root=state_root,
    )
    assert_true("terminal workflow status" in result["error"], "manual plan_accepted should not append after terminal goal")

    goal_events = events(state_root, workflow_id)
    late_conflict_event = json.loads(json.dumps(next(event for event in goal_events if event["event_type"] == "plan_accepted")))
    late_conflict_event["event_id"] = "evt-corrupt-conflicting-plan-accepted"
    late_conflict_event["payload"] = conflicting_payload
    (state_root / "workflows" / workflow_id / "events.jsonl").write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in [*goal_events, late_conflict_event]) + "\n",
        encoding="utf-8",
    )
    result = run_fail("validate", "--workflow-id", workflow_id, state_root=state_root)
    assert_true("conflicting plan_accepted event" in result["error"], "terminal goal should reject conflicting acceptance history")


def assert_goal_integrity_rejects_drift(state_root: Path) -> None:
    base = workflow(state_root, "goal-c4-smoke")

    missing_acceptance = json.loads(json.dumps(base))
    missing_acceptance["goal_state"]["plan_accepted"].pop("success_criteria")
    write_workflow(state_root, "goal-c4-smoke", missing_acceptance)
    result = run_fail("validate", "--workflow-id", "goal-c4-smoke", state_root=state_root)
    assert_true(
        "goal_state must match terminal checkpoint goal_state" in result["error"]
        or "payload missing required fields" in result["error"],
        "goal should reject plan_accepted drift",
    )
    write_workflow(state_root, "goal-c4-smoke", base)

    incomplete_evidence = json.loads(json.dumps(base["goal_state"]))
    incomplete_evidence["evidence_completion_check"]["complete"] = False
    try:
        validate_goal_state(incomplete_evidence, workflow=base, terminal=True, final_status=base["final_status"])
    except ValueError as exc:
        assert_true("complete evidence completion" in str(exc), "goal should reject incomplete evidence")
    else:
        raise AssertionError("goal should reject incomplete evidence")

    queue_drift = json.loads(json.dumps(base))
    queue_drift["goal_state"]["slice_queue"].pop()
    write_workflow(state_root, "goal-c4-smoke", queue_drift)
    result = run_fail("validate", "--workflow-id", "goal-c4-smoke", state_root=state_root)
    assert_true(
        "goal_state must match terminal checkpoint goal_state" in result["error"]
        or "slice_queue must match continuation_plan.steps" in result["error"],
        "goal should reject slice queue drift",
    )
    write_workflow(state_root, "goal-c4-smoke", base)

    bad_child = json.loads(json.dumps(base["goal_state"]))
    bad_child["child_workflow_refs"][0]["kind"] = "plan"
    try:
        validate_goal_state(bad_child, workflow=base, terminal=True, final_status=base["final_status"])
    except ValueError as exc:
        assert_true("verify or conv" in str(exc), "goal should reject invalid child workflow ref kind")
    else:
        raise AssertionError("goal should reject invalid child workflow ref kind")


def assert_reserve_validates_goal_terminal_material_before_send(state_root: Path) -> None:
    missing = finalize_goal(state_root, workflow_id="goal-terminal-material-missing", text="missing terminal material")
    Path(missing["goal_state"]["final_plan_artifact_path"]).unlink()
    result = run_fail(
        "reserve-delivery",
        "--workflow-id",
        "goal-terminal-material-missing",
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "must not send missing goal plan",
        "--final-status",
        json.dumps(missing["final_status"]),
        state_root=state_root,
    )
    assert_true(result["send_authorized"] is False, "goal reserve-delivery should reject missing terminal material")
    assert_true(result["reason"] == "validation_error", "goal missing material should require validation reconciliation")


def assert_terminal_goal_requires_valid_goal_state(state_root: Path) -> None:
    workflow_id = "goal-malformed-terminal-state"
    run("start", "--kind", "goal", "--text", "Malformed terminal state guard", "--workflow-id", workflow_id, state_root=state_root)
    before_events = events(state_root, workflow_id)
    result = run_fail(
        "checkpoint",
        "--workflow-id",
        workflow_id,
        "--checkpoint-type",
        "terminal",
        "--summary",
        "bad malformed terminal goal",
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
                "residuals": {},
                "mode_state_update": {"objective": ""},
                "terminal_evidence": {
                    "evidence_key": "malformed-goal-terminal-state",
                    "kind": "smoke",
                    "summary": "malformed goal state should not validate",
                    "artifact_refs": [],
                },
                "final_status": {"result": "pass", "residuals": {}},
            }
        ),
        state_root=state_root,
    )
    assert_true("goal_state is missing required fields" in result["error"], "terminal goal must not validate malformed goal_state")
    failed = workflow(state_root, workflow_id)
    assert_true(failed["status"] == "running", "invalid terminal goal checkpoint must not mutate workflow status")
    assert_true(events(state_root, workflow_id) == before_events, "invalid terminal goal checkpoint must not append events")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="converge-goal-mode-smoke-") as tmp:
        state_root = Path(tmp)
        assert_default_goal_contract(state_root)
        assert_execution_required_goal_blocks_planned_children(state_root)
        assert_completion_criteria_plan_only_wording_does_not_downgrade_goal(state_root)
        assert_plan_accepted_requires_objective(state_root)
        assert_goal_retry_reuses_existing_plan_accepted(state_root)
        assert_goal_rejects_duplicate_preterminal_acceptance(state_root)
        assert_goal_rejects_conflicting_acceptance_without_dirtying_artifact(state_root)
        assert_terminal_goal_rejects_late_or_conflicting_acceptance(state_root)
        assert_goal_integrity_rejects_drift(state_root)
        assert_reserve_validates_goal_terminal_material_before_send(state_root)
        assert_terminal_goal_requires_valid_goal_state(state_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
