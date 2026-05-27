#!/usr/bin/env python3
"""Smoke coverage for C3 conv mode."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


try:
    from smoke_helpers import VISIBLE_DELIVERY, assert_true, events, run, run_fail, workflow, write_workflow
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import VISIBLE_DELIVERY, assert_true, events, run, run_fail, workflow, write_workflow
from converge.modes.conv import _material_change_record, _max_round_record, build_conv_record, render_conv_report, validate_conv_state  # noqa: E402


def finalize_conv(state_root: Path, *, workflow_id: str, text: str) -> dict[str, Any]:
    return run(
        "conv",
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


def assert_conv_report_matches(wf: dict[str, Any], text: str | None = None) -> None:
    artifact_path = Path(wf["conv_state"]["final_report_artifact_path"])
    source_text = text if text is not None else wf["source_request"]
    assert_true(artifact_path.is_file(), "conv report artifact should be materialized")
    assert_true(
        artifact_path.read_text(encoding="utf-8") == render_conv_report(build_conv_record(source_text)),
        "conv report artifact should match rendered conv state",
    )


def assert_default_conv_contract(state_root: Path) -> None:
    text = "Converge C3 evidence sufficient contract"
    wf = finalize_conv(state_root, workflow_id="conv-c3-smoke", text=text)
    conv_state = wf["conv_state"]
    assert_true(wf["kind"] == "conv", "conv command should create a conv workflow")
    assert_true(wf["status"] == "completed_unreported", "conv mode should stop at terminal unreported")
    assert_true(isinstance(wf["continuation_plan"], dict), "conv mode should preserve iterative continuation metadata in C3")
    assert_true(
        wf["continuation_plan"]["rolling_state"]["last_checkpoint_id"] in wf["checkpoint_index"],
        "conv continuation metadata should point to the terminal checkpoint",
    )
    assert_true(conv_state["round_count"] == 1, "default conv fixture should produce one round")
    assert_true(conv_state["rounds"][0]["original_target_gate"] == "within_original_target", "conv should record original target gate")
    assert_true(conv_state["rounds"][0]["delta_gate"] == "no_delta", "conv should record delta gate")
    assert_true(conv_state["stop_condition"] == "evidence_sufficient", "default conv should stop on evidence sufficiency")
    assert_true(wf["final_status"]["stop_reason"] == conv_state["stop_condition"], "conv stop condition should bind final_status")
    assert_true(wf["verification"]["evidence"][-1]["artifact_refs"] == ["conv-final-report"], "terminal evidence should reference conv report")
    assert_true(wf["next_safe_action"]["action_type"] == "report_terminal_status", "conv terminal state should require report flow")
    assert_true([event["event_type"] for event in events(state_root, "conv-c3-smoke")] == ["start", "artifact", "complete"], "conv should use start/artifact/complete events")
    assert_conv_report_matches(wf)
    run("validate", "--workflow-id", "conv-c3-smoke", state_root=state_root)

    reservation = run(
        "reserve-delivery",
        "--workflow-id",
        "conv-c3-smoke",
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "reserve final conv delivery",
        "--final-status",
        json.dumps(wf["final_status"]),
        state_root=state_root,
    )
    assert_true(reservation["send_authorized"] is True, "reserve-delivery should authorize terminal conv report")
    reported = run(
        "complete-reported",
        "--workflow-id",
        "conv-c3-smoke",
        "--reservation-id",
        reservation["reservation_id"],
        "--delivery-message-id",
        "telegram-message-conv",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    assert_true(reported["status"] == "reported", "complete-reported should mark conv workflow reported")
    before_events = events(state_root, "conv-c3-smoke")
    before_worklog = (state_root / "workflows" / "conv-c3-smoke" / "worklog.md").read_text(encoding="utf-8")
    terminal_append = run_fail(
        "append-round",
        "--workflow-id",
        "conv-c3-smoke",
        "--round",
        "99",
        "--summary",
        "must not append after report",
        state_root=state_root,
    )
    assert_true("terminal status" in terminal_append["error"], "append-round should reject terminal workflows")
    assert_true(events(state_root, "conv-c3-smoke") == before_events, "terminal append-round should not add events")
    assert_true(
        (state_root / "workflows" / "conv-c3-smoke" / "worklog.md").read_text(encoding="utf-8") == before_worklog,
        "terminal append-round should not add worklog blocks",
    )


def assert_execution_required_conv_blocks_synthetic_round(state_root: Path) -> None:
    wf = run(
        "conv",
        "--text",
        "Improve execution-required target until convergence",
        "--workflow-id",
        "conv-execution-required-blocked",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    assert_true(wf["status"] == "failed_unreported", "execution-required conv should fail closed")
    assert_true(wf["final_status"]["result"] == "blocked", "execution-required conv should be blocked")
    assert_true(
        wf["final_status"]["stop_reason"] == "blocked_no_execution_evidence",
        "conv should block on missing execution evidence",
    )
    assert_true(
        wf["conv_state"]["execution_required"] is True and wf["conv_state"]["execution_performed"] is False,
        "conv should record execution truth markers",
    )
    assert_true(
        wf["conv_state"]["execution_blocked"] is True,
        "conv blocked state should record execution_blocked",
    )
    assert_true(
        [event["event_type"] for event in events(state_root, "conv-execution-required-blocked")] == ["start", "artifact", "fail"],
        "execution-required conv should fail terminally instead of completing",
    )
    run("validate", "--workflow-id", "conv-execution-required-blocked", state_root=state_root)


def assert_resume_preserves_progress(state_root: Path) -> None:
    run(
        "start",
        "--kind",
        "conv",
        "--text",
        "plan-only Converge in-progress conv fixture",
        "--workflow-id",
        "conv-progress-helper",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    run(
        "append-round",
        "--workflow-id",
        "conv-progress-helper",
        "--round",
        "1",
        "--summary",
        "record interim specialist pass before final conv",
        state_root=state_root,
    )
    wf = run(
        "conv",
        "--text",
        "plan-only ignored retry text",
        "--workflow-id",
        "conv-progress-helper",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    assert_true(wf["status"] == "completed_unreported", "conv should finalize resumable conv workflow")
    assert_true([event["event_type"] for event in events(state_root, "conv-progress-helper")] == ["start", "progress", "artifact", "complete"], "conv should preserve prior progress event")
    run("validate", "--workflow-id", "conv-progress-helper", state_root=state_root)


def assert_material_change_and_max_round_fixtures(state_root: Path) -> None:
    material_text = "material-change fixture"
    accidental = finalize_conv(state_root, workflow_id="conv-plain-fixture-text", text=material_text)
    assert_true(accidental["conv_state"]["round_count"] == 1, "fixture words in user text should not select synthetic paths")
    assert_true(accidental["conv_state"]["stop_condition"] == "evidence_sufficient", "fixture words should behave like normal text")
    assert_conv_report_matches(accidental)

    material_record = _material_change_record(material_text)
    material_state = material_record.as_state(artifact_id="conv-final-report", artifact_path="/tmp/conv-report.md")
    assert_true(material_state["round_count"] == 2, "material change should require a follow-up round")
    assert_true(material_state["rounds"][0]["material_changes"] is True, "round 1 should carry material change")
    assert_true(material_state["rounds"][0]["follow_up_required"] is True, "round 1 should require follow-up")
    assert_true(material_state["rounds"][1]["evidence_sufficient"] is True, "round 2 should close with evidence sufficiency")
    validate_conv_state(
        material_state,
        terminal=True,
        final_status={"result": "pass", "stop_reason": material_state["stop_condition"], "residuals": material_state["residuals"]},
    )
    assert_true(render_conv_report(material_record).startswith("# Convergence Report"), "material fixture should render")

    max_round_text = "max-round fixture"
    max_round_record = _max_round_record(max_round_text)
    max_state = max_round_record.as_state(artifact_id="conv-final-report", artifact_path="/tmp/conv-report.md")
    assert_true(max_state["stop_condition"] == "max_round", "max round fixture should stop on max_round")
    assert_true(max_state["round_count"] == max_state["max_rounds"], "max round stop should exhaust max rounds")
    assert_true(max_state["evidence_sufficient"] is False, "max round stop should not claim evidence sufficiency")
    validate_conv_state(
        max_state,
        terminal=True,
        final_status={"result": "pass_with_risks", "stop_reason": max_state["stop_condition"], "residuals": max_state["residuals"]},
    )
    assert_true(render_conv_report(max_round_record).startswith("# Convergence Report"), "max-round fixture should render")


def assert_conv_integrity_rejects_drift(state_root: Path) -> None:
    base = workflow(state_root, "conv-c3-smoke")

    round_count = json.loads(json.dumps(base))
    round_count["conv_state"]["round_count"] += 1
    write_workflow(state_root, "conv-c3-smoke", round_count)
    result = run_fail("validate", "--workflow-id", "conv-c3-smoke", state_root=state_root)
    assert_true(
        "conv_state must match terminal checkpoint conv_state" in result["error"]
        or "round_count must match rounds" in result["error"],
        "conv should reject round_count drift",
    )
    write_workflow(state_root, "conv-c3-smoke", base)

    evidence = json.loads(json.dumps(base))
    evidence["conv_state"]["rounds"][-1]["evidence_sufficient"] = False
    write_workflow(state_root, "conv-c3-smoke", evidence)
    result = run_fail("validate", "--workflow-id", "conv-c3-smoke", state_root=state_root)
    assert_true(
        "conv_state must match terminal checkpoint conv_state" in result["error"]
        or "evidence_sufficient stop requires final round evidence sufficiency" in result["error"],
        "conv should reject evidence stop drift",
    )
    write_workflow(state_root, "conv-c3-smoke", base)

    skipped_round = json.loads(json.dumps(base["conv_state"]))
    skipped_round["rounds"][0]["round_index"] = 99
    try:
        validate_conv_state(skipped_round, terminal=True, final_status=base["final_status"])
    except ValueError as exc:
        assert_true("round_index values must be sequential" in str(exc), "conv should reject skipped round indexes")
    else:
        raise AssertionError("conv should reject skipped round indexes")

    material_state = _material_change_record("material-change fixture").as_state(
        artifact_id="conv-final-report",
        artifact_path="/tmp/conv-report.md",
    )
    material_state["rounds"][1]["round_index"] = 1
    material_final_status = {
        "result": "pass",
        "stop_reason": material_state["stop_condition"],
        "residuals": material_state["residuals"],
    }
    try:
        validate_conv_state(material_state, terminal=True, final_status=material_final_status)
    except ValueError as exc:
        assert_true("round_index values must be sequential" in str(exc), "conv should reject duplicate round indexes")
    else:
        raise AssertionError("conv should reject duplicate round indexes")

    missing_follow_up = _material_change_record("material-change fixture").as_state(
        artifact_id="conv-final-report",
        artifact_path="/tmp/conv-report.md",
    )
    missing_follow_up["rounds"] = missing_follow_up["rounds"][:1]
    missing_follow_up["round_count"] = 1
    missing_follow_up["explicit_stop_proof"] = ""
    try:
        validate_conv_state(
            missing_follow_up,
            terminal=True,
            final_status={"result": "pass", "stop_reason": missing_follow_up["stop_condition"], "residuals": missing_follow_up["residuals"]},
        )
    except ValueError as exc:
        assert_true("terminal material changes require explicit stop proof" in str(exc), "conv should reject missing material follow-up proof")
    else:
        raise AssertionError("conv should reject missing material follow-up proof")

    missing_terminal_evidence = json.loads(json.dumps(base))
    missing_terminal_evidence["verification"]["evidence"] = []
    write_workflow(state_root, "conv-c3-smoke", missing_terminal_evidence)
    result = run_fail("validate", "--workflow-id", "conv-c3-smoke", state_root=state_root)
    assert_true("verification evidence must match checkpoint-backed terminal evidence sequence" in result["error"], "conv should reject uncheckpointed evidence drift")


def assert_conv_gate_integrity_rejects_drift(state_root: Path) -> None:
    material_state = _material_change_record("material-change fixture").as_state(
        artifact_id="conv-final-report",
        artifact_path="/tmp/conv-report.md",
    )
    material_final_status = {
        "result": "pass",
        "stop_reason": material_state["stop_condition"],
        "residuals": material_state["residuals"],
    }
    outside_target = json.loads(json.dumps(material_state))
    outside_target["rounds"][0]["original_target_gate"] = "outside_original_target_rejected"
    try:
        validate_conv_state(outside_target, terminal=True, final_status=material_final_status)
    except ValueError as exc:
        assert_true("original target gate" in str(exc), "conv should reject accepted material changes outside target")
    else:
        raise AssertionError("conv should reject accepted material changes outside target")

    no_delta = json.loads(json.dumps(material_state))
    no_delta["rounds"][0]["delta_gate"] = "no_delta"
    try:
        validate_conv_state(no_delta, terminal=True, final_status=material_final_status)
    except ValueError as exc:
        assert_true("delta_gate no_delta" in str(exc), "conv should reject no_delta with material changes")
    else:
        raise AssertionError("conv should reject no_delta with material changes")

    default = workflow(state_root, "conv-plain-fixture-text")
    false_material_delta = json.loads(json.dumps(default["conv_state"]))
    false_material_delta["rounds"][0]["delta_gate"] = "new_material_delta"
    try:
        validate_conv_state(false_material_delta, terminal=True, final_status=default["final_status"])
    except ValueError as exc:
        assert_true("new_material_delta requires" in str(exc), "conv should reject material delta with no material changes")
    else:
        raise AssertionError("conv should reject material delta with no material changes")


def assert_conv_preterminal_state_is_validated(state_root: Path) -> None:
    run(
        "start",
        "--kind",
        "conv",
        "--text",
        "preterminal malformed state fixture",
        "--workflow-id",
        "conv-malformed-preterminal",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
    wf = workflow(state_root, "conv-malformed-preterminal")
    wf["conv_state"] = {"foo": "bar"}
    write_workflow(state_root, "conv-malformed-preterminal", wf)
    result = run_fail("validate", "--workflow-id", "conv-malformed-preterminal", state_root=state_root)
    assert_true("conv_state is missing required fields" in result["error"], "non-empty preterminal conv_state should be validated")


def assert_reserve_validates_conv_terminal_material_before_send(state_root: Path) -> None:
    missing = finalize_conv(state_root, workflow_id="conv-terminal-material-missing", text="missing terminal material")
    Path(missing["conv_state"]["final_report_artifact_path"]).unlink()
    result = run_fail(
        "reserve-delivery",
        "--workflow-id",
        "conv-terminal-material-missing",
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "must not send missing conv report",
        "--final-status",
        json.dumps(missing["final_status"]),
        state_root=state_root,
    )
    assert_true(result["send_authorized"] is False, "conv reserve-delivery should reject missing terminal material")
    assert_true(result["reason"] == "validation_error", "conv missing material should require validation reconciliation")

    stale = finalize_conv(state_root, workflow_id="conv-terminal-material-stale", text="stale terminal material")
    Path(stale["conv_state"]["final_report_artifact_path"]).write_text("stale conv report\n", encoding="utf-8")
    stale_result = run_fail(
        "reserve-delivery",
        "--workflow-id",
        "conv-terminal-material-stale",
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "must not send stale conv report",
        "--final-status",
        json.dumps(stale["final_status"]),
        state_root=state_root,
    )
    assert_true(stale_result["send_authorized"] is False, "conv reserve-delivery should reject stale terminal material")
    assert_true("artifact hash is stale" in stale_result["error"], "conv stale material should be hash-checked")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="converge-conv-mode-smoke-") as tmp:
        state_root = Path(tmp)
        assert_default_conv_contract(state_root)
        assert_execution_required_conv_blocks_synthetic_round(state_root)
        assert_resume_preserves_progress(state_root)
        assert_material_change_and_max_round_fixtures(state_root)
        assert_conv_integrity_rejects_drift(state_root)
        assert_conv_gate_integrity_rejects_drift(state_root)
        assert_conv_preterminal_state_is_validated(state_root)
        assert_reserve_validates_conv_terminal_material_before_send(state_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
