#!/usr/bin/env python3
"""Smoke coverage for C2 verify mode."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


try:
    from smoke_helpers import ROOT, assert_phase5a_contract, assert_phase5a_accepted_change_stale_rejected, assert_phase5a_freshness_rejected, assert_phase5a_missing_gate_rejected, assert_phase5a_stale_hash_rejected, assert_phase5a_terminal_status_rejected, assert_true, events, run, run_fail, workflow, write_events, write_workflow
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import ROOT, assert_phase5a_contract, assert_phase5a_accepted_change_stale_rejected, assert_phase5a_freshness_rejected, assert_phase5a_missing_gate_rejected, assert_phase5a_stale_hash_rejected, assert_phase5a_terminal_status_rejected, assert_true, events, run, run_fail, workflow, write_events, write_workflow
from converge.artifacts import sha256_file  # noqa: E402
from converge.messages import format_final  # noqa: E402
from converge.modes.verify import build_verify_record, render_verify_report  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="converge-verify-mode-smoke-") as tmp:
        state_root = Path(tmp)
        visible_delivery = '{"channel":"telegram","target":"test"}'
        payload = run(
            "verify",
            "--text",
            "plan-only Audit C2 verify mode contract only",
            "--workflow-id",
            "verify-c2-smoke",
            "--owner-session-key",
            "session:test",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        wf = payload["workflow"]
        assert_true(wf["kind"] == "verify", "verify command should create a verify workflow")
        assert_true(wf["status"] == "completed_unreported", "verify mode should stop at terminal unreported")
        assert_true(wf["continuation_plan"] is None, "verify mode should keep the short-workflow contract")
        assert_true(wf["verify_state"]["final_report_artifact_id"] == "verify-final-report", "verify state should point to final report")
        assert_true(wf["verify_state"]["final_report_artifact_path"] == wf["artifacts"][0]["path"], "verify state should use registered artifact path")
        assert_true(wf["verify_state"]["verdict"] == wf["final_status"]["result"], "verify verdict should match final status")
        assert_true(wf["verify_state"]["evidence"], "verify state should carry evidence records")
        assert_true(wf["verify_state"]["residuals"]["accepted_risks"], "verify state should carry accepted risks")
        assert_true(wf["final_status"]["result"] == "pass_with_risks", "verify final status should preserve accepted C2 risk")
        assert_true(wf["next_safe_action"]["action_type"] == "report_terminal_status", "verify terminal state should require report flow")

        artifact = wf["artifacts"][0]
        artifact_path = Path(artifact["path"])
        assert_true(artifact["artifact_id"] == "verify-final-report", "verify report artifact id mismatch")
        assert_true(artifact["kind"] == "report", "verify report artifact kind mismatch")
        assert_true(artifact_path.is_file(), "verify report artifact should be materialized")
        assert_true("## Verdict" in artifact_path.read_text(encoding="utf-8"), "verify report should include verdict")

        event_types = [event["event_type"] for event in events(state_root, "verify-c2-smoke")]
        assert_true(event_types == ["start", "artifact", "complete"], "verify mode should use start/artifact/complete events only")
        assert_true(format_final(wf).startswith("■ Verification final"), "verify final report marker mismatch")

        blocked_verify = run(
            "verify",
            "--text",
            "Audit execution-required target",
            "--workflow-id",
            "verify-execution-required-blocked",
            "--owner-session-key",
            "session:test",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )["workflow"]
        assert_true(blocked_verify["status"] == "failed_unreported", "execution-required verify should fail closed")
        assert_true(blocked_verify["final_status"]["result"] == "blocked", "execution-required verify should be blocked")
        assert_true(
            blocked_verify["final_status"]["stop_reason"] == "blocked_no_execution_evidence",
            "verify should block on missing execution evidence",
        )
        assert_true(
            blocked_verify["verify_state"]["execution_required"] is True
            and blocked_verify["verify_state"]["execution_performed"] is False,
            "verify should record execution truth markers",
        )
        assert_true(
            [event["event_type"] for event in events(state_root, "verify-execution-required-blocked")] == ["start", "artifact", "fail"],
            "execution-required verify should fail terminally instead of completing",
        )
        run("validate", "--workflow-id", "verify-execution-required-blocked", state_root=state_root)

        read_only_verify = run(
            "verify",
            "--text",
            "Review PR read-only with no code changes but verify execution evidence",
            "--workflow-id",
            "verify-read-only-still-execution-required",
            "--owner-session-key",
            "session:test",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )["workflow"]
        assert_true(
            read_only_verify["status"] == "failed_unreported",
            "read-only verify still requires execution evidence",
        )
        assert_true(
            read_only_verify["verify_state"]["execution_required"] is True,
            "read-only/no-code-change wording must not downgrade verify to plan-only",
        )
        run("validate", "--workflow-id", "verify-read-only-still-execution-required", state_root=state_root)

        target_file = state_root / "fixture-target.txt"
        target_file.write_text("phase 1 deterministic evidence fixture\n", encoding="utf-8")
        deterministic_verify = run(
            "verify",
            "--text",
            f"Verify deterministic evidence for {target_file}",
            "--workflow-id",
            "verify-deterministic-file-evidence",
            "--owner-session-key",
            "session:test",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )["workflow"]
        deterministic_state = deterministic_verify["verify_state"]
        assert_true(deterministic_verify["status"] == "completed_unreported", "deterministic evidence should allow verify completion")
        assert_true(deterministic_verify["final_status"]["result"] == "pass_with_risks", "deterministic verify should keep accepted-risk verdict")
        assert_true(
            deterministic_state["execution_required"] is True
            and deterministic_state["execution_performed"] is True
            and deterministic_state["synthetic_report"] is False,
            "trusted deterministic checks should earn execution truth markers",
        )
        assert_true(
            deterministic_state["execution_capability"] == "local_checks",
            "deterministic verify should record local_checks capability",
        )
        assert_true(
            deterministic_state["execution_evidence_refs"] == ["verify-deterministic-checks"],
            "deterministic verify should bind execution evidence refs",
        )
        assert_true(
            any("file_inspection passed" in item for item in deterministic_state["deterministic_checks"]),
            "deterministic check summary should be target-specific",
        )
        assert_true(
            any(event["event_type"] == "deterministic_check_recorded" for event in events(state_root, "verify-deterministic-file-evidence")),
            "deterministic check should be event-backed",
        )
        artifact_ids = {artifact["artifact_id"] for artifact in deterministic_verify["artifacts"]}
        assert_true(
            {"verify-deterministic-checks", "verify-final-report"}.issubset(artifact_ids),
            "deterministic verify should register evidence and report artifacts",
        )
        run("validate", "--workflow-id", "verify-deterministic-file-evidence", state_root=state_root)
        assert_phase5a_contract(deterministic_verify, "verify_state")
        assert_phase5a_missing_gate_rejected(
            state_root,
            "verify-deterministic-file-evidence",
            "verify_state",
            "execution:verify-deterministic-checks",
        )
        assert_phase5a_stale_hash_rejected(
            state_root,
            "verify-deterministic-file-evidence",
            "verify_state",
            "execution:verify-deterministic-checks",
        )
        assert_phase5a_freshness_rejected(state_root, "verify-deterministic-file-evidence", "verify_state")
        assert_phase5a_terminal_status_rejected(state_root, "verify-deterministic-file-evidence", "verify_state")
        assert_phase5a_accepted_change_stale_rejected(state_root, "verify-deterministic-file-evidence", "verify_state")
        deterministic_events = events(state_root, "verify-deterministic-file-evidence")
        write_events(
            state_root,
            "verify-deterministic-file-evidence",
            [event for event in deterministic_events if event["event_type"] != "deterministic_check_recorded"],
        )
        missing_event = run_fail("validate", "--workflow-id", "verify-deterministic-file-evidence", state_root=state_root)
        assert_true(
            "deterministic_check_recorded event" in missing_event["error"],
            "execution_performed=true should require trusted deterministic event proof",
        )
        write_events(state_root, "verify-deterministic-file-evidence", deterministic_events)
        bad_runner_events = json.loads(json.dumps(deterministic_events))
        for event in bad_runner_events:
            if event["event_type"] == "deterministic_check_recorded":
                event["payload"]["runner_ref"] = "untrusted-runner"
        write_events(state_root, "verify-deterministic-file-evidence", bad_runner_events)
        bad_runner = run_fail("validate", "--workflow-id", "verify-deterministic-file-evidence", state_root=state_root)
        assert_true(
            "untrusted runner_ref" in bad_runner["error"],
            "execution_performed=true should reject untrusted runner refs",
        )
        duplicate_events = json.loads(json.dumps(deterministic_events))
        deterministic_event = next(event for event in deterministic_events if event["event_type"] == "deterministic_check_recorded")
        duplicate_event = json.loads(json.dumps(deterministic_event))
        duplicate_event["event_id"] = "evt-deterministic-check-recorded-duplicate"
        duplicate_events.append(duplicate_event)
        write_events(state_root, "verify-deterministic-file-evidence", duplicate_events)
        duplicate_runner = run_fail("validate", "--workflow-id", "verify-deterministic-file-evidence", state_root=state_root)
        assert_true(
            "exactly one deterministic_check_recorded event" in duplicate_runner["error"],
            "execution_performed=true should reject duplicate deterministic runner events",
        )
        write_events(state_root, "verify-deterministic-file-evidence", deterministic_events)

        packet = specialist_packet()
        packet_path = state_root / "verify-specialist-findings.json"
        packet_path.write_text(json.dumps(packet), encoding="utf-8")
        specialist_verify = run(
            "verify",
            "--text",
            "Verify specialist structured findings without local file evidence",
            "--workflow-id",
            "verify-specialist-findings",
            "--owner-session-key",
            "session:test",
            "--visible-delivery",
            visible_delivery,
            "--structured-findings-file",
            str(packet_path),
            state_root=state_root,
        )["workflow"]
        specialist_state = specialist_verify["verify_state"]
        assert_true(specialist_verify["status"] == "completed_unreported", "structured findings should allow verify completion")
        assert_true(specialist_state["execution_capability"] == "delegated_agents", "structured findings should use delegated_agents capability")
        assert_true(specialist_state["execution_evidence_refs"] == ["verify-specialist-findings"], "structured findings should bind specialist evidence")
        assert_true(len(specialist_state["review_panel_spec"]["profiles"]) == 3, "structured findings should preserve panel profiles")
        assert_true(
            specialist_state["raw_finding_to_group_map"][0]["group_id"] == specialist_state["raw_finding_to_group_map"][1]["group_id"],
            "structured findings should dedupe by failure mode",
        )
        assert_true(
            {item["event_type"] for item in events(state_root, "verify-specialist-findings")}.issuperset(
                {"agent_panel_requested", "agent_findings_recorded", "finding_arbitrated"}
            ),
            "structured findings should be event-backed",
        )
        assert_true(len(specialist_state["agent_request_refs"]) == 3, "structured findings should persist one agent request per profile")
        assert_true(len(specialist_state["agent_result_refs"]) == len(specialist_state["agent_finding_refs"]), "structured findings should persist agent results")
        assert_true(
            specialist_state["agent_result_collection_status"]["status"] == "complete",
            "structured findings should record complete agent result collection",
        )
        assert_true(
            specialist_state["agent_result_collection_status"]["relaunch_required"] is False,
            "completed structured findings should not require relaunch on recovery",
        )
        assert_true(
            specialist_state["recovery_resume_cursor"] == specialist_state["agent_result_collection_status"]["collection_cursor"],
            "structured findings should bind recovery cursor to collection cursor",
        )
        recovery_scan = run("scan", state_root=state_root)
        recovery_record = next(item for item in recovery_scan["workflows"] if item["workflow_id"] == "verify-specialist-findings")
        assert_true(
            recovery_record["agent_result_collection"]["recovery_resume_cursor"] == specialist_state["recovery_resume_cursor"],
            "recovery scan should expose specialist collection resume cursor",
        )
        recovery_watchdog = run("watchdog-check", state_root=state_root)
        recovery_packet = next(item for item in recovery_watchdog["recoveries"] if item["workflow_id"] == "verify-specialist-findings")
        assert_true(
            recovery_packet["agent_result_collection"]["relaunch_required"] is False,
            "recovery packet should prove completed specialist requests are not relaunched",
        )
        run("validate", "--workflow-id", "verify-specialist-findings", state_root=state_root)
        specialist_events = events(state_root, "verify-specialist-findings")
        write_events(
            state_root,
            "verify-specialist-findings",
            [event for event in specialist_events if event["event_type"] != "finding_arbitrated"],
        )
        missing_specialist_event = run_fail("validate", "--workflow-id", "verify-specialist-findings", state_root=state_root)
        assert_true("finding_arbitrated event" in missing_specialist_event["error"], "specialist execution should require arbitration event proof")
        write_events(state_root, "verify-specialist-findings", specialist_events)

        pending_collection = json.loads(json.dumps(specialist_verify))
        pending_collection["verify_state"]["agent_result_collection_status"]["status"] = "partial"
        write_workflow(state_root, "verify-specialist-findings", pending_collection)
        pending_result = run_fail("validate", "--workflow-id", "verify-specialist-findings", state_root=state_root)
        assert_true(
            "collection" in pending_result["error"] or "terminal checkpoint" in pending_result["error"],
            "specialist collection should fail closed when partial",
        )
        write_workflow(state_root, "verify-specialist-findings", specialist_verify)

        duplicate_accepted = json.loads(json.dumps(specialist_verify))
        duplicate_accepted["verify_state"]["agent_result_refs"].append(
            json.loads(json.dumps(duplicate_accepted["verify_state"]["agent_result_refs"][0]))
        )
        duplicate_accepted["verify_state"]["agent_result_collection_status"]["accepted_result_count"] += 1
        write_workflow(state_root, "verify-specialist-findings", duplicate_accepted)
        duplicate_artifact = next(
            artifact for artifact in duplicate_accepted["artifacts"] if artifact["artifact_id"] == "verify-specialist-findings"
        )
        Path(duplicate_artifact["path"]).write_text(
            json.dumps({"packet": packet, "specialist_review": duplicate_accepted["verify_state"]}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        duplicate_result = run_fail("validate", "--workflow-id", "verify-specialist-findings", state_root=state_root)
        assert_true(
            "artifact hash is stale" in duplicate_result["error"] or "idempotency_key must be unique" in duplicate_result["error"] or "terminal checkpoint" in duplicate_result["error"],
            "specialist accepted duplicate results should not double-count",
        )
        Path(duplicate_artifact["path"]).write_text(
            json.dumps({"packet": packet, "specialist_review": specialist_verify["verify_state"]}, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_workflow(state_root, "verify-specialist-findings", specialist_verify)

        drifted_specialist = json.loads(json.dumps(specialist_verify))
        drifted_specialist["verify_state"]["agent_finding_refs"][0].pop("evidence")
        write_workflow(state_root, "verify-specialist-findings", drifted_specialist)
        drifted_result = run_fail("validate", "--workflow-id", "verify-specialist-findings", state_root=state_root)
        assert_true(
            "specialist" in drifted_result["error"]
            or "report artifact" in drifted_result["error"]
            or "terminal checkpoint" in drifted_result["error"],
            "stored specialist findings should remain fully validated",
        )
        write_workflow(state_root, "verify-specialist-findings", specialist_verify)

        count_drift_events = json.loads(json.dumps(specialist_events))
        for event in count_drift_events:
            if event["event_type"] == "agent_findings_recorded":
                event["payload"]["finding_count"] = 99
        write_events(state_root, "verify-specialist-findings", count_drift_events)
        count_drift = run_fail("validate", "--workflow-id", "verify-specialist-findings", state_root=state_root)
        assert_true(
            "must match state" in count_drift["error"] or "artifact hash is stale" in count_drift["error"],
            "specialist event counts should bind to state",
        )
        write_events(state_root, "verify-specialist-findings", specialist_events)

        mixed_target = state_root / "verify-mixed-specialist-target.txt"
        mixed_target.write_text("phase 4 mixed deterministic plus specialist target\n", encoding="utf-8")
        mixed_verify = run(
            "verify",
            "--text",
            f"Verify execution-required mixed target {mixed_target}",
            "--workflow-id",
            "verify-mixed-specialist-findings",
            "--owner-session-key",
            "session:test",
            "--visible-delivery",
            visible_delivery,
            "--structured-findings-file",
            str(packet_path),
            state_root=state_root,
        )["workflow"]
        assert_true(mixed_verify["verify_state"]["execution_capability"] == "delegated_agents", "specialist proof should not be bypassed by deterministic evidence")
        mixed_events = events(state_root, "verify-mixed-specialist-findings")
        write_events(
            state_root,
            "verify-mixed-specialist-findings",
            [event for event in mixed_events if event["event_type"] != "finding_arbitrated"],
        )
        mixed_missing_event = run_fail("validate", "--workflow-id", "verify-mixed-specialist-findings", state_root=state_root)
        assert_true("finding_arbitrated event" in mixed_missing_event["error"], "mixed evidence should still require specialist proof")

        bad_packet = json.loads(json.dumps(packet))
        bad_packet["findings"][0].pop("evidence")
        bad_packet_path = state_root / "verify-bad-specialist-findings.json"
        bad_packet_path.write_text(json.dumps(bad_packet), encoding="utf-8")
        bad_specialist = run_fail(
            "verify",
            "--text",
            "Verify malformed specialist packet",
            "--workflow-id",
            "verify-bad-specialist-findings",
            "--visible-delivery",
            visible_delivery,
            "--structured-findings-file",
            str(bad_packet_path),
            state_root=state_root,
        )
        assert_true("evidence" in bad_specialist["error"], "structured findings should require evidence anchors")

        side_effect_packet = json.loads(json.dumps(packet))
        side_effect_packet["side_effects_performed"] = ["visible_message_sent"]
        side_effect_packet_path = state_root / "verify-side-effect-specialist-findings.json"
        side_effect_packet_path.write_text(json.dumps(side_effect_packet), encoding="utf-8")
        side_effect_specialist = run_fail(
            "verify",
            "--text",
            "Verify side-effect specialist packet",
            "--workflow-id",
            "verify-side-effect-specialist-findings",
            "--visible-delivery",
            visible_delivery,
            "--structured-findings-file",
            str(side_effect_packet_path),
            state_root=state_root,
        )
        assert_true("side effects" in side_effect_specialist["error"], "structured findings should reject specialist side effects")

        unknown_field_packet = json.loads(json.dumps(packet))
        unknown_field_packet["spawned_agents"] = ["agent-1"]
        unknown_field_path = state_root / "verify-unknown-field-specialist-findings.json"
        unknown_field_path.write_text(json.dumps(unknown_field_packet), encoding="utf-8")
        unknown_field = run_fail(
            "verify",
            "--text",
            "Verify unsupported specialist packet field",
            "--workflow-id",
            "verify-unknown-field-specialist-findings",
            "--visible-delivery",
            visible_delivery,
            "--structured-findings-file",
            str(unknown_field_path),
            state_root=state_root,
        )
        assert_true("unsupported fields" in unknown_field["error"], "structured findings should reject unsupported live-action fields")

        weak_evidence_packet = json.loads(json.dumps(packet))
        weak_evidence_packet["findings"][0]["evidence"] = "missing-artifact.json"
        weak_evidence_path = state_root / "verify-weak-evidence-specialist-findings.json"
        weak_evidence_path.write_text(json.dumps(weak_evidence_packet), encoding="utf-8")
        weak_evidence = run_fail(
            "verify",
            "--text",
            "Verify weak specialist evidence anchor",
            "--workflow-id",
            "verify-weak-evidence-specialist-findings",
            "--visible-delivery",
            visible_delivery,
            "--structured-findings-file",
            str(weak_evidence_path),
            state_root=state_root,
        )
        assert_true("evidence" in weak_evidence["error"], "structured findings should reject unbound evidence prose")

        forbidden_flag_packet = json.loads(json.dumps(packet))
        forbidden_flag_packet["visible_message_sent"] = True
        forbidden_flag_path = state_root / "verify-forbidden-specialist-findings.json"
        forbidden_flag_path.write_text(json.dumps(forbidden_flag_packet), encoding="utf-8")
        forbidden_flag = run_fail(
            "verify",
            "--text",
            "Verify forbidden specialist side-effect flag",
            "--workflow-id",
            "verify-forbidden-specialist-findings",
            "--visible-delivery",
            visible_delivery,
            "--structured-findings-file",
            str(forbidden_flag_path),
            state_root=state_root,
        )
        assert_true(
            "forbidden side effects" in forbidden_flag["error"] or "unsupported fields" in forbidden_flag["error"],
            "structured findings should reject explicit forbidden side-effect flags",
        )

        manual_packet = json.loads(json.dumps(packet))
        manual_packet["findings"][0]["source_provenance"] = "manual_note"
        manual_path = state_root / "verify-manual-specialist-findings.json"
        manual_path.write_text(json.dumps(manual_packet), encoding="utf-8")
        manual_result = run_fail(
            "verify",
            "--text",
            "Verify manual-provenance specialist packet",
            "--workflow-id",
            "verify-manual-specialist-findings",
            "--visible-delivery",
            visible_delivery,
            "--structured-findings-file",
            str(manual_path),
            state_root=state_root,
        )
        assert_true("source_provenance" in manual_result["error"], "manual provenance should not become specialist execution proof")

        mismatched_status = run(
            "verify",
            "--text",
            "plan-only Reject mismatched reserve final status",
            "--workflow-id",
            "verify-reserve-final-status-mismatch",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        bad_final_status = {
            "result": "needs_fix",
            "done": ["bad stale caller status"],
            "checked": ["bad stale caller status"],
            "residuals": {
                "blocking_remaining": [],
                "accepted_risks": [],
                "implementation_backlog": ["bad stale caller status"],
                "deferred_scope": [],
            },
        }
        status_mismatch = run_fail(
            "reserve-delivery",
            "--workflow-id",
            "verify-reserve-final-status-mismatch",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "bad final status reserve",
            "--final-status",
            json.dumps(bad_final_status),
            state_root=state_root,
        )
        assert_true(status_mismatch["send_authorized"] is False, "reserve-delivery should reject mismatched final_status")
        assert_true("final_status must match" in status_mismatch["error"], "reserve-delivery should bind caller final_status to stored final_status")
        run("validate", "--workflow-id", mismatched_status["workflow"]["workflow_id"], state_root=state_root)

        reservation = run(
            "reserve-delivery",
            "--workflow-id",
            "verify-c2-smoke",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "reserve final verify delivery",
            "--final-status",
            json.dumps(wf["final_status"]),
            state_root=state_root,
        )
        assert_true(reservation["send_authorized"] is True, "reserve-delivery should authorize terminal verify report")
        reported = run(
            "complete-reported",
            "--workflow-id",
            "verify-c2-smoke",
            "--reservation-id",
            reservation["reservation_id"],
            "--delivery-message-id",
            "telegram-message-verify",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(reported["status"] == "reported", "complete-reported should mark verify workflow reported")
        assert_true(workflow(state_root, "verify-c2-smoke")["status"] == "reported", "reported status should persist")

        active_verify = run(
            "start",
            "--kind",
            "verify",
            "--text",
            "Do not bypass verify finalization",
            "--workflow-id",
            "verify-reserve-bypass",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(active_verify["workflow"]["status"] == "running", "reserve bypass fixture should start running")
        bypass = run_fail(
            "reserve-delivery",
            "--workflow-id",
            "verify-reserve-bypass",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "bad reserve bypass",
            "--final-status",
            json.dumps(wf["final_status"]),
            "--terminal-evidence",
            json.dumps({"evidence_key": "bad-verify-reserve", "kind": "smoke", "summary": "bad reserve bypass", "artifact_refs": ["worklog.md#bad"]}),
            state_root=state_root,
        )
        assert_true("finalize through verify mode" in bypass["error"], "reserve-delivery should not terminalize active verify workflows")

        stale_reserve = run(
            "verify",
            "--text",
            "plan-only Do not send stale verify reports",
            "--workflow-id",
            "verify-stale-reserve",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        stale_report_path = Path(stale_reserve["workflow"]["verify_state"]["final_report_artifact_path"])
        stale_report_path.unlink()
        stale_reserve_result = run_fail(
            "reserve-delivery",
            "--workflow-id",
            "verify-stale-reserve",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "bad stale reserve",
            "--final-status",
            json.dumps(stale_reserve["workflow"]["final_status"]),
            state_root=state_root,
        )
        assert_true(stale_reserve_result["send_authorized"] is False, "reserve-delivery should not authorize stale verify report delivery")
        assert_true(stale_reserve_result["reason"] == "validation_error", "stale verify report delivery should require validation reconciliation")

        stale_proof = run(
            "verify",
            "--text",
            "plan-only Proof externally sent stale verify reports",
            "--workflow-id",
            "verify-stale-proof",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        stale_proof_reservation = run(
            "reserve-delivery",
            "--workflow-id",
            "verify-stale-proof",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "reserve stale proof fixture",
            "--final-status",
            json.dumps(stale_proof["workflow"]["final_status"]),
            state_root=state_root,
        )
        Path(stale_proof["workflow"]["verify_state"]["final_report_artifact_path"]).unlink()
        stale_proof_result = run(
            "report-proof",
            "--workflow-id",
            "verify-stale-proof",
            "--reservation-id",
            stale_proof_reservation["reservation_id"],
            "--delivery-message-id",
            "telegram-message-stale-proof",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(stale_proof_result["proof"]["delivery_message_id"] == "telegram-message-stale-proof", "report-proof should record external delivery proof after send")
        stale_proof_workflow = workflow(state_root, "verify-stale-proof")
        assert_true("report_proof" in stale_proof_workflow["visible_delivery_state"], "report-proof should persist proof state after external delivery")
        assert_true("report_proof" in [event["event_type"] for event in events(state_root, "verify-stale-proof")], "report-proof should append proof event after external delivery")

        proofed_then_stale = run(
            "verify",
            "--text",
            "plan-only Complete already proofed stale verify reports",
            "--workflow-id",
            "verify-proofed-then-stale",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        proofed_reservation = run(
            "reserve-delivery",
            "--workflow-id",
            "verify-proofed-then-stale",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "reserve proofed stale fixture",
            "--final-status",
            json.dumps(proofed_then_stale["workflow"]["final_status"]),
            state_root=state_root,
        )
        run(
            "report-proof",
            "--workflow-id",
            "verify-proofed-then-stale",
            "--reservation-id",
            proofed_reservation["reservation_id"],
            "--delivery-message-id",
            "telegram-message-proofed-then-stale",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        Path(proofed_then_stale["workflow"]["verify_state"]["final_report_artifact_path"]).unlink()
        proofed_complete = run(
            "complete-reported",
            "--workflow-id",
            "verify-proofed-then-stale",
            "--reservation-id",
            proofed_reservation["reservation_id"],
            "--delivery-message-id",
            "telegram-message-proofed-then-stale",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(proofed_complete["status"] == "reported", "complete-reported should not deadlock after proofed visible delivery")
        proofed_events = [event["event_type"] for event in events(state_root, "verify-proofed-then-stale")]
        assert_true(proofed_events.count("report_proof") == 1, "proofed stale completion should not duplicate proof")
        assert_true(proofed_events.count("report_sent") == 1, "proofed stale completion should append one reported event")

        duplicate = run(
            "verify",
            "--text",
            "plan-only Duplicate verify",
            "--workflow-id",
            "verify-c2-smoke",
            state_root=state_root,
        )
        assert_true(duplicate["result"] == "already_finalized", "duplicate terminal verify retry should reconcile as already finalized")

        run("validate", "--workflow-id", "verify-c2-smoke", state_root=state_root)

        corrupt = workflow(state_root, "verify-c2-smoke")
        corrupt["verify_state"]["final_report_artifact_id"] = "missing-report"
        write_workflow(state_root, "verify-c2-smoke", corrupt)
        invalid_report_ref = run_fail("validate", "--workflow-id", "verify-c2-smoke", state_root=state_root)
        assert_true(
            "final_report_artifact_id" in invalid_report_ref["error"]
            or "verify_state must match terminal checkpoint verify_state" in invalid_report_ref["error"],
            "validate should bind verify_state to registered report artifact and checkpoint",
        )
        corrupt["verify_state"] = {}
        write_workflow(state_root, "verify-c2-smoke", corrupt)
        empty_verify_state = run_fail("validate", "--workflow-id", "verify-c2-smoke", state_root=state_root)
        assert_true(
            "populated verify_state" in empty_verify_state["error"]
            or "verify_state must match terminal checkpoint verify_state" in empty_verify_state["error"],
            "terminal verify validate should reject empty verify_state",
        )

        missing_evidence = run(
            "verify",
            "--text",
            "plan-only Reject missing verify evidence",
            "--workflow-id",
            "verify-missing-evidence",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        missing_evidence_workflow = missing_evidence["workflow"]
        missing_evidence_workflow["verify_state"]["evidence"] = []
        write_workflow(state_root, "verify-missing-evidence", missing_evidence_workflow)
        missing_evidence_result = run_fail("validate", "--workflow-id", "verify-missing-evidence", state_root=state_root)
        assert_true(
            "requires evidence" in missing_evidence_result["error"]
            or "verify_state must match terminal checkpoint verify_state" in missing_evidence_result["error"],
            "terminal verify validate should require evidence",
        )

        unanchored_evidence = run(
            "verify",
            "--text",
            "plan-only Reject unanchored verify evidence",
            "--workflow-id",
            "verify-unanchored-evidence",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        unanchored_workflow = unanchored_evidence["workflow"]
        unanchored_workflow["verify_state"]["evidence"] = [
            evidence
            for evidence in unanchored_workflow["verify_state"]["evidence"]
            if "verify-final-report" not in evidence.get("artifact_refs", [])
        ]
        write_workflow(state_root, "verify-unanchored-evidence", unanchored_workflow)
        unanchored_result = run_fail("validate", "--workflow-id", "verify-unanchored-evidence", state_root=state_root)
        assert_true(
            "final_report_artifact_id" in unanchored_result["error"]
            or "verify_state must match terminal checkpoint verify_state" in unanchored_result["error"],
            "terminal verify validate should require final report evidence",
        )

        residual_mismatch = run(
            "verify",
            "--text",
            "plan-only Reject verify residual drift",
            "--workflow-id",
            "verify-residual-mismatch",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        residual_workflow = residual_mismatch["workflow"]
        residual_workflow["verify_state"]["residuals"]["implementation_backlog"].append("drifted residual")
        write_workflow(state_root, "verify-residual-mismatch", residual_workflow)
        residual_result = run_fail("validate", "--workflow-id", "verify-residual-mismatch", state_root=state_root)
        assert_true(
            "residuals must match" in residual_result["error"]
            or "verify_state must match terminal checkpoint verify_state" in residual_result["error"],
            "terminal verify validate should reject residual drift",
        )

        report_drift = run(
            "verify",
            "--text",
            "plan-only Reject verify report drift",
            "--workflow-id",
            "verify-report-drift",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        drift_workflow = report_drift["workflow"]
        drift_workflow["verify_state"]["verdict"] = "pass"
        drift_workflow["verify_state"]["residuals"] = {
            "blocking_remaining": [],
            "accepted_risks": [],
            "implementation_backlog": [],
            "deferred_scope": [],
        }
        drift_workflow["final_status"]["result"] = "pass"
        drift_workflow["final_status"]["residuals"] = drift_workflow["verify_state"]["residuals"]
        write_workflow(state_root, "verify-report-drift", drift_workflow)
        report_drift_result = run_fail("validate", "--workflow-id", "verify-report-drift", state_root=state_root)
        assert_true(
            "final_status must match terminal checkpoint final_status" in report_drift_result["error"]
            or "report artifact must match" in report_drift_result["error"],
            "terminal verify validate should bind mutable state to checkpoint/report artifact",
        )

        checkpoint_state_drift = run(
            "verify",
            "--text",
            "plan-only Reject checkpoint verify state drift",
            "--workflow-id",
            "verify-checkpoint-state-drift",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        checkpoint_drift_workflow = checkpoint_state_drift["workflow"]
        checkpoint_drift_workflow["verify_state"]["target"] = "mutated target"
        checkpoint_drift_workflow["verify_state"]["final_report_summary"] = "mutated summary"
        checkpoint_drift_report = render_verify_report(
            build_verify_record("mutated target")
        ).replace(
            "Verification record produced with no blocking remaining item in the current C2 scope.",
            "mutated summary",
        )
        checkpoint_drift_report_path = Path(checkpoint_drift_workflow["verify_state"]["final_report_artifact_path"])
        checkpoint_drift_report_path.write_text(checkpoint_drift_report, encoding="utf-8")
        for artifact_item in checkpoint_drift_workflow["artifacts"]:
            if artifact_item["artifact_id"] == "verify-final-report":
                artifact_item["sha256"] = sha256_file(checkpoint_drift_report_path)
        write_workflow(state_root, "verify-checkpoint-state-drift", checkpoint_drift_workflow)
        checkpoint_drift_result = run_fail("validate", "--workflow-id", "verify-checkpoint-state-drift", state_root=state_root)
        assert_true(
            "verify_state must match terminal checkpoint verify_state" in checkpoint_drift_result["error"],
            "terminal verify validate should reject mutable verify_state drift from checkpoint",
        )

        checkpoint_extra_drift = run(
            "verify",
            "--text",
            "plan-only Reject extra checkpoint verify state drift",
            "--workflow-id",
            "verify-checkpoint-extra-drift",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        checkpoint_extra_workflow = checkpoint_extra_drift["workflow"]
        checkpoint_extra_workflow["verify_state"]["unexpected_injected"] = "drift"
        write_workflow(state_root, "verify-checkpoint-extra-drift", checkpoint_extra_workflow)
        checkpoint_extra_result = run_fail("validate", "--workflow-id", "verify-checkpoint-extra-drift", state_root=state_root)
        assert_true(
            "verify_state must match terminal checkpoint verify_state" in checkpoint_extra_result["error"],
            "terminal verify validate should reject extra mutable verify_state keys",
        )

        verification_evidence_drift = run(
            "verify",
            "--text",
            "plan-only Reject workflow verification evidence drift",
            "--workflow-id",
            "verify-evidence-drift",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        verification_evidence_workflow = verification_evidence_drift["workflow"]
        verification_evidence_workflow["verification"]["evidence"] = [
            {
                "evidence_key": "drifted-terminal-evidence",
                "kind": "contract",
                "summary": "Valid-looking but uncheckpointed evidence drift.",
                "artifact_refs": [],
            }
        ]
        write_workflow(state_root, "verify-evidence-drift", verification_evidence_workflow)
        verification_evidence_result = run_fail("validate", "--workflow-id", "verify-evidence-drift", state_root=state_root)
        assert_true(
            "verification evidence must match checkpoint-backed terminal evidence sequence" in verification_evidence_result["error"],
            "terminal verify validate should reject workflow verification evidence drift",
        )
        verification_extra_workflow = verification_evidence_drift["workflow"]
        verification_extra_workflow["verification"]["evidence"] = [
            {
                "evidence_key": "injected-extra-evidence",
                "kind": "contract",
                "summary": "Valid-looking extra evidence not represented by the terminal checkpoint.",
                "artifact_refs": [],
            },
            *verification_extra_workflow["verification"]["evidence"],
        ]
        write_workflow(state_root, "verify-evidence-drift", verification_extra_workflow)
        verification_extra_result = run_fail("validate", "--workflow-id", "verify-evidence-drift", state_root=state_root)
        assert_true(
            "verification evidence must match checkpoint-backed terminal evidence sequence" in verification_extra_result["error"],
            "terminal verify validate should reject extra workflow verification evidence",
        )

        stale_hash = run(
            "verify",
            "--text",
            "plan-only Reject stale verify report hash",
            "--workflow-id",
            "verify-stale-report-hash",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        stale_hash_workflow = stale_hash["workflow"]
        stale_hash_path = Path(stale_hash_workflow["verify_state"]["final_report_artifact_path"])
        stale_hash_path.write_text(stale_hash_path.read_text(encoding="utf-8") + "\n- untracked drift\n", encoding="utf-8")
        stale_hash_result = run_fail("validate", "--workflow-id", "verify-stale-report-hash", state_root=state_root)
        assert_true("artifact hash is stale" in stale_hash_result["error"], "terminal verify validate should reject stale report artifact hash")

        resumed = run(
            "start",
            "--kind",
            "verify",
            "--text",
            "plan-only Resume a start-only verify",
            "--workflow-id",
            "verify-start-partial",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(resumed["workflow"]["status"] == "running", "start-only verify fixture should be running")
        resumed_final = run(
            "verify",
            "--text",
            "plan-only ignored retry text",
            "--workflow-id",
            "verify-start-partial",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(resumed_final["workflow"]["status"] == "completed_unreported", "verify command should resume start-only workflow")

        run(
            "start",
            "--kind",
            "verify",
            "--text",
            "plan-only Resume an artifact-only verify",
            "--workflow-id",
            "verify-artifact-partial",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        partial_artifact_path = state_root / "workflows" / "verify-artifact-partial" / "artifacts" / "verify-report.md"
        partial_artifact_path.parent.mkdir(parents=True, exist_ok=True)
        partial_artifact_path.write_text("# Verification Report\n\n## Target\n\n- stale\n", encoding="utf-8")
        run(
            "artifact",
            "--workflow-id",
            "verify-artifact-partial",
            "--artifact-id",
            "verify-final-report",
            "--kind",
            "report",
            "--path",
            str(partial_artifact_path),
            state_root=state_root,
        )
        artifact_resumed = run_fail(
            "verify",
            "--text",
            "plan-only ignored retry text",
            "--workflow-id",
            "verify-artifact-partial",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(
            "does not match rendered final report" in artifact_resumed["error"],
            "verify command should not bless a non-final registered report artifact",
        )

        with tempfile.TemporaryDirectory(prefix="converge-verify-relative-", dir=ROOT) as relative_tmp:
            relative_root = Path(relative_tmp).relative_to(ROOT)
            run(
                "start",
                "--kind",
                "verify",
                "--text",
                "plan-only Relative root report retry",
                "--workflow-id",
                "verify-relative-artifact",
                "--visible-delivery",
                visible_delivery,
                state_root=relative_root,
            )
            relative_report_arg = relative_root / "workflows" / "verify-relative-artifact" / "artifacts" / "verify-report.md"
            relative_report_path = ROOT / relative_report_arg
            relative_report_path.parent.mkdir(parents=True, exist_ok=True)
            relative_report_path.write_text(
                render_verify_report(build_verify_record("plan-only Relative root report retry")),
                encoding="utf-8",
            )
            run(
                "artifact",
                "--workflow-id",
                "verify-relative-artifact",
                "--artifact-id",
                "verify-final-report",
                "--kind",
                "report",
                "--path",
                str(relative_report_arg),
                state_root=relative_root,
            )
            relative_resumed = run(
                "verify",
                "--text",
                "plan-only ignored retry text",
                "--workflow-id",
                "verify-relative-artifact",
                "--visible-delivery",
                visible_delivery,
                state_root=relative_root,
            )
            assert_true(relative_resumed["workflow"]["status"] == "completed_unreported", "relative state-root report retry should finalize")
    return 0


def specialist_packet() -> dict:
    return {
        "panel_id": "panel-phase4-verify",
        "risk_level": "medium",
        "side_effects_performed": [],
        "profiles": [
            {
                "profile_id": "reviewer-contracts",
                "role": "contract reviewer",
                "expertise": ["schema", "state"],
                "likely_failure_modes": ["evidence binding"],
                "prohibited_actions": ["visible_messages", "workflow_state_mutation"],
            },
            {
                "profile_id": "reviewer-runtime",
                "role": "runtime reviewer",
                "expertise": ["runner", "events"],
                "likely_failure_modes": ["evidence binding"],
                "prohibited_actions": ["external_actions", "service_restart"],
            },
            {
                "profile_id": "reviewer-risk",
                "role": "risk reviewer",
                "expertise": ["approval boundaries", "report proof"],
                "likely_failure_modes": ["report proof drift"],
                "prohibited_actions": ["push_or_pr", "target_mutation"],
            },
        ],
        "findings": [
            {
                "finding_id": "finding-evidence-anchor-a",
                "profile_id": "reviewer-contracts",
                "finding": "Evidence binding must be validated before a pass verdict.",
                "severity": "p3",
                "evidence": "verify_state.execution_evidence_refs",
                "why_it_matters": "A passing verdict without evidence refs would recreate false completion.",
                "minimal_fix_or_test": "Assert specialist artifact and events are required by validate.",
                "scope_risk": "state-contract",
                "confidence": 0.82,
                "failure_mode": "evidence binding",
                "source_provenance": "runner_provided",
            },
            {
                "finding_id": "finding-evidence-anchor-b",
                "profile_id": "reviewer-runtime",
                "finding": "Specialist event proof must survive validation.",
                "severity": "p3",
                "evidence": "events.jsonl finding_arbitrated",
                "why_it_matters": "Event loss would make findings unverifiable after recovery.",
                "minimal_fix_or_test": "Remove finding_arbitrated and expect validate failure.",
                "scope_risk": "recovery",
                "confidence": 0.76,
                "failure_mode": "evidence binding",
                "source_provenance": "runner_provided",
            },
        ],
    }


if __name__ == "__main__":
    raise SystemExit(main())
