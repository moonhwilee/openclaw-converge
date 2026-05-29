#!/usr/bin/env python3
"""Smoke coverage for C4 goal mode."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


try:
    from smoke_helpers import VISIBLE_DELIVERY, assert_phase5a_contract, assert_phase5a_accepted_change_stale_rejected, assert_phase5a_freshness_rejected, assert_phase5a_missing_gate_rejected, assert_phase5a_stale_hash_rejected, assert_phase5a_terminal_status_rejected, assert_true, events, run, run_fail, workflow, write_events, write_workflow
    from converge_verify_mode_smoke import _write_fake_openclaw_cli
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import VISIBLE_DELIVERY, assert_phase5a_contract, assert_phase5a_accepted_change_stale_rejected, assert_phase5a_freshness_rejected, assert_phase5a_missing_gate_rejected, assert_phase5a_stale_hash_rejected, assert_phase5a_terminal_status_rejected, assert_true, events, run, run_fail, workflow, write_events, write_workflow
    from tests.smoke.converge_verify_mode_smoke import _write_fake_openclaw_cli
from converge.modes.goal import GoalRecord, _child_ref_from_workflow, _child_workflow_id, _record_child_collected, build_goal_record, render_goal_plan, validate_goal_state  # noqa: E402
from converge.artifacts import sha256_file  # noqa: E402
from converge.agents.contracts import NativeChildResult  # noqa: E402
from converge.modes.goal import GoalHandler  # noqa: E402
from converge.store import WorkflowStore  # noqa: E402


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
        "--scaffold-only",
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
    implicit_scaffold = run_fail(
        "goal",
        "--text",
        "Implement execution-required goal workflow",
        "--workflow-id",
        "goal-implicit-planned-children-rejected",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    assert_true(
        "goal execution_backend_missing" in implicit_scaffold["error"],
        "goal should reject implicit scaffold child workflows without a real execution backend",
    )


def assert_execution_required_goal_collects_child_evidence(state_root: Path) -> None:
    target = state_root / "phase3-goal-target.txt"
    target.write_text("phase 3 goal child evidence target\n", encoding="utf-8")
    wf = run(
        "goal",
        "--text",
        f"Read-only audit execution-required goal workflow for {target}",
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
    assert_true(
        sorted(node["workflow_id"] for node in goal_state["workflow_graph"]["nodes"])
        == sorted([wf["workflow_id"], *wf["child_workflow_ids"]]),
        "Phase 5 should expose a workflow_graph over parent and child workflows",
    )
    assert_true(
        sorted(edge["child_id"] for edge in goal_state["workflow_graph"]["edges"]) == sorted(wf["child_workflow_ids"]),
        "Phase 5 workflow_graph edges should match child workflows",
    )
    for child_id in wf["child_workflow_ids"]:
        child = workflow(state_root, child_id)
        assert_true(child["parent_workflow_id"] == wf["workflow_id"], "child should point back to parent")
        assert_true(child["status"] == "completed_unreported", "child should reach terminal unreported")
        assert_true(child["final_status"]["result"] in {"pass", "pass_with_risks"}, "child terminal result should pass")
    assert_true(
        {item.get("native_agent_panel_proof") for item in goal_state["child_workflow_refs"]} == {None},
        "default goal child collection should not invent native panel proof",
    )
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

    missing_graph = json.loads(json.dumps(wf))
    del missing_graph["goal_state"]["workflow_graph"]
    persist_goal_state(state_root, "goal-execution-required-real-children", missing_graph)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("workflow_graph" in result["error"], "Phase 5 should reject missing workflow_graph")
    persist_goal_state(state_root, "goal-execution-required-real-children", wf)

    wrong_graph_parent = json.loads(json.dumps(wf))
    wrong_graph_parent["goal_state"]["workflow_graph"]["graph_id"] = "phase5-goal-child-graph:wrong-parent"
    for node in wrong_graph_parent["goal_state"]["workflow_graph"]["nodes"]:
        if node["role"] == "goal":
            node["workflow_id"] = "wrong-parent"
        else:
            node["parent_id"] = "wrong-parent"
    for edge in wrong_graph_parent["goal_state"]["workflow_graph"]["edges"]:
        edge["parent_id"] = "wrong-parent"
    persist_goal_state(state_root, "goal-execution-required-real-children", wrong_graph_parent)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true("parent" in result["error"], "Phase 5 should bind workflow_graph parent to actual workflow id")
    persist_goal_state(state_root, "goal-execution-required-real-children", wf)

    wrong_graph_child = json.loads(json.dumps(wf))
    child_graph_node = next(
        node
        for node in wrong_graph_child["goal_state"]["workflow_graph"]["nodes"]
        if node["workflow_id"] == wf["child_workflow_ids"][0]
    )
    child_graph_node["owner_session"] = "session:evil"
    child_graph_node["visible_delivery_policy"] = {"channel": "telegram", "target": "evil"}
    child_graph_node["state_root"] = "other-state-root"
    persist_goal_state(state_root, "goal-execution-required-real-children", wrong_graph_child)
    result = run_fail("validate", "--workflow-id", "goal-execution-required-real-children", state_root=state_root)
    assert_true(
        "owner_session" in result["error"] or "state_root" in result["error"],
        "Phase 5 should reject workflow_graph child metadata drift",
    )
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

    early_child_result = run(
        "reserve-delivery",
        "--workflow-id",
        wf["child_workflow_ids"][0],
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "child report should be blocked before parent summary report",
        "--final-status",
        json.dumps(workflow(state_root, wf["child_workflow_ids"][0])["final_status"]),
        state_root=state_root,
    )
    assert_true(
        early_child_result["send_authorized"] is False and early_child_result["reason"] == "duplicate_child_report_guard",
        "Phase 5B should block parent_summary_only child visible reports before parent report",
    )

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
    forced_reported_child = workflow(state_root, wf["child_workflow_ids"][0])
    forced_reported_child["status"] = "reported"
    forced_reported_child["phase"] = "reported"
    forced_reported_child.setdefault("visible_delivery_state", {})["reported"] = {
        "reservation_id": "forced-child-reservation",
        "delivery_message_id": "forced-child-message",
        "visible_delivery": VISIBLE_DELIVERY,
        "reported_at": "2026-01-01T00:00:00Z",
        "report_authority": "test",
        "source_of_truth": "test",
    }
    write_workflow(state_root, forced_reported_child["workflow_id"], forced_reported_child)
    reported_parent_with_invalid_child = run_fail(
        "complete-reported",
        "--workflow-id",
        "goal-execution-required-real-children",
        "--reservation-id",
        reservation["reservation_id"],
        "--delivery-message-id",
        "telegram-message-parent-goal-invalid-child",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    assert_true(
        "parent_summary_only child must not be separately reported" in reported_parent_with_invalid_child["error"],
        "complete-reported should validate reported goal child state before recording report_sent",
    )
    assert_true(
        not any(event["event_type"] == "report_sent" for event in events(state_root, "goal-execution-required-real-children")),
        "failed complete-reported should not append report_sent before validation passes",
    )
    forced_reported_child["status"] = "completed_unreported"
    forced_reported_child["phase"] = "terminal"
    forced_reported_child["visible_delivery_state"].pop("reported")
    write_workflow(state_root, forced_reported_child["workflow_id"], forced_reported_child)

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


def assert_material_goal_blocks_without_fix_runner_child(state_root: Path) -> None:
    target = state_root / "phase5-material-goal-target.txt"
    target.write_text("phase 5 material goal target\n", encoding="utf-8")
    wf = run(
        "goal",
        "--text",
        f"Implement execution-required goal workflow for {target}",
        "--scaffold-only",
        "--workflow-id",
        "goal-execution-required-material-child-blocked",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    goal_state = wf["goal_state"]
    assert_true(wf["status"] == "failed_unreported", "material goal should fail closed without fix-runner child evidence")
    assert_true(wf["final_status"]["result"] == "blocked", "material goal should be blocked")
    assert_true(
        wf["final_status"]["stop_reason"] == "blocked_child_workflow_failed",
        "material goal should block when a required child cannot prove execution",
    )
    assert_true(
        goal_state["execution_required"] is True and goal_state["execution_performed"] is True,
        "material goal should record child workflow execution truth markers",
    )
    assert_true(
        {item["kind"] for item in goal_state["child_workflow_refs"]} == {"verify", "conv"},
        "material goal should still create verify and conv children",
    )
    assert_true(
        any(item["status"] == "blocked" and item["kind"] == "conv" for item in goal_state["child_workflow_refs"]),
        "material goal should record the blocked conv child",
    )
    conv_child_id = next(
        item["workflow_id"]
        for item in goal_state["child_workflow_refs"]
        if item["kind"] == "conv"
    )
    conv_child = workflow(state_root, conv_child_id)
    assert_true(conv_child["status"] == "failed_unreported", "material conv child should fail closed")
    assert_true(
        conv_child["final_status"]["stop_reason"] == "blocked_no_execution_evidence",
        "material conv child should require specialist or fix-runner evidence",
    )
    run("validate", "--workflow-id", "goal-execution-required-material-child-blocked", state_root=state_root)
    assert_phase5a_contract(wf, "goal_state")

    implicit_scaffold = run_fail(
        "goal",
        "--text",
        f"Implement execution-required goal workflow for {target}",
        "--workflow-id",
        "goal-implicit-scaffold-rejected",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    assert_true(
        "goal execution_backend_missing" in implicit_scaffold["error"],
        "goal should reject implicit scaffold child workflows without a real execution backend",
    )


def assert_goal_collects_native_child_panel_evidence(state_root: Path) -> None:
    store = WorkflowStore(state_root)
    parent = store.create_workflow(
        kind="goal",
        text="Read-only audit execution-required native goal workflow",
        workflow_id="goal-native-child-panel",
        owner_session_key="session:test",
        visible_delivery={"channel": "telegram", "target": "test"},
    )
    GoalHandler(store).finalize_goal(parent["workflow_id"], native_agent_backend=FakeNativePanelBackend())
    wf = workflow(state_root, "goal-native-child-panel")
    goal_state = wf["goal_state"]
    assert_true(wf["status"] == "completed_unreported", "native child goal should complete with passing child evidence")
    assert_true({item["kind"] for item in goal_state["child_workflow_refs"]} == {"verify", "conv"}, "native goal should collect verify and conv children")
    for child_ref in goal_state["child_workflow_refs"]:
        proof = child_ref.get("native_agent_panel_proof")
        assert_true(isinstance(proof, dict), "native child refs should carry native panel proof")
        assert_true(proof["source"] == "native_agent_panel", "native child proof should bind native source")
        assert_true(proof["satisfies_native_agent_panel"] is True, "native child proof should satisfy native panel")
        assert_true(len(proof["session_keys"]) == 3 and len(proof["request_ids"]) == 3, "native child proof should expose child sessions and requests")
        assert_true(proof["tool_smoke_status"] == "passed", "native child proof should require passed tool smoke")
        assert_true(
            len(proof["tool_smoke_proofs"]) == 3
            and all(item["tool_smoke_evidence"]["trajectory_proof"]["tool_call_count"] >= 1 for item in proof["tool_smoke_proofs"]),
            "native child proof should preserve per-result tool-smoke trajectory proof",
        )
        child = workflow(state_root, child_ref["workflow_id"])
        state = child[f"{child_ref['kind']}_state"]
        assert_true(state["execution_source"] == "native_agent_panel", "child workflow should persist native execution source")
        assert_true(state["satisfies_native_agent_panel"] is True, "child workflow should satisfy native parity")
    run("validate", "--workflow-id", "goal-native-child-panel", state_root=state_root)
    assert_phase5a_contract(wf, "goal_state")

    forged = json.loads(json.dumps(wf))
    forged["goal_state"]["child_workflow_refs"][0]["native_agent_panel_proof"]["session_keys"][0] = "agent:main:converge-forged"
    persist_goal_state(state_root, "goal-native-child-panel", forged)
    result = run_fail("validate", "--workflow-id", "goal-native-child-panel", state_root=state_root)
    assert_true("native_agent_panel_proof" in result["error"], "goal validate should reject forged parent native proof")
    persist_goal_state(state_root, "goal-native-child-panel", wf)

    tampered_child_ref = wf["goal_state"]["child_workflow_refs"][0]
    tampered_child = workflow(state_root, tampered_child_ref["workflow_id"])
    original_child = json.loads(json.dumps(tampered_child))
    tampered_state = tampered_child[f"{tampered_child_ref['kind']}_state"]
    tampered_state["agent_result_refs"][0]["tool_smoke_evidence"].pop("session_store_proof", None)
    write_workflow(state_root, tampered_child_ref["workflow_id"], tampered_child)
    matching_tampered_parent = json.loads(json.dumps(wf))
    matching_tampered_parent["goal_state"]["child_workflow_refs"][0]["native_agent_panel_proof"]["tool_smoke_proofs"][0][
        "tool_smoke_evidence"
    ].pop("session_store_proof", None)
    persist_goal_state(state_root, "goal-native-child-panel", matching_tampered_parent)
    result = run_fail("validate", "--workflow-id", "goal-native-child-panel", state_root=state_root)
    assert_true(
        "session_store_proof" in result["error"] or "specialist artifact must match mode state" in result["error"],
        "goal validate should deep-validate child native tool-smoke proof instead of only matching parent summary",
    )
    write_workflow(state_root, tampered_child_ref["workflow_id"], original_child)
    persist_goal_state(state_root, "goal-native-child-panel", wf)

    cli_workflow_id = "goal-native-cli-child-panel"
    goal_record = build_goal_record(
        {
            "workflow_id": cli_workflow_id,
            "source_request": "Read-only audit execution-required native goal CLI workflow",
            "continuation_plan": {"steps": []},
        }
    )
    target_refs_file = state_root / "goal-target-refs.json"
    target_refs_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "target_refs": [
                    {"kind": "file", "path": "converge/modes/goal.py", "role": "mode"},
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    fake_openclaw = _write_fake_openclaw_cli(
        state_root / "fake-goal-openclaw",
        include_tool_smoke_evidence=True,
        extra_workflow_ids=[
            _child_workflow_id(cli_workflow_id, role="verify", objective=goal_record.objective),
            _child_workflow_id(cli_workflow_id, role="conv", objective=goal_record.objective),
        ],
    )
    cli_result = run(
        "goal",
        "--text",
        "Read-only audit execution-required native goal CLI workflow",
        "--workflow-id",
        cli_workflow_id,
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--target-refs-file",
        str(target_refs_file),
        "--native-panel-openclaw-cli",
        "--native-panel-openclaw-bin",
        str(fake_openclaw),
        state_root=state_root,
    )["workflow"]
    assert_true(
        all(isinstance(item.get("native_agent_panel_proof"), dict) for item in cli_result["goal_state"]["child_workflow_refs"]),
        "goal CLI native flag should propagate native proof into child refs",
    )
    for child_ref in cli_result["goal_state"]["child_workflow_refs"]:
        proof = child_ref["native_agent_panel_proof"]
        expected_file_ref = {
            "kind": "file",
            "path": "converge/modes/goal.py",
            "source_root": str(Path.cwd().resolve()),
            "role": "mode",
        }
        assert_true(len(proof["tool_smoke_proofs"]) == 3, "goal CLI native proof should preserve every child tool-smoke proof")
        assert_true(
            all(
                item["tool_smoke_evidence"]["session_key"] == item["session_key"]
                and item["tool_smoke_evidence"]["agent_session_ref"] == item["agent_session_ref"]
                and item["tool_smoke_evidence"]["child_read_action"] == "read_files"
                and item["tool_smoke_evidence"]["child_status_action"] == "shell_status"
                for item in proof["tool_smoke_proofs"]
            ),
            "goal CLI native proof should preserve per-result session-bound smoke proof",
        )
        assert_true(
            all(
                item["tool_smoke_evidence"]["trajectory_proof"]["tool_call_count"] >= 1
                for item in proof["tool_smoke_proofs"]
            ),
            "goal CLI native proof should preserve per-result trajectory proof",
        )
        assert_true(
            all(session_key.startswith("agent:main:converge-") for session_key in proof["session_keys"]),
            "goal CLI native proof should expose explicit native child session keys",
        )
        child = workflow(state_root, child_ref["workflow_id"])
        child_state = child[f"{child_ref['kind']}_state"]
        assert_true(
            all(
                expected_file_ref in item["target_refs"]
                for item in child_state["agent_request_refs"]
            ),
            "goal CLI should propagate manifest file refs into child native requests",
        )
    run("validate", "--workflow-id", cli_workflow_id, state_root=state_root)


def assert_phase5b_visible_child_mode_positive_path(state_root: Path) -> None:
    target = state_root / "phase5b-visible-child-target.txt"
    target.write_text("phase 5b visible child delivery target\n", encoding="utf-8")
    wf = run(
        "goal",
        "--text",
        f"Read-only audit execution-required goal workflow for {target}",
        "--workflow-id",
        "goal-phase5b-visible-child",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    for child_ref in wf["goal_state"]["child_workflow_refs"]:
        child_ref["delivery_mode"] = "visible_child_report_required"
        child_ref["delivery_mode_reason"] = "child visible report proof is required before parent reported completion"
    for item in wf["goal_state"]["child_residual_rollup"]["children"]:
        item["delivery_mode"] = "visible_child_report_required"
    for item in wf["goal_state"]["child_delivery_mode_transitions"]:
        item["to_mode"] = "visible_child_report_required"
        item["reason"] = "child visible report proof is required before parent reported completion"
    persist_goal_state(state_root, wf["workflow_id"], wf)
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
    graph_nodes = {
        item["workflow_id"]: item
        for item in wf["goal_state"]["workflow_graph"]["nodes"]
        if item["workflow_id"] in wf["child_workflow_ids"]
    }
    for child_ref in wf["goal_state"]["child_workflow_refs"]:
        graph_node = graph_nodes[child_ref["workflow_id"]]
        graph_node["terminal_status"] = child_ref["terminal_status"]
        graph_node["report_proof_ref"] = child_ref["report_proof_ref"]
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
        f"Read-only audit execution-required goal workflow for {target}",
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
    early_child_result = run(
        "reserve-delivery",
        "--workflow-id",
        child_id,
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "waived child report should be blocked before parent report",
        "--final-status",
        json.dumps(workflow(state_root, child_id)["final_status"]),
        state_root=state_root,
    )
    assert_true(
        early_child_result["send_authorized"] is False and early_child_result["reason"] == "duplicate_child_report_guard",
        "Phase 5B should block waived child visible reports before parent report",
    )
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
    late_child_result = run(
        "reserve-delivery",
        "--workflow-id",
        child_id,
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "waived child report should be blocked after parent report",
        "--final-status",
        json.dumps(child["final_status"]),
        state_root=state_root,
    )
    assert_true(
        late_child_result["send_authorized"] is False and late_child_result["reason"] == "duplicate_child_report_guard",
        "Phase 5B should block waived child visible reports after parent report",
    )


def assert_goal_child_ids_handle_long_parent_ids(state_root: Path) -> None:
    target = state_root / "long-parent-target.txt"
    target.write_text("long parent deterministic goal target\n", encoding="utf-8")
    workflow_id = "g" + ("a" * 89)
    wf = run(
        "goal",
        "--text",
        f"Read-only audit execution-required long parent goal for {target}",
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


def assert_goal_collects_blocked_existing_child_as_terminal(state_root: Path) -> None:
    parent_id = "goal-blocked-child-terminal"
    text = "Implement execution-required goal with preexisting blocked child"
    verify_child_id = _child_workflow_id(parent_id, role="verify", objective=text)
    conv_child_id = _child_workflow_id(parent_id, role="conv", objective=text)
    for role, child_id, child_text in (
        ("verify", verify_child_id, f"Verify required goal child evidence for: {text}"),
        ("conv", conv_child_id, f"Converge required goal child execution for: {text}"),
    ):
        run(
            "start",
            "--kind",
            role,
            "--text",
            child_text,
            "--workflow-id",
            child_id,
            "--owner-session-key",
            "session:test",
            "--visible-delivery",
            VISIBLE_DELIVERY,
            state_root=state_root,
        )
        child = workflow(state_root, child_id)
        child["parent_workflow_id"] = parent_id
        child["status"] = "blocked" if role == "verify" else "completed_unreported"
        child["phase"] = "native_panel_blocked" if role == "verify" else "terminal"
        child["final_status"] = (
            {
                "result": "blocked",
                "stop_reason": "blocked_no_execution_evidence",
                "residuals": {
                    "blocking_remaining": ["blocked child smoke"],
                    "accepted_risks": [],
                    "implementation_backlog": [],
                    "deferred_scope": [],
                },
            }
            if role == "verify"
            else {
                "result": "pass",
                "stop_reason": "complete",
                "residuals": {
                    "blocking_remaining": [],
                    "accepted_risks": [],
                    "implementation_backlog": [],
                    "deferred_scope": [],
                },
            }
        )
        write_workflow(state_root, child_id, child)

    wf = run(
        "goal",
        "--text",
        text,
        "--scaffold-only",
        "--workflow-id",
        parent_id,
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    assert_true(wf["status"] == "failed_unreported", "goal should close when an existing required child is blocked")
    goal_state = wf["goal_state"]
    child_refs = {item["workflow_id"]: item for item in goal_state["child_workflow_refs"]}
    assert_true(child_refs[verify_child_id]["status"] == "blocked", "blocked child should be collected as blocked")
    assert_true(child_refs[verify_child_id]["terminal_status"] == "blocked", "blocked child terminal_status should be preserved")
    assert_true(
        goal_state["child_collection_status"]["complete"] is True,
        "blocked terminal child should not leave parent collection incomplete",
    )
    run("validate", "--workflow-id", parent_id, state_root=state_root)


def assert_goal_refreshes_child_collection_after_child_terminal_status_changes(state_root: Path) -> None:
    parent_id = "goal-refresh-child-collection"
    text = "Implement execution-required goal with remediated child collection"
    verify_child_id = _child_workflow_id(parent_id, role="verify", objective=text)
    conv_child_id = _child_workflow_id(parent_id, role="conv", objective=text)
    for role, child_id, child_text in (
        ("verify", verify_child_id, f"Verify required goal child evidence for: {text}"),
        ("conv", conv_child_id, f"Converge required goal child execution for: {text}"),
    ):
        run(
            "start",
            "--kind",
            role,
            "--text",
            child_text,
            "--workflow-id",
            child_id,
            "--owner-session-key",
            "session:test",
            "--visible-delivery",
            VISIBLE_DELIVERY,
            state_root=state_root,
        )
        child = workflow(state_root, child_id)
        child["parent_workflow_id"] = parent_id
        child["status"] = "blocked" if role == "verify" else "completed_unreported"
        child["phase"] = "native_panel_blocked" if role == "verify" else "terminal"
        child["final_status"] = (
            {
                "result": "blocked",
                "stop_reason": "blocked_no_execution_evidence",
                "residuals": {
                    "blocking_remaining": ["initial blocked child"],
                    "accepted_risks": [],
                    "implementation_backlog": [],
                    "deferred_scope": [],
                },
            }
            if role == "verify"
            else {
                "result": "pass",
                "stop_reason": "complete",
                "residuals": {
                    "blocking_remaining": [],
                    "accepted_risks": [],
                    "implementation_backlog": [],
                    "deferred_scope": [],
                },
            }
        )
        write_workflow(state_root, child_id, child)

    first = run(
        "goal",
        "--text",
        text,
        "--scaffold-only",
        "--workflow-id",
        parent_id,
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    assert_true(first["status"] == "failed_unreported", "first goal run should close on blocked child")
    verify_child = workflow(state_root, verify_child_id)
    verify_child["status"] = "completed_unreported"
    verify_child["phase"] = "terminal"
    verify_child["final_status"] = {
        "result": "pass",
        "stop_reason": "complete",
        "residuals": {
            "blocking_remaining": [],
            "accepted_risks": [],
            "implementation_backlog": [],
            "deferred_scope": [],
        },
    }
    write_workflow(state_root, verify_child_id, verify_child)

    store = WorkflowStore(state_root)
    _record_child_collected(GoalHandler(store), parent_id, verify_child)
    retry = workflow(state_root, parent_id)
    conv_child = workflow(state_root, conv_child_id)
    child_refs = [
        _child_ref_from_workflow(verify_child, role="verify"),
        _child_ref_from_workflow(conv_child, role="conv"),
    ]
    child_ids = [item["workflow_id"] for item in child_refs]
    retry["goal_state"]["child_workflow_refs"] = child_refs
    retry["goal_state"]["child_collection_status"] = {
        "required_child_workflow_ids": child_ids,
        "collected_child_workflow_ids": child_ids,
        "children": [
            {
                "workflow_id": item["workflow_id"],
                "kind": item["kind"],
                "status": item["status"],
                "terminal_status": item["terminal_status"],
                "result": item["result"],
                "stop_reason": (item.get("final_status") or {}).get("stop_reason"),
                "residuals": (item.get("final_status") or {}).get("residuals"),
                "evidence_refs": item.get("evidence_refs") or [],
            }
            for item in child_refs
        ],
        "complete": True,
    }
    write_workflow(state_root, parent_id, retry)
    persist_goal_state(state_root, parent_id, retry)
    child_refs_by_id = {item["workflow_id"]: item for item in retry["goal_state"]["child_workflow_refs"]}
    assert_true(child_refs_by_id[verify_child_id]["terminal_status"] == "completed_unreported", "retry should refresh terminal_status")
    collected = [
        record
        for record in events(state_root, parent_id)
        if record.get("event_type") == "child_workflow_collected"
        and (record.get("payload") or {}).get("child_workflow_id") == verify_child_id
    ]
    assert_true(len(collected) == 2, "retry should append a new child collection event for changed terminal status")
    assert_true((collected[-1].get("payload") or {}).get("result") == "pass", "latest collection event should match current child result")


def assert_completion_criteria_plan_only_wording_does_not_downgrade_goal(state_root: Path) -> None:
    wf = run(
        "goal",
        "--text",
        (
            "Converge execution parity Phase 1 구현-검증-수렴 진행해줘. "
            "목표는 Phase 1만 완료하는 것이다. "
            "완료 기준: plan-only 케이스는 execution_required=false로 유지된다."
        ),
        "--scaffold-only",
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
        assert_material_goal_blocks_without_fix_runner_child(state_root)
        assert_goal_collects_native_child_panel_evidence(state_root)
        assert_phase5b_visible_child_mode_positive_path(state_root)
        assert_phase5b_waived_child_mode_requires_owner_proof(state_root)
        assert_goal_child_ids_handle_long_parent_ids(state_root)
        assert_goal_does_not_collect_nonterminal_existing_child(state_root)
        assert_goal_collects_blocked_existing_child_as_terminal(state_root)
        assert_goal_refreshes_child_collection_after_child_terminal_status_changes(state_root)
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


class FakeNativePanelBackend:
    def run_panel(self, requests):
        results = []
        for index, request in enumerate(requests, start=1):
            completed_at = f"2026-05-29T01:0{index}:00Z"
            results.append(
                NativeChildResult(
                    request_id=request.request_id,
                    result_id=f"goal-native-result-{request.mode}-{index}",
                    agent_session_ref=request.session_key,
                    session_key=request.session_key,
                    tool_smoke_status="passed",
                    profile_ref=request.profile_ref,
                    context_hash=request.context_hash,
                    status="completed",
                    findings=[
                        {
                            "finding_id": f"goal-native-finding-{request.mode}-{index}",
                            "profile_id": request.profile_ref,
                            "finding": f"Native {request.mode} child produced passing evidence.",
                            "severity": "p3",
                            "evidence": f"agent_session_ref:{request.session_key}",
                            "why_it_matters": "Goal parent success depends on collected native child proof.",
                            "minimal_fix_or_test": "Keep parent collection bound to child session refs and smoke proof.",
                            "scope_risk": "goal-child-native-panel",
                            "confidence": 0.82,
                            "failure_mode": "goal child evidence binding",
                            "source_provenance": "native_openclaw_session",
                        }
                    ],
                    started_at=f"2026-05-29T01:0{index}:00Z",
                    deadline_at=f"2026-05-29T01:1{index}:00Z",
                    completed_at=completed_at,
                    tool_smoke_evidence={
                        "status": "passed",
                        "session_key": request.session_key,
                        "agent_session_ref": request.session_key,
                        "kind": "coordinator_verified_child_tool_smoke_session_and_trajectory_binding",
                        "checked_at": completed_at,
                        "verification_scope": "fixture_goal_parent_child_native_panel_binding",
                        "policy_enforcement": "prompt_and_coordinator_validation_only",
                        "lifecycle_model": "synchronous_serial_openclaw_agent_child_process",
                        "lifecycle_scope": "launch_and_collect_are_executed_by_one_bounded_openclaw_agent_command; wait is the bounded command wait",
                        "child_tool_smoke_kind": "fixture",
                        "child_tool_smoke_checked_at": completed_at,
                        "read_action": "read_files",
                        "status_action": "shell_status",
                        "child_read_action": "read_files",
                        "child_status_action": "shell_status",
                        "target_ref_read_manifest": {
                            "required_count": 0,
                            "read_count": 0,
                            "missing": [],
                            "read_target_refs": [],
                        },
                        "trajectory_action_binding": {
                            "read_action": "read_files",
                            "status_action": "shell_status",
                            "tool_names": ["exec_command"],
                            "read_action_bound_by_tool_names": True,
                            "status_action_bound_by_tool_names": True,
                        },
                        "session_store_proof": {
                            "session_key": request.session_key,
                            "session_id": f"fixture-goal-child-session-{request.mode}-{index}",
                            "updated_at": 1779981795923 + index,
                            "agent_id": "converge",
                            "kind": "spawn-child",
                        },
                        "trajectory_proof": {
                            "session_key": request.session_key,
                            "output_dir": f"/tmp/fixture-goal-child-trajectory-{request.mode}-{index}",
                            "event_count": 2,
                            "runtime_event_count": 0,
                            "transcript_event_count": 2,
                            "tool_call_count": 1,
                            "tool_result_count": 1,
                            "tool_names": ["exec_command"],
                        },
                    },
                )
            )
        return results


if __name__ == "__main__":
    raise SystemExit(main())
