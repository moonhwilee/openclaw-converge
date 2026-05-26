#!/usr/bin/env python3
"""Smoke coverage for common CLI/runtime foundation commands."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


try:
    from smoke_helpers import (
        TEST_VISIBLE_DELIVERY,
        assert_keys,
        assert_true,
        current_cursor,
        events,
        run,
        run_bin,
        run_fail,
        workflow,
        write_events,
    )
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import (
        TEST_VISIBLE_DELIVERY,
        assert_keys,
        assert_true,
        current_cursor,
        events,
        run,
        run_bin,
        run_fail,
        workflow,
        write_events,
    )


def assert_initial_contract(workflow_payload: dict, message: str) -> None:
    plan = workflow_payload["continuation_plan"]
    assert_true(plan["plan_id"].endswith("initialization-contract"), f"{message}: plan id mismatch")
    assert_true([step["step_id"] for step in plan["steps"]] == ["baseline"], f"{message}: step shape mismatch")
    assert_true(plan["rolling_state"]["current_resume_cursor"] == "baseline", f"{message}: cursor mismatch")
    assert_true(workflow_payload["next_safe_action"]["cursor"] == "baseline", f"{message}: next action cursor mismatch")


def assert_goal_c4_initial_contract(workflow_payload: dict, message: str) -> None:
    plan = workflow_payload["continuation_plan"]
    assert_true(plan["plan_id"] == "goal-initialization-contract", f"{message}: plan id mismatch")
    assert_true(
        [step["step_id"] for step in plan["steps"]]
        == ["objective-gate", "plan-acceptance-gate", "evidence-completion-gate"],
        f"{message}: step shape mismatch",
    )
    assert_true(plan["rolling_state"]["current_resume_cursor"] == "objective-gate", f"{message}: cursor mismatch")
    assert_true(workflow_payload["next_safe_action"]["cursor"] == "objective-gate", f"{message}: next action cursor mismatch")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="converge-runtime-foundation-smoke-") as tmp:
        state_root = Path(tmp)
        visible_delivery = TEST_VISIBLE_DELIVERY

        wrapper_args = (
            "--owner-session-key",
            "session:test",
            "--visible-delivery",
            visible_delivery,
        )
        malformed_delivery = run_fail("goal", "--text", "bad input", "--workflow-id", "bad-input", "--visible-delivery", "{", state_root=state_root)
        assert_true(not malformed_delivery["ok"] and "argument --visible-delivery" in malformed_delivery["error"], "argparse JSON errors should stay machine-readable")
        invalid_choice = run_fail("artifact", "--workflow-id", "bad-input", "--kind", "unknown", "--path", str(state_root), state_root=state_root)
        assert_true(not invalid_choice["ok"] and "invalid choice" in invalid_choice["error"], "argparse choice errors should stay machine-readable")

        goal = run("start", "--kind", "goal", "--text", "Implement runtime foundation", "--workflow-id", "goal-runtime", *wrapper_args, state_root=state_root)
        assert_true(goal["workflow"]["kind"] == "goal", "start --kind goal should create goal workflow")
        assert_goal_c4_initial_contract(goal["workflow"], "start --kind goal should use C4 initialization contract")
        assert_true(goal["workflow"]["owner_session_key"] == "session:test", "start --kind goal should preserve owner session")
        assert_true(goal["workflow"]["visible_delivery"]["channel"] == "telegram", "start --kind goal should preserve visible delivery")
        status_json = run("status", "--workflow-id", "goal-runtime", "--json", state_root=state_root)
        assert_true(status_json["workflow"]["workflow_id"] == "goal-runtime", "subcommand-local --json should be accepted")

        plan = run("plan", "--text", "Plan runtime foundation", "--workflow-id", "plan-runtime", *wrapper_args, state_root=state_root)
        assert_true(plan["workflow"]["kind"] == "plan", "plan helper should create plan workflow")
        assert_true(plan["workflow"]["status"] == "completed_unreported", "plan helper should finalize through shared checkpoint")
        assert_true(plan["workflow"]["continuation_plan"] is None, "plan helper should use shared start contract")
        verify = run_bin("verify", "--text", "Verify runtime foundation", "--workflow-id", "verify-runtime", *wrapper_args, state_root=state_root)
        assert_true(verify["workflow"]["kind"] == "verify", "verify helper should create verify workflow through bin")
        assert_true(verify["workflow"]["continuation_plan"] is None, "verify helper should use shared start contract")
        conv = run("start", "--kind", "conv", "--text", "Converge runtime foundation", "--workflow-id", "conv-runtime", *wrapper_args, state_root=state_root)
        assert_true(conv["workflow"]["kind"] == "conv", "conv helper should create conv workflow")
        assert_initial_contract(conv["workflow"], "start conv should use initialization contract")

        plan_start_only = run("start", "--kind", "plan", "--text", "Start-only plan runtime foundation", "--workflow-id", "plan-start-runtime", *wrapper_args, state_root=state_root)
        assert_true(plan_start_only["workflow"]["kind"] == "plan", "start should still expose low-level plan workflow creation")
        plan_ready = run("advance", "--workflow-id", "plan-start-runtime", "--summary", "plan ready", state_root=state_root)
        assert_true(plan_ready["result"] == "terminal_ready", "no-continuation advance should return terminal_ready")
        assert_true(not workflow(state_root, "plan-start-runtime")["checkpoint_index"], "no-continuation advance should not checkpoint")

        evidence_path = state_root / "evidence.txt"
        evidence_path.write_text("runtime foundation evidence\n", encoding="utf-8")
        artifact = run(
            "artifact",
            "--workflow-id",
            "goal-runtime",
            "--kind",
            "evidence",
            "--path",
            str(evidence_path),
            state_root=state_root,
        )["artifact"]
        assert_true(artifact["kind"] == "evidence" and artifact.get("sha256"), "artifact should be hashed and recorded")
        assert_true(workflow(state_root, "goal-runtime")["artifacts"][0]["artifact_id"] == artifact["artifact_id"], "artifact should persist")
        artifact_events = [event for event in events(state_root, "goal-runtime") if event["event_type"] == "artifact"]
        assert_true(artifact_events and artifact_events[-1]["payload"]["artifact"]["artifact_id"] == artifact["artifact_id"], "artifact event should match workflow artifact")
        duplicate_artifact = run_fail(
            "artifact",
            "--workflow-id",
            "goal-runtime",
            "--artifact-id",
            artifact["artifact_id"],
            "--kind",
            "evidence",
            "--path",
            str(evidence_path),
            state_root=state_root,
        )
        assert_true("artifact already exists" in duplicate_artifact["error"], "duplicate artifact id should fail")
        missing_artifact = run_fail(
            "artifact",
            "--workflow-id",
            "goal-runtime",
            "--kind",
            "context",
            "--path",
            str(state_root / "missing-evidence.txt"),
            state_root=state_root,
        )
        assert_true("existing file" in missing_artifact["error"], "artifact should require materialized files")
        artifact_ref_goal = run("start", "--kind", "goal", "--text", "Artifact ref guard", "--workflow-id", "artifact-ref-runtime", state_root=state_root)
        assert_true(artifact_ref_goal["workflow"]["kind"] == "goal", "artifact ref fixture should create goal workflow")
        materialized_ref = run(
            "artifact",
            "--workflow-id",
            "artifact-ref-runtime",
            "--kind",
            "evidence",
            "--path",
            str(evidence_path),
            state_root=state_root,
        )["artifact"]
        dangling_ref = run_fail(
            "artifact",
            "--workflow-id",
            "artifact-ref-runtime",
            "--kind",
            "evidence",
            "--path",
            str(state_root / "dangling-evidence.txt"),
            state_root=state_root,
        )
        assert_true("existing file" in dangling_ref["error"], "dangling artifact registration should fail")
        materialized_checkpoint = run(
            "checkpoint",
            "--workflow-id",
            "artifact-ref-runtime",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "materialized artifact evidence",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"artifact-ref","cursor_before":"objective-gate","cursor_after":"objective-gate","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            "--evidence",
            json.dumps(
                {
                    "evidence_key": "materialized-artifact",
                    "kind": "smoke",
                    "summary": "materialized artifact accepted",
                    "artifact_refs": [materialized_ref["artifact_id"]],
                }
            ),
            state_root=state_root,
        )
        assert_true("checkpoint_id" in materialized_checkpoint["checkpoint"], "materialized artifact ref should pass")
        materialized_relative_checkpoint = run(
            "checkpoint",
            "--workflow-id",
            "artifact-ref-runtime",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "materialized artifact-relative evidence",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"artifact-ref","cursor_before":"objective-gate","cursor_after":"objective-gate","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            "--evidence",
            json.dumps(
                {
                    "evidence_key": "materialized-artifact-relative",
                    "kind": "smoke",
                    "summary": "materialized artifact-relative accepted",
                    "artifact_refs": [f"{materialized_ref['artifact_id']}/section"],
                }
            ),
            state_root=state_root,
        )
        assert_true("checkpoint_id" in materialized_relative_checkpoint["checkpoint"], "materialized artifact-relative ref should pass")
        evidence_path.write_text("runtime foundation evidence changed\n", encoding="utf-8")
        stale_checkpoint = run_fail(
            "checkpoint",
            "--workflow-id",
            "artifact-ref-runtime",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "stale artifact evidence",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"artifact-ref","cursor_before":"objective-gate","cursor_after":"objective-gate","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            "--evidence",
            json.dumps(
                {
                    "evidence_key": "stale-artifact",
                    "kind": "smoke",
                    "summary": "stale artifact rejected",
                    "artifact_refs": [materialized_ref["artifact_id"]],
                }
            ),
            state_root=state_root,
        )
        assert_true("stale" in stale_checkpoint["error"], "stale artifact refs should fail before checkpoint")
        evidence_path.write_text("runtime foundation evidence\n", encoding="utf-8")
        missing_checkpoint = run_fail(
            "checkpoint",
            "--workflow-id",
            "artifact-ref-runtime",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "missing artifact evidence",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"artifact-ref","cursor_before":"objective-gate","cursor_after":"objective-gate","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            "--evidence",
            json.dumps(
                {
                    "evidence_key": "dangling-artifact",
                    "kind": "smoke",
                    "summary": "dangling artifact rejected",
                    "artifact_refs": ["missing-artifact"],
                }
            ),
            state_root=state_root,
        )
        assert_true("not registered" in missing_checkpoint["error"], "unregistered artifact refs should fail")
        missing_relative_checkpoint = run_fail(
            "checkpoint",
            "--workflow-id",
            "artifact-ref-runtime",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "missing artifact-relative evidence",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"artifact-ref","cursor_before":"objective-gate","cursor_after":"objective-gate","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            "--evidence",
            json.dumps(
                {
                    "evidence_key": "dangling-artifact-relative",
                    "kind": "smoke",
                    "summary": "dangling artifact-relative rejected",
                    "artifact_refs": ["missing-artifact/section"],
                }
            ),
            state_root=state_root,
        )
        assert_true("not registered" in missing_relative_checkpoint["error"], "unregistered artifact-relative refs should fail")
        pending_goal = run("start", "--kind", "goal", "--text", "Pending artifact guard", "--workflow-id", "artifact-pending-runtime", state_root=state_root)
        assert_true(pending_goal["workflow"]["kind"] == "goal", "pending artifact fixture should create goal workflow")
        (state_root / "workflows" / "artifact-pending-runtime" / ".pending-chk-test.json").write_text("{}", encoding="utf-8")
        pending_artifact = run_fail(
            "artifact",
            "--workflow-id",
            "artifact-pending-runtime",
            "--kind",
            "evidence",
            "--path",
            str(evidence_path),
            state_root=state_root,
        )
        assert_true("pending checkpoint" in pending_artifact["error"], "artifact should respect pending checkpoint guard")

        terminal_ready_from_initial = run(
            "advance",
            "--workflow-id",
            "goal-runtime",
            "--summary",
            "baseline inspected",
            "--evidence",
            json.dumps({"evidence_key": "runtime-smoke", "kind": "smoke", "summary": "advance passed", "artifact_refs": [artifact["artifact_id"]]}),
            state_root=state_root,
        )
        assert_true(terminal_ready_from_initial["result"] == "advance_ready", "C4 goal initialization should advance through the objective gate")
        after_initial_advance = workflow(state_root, "goal-runtime")
        assert_true(current_cursor(after_initial_advance) == "plan-acceptance-gate", "C4 goal advance should continue to plan acceptance")

        plan_acceptance_advance = run(
            "advance",
            "--workflow-id",
            "goal-runtime",
            "--summary",
            "plan acceptance gate inspected",
            "--evidence",
            json.dumps(
                {
                    "evidence_key": "runtime-smoke-plan-acceptance",
                    "kind": "smoke",
                    "summary": "plan acceptance advance passed",
                    "artifact_refs": [artifact["artifact_id"]],
                }
            ),
            state_root=state_root,
        )
        assert_true(plan_acceptance_advance["result"] == "advance_ready", "C4 goal should advance through plan acceptance before terminal-ready")
        after_plan_acceptance = workflow(state_root, "goal-runtime")
        assert_true(current_cursor(after_plan_acceptance) == "evidence-completion-gate", "C4 goal advance should continue to evidence completion")

        terminal_ready_without_evidence = run_fail(
            "advance",
            "--workflow-id",
            "goal-runtime",
            "--summary",
            "baseline inspected without evidence",
            state_root=state_root,
        )
        assert_true(
            "terminal_ready requires checkpoint evidence" in terminal_ready_without_evidence["error"],
            "terminal_ready should require gate evidence before terminal target",
        )

        direct_terminal_reserve = run(
            "reserve-delivery",
            "--workflow-id",
            "goal-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "terminal complete",
            "--terminal-evidence",
            '{"evidence_key":"terminal-runtime-smoke","kind":"smoke","summary":"terminal passed","artifact_refs":["worklog.md#runtime-smoke"]}',
            "--final-status",
            '{"result":"pass","residuals":{}}',
            "--json",
            state_root=state_root,
        )
        assert_true(
            direct_terminal_reserve["send_authorized"] is False and direct_terminal_reserve["reason"] == "invalid_state",
            "active continuation workflows should not reserve delivery before terminal checkpoint",
        )
        direct_conv_terminal_reserve = run(
            "reserve-delivery",
            "--workflow-id",
            "conv-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "conv terminal complete",
            "--final-status",
            '{"result":"pass","residuals":{}}',
            "--json",
            state_root=state_root,
        )
        assert_true(
            direct_conv_terminal_reserve["send_authorized"] is False and direct_conv_terminal_reserve["reason"] == "invalid_state",
            "active conv workflows should not reserve delivery before terminal checkpoint",
        )
        route_mismatch_reserve = run(
            "reserve-delivery",
            "--workflow-id",
            "goal-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            '{"channel":"telegram","target":"other"}',
            "--summary",
            "wrong route terminal",
            "--terminal-evidence",
            '{"evidence_key":"wrong-route-terminal-runtime-smoke","kind":"smoke","summary":"wrong route terminal","artifact_refs":["worklog.md#runtime-smoke"]}',
            "--final-status",
            '{"result":"pass","residuals":{}}',
            "--json",
            state_root=state_root,
        )
        assert_true(
            route_mismatch_reserve["send_authorized"] is False and route_mismatch_reserve["reason"] == "visible_delivery_mismatch",
            "reserve-delivery should preserve the workflow visible delivery route",
        )
        run("goal", "--text", "Implement runtime foundation", "--workflow-id", "goal-runtime", *wrapper_args, state_root=state_root)
        terminal = run(
            "reserve-delivery",
            "--workflow-id",
            "goal-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "terminal complete",
            "--final-status",
            json.dumps(workflow(state_root, "goal-runtime")["final_status"]),
            "--json",
            state_root=state_root,
        )
        reservation_id = terminal["reservation_id"]
        authorized_with_checkpoint_keys = {
            "ok",
            "workflow_id",
            "send_authorized",
            "reconcile_required",
            "reservation_id",
            "terminal_status",
            "visible_delivery",
            "checkpoint_id",
            "lease_expires_at",
            "reason",
            "checkpoint",
            "event_id",
            "send_authority",
            "source_of_truth",
        }
        authorized_without_checkpoint_keys = authorized_with_checkpoint_keys - {"checkpoint"}
        reconcile_keys = {
            "ok",
            "workflow_id",
            "send_authorized",
            "reconcile_required",
            "reservation_id",
            "terminal_status",
            "visible_delivery",
            "checkpoint_id",
            "lease_expires_at",
            "reason",
            "send_authority",
            "source_of_truth",
        }
        assert_keys(terminal, authorized_without_checkpoint_keys, "terminal reserve payload keys should stay stable")
        assert_true(terminal["send_authority"] == "converge.reserve-delivery", "reserve payload should expose Converge send authority")
        assert_true(terminal["source_of_truth"] == "converge.workflow", "reserve payload should expose Converge workflow source")
        assert_true(isinstance(terminal["checkpoint_id"], str) and terminal["checkpoint_id"], "terminal reserve should bind a terminal checkpoint")
        terminal_checkpoint_id = terminal["checkpoint_id"]
        after_reserve = workflow(state_root, "goal-runtime")
        assert_true(after_reserve["status"] == "completed_unreported", "reserve should preserve terminal unreported state")
        assert_true(after_reserve["active_delivery_reservation"]["reservation_id"] == reservation_id, "reservation should persist")
        terminal_artifact = run_fail(
            "artifact",
            "--workflow-id",
            "goal-runtime",
            "--kind",
            "evidence",
            "--path",
            str(evidence_path),
            state_root=state_root,
        )
        assert_true("terminal status" in terminal_artifact["error"], "terminal-unreported workflows should reject artifact mutation")
        assert_true(
            {
                key: after_reserve["active_delivery_reservation"][key]
                for key in ("reservation_id", "terminal_status", "visible_delivery", "checkpoint_id", "lease_expires_at")
            }
            == {
                key: terminal[key]
                for key in ("reservation_id", "terminal_status", "visible_delivery", "checkpoint_id", "lease_expires_at")
            },
            "persisted delivery reservation should match authorized response",
        )
        terminal_delivery_event = [event for event in events(state_root, "goal-runtime") if event["event_type"] == "delivery_reserved"][-1]
        assert_true(
            terminal_delivery_event["checkpoint_id"] == terminal["checkpoint_id"]
            and terminal_delivery_event["status_after"] == terminal["terminal_status"]
            and terminal_delivery_event["payload"]["reservation_id"] == terminal["reservation_id"]
            and terminal_delivery_event["payload"]["visible_delivery"] == terminal["visible_delivery"],
            "delivery_reserved event should match authorized response",
        )
        completed_status_mismatch = run(
            "reserve-delivery",
            "--workflow-id",
            "goal-runtime",
            "--terminal-status",
            "failed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "wrong completed status terminal",
            "--final-status",
            '{"result":"pass","residuals":{}}',
            state_root=state_root,
        )
        assert_true(
            completed_status_mismatch["send_authorized"] is False
            and completed_status_mismatch["reason"] == "terminal_status_mismatch"
            and completed_status_mismatch["terminal_status"] == "completed_unreported",
            "reserve-delivery should reject mismatched terminal-status for completed workflows",
        )
        duplicate_reserve = run(
            "reserve-delivery",
            "--workflow-id",
            "goal-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "duplicate terminal complete",
            "--terminal-evidence",
            '{"evidence_key":"duplicate-terminal-runtime-smoke","kind":"smoke","summary":"duplicate terminal","artifact_refs":["worklog.md#runtime-smoke"]}',
            "--final-status",
            '{"result":"pass","residuals":{}}',
            state_root=state_root,
        )
        assert_true(
            duplicate_reserve["send_authorized"] is False and duplicate_reserve["reason"] == "active_reservation_exists",
            "duplicate reserve should not authorize a second send",
        )
        assert_keys(duplicate_reserve, reconcile_keys, "active reservation reconcile payload keys should stay stable")
        assert_true(
            duplicate_reserve["send_authority"] == "converge.reserve-delivery"
            and duplicate_reserve["source_of_truth"] == "converge.workflow",
            "duplicate reserve no-send payload should keep Converge authority metadata",
        )
        empty_route_goal = run("start", "--kind", "goal", "--text", "Empty route guard", "--workflow-id", "empty-route-runtime", state_root=state_root)
        assert_true(empty_route_goal["workflow"]["kind"] == "goal", "empty route fixture should create goal workflow")
        empty_route_reserve = run_fail(
            "reserve-delivery",
            "--workflow-id",
            "empty-route-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            "{}",
            "--summary",
            "empty route terminal",
            "--terminal-evidence",
            '{"evidence_key":"empty-route-runtime-smoke","kind":"smoke","summary":"empty route","artifact_refs":["worklog.md#runtime-smoke"]}',
            "--final-status",
            '{"result":"pass","residuals":{}}',
            state_root=state_root,
        )
        assert_true("visible_delivery" in empty_route_reserve["error"], "reserve-delivery should reject empty visible delivery before mutation")
        assert_true(workflow(state_root, "empty-route-runtime")["status"] == "running", "failed empty route reserve should not terminalize workflow")

        missing_target_route = run_fail(
            "goal",
            "--text",
            "Missing target route guard",
            "--workflow-id",
            "missing-target-runtime",
            "--visible-delivery",
            '{"channel":"telegram"}',
            state_root=state_root,
        )
        assert_true(
            "visible_delivery.target" in missing_target_route["error"],
            "visible delivery should require an explicit target identity",
        )

        gap_goal = run("plan", "--text", "Checkpoint reservation gap", "--workflow-id", "gap-runtime", state_root=state_root)
        assert_true(gap_goal["workflow"]["kind"] == "plan", "gap fixture should create plan workflow")
        gap_checkpoint = gap_goal["checkpoint"]
        gap_terminal = run(
            "reserve-delivery",
            "--workflow-id",
            "gap-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "gap terminal complete",
            "--terminal-evidence",
            '{"evidence_key":"gap-terminal-runtime-smoke","kind":"smoke","summary":"gap terminal","artifact_refs":["worklog.md#runtime-smoke"]}',
            "--final-status",
            json.dumps(gap_goal["workflow"]["final_status"]),
            state_root=state_root,
        )
        gap_workflow = workflow(state_root, "gap-runtime")
        gap_workflow["active_delivery_reservation"] = None
        (state_root / "workflows" / "gap-runtime" / "workflow.json").write_text(
            json.dumps(gap_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        gap_events = [
            event
            for event in events(state_root, "gap-runtime")
            if not (event["event_type"] == "delivery_reserved" and event.get("checkpoint_id") == gap_checkpoint["checkpoint_id"])
        ]
        write_events(state_root, "gap-runtime", gap_events)
        gap_recovered = run(
            "reserve-delivery",
            "--workflow-id",
            "gap-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "gap recovered delivery",
            "--terminal-evidence",
            '{"evidence_key":"gap-recovered-runtime-smoke","kind":"smoke","summary":"gap recovered","artifact_refs":["worklog.md#runtime-smoke"]}',
            "--final-status",
            json.dumps(gap_goal["workflow"]["final_status"]),
            state_root=state_root,
        )
        assert_true(gap_recovered["send_authorized"] is True, "checkpoint-only terminal gap should create first delivery reservation")
        assert_keys(gap_recovered, authorized_without_checkpoint_keys, "terminal recovery reserve payload keys should stay stable")
        assert_true(
            gap_recovered["send_authority"] == "converge.reserve-delivery"
            and gap_recovered["source_of_truth"] == "converge.workflow",
            "terminal gap recovery reserve should keep Converge authority metadata",
        )
        assert_true(
            gap_recovered["checkpoint_id"] == gap_checkpoint["checkpoint_id"],
            "gap recovery should reuse the existing terminal checkpoint",
        )
        assert_true(
            len([event for event in events(state_root, "gap-runtime") if event["event_type"] == "delivery_reserved"]) == 1,
            "gap recovery should create exactly one delivery reservation event",
        )
        terminal_without_checkpoint = run("start", "--kind", "goal", "--text", "Terminal without checkpoint", "--workflow-id", "terminalless-runtime", state_root=state_root)
        terminalless_workflow = terminal_without_checkpoint["workflow"]
        terminalless_workflow["status"] = "completed_unreported"
        terminalless_workflow["phase"] = "terminal"
        terminalless_workflow["final_status"] = {"result": "pass", "residuals": {}}
        (state_root / "workflows" / "terminalless-runtime" / "workflow.json").write_text(
            json.dumps(terminalless_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        terminalless_validate = run_fail("validate", "--workflow-id", "terminalless-runtime", state_root=state_root)
        assert_true(
            "terminal checkpoint" in terminalless_validate["error"],
            "terminal unreported workflows should require a terminal checkpoint",
        )
        gap_recovered_workflow = workflow(state_root, "gap-runtime")
        gap_recovered_workflow["active_delivery_reservation"] = None
        (state_root / "workflows" / "gap-runtime" / "workflow.json").write_text(
            json.dumps(gap_recovered_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        gap_existing_reservation = run(
            "reserve-delivery",
            "--workflow-id",
            "gap-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "gap existing reservation",
            "--terminal-evidence",
            '{"evidence_key":"gap-existing-runtime-smoke","kind":"smoke","summary":"gap existing","artifact_refs":["worklog.md#runtime-smoke"]}',
            "--final-status",
            '{"result":"pass","residuals":{}}',
            state_root=state_root,
        )
        assert_true(
            gap_existing_reservation["send_authorized"] is False
            and gap_existing_reservation["reason"] == "expired_reservation_requires_reconcile",
            "terminal gap with historical reservation should return stable no-send reconcile",
        )
        assert_keys(gap_existing_reservation, reconcile_keys, "historical reservation reconcile payload keys should stay stable")

        delivery_workflow = workflow(state_root, "goal-runtime")
        expired_delivery_workflow = json.loads(json.dumps(delivery_workflow))
        expired_delivery_workflow["active_delivery_reservation"]["lease_expires_at"] = "2000-01-01T00:00:00Z"
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(expired_delivery_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        expired_delivery = run(
            "reserve-delivery",
            "--workflow-id",
            "goal-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "expired active delivery",
            "--terminal-evidence",
            '{"evidence_key":"expired-active-runtime-smoke","kind":"smoke","summary":"expired active","artifact_refs":["worklog.md#runtime-smoke"]}',
            "--final-status",
            '{"result":"pass","residuals":{}}',
            state_root=state_root,
        )
        assert_true(
            expired_delivery["send_authorized"] is False and expired_delivery["reason"] == "expired_reservation_requires_reconcile",
            "expired active delivery should return expired reconcile reason",
        )
        assert_keys(expired_delivery, reconcile_keys, "expired active reservation payload keys should stay stable")
        expired_active_proof = run_fail(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20197",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(
            "expired" in expired_active_proof["error"],
            "expired active reservation should require manual reconcile before report proof",
        )
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(delivery_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        corrupted_delivery_workflow = json.loads(json.dumps(delivery_workflow))
        corrupted_delivery_workflow["active_delivery_reservation"]["checkpoint_id"] = "chk-does-not-exist"
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(corrupted_delivery_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        bad_active_reservation = run_fail("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        assert_true(
            "active_delivery_reservation checkpoint_id" in bad_active_reservation["error"],
            "active delivery reservation checkpoint ref should validate",
        )
        corrupted_index_workflow = json.loads(json.dumps(delivery_workflow))
        checkpoint_id = terminal_checkpoint_id
        corrupted_index_workflow["checkpoint_index"][checkpoint_id]["checkpoint_id"] = "chk-internal-mismatch"
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(corrupted_index_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        bad_checkpoint_index = run_fail("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        assert_true(
            "does not match checkpoint_id" in bad_checkpoint_index["error"],
            "checkpoint_index key and checkpoint_id should match",
        )
        corrupted_route_workflow = json.loads(json.dumps(delivery_workflow))
        corrupted_route_workflow["active_delivery_reservation"]["visible_delivery"] = {"channel": "telegram", "target": "wrong"}
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(corrupted_route_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        bad_active_route = run_fail("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        assert_true(
            "active_delivery_reservation" in bad_active_route["error"],
            "active delivery reservation should match delivery_reserved event",
        )
        malformed_active_workflow = json.loads(json.dumps(delivery_workflow))
        malformed_active_workflow["active_delivery_reservation"].pop("reservation_id", None)
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(malformed_active_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        malformed_active_reserve = run_fail(
            "reserve-delivery",
            "--workflow-id",
            "goal-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "malformed active reservation",
            "--final-status",
            '{"result":"pass","residuals":{}}',
            state_root=state_root,
        )
        assert_true(
            malformed_active_reserve["ok"] is False and "reservation_id" in malformed_active_reserve["error"],
            "unexpected malformed active reservation should not be reported as ok no-send",
        )
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(corrupted_route_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        bad_active_route_proof = run_fail(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20198",
            "--visible-delivery",
            '{"channel":"telegram","target":"wrong"}',
            state_root=state_root,
        )
        assert_true(
            "matching delivery_reserved" in bad_active_route_proof["error"],
            "report proof should not trust corrupted active delivery reservation",
        )
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(delivery_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        mismatch = run_fail(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20199",
            "--visible-delivery",
            '{"channel":"telegram","target":"wrong"}',
            state_root=state_root,
        )
        assert_true("visible_delivery" in mismatch["error"], "report proof should bind visible_delivery to reservation")

        proof = run(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )["proof"]
        assert_true(proof["delivery_message_id"] == "20200", "report proof should persist delivery message id")
        assert_true(proof["proof_authority"] == "converge.report-proof", "report proof should expose Converge proof authority")
        assert_true(proof["source_of_truth"] == "converge.workflow", "report proof should expose Converge workflow source")
        assert_true(workflow(state_root, "goal-runtime")["status"] == "completed_unreported", "report proof should not mark reported")
        duplicate_proof = run(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )["proof"]
        assert_true(
            {key: duplicate_proof.get(key) for key in ("reservation_id", "delivery_message_id", "visible_delivery", "recorded_at")}
            == {key: proof.get(key) for key in ("reservation_id", "delivery_message_id", "visible_delivery", "recorded_at")},
            "duplicate report proof should be idempotent",
        )
        conflicting_proof = run_fail(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20201",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true("different report proof" in conflicting_proof["error"], "conflicting report proof should fail")
        proof_crash_workflow = workflow(state_root, "goal-runtime")
        proof_crash_workflow["visible_delivery_state"].pop("report_proof", None)
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(proof_crash_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        hydrated_proof = run(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(hydrated_proof["proof"]["delivery_message_id"] == "20200", "report-proof retry should hydrate missing JSON proof")
        assert_true(
            sum(1 for event in events(state_root, "goal-runtime") if event["event_type"] == "report_proof") == 1,
            "report-proof retry should not duplicate historical proof event",
        )
        run("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        good_hydration_events = events(state_root, "goal-runtime")
        proof_hydration_workflow = workflow(state_root, "goal-runtime")
        proof_hydration_workflow["visible_delivery_state"].pop("report_proof", None)
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(proof_hydration_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        wrong_hydration_events = [dict(event) for event in events(state_root, "goal-runtime")]
        for event in wrong_hydration_events:
            if event["event_type"] == "report_proof":
                event["checkpoint_id"] = "chk-wrong-proof"
                break
        write_events(state_root, "goal-runtime", wrong_hydration_events)
        wrong_hydration_proof = run_fail(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(
            "checkpoint_id does not match delivery_reserved" in wrong_hydration_proof["error"],
            "report-proof hydration should reject proof events bound to the wrong checkpoint",
        )
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(proof_hydration_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_events(state_root, "goal-runtime", good_hydration_events)
        hydrated_proof = run(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(hydrated_proof["proof"]["delivery_message_id"] == "20200", "report-proof retry should rehydrate after rejected corrupt event state")
        pre_reported_workflow = workflow(state_root, "goal-runtime")
        empty_complete = run_fail(
            "complete-reported",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200-empty",
            "--visible-delivery",
            "{}",
            state_root=state_root,
        )
        assert_true("visible_delivery" in empty_complete["error"], "complete-reported should reject empty visible delivery")
        blank_message_complete = run_fail(
            "complete-reported",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true("delivery_message_id" in blank_message_complete["error"], "complete-reported should reject empty delivery message id")
        assert_true(workflow(state_root, "goal-runtime")["status"] == "completed_unreported", "invalid report proof should not mark workflow reported")

        reported = run(
            "complete-reported",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(reported["status"] == "reported", "complete-reported should mark workflow reported")
        final_workflow = workflow(state_root, "goal-runtime")
        assert_true(final_workflow["active_delivery_reservation"] is None, "complete-reported should clear reservation")
        assert_true(final_workflow["visible_delivery_state"]["reported"]["delivery_message_id"] == "20200", "reported proof missing")
        assert_true(
            final_workflow["visible_delivery_state"]["reported"]["report_authority"] == "converge.complete-reported"
            and final_workflow["visible_delivery_state"]["reported"]["source_of_truth"] == "converge.workflow",
            "complete-reported should expose Converge report authority",
        )
        fresh_reported_state = final_workflow["visible_delivery_state"]["reported"]
        run("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        final_events = events(state_root, "goal-runtime")
        assert_true(sum(1 for event in final_events if event["event_type"] == "delivery_reserved") == 1, "delivery reservation event should be unique")
        assert_true(sum(1 for event in final_events if event["event_type"] == "report_proof") == 1, "report proof event should be unique")
        assert_true(sum(1 for event in final_events if event["event_type"] == "report_sent") == 1, "report sent event should be unique")
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(pre_reported_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        hydrated_reported = run(
            "complete-reported",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(hydrated_reported["status"] == "reported", "complete-reported retry should hydrate missing reported JSON")
        assert_true(
            sum(1 for event in events(state_root, "goal-runtime") if event["event_type"] == "report_sent") == 1,
            "complete-reported retry should not duplicate historical report_sent event",
        )
        run("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        final_workflow = workflow(state_root, "goal-runtime")
        assert_true(
            final_workflow["visible_delivery_state"]["reported"] == fresh_reported_state,
            "fresh report transition and report_sent hydration should produce the same reported state",
        )
        final_events = events(state_root, "goal-runtime")
        corrupt_proof_events = json.loads(json.dumps(final_events))
        for event in corrupt_proof_events:
            if event["event_type"] == "report_proof":
                event["payload"].pop("recorded_at", None)
                break
        write_events(state_root, "goal-runtime", corrupt_proof_events)
        reported_missing_bad_proof = json.loads(json.dumps(final_workflow))
        reported_missing_bad_proof["visible_delivery_state"].pop("report_proof", None)
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(reported_missing_bad_proof, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        bad_historical_proof = run_fail(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true("report_proof requires non-empty recorded_at" in bad_historical_proof["error"], "historical report_proof payload should validate before hydration")
        assert_true("report_proof" not in workflow(state_root, "goal-runtime")["visible_delivery_state"], "bad proof hydration should not mutate workflow state")
        write_events(state_root, "goal-runtime", final_events)
        reported_missing_proof = json.loads(json.dumps(final_workflow))
        reported_missing_proof["visible_delivery_state"].pop("report_proof", None)
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(reported_missing_proof, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        reported_proof_retry = run(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(
            reported_proof_retry["proof"]["delivery_message_id"] == "20200",
            "reported report-proof retry should hydrate missing proof JSON",
        )
        assert_true(
            sum(1 for event in events(state_root, "goal-runtime") if event["event_type"] == "report_proof") == 1,
            "reported report-proof retry should not duplicate historical proof event",
        )
        run("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        reported_missing_state = workflow(state_root, "goal-runtime")
        reported_missing_state["visible_delivery_state"].pop("reported", None)
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(reported_missing_state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        reported_state_retry = run(
            "complete-reported",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(reported_state_retry["status"] == "reported", "reported complete-reported retry should hydrate missing reported JSON")
        assert_true(
            workflow(state_root, "goal-runtime")["visible_delivery_state"]["reported"] == fresh_reported_state,
            "reported complete-reported retry should restore report_sent JSON",
        )
        corrupt_report_sent_events = json.loads(json.dumps(final_events))
        for event in corrupt_report_sent_events:
            if event["event_type"] == "report_sent":
                event["payload"].pop("reported_at", None)
                break
        write_events(state_root, "goal-runtime", corrupt_report_sent_events)
        reported_missing_bad_sent = json.loads(json.dumps(final_workflow))
        reported_missing_bad_sent["visible_delivery_state"].pop("reported", None)
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(reported_missing_bad_sent, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        bad_historical_sent = run_fail(
            "complete-reported",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true("report_sent requires non-empty reported_at" in bad_historical_sent["error"], "historical report_sent payload should validate before hydration")
        assert_true("reported" not in workflow(state_root, "goal-runtime")["visible_delivery_state"], "bad report_sent hydration should not mutate workflow state")
        write_events(state_root, "goal-runtime", final_events)
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(final_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        assert_true(
            sum(1 for event in events(state_root, "goal-runtime") if event["event_type"] == "report_sent") == 1,
            "reported complete-reported retry should not duplicate historical report_sent event",
        )
        run("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        final_workflow = workflow(state_root, "goal-runtime")
        final_events = events(state_root, "goal-runtime")
        corrupted_report_state = json.loads(json.dumps(final_workflow))
        corrupted_report_state["visible_delivery_state"]["report_proof"]["delivery_message_id"] = "tampered"
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(corrupted_report_state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        bad_report_state = run_fail("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        assert_true(
            "report_proof" in bad_report_state["error"],
            "report proof state should match report_proof event",
        )
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(final_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        missing_report_events = [event for event in final_events if event["event_type"] not in {"report_proof", "report_sent"}]
        write_events(state_root, "goal-runtime", missing_report_events)
        missing_report_event = run_fail("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        assert_true(
            "report_proof" in missing_report_event["error"],
            "reported workflow should require report_proof/report_sent events",
        )
        missing_report_event_retry = run_fail(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(
            "matching report_proof event" in missing_report_event_retry["error"],
            "report-proof should not trust mutable workflow JSON without append-only proof event",
        )
        write_events(state_root, "goal-runtime", final_events)
        duplicate_report_events = [dict(event) for event in final_events]
        for event in final_events:
            if event["event_type"] == "report_proof":
                duplicate = dict(event)
                duplicate["event_id"] = "evt-proof-duplicate"
                duplicate_report_events.append(duplicate)
                break
        write_events(state_root, "goal-runtime", duplicate_report_events)
        duplicate_report_event = run_fail("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        assert_true(
            "exactly one report_proof" in duplicate_report_event["error"],
            "reported workflow should reject duplicate report_proof events",
        )
        write_events(state_root, "goal-runtime", final_events)
        missing_delivery_events = [event for event in final_events if event["event_type"] != "delivery_reserved"]
        write_events(state_root, "goal-runtime", missing_delivery_events)
        missing_delivery_event = run_fail("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        assert_true(
            "matching delivery_reserved" in missing_delivery_event["error"],
            "reported workflow should require report proof to match delivery_reserved event",
        )
        missing_delivery_report_proof = run_fail(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(
            "matching delivery_reserved" in missing_delivery_report_proof["error"],
            "report-proof should not trust existing proof without matching delivery_reserved event",
        )
        write_events(state_root, "goal-runtime", final_events)
        missing_report_sent_events = [event for event in final_events if event["event_type"] != "report_sent"]
        write_events(state_root, "goal-runtime", missing_report_sent_events)
        missing_report_sent_retry = run_fail(
            "complete-reported",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(
            "report_sent" in missing_report_sent_retry["error"],
            "complete-reported should not return success for reported state without report_sent event",
        )
        write_events(state_root, "goal-runtime", final_events)
        missing_delivery_message_workflow = json.loads(json.dumps(final_workflow))
        missing_delivery_message_workflow["visible_delivery_state"]["report_proof"].pop("delivery_message_id", None)
        missing_delivery_message_workflow["visible_delivery_state"]["reported"].pop("delivery_message_id", None)
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(missing_delivery_message_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        missing_delivery_message_events = [dict(event) for event in final_events]
        for event in missing_delivery_message_events:
            if event["event_type"] in {"report_proof", "report_sent"}:
                event["payload"] = dict(event["payload"])
                event["payload"].pop("delivery_message_id", None)
        write_events(state_root, "goal-runtime", missing_delivery_message_events)
        missing_delivery_message = run_fail("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        assert_true(
            "delivery_message_id" in missing_delivery_message["error"],
            "reported workflow should require concrete delivery_message_id",
        )
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(final_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_events(state_root, "goal-runtime", final_events)
        duplicate_delivery_events = [dict(event) for event in final_events]
        for event in final_events:
            if event["event_type"] == "delivery_reserved":
                duplicate = dict(event)
                duplicate["event_id"] = "evt-delivery-duplicate"
                duplicate_delivery_events.append(duplicate)
                break
        write_events(state_root, "goal-runtime", duplicate_delivery_events)
        duplicate_delivery_event = run_fail("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        assert_true(
            "duplicate reservations" in duplicate_delivery_event["error"],
            "terminal checkpoint should not have duplicate delivery reservations",
        )
        write_events(state_root, "goal-runtime", final_events)
        active_reported_workflow = json.loads(json.dumps(final_workflow))
        active_reported_workflow["active_delivery_reservation"] = delivery_workflow["active_delivery_reservation"]
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(active_reported_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        active_reported = run_fail("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        assert_true(
            "active_delivery_reservation requires terminal unreported" in active_reported["error"],
            "reported workflow should not retain active delivery reservation",
        )
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(final_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        wrong_report_checkpoint = [dict(event) for event in final_events]
        for event in wrong_report_checkpoint:
            if event["event_type"] in {"report_proof", "report_sent"}:
                event["checkpoint_id"] = "chk-wrong-report-proof"
        write_events(state_root, "goal-runtime", wrong_report_checkpoint)
        bad_report_checkpoint = run_fail("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        assert_true(
            "checkpoint_id does not match delivery_reserved" in bad_report_checkpoint["error"]
            or "matching delivery_reserved" in bad_report_checkpoint["error"]
            or "missing from checkpoint_index" in bad_report_checkpoint["error"],
            f"report proof/report sent checkpoints should bind to delivery reservation checkpoint: {bad_report_checkpoint['error']}",
        )
        wrong_report_checkpoint_retry = run_fail(
            "report-proof",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(
            "checkpoint_id does not match delivery_reserved" in wrong_report_checkpoint_retry["error"],
            "report-proof retry should reject proof events bound to the wrong checkpoint",
        )
        write_events(state_root, "goal-runtime", final_events)
        corrupted_events = [dict(event) for event in final_events]
        for event in corrupted_events:
            if event["event_type"] == "report_sent":
                event["checkpoint_id"] = "chk-does-not-exist"
                break
        write_events(state_root, "goal-runtime", corrupted_events)
        bad_checkpoint_ref = run_fail("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        assert_true("missing from checkpoint_index" in bad_checkpoint_ref["error"], "non-checkpoint event checkpoint refs should validate")
        write_events(state_root, "goal-runtime", final_events)
        corrupted_delivery_status = [dict(event) for event in final_events]
        for event in corrupted_delivery_status:
            if event["event_type"] == "delivery_reserved":
                event["status_after"] = "failed_unreported"
                break
        write_events(state_root, "goal-runtime", corrupted_delivery_status)
        bad_delivery_status = run_fail("validate", "--workflow-id", "goal-runtime", state_root=state_root)
        assert_true(
            "status_after does not match checkpoint" in bad_delivery_status["error"],
            "historical delivery reservation status should match terminal checkpoint",
        )
        write_events(state_root, "goal-runtime", final_events)
        missing_checkpoint_index_workflow = json.loads(json.dumps(final_workflow))
        missing_checkpoint_index_workflow["checkpoint_index"] = {}
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(missing_checkpoint_index_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        missing_checkpoint_index_retry = run_fail(
            "complete-reported",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(
            "checkpoint_index" in missing_checkpoint_index_retry["error"],
            "reported complete-reported retry should require checkpoint index integrity",
        )
        (state_root / "workflows" / "goal-runtime" / "workflow.json").write_text(
            json.dumps(final_workflow, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        duplicate_reported = run(
            "complete-reported",
            "--workflow-id",
            "goal-runtime",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "20200",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(duplicate_reported["status"] == "reported", "duplicate complete-reported should be idempotent")
        events_after_duplicate_reported = events(state_root, "goal-runtime")
        assert_true(
            sum(1 for event in events_after_duplicate_reported if event["event_type"] == "delivery_reserved") == 1,
            "duplicate complete-reported should not add delivery reservations",
        )
        assert_true(
            sum(1 for event in events_after_duplicate_reported if event["event_type"] == "report_proof") == 1,
            "duplicate complete-reported should not add report proofs",
        )
        assert_true(
            sum(1 for event in events_after_duplicate_reported if event["event_type"] == "report_sent") == 1,
            "duplicate complete-reported should not add report sent events",
        )
        reported_reserve = run(
            "reserve-delivery",
            "--workflow-id",
            "goal-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "reported reserve should not send",
            "--terminal-evidence",
            '{"evidence_key":"reported-reserve-runtime-smoke","kind":"smoke","summary":"reported reserve","artifact_refs":["worklog.md#runtime-smoke"]}',
            "--final-status",
            '{"result":"pass","residuals":{}}',
            "--json",
            state_root=state_root,
        )
        assert_true(
            reported_reserve["send_authorized"] is False and reported_reserve["reason"] == "invalid_state",
            "reserve-delivery against reported workflow should return stable invalid_state no-send payload",
        )

        stale_terminal_goal = run("start", "--kind", "goal", "--text", "Stale terminal artifact", "--workflow-id", "stale-terminal-runtime", state_root=state_root)
        assert_true(stale_terminal_goal["workflow"]["kind"] == "goal", "stale terminal fixture should create goal workflow")
        terminal_artifact_path = state_root / "terminal-artifact.txt"
        terminal_artifact_path.write_text("terminal artifact evidence\n", encoding="utf-8")
        terminal_artifact = run(
            "artifact",
            "--workflow-id",
            "stale-terminal-runtime",
            "--kind",
            "evidence",
            "--path",
            str(terminal_artifact_path),
            state_root=state_root,
        )["artifact"]
        terminal_artifact_path.write_text("terminal artifact evidence changed\n", encoding="utf-8")
        stale_terminal = run_fail(
            "checkpoint",
            "--workflow-id",
            "stale-terminal-runtime",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "stale terminal artifact should fail",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "completed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "objective-gate",
                    "cursor_after": "objective-gate",
                    "event_type": "complete",
                    "event_status": "completed_unreported",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "residuals": {},
                    "mode_state_update": {},
                    "terminal_evidence": {
                        "evidence_key": "stale-terminal-artifact",
                        "kind": "smoke",
                        "summary": "stale terminal artifact rejected",
                        "artifact_refs": [terminal_artifact["artifact_id"]],
                    },
                    "final_status": {"result": "pass", "residuals": {}},
                }
            ),
            state_root=state_root,
        )
        assert_true("stale" in stale_terminal["error"], "stale terminal artifact refs should fail before reservation")

        manual_goal = run("plan", "--text", "Manual reconcile workflow", "--workflow-id", "manual-runtime", state_root=state_root)
        assert_true(manual_goal["workflow"]["kind"] == "plan", "manual reconcile fixture should create plan workflow")
        manual_checkpoint = manual_goal["checkpoint"]
        manual_terminal = run(
            "reserve-delivery",
            "--workflow-id",
            "manual-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "manual terminal complete",
            "--terminal-evidence",
            '{"evidence_key":"manual-terminal-runtime-smoke","kind":"smoke","summary":"manual terminal passed","artifact_refs":["worklog.md#runtime-smoke"]}',
            "--final-status",
            json.dumps(manual_goal["workflow"]["final_status"]),
            state_root=state_root,
        )
        manual_reservation_id = manual_terminal["reservation_id"]
        manual_workflow = workflow(state_root, "manual-runtime")
        manual_workflow["active_delivery_reservation"] = None
        (state_root / "workflows" / "manual-runtime" / "workflow.json").write_text(json.dumps(manual_workflow, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        no_manual = run_fail(
            "report-proof",
            "--workflow-id",
            "manual-runtime",
            "--reservation-id",
            manual_reservation_id,
            "--delivery-message-id",
            "20210",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true("active delivery reservation" in no_manual["error"], "missing reservation should require manual reconcile")
        wrong_manual = run_fail(
            "complete-reported",
            "--workflow-id",
            "manual-runtime",
            "--reservation-id",
            "delivery-wrong",
            "--delivery-message-id",
            "20210",
            "--visible-delivery",
            visible_delivery,
            "--manual-reconcile",
            "wrong reservation should fail",
            state_root=state_root,
        )
        assert_true("matching delivery_reserved" in wrong_manual["error"], "manual reconcile should require historical reservation")
        wrong_target_manual = run_fail(
            "complete-reported",
            "--workflow-id",
            "manual-runtime",
            "--reservation-id",
            manual_reservation_id,
            "--delivery-message-id",
            "20210",
            "--visible-delivery",
            '{"channel":"telegram","target":"wrong"}',
            "--manual-reconcile",
            "wrong target should fail",
            state_root=state_root,
        )
        assert_true("matching delivery_reserved" in wrong_target_manual["error"], "manual reconcile should bind historical visible target")
        manual_reported = run(
            "complete-reported",
            "--workflow-id",
            "manual-runtime",
            "--reservation-id",
            manual_reservation_id,
            "--delivery-message-id",
            "20210",
            "--visible-delivery",
            visible_delivery,
            "--manual-reconcile",
            "existing Telegram message was verified manually",
            state_root=state_root,
        )
        assert_true(manual_reported["status"] == "reported", "manual reconcile should mark workflow reported")
        assert_true(
            workflow(state_root, "manual-runtime")["visible_delivery_state"]["report_proof"]["manual_reconcile"],
            "manual reconcile reason should persist",
        )
        run("validate", "--workflow-id", "manual-runtime", state_root=state_root)
        manual_events = events(state_root, "manual-runtime")
        assert_true(sum(1 for event in manual_events if event["event_type"] == "report_proof") == 1, "manual reconcile should create one proof")
        assert_true(sum(1 for event in manual_events if event["event_type"] == "report_sent") == 1, "manual reconcile should create one report event")
        terminal_checkpoint_id = manual_checkpoint["checkpoint_id"]
        for event in manual_events:
            if event["event_type"] in {"report_proof", "report_sent"}:
                assert_true(event.get("checkpoint_id") == terminal_checkpoint_id, "manual report events should bind terminal checkpoint")
        duplicate_manual = run(
            "complete-reported",
            "--workflow-id",
            "manual-runtime",
            "--reservation-id",
            manual_reservation_id,
            "--delivery-message-id",
            "20210",
            "--visible-delivery",
            visible_delivery,
            "--manual-reconcile",
            "existing Telegram message was verified manually",
            state_root=state_root,
        )
        assert_true(duplicate_manual["status"] == "reported", "duplicate manual reconcile should be idempotent")
        manual_events_after_duplicate = events(state_root, "manual-runtime")
        assert_true(
            sum(1 for event in manual_events_after_duplicate if event["event_type"] == "report_proof") == 1,
            "duplicate manual reconcile should not add proof events",
        )
        assert_true(
            sum(1 for event in manual_events_after_duplicate if event["event_type"] == "report_sent") == 1,
            "duplicate manual reconcile should not add report events",
        )

        failed_goal = run("plan", "--text", "Failed terminal workflow", "--workflow-id", "failed-runtime", state_root=state_root)
        assert_true(failed_goal["workflow"]["kind"] == "plan", "failed fixture should create plan workflow")
        failed_fixture = failed_goal["workflow"]
        failed_fixture["status"] = "running"
        failed_fixture["phase"] = "start"
        failed_fixture["final_status"] = None
        failed_fixture["checkpoint_index"] = {}
        failed_fixture["active_delivery_reservation"] = None
        failed_fixture["verification"] = {}
        (state_root / "workflows" / "failed-runtime" / "workflow.json").write_text(
            json.dumps(failed_fixture, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_events(
            state_root,
            "failed-runtime",
            [event for event in events(state_root, "failed-runtime") if event["event_type"] != "complete"],
        )
        missing_failure_reason = run_fail(
            "checkpoint",
            "--workflow-id",
            "failed-runtime",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "failed terminal missing reason",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "failed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "start",
                    "cursor_after": "start",
                    "event_type": "fail",
                    "event_status": "failed_unreported",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "residuals": {"blocking_remaining": ["failure reason missing"]},
                    "mode_state_update": failed_goal["workflow"]["plan_state"],
                    "final_status": {"result": "needs_fix", "residuals": {"blocking_remaining": ["failure reason missing"]}},
                }
            ),
            state_root=state_root,
        )
        assert_true("failure_reason" in missing_failure_reason["error"], "failed terminal reservation should require failure reason")
        run(
            "checkpoint",
            "--workflow-id",
            "failed-runtime",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "failed terminal complete",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "failed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "start",
                    "cursor_after": "start",
                    "event_type": "fail",
                    "event_status": "failed_unreported",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "residuals": {"blocking_remaining": ["smoke-covered failure"]},
                    "mode_state_update": failed_goal["workflow"]["plan_state"],
                    "failure_reason": "smoke-covered failure",
                    "final_status": {"result": "needs_fix", "residuals": {"blocking_remaining": ["smoke-covered failure"]}},
                }
            ),
            state_root=state_root,
        )
        failed_terminal = run(
            "reserve-delivery",
            "--workflow-id",
            "failed-runtime",
            "--terminal-status",
            "failed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "failed terminal complete",
            "--final-status",
            '{"result":"needs_fix","residuals":{"blocking_remaining":["smoke-covered failure"]}}',
            state_root=state_root,
        )
        assert_true(workflow(state_root, "failed-runtime")["status"] == "failed_unreported", "failed reservation should preserve failed_unreported")
        failed_status_mismatch = run(
            "reserve-delivery",
            "--workflow-id",
            "failed-runtime",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "wrong failed status terminal",
            "--final-status",
            '{"result":"needs_fix","residuals":{"blocking_remaining":["smoke-covered failure"]}}',
            state_root=state_root,
        )
        assert_true(
            failed_status_mismatch["send_authorized"] is False
            and failed_status_mismatch["reason"] == "terminal_status_mismatch"
            and failed_status_mismatch["terminal_status"] == "failed_unreported",
            "reserve-delivery should reject mismatched terminal-status for failed workflows",
        )
        failed_reported = run(
            "complete-reported",
            "--workflow-id",
            "failed-runtime",
            "--reservation-id",
            failed_terminal["reservation_id"],
            "--delivery-message-id",
            "20220",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(failed_reported["status"] == "reported", "failed terminal report should mark reported")

    print(
        json.dumps(
            {
                "ok": True,
                "checked": [
                    "mode helper wrappers",
                    "mode helper wrapper contracts",
                    "artifact registration",
                    "artifact edge guards",
                    "advance checkpoint path",
                    "advance terminal_ready",
                    "delivery reservation",
                    "duplicate delivery no-send",
                    "report proof binding",
                    "report proof idempotency",
                    "complete-reported transition",
                    "complete-reported idempotency",
                    "manual reconcile report proof",
                    "manual reconcile historical reservation binding",
                    "manual reconcile idempotency",
                    "checkpoint-only terminal delivery recovery",
                    "terminal delivery recovery historical no-send",
                    "expired active delivery reservation reason",
                    "failed terminal reporting",
                    "non-checkpoint event checkpoint ref integrity",
                    "checkpoint index and reservation ref integrity",
                    "active delivery reservation event binding",
                    "historical delivery reservation checkpoint binding",
                    "report proof event integrity",
                    "report proof crash retry hydration",
                    "report sent crash retry hydration",
                    "reported proof crash retry hydration",
                    "reported state crash retry hydration",
                    "report proof delivery reservation binding",
                    "report proof required delivery message id",
                    "duplicate delivery reservation rejection",
                    "reported active delivery rejection",
                    "reported reserve invalid_state no-send",
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
