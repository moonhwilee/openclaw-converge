"""Continuation plan primitives."""

from __future__ import annotations

from typing import Any


TERMINAL_CONTINUATION_TARGETS = {"blocked", "complete", "terminal", "stopped"}


def default_continuation_plan(kind: str) -> dict[str, Any] | None:
    if kind in {"plan", "verify"}:
        return None
    if kind == "goal":
        steps = [
            _default_step(
                "objective-gate",
                "Confirm the objective, non-goals, success criteria, assumptions, and approval boundaries.",
                next_on_pass="plan-acceptance-gate",
            ),
            _default_step(
                "plan-acceptance-gate",
                "Promote a durable plan artifact only after a scoped plan_accepted payload validates.",
                next_on_pass="evidence-completion-gate",
            ),
            _default_step(
                "evidence-completion-gate",
                "Check required evidence, child workflow references, and completion criteria before terminal success.",
                next_on_pass="complete",
            ),
        ]
    else:
        steps = [
            _default_step(
                "baseline",
                "Capture mode-owned continuation state after a scoped owner decision.",
                next_on_pass="complete",
            )
        ]
    return {
        "plan_id": f"{kind}-initialization-contract",
        "current_step_index": 0,
        "steps": steps,
        "budgets": {
            "max_steps_per_wake": 1,
            "max_rounds": 5,
            "max_retries_per_step": 1,
        },
        "stop_conditions": [
            "approval_boundary",
            "rescope_needed",
            "evidence_failure",
            "ambiguous_recovery",
            "stale_context",
            "retry_budget_exceeded",
            "owner_stop",
        ],
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
            "current_resume_cursor": steps[0]["step_id"],
            "last_checkpoint_id": None,
        },
    }


def _default_step(step_id: str, objective: str, *, next_on_pass: str) -> dict[str, Any]:
    return {
        "step_id": step_id,
        "objective": objective,
        "expected_artifacts": ["workflow.json", "events.jsonl", "worklog.md"],
        "gate": {"type": "manual_or_smoke", "requires_evidence": True},
        "allowed_risk_classes": ["local_files", "repo_changes"],
        "verification_commands": ["python -m converge.cli validate --sample-docs"],
        "next_on_pass": next_on_pass,
        "next_on_fail": "blocked",
    }


def current_cursor(workflow: dict[str, Any]) -> str:
    plan = workflow.get("continuation_plan")
    if not isinstance(plan, dict):
        return "start"
    rolling = plan.get("rolling_state") or {}
    return str(rolling.get("current_resume_cursor") or "baseline")


def validate_cursor_transition(workflow: dict[str, Any], before: str, after: str, step_result: str) -> None:
    cursor = current_cursor(workflow)
    if before != cursor:
        raise ValueError(f"cursor_before {before!r} does not match current cursor {cursor!r}")
    if after == before:
        return
    plan = workflow.get("continuation_plan")
    if not isinstance(plan, dict):
        raise ValueError("cannot advance cursor without continuation_plan")
    steps = plan.get("steps") or []
    current_index = int(plan.get("current_step_index", 0))
    if current_index >= len(steps):
        raise ValueError("current_step_index is out of range")
    expected = steps[current_index].get("next_on_pass") if step_result == "passed" else None
    if after != expected:
        raise ValueError(f"cursor_after {after!r} is not the current step pass target {expected!r}")
    if after in TERMINAL_CONTINUATION_TARGETS:
        raise ValueError(f"terminal continuation target {after!r} requires terminal checkpoint flow")


def apply_cursor_transition(workflow: dict[str, Any], checkpoint_id: str, after: str, step_result: str) -> None:
    plan = workflow.get("continuation_plan")
    if not isinstance(plan, dict):
        return
    rolling = plan.setdefault("rolling_state", {})
    before = rolling.get("current_resume_cursor")
    if after != before and step_result == "passed":
        completed = rolling.setdefault("completed_steps", [])
        if before not in completed:
            completed.append(before)
        plan["current_step_index"] = int(plan.get("current_step_index", 0)) + 1
    rolling["current_resume_cursor"] = after
    rolling["last_checkpoint_id"] = checkpoint_id
