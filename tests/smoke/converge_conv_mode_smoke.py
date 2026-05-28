#!/usr/bin/env python3
"""Smoke coverage for C3 conv mode."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


try:
    from smoke_helpers import VISIBLE_DELIVERY, assert_phase5a_contract, assert_phase5a_accepted_change_stale_rejected, assert_phase5a_freshness_rejected, assert_phase5a_missing_gate_rejected, assert_phase5a_stale_hash_rejected, assert_phase5a_terminal_status_rejected, assert_true, events, run, run_fail, workflow, write_workflow
    from converge_verify_mode_smoke import _write_fake_openclaw_cli
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import VISIBLE_DELIVERY, assert_phase5a_contract, assert_phase5a_accepted_change_stale_rejected, assert_phase5a_freshness_rejected, assert_phase5a_missing_gate_rejected, assert_phase5a_stale_hash_rejected, assert_phase5a_terminal_status_rejected, assert_true, events, run, run_fail, workflow, write_workflow
    from tests.smoke.converge_verify_mode_smoke import _write_fake_openclaw_cli
from converge.agents.contracts import NativeChildResult, stable_hash  # noqa: E402
from converge.modes.conv import ConvRecord, ConvRound, _material_change_record, _max_round_record, build_conv_record, render_conv_report, validate_conv_state  # noqa: E402
from converge.modes.conv import ConvHandler  # noqa: E402
from converge.store import WorkflowStore  # noqa: E402


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


def assert_conv_report_matches_state(wf: dict[str, Any]) -> None:
    conv_state = wf["conv_state"]
    artifact_path = Path(conv_state["final_report_artifact_path"])
    record = _record_from_state(conv_state)
    assert_true(artifact_path.is_file(), "conv report artifact should be materialized")
    assert_true(
        artifact_path.read_text(encoding="utf-8") == render_conv_report(record),
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


def assert_execution_required_conv_records_real_round_evidence(state_root: Path) -> None:
    target = state_root / "phase2-target.txt"
    target.write_text("phase 2 deterministic conv target\n", encoding="utf-8")
    wf = run(
        "conv",
        "--text",
        f"Read-only audit execution-required target {target}",
        "--workflow-id",
        "conv-execution-required-real-round",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    conv_state = wf["conv_state"]
    assert_true(wf["status"] == "completed_unreported", "execution-required conv with real evidence should complete")
    assert_true(wf["final_status"]["result"] == "pass_with_risks", "Phase 2 real evidence should pass with scoped residuals")
    assert_true(conv_state["execution_required"] is True, "conv should preserve execution_required=true")
    assert_true(conv_state["execution_performed"] is True, "trusted conv runner should record execution_performed=true")
    assert_true(conv_state["synthetic_report"] is False, "real conv evidence should clear synthetic_report")
    assert_true(conv_state["execution_capability"] == "local_rounds", "conv should record local round capability")
    assert_true(
        conv_state["execution_evidence_refs"] == ["conv-round-execution"],
        "conv should reference round execution evidence",
    )
    assert_true(conv_state["round_count"] == 1, "trusted conv runner should record one minimal round")
    assert_true(conv_state["rounds"][0]["delta_gate"] == "no_delta", "minimal round should carry no delta")
    artifact_ids = [artifact["artifact_id"] for artifact in wf["artifacts"]]
    assert_true("conv-round-execution" in artifact_ids, "conv should register round evidence artifact")
    assert_true("conv-final-report" in artifact_ids, "conv should register final report artifact")
    event_types = [event["event_type"] for event in events(state_root, "conv-execution-required-real-round")]
    assert_true(event_types == ["start", "artifact", "round_start", "round_summary", "artifact", "complete"], "conv should record real round events")
    assert_conv_report_matches_state(wf)
    run("validate", "--workflow-id", "conv-execution-required-real-round", state_root=state_root)
    assert_phase5a_contract(wf, "conv_state")
    assert_phase5a_missing_gate_rejected(
        state_root,
        "conv-execution-required-real-round",
        "conv_state",
        "execution:conv-round-execution",
    )
    assert_phase5a_stale_hash_rejected(
        state_root,
        "conv-execution-required-real-round",
        "conv_state",
        "execution:conv-round-execution",
    )
    assert_phase5a_freshness_rejected(state_root, "conv-execution-required-real-round", "conv_state")
    assert_phase5a_terminal_status_rejected(state_root, "conv-execution-required-real-round", "conv_state")
    assert_phase5a_accepted_change_stale_rejected(state_root, "conv-execution-required-real-round", "conv_state")

    material_request = run(
        "conv",
        "--text",
        f"Improve execution-required target {target}",
        "--workflow-id",
        "conv-execution-required-material-blocked",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    assert_true(
        material_request["status"] == "failed_unreported",
        "material conv request should block when only local inspection evidence is available",
    )
    assert_true(
        material_request["final_status"]["stop_reason"] == "blocked_no_execution_evidence",
        "material conv request should require specialist/fix-runner evidence",
    )
    assert_true(
        material_request["conv_state"]["execution_performed"] is False,
        "material conv request should not mark local file inspection as execution proof",
    )
    run("validate", "--workflow-id", "conv-execution-required-material-blocked", state_root=state_root)

    mixed_material_request = run(
        "conv",
        "--text",
        f"Review and fix execution-required target {target}",
        "--workflow-id",
        "conv-execution-required-mixed-material-blocked",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    assert_true(
        mixed_material_request["status"] == "failed_unreported",
        "mixed review+fix conv request should block when only local inspection evidence is available",
    )
    assert_true(
        mixed_material_request["conv_state"]["execution_performed"] is False,
        "mixed material conv request should not mark local file inspection as execution proof",
    )
    run("validate", "--workflow-id", "conv-execution-required-mixed-material-blocked", state_root=state_root)

    korean_mixed_material_request = run(
        "conv",
        "--text",
        f"검토 후 수정 execution-required target {target}",
        "--workflow-id",
        "conv-execution-required-korean-mixed-material-blocked",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    assert_true(
        korean_mixed_material_request["status"] == "failed_unreported",
        "Korean mixed review+fix conv request should require material runner evidence",
    )
    run("validate", "--workflow-id", "conv-execution-required-korean-mixed-material-blocked", state_root=state_root)

    explicit_read_only_boundary = run(
        "conv",
        "--text",
        f"Review only, no fixes for execution-required target {target}",
        "--workflow-id",
        "conv-execution-required-explicit-read-only",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]
    assert_true(
        explicit_read_only_boundary["status"] == "completed_unreported",
        "explicit read-only conv boundary should still allow local inspection evidence",
    )
    run("validate", "--workflow-id", "conv-execution-required-explicit-read-only", state_root=state_root)

    missing_round_summary = json.loads(json.dumps(wf))
    write_workflow(state_root, "conv-execution-required-real-round", missing_round_summary)
    events_path = state_root / "workflows" / "conv-execution-required-real-round" / "events.jsonl"
    original_events = events_path.read_text(encoding="utf-8")
    without_summary = "\n".join(
        line
        for line in original_events.splitlines()
        if not line.strip() or json.loads(line).get("event_type") != "round_summary"
    )
    events_path.write_text(without_summary + "\n", encoding="utf-8")
    result = run_fail("validate", "--workflow-id", "conv-execution-required-real-round", state_root=state_root)
    assert_true("round_summary event" in result["error"], "conv should reject missing round_summary evidence")
    events_path.write_text(original_events, encoding="utf-8")

    bad_runner = json.loads(json.dumps(wf))
    for event in events(state_root, "conv-execution-required-real-round"):
        if event["event_type"] == "round_start":
            event["payload"]["runner_ref"] = "untrusted-runner"
            lines = []
            for line in original_events.splitlines():
                parsed = json.loads(line)
                if parsed["event_id"] == event["event_id"]:
                    parsed = event
                lines.append(json.dumps(parsed, ensure_ascii=False, sort_keys=True))
            events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            break
    write_workflow(state_root, "conv-execution-required-real-round", bad_runner)
    result = run_fail("validate", "--workflow-id", "conv-execution-required-real-round", state_root=state_root)
    assert_true("round_start event has untrusted runner_ref" in result["error"], "conv should reject untrusted round_start runner")
    events_path.write_text(original_events, encoding="utf-8")

    drifted_start = json.loads(json.dumps(wf))
    for event in events(state_root, "conv-execution-required-real-round"):
        if event["event_type"] == "round_start":
            event["payload"]["target_ref"] = "different-target"
            lines = []
            for line in original_events.splitlines():
                parsed = json.loads(line)
                if parsed["event_id"] == event["event_id"]:
                    parsed = event
                lines.append(json.dumps(parsed, ensure_ascii=False, sort_keys=True))
            events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            break
    write_workflow(state_root, "conv-execution-required-real-round", drifted_start)
    result = run_fail("validate", "--workflow-id", "conv-execution-required-real-round", state_root=state_root)
    assert_true("round_start target_ref must match" in result["error"], "conv should reject round_start target drift")
    events_path.write_text(original_events, encoding="utf-8")

    stale_target = json.loads(json.dumps(wf))
    target.write_text("phase 2 deterministic conv target changed after evidence\n", encoding="utf-8")
    write_workflow(state_root, "conv-execution-required-real-round", stale_target)
    result = run_fail("validate", "--workflow-id", "conv-execution-required-real-round", state_root=state_root)
    assert_true("target check hash is stale" in result["error"], "conv should reject stale target evidence")
    events_path.write_text(original_events, encoding="utf-8")


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


def assert_conv_records_structured_specialist_findings(state_root: Path) -> None:
    packet_path = state_root / "conv-specialist-findings.json"
    packet_path.write_text(json.dumps(specialist_packet()), encoding="utf-8")
    wf = run(
        "conv",
        "--text",
        "Converge execution-required target using structured specialist findings",
        "--workflow-id",
        "conv-specialist-findings",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--structured-findings-file",
        str(packet_path),
        state_root=state_root,
    )["workflow"]
    conv_state = wf["conv_state"]
    assert_true(wf["status"] == "completed_unreported", "structured specialist conv should complete")
    assert_true(conv_state["execution_capability"] == "delegated_agents", "conv structured findings should use delegated_agents")
    assert_true(conv_state["execution_source"] == "runner_provided_packet", "conv structured findings should expose runner packet execution source")
    assert_true(conv_state["satisfies_native_agent_panel"] is False, "conv structured findings should not satisfy native panel parity")
    assert_true(conv_state["execution_evidence_refs"] == ["conv-specialist-findings"], "conv should bind specialist evidence")
    assert_true(conv_state["max_rounds_default"] == 1, "conv should use native adapter max_rounds default")
    assert_true(conv_state["round_count"] == 1, "conv specialist adapter should record one bounded round")
    assert_true(
        conv_state["raw_finding_to_group_map"][0]["group_id"] == conv_state["raw_finding_to_group_map"][1]["group_id"],
        "conv should dedupe structured findings by failure mode",
    )
    assert_true(len(conv_state["agent_request_refs"]) == 3, "conv should persist one agent request per profile")
    assert_true(len(conv_state["agent_result_refs"]) == len(conv_state["agent_finding_refs"]), "conv should persist specialist result refs")
    assert_true(
        all(
            request["execution_source"] == "runner_provided_packet"
            and request["satisfies_native_agent_panel"] is False
            and request["tool_smoke_status"] == "not_applicable"
            and request["session_key"] is None
            and request["agent_session_ref"] is None
            for request in conv_state["agent_request_refs"]
        ),
        "conv runner packet requests should not satisfy native agent panel parity",
    )
    assert_true(
        all(
            result["execution_source"] == "runner_provided_packet"
            and result["satisfies_native_agent_panel"] is False
            and result["tool_smoke_status"] == "not_applicable"
            and result["session_key"] is None
            and result["agent_session_ref"] is None
            for result in conv_state["agent_result_refs"]
        ),
        "conv runner packet results should not invent native session evidence",
    )
    assert_true(len(conv_state["profile_registry_refs"]) == 5, "conv should persist reviewer/check/runner profile specs")
    assert_true(
        {item["kind"] for item in conv_state["profile_registry_refs"]} == {"reviewer", "check", "runner"},
        "conv should include reusable reviewer/check/runner profile kinds",
    )
    assert_true(
        conv_state["agent_result_collection_status"]["status"] == "complete",
        "conv should record complete specialist result collection",
    )
    assert_true(
        conv_state["agent_result_collection_status"]["relaunch_required"] is False,
        "conv recovered specialist collection should not relaunch completed requests",
    )
    assert_true(
        conv_state["recovery_resume_cursor"] == conv_state["agent_result_collection_status"]["collection_cursor"],
        "conv should bind specialist recovery cursor",
    )
    recovery_scan = run("scan", state_root=state_root)
    recovery_record = next(item for item in recovery_scan["workflows"] if item["workflow_id"] == "conv-specialist-findings")
    assert_true(
        recovery_record["agent_result_collection"]["recovery_resume_cursor"] == conv_state["recovery_resume_cursor"],
        "conv recovery scan should expose specialist collection resume cursor",
    )
    assert_true(
        recovery_record["profile_registry"]["kinds"] == ["check", "reviewer", "runner"],
        "conv recovery scan should expose specialist profile registry kinds",
    )
    recovery_watchdog = run("watchdog-check", state_root=state_root)
    recovery_packet = next(item for item in recovery_watchdog["recoveries"] if item["workflow_id"] == "conv-specialist-findings")
    assert_true(
        recovery_packet["agent_result_collection"]["relaunch_required"] is False,
        "conv recovery packet should prove completed specialist requests are not relaunched",
    )
    assert_true(
        recovery_packet["profile_registry"]["kinds"] == ["check", "reviewer", "runner"],
        "conv recovery packet should expose specialist profile registry kinds",
    )
    assert_true(
        {event["event_type"] for event in events(state_root, "conv-specialist-findings")}.issuperset(
            {"agent_panel_requested", "agent_findings_recorded", "finding_arbitrated"}
        ),
        "conv should record specialist panel events",
    )
    assert_conv_report_matches_state(wf)
    run("validate", "--workflow-id", "conv-specialist-findings", state_root=state_root)

    original_events = events(state_root, "conv-specialist-findings")
    events_path = state_root / "workflows" / "conv-specialist-findings" / "events.jsonl"
    events_path.write_text(
        "\n".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True)
            for event in original_events
            if event["event_type"] != "finding_arbitrated"
        )
        + "\n",
        encoding="utf-8",
    )
    result = run_fail("validate", "--workflow-id", "conv-specialist-findings", state_root=state_root)
    assert_true("finding_arbitrated event" in result["error"], "conv should require specialist arbitration event proof")
    events_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in original_events) + "\n",
        encoding="utf-8",
    )

    wrong_context = json.loads(json.dumps(wf))
    wrong_context["conv_state"]["agent_result_refs"][0]["context_hash"] = "wrong-context"
    write_workflow(state_root, "conv-specialist-findings", wrong_context)
    wrong_context_result = run_fail("validate", "--workflow-id", "conv-specialist-findings", state_root=state_root)
    assert_true(
        "context_hash" in wrong_context_result["error"] or "terminal checkpoint" in wrong_context_result["error"],
        "conv should reject result/request context mismatch",
    )
    write_workflow(state_root, "conv-specialist-findings", wf)

    bad_registry = json.loads(json.dumps(wf))
    bad_registry["conv_state"]["profile_registry_refs"][0]["kind"] = "runner"
    write_workflow(state_root, "conv-specialist-findings", bad_registry)
    bad_registry_result = run_fail("validate", "--workflow-id", "conv-specialist-findings", state_root=state_root)
    assert_true(
        "profile" in bad_registry_result["error"] or "terminal checkpoint" in bad_registry_result["error"],
        "conv should reject malformed reviewer profile registry entries",
    )
    write_workflow(state_root, "conv-specialist-findings", wf)

    event_drift = json.loads(json.dumps(original_events))
    for event in event_drift:
        if event["event_type"] == "agent_findings_recorded":
            event["payload"]["collection_cursor"] = "stale-cursor"
    events_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in event_drift) + "\n",
        encoding="utf-8",
    )
    event_drift_result = run_fail("validate", "--workflow-id", "conv-specialist-findings", state_root=state_root)
    assert_true("collection_cursor must match state" in event_drift_result["error"], "conv should bind event collection cursor to state")
    events_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in original_events) + "\n",
        encoding="utf-8",
    )

    profile_event_drift = json.loads(json.dumps(original_events))
    for event in profile_event_drift:
        if event["event_type"] == "agent_panel_requested":
            event["payload"]["profile_registry_hashes"] = []
    events_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in profile_event_drift) + "\n",
        encoding="utf-8",
    )
    profile_event_drift_result = run_fail("validate", "--workflow-id", "conv-specialist-findings", state_root=state_root)
    assert_true(
        "profile_registry_hashes must match state" in profile_event_drift_result["error"],
        "conv should bind specialist profile registry hashes to event proof",
    )
    events_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in original_events) + "\n",
        encoding="utf-8",
    )

    block_packet = specialist_packet()
    block_packet["findings"][0]["severity"] = "p1"
    block_packet_path = state_root / "conv-block-specialist-findings.json"
    block_packet_path.write_text(json.dumps(block_packet), encoding="utf-8")
    blocked = run(
        "conv",
        "--text",
        "Converge execution-required target with blocking specialist finding",
        "--workflow-id",
        "conv-block-specialist-findings",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--structured-findings-file",
        str(block_packet_path),
        state_root=state_root,
    )["workflow"]
    assert_true(blocked["status"] == "failed_unreported", "blocking specialist findings should fail closed")
    assert_true(blocked["final_status"]["result"] == "blocked", "blocking specialist findings should not pass with risks")
    assert_true(blocked["conv_state"]["stop_condition"] == "blocked_specialist_findings", "blocking specialist findings should bind stop condition")
    assert_true(
        blocked["conv_state"]["required_evidence_contract"]["terminal_status"] == "blocked",
        "blocking specialist findings should bind Phase 5A contract to blocked terminal status",
    )
    run("validate", "--workflow-id", "conv-block-specialist-findings", state_root=state_root)

    fix_packet = specialist_packet()
    fix_packet["findings"][0]["severity"] = "p2"
    fix_runner_source_root = state_root / "fix-runner-source"
    fix_runner_source_root.mkdir()
    fix_runner_target = fix_runner_source_root / "target.txt"
    fix_runner_target.write_text("before fix\n", encoding="utf-8")
    fix_packet["findings"][0]["local_file_edits"] = [
        {
            "path": "target.txt",
            "old": "before fix\n",
            "new": "after fix\n",
        }
    ]
    fix_packet_path = state_root / "conv-fix-specialist-findings.json"
    fix_packet_path.write_text(json.dumps(fix_packet), encoding="utf-8")
    needs_follow_up = run(
        "conv",
        "--text",
        "Converge execution-required target with accepted specialist fix",
        "--workflow-id",
        "conv-fix-specialist-findings",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--structured-findings-file",
        str(fix_packet_path),
        state_root=state_root,
    )["workflow"]
    assert_true(needs_follow_up["status"] == "running", "accepted specialist fixes should remain resumable until fix runner result is supplied")
    assert_true(
        needs_follow_up["final_status"] is None,
        "accepted specialist fixes should not create a terminal final_status before bounded fix runner proof",
    )
    assert_true(needs_follow_up["conv_state"]["round_count"] == 1, "accepted specialist fixes should not synthesize a follow-up round")
    assert_true(needs_follow_up["conv_state"]["follow_up_required"] is True, "accepted specialist fixes should record follow-up required")
    assert_true(needs_follow_up["conv_state"]["fix_runner_required"] is True, "accepted specialist fixes should require coordinator fix runner")
    assert_true(
        needs_follow_up["conv_state"]["fix_runner_collection_status"]["status"] == "pending",
        "accepted specialist fixes should leave coordinator fix runner collection pending without a result",
    )
    assert_true(
        needs_follow_up["conv_state"]["fix_runner_collection_status"]["follow_up_completed"] is False,
        "accepted specialist fixes should not prove follow-up completion without a result",
    )
    assert_true(
        needs_follow_up["conv_state"]["fix_runner_request_refs"][0]["source_classification"] == "fix_runner",
        "accepted specialist fixes should create a fix_runner-classified request",
    )
    assert_true(needs_follow_up["conv_state"]["fix_runner_result_refs"] == [], "accepted specialist fixes should not fabricate a fix runner result")
    assert_true(
        needs_follow_up["conv_state"]["stop_condition"] == "blocked_specialist_follow_up_required",
        "accepted specialist fixes should stop on pending follow-up proof",
    )
    assert_true(
        needs_follow_up["conv_state"]["required_evidence_contract"]["terminal_status"] == "blocked",
        "accepted specialist fixes should bind pending follow-up to blocked terminal status",
    )
    run("validate", "--workflow-id", "conv-fix-specialist-findings", state_root=state_root)

    fix_runner_events = [event for event in events(state_root, "conv-fix-specialist-findings") if event["event_type"] == "fix_runner_requested"]
    assert_true(len(fix_runner_events) == 1, "accepted specialist fixes should record fix_runner_requested proof")
    assert_true(
        fix_runner_events[0]["payload"]["request_ids"]
        == [needs_follow_up["conv_state"]["fix_runner_request_refs"][0]["runner_id"]],
        "fix_runner_requested event should bind request ids to state",
    )
    assert_true(
        not [event for event in events(state_root, "conv-fix-specialist-findings") if event["event_type"] == "fix_runner_completed"],
        "pending accepted specialist fixes should not record fix_runner_completed proof",
    )

    accepted_changes = needs_follow_up["conv_state"]["accepted_change_refs"]
    fix_runner_result_path = state_root / "conv-fix-runner-result.json"
    fix_runner_output = run(
        "fix-runner",
        "--workflow-id",
        "conv-fix-specialist-findings",
        "--source-root",
        str(fix_runner_source_root),
        "--output-file",
        str(fix_runner_result_path),
        state_root=state_root,
    )
    assert_true(fix_runner_output["result"]["workflow_id"] == "conv-fix-specialist-findings", "fix-runner should bind result to pending workflow")
    assert_true(fix_runner_target.read_text(encoding="utf-8") == "after fix\n", "fix-runner should apply the bounded local file edit")
    assert_true(
        fix_runner_output["result"]["focused_check_results"][0]["status"] == "pass",
        "fix-runner should produce focused check proof for the accepted change",
    )
    completed_follow_up = run(
        "conv",
        "--text",
        "Converge execution-required target with accepted specialist fix",
        "--workflow-id",
        "conv-fix-specialist-findings",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--structured-findings-file",
        str(fix_packet_path),
        "--fix-runner-result-file",
        str(fix_runner_result_path),
        "--fix-runner-source-root",
        str(fix_runner_source_root),
        state_root=state_root,
    )["workflow"]
    assert_true(completed_follow_up["status"] == "completed_unreported", "accepted specialist fixes should complete after supplied fix runner follow-up")
    assert_true(
        completed_follow_up["final_status"]["result"] == "pass_with_risks",
        "accepted specialist fixes should pass with bounded residual scope after real follow-up proof",
    )
    assert_true(completed_follow_up["conv_state"]["round_count"] == 2, "accepted specialist fixes should record the material round and follow-up round")
    assert_true(
        completed_follow_up["conv_state"]["fix_runner_collection_status"]["status"] == "complete",
        "accepted specialist fixes should complete coordinator fix runner collection only with a result",
    )
    assert_true(
        completed_follow_up["conv_state"]["fix_runner_collection_status"]["follow_up_completed"] is True,
        "accepted specialist fixes should prove follow-up completion after a supplied result",
    )
    assert_true(
        completed_follow_up["conv_state"]["fix_runner_result_refs"][0]["source_classification"] == "fix_runner",
        "accepted specialist fixes should record a fix_runner-classified result",
    )
    assert_true(
        completed_follow_up["conv_state"]["fix_runner_result_refs"][0]["material_change_applied"] is True,
        "fix runner result should mark bounded material change application",
    )
    assert_true(
        completed_follow_up["conv_state"]["fix_runner_request_refs"][0]["agent_session_ref"] is None,
        "fix runner request should be coordinator-owned rather than a reviewer child session",
    )
    assert_true(
        completed_follow_up["conv_state"]["fix_runner_request_refs"][0]["tool_policy"]["target_mutation"]
        == "bounded_by_accepted_change_refs",
        "fix runner request should only allow bounded accepted-change mutation",
    )
    assert_true(
        completed_follow_up["conv_state"]["fix_runner_request_refs"][0]["tool_policy"]["push_or_pr"] == "forbidden",
        "fix runner request should forbid push/PR side effects",
    )
    assert_true(
        completed_follow_up["conv_state"]["stop_condition"] == "evidence_sufficient",
        "accepted specialist fixes should stop after follow-up evidence sufficiency",
    )
    assert_true(
        completed_follow_up["conv_state"]["required_evidence_contract"]["terminal_status"] == "pass_with_risks",
        "accepted specialist fixes should bind Phase 5A contract to pass_with_risks terminal status",
    )
    fix_runner_artifact_ref = completed_follow_up["conv_state"]["fix_runner_result_refs"][0]["artifact_refs"][0]
    assert_true(
        any(
            item.get("gate_id") == f"evidence:{fix_runner_artifact_ref}" and item.get("evidence_kind") == "fix_runner_result"
            for item in completed_follow_up["conv_state"]["required_evidence_contract"]["required"]
        ),
        "fix runner result artifact should be a required Phase 5A evidence gate",
    )
    run("validate", "--workflow-id", "conv-fix-specialist-findings", state_root=state_root)
    fix_runner_target.write_text("after fix but stale\n", encoding="utf-8")
    stale_source_result = run_fail("validate", "--workflow-id", "conv-fix-specialist-findings", state_root=state_root)
    assert_true(
        "after_sha256 must match current source root" in stale_source_result["error"],
        "conv validate should reject stale fix-runner source content after completed result proof",
    )
    fix_runner_target.write_text("after fix\n", encoding="utf-8")
    run("validate", "--workflow-id", "conv-fix-specialist-findings", state_root=state_root)

    partial_result_path = state_root / "conv-fix-runner-result-partial.json"
    partial_result_path.write_text(
        json.dumps(
            {
                "applied_change_refs": accepted_changes,
                "focused_check_results": [
                    {
                        "check_id": f"focused-check-{item['change_ref']}",
                        "change_ref": item["change_ref"],
                        "status": "pass",
                    }
                    for item in accepted_changes
                ],
                "material_change_applied": True,
            }
        ),
        encoding="utf-8",
    )
    partial_result = run_fail(
        "conv",
        "--text",
        "Converge execution-required target with accepted specialist fix",
        "--workflow-id",
        "conv-fix-specialist-findings-partial-result",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--structured-findings-file",
        str(fix_packet_path),
        "--fix-runner-result-file",
        str(partial_result_path),
        "--fix-runner-source-root",
        str(fix_runner_source_root),
        state_root=state_root,
    )
    assert_true(
        "fix_runner result runner_id must match request" in partial_result["error"],
        "conv should reject partial fix runner results instead of filling required identity fields",
    )

    false_material_path = state_root / "conv-fix-runner-result-false-material.json"
    false_material = json.loads(fix_runner_result_path.read_text(encoding="utf-8"))
    false_material["workflow_id"] = "conv-fix-specialist-findings-false-material"
    false_runner_id = "conv-fix-runner-conv-fix-specialist-findings-false-material-round-1"
    false_material["runner_id"] = false_runner_id
    false_material["result_id"] = f"{false_runner_id}-result"
    false_material["artifact_refs"] = [f"{false_runner_id}-result"]
    false_material["material_change_applied"] = False
    false_material_path.write_text(json.dumps(false_material), encoding="utf-8")
    false_material_result = run_fail(
        "conv",
        "--text",
        "Converge execution-required target with accepted specialist fix",
        "--workflow-id",
        "conv-fix-specialist-findings-false-material",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--structured-findings-file",
        str(fix_packet_path),
        "--fix-runner-result-file",
        str(false_material_path),
        "--fix-runner-source-root",
        str(fix_runner_source_root),
        state_root=state_root,
    )
    assert_true(
        "material_change_applied must be true" in false_material_result["error"]
        or "idempotency_key must match mutation proof" in false_material_result["error"],
        "conv should reject fix runner results that did not apply the accepted material change",
    )

    forged_result_path = state_root / "conv-fix-runner-result-forged.json"
    forged_result = json.loads(fix_runner_result_path.read_text(encoding="utf-8"))
    forged_result["workflow_id"] = "conv-fix-specialist-findings-forged-result"
    forged_runner_id = "conv-fix-runner-conv-fix-specialist-findings-forged-result-round-1"
    forged_result["runner_id"] = forged_runner_id
    forged_result["result_id"] = f"{forged_runner_id}-result"
    forged_result["artifact_refs"] = [f"{forged_runner_id}-result"]
    forged_result.pop("file_mutations", None)
    forged_result_path.write_text(json.dumps(forged_result), encoding="utf-8")
    forged_result_rejected = run_fail(
        "conv",
        "--text",
        "Converge execution-required target with accepted specialist fix",
        "--workflow-id",
        "conv-fix-specialist-findings-forged-result",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--structured-findings-file",
        str(fix_packet_path),
        "--fix-runner-result-file",
        str(forged_result_path),
        "--fix-runner-source-root",
        str(fix_runner_source_root),
        state_root=state_root,
    )
    assert_true(
        "file_mutations" in forged_result_rejected["error"],
        "conv should reject forged fix runner results without file mutation proof",
    )

    weak_check_path = state_root / "conv-fix-runner-result-weak-check.json"
    weak_check = json.loads(fix_runner_result_path.read_text(encoding="utf-8"))
    weak_check["workflow_id"] = "conv-fix-specialist-findings-weak-check"
    weak_check_runner_id = "conv-fix-runner-conv-fix-specialist-findings-weak-check-round-1"
    weak_check["runner_id"] = weak_check_runner_id
    weak_check["result_id"] = f"{weak_check_runner_id}-result"
    weak_check["artifact_refs"] = [f"{weak_check_runner_id}-result"]
    weak_check["focused_check_results"][0].pop("mutation_hashes", None)
    weak_check["idempotency_key"] = stable_hash(
        {
            "runner_id": weak_check["runner_id"],
            "workflow_id": weak_check["workflow_id"],
            "source_root": weak_check["source_root"],
            "accepted_change_refs": weak_check["accepted_change_refs"],
            "file_mutations": weak_check["file_mutations"],
        }
    )
    weak_check_path.write_text(json.dumps(weak_check), encoding="utf-8")
    weak_check_rejected = run_fail(
        "conv",
        "--text",
        "Converge execution-required target with accepted specialist fix",
        "--workflow-id",
        "conv-fix-specialist-findings-weak-check",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--structured-findings-file",
        str(fix_packet_path),
        "--fix-runner-result-file",
        str(weak_check_path),
        "--fix-runner-source-root",
        str(fix_runner_source_root),
        state_root=state_root,
    )
    assert_true(
        "focused checks must bind mutation hashes" in weak_check_rejected["error"],
        "conv should reject fix runner results whose focused checks do not bind mutation hashes",
    )

    wrong_before_path = state_root / "conv-fix-runner-result-wrong-before.json"
    wrong_before = json.loads(fix_runner_result_path.read_text(encoding="utf-8"))
    wrong_before["workflow_id"] = "conv-fix-specialist-findings-wrong-before"
    wrong_before_runner_id = "conv-fix-runner-conv-fix-specialist-findings-wrong-before-round-1"
    wrong_before["runner_id"] = wrong_before_runner_id
    wrong_before["result_id"] = f"{wrong_before_runner_id}-result"
    wrong_before["artifact_refs"] = [f"{wrong_before_runner_id}-result"]
    wrong_before["file_mutations"][0]["before_sha256"] = "0" * 64
    wrong_before["focused_check_results"][0]["mutation_hashes"][0]["before_sha256"] = "0" * 64
    wrong_before["idempotency_key"] = stable_hash(
        {
            "runner_id": wrong_before["runner_id"],
            "workflow_id": wrong_before["workflow_id"],
            "source_root": wrong_before["source_root"],
            "accepted_change_refs": wrong_before["accepted_change_refs"],
            "file_mutations": wrong_before["file_mutations"],
        }
    )
    wrong_before_path.write_text(json.dumps(wrong_before), encoding="utf-8")
    wrong_before_rejected = run_fail(
        "conv",
        "--text",
        "Converge execution-required target with accepted specialist fix",
        "--workflow-id",
        "conv-fix-specialist-findings-wrong-before",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--structured-findings-file",
        str(fix_packet_path),
        "--fix-runner-result-file",
        str(wrong_before_path),
        "--fix-runner-source-root",
        str(fix_runner_source_root),
        state_root=state_root,
    )
    assert_true(
        "mutation proof must match accepted local_file_edits" in wrong_before_rejected["error"],
        "conv should reject fix runner mutation proof that is not bound to accepted local_file_edits",
    )

    wrong_source_root = state_root / "wrong-fix-runner-source"
    wrong_source_root.mkdir()
    (wrong_source_root / "target.txt").write_text("before fix\n", encoding="utf-8")
    wrong_source_result_path = state_root / "conv-fix-runner-result-wrong-source.json"
    wrong_source_packet = json.loads(fix_runner_result_path.read_text(encoding="utf-8"))
    wrong_source_packet["workflow_id"] = "conv-fix-specialist-findings-wrong-source"
    wrong_source_runner_id = "conv-fix-runner-conv-fix-specialist-findings-wrong-source-round-1"
    wrong_source_packet["runner_id"] = wrong_source_runner_id
    wrong_source_packet["result_id"] = f"{wrong_source_runner_id}-result"
    wrong_source_packet["artifact_refs"] = [f"{wrong_source_runner_id}-result"]
    wrong_source_packet["idempotency_key"] = stable_hash(
        {
            "runner_id": wrong_source_packet["runner_id"],
            "workflow_id": wrong_source_packet["workflow_id"],
            "source_root": wrong_source_packet["source_root"],
            "accepted_change_refs": wrong_source_packet["accepted_change_refs"],
            "file_mutations": wrong_source_packet["file_mutations"],
        }
    )
    wrong_source_result_path.write_text(json.dumps(wrong_source_packet), encoding="utf-8")
    wrong_source_result = run_fail(
        "conv",
        "--text",
        "Converge execution-required target with accepted specialist fix",
        "--workflow-id",
        "conv-fix-specialist-findings-wrong-source",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--structured-findings-file",
        str(fix_packet_path),
        "--fix-runner-result-file",
        str(wrong_source_result_path),
        "--fix-runner-source-root",
        str(wrong_source_root),
        state_root=state_root,
    )
    assert_true(
        "source_root must match supplied source root" in wrong_source_result["error"],
        "conv should reject fix runner results produced against a different source root",
    )

    atomic_packet = specialist_packet()
    atomic_packet["findings"][0]["severity"] = "p2"
    atomic_source_root = state_root / "fix-runner-atomic-source"
    atomic_source_root.mkdir()
    atomic_first = atomic_source_root / "first.txt"
    atomic_second = atomic_source_root / "second.txt"
    atomic_first.write_text("first before\n", encoding="utf-8")
    atomic_second.write_text("second before\n", encoding="utf-8")
    atomic_packet["findings"][0]["local_file_edits"] = [
        {"path": "first.txt", "old": "first before\n", "new": "first after\n"},
        {"path": "second.txt", "old": "missing old text\n", "new": "second after\n"},
    ]
    atomic_packet_path = state_root / "conv-fix-specialist-atomic.json"
    atomic_packet_path.write_text(json.dumps(atomic_packet), encoding="utf-8")
    run(
        "conv",
        "--text",
        "Converge execution-required target with multi-edit accepted specialist fix",
        "--workflow-id",
        "conv-fix-specialist-atomic",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--structured-findings-file",
        str(atomic_packet_path),
        state_root=state_root,
    )
    atomic_failure = run_fail(
        "fix-runner",
        "--workflow-id",
        "conv-fix-specialist-atomic",
        "--source-root",
        str(atomic_source_root),
        state_root=state_root,
    )
    assert_true(
        "old text must match exactly once" in atomic_failure["error"],
        "fix-runner should fail before applying any edit when one bounded edit is invalid",
    )
    assert_true(
        atomic_first.read_text(encoding="utf-8") == "first before\n",
        "fix-runner should not leave partial file mutations after preflight failure",
    )

    fix_runner_completed_events = [event for event in events(state_root, "conv-fix-specialist-findings") if event["event_type"] == "fix_runner_completed"]
    assert_true(len(fix_runner_completed_events) == 1, "accepted specialist fixes should record fix_runner_completed proof")
    assert_true(
        fix_runner_completed_events[0]["payload"]["result_ids"]
        == [completed_follow_up["conv_state"]["fix_runner_result_refs"][0]["result_id"]],
        "fix_runner_completed event should bind result ids to state",
    )
    fix_runner_original_events = events(state_root, "conv-fix-specialist-findings")
    fix_runner_events_path = state_root / "workflows" / "conv-fix-specialist-findings" / "events.jsonl"
    fix_runner_event_drift = json.loads(json.dumps(fix_runner_original_events))
    for event in fix_runner_event_drift:
        if event["event_type"] == "fix_runner_completed":
            event["payload"]["collection_cursor"] = "stale-fix-runner-cursor"
    fix_runner_events_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in fix_runner_event_drift) + "\n",
        encoding="utf-8",
    )
    fix_runner_drift_result = run_fail("validate", "--workflow-id", "conv-fix-specialist-findings", state_root=state_root)
    assert_true(
        "conv fix_runner completed event collection_cursor must match state" in fix_runner_drift_result["error"],
        "conv should bind completed fix runner event cursor to state",
    )
    fix_runner_events_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in fix_runner_original_events) + "\n",
        encoding="utf-8",
    )
    fix_runner_event_drift = json.loads(json.dumps(fix_runner_original_events))
    for event in fix_runner_event_drift:
        if event["event_type"] == "fix_runner_completed":
            event["payload"]["result_ids"] = []
    fix_runner_events_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in fix_runner_event_drift) + "\n",
        encoding="utf-8",
    )
    fix_runner_completed_drift = run_fail("validate", "--workflow-id", "conv-fix-specialist-findings", state_root=state_root)
    assert_true(
        "conv fix_runner completed event result_ids must match state" in fix_runner_completed_drift["error"],
        "conv should bind fix runner completed event result ids to state",
    )
    fix_runner_events_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in fix_runner_original_events) + "\n",
        encoding="utf-8",
    )
    fix_runner_event_drift = json.loads(json.dumps(fix_runner_original_events))
    for event in fix_runner_event_drift:
        if event["event_type"] == "fix_runner_completed":
            event["payload"]["artifact_refs"] = ["stale-artifact"]
    fix_runner_events_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in fix_runner_event_drift) + "\n",
        encoding="utf-8",
    )
    fix_runner_artifact_drift = run_fail("validate", "--workflow-id", "conv-fix-specialist-findings", state_root=state_root)
    assert_true(
        "conv fix_runner completed event artifact_refs must match state" in fix_runner_artifact_drift["error"],
        "conv should bind fix runner completed event artifact refs to state",
    )
    fix_runner_events_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in fix_runner_original_events) + "\n",
        encoding="utf-8",
    )


def assert_conv_records_native_specialist_panel(state_root: Path) -> None:
    native_store = WorkflowStore(state_root)
    native_workflow = native_store.create_workflow(
        kind="conv",
        text="Converge with native OpenClaw child panel",
        workflow_id="conv-native-panel",
        owner_session_key="session:test",
        visible_delivery={"channel": "telegram", "target": "test"},
    )
    ConvHandler(native_store).finalize_conv(native_workflow["workflow_id"], native_agent_backend=FakeNativePanelBackend())
    native_conv = workflow(state_root, "conv-native-panel")
    native_state = native_conv["conv_state"]
    assert_true(native_conv["status"] == "completed_unreported", "native conv panel should allow completion")
    assert_true(native_state["execution_source"] == "native_agent_panel", "native conv should carry native execution source")
    assert_true(native_state["satisfies_native_agent_panel"] is True, "native conv should satisfy native panel parity")
    assert_true(native_state["round_count"] == 1, "native conv should remain a single bounded round")
    assert_true(len(native_state["agent_request_refs"]) == 3, "native conv should launch exactly three child requests")
    assert_true(len(native_state["agent_result_refs"]) == 3, "native conv should collect one result per child")
    assert_true(
        all(item["session_key"].startswith("agent:main:converge-") for item in native_state["agent_request_refs"]),
        "native conv should persist explicit child session keys",
    )
    assert_true(
        all(
            item["tool_policy"]["filesystem"] == "read_only"
            and item["tool_policy"]["shell"] == "status_only"
            and item["tool_policy"]["target_mutation"] == "forbidden"
            and item["tool_policy"]["visible_messages"] == "forbidden"
            and item["tool_policy"]["external_actions"] == "forbidden"
            for item in native_state["agent_request_refs"]
        ),
        "native conv requests should carry explicit read-only reviewer tool policy",
    )
    assert_true(
        all(item["tool_smoke_status"] == "passed" and item["tool_smoke_evidence"] for item in native_state["agent_result_refs"]),
        "native conv results should carry coordinator-verified tool-smoke evidence",
    )
    assert_true(
        all(
            item["profile_id"].startswith("native-conv-")
            and item["source_provenance"] == "native_openclaw_session"
            for item in native_state["agent_finding_refs"]
        ),
        "native conv should force native finding profile/provenance onto child findings",
    )
    assert_conv_report_matches_state(native_conv)
    run("validate", "--workflow-id", "conv-native-panel", state_root=state_root)

    fake_openclaw = _write_fake_openclaw_cli(state_root / "fake-openclaw-conv", include_tool_smoke_evidence=True)
    native_cli_conv = run(
        "conv",
        "--text",
        "Converge native CLI child panel",
        "--workflow-id",
        "conv-native-cli-panel",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--native-panel-openclaw-cli",
        "--native-panel-openclaw-bin",
        str(fake_openclaw),
        state_root=state_root,
    )
    native_cli_state = native_cli_conv["workflow"]["conv_state"]
    assert_true(native_cli_conv["workflow"]["status"] == "completed_unreported", "native CLI conv panel should complete")
    assert_true(native_cli_state["execution_source"] == "native_agent_panel", "native CLI conv should carry native source")
    assert_true(native_cli_state["satisfies_native_agent_panel"] is True, "native CLI conv should satisfy native panel only after proof")
    assert_true(
        all(
            item["tool_smoke_evidence"]["kind"] == "coordinator_verified_child_tool_smoke_session_and_trajectory_binding"
            and item["tool_smoke_evidence"]["session_store_proof"]["session_key"] == item["session_key"]
            and item["tool_smoke_evidence"]["trajectory_proof"]["session_key"] == item["session_key"]
            and item["tool_smoke_evidence"]["trajectory_proof"]["tool_call_count"] >= 1
            and item["tool_smoke_evidence"]["trajectory_proof"]["tool_result_count"] >= 1
            for item in native_cli_state["agent_result_refs"]
        ),
        "native CLI conv should persist coordinator-verified smoke, session, and trajectory proof",
    )
    run("validate", "--workflow-id", "conv-native-cli-panel", state_root=state_root)

    fake_openclaw_no_evidence = _write_fake_openclaw_cli(state_root / "fake-openclaw-conv-no-evidence", include_tool_smoke_evidence=False)
    native_cli_missing_smoke = run_fail(
        "conv",
        "--text",
        "Converge native CLI child panel without smoke evidence",
        "--workflow-id",
        "conv-native-cli-missing-smoke",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--native-panel-openclaw-cli",
        "--native-panel-openclaw-bin",
        str(fake_openclaw_no_evidence),
        state_root=state_root,
    )
    assert_true(
        "tool_smoke_evidence" in native_cli_missing_smoke["error"],
        "native CLI conv should fail closed when coordinator cannot verify child smoke evidence",
    )

    fake_openclaw_failed_smoke = _write_fake_openclaw_cli(
        state_root / "fake-openclaw-conv-failed-smoke",
        include_tool_smoke_evidence=True,
        tool_smoke_status="failed",
    )
    native_cli_failed_smoke = run_fail(
        "conv",
        "--text",
        "Converge native CLI child panel with failed smoke evidence",
        "--workflow-id",
        "conv-native-cli-failed-smoke",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--native-panel-openclaw-cli",
        "--native-panel-openclaw-bin",
        str(fake_openclaw_failed_smoke),
        state_root=state_root,
    )
    assert_true(
        "tool_smoke_status=passed" in native_cli_failed_smoke["error"],
        "native CLI conv should fail closed when child tool smoke fails",
    )

    fake_openclaw_no_session = _write_fake_openclaw_cli(
        state_root / "fake-openclaw-conv-no-session",
        include_tool_smoke_evidence=True,
        include_session_store_proof=False,
    )
    native_cli_missing_session = run_fail(
        "conv",
        "--text",
        "Converge native CLI child panel without session store proof",
        "--workflow-id",
        "conv-native-cli-missing-session-proof",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--native-panel-openclaw-cli",
        "--native-panel-openclaw-bin",
        str(fake_openclaw_no_session),
        state_root=state_root,
    )
    assert_true(
        "session_key" in native_cli_missing_session["error"],
        "native CLI conv should fail closed when OpenClaw session store proof is missing",
    )

    tampered_cli = json.loads(json.dumps(native_cli_conv["workflow"]))
    tampered_cli["conv_state"]["agent_result_refs"][0]["tool_smoke_evidence"]["trajectory_proof"]["tool_result_count"] = 0
    write_workflow(state_root, "conv-native-cli-panel", tampered_cli)
    tampered_cli_result = run_fail("validate", "--workflow-id", "conv-native-cli-panel", state_root=state_root)
    assert_true(
        "conv_state must match terminal checkpoint" in tampered_cli_result["error"],
        "native CLI conv should fail closed if persisted trajectory proof is altered",
    )
    write_workflow(state_root, "conv-native-cli-panel", native_cli_conv["workflow"])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="converge-conv-mode-smoke-") as tmp:
        state_root = Path(tmp)
        assert_default_conv_contract(state_root)
        assert_execution_required_conv_blocks_synthetic_round(state_root)
        assert_execution_required_conv_records_real_round_evidence(state_root)
        assert_resume_preserves_progress(state_root)
        assert_material_change_and_max_round_fixtures(state_root)
        assert_conv_integrity_rejects_drift(state_root)
        assert_conv_gate_integrity_rejects_drift(state_root)
        assert_conv_preterminal_state_is_validated(state_root)
        assert_reserve_validates_conv_terminal_material_before_send(state_root)
        assert_conv_records_structured_specialist_findings(state_root)
        assert_conv_records_native_specialist_panel(state_root)
    return 0


def _record_from_state(state: dict[str, Any]) -> ConvRecord:
    return ConvRecord(
        target=state["target"],
        max_rounds=state["max_rounds"],
        rounds=[
            ConvRound(
                round_index=item["round_index"],
                target_ref=item["target_ref"],
                original_target_gate=item["original_target_gate"],
                delta_gate=item["delta_gate"],
                findings=item["findings"],
                material_changes=item["material_changes"],
                follow_up_required=item["follow_up_required"],
                evidence_sufficient=item["evidence_sufficient"],
                summary=item["summary"],
            )
            for item in state["rounds"]
        ],
        stop_condition=state["stop_condition"],
        stop_reason=state["stop_reason"],
        explicit_stop_proof=state["explicit_stop_proof"],
        residuals=state["residuals"],
        final_report_summary=state["final_report_summary"],
    )


def specialist_packet() -> dict[str, Any]:
    return {
        "panel_id": "conv-phase4a-panel",
        "risk_level": "medium",
        "profiles": [
            {
                "profile_id": "reviewer-integrity",
                "role": "execution integrity reviewer",
                "expertise": ["evidence binding", "state validation"],
                "likely_failure_modes": ["missing arbitration proof"],
                "prohibited_actions": ["visible_messages", "target_mutation"],
            },
            {
                "profile_id": "reviewer-regression",
                "role": "regression reviewer",
                "expertise": ["phase regression", "schema compatibility"],
                "likely_failure_modes": ["missing arbitration proof"],
                "prohibited_actions": ["external_actions", "push_or_pr"],
            },
            {
                "profile_id": "reviewer-ops",
                "role": "operations boundary reviewer",
                "expertise": ["side-effect containment", "report proof"],
                "likely_failure_modes": ["unsafe side effects"],
                "prohibited_actions": ["service_restart", "workflow_state_mutation"],
            },
        ],
        "findings": [
            {
                "finding_id": "conv-finding-arbitration-event",
                "profile_id": "reviewer-integrity",
                "finding": "Conv must require an arbitration event for structured specialist evidence.",
                "severity": "p3",
                "evidence": "events.jsonl finding_arbitrated",
                "why_it_matters": "A missing arbitration event would allow unclassified findings into the verdict.",
                "minimal_fix_or_test": "Remove finding_arbitrated and expect validate failure.",
                "scope_risk": "Low, validation-only proof requirement.",
                "confidence": 0.87,
                "failure_mode": "missing arbitration proof",
                "source_provenance": "runner_provided",
            },
            {
                "finding_id": "conv-finding-dedupe",
                "profile_id": "reviewer-regression",
                "finding": "Duplicate failure-mode findings must collapse into one arbitration group.",
                "severity": "p3",
                "evidence": "conv_state.raw_finding_to_group_map",
                "why_it_matters": "Duplicate findings should not inflate convergence severity.",
                "minimal_fix_or_test": "Assert both raw findings map to the same failure-mode group.",
                "scope_risk": "Low, adapter-local grouping.",
                "confidence": 0.82,
                "failure_mode": "missing arbitration proof",
                "source_provenance": "runner_provided",
            },
        ],
        "side_effects_performed": [],
    }


class FakeNativePanelBackend:
    def run_panel(self, requests):
        results = []
        for index, request in enumerate(requests, start=1):
            completed_at = f"2026-05-29T00:0{index}:00Z"
            finding = {
                "finding_id": f"native-conv-finding-{index}",
                "profile_id": "child-supplied-wrong-profile",
                "finding": f"Native child {index} inspected the conv target without blocking findings.",
                "severity": "p3",
                "evidence": f"agent_session_ref:{request.session_key}",
                "why_it_matters": "Conv native parity requires explicit child session evidence.",
                "minimal_fix_or_test": "Keep conv validation bound to native child session refs and tool-smoke evidence.",
                "scope_risk": "native-panel",
                "confidence": 0.81,
                "failure_mode": "native evidence binding",
                "source_provenance": "runner_provided",
            }
            results.append(
                NativeChildResult(
                    request_id=request.request_id,
                    result_id=f"native-conv-result-{index}",
                    agent_session_ref=request.session_key,
                    session_key=request.session_key,
                    tool_smoke_status="passed",
                    profile_ref=request.profile_ref,
                    context_hash=request.context_hash,
                    status="completed",
                    findings=[finding],
                    started_at=f"2026-05-29T00:0{index}:00Z",
                    deadline_at=f"2026-05-29T00:1{index}:00Z",
                    completed_at=completed_at,
                    tool_smoke_evidence={
                        "status": "passed",
                        "session_key": request.session_key,
                        "agent_session_ref": request.session_key,
                        "kind": "coordinator_verified_child_tool_smoke_session_and_trajectory_binding",
                        "checked_at": completed_at,
                        "verification_scope": "fixture_child_claim_bound_to_explicit_session_refs_with_openclaw_session_store_and_trajectory_tool_events",
                        "child_tool_smoke_kind": "fixture",
                        "child_tool_smoke_checked_at": completed_at,
                        "session_store_proof": {
                            "session_key": request.session_key,
                            "session_id": f"fixture-conv-session-{index}",
                            "updated_at": 1779981795923 + index,
                            "agent_id": "converge",
                            "kind": "spawn-child",
                        },
                        "trajectory_proof": {
                            "session_key": request.session_key,
                            "output_dir": f"/tmp/fixture-conv-trajectory-{index}",
                            "event_count": 2,
                            "runtime_event_count": 0,
                            "transcript_event_count": 2,
                            "tool_call_count": 1,
                            "tool_result_count": 1,
                            "tool_names": ["fixture_tool"],
                        },
                    },
                )
            )
        return results


if __name__ == "__main__":
    raise SystemExit(main())
