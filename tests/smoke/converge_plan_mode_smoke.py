#!/usr/bin/env python3
"""Smoke coverage for C1 plan mode."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path


try:
    from smoke_helpers import ROOT, assert_true, events, run, run_fail, workflow, write_workflow
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import ROOT, assert_true, events, run, run_fail, workflow, write_workflow
from converge.messages import format_final  # noqa: E402
from converge.modes.plan import build_plan_draft, render_plan_markdown  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="converge-plan-mode-smoke-") as tmp:
        state_root = Path(tmp)
        visible_delivery = '{"channel":"telegram","target":"test"}'
        payload = run(
            "plan",
            "--text",
            "Design a focused C1 plan mode implementation",
            "--workflow-id",
            "plan-c1-smoke",
            "--owner-session-key",
            "session:test",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        wf = payload["workflow"]
        assert_true(wf["kind"] == "plan", "plan command should create a plan workflow")
        assert_true(wf["status"] == "completed_unreported", "plan mode should stop at terminal unreported")
        assert_true(wf["continuation_plan"] is None, "plan mode should keep the short-workflow contract")
        assert_true(wf["plan_state"]["final_plan_artifact_id"] == "plan-final", "plan state should point to final artifact")
        assert_true(wf["plan_state"]["final_plan_artifact_path"] == wf["artifacts"][0]["path"], "plan state should use registered artifact path")
        assert_true(isinstance(wf["plan_state"]["intake_questions"], list), "plan state should carry intake questions")
        assert_true(wf["plan_state"]["answered_decisions"], "plan state should carry answered decisions")
        assert_true(wf["plan_state"]["deferred_decisions"], "plan state should carry deferred decisions")
        assert_true(wf["plan_state"]["promotion_recommendation"], "plan state should carry promotion recommendation")
        assert_true(wf["final_status"]["result"] == "pass_with_risks", "plan final status should preserve deferred implementation scope")
        assert_true(wf["next_safe_action"]["action_type"] == "report_terminal_status", "plan terminal state should require report flow")

        artifact = wf["artifacts"][0]
        artifact_path = Path(artifact["path"])
        assert_true(artifact["artifact_id"] == "plan-final", "plan artifact id mismatch")
        assert_true(artifact["kind"] == "plan", "plan artifact kind mismatch")
        assert_true(artifact_path.is_file(), "plan artifact should be materialized")
        assert_true("## Approval Boundaries" in artifact_path.read_text(encoding="utf-8"), "plan artifact should include approval boundaries")

        event_types = [event["event_type"] for event in events(state_root, "plan-c1-smoke")]
        assert_true(event_types == ["start", "artifact", "complete"], "plan mode should use start/artifact/complete events only")
        assert_true(format_final(wf).startswith("■ Plan final"), "plan final report marker mismatch")

        reservation = run(
            "reserve-delivery",
            "--workflow-id",
            "plan-c1-smoke",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "reserve final plan delivery",
            "--final-status",
            json.dumps(wf["final_status"]),
            state_root=state_root,
        )
        assert_true(reservation["send_authorized"] is True, "reserve-delivery should authorize terminal plan report")
        reservation_id = reservation["reservation_id"]
        reported = run(
            "complete-reported",
            "--workflow-id",
            "plan-c1-smoke",
            "--reservation-id",
            reservation_id,
            "--delivery-message-id",
            "telegram-message-1",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(reported["status"] == "reported", "complete-reported should mark plan workflow reported")
        assert_true(workflow(state_root, "plan-c1-smoke")["status"] == "reported", "reported status should persist")

        active_plan = run(
            "start",
            "--kind",
            "plan",
            "--text",
            "Do not bypass plan finalization",
            "--workflow-id",
            "plan-reserve-bypass",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(active_plan["workflow"]["status"] == "running", "reserve bypass fixture should start running")
        bypass = run_fail(
            "reserve-delivery",
            "--workflow-id",
            "plan-reserve-bypass",
            "--terminal-status",
            "completed",
            "--visible-delivery",
            visible_delivery,
            "--summary",
            "bad reserve bypass",
            "--final-status",
            json.dumps(wf["final_status"]),
            "--terminal-evidence",
            json.dumps({"evidence_key": "bad-plan-reserve", "kind": "smoke", "summary": "bad reserve bypass", "artifact_refs": ["worklog.md#bad"]}),
            state_root=state_root,
        )
        assert_true("finalize through plan mode" in bypass["error"], "reserve-delivery should not terminalize active plan workflows")

        duplicate = run(
            "plan",
            "--text",
            "Duplicate plan",
            "--workflow-id",
            "plan-c1-smoke",
            state_root=state_root,
        )
        assert_true(duplicate["result"] == "already_finalized", "duplicate terminal plan retry should reconcile as already finalized")

        run("validate", "--workflow-id", "plan-c1-smoke", state_root=state_root)

        corrupt = workflow(state_root, "plan-c1-smoke")
        corrupt["plan_state"]["final_plan_artifact_id"] = "missing-plan"
        write_workflow(state_root, "plan-c1-smoke", corrupt)
        invalid_plan_state = run_fail("validate", "--workflow-id", "plan-c1-smoke", state_root=state_root)
        assert_true(
            "final_plan_artifact_id" in invalid_plan_state["error"]
            or "plan_state must match terminal checkpoint plan_state" in invalid_plan_state["error"],
            "validate should bind plan_state to registered artifact and checkpoint",
        )
        corrupt["plan_state"] = {}
        write_workflow(state_root, "plan-c1-smoke", corrupt)
        empty_plan_state = run_fail("validate", "--workflow-id", "plan-c1-smoke", state_root=state_root)
        assert_true(
            "populated plan_state" in empty_plan_state["error"]
            or "plan_state must match terminal checkpoint plan_state" in empty_plan_state["error"],
            "terminal plan validate should reject empty plan_state",
        )

        verification_evidence_drift = run(
            "plan",
            "--text",
            "Reject workflow verification evidence drift",
            "--workflow-id",
            "plan-evidence-drift",
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
        write_workflow(state_root, "plan-evidence-drift", verification_evidence_workflow)
        verification_evidence_result = run_fail("validate", "--workflow-id", "plan-evidence-drift", state_root=state_root)
        assert_true(
            "verification evidence must match checkpoint-backed terminal evidence sequence" in verification_evidence_result["error"],
            "terminal plan validate should reject workflow verification evidence drift",
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
        write_workflow(state_root, "plan-evidence-drift", verification_extra_workflow)
        verification_extra_result = run_fail("validate", "--workflow-id", "plan-evidence-drift", state_root=state_root)
        assert_true(
            "verification evidence must match checkpoint-backed terminal evidence sequence" in verification_extra_result["error"],
            "terminal plan validate should reject extra workflow verification evidence",
        )

        run(
            "start",
            "--kind",
            "plan",
            "--text",
            "Allow checkpoint-backed preterminal evidence",
            "--workflow-id",
            "plan-checkpoint-backed-evidence",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        preterminal_evidence = {
            "evidence_key": "checkpoint-backed-preterminal-evidence",
            "kind": "contract",
            "summary": "Checkpoint-backed evidence before terminal finalization.",
            "artifact_refs": [],
        }
        run(
            "checkpoint",
            "--workflow-id",
            "plan-checkpoint-backed-evidence",
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
            "record checkpoint-backed preterminal evidence",
            "--evidence",
            json.dumps(preterminal_evidence),
            state_root=state_root,
        )
        checkpoint_backed_final = run(
            "plan",
            "--text",
            "ignored retry text",
            "--workflow-id",
            "plan-checkpoint-backed-evidence",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        checkpoint_backed_workflow = checkpoint_backed_final["workflow"]
        assert_true(
            checkpoint_backed_workflow["verification"]["evidence"][0] == preterminal_evidence,
            "plan workflow should retain checkpoint-backed preterminal evidence",
        )
        run("validate", "--workflow-id", "plan-checkpoint-backed-evidence", state_root=state_root)

        resumed = run(
            "start",
            "--kind",
            "plan",
            "--text",
            "Resume a start-only plan",
            "--workflow-id",
            "plan-start-partial",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(resumed["workflow"]["status"] == "running", "start-only plan fixture should be running")
        resumed_final = run(
            "plan",
            "--text",
            "ignored retry text",
            "--workflow-id",
            "plan-start-partial",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(resumed_final["workflow"]["status"] == "completed_unreported", "plan command should resume start-only workflow")

        artifact_partial = run(
            "start",
            "--kind",
            "plan",
            "--text",
            "Resume an artifact-only plan",
            "--workflow-id",
            "plan-artifact-partial",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        partial_artifact_path = state_root / "workflows" / "plan-artifact-partial" / "artifacts" / "plan.md"
        partial_artifact_path.write_text("# Plan\n\n## Objective\n\n- Resume an artifact-only plan\n", encoding="utf-8")
        run(
            "artifact",
            "--workflow-id",
            "plan-artifact-partial",
            "--artifact-id",
            "plan-final",
            "--kind",
            "plan",
            "--path",
            str(partial_artifact_path),
            state_root=state_root,
        )
        artifact_resumed = run_fail(
            "plan",
            "--text",
            "ignored retry text",
            "--workflow-id",
            "plan-artifact-partial",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(artifact_partial["workflow"]["kind"] == "plan", "artifact-only fixture should be a plan")
        assert_true(
            "does not match rendered final plan" in artifact_resumed["error"],
            "plan command should not bless a non-final registered plan artifact",
        )

        external_artifact = run(
            "start",
            "--kind",
            "plan",
            "--text",
            "Reject an external final plan",
            "--workflow-id",
            "plan-external-artifact",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        external_path = state_root / "external" / "artifacts" / "plan.md"
        external_path.parent.mkdir(parents=True)
        external_path.write_text((state_root / "workflows" / "plan-artifact-partial" / "artifacts" / "plan.md").read_text(encoding="utf-8"), encoding="utf-8")
        run(
            "artifact",
            "--workflow-id",
            "plan-external-artifact",
            "--artifact-id",
            "plan-final",
            "--kind",
            "plan",
            "--path",
            str(external_path),
            state_root=state_root,
        )
        external_resumed = run_fail(
            "plan",
            "--text",
            external_artifact["workflow"]["source_request"],
            "--workflow-id",
            "plan-external-artifact",
            "--visible-delivery",
            visible_delivery,
            state_root=state_root,
        )
        assert_true(
            "canonical plan output path" in external_resumed["error"],
            "plan command should reject external pre-registered plan artifact paths",
        )

        with tempfile.TemporaryDirectory(prefix="converge-plan-relative-", dir=ROOT) as relative_tmp:
            relative_root = Path(relative_tmp).relative_to(ROOT)
            run(
                "start",
                "--kind",
                "plan",
                "--text",
                "Relative root artifact retry",
                "--workflow-id",
                "plan-relative-artifact",
                "--visible-delivery",
                visible_delivery,
                state_root=relative_root,
            )
            relative_plan_arg = relative_root / "workflows" / "plan-relative-artifact" / "artifacts" / "plan.md"
            relative_plan_path = ROOT / relative_plan_arg
            relative_plan_path.parent.mkdir(parents=True, exist_ok=True)
            relative_plan_path.write_text(
                render_plan_markdown(build_plan_draft("Relative root artifact retry")),
                encoding="utf-8",
            )
            run(
                "artifact",
                "--workflow-id",
                "plan-relative-artifact",
                "--artifact-id",
                "plan-final",
                "--kind",
                "plan",
                "--path",
                str(relative_plan_arg),
                state_root=relative_root,
            )
            relative_resumed = run(
                "plan",
                "--text",
                "ignored retry text",
                "--workflow-id",
                "plan-relative-artifact",
                "--visible-delivery",
                visible_delivery,
                state_root=relative_root,
            )
            assert_true(relative_resumed["workflow"]["status"] == "completed_unreported", "relative state-root artifact retry should finalize")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
