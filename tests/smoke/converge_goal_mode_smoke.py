#!/usr/bin/env python3
"""Smoke coverage for C4 goal mode."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


try:
    from smoke_helpers import VISIBLE_DELIVERY, assert_phase5a_contract, assert_phase5a_accepted_change_stale_rejected, assert_phase5a_freshness_rejected, assert_phase5a_missing_gate_rejected, assert_phase5a_stale_hash_rejected, assert_phase5a_terminal_status_rejected, assert_true, events, run, run_fail, workflow, write_events, write_workflow
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import VISIBLE_DELIVERY, assert_phase5a_contract, assert_phase5a_accepted_change_stale_rejected, assert_phase5a_freshness_rejected, assert_phase5a_missing_gate_rejected, assert_phase5a_stale_hash_rejected, assert_phase5a_terminal_status_rejected, assert_true, events, run, run_fail, workflow, write_events, write_workflow
from converge.modes.goal import GoalRecord, _child_workflow_id, build_goal_record, render_goal_plan, validate_goal_state  # noqa: E402
from converge.artifacts import sha256_file  # noqa: E402


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


def report_workflow(state_root: Path, workflow_id: str) -> dict[str, Any]:
    wf = workflow(state_root, workflow_id)
    reservation = run(
        "reserve-delivery",
        "--workflow-id",
        workflow_id,
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        f"reserve delivery for {workflow_id}",
        "--final-status",
        json.dumps(wf["final_status"]),
        state_root=state_root,
    )
    return run(
        "complete-reported",
        "--workflow-id",
        workflow_id,
        "--reservation-id",
        reservation["reservation_id"],
        "--delivery-message-id",
        f"telegram-message-{workflow_id}",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )


def persist_goal_state(state_root: Path, workflow_id: str, wf: dict[str, Any]) -> None:
    write_workflow(state_root, workflow_id, wf)
    records = events(state_root, workflow_id)
    for record in reversed(records):
        payload = record.get("payload") or {}
        state_update = payload.get("state_update") if isinstance(payload, dict) else None
        if isinstance(state_update, dict) and isinstance(state_update.get("mode_state_update"), dict):
            state_update["mode_state_update"] = wf["goal_state"]
            write_events(state_root, workflow_id, records)
            return
    raise AssertionError("goal terminal state_update not found")


def set_child_collected_event_status(state_root: Path, workflow_id: str, child_id: str, terminal_status: str) -> None:
    records = events(state_root, workflow_id)
    for record in records:
        payload = record.get("payload") or {}
        if record.get("event_type") == "child_workflow_collected" and payload.get("child_workflow_id") == child_id:
            payload["terminal_status"] = terminal_status
    write_events(state_root, workflow_id, records)


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
        wf["final_status"]["stop_reason"] == "blocked_child_workflow_failed",
        "goal should block when required child workflows fail",
    )
    assert_true(
        wf["goal_state"]["execution_required"] is True and wf["goal_state"]["execution_performed"] is True,
        "goal should record child workflow execution truth markers",
    )
    assert_true(
        wf["goal_state"]["synthetic_report"] is False,
        "goal child workflow collection should clear synthetic_report",
    )
    assert_true(
        {item["status"] for item in wf["goal_state"]["child_workflow_refs"]} == {"blocked"},
        "goal should record blocked child workflow refs",
    )
    assert_true(
        len(wf["child_workflow_ids"]) == 2,
        "goal should create real child workflows instead of planned refs",
    )
    goal_events = [event["event_type"] for event in events(state_root, "goal-execution-required-blocked")]
    assert_true(
        goal_events.count("child_creation_intent") == 2,
        "execution-required goal should record child creation intent events",
    )
    assert_true(
        goal_events.count("child_workflow_created") == 2 and goal_events.count("child_workflow_collected") == 2,
        "execution-required goal should record child creation and collection events",
    )
    assert_true(
        goal_events[-3:] == ["artifact", "plan_accepted", "fail"],
        "execution-required goal should fail terminally instead of completing",
    )
    run("validate", "--workflow-id", "goal-execution-required-blocked", state_root=state_root)


def assert_execution_required_goal_collects_child_evidence(state_root: Path) -> None:
    target = state_root / "phase3-goal-target.txt"
    target.write_text("phase 3 goal child evidence target\n", encoding="utf-8")
    wf = run(
        "goal",
        "--text",
        f"Implement execution-required goal workflow for {target}",
        "--workflow-id",
        "goal-execution-required-real-children",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    goal_state = wf["goal_state"]
    assert_true(wf["status"] == "completed_unreported", "goal should complete after collecting passing children")
    assert_true(wf["final_status"]["result"] == "pass_with_risks", "Phase 3 goal should carry scoped child-report residuals")
    assert_true(goal_state["execution_required"] is True, "goal should remain execution-required")
    assert_true(goal_state["execution_performed"] is True, "goal should mark execution performed from collected children")
    assert_true(goal_state["synthetic_report"] is False, "goal child evidence should clear synthetic_report")
    assert_true(goal_state["execution_capability"] == "child_workflows", "goal should record child_workflows capability")
    assert_true(sorted(goal_state["execution_evidence_refs"]) == sorted(wf["child_workflow_ids"]), "goal evidence refs should match real child ids")
    assert_true({item["status"] for item in goal_state["child_workflow_refs"]} == {"completed"}, "child refs should be completed")
    assert_true({item["kind"] for item in goal_state["child_workflow_refs"]} == {"verify", "conv"}, "goal should create verify and conv children")
    assert_true(
        sorted(goal_state["child_residual_rollup"]["required_child_workflow_ids"]) == sorted(wf["child_workflow_ids"]),
        "Phase 5B should record required child residual rollup ids",
    )
    assert_true(
        sorted(item["workflow_id"] for item in goal_state["child_residual_rollup"]["children"]) == sorted(wf["child_workflow_ids"]),
        "Phase 5B should roll each child into parent state",
    )
    assert_true(
        {item["to_mode"] for item in goal_state["child_delivery_mode_transitions"]} == {"parent_summary_only"},
        "Phase 5B should make parent_summary_only delivery transitions explicit",
    )
    assert_true(
        {item["delivery_mode"] for item in goal_state["child_workflow_refs"]} == {"parent_summary_only"},
        "Phase 5B child refs should carry explicit delivery mode",
    )
    assert_true(
        goal_state["duplicate_report_guard"]["parent_must_not_duplicate_child_reports"] is True,
        "Phase 5B should attach duplicate child report guard",
    )
    for child_id in wf["child_workflow_ids"]:
        child = workflow(state_root, child_id)
        assert_true(child["parent_workflow_id"] == wf["workflow_id"], "child should point back to parent")
        assert_true(child["status"] == "completed_unreported", "child should reach terminal unreported")
        assert_true(child["final_status"]["result"] in {"pass", "pass_with_risks"}, "child terminal result should pass")
    goal_events = [event["event_type"] for event in events(state_root, "goal-execution-required-real-children")]
    assert_true(goal_events.count("child_creation_intent") == 2, "goal should record two child intent events")
    assert_true(goal_events.count("child_workflow_created") == 2, "goal should record two child creation events")
    assert_true(goal_events.count("child_workflow_collected") == 2, "goal should record two child collection events")
    assert_true(goal_events[-3:] == ["artifact", "plan_accepted", "complete"], "goal should complete after artifact and acceptance")
    assert_true(Path(goal_state["final_plan_artifact_path"]).read_text(encoding="utf-8") == render_goal_plan(_goal_record_from_state(goal_state)), "goal artifact should render from collected child state")
    run("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_phase5a_contract(wf, "goal_state")

    missing_rollup = json.loads(json.dumps(wf))
    del missing_rollup["goal_state"]["child_residual_rollup"]
    persist_goal_state(state_root, "goal-execution-required-real-children", missing_rollup)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("child_residual_rollup" in result["error"], "Phase 5B should reject missing child residual rollup")
    persist_goal_state(state_root, "goal-execution-required-real-children", wf)

    missing_transition = json.loads(json.dumps(wf))
    missing_transition["goal_state"]["child_delivery_mode_transitions"].pop()
    persist_goal_state(state_root, "goal-execution-required-real-children", missing_transition)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("child_delivery_mode" in result["error"], "Phase 5B should reject missing delivery mode transition")
    persist_goal_state(state_root, "goal-execution-required-real-children", wf)

    visible_without_proof = json.loads(json.dumps(wf))
    child_id = visible_without_proof["child_workflow_ids"][0]
    for item in visible_without_proof["goal_state"]["child_workflow_refs"]:
        if item["workflow_id"] == child_id:
            item["delivery_mode"] = "visible_child_report_required"
    for item in visible_without_proof["goal_state"]["child_delivery_mode_transitions"]:
        if item["workflow_id"] == child_id:
            item["to_mode"] = "visible_child_report_required"
            item["reason"] = "child visible report proof is required before parent reported completion"
    for item in visible_without_proof["goal_state"]["child_residual_rollup"]["children"]:
        if item["workflow_id"] == child_id:
            item["delivery_mode"] = "visible_child_report_required"
    persist_goal_state(state_root, "goal-execution-required-real-children", visible_without_proof)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("visible_child_report_required" in result["error"], "Phase 5B should reject visible-child mode without child proof")
    persist_goal_state(state_root, "goal-execution-required-real-children", wf)

    parent_summary_with_child_proof = json.loads(json.dumps(wf))
    parent_summary_with_child_proof["goal_state"]["child_workflow_refs"][0]["report_proof_ref"] = "child-proof-should-not-exist"
    parent_summary_with_child_proof["goal_state"]["child_residual_rollup"]["children"][0]["report_proof_ref"] = "child-proof-should-not-exist"
    persist_goal_state(state_root, "goal-execution-required-real-children", parent_summary_with_child_proof)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("parent_summary_only" in result["error"], "Phase 5B should reject child proof under parent_summary_only")
    persist_goal_state(state_root, "goal-execution-required-real-children", wf)

    duplicate_attempt = json.loads(json.dumps(wf))
    duplicate_attempt["goal_state"]["duplicate_report_guard"]["duplicate_child_report_attempts"] = [
        {"child_workflow_id": wf["child_workflow_ids"][0], "report_proof_ref": "duplicate-proof"}
    ]
    persist_goal_state(state_root, "goal-execution-required-real-children", duplicate_attempt)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("duplicate child visible report attempts" in result["error"], "Phase 5B should reject duplicate child report attempts")
    persist_goal_state(state_root, "goal-execution-required-real-children", wf)

    first_child_gate = f"child:{wf['child_workflow_ids'][0]}"
    assert_phase5a_missing_gate_rejected(
        state_root,
        "goal-execution-required-real-children",
        "goal_state",
        first_child_gate,
    )
    assert_phase5a_stale_hash_rejected(
        state_root,
        "goal-execution-required-real-children",
        "goal_state",
        "terminal:goal-promoted-plan",
    )
    assert_phase5a_freshness_rejected(state_root, "goal-execution-required-real-children", "goal_state")
    assert_phase5a_terminal_status_rejected(state_root, "goal-execution-required-real-children", "goal_state")
    assert_phase5a_accepted_change_stale_rejected(state_root, "goal-execution-required-real-children", "goal_state")

    missing_created = json.loads(json.dumps(wf))
    events_path = state_root / "workflows" / "goal-execution-required-real-children" / "events.jsonl"
    original_events = events_path.read_text(encoding="utf-8")
    without_created = "\n".join(
        line
        for line in original_events.splitlines()
        if not line.strip() or json.loads(line).get("event_type") != "child_workflow_created"
    )
    events_path.write_text(without_created + "\n", encoding="utf-8")
    write_workflow(state_root, "goal-execution-required-real-children", missing_created)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("child_workflow_created event" in result["error"], "goal should reject missing child creation evidence")
    events_path.write_text(original_events, encoding="utf-8")

    drifted_created = json.loads(json.dumps(wf))
    for event in events(state_root, "goal-execution-required-real-children"):
        if event["event_type"] == "child_workflow_created":
            event["payload"]["child_role"] = "conv" if event["payload"]["child_role"] == "verify" else "verify"
            lines = []
            for line in original_events.splitlines():
                parsed = json.loads(line)
                if parsed["event_id"] == event["event_id"]:
                    parsed = event
                lines.append(json.dumps(parsed, ensure_ascii=False, sort_keys=True))
            events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            break
    write_workflow(state_root, "goal-execution-required-real-children", drifted_created)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("child_workflow_created child_role" in result["error"], "goal should reject child creation role drift")
    events_path.write_text(original_events, encoding="utf-8")

    child = workflow(state_root, wf["child_workflow_ids"][0])
    child["parent_workflow_id"] = "different-parent"
    write_workflow(state_root, child["workflow_id"], child)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true(
        "parent_workflow_id" in result["error"] or "linked child workflows" in result["error"],
        "goal should reject child parent drift",
    )
    child["parent_workflow_id"] = wf["workflow_id"]
    write_workflow(state_root, child["workflow_id"], child)

    child["owner_session_key"] = "session:other"
    write_workflow(state_root, child["workflow_id"], child)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("owner_session_key" in result["error"], "goal should reject child owner/session drift")
    child["owner_session_key"] = wf["owner_session_key"]
    write_workflow(state_root, child["workflow_id"], child)

    child_events_path = state_root / "workflows" / child["workflow_id"] / "events.jsonl"
    original_child_events = child_events_path.read_text(encoding="utf-8")
    without_parent_linked = "\n".join(
        line
        for line in original_child_events.splitlines()
        if not line.strip() or json.loads(line).get("event_type") != "parent_linked"
    )
    child_events_path.write_text(without_parent_linked + "\n", encoding="utf-8")
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("parent_linked" in result["error"], "goal should reject missing child-side parent_linked evidence")
    child_events_path.write_text(original_child_events, encoding="utf-8")

    run(
        "start",
        "--kind",
        "verify",
        "--text",
        "extra omitted child",
        "--workflow-id",
        "goal-extra-omitted-child",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    extra_child = workflow(state_root, "goal-extra-omitted-child")
    extra_child["parent_workflow_id"] = wf["workflow_id"]
    write_workflow(state_root, "goal-extra-omitted-child", extra_child)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("linked child workflows" in result["error"], "goal should reject omitted linked child workflows")
    extra_child["parent_workflow_id"] = None
    write_workflow(state_root, "goal-extra-omitted-child", extra_child)

    duplicate = workflow(state_root, "goal-execution-required-real-children")
    first_ref = json.loads(json.dumps(duplicate["goal_state"]["child_workflow_refs"][0]))
    duplicate["goal_state"]["child_workflow_refs"] = [first_ref, json.loads(json.dumps(first_ref))]
    duplicate["goal_state"]["execution_evidence_refs"] = [first_ref["workflow_id"], first_ref["workflow_id"]]
    duplicate["child_workflow_ids"] = [first_ref["workflow_id"], first_ref["workflow_id"]]
    duplicate["goal_state"]["child_collection_status"]["required_child_workflow_ids"] = [first_ref["workflow_id"], first_ref["workflow_id"]]
    duplicate["goal_state"]["child_collection_status"]["collected_child_workflow_ids"] = [first_ref["workflow_id"], first_ref["workflow_id"]]
    duplicate["goal_state"]["child_collection_status"]["children"] = [
        json.loads(json.dumps(duplicate["goal_state"]["child_collection_status"]["children"][0])),
        json.loads(json.dumps(duplicate["goal_state"]["child_collection_status"]["children"][0])),
    ]
    duplicate_artifact_path = Path(duplicate["goal_state"]["final_plan_artifact_path"])
    duplicate_artifact_path.write_text(
        render_goal_plan(_goal_record_from_state(duplicate["goal_state"])),
        encoding="utf-8",
    )
    duplicate_hash = sha256_file(duplicate_artifact_path)
    duplicate["artifacts"][0]["sha256"] = duplicate_hash
    duplicate["goal_state"]["plan_artifact_promotion"]["plan_artifact_hash"] = duplicate_hash
    duplicate["goal_state"]["plan_accepted"]["plan_artifact_hash"] = duplicate_hash
    duplicate_event_lines = []
    for line in original_events.splitlines():
        event = json.loads(line)
        if event.get("event_type") == "complete":
            event["payload"]["state_update"]["mode_state_update"] = duplicate["goal_state"]
        duplicate_event_lines.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
    events_path.write_text("\n".join(duplicate_event_lines) + "\n", encoding="utf-8")
    write_workflow(state_root, "goal-execution-required-real-children", duplicate)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("unique" in result["error"], "goal should reject duplicate child workflow ids")
    events_path.write_text(original_events, encoding="utf-8")
    write_workflow(state_root, "goal-execution-required-real-children", wf)
    Path(goal_state["final_plan_artifact_path"]).write_text(render_goal_plan(_goal_record_from_state(goal_state)), encoding="utf-8")

    reservation = run(
        "reserve-delivery",
        "--workflow-id",
        "goal-execution-required-real-children",
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "reserve parent goal delivery",
        "--final-status",
        json.dumps(wf["final_status"]),
        state_root=state_root,
    )
    reported_parent = run(
        "complete-reported",
        "--workflow-id",
        "goal-execution-required-real-children",
        "--reservation-id",
        reservation["reservation_id"],
        "--delivery-message-id",
        "telegram-message-parent-goal",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    assert_true(
        reported_parent["status"] == "reported",
        "Phase 5B parent_summary_only should allow parent report while children remain terminal-unreported",
    )
    for child_id in wf["child_workflow_ids"]:
        child = workflow(state_root, child_id)
        assert_true(child["status"] == "completed_unreported", "parent_summary_only should not silently report children")
    result = run(
        "reserve-delivery",
        "--workflow-id",
        wf["child_workflow_ids"][0],
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "child report should be blocked after parent summary",
        "--final-status",
        json.dumps(workflow(state_root, wf["child_workflow_ids"][0])["final_status"]),
        state_root=state_root,
    )
    assert_true(
        result["send_authorized"] is False and result["reason"] == "duplicate_child_report_guard",
        "Phase 5B should block child visible reports after parent_summary_only parent report",
    )
    child = workflow(state_root, wf["child_workflow_ids"][0])
    child["status"] = "reported"
    child["phase"] = "reported"
    child.setdefault("visible_delivery_state", {})["reported"] = {
        "reservation_id": "forced-child-reservation",
        "delivery_message_id": "forced-child-message",
        "visible_delivery": VISIBLE_DELIVERY,
        "reported_at": "2026-01-01T00:00:00Z",
        "report_authority": "test",
        "source_of_truth": "test",
    }
    write_workflow(state_root, child["workflow_id"], child)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("parent_summary_only child must not be separately reported" in result["error"], "Phase 5B should detect forced child report drift")


def assert_phase5b_visible_child_mode_positive_path(state_root: Path) -> None:
    target = state_root / "phase5b-visible-child-target.txt"
    target.write_text("phase 5b visible child delivery target\n", encoding="utf-8")
    wf = run(
        "goal",
        "--text",
        f"Implement execution-required goal workflow for {target}",
        "--workflow-id",
        "goal-phase5b-visible-child",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    for child_id in wf["child_workflow_ids"]:
        reported_child = report_workflow(state_root, child_id)
        assert_true(reported_child["status"] == "reported", "visible_child_report_required setup should report child")
    wf = workflow(state_root, "goal-phase5b-visible-child")
    proof_refs = []
    for child_ref in wf["goal_state"]["child_workflow_refs"]:
        child = workflow(state_root, child_ref["workflow_id"])
        proof = child["visible_delivery_state"]["report_proof"]
        child_ref["terminal_status"] = "reported"
        child_ref["delivery_mode"] = "visible_child_report_required"
        child_ref["delivery_mode_reason"] = "child visible report proof is required before parent reported completion"
        child_ref["report_proof_ref"] = proof
        proof_refs.append(proof["delivery_message_id"])
        set_child_collected_event_status(state_root, wf["workflow_id"], child_ref["workflow_id"], "reported")
    for item in wf["goal_state"]["child_collection_status"]["children"]:
        child = workflow(state_root, item["workflow_id"])
        item["terminal_status"] = "reported"
        item["report_proof_ref"] = child["visible_delivery_state"]["report_proof"]
    for item in wf["goal_state"]["child_residual_rollup"]["children"]:
        child = workflow(state_root, item["workflow_id"])
        item["terminal_status"] = "reported"
        item["delivery_mode"] = "visible_child_report_required"
        item["report_proof_ref"] = child["visible_delivery_state"]["report_proof"]
    for item in wf["goal_state"]["child_delivery_mode_transitions"]:
        child = workflow(state_root, item["workflow_id"])
        item["to_mode"] = "visible_child_report_required"
        item["reason"] = "child visible report proof is required before parent reported completion"
        item["report_proof_ref"] = child["visible_delivery_state"]["report_proof"]
    wf["goal_state"]["duplicate_report_guard"]["child_report_proof_refs"] = sorted(proof_refs)
    persist_goal_state(state_root, wf["workflow_id"], wf)
    run("validate", "--workflow-id", wf["workflow_id"], state_root=state_root)
    reservation = run(
        "reserve-delivery",
        "--workflow-id",
        wf["workflow_id"],
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "reserve visible-child parent goal delivery",
        "--final-status",
        json.dumps(wf["final_status"]),
        state_root=state_root,
    )
    reported = run(
        "complete-reported",
        "--workflow-id",
        wf["workflow_id"],
        "--reservation-id",
        reservation["reservation_id"],
        "--delivery-message-id",
        "telegram-message-phase5b-visible-parent",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    assert_true(reported["status"] == "reported", "visible_child_report_required should allow parent report after children report")


def assert_phase5b_waived_child_mode_requires_owner_proof(state_root: Path) -> None:
    target = state_root / "phase5b-waived-child-target.txt"
    target.write_text("phase 5b waived child delivery target\n", encoding="utf-8")
    wf = run(
        "goal",
        "--text",
        f"Implement execution-required goal workflow for {target}",
        "--workflow-id",
        "goal-phase5b-waived-child",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    waived = json.loads(json.dumps(wf))
    child_id = waived["child_workflow_ids"][0]
    for item in waived["goal_state"]["child_workflow_refs"]:
        if item["workflow_id"] == child_id:
            item["delivery_mode"] = "waived_with_owner_proof"
            item["report_proof_ref"] = None
    for item in waived["goal_state"]["child_residual_rollup"]["children"]:
        if item["workflow_id"] == child_id:
            item["delivery_mode"] = "waived_with_owner_proof"
            item["report_proof_ref"] = None
    for item in waived["goal_state"]["child_delivery_mode_transitions"]:
        if item["workflow_id"] == child_id:
            item["to_mode"] = "waived_with_owner_proof"
            item["report_proof_ref"] = None
            item["owner_waiver_ref"] = "evt-phase5b-child-waiver"
    waived["goal_state"]["duplicate_report_guard"]["child_report_proof_refs"] = sorted(
        proof["delivery_message_id"]
        for proof in (
            item.get("report_proof_ref")
            for item in waived["goal_state"]["child_workflow_refs"]
        )
        if isinstance(proof, dict)
    )
    persist_goal_state(state_root, waived["workflow_id"], waived)
    result = run_fail("validate", "--workflow-id", waived["workflow_id"], state_root=state_root)
    assert_true("owner_decision" in result["error"], "waived_with_owner_proof should require durable owner waiver event")
    waiver_events = events(state_root, waived["workflow_id"])
    waiver_events.append(
        {
            "schema_version": 1,
            "event_id": "evt-phase5b-child-waiver",
            "workflow_id": waived["workflow_id"],
            "event_type": "owner_decision",
            "created_at": "2026-01-01T00:00:00Z",
            "note": "Phase 5B child visible report waiver",
            "payload": {
                "decision": "waive_child_visible_report",
                "child_workflow_id": child_id,
                "reason": "owner accepted parent summary delivery",
                "residual_handling": "parent child_residual_rollup preserves residuals",
            },
        }
    )
    write_events(state_root, waived["workflow_id"], waiver_events)
    run("validate", "--workflow-id", waived["workflow_id"], state_root=state_root)
    reservation = run(
        "reserve-delivery",
        "--workflow-id",
        waived["workflow_id"],
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "reserve waived-child parent goal delivery",
        "--final-status",
        json.dumps(waived["final_status"]),
        state_root=state_root,
    )
    reported = run(
        "complete-reported",
        "--workflow-id",
        waived["workflow_id"],
        "--reservation-id",
        reservation["reservation_id"],
        "--delivery-message-id",
        "telegram-message-phase5b-waived-parent",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    assert_true(reported["status"] == "reported", "waived_with_owner_proof should allow parent report with unreported child")
    child = workflow(state_root, child_id)
    assert_true(child["status"] == "completed_unreported", "waived_with_owner_proof should not silently report child")


def assert_goal_child_ids_handle_long_parent_ids(state_root: Path) -> None:
    target = state_root / "long-parent-target.txt"
    target.write_text("long parent deterministic goal target\n", encoding="utf-8")
    workflow_id = "g" + ("a" * 89)
    wf = run(
        "goal",
        "--text",
        f"Implement execution-required long parent goal for {target}",
        "--workflow-id",
        workflow_id,
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    assert_true(wf["status"] == "completed_unreported", "long valid parent id should create valid child ids")
    assert_true(all(child_id.startswith("goal-child-") for child_id in wf["child_workflow_ids"]), "child ids should use fixed safe prefix")
    run("validate", "--workflow-id", workflow_id, state_root=state_root)


def assert_goal_does_not_collect_nonterminal_existing_child(state_root: Path) -> None:
    parent_id = "goal-nonterminal-child"
    text = "Implement execution-required goal with preexisting nonterminal child"
    child_id = _child_workflow_id(parent_id, role="verify", objective=text)
    run(
        "start",
        "--kind",
        "verify",
        "--text",
        f"Verify required goal child evidence for: {text}",
        "--workflow-id",
        child_id,
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    child = workflow(state_root, child_id)
    child["status"] = "waiting_user"
    write_workflow(state_root, child_id, child)
    result = run_fail(
        "goal",
        "--text",
        text,
        "--workflow-id",
        parent_id,
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    assert_true("required child workflow is not terminal" in result["error"], "goal should not collect nonterminal child workflows")


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
        assert_execution_required_goal_collects_child_evidence(state_root)
        assert_phase5b_visible_child_mode_positive_path(state_root)
        assert_phase5b_waived_child_mode_requires_owner_proof(state_root)
        assert_goal_child_ids_handle_long_parent_ids(state_root)
        assert_goal_does_not_collect_nonterminal_existing_child(state_root)
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


def _goal_record_from_state(state: dict[str, Any]) -> GoalRecord:
    return GoalRecord(
        objective=state["objective"],
        non_goals=state["non_goals"],
        success_criteria=state["success_criteria"],
        assumptions=state["assumptions"],
        approval_boundaries=state["approval_boundaries"],
        slice_queue=state["slice_queue"],
        plan_accepted=state["plan_accepted"],
        evidence_completion_check=state["evidence_completion_check"],
        plan_artifact_promotion=state["plan_artifact_promotion"],
        child_workflow_refs=state["child_workflow_refs"],
        residuals=state["residuals"],
        final_report_summary=state["final_report_summary"],
    )


if __name__ == "__main__":
    raise SystemExit(main())
