#!/usr/bin/env python3
"""Smoke coverage for Slice 3 shared mode handler primitives."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from converge.modes import ModeHandler, ModeOutcome  # noqa: E402
from converge.messages import format_final  # noqa: E402
from converge.store import WorkflowStore  # noqa: E402


class GoalHandler(ModeHandler):
    kind = "goal"


class VerifyHandler(ModeHandler):
    kind = "verify"


class TerminalRaceStore(WorkflowStore):
    def __init__(self, root: Path, workflow_id: str):
        super().__init__(root)
        self.workflow_id = workflow_id
        self.loads = 0

    def load_workflow(self, workflow_id: str) -> dict[str, Any]:
        self.loads += 1
        if workflow_id == self.workflow_id and self.loads == 2:
            workflow_path = self.workflow_dir(workflow_id) / "workflow.json"
            workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
            workflow["status"] = "completed_unreported"
            workflow_path.write_text(json.dumps(workflow, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return super().load_workflow(workflow_id)


def assert_true(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def event_count(workflow_dir: Path) -> int:
    return len((workflow_dir / "events.jsonl").read_text(encoding="utf-8").splitlines())


def seed_worklog_anchor(store: WorkflowStore, workflow_id: str, heading: str = "Mode Base Smoke") -> None:
    worklog_path = store.workflow_dir(workflow_id) / "worklog.md"
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


def install_two_step_plan(store: WorkflowStore, workflow_id: str) -> None:
    # Fixture-only continuation plan used to exercise advance gates after C0
    # removed the production Slice 1-9 default plan.
    workflow = store.load_workflow(workflow_id)
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
    store.save_workflow(workflow)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="converge-mode-base-smoke-") as tmp:
        state_root = Path(tmp)
        store = WorkflowStore(state_root)
        store.create_workflow(kind="goal", text="Implement Slice 3", workflow_id="goal-mode-base")
        seed_worklog_anchor(store, "goal-mode-base")
        install_two_step_plan(store, "goal-mode-base")
        workflow_dir = state_root / "workflows" / "goal-mode-base"
        handler = GoalHandler(store)

        artifact_path = state_root / "mode-artifact.txt"
        artifact_path.write_text("mode artifact evidence\n", encoding="utf-8")
        artifact = handler.record_artifact(
            "goal-mode-base",
            kind="evidence",
            path=artifact_path,
            note="mode artifact fixture",
        )
        assert_true(artifact["artifact"]["kind"] == "evidence", "mode handler should record artifacts")
        artifact_events = [
            json.loads(line)
            for line in (workflow_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("event_type") == "artifact"
        ]
        assert_true(
            artifact_events and artifact_events[-1]["payload"]["artifact"]["artifact_id"] == artifact["artifact"]["artifact_id"],
            "mode handler artifact event should match workflow artifact",
        )

        waiting = handler.record_outcome(
            "goal-mode-base",
            ModeOutcome(
                summary="mode base waiting checkpoint",
                status_after="running",
                phase_after="slice",
                step_result="waiting",
                residuals={"deferred_scope": ["plan mode behavior remains C1 / Slice 5 scope"]},
            ),
        )
        assert_true("checkpoint_id" in waiting, "waiting outcome should create checkpoint")
        after_wait = store.load_workflow("goal-mode-base")
        assert_true(after_wait["continuation_plan"]["rolling_state"]["current_resume_cursor"] == "slice-1", "waiting must not advance cursor")
        assert_true(after_wait["checkpoint_index"], "checkpoint index should be populated")

        before_bad = event_count(workflow_dir)
        try:
            handler.record_outcome(
                "goal-mode-base",
                ModeOutcome(
                    summary="bad residual bucket",
                    status_after="running",
                    phase_after="slice",
                    residuals={"unknown_bucket": ["bad"]},
                ),
            )
            raise AssertionError("unknown residual bucket unexpectedly passed")
        except Exception as exc:
            assert_true("unknown residual buckets" in str(exc), "unknown residual bucket should fail")
        assert_true(event_count(workflow_dir) == before_bad, "failed mode outcome must not append events")

        try:
            VerifyHandler(store).current_cursor("goal-mode-base")
            raise AssertionError("wrong handler kind unexpectedly passed")
        except ValueError as exc:
            assert_true("cannot operate" in str(exc), "wrong handler kind should fail")

        advanced = handler.record_outcome(
            "goal-mode-base",
            ModeOutcome(
                summary="slice 1 passed through mode base",
                checkpoint_type="advance",
                event_type="advance",
                status_after="running",
                phase_after="slice",
                cursor_after="slice-2",
                step_result="passed",
                next_action=next_action("run_slice", "slice-2"),
                evidence={
                    "evidence_key": "mode-base-smoke",
                    "kind": "smoke",
                    "summary": "mode base checkpoint bridge passed",
                    "artifact_refs": ["worklog.md#mode-base-smoke"],
                },
            ),
        )
        assert_true("checkpoint_id" in advanced, "advance outcome should create checkpoint")
        after_advance = store.load_workflow("goal-mode-base")
        rolling = after_advance["continuation_plan"]["rolling_state"]
        assert_true(rolling["current_resume_cursor"] == "slice-2", "advance must move cursor")
        assert_true(rolling["completed_steps"] == ["slice-1"], "advance must mark completed step")
        assert_true(after_advance["next_safe_action"]["cursor"] == "slice-2", "next safe action should be retained")

        try:
            ModeOutcome(
                summary="bad terminal",
                checkpoint_type="terminal",
                event_type="checkpoint",
                status_after="completed_unreported",
                phase_after="terminal",
                step_result="terminal",
            )
            raise AssertionError("bad terminal outcome unexpectedly passed")
        except ValueError as exc:
            assert_true("terminal mode outcomes" in str(exc), "terminal event contract should be enforced")

        try:
            ModeOutcome(
                summary="bad terminal evidence",
                checkpoint_type="terminal",
                event_type="complete",
                status_after="completed_unreported",
                phase_after="terminal",
                step_result="waiting",
                worklog_block_kind="terminal_summary",
                final_status={"result": "pass"},
            )
            raise AssertionError("terminal complete without terminal step unexpectedly passed")
        except ValueError as exc:
            assert_true("terminal step_result" in str(exc), "terminal complete should require terminal step")

        try:
            ModeOutcome(
                summary="bad advance event",
                checkpoint_type="advance",
                event_type="checkpoint",
                status_after="running",
                phase_after="slice",
                cursor_after="slice-3",
                step_result="passed",
            )
            raise AssertionError("advance with checkpoint event unexpectedly passed")
        except ValueError as exc:
            assert_true("advance event_type" in str(exc), "advance should require advance event")

        try:
            handler.record_outcome(
                "goal-mode-base",
                ModeOutcome(
                    summary="bad reported status",
                    status_after="reported",
                    phase_after="slice",
                ),
            )
            raise AssertionError("reported status through mode outcome unexpectedly passed")
        except ValueError as exc:
            assert_true("reported or abandoned" in str(exc), "reported status should be reserved for report proof")

        try:
            handler.record_outcome(
                "goal-mode-base",
                ModeOutcome(
                    summary="bad abandoned status",
                    status_after="abandoned",
                    phase_after="slice",
                ),
            )
            raise AssertionError("abandoned status through mode outcome unexpectedly passed")
        except ValueError as exc:
            assert_true("reported or abandoned" in str(exc), "abandoned status should be reserved for abandon flow")

        try:
            handler.record_outcome(
                "goal-mode-base",
                ModeOutcome(
                    summary="same cursor passed",
                    status_after="running",
                    phase_after="slice",
                    step_result="passed",
                    evidence={
                        "evidence_key": "same-cursor-passed",
                        "kind": "smoke",
                        "summary": "same cursor passed",
                        "artifact_refs": ["worklog.md#mode-base-smoke"],
                    },
                ),
            )
            raise AssertionError("same-cursor passed outcome unexpectedly passed")
        except ValueError as exc:
            assert_true("passed continuation steps" in str(exc), "same-cursor passed should fail")

        store.create_workflow(kind="goal", text="Next action mismatch", workflow_id="goal-mode-next-action")
        seed_worklog_anchor(store, "goal-mode-next-action")
        install_two_step_plan(store, "goal-mode-next-action")
        next_action_handler = GoalHandler(store)
        try:
            next_action_handler.record_outcome(
                "goal-mode-next-action",
                ModeOutcome(
                    summary="bad next action",
                    checkpoint_type="advance",
                    event_type="advance",
                    status_after="running",
                    phase_after="slice",
                    cursor_after="slice-2",
                    step_result="passed",
                    next_action=next_action("run_slice", "slice-99"),
                    evidence={
                        "evidence_key": "bad-next-action",
                        "kind": "smoke",
                        "summary": "bad next action",
                        "artifact_refs": ["worklog.md#mode-base-smoke"],
                    },
                ),
            )
            raise AssertionError("mismatched next_action unexpectedly passed")
        except ValueError as exc:
            assert_true("next_action.cursor" in str(exc), "mismatched next_action cursor should fail")

        side_effect = handler.record_outcome(
            "goal-mode-base",
            ModeOutcome(
                summary="side effect checkpoint",
                status_after="running",
                phase_after="slice",
                mode_state_update={"last_checkpoint_kind": "side_effect_checkpoint"},
                residuals={"implementation_backlog": ["idempotency policy details remain later-slice scope"]},
                side_effects=[
                    {
                        "side_effect_key": "local:write:mode-base-smoke",
                        "idempotency_policy": "never_repeat_without_approval",
                        "kind": "local_file",
                    }
                ],
            ),
        )
        assert_true("checkpoint_id" in side_effect, "side effect outcome should create checkpoint")
        after_side_effect = store.load_workflow("goal-mode-base")
        assert_true(
            after_side_effect["side_effects_performed"] == [
                {
                    "side_effect_key": "local:write:mode-base-smoke",
                    "idempotency_policy": "never_repeat_without_approval",
                    "kind": "local_file",
                }
            ],
            "mode side effects should be durable workflow state",
        )
        assert_true(
            after_side_effect["goal_state"]["last_checkpoint_kind"] == "side_effect_checkpoint",
            "mode_state_update should update only active mode state",
        )

        try:
            handler.record_outcome(
                "goal-mode-base",
                ModeOutcome(
                    summary="bad side effect",
                    status_after="running",
                    phase_after="slice",
                    side_effects=[{"kind": "local_file"}],
                ),
            )
            raise AssertionError("side effect without key/policy unexpectedly passed")
        except Exception as exc:
            assert_true("side_effect" in str(exc), "side effect should require key and policy")

        try:
            handler.record_outcome(
                "goal-mode-base",
                ModeOutcome(
                    summary="duplicate side effect",
                    status_after="running",
                    phase_after="slice",
                    side_effects=[
                        {
                            "side_effect_key": "local:write:mode-base-smoke",
                            "idempotency_policy": "never_repeat_without_approval",
                        }
                    ],
                ),
            )
            raise AssertionError("non-repeatable duplicate side effect unexpectedly passed")
        except ValueError as exc:
            assert_true("repeated side_effect_key" in str(exc), "non-repeatable duplicate should fail")

        try:
            handler.record_outcome(
                "goal-mode-base",
                ModeOutcome(
                    summary="risky side effect",
                    status_after="running",
                    phase_after="slice",
                    side_effects=[
                        {
                            "side_effect_key": "external-send:telegram:mode-base-smoke",
                            "idempotency_policy": "never_repeat_without_approval",
                        }
                    ],
                ),
            )
            raise AssertionError("risky side effect unexpectedly passed")
        except ValueError as exc:
            assert_true("risky side_effect_key" in str(exc), "risky side effect prefixes should fail before approval contract")

        try:
            handler.record_outcome(
                "goal-mode-base",
                ModeOutcome(
                    summary="ambiguous side effect",
                    status_after="running",
                    phase_after="slice",
                    side_effects=[
                        {
                            "side_effect_key": "send-email:customer-123",
                            "idempotency_policy": "never_repeat_without_approval",
                        }
                    ],
                ),
            )
            raise AssertionError("ambiguous side effect unexpectedly passed")
        except ValueError as exc:
            assert_true("approved local/reporting prefix" in str(exc), "side effect keys should be local/reporting allowlisted")

        workflow_path = state_root / "workflows" / "goal-mode-base" / "workflow.json"
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        workflow["active_recovery_lease"] = {
            "lease_id": "lease-mode-smoke",
            "lease_type": "recovery",
            "cursor": "slice-2",
            "holder": "other-worker",
            "acquired_at": "2026-05-24T00:00:00Z",
            "lease_expires_at": "2099-05-24T00:30:00Z",
            "checkpoint_id": "chk-before",
        }
        workflow_path.write_text(json.dumps(workflow, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            handler.record_outcome(
                "goal-mode-base",
                ModeOutcome(
                    summary="lease missing",
                    status_after="running",
                    phase_after="slice",
                ),
            )
            raise AssertionError("missing recovery lease id unexpectedly passed")
        except ValueError as exc:
            assert_true("recovery_lease_id" in str(exc), "active lease should require matching lease id")

        lease_owned = handler.record_outcome(
            "goal-mode-base",
            ModeOutcome(
                summary="lease owned",
                status_after="running",
                phase_after="slice",
                recovery_lease_id="lease-mode-smoke",
                recovery_lease_holder="other-worker",
            ),
        )
        assert_true("checkpoint_id" in lease_owned, "matching lease owner should pass")
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        workflow["status"] = "blocked"
        workflow_path.write_text(json.dumps(workflow, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            handler.record_outcome(
                "goal-mode-base",
                ModeOutcome(
                    summary="blocked complete",
                    checkpoint_type="terminal",
                    event_type="complete",
                    status_after="completed_unreported",
                    phase_after="terminal",
                    step_result="terminal",
                    worklog_block_kind="terminal_summary",
                    terminal_evidence={
                        "evidence_key": "blocked-complete",
                        "kind": "smoke",
                        "summary": "blocked complete",
                        "artifact_refs": ["worklog.md#mode-base-smoke"],
                    },
                    final_status={"result": "pass", "residuals": {}},
                ),
            )
            raise AssertionError("blocked workflow completed unexpectedly")
        except ValueError as exc:
            assert_true("blocked workflows" in str(exc), "blocked workflows should require owner/rescope before completion")

        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
        workflow["status"] = "completed_unreported"
        workflow_path.write_text(json.dumps(workflow, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        try:
            handler.record_outcome(
                "goal-mode-base",
                ModeOutcome(
                    summary="terminal resume",
                    status_after="running",
                    phase_after="slice",
                ),
            )
            raise AssertionError("terminal-unreported workflow resumed unexpectedly")
        except ValueError as exc:
            assert_true("terminal status" in str(exc), "terminal-unreported workflow should not resume")

        store.create_workflow(kind="goal", text="Terminal mode", workflow_id="goal-mode-terminal")
        seed_worklog_anchor(store, "goal-mode-terminal")
        terminal_handler = GoalHandler(store)
        terminal_handler.record_outcome(
            "goal-mode-terminal",
            ModeOutcome(
                summary="terminal complete mode",
                checkpoint_type="terminal",
                event_type="complete",
                status_after="completed_unreported",
                phase_after="terminal",
                step_result="terminal",
                worklog_block_kind="terminal_summary",
                terminal_evidence={
                    "evidence_key": "terminal-mode",
                    "kind": "smoke",
                    "summary": "terminal mode complete",
                    "artifact_refs": ["worklog.md#mode-base-smoke"],
                },
                final_status={
                    "result": "pass",
                    "done": ["terminal mode complete"],
                    "checked": ["mode final_status"],
                    "residuals": {},
                },
            ),
        )
        terminal_workflow = store.load_workflow("goal-mode-terminal")
        assert_true(terminal_workflow["final_status"]["result"] == "pass", "terminal mode final_status should persist")
        assert_true("■ Goal final" in format_final(terminal_workflow), "terminal mode workflow should format final report")

        store.create_workflow(kind="goal", text="Race mode", workflow_id="goal-mode-race")
        seed_worklog_anchor(store, "goal-mode-race")
        race_dir = state_root / "workflows" / "goal-mode-race"
        race_handler = GoalHandler(TerminalRaceStore(state_root, "goal-mode-race"))
        before_race = event_count(race_dir)
        try:
            race_handler.record_outcome(
                "goal-mode-race",
                ModeOutcome(
                    summary="terminal race",
                    status_after="running",
                    phase_after="slice",
                ),
            )
            raise AssertionError("terminal race outcome unexpectedly passed")
        except ValueError as exc:
            assert_true("terminal status" in str(exc), "locked terminal race should fail")
        assert_true(event_count(race_dir) == before_race, "terminal race must not append events")

    print(
        json.dumps(
            {
                "ok": True,
                "checked": [
                    "waiting outcome checkpoint",
                    "residual validation no partial append",
                    "handler kind guard",
                    "advance outcome cursor update",
                    "terminal event contract guard",
                    "terminal complete evidence guard",
                    "advance event contract guard",
                    "reported status guard",
                    "abandoned status guard",
                    "same-cursor passed guard",
                    "next_action cursor guard",
                    "side effect persistence",
                    "side effect key and policy guard",
                    "side effect duplicate guard",
                    "recovery lease ownership guard",
                    "blocked status guard",
                    "terminal current-status guard",
                    "terminal final status formatting",
                    "locked terminal race guard",
                ],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
