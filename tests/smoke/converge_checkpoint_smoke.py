#!/usr/bin/env python3
"""Smoke coverage for checkpoint atomicity and validation."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from converge.messages import format_final  # noqa: E402


def run(*args: str, state_root: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "converge.cli", "--state-root", str(state_root), *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"command failed: {' '.join(args)}\nstdout={result.stdout}\nstderr={result.stderr}")
    payload = json.loads(result.stdout)
    if args and args[0] in {"start", "plan", "goal", "verify", "conv"}:
        workflow_id = payload.get("workflow", {}).get("workflow_id")
        if workflow_id:
            seed_worklog_anchor(state_root, workflow_id, "Checkpoint Smoke")
            seed_worklog_anchor(state_root, workflow_id, "Terminal Summary")
    return payload


def run_fail(*args: str, state_root: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "converge.cli", "--state-root", str(state_root), *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        raise AssertionError(f"command unexpectedly succeeded: {' '.join(args)}\nstdout={result.stdout}")
    return json.loads(result.stdout)


def run_raw(*args: str, state_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "converge.cli", "--state-root", str(state_root), *args],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def seed_worklog_anchor(state_root: Path, workflow_id: str, heading: str) -> None:
    worklog_path = state_root / "workflows" / workflow_id / "worklog.md"
    existing = worklog_path.read_text(encoding="utf-8") if worklog_path.exists() else ""
    if f"## {heading}" not in existing:
        worklog_path.write_text(existing + f"\n## {heading}\n\n", encoding="utf-8")


def next_action(action_type: str, cursor: str, *, requires_approval: bool = False, policy: str = "repeatable") -> dict:
    return {
        "action_type": action_type,
        "summary": f"{action_type} at {cursor}",
        "risk_class": "read_only",
        "requires_approval": requires_approval,
        "approval_ref": None,
        "side_effect_key": f"{action_type}:{cursor}",
        "idempotency_policy": policy,
        "expected_artifacts": ["workflow.json", "worklog.md"],
        "cursor": cursor,
    }


def install_two_step_plan(state_root: Path, workflow_id: str) -> None:
    # Fixture-only continuation plan used to exercise advance gates after C0
    # removed the production Slice 1-9 default plan.
    workflow_path = state_root / "workflows" / workflow_id / "workflow.json"
    workflow = read_json(workflow_path)
    workflow["continuation_plan"] = {
        "plan_id": "test-two-step-plan",
        "current_step_index": 0,
        "steps": [
            {
                "step_id": "slice-1",
                "objective": "First test step",
                "expected_artifacts": ["workflow.json", "worklog.md"],
                "gate": {"type": "smoke", "requires_evidence": True},
                "allowed_risk_classes": ["local_files"],
                "verification_commands": ["python -m converge.cli validate --sample-docs"],
                "next_on_pass": "slice-2",
                "next_on_fail": "blocked",
            },
            {
                "step_id": "slice-2",
                "objective": "Second test step",
                "expected_artifacts": ["workflow.json", "worklog.md"],
                "gate": {"type": "smoke", "requires_evidence": True},
                "allowed_risk_classes": ["local_files"],
                "verification_commands": ["python -m converge.cli validate --sample-docs"],
                "next_on_pass": "complete",
                "next_on_fail": "blocked",
            },
        ],
        "budgets": {
            "max_steps_per_wake": 1,
            "max_rounds": 5,
            "max_retries_per_step": 1,
        },
        "stop_conditions": ["evidence_failure"],
        "rolling_state": {
            "completed_steps": [],
            "open_decisions": [],
            "evidence_map": {},
            "residuals": {
                "blocking_remaining": [],
                "accepted_risks": [],
                "implementation_backlog": [],
                "deferred_scope": [],
            },
            "active_child_workflows": [],
            "current_resume_cursor": "slice-1",
            "last_checkpoint_id": None,
        },
    }
    workflow["next_safe_action"] = next_action("continue", "slice-1")
    write_json(workflow_path, workflow)


def acceptance_payload(
    objective: str,
    *,
    success_criteria: list[str] | None = None,
    plan_artifact_ref: str = "artifact://plan",
    plan_artifact_hash: str = "sha256-demo",
    accepted_at: str = "2026-05-24T00:00:00Z",
) -> str:
    return json.dumps(
        {
            "objective": objective,
            "non_goals": [],
            "success_criteria": success_criteria or [],
            "assumptions": [],
            "approval_boundaries": [],
            "plan_artifact_ref": plan_artifact_ref,
            "plan_artifact_hash": plan_artifact_hash,
            "source_ref": "telegram:19982",
            "accepted_at": accepted_at,
        }
    )


def event_count(workflow_dir: Path) -> int:
    return len((workflow_dir / "events.jsonl").read_text(encoding="utf-8").splitlines())


def worklog_checkpoint_count(workflow_dir: Path) -> int:
    return len(re.findall(r"^## Checkpoint chk-", (workflow_dir / "worklog.md").read_text(encoding="utf-8"), flags=re.MULTILINE))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="converge-checkpoint-smoke-") as tmp:
        state_root = Path(tmp)
        run("start", "--kind", "goal", "--text", "Implement a slice", "--workflow-id", "goal-checkpoint", state_root=state_root)
        install_two_step_plan(state_root, "goal-checkpoint")
        workflow_dir = state_root / "workflows" / "goal-checkpoint"
        missing_worklog_ref = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "missing worklog evidence",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            "--evidence",
            '{"evidence_key":"missing-worklog","kind":"smoke","summary":"missing","artifact_refs":["worklog.md#definitely-missing-anchor"]}',
            state_root=state_root,
        )
        assert_true("worklog artifact_ref is not found" in missing_worklog_ref["error"], "dangling worklog evidence ref should fail")

        mismatch = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "advance",
            "--summary",
            "bad mismatch",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"passed"}',
            "--evidence",
            '{"evidence_key":"stale","kind":"smoke","summary":"stale","artifact_refs":["worklog.md#checkpoint-smoke"]}',
            state_root=state_root,
        )
        assert_true("does not match" in mismatch["error"], "checkpoint type mismatch should fail")

        bad_event = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "bad event",
            "--state-update",
            '{"checkpoint_type":"terminal","status_after":"completed_unreported","phase_after":"terminal","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"completed_unreported","worklog_block_kind":"terminal_summary","step_result":"terminal"}',
            state_root=state_root,
        )
        assert_true("completed_unreported" in bad_event["error"], "status-as-event should fail")

        bad_terminal_mix = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "bad terminal mix",
            "--state-update",
            '{"checkpoint_type":"terminal","status_after":"running","phase_after":"terminal","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"advance","worklog_block_kind":"terminal_summary","step_result":"terminal"}',
            "--evidence",
            '{"evidence_key":"bad-terminal","kind":"smoke","summary":"bad","artifact_refs":["worklog.md#checkpoint-smoke"]}',
            state_root=state_root,
        )
        assert_true("terminal checkpoints require" in bad_terminal_mix["error"], "terminal event/status matrix should fail")

        bad_non_terminal_matrix = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "bad non-terminal matrix",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"advance","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true("checkpoint checkpoints require checkpoint event_type" in bad_non_terminal_matrix["error"], "checkpoint/event matrix should fail")

        bad_advance_matrix = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "advance",
            "--summary",
            "bad advance matrix",
            "--state-update",
            '{"checkpoint_type":"advance","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-2","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"passed"}',
            "--evidence",
            '{"evidence_key":"bad-advance","kind":"smoke","summary":"bad advance","artifact_refs":["worklog.md#checkpoint-smoke"]}',
            state_root=state_root,
        )
        assert_true("advance checkpoints require advance event_type" in bad_advance_matrix["error"], "advance/event matrix should fail")

        missing_target_workflow_path = workflow_dir / "workflow.json"
        missing_target_workflow = read_json(missing_target_workflow_path)
        missing_target_workflow["continuation_plan"]["steps"][0]["next_on_pass"] = "slice-missing"
        write_json(missing_target_workflow_path, missing_target_workflow)
        missing_target_checkpoint = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "advance",
            "--summary",
            "missing target",
            "--state-update",
            '{"checkpoint_type":"advance","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-missing","event_type":"advance","worklog_block_kind":"slice_summary","step_result":"passed"}',
            "--evidence",
            '{"evidence_key":"missing-target","kind":"smoke","summary":"missing target","artifact_refs":["worklog.md#checkpoint-smoke"]}',
            state_root=state_root,
        )
        assert_true("next_on_pass" in missing_target_checkpoint["error"], "advance to missing continuation step should fail")
        missing_target_workflow["continuation_plan"]["steps"][0]["next_on_pass"] = "slice-2"
        write_json(missing_target_workflow_path, missing_target_workflow)

        run("start", "--kind", "goal", "--text", "Terminal sentinel", "--workflow-id", "terminal-sentinel", state_root=state_root)
        install_two_step_plan(state_root, "terminal-sentinel")
        terminal_sentinel_path = state_root / "workflows" / "terminal-sentinel" / "workflow.json"
        terminal_sentinel_workflow = read_json(terminal_sentinel_path)
        terminal_sentinel_workflow["continuation_plan"]["current_step_index"] = 1
        terminal_sentinel_workflow["continuation_plan"]["rolling_state"]["current_resume_cursor"] = "slice-2"
        terminal_sentinel_workflow["next_safe_action"] = next_action("run_slice", "slice-2")
        write_json(terminal_sentinel_path, terminal_sentinel_workflow)
        terminal_sentinel_advance = run_fail(
            "checkpoint",
            "--workflow-id",
            "terminal-sentinel",
            "--checkpoint-type",
            "advance",
            "--summary",
            "terminal sentinel",
            "--state-update",
            '{"checkpoint_type":"advance","status_after":"running","phase_after":"slice","cursor_before":"slice-2","cursor_after":"complete","event_type":"advance","worklog_block_kind":"slice_summary","step_result":"passed"}',
            "--evidence",
            '{"evidence_key":"terminal-sentinel","kind":"smoke","summary":"terminal sentinel","artifact_refs":["worklog.md#checkpoint-smoke"]}',
            state_root=state_root,
        )
        assert_true("terminal continuation target" in terminal_sentinel_advance["error"], "active advance should not enter terminal sentinel")

        run("start", "--kind", "goal", "--text", "Terminal complete", "--workflow-id", "terminal-complete", state_root=state_root)
        install_two_step_plan(state_root, "terminal-complete")
        terminal_complete = run(
            "checkpoint",
            "--workflow-id",
            "terminal-complete",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "terminal complete",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "completed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "slice-1",
                    "cursor_after": "slice-1",
                    "event_type": "complete",
                    "event_status": "completed_unreported",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "residuals": {},
                    "terminal_evidence": {
                        "evidence_key": "final-report-ready",
                        "kind": "verification",
                        "summary": "Final report is ready.",
                        "artifact_refs": ["worklog.md#terminal-summary"],
                    },
                    "final_status": {
                        "result": "pass",
                        "done": ["terminal complete checkpoint"],
                        "checked": ["terminal evidence"],
                        "residuals": {},
                    },
                }
            ),
            state_root=state_root,
        )
        assert_true(terminal_complete["ok"], "terminal_evidence should satisfy complete checkpoint evidence")
        terminal_complete_workflow = run("status", "--workflow-id", "terminal-complete", state_root=state_root)["workflow"]
        assert_true(terminal_complete_workflow["status"] == "completed_unreported", "terminal complete status mismatch")
        assert_true(terminal_complete_workflow["final_status"]["result"] == "pass", "terminal final status should be retained")
        assert_true(terminal_complete_workflow["verification"]["evidence"], "terminal evidence should be retained")
        assert_true("■ Goal final" in format_final(terminal_complete_workflow), "terminal complete should format final")

        run("start", "--kind", "goal", "--text", "Terminal fail", "--workflow-id", "terminal-fail", state_root=state_root)
        install_two_step_plan(state_root, "terminal-fail")
        terminal_fail = run(
            "checkpoint",
            "--workflow-id",
            "terminal-fail",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "terminal fail",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "failed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "slice-1",
                    "cursor_after": "slice-1",
                    "event_type": "fail",
                    "event_status": "failed_unreported",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "residuals": {"blocking_remaining": ["verification_blocker_unresolved"]},
                    "failure_reason": "verification_blocker_unresolved",
                    "final_status": {
                        "result": "blocked",
                        "done": ["terminal failed checkpoint"],
                        "checked": ["failure reason"],
                        "residuals": {"blocking_remaining": ["verification_blocker_unresolved"]},
                    },
                }
            ),
            state_root=state_root,
        )
        assert_true(terminal_fail["ok"], "failure_reason should satisfy failed terminal checkpoint")
        terminal_fail_workflow = run("status", "--workflow-id", "terminal-fail", state_root=state_root)["workflow"]
        assert_true("■ Goal final" in format_final(terminal_fail_workflow), "terminal fail should format final")

        run("start", "--kind", "goal", "--text", "Hidden terminal residual", "--workflow-id", "terminal-hidden-residual", state_root=state_root)
        install_two_step_plan(state_root, "terminal-hidden-residual")
        hidden_terminal_residual = run_fail(
            "checkpoint",
            "--workflow-id",
            "terminal-hidden-residual",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "terminal hidden residual",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "completed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "slice-1",
                    "cursor_after": "slice-1",
                    "event_type": "complete",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "residuals": {"blocking_remaining": ["hidden blocker"]},
                    "terminal_evidence": {
                        "evidence_key": "hidden-residual",
                        "kind": "smoke",
                        "summary": "hidden residual",
                        "artifact_refs": ["worklog.md#checkpoint-smoke"],
                    },
                    "final_status": {"result": "pass", "residuals": {}},
                }
            ),
            state_root=state_root,
        )
        assert_true("final_status.residuals" in hidden_terminal_residual["error"], "terminal residuals must match final_status residuals")

        run("start", "--kind", "goal", "--text", "Bad terminal verdict", "--workflow-id", "terminal-bad-verdict", state_root=state_root)
        install_two_step_plan(state_root, "terminal-bad-verdict")
        bad_terminal_verdict = run_fail(
            "checkpoint",
            "--workflow-id",
            "terminal-bad-verdict",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "terminal bad verdict",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "failed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "slice-1",
                    "cursor_after": "slice-1",
                    "event_type": "fail",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "failure_reason": "bad_verdict",
                    "final_status": {"result": "fail"},
                }
            ),
            state_root=state_root,
        )
        assert_true(
            "invalid verdict" in bad_terminal_verdict["error"] or "not one of" in bad_terminal_verdict["error"],
            "terminal final_status should reject invalid verdict",
        )

        run("start", "--kind", "goal", "--text", "Terminal verdict alias", "--workflow-id", "terminal-verdict-alias", state_root=state_root)
        install_two_step_plan(state_root, "terminal-verdict-alias")
        terminal_verdict_alias = run_fail(
            "checkpoint",
            "--workflow-id",
            "terminal-verdict-alias",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "terminal verdict alias",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "completed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "slice-1",
                    "cursor_after": "slice-1",
                    "event_type": "complete",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "final_status": {"verdict": "pass", "residuals": {}},
                }
            ),
            state_root=state_root,
        )
        assert_true(
            "requires result" in terminal_verdict_alias["error"]
            or "missing required property 'result'" in terminal_verdict_alias["error"]
            or "unknown properties" in terminal_verdict_alias["error"],
            "terminal final_status should reject verdict alias",
        )

        run("start", "--kind", "goal", "--text", "Bad terminal pass", "--workflow-id", "terminal-bad-pass", state_root=state_root)
        install_two_step_plan(state_root, "terminal-bad-pass")
        bad_terminal_pass = run_fail(
            "checkpoint",
            "--workflow-id",
            "terminal-bad-pass",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "terminal bad pass",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "failed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "slice-1",
                    "cursor_after": "slice-1",
                    "event_type": "fail",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "failure_reason": "contradictory_pass",
                    "final_status": {"result": "pass"},
                }
            ),
            state_root=state_root,
        )
        assert_true("fail checkpoints require" in bad_terminal_pass["error"], "failed terminal should reject pass verdict")

        run("start", "--kind", "goal", "--text", "Bad terminal blocked", "--workflow-id", "terminal-bad-blocked", state_root=state_root)
        install_two_step_plan(state_root, "terminal-bad-blocked")
        bad_terminal_blocked = run_fail(
            "checkpoint",
            "--workflow-id",
            "terminal-bad-blocked",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "terminal bad blocked",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "completed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "slice-1",
                    "cursor_after": "slice-1",
                    "event_type": "complete",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "terminal_evidence": {
                        "evidence_key": "contradictory-blocked",
                        "kind": "smoke",
                        "summary": "contradictory blocked",
                        "artifact_refs": ["worklog.md#checkpoint-smoke"],
                    },
                    "final_status": {"result": "blocked"},
                }
            ),
            state_root=state_root,
        )
        assert_true("complete checkpoints require" in bad_terminal_blocked["error"], "complete terminal should reject blocked verdict")

        before_events = event_count(workflow_dir)
        before_blocks = worklog_checkpoint_count(workflow_dir)
        invalid_status = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "invalid status",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"not_a_status","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true("not_a_status" in invalid_status["error"], "invalid status should fail validation")
        assert_true(event_count(workflow_dir) == before_events, "failed checkpoint must not append event")
        assert_true(worklog_checkpoint_count(workflow_dir) == before_blocks, "failed checkpoint must not append worklog")

        missing_evidence = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "advance",
            "--summary",
            "missing evidence",
            "--state-update",
            '{"checkpoint_type":"advance","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-2","event_type":"advance","worklog_block_kind":"slice_summary","step_result":"passed"}',
            state_root=state_root,
        )
        assert_true("evidence is required" in missing_evidence["error"], "passed checkpoint should require evidence")

        workflow_path = workflow_dir / "workflow.json"
        workflow_with_lease = read_json(workflow_path)
        workflow_with_lease["active_recovery_lease"] = {
            "lease_id": "lease-smoke",
            "lease_type": "recovery",
            "cursor": "slice-1",
            "holder": "smoke",
            "acquired_at": "2026-05-24T00:00:00Z",
            "lease_expires_at": "2099-05-24T00:30:00Z",
            "checkpoint_id": "chk-before",
        }
        write_json(workflow_path, workflow_with_lease)
        blocked_by_lease = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "wrong lease wait",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true("recovery_lease_id" in blocked_by_lease["error"], "active lease should require matching lease id")
        waiting_checkpoint = run(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "same cursor wait",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting","recovery_lease_id":"lease-smoke","recovery_lease_holder":"smoke"}',
            state_root=state_root,
        )
        assert_true(waiting_checkpoint["ok"], "same cursor checkpoint should pass")
        after_wait = run("status", "--workflow-id", "goal-checkpoint", state_root=state_root)["workflow"]
        assert_true(after_wait["active_recovery_lease"] is None, "same-cursor checkpoint should clear matching lease")

        context_file = state_root / "context.txt"
        context_file.write_text("fresh\n", encoding="utf-8")
        run(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "capture context",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "checkpoint",
                    "status_after": "running",
                    "phase_after": "slice",
                    "cursor_before": "slice-1",
                    "cursor_after": "slice-1",
                    "event_type": "checkpoint",
                    "worklog_block_kind": "slice_summary",
                    "step_result": "waiting",
                    "context_manifest_updates": [{"path": str(context_file), "action": "capture"}],
                }
            ),
            state_root=state_root,
        )
        captured = run("status", "--workflow-id", "goal-checkpoint", state_root=state_root)["workflow"]
        assert_true(captured["context_manifest"], "context manifest should capture local file")
        context_file.write_text("stale\n", encoding="utf-8")
        stale_context = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "stale context",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true("context manifest is stale" in stale_context["error"], "stale context should block checkpoint")
        removed_context = run(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "remove stale context",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "checkpoint",
                    "status_after": "running",
                    "phase_after": "slice",
                    "cursor_before": "slice-1",
                    "cursor_after": "slice-1",
                    "event_type": "checkpoint",
                    "worklog_block_kind": "slice_summary",
                    "step_result": "waiting",
                    "context_manifest_updates": [{"path": str(context_file), "action": "remove"}],
                }
            ),
            state_root=state_root,
        )
        assert_true(removed_context["ok"], "stale context should be removable")
        after_remove = run("status", "--workflow-id", "goal-checkpoint", state_root=state_root)["workflow"]
        assert_true(after_remove["context_manifest"] == [], "context manifest should be empty after remove")

        pending_path = workflow_dir / ".pending-chk-smoke.json"
        write_json(pending_path, {"schema_version": 1, "checkpoint_id": "chk-smoke"})
        pending = run_fail("validate", "--workflow-id", "goal-checkpoint", state_root=state_root)
        assert_true("pending checkpoint transaction" in pending["error"], "pending checkpoint should block validation")
        pending_checkpoint = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "pending should block checkpoint",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true("pending checkpoint transaction" in pending_checkpoint["error"], "pending checkpoint should block new checkpoints")
        pending_path.unlink()

        reported_checkpoint = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "bad reported",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"reported","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true("reported and abandoned" in reported_checkpoint["error"], "checkpoint should not set reported")

        abandoned_checkpoint = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "bad abandoned",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"abandoned","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true("reported and abandoned" in abandoned_checkpoint["error"], "checkpoint should not set abandoned")

        same_cursor_passed = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "same cursor passed",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"passed"}',
            "--evidence",
            '{"evidence_key":"same-cursor","kind":"smoke","summary":"same","artifact_refs":["worklog.md#checkpoint-smoke"]}',
            state_root=state_root,
        )
        assert_true("passed continuation steps" in same_cursor_passed["error"], "passed continuation steps should advance cursor")

        mismatched_next_action = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "advance",
            "--summary",
            "bad next action",
            "--state-update",
            '{"checkpoint_type":"advance","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-2","event_type":"advance","worklog_block_kind":"slice_summary","step_result":"passed"}',
            "--next-action",
            json.dumps(next_action("run_slice", "slice-99")),
            "--evidence",
            '{"evidence_key":"bad-next-action","kind":"smoke","summary":"bad","artifact_refs":["worklog.md#checkpoint-smoke"]}',
            state_root=state_root,
        )
        assert_true("next_action.cursor" in mismatched_next_action["error"], "next_action cursor should match checkpoint cursor")
        unstructured_next_action = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "advance",
            "--summary",
            "unstructured next action",
            "--state-update",
            '{"checkpoint_type":"advance","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-2","event_type":"advance","worklog_block_kind":"slice_summary","step_result":"passed"}',
            "--next-action",
            '{"cursor":"slice-2","foo":"bar"}',
            "--evidence",
            '{"evidence_key":"bad-next-action-shape","kind":"smoke","summary":"bad","artifact_refs":["worklog.md#checkpoint-smoke"]}',
            state_root=state_root,
        )
        assert_true("next_action" in unstructured_next_action["error"], "next_action should require structured contract")

        checkpoint = run(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "advance",
            "--summary",
            "slice 1 passed",
            "--state-update",
            '{"checkpoint_type":"advance","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-2","event_type":"advance","worklog_block_kind":"slice_summary","step_result":"passed","residuals":{"blocking_remaining":[],"accepted_risks":[],"implementation_backlog":[],"deferred_scope":[]}}',
            "--next-action",
            json.dumps(next_action("run_slice", "slice-2")),
            "--evidence",
            '{"evidence_key":"slice1-smoke","kind":"smoke","summary":"passed","artifact_refs":["worklog.md#checkpoint-smoke"]}',
            state_root=state_root,
        )
        checkpoint_id = checkpoint["checkpoint"]["checkpoint_id"]
        workflow = run("status", "--workflow-id", "goal-checkpoint", state_root=state_root)["workflow"]
        rolling = workflow["continuation_plan"]["rolling_state"]
        assert_true(rolling["current_resume_cursor"] == "slice-2", "checkpoint should advance cursor")
        assert_true(rolling["last_checkpoint_id"] == checkpoint_id, "last checkpoint id mismatch")
        assert_true(checkpoint_id in workflow["checkpoint_index"], "checkpoint index missing")
        assert_true(workflow["next_safe_action"]["cursor"] == "slice-2", "next safe action mismatch")
        workflow_path = workflow_dir / "workflow.json"
        valid_workflow = read_json(workflow_path)
        corrupt_next_action = dict(valid_workflow)
        corrupt_next_action["next_safe_action"] = next_action("run_slice", "slice-99")
        write_json(workflow_path, corrupt_next_action)
        invalid_next_action_validate = run_fail("validate", "--workflow-id", "goal-checkpoint", state_root=state_root)
        assert_true("next_safe_action cursor" in invalid_next_action_validate["error"], "validate should reject next_action cursor mismatch")
        corrupt_missing_next_action = dict(valid_workflow)
        corrupt_missing_next_action["next_safe_action"] = {}
        write_json(workflow_path, corrupt_missing_next_action)
        missing_next_action_validate = run_fail("validate", "--workflow-id", "goal-checkpoint", state_root=state_root)
        assert_true("next_safe_action" in missing_next_action_validate["error"], "validate should reject missing next_action cursor")
        write_json(workflow_path, valid_workflow)
        corrupt_evidence = read_json(workflow_path)
        corrupt_evidence.setdefault("verification", {}).setdefault("evidence", []).append(
            {
                "evidence_key": "corrupt-evidence",
                "kind": "smoke",
                "summary": "corrupt evidence",
                "artifact_refs": ["missing-artifact"],
            }
        )
        write_json(workflow_path, corrupt_evidence)
        corrupt_evidence_validate = run_fail("validate", "--workflow-id", "goal-checkpoint", state_root=state_root)
        assert_true("artifact_ref is not registered" in corrupt_evidence_validate["error"], "validate should reject persisted dangling evidence refs")
        write_json(workflow_path, valid_workflow)

        stale_cursor = run_fail(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "stale",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"passed"}',
            "--evidence",
            '{"evidence_key":"stale","kind":"smoke","summary":"stale","artifact_refs":["worklog.md#checkpoint-smoke"]}',
            state_root=state_root,
        )
        assert_true("does not match current cursor" in stale_cursor["error"], "stale cursor should fail")

        direct_event = run_fail(
            "event",
            "--workflow-id",
            "goal-checkpoint",
            "--type",
            "progress",
            "--event-id",
            "evt-direct-mutate",
            "--payload",
            '{"status":"blocked"}',
            state_root=state_root,
        )
        assert_true("must use checkpoint" in direct_event["error"], "mutating event should fail")

        direct_terminal_event = run_fail(
            "event",
            "--workflow-id",
            "goal-checkpoint",
            "--type",
            "complete",
            "--event-id",
            "evt-direct-complete",
            state_root=state_root,
        )
        assert_true("checkpoint-owned event types" in direct_terminal_event["error"], "direct terminal event should fail")

        direct_lease_event = run_fail(
            "event",
            "--workflow-id",
            "goal-checkpoint",
            "--type",
            "progress",
            "--event-id",
            "evt-direct-lease",
            "--payload",
            '{"active_recovery_lease":{"cursor":"slice-2"}}',
            state_root=state_root,
        )
        assert_true("must use checkpoint" in direct_lease_event["error"], "lease-shaped event should fail")

        missing_workflow_event = run_fail(
            "event",
            "--workflow-id",
            "ghost-workflow",
            "--type",
            "progress",
            "--event-id",
            "evt-ghost",
            state_root=state_root,
        )
        assert_true("workflow not found" in missing_workflow_event["error"], "event for missing workflow should fail")
        assert_true(not (state_root / "workflows" / "ghost-workflow").exists(), "missing workflow event should not create files")

        unsupported_manual_event = run_fail(
            "event",
            "--workflow-id",
            "goal-checkpoint",
            "--type",
            "wait",
            "--event-id",
            "evt-wait-future",
            state_root=state_root,
        )
        assert_true("not currently supported" in unsupported_manual_event["error"], "manual event should reject future event types without validators")

        pending_event_path = workflow_dir / ".pending-chk-manual.json"
        write_json(pending_event_path, {"schema_version": 1, "checkpoint_id": "chk-manual"})
        pending_event = run_fail(
            "event",
            "--workflow-id",
            "goal-checkpoint",
            "--type",
            "owner_decision",
            "--event-id",
            "evt-pending-owner",
            "--payload",
            acceptance_payload("demo"),
            state_root=state_root,
        )
        assert_true("pending checkpoint transaction" in pending_event["error"], "generic event should fail while checkpoint is pending")
        pending_event_path.unlink()

        bad_plan = run_fail(
            "event",
            "--workflow-id",
            "goal-checkpoint",
            "--type",
            "plan_accepted",
            "--event-id",
            "evt-plan-bad",
            "--payload",
            '{"objective":"demo"}',
            state_root=state_root,
        )
        assert_true("plan_accepted payload missing" in bad_plan["error"], "plan_accepted payload should validate")

        good_plan = run(
            "event",
            "--workflow-id",
            "goal-checkpoint",
            "--type",
            "plan_accepted",
            "--event-id",
            "evt-plan-good",
            "--payload",
            acceptance_payload("demo"),
            state_root=state_root,
        )
        assert_true(good_plan["ok"], "plan_accepted good payload should pass")
        bad_owner_decision = run_fail(
            "event",
            "--workflow-id",
            "goal-checkpoint",
            "--type",
            "owner_decision",
            "--event-id",
            "evt-owner-bad",
            state_root=state_root,
        )
        assert_true("owner_decision requires a payload" in bad_owner_decision["error"], "owner_decision payload should validate")
        bad_owner_artifact = run_fail(
            "event",
            "--workflow-id",
            "goal-checkpoint",
            "--type",
            "owner_decision",
            "--event-id",
            "evt-owner-missing-artifact",
            "--payload",
            '{"objective":"demo","non_goals":[],"success_criteria":[],"assumptions":[],"approval_boundaries":[],"source_ref":"telegram:19982","accepted_at":"2026-05-24T00:00:00Z"}',
            state_root=state_root,
        )
        assert_true("plan_artifact" in bad_owner_artifact["error"], "owner_decision should require plan artifact identity")

        run("start", "--kind", "goal", "--text", "Blocked workflow", "--workflow-id", "blocked-unlock", state_root=state_root)
        install_two_step_plan(state_root, "blocked-unlock")
        run(
            "checkpoint",
            "--workflow-id",
            "blocked-unlock",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "block workflow",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"blocked","phase_after":"blocked","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"blocked"}',
            "--next-action",
            json.dumps(next_action("owner_decision_or_rescope", "slice-1", requires_approval=True, policy="reconcile_first")),
            state_root=state_root,
        )
        blocked_without_decision = run_fail(
            "checkpoint",
            "--workflow-id",
            "blocked-unlock",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "resume without decision",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true("owner_decision or rescope" in blocked_without_decision["error"], "blocked resume should require owner_decision")
        unrelated_owner_decision = run(
            "event",
            "--workflow-id",
            "blocked-unlock",
            "--type",
            "owner_decision",
            "--event-id",
            "evt-owner-unrelated",
            "--payload",
            acceptance_payload("Other workflow"),
            state_root=state_root,
        )
        assert_true(unrelated_owner_decision["ok"], "unrelated owner_decision should still be recorded as an event")
        unrelated_unblock = run_fail(
            "checkpoint",
            "--workflow-id",
            "blocked-unlock",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "resume after unrelated decision",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true("matching owner_decision or rescope" in unrelated_unblock["error"], "unrelated owner_decision should not unblock")
        good_owner_decision = run(
            "event",
            "--workflow-id",
            "blocked-unlock",
            "--type",
            "owner_decision",
            "--event-id",
            "evt-owner-good",
            "--payload",
            acceptance_payload("Blocked workflow"),
            state_root=state_root,
        )
        assert_true(good_owner_decision["ok"], "valid owner_decision should be recorded")
        unblocked = run(
            "checkpoint",
            "--workflow-id",
            "blocked-unlock",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "resume after decision",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true(unblocked["ok"], "blocked workflow should resume after owner_decision")
        run("start", "--kind", "goal", "--text", "Accepted timestamp does not gate unblock", "--workflow-id", "accepted-at-not-gate", state_root=state_root)
        install_two_step_plan(state_root, "accepted-at-not-gate")
        run(
            "checkpoint",
            "--workflow-id",
            "accepted-at-not-gate",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "block before owner decision",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"blocked","phase_after":"blocked","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"blocked"}',
            "--next-action",
            json.dumps(next_action("owner_decision_or_rescope", "slice-1", requires_approval=True, policy="reconcile_first")),
            state_root=state_root,
        )
        old_accepted_at_event = run(
            "event",
            "--workflow-id",
            "accepted-at-not-gate",
            "--type",
            "owner_decision",
            "--event-id",
            "evt-old-accepted-at-after-block",
            "--payload",
            acceptance_payload("Accepted timestamp does not gate unblock", accepted_at="2000-01-01T00:00:00Z"),
            state_root=state_root,
        )
        assert_true(old_accepted_at_event["ok"], "owner_decision with old accepted_at should be recorded")
        old_accepted_at_unlock = run(
            "checkpoint",
            "--workflow-id",
            "accepted-at-not-gate",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "resume after old accepted_at decision",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true(old_accepted_at_unlock["ok"], "event order, not accepted_at, should unlock blocked workflow")

        run("start", "--kind", "goal", "--text", "Original scope", "--workflow-id", "changed-rescope", state_root=state_root)
        install_two_step_plan(state_root, "changed-rescope")
        run(
            "checkpoint",
            "--workflow-id",
            "changed-rescope",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "block before changed rescope",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"blocked","phase_after":"blocked","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"blocked"}',
            "--next-action",
            json.dumps(next_action("owner_decision_or_rescope", "slice-1", requires_approval=True, policy="reconcile_first")),
            state_root=state_root,
        )
        changed_rescope = run(
            "event",
            "--workflow-id",
            "changed-rescope",
            "--type",
            "rescope",
            "--event-id",
            "evt-changed-rescope",
            "--payload",
            acceptance_payload(
                "New scope",
                success_criteria=["new target"],
                plan_artifact_ref="artifact://rescope",
                plan_artifact_hash="sha256-rescope",
            ),
            state_root=state_root,
        )
        assert_true(changed_rescope["ok"], "changed rescope should be recorded")
        changed_rescope_unlock = run(
            "checkpoint",
            "--workflow-id",
            "changed-rescope",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "resume after changed rescope",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true(changed_rescope_unlock["ok"], "changed-scope rescope should unlock blocked workflow")
        run(
            "checkpoint",
            "--workflow-id",
            "blocked-unlock",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "block again",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"blocked","phase_after":"blocked","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"blocked"}',
            "--next-action",
            json.dumps(next_action("owner_decision_or_rescope", "slice-1", requires_approval=True, policy="reconcile_first")),
            state_root=state_root,
        )
        stale_owner_decision = run_fail(
            "checkpoint",
            "--workflow-id",
            "blocked-unlock",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "resume after stale decision",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true("owner_decision or rescope" in stale_owner_decision["error"], "stale owner_decision should not unlock a later block")

        run("start", "--kind", "plan", "--text", "Plan terminal action", "--workflow-id", "plan-terminal-action", state_root=state_root)
        plan_bad_terminal_action = run_fail(
            "checkpoint",
            "--workflow-id",
            "plan-terminal-action",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "plan terminal wrong next action",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "completed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "start",
                    "cursor_after": "start",
                    "event_type": "complete",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "terminal_evidence": {
                        "evidence_key": "plan-terminal",
                        "kind": "smoke",
                        "summary": "plan terminal",
                        "artifact_refs": ["worklog.md#checkpoint-smoke"],
                    },
                    "final_status": {"result": "pass", "residuals": {}},
                }
            ),
            "--next-action",
            json.dumps(next_action("continue", "start")),
            state_root=state_root,
        )
        assert_true("report_terminal_status" in plan_bad_terminal_action["error"], "plan terminal should require terminal next action")
        plan_bad_cursor = run_fail(
            "checkpoint",
            "--workflow-id",
            "plan-terminal-action",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "plan terminal wrong cursor",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "completed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "start",
                    "cursor_after": "start",
                    "event_type": "complete",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "terminal_evidence": {
                        "evidence_key": "plan-terminal-cursor",
                        "kind": "smoke",
                        "summary": "plan terminal cursor",
                        "artifact_refs": ["worklog.md#checkpoint-smoke"],
                    },
                    "final_status": {"result": "pass", "residuals": {}},
                }
            ),
            "--next-action",
            json.dumps(next_action("report_terminal_status", "wrong")),
            state_root=state_root,
        )
        assert_true("next_action.cursor" in plan_bad_cursor["error"], "plan/verify next_action cursor should match cursor_after")

        run("start", "--kind", "conv", "--text", "Empty terminal mode update guard", "--workflow-id", "empty-terminal-mode-update", state_root=state_root)
        empty_terminal_path = state_root / "workflows" / "empty-terminal-mode-update" / "workflow.json"
        empty_terminal_before = read_json(empty_terminal_path)
        empty_terminal_events = (state_root / "workflows" / "empty-terminal-mode-update" / "events.jsonl").read_text(encoding="utf-8")
        empty_terminal = run_fail(
            "checkpoint",
            "--workflow-id",
            "empty-terminal-mode-update",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "empty terminal mode update should not persist invalid state",
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
                    "mode_state_update": {},
                    "terminal_evidence": {
                        "evidence_key": "empty-terminal-mode-update",
                        "kind": "smoke",
                        "summary": "empty terminal mode update should fail before mutation",
                        "artifact_refs": ["worklog.md#checkpoint-smoke"],
                    },
                    "final_status": {"result": "pass", "residuals": {}},
                }
            ),
            state_root=state_root,
        )
        assert_true("requires populated conv_state" in empty_terminal["error"], "empty terminal mode update should fail before mutation")
        assert_true(read_json(empty_terminal_path) == empty_terminal_before, "empty terminal mode update must not mutate workflow")
        assert_true(
            (state_root / "workflows" / "empty-terminal-mode-update" / "events.jsonl").read_text(encoding="utf-8") == empty_terminal_events,
            "empty terminal mode update must not append events",
        )

        for kind, workflow_id, expected_error in (
            ("plan", "malformed-plan-terminal-mode-update", "plan_state is missing required fields"),
            ("verify", "malformed-verify-terminal-mode-update", "verify_state is missing required fields"),
        ):
            run("start", "--kind", kind, "--text", f"Malformed {kind} terminal state guard", "--workflow-id", workflow_id, state_root=state_root)
            malformed_path = state_root / "workflows" / workflow_id / "workflow.json"
            malformed_before = read_json(malformed_path)
            malformed_events = (state_root / "workflows" / workflow_id / "events.jsonl").read_text(encoding="utf-8")
            malformed = run_fail(
                "checkpoint",
                "--workflow-id",
                workflow_id,
                "--checkpoint-type",
                "terminal",
                "--summary",
                f"malformed {kind} terminal mode update should fail before mutation",
                "--state-update",
                json.dumps(
                    {
                        "checkpoint_type": "terminal",
                        "status_after": "completed_unreported",
                        "phase_after": "terminal",
                        "cursor_before": "start",
                        "cursor_after": "start",
                        "event_type": "complete",
                        "worklog_block_kind": "terminal_summary",
                        "step_result": "terminal",
                        "residuals": {},
                        "mode_state_update": {"objective": ""},
                        "terminal_evidence": {
                            "evidence_key": f"malformed-{kind}-terminal-mode-update",
                            "kind": "smoke",
                            "summary": f"malformed {kind} terminal mode update should fail before mutation",
                            "artifact_refs": ["worklog.md#checkpoint-smoke"],
                        },
                        "final_status": {"result": "pass", "residuals": {}},
                    }
                ),
                state_root=state_root,
            )
            assert_true(expected_error in malformed["error"], f"malformed {kind} terminal mode update should fail before mutation")
            assert_true(read_json(malformed_path) == malformed_before, f"malformed {kind} terminal mode update must not mutate workflow")
            assert_true(
                (state_root / "workflows" / workflow_id / "events.jsonl").read_text(encoding="utf-8") == malformed_events,
                f"malformed {kind} terminal mode update must not append events",
            )

        plan_terminal = run(
            "checkpoint",
            "--workflow-id",
            "plan-terminal-action",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "plan terminal good",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "completed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "start",
                    "cursor_after": "start",
                    "event_type": "complete",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "mode_state_update": {
                        "final_plan_artifact_id": "plan-terminal-good",
                        "final_plan_artifact_path": "artifacts/plan-terminal-good.md",
                        "objective": "Validate terminal plan next_action contracts",
                        "intake_questions": [],
                        "answered_decisions": [],
                        "deferred_decisions": [],
                        "assumptions": [],
                        "approval_boundaries": [],
                        "success_criteria": ["terminal next_action cursor remains bound to cursor_after"],
                        "risks": [],
                        "first_slices": [],
                        "next_action": "report terminal status",
                        "unresolved_questions": [],
                        "promotion_recommendation": "do not promote",
                        "promoted_to_goal": False,
                    },
                    "terminal_evidence": {
                        "evidence_key": "plan-terminal-good",
                        "kind": "smoke",
                        "summary": "plan terminal good",
                        "artifact_refs": ["worklog.md#checkpoint-smoke"],
                    },
                    "final_status": {"result": "pass", "residuals": {}},
                }
            ),
            state_root=state_root,
        )
        assert_true(plan_terminal["ok"], "plan terminal should pass with correct structured next_action")
        plan_workflow_path = state_root / "workflows" / "plan-terminal-action" / "workflow.json"
        plan_workflow = read_json(plan_workflow_path)
        plan_workflow["next_safe_action"]["cursor"] = "wrong"
        write_json(plan_workflow_path, plan_workflow)
        plan_bad_validate_cursor = run_fail("validate", "--workflow-id", "plan-terminal-action", state_root=state_root)
        assert_true("next_safe_action cursor" in plan_bad_validate_cursor["error"], "plan validate should reject stale next_safe_action cursor")

        run("start", "--kind", "goal", "--text", "Draft regression", "--workflow-id", "draft-regression", state_root=state_root)
        install_two_step_plan(state_root, "draft-regression")
        draft_regression = run_fail(
            "checkpoint",
            "--workflow-id",
            "draft-regression",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "bad draft regression",
            "--state-update",
            '{"checkpoint_type":"checkpoint","status_after":"draft","phase_after":"draft","cursor_before":"slice-1","cursor_after":"slice-1","event_type":"checkpoint","worklog_block_kind":"slice_summary","step_result":"waiting"}',
            state_root=state_root,
        )
        assert_true("cannot return to draft" in draft_regression["error"], "active workflow should not return to draft")

        run("start", "--kind", "goal", "--text", "Concurrent checkpoint", "--workflow-id", "concurrent-checkpoint", state_root=state_root)
        install_two_step_plan(state_root, "concurrent-checkpoint")
        concurrent_dir = state_root / "workflows" / "concurrent-checkpoint"
        concurrent_args = (
            "checkpoint",
            "--workflow-id",
            "concurrent-checkpoint",
            "--checkpoint-type",
            "advance",
            "--summary",
            "concurrent advance",
            "--state-update",
            '{"checkpoint_type":"advance","status_after":"running","phase_after":"slice","cursor_before":"slice-1","cursor_after":"slice-2","event_type":"advance","worklog_block_kind":"slice_summary","step_result":"passed"}',
            "--next-action",
            json.dumps(next_action("run_slice", "slice-2")),
            "--evidence",
            '{"evidence_key":"concurrent","kind":"smoke","summary":"concurrent","artifact_refs":["worklog.md#checkpoint-smoke"]}',
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_results = list(pool.map(lambda _: run_raw(*concurrent_args, state_root=state_root), range(2)))
        success_count = sum(result.returncode == 0 for result in concurrent_results)
        failure_text = "\n".join(result.stdout + result.stderr for result in concurrent_results if result.returncode != 0)
        assert_true(success_count == 1, "exactly one concurrent checkpoint should succeed")
        assert_true("does not match current cursor" in failure_text, "losing concurrent checkpoint should fail on stale cursor")
        assert_true(event_count(concurrent_dir) == 2, "concurrent checkpoint should append one checkpoint event")
        assert_true(worklog_checkpoint_count(concurrent_dir) == 1, "concurrent checkpoint should append one worklog block")

        duplicate_event = run_fail(
            "event",
            "--workflow-id",
            "goal-checkpoint",
            "--type",
            "progress",
            "--event-id",
            "evt-plan-good",
            "--note",
            "duplicate",
            state_root=state_root,
        )
        assert_true("duplicate event_id" in duplicate_event["error"], "duplicate event_id should fail")

        round_one = run(
            "append-round",
            "--workflow-id",
            "goal-checkpoint",
            "--round",
            "1",
            "--summary",
            "round one",
            state_root=state_root,
        )
        round_one_repeat = run(
            "append-round",
            "--workflow-id",
            "goal-checkpoint",
            "--round",
            "1",
            "--summary",
            "round one repeat",
            state_root=state_root,
        )
        assert_true(round_one["event_id"] != round_one_repeat["event_id"], "append-round event ids should be unique")

        context_file.write_text("fresh-again\n", encoding="utf-8")
        run(
            "checkpoint",
            "--workflow-id",
            "goal-checkpoint",
            "--checkpoint-type",
            "checkpoint",
            "--summary",
            "capture validation context",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "checkpoint",
                    "status_after": "running",
                    "phase_after": "slice",
                    "cursor_before": "slice-2",
                    "cursor_after": "slice-2",
                    "event_type": "checkpoint",
                    "worklog_block_kind": "slice_summary",
                    "step_result": "waiting",
                    "context_manifest_updates": [{"path": str(context_file), "action": "capture"}],
                }
            ),
            state_root=state_root,
        )
        context_file.write_text("stale-for-validate\n", encoding="utf-8")
        stale_validate = run_fail("validate", "--workflow-id", "goal-checkpoint", state_root=state_root)
        assert_true("context manifest is stale" in stale_validate["error"], "validate should reject stale context")

        write_json(workflow_path, valid_workflow)
        corrupt_index = read_json(workflow_path)
        corrupt_index["checkpoint_index"][checkpoint_id]["cursor_after"] = "slice-99"
        write_json(workflow_path, corrupt_index)
        corrupt_index_validate = run_fail("validate", "--workflow-id", "goal-checkpoint", state_root=state_root)
        assert_true(bool(corrupt_index_validate["error"]), "validate should reject checkpoint index cursor mismatch")
        write_json(workflow_path, valid_workflow)

        run("start", "--kind", "goal", "--text", "Waiting terminal", "--workflow-id", "waiting-terminal", state_root=state_root)
        install_two_step_plan(state_root, "waiting-terminal")
        waiting_path = state_root / "workflows" / "waiting-terminal" / "workflow.json"
        waiting_workflow = read_json(waiting_path)
        waiting_workflow["status"] = "waiting_user"
        write_json(waiting_path, waiting_workflow)
        waiting_terminal = run_fail(
            "checkpoint",
            "--workflow-id",
            "waiting-terminal",
            "--checkpoint-type",
            "terminal",
            "--summary",
            "waiting complete",
            "--state-update",
            json.dumps(
                {
                    "checkpoint_type": "terminal",
                    "status_after": "completed_unreported",
                    "phase_after": "terminal",
                    "cursor_before": "slice-1",
                    "cursor_after": "slice-1",
                    "event_type": "complete",
                    "worklog_block_kind": "terminal_summary",
                    "step_result": "terminal",
                    "terminal_evidence": {
                        "evidence_key": "waiting-terminal",
                        "kind": "smoke",
                        "summary": "waiting terminal",
                        "artifact_refs": ["worklog.md#checkpoint-smoke"],
                    },
                    "final_status": {"result": "pass", "residuals": {}},
                }
            ),
            state_root=state_root,
        )
        assert_true("waiting_user workflows" in waiting_terminal["error"], "waiting_user should not complete directly")

        run("start", "--kind", "goal", "--text", "Corrupt state", "--workflow-id", "corrupt-state", state_root=state_root)
        install_two_step_plan(state_root, "corrupt-state")
        corrupt_dir = state_root / "workflows" / "corrupt-state"
        corrupt_workflow_path = corrupt_dir / "workflow.json"
        corrupt_workflow = read_json(corrupt_workflow_path)
        corrupt_workflow["context_manifest"] = [{"kind": "file", "ref": "/missing/no-hash"}]
        write_json(corrupt_workflow_path, corrupt_workflow)
        malformed_context = run_fail("validate", "--workflow-id", "corrupt-state", state_root=state_root)
        assert_true("context manifest is stale" in malformed_context["error"], "malformed context manifest should fail validation")

        corrupt_workflow["context_manifest"] = []
        corrupt_workflow["active_recovery_lease"] = {
            "lease_id": "lease-wrong-type",
            "lease_type": "delivery",
            "cursor": "slice-1",
            "holder": "smoke",
            "acquired_at": "2026-05-24T00:00:00Z",
            "lease_expires_at": "2099-05-24T00:30:00Z",
            "checkpoint_id": "chk-before",
        }
        write_json(corrupt_workflow_path, corrupt_workflow)
        wrong_lease_type = run_fail("validate", "--workflow-id", "corrupt-state", state_root=state_root)
        assert_true("lease_type" in wrong_lease_type["error"], "recovery lease should require recovery type")
        corrupt_workflow["active_recovery_lease"] = None
        write_json(corrupt_workflow_path, corrupt_workflow)
        with (corrupt_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "event_id": "evt-orphan-checkpoint",
                        "workflow_id": "corrupt-state",
                        "event_type": "advance",
                        "created_at": "2026-05-24T00:00:00Z",
                        "checkpoint_id": "chk-orphan",
                        "status_after": "running",
                        "phase_after": "slice",
                        "cursor_before": "slice-1",
                        "cursor_after": "slice-2",
                        "payload": {},
                    },
                    sort_keys=True,
                )
                + "\n"
            )
        orphan_checkpoint = run_fail("validate", "--workflow-id", "corrupt-state", state_root=state_root)
        assert_true("missing from checkpoint_index" in orphan_checkpoint["error"], "orphan checkpoint event should fail validation")

        corrupt_events = (corrupt_dir / "events.jsonl").read_text(encoding="utf-8")
        with (corrupt_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
            start_event = json.loads(corrupt_events.splitlines()[0])
            fh.write(json.dumps(start_event, sort_keys=True) + "\n")
        duplicate_validate = run_fail("validate", "--workflow-id", "corrupt-state", state_root=state_root)
        assert_true("duplicate event_id" in duplicate_validate["error"], "validate should reject duplicate event ids")

    print(
        json.dumps(
            {
                "ok": True,
                "checked": [
                    "mismatch reject",
                    "terminal event reject",
                    "terminal matrix reject",
                    "non-terminal matrix reject",
                    "missing continuation step reject",
                    "terminal sentinel active advance reject",
                    "failed checkpoint no partial append",
                    "terminal final formatting",
                    "terminal mode state prevalidation",
                    "terminal verdict validation",
                    "terminal verdict consistency",
                    "terminal residual consistency",
                    "evidence required",
                    "recovery lease ownership guard",
                    "same-cursor lease clear",
                    "context manifest stale block",
                    "context manifest stale remove",
                    "pending checkpoint validate block",
                    "reported status guard",
                    "abandoned status guard",
                    "same-cursor passed reject",
                    "next_action cursor guard",
                    "atomic checkpoint",
                    "next_action validate guard",
                    "missing next_action validate guard",
                    "stale cursor reject",
                    "event mutation reject",
                    "direct terminal event reject",
                    "missing workflow event reject",
                    "plan_accepted payload validation",
                    "owner_decision payload validation",
                    "blocked owner decision unlock guard",
                    "accepted_at not used as unblock freshness gate",
                    "stale owner decision guard",
                    "plan terminal next_action guard",
                    "plan next_safe_action validate guard",
                    "active draft regression guard",
                    "concurrent checkpoint writer guard",
                    "terminal evidence and failure reason",
                    "event id uniqueness",
                    "context manifest shape validation",
                    "lease type validation",
                    "orphan checkpoint event validation",
                    "validate stale context reject",
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
