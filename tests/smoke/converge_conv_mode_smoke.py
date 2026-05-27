#!/usr/bin/env python3
"""Smoke coverage for C3 conv mode."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


try:
    from smoke_helpers import VISIBLE_DELIVERY, assert_phase5a_contract, assert_phase5a_accepted_change_stale_rejected, assert_phase5a_freshness_rejected, assert_phase5a_missing_gate_rejected, assert_phase5a_stale_hash_rejected, assert_phase5a_terminal_status_rejected, assert_true, events, run, run_fail, workflow, write_workflow
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import VISIBLE_DELIVERY, assert_phase5a_contract, assert_phase5a_accepted_change_stale_rejected, assert_phase5a_freshness_rejected, assert_phase5a_missing_gate_rejected, assert_phase5a_stale_hash_rejected, assert_phase5a_terminal_status_rejected, assert_true, events, run, run_fail, workflow, write_workflow
from converge.modes.conv import ConvRecord, ConvRound, _material_change_record, _max_round_record, build_conv_record, render_conv_report, validate_conv_state  # noqa: E402


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
        f"Improve execution-required target {target}",
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
    assert_true(conv_state["execution_evidence_refs"] == ["conv-specialist-findings"], "conv should bind specialist evidence")
    assert_true(conv_state["max_rounds_default"] == 5, "conv should preserve Phase 4 max_rounds default")
    assert_true(conv_state["round_count"] == 1, "conv specialist adapter should record one bounded round")
    assert_true(
        conv_state["raw_finding_to_group_map"][0]["group_id"] == conv_state["raw_finding_to_group_map"][1]["group_id"],
        "conv should dedupe structured findings by failure mode",
    )
    assert_true(len(conv_state["agent_request_refs"]) == 3, "conv should persist one agent request per profile")
    assert_true(len(conv_state["agent_result_refs"]) == len(conv_state["agent_finding_refs"]), "conv should persist specialist result refs")
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

    fix_packet = specialist_packet()
    fix_packet["findings"][0]["severity"] = "p2"
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
    assert_true(needs_follow_up["status"] == "failed_unreported", "accepted specialist fixes should require follow-up before completion")
    assert_true(needs_follow_up["final_status"]["result"] == "blocked", "accepted specialist fixes should not pass before follow-up")
    assert_true(needs_follow_up["conv_state"]["follow_up_required"] is True, "accepted specialist fixes should record follow-up required")
    assert_true(
        needs_follow_up["conv_state"]["stop_condition"] == "blocked_specialist_follow_up_required",
        "accepted specialist fixes should bind follow-up stop condition",
    )


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


if __name__ == "__main__":
    raise SystemExit(main())
