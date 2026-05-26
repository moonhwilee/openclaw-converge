"""Plan mode vertical slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import ModeHandler, ModeOutcome


PLAN_ARTIFACT_ID = "plan-final"


@dataclass(frozen=True)
class PlanDraft:
    objective: str
    intake_questions: list[str]
    answered_decisions: list[str]
    deferred_decisions: list[str]
    non_goals: list[str]
    assumptions: list[str]
    approval_boundaries: list[str]
    success_criteria: list[str]
    risks: list[str]
    first_slices: list[str]
    next_action: str
    unresolved_questions: list[str]
    promotion_recommendation: str

    def as_state(self, *, artifact_id: str, artifact_path: str) -> dict[str, Any]:
        return {
            "final_plan_artifact_id": artifact_id,
            "final_plan_artifact_path": artifact_path,
            "objective": self.objective,
            "intake_questions": self.intake_questions,
            "answered_decisions": self.answered_decisions,
            "deferred_decisions": self.deferred_decisions,
            "non_goals": self.non_goals,
            "assumptions": self.assumptions,
            "approval_boundaries": self.approval_boundaries,
            "success_criteria": self.success_criteria,
            "risks": self.risks,
            "first_slices": self.first_slices,
            "next_action": self.next_action,
            "unresolved_questions": self.unresolved_questions,
            "promotion_recommendation": self.promotion_recommendation,
            "promoted_to_goal": False,
        }


def validate_plan_state(
    state: dict[str, Any],
    *,
    terminal: bool,
    final_status: dict[str, Any] | None,
) -> dict[str, list[str]]:
    if not state:
        if terminal:
            raise ValueError("terminal or artifact-backed plan workflow requires populated plan_state")
        return {}
    required = {
        "final_plan_artifact_id",
        "final_plan_artifact_path",
        "objective",
        "intake_questions",
        "answered_decisions",
        "deferred_decisions",
        "assumptions",
        "approval_boundaries",
        "success_criteria",
        "risks",
        "first_slices",
        "next_action",
        "unresolved_questions",
        "promotion_recommendation",
        "promoted_to_goal",
    }
    missing = sorted(required - set(state))
    if missing:
        raise ValueError(f"plan_state is missing required fields: {missing!r}")
    for key in (
        "intake_questions",
        "answered_decisions",
        "deferred_decisions",
        "assumptions",
        "approval_boundaries",
        "success_criteria",
        "risks",
        "first_slices",
        "unresolved_questions",
    ):
        if not isinstance(state.get(key), list):
            raise ValueError(f"plan_state {key} must be an array")
    for key in ("final_plan_artifact_id", "final_plan_artifact_path", "objective", "next_action", "promotion_recommendation"):
        if not isinstance(state.get(key), str) or not state.get(key):
            raise ValueError(f"plan_state {key} must be a non-empty string")
    if not isinstance(state.get("promoted_to_goal"), bool):
        raise ValueError("plan_state promoted_to_goal must be a boolean")
    residuals: dict[str, list[str]] = {}
    if final_status is not None:
        residuals = final_status.get("residuals") or {}
        if not isinstance(residuals, dict):
            raise ValueError("plan final_status.residuals must be an object")
    return residuals


class PlanHandler(ModeHandler):
    """Produces a final plan artifact without bypassing shared runtime paths."""

    kind = "plan"

    def finalize_plan(
        self,
        workflow_id: str,
        *,
        recovery_lease_id: str | None = None,
        recovery_lease_holder: str | None = None,
    ) -> dict[str, Any]:
        self.validate_recovery_preflight(
            workflow_id,
            recovery_lease_id=recovery_lease_id,
            recovery_lease_holder=recovery_lease_holder,
        )
        workflow = self.load_workflow(workflow_id)
        draft = build_plan_draft(workflow.get("source_request") or workflow.get("objective") or "")
        rendered_plan = render_plan_markdown(draft)
        artifact_path = (self.store.workflow_dir(workflow_id) / "artifacts" / "plan.md").expanduser().resolve()
        artifact = _existing_plan_artifact(workflow, expected_path=artifact_path, rendered_plan=rendered_plan)
        if artifact is None:
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(rendered_plan, encoding="utf-8")
            artifact = self.record_artifact(
                workflow_id,
                kind="plan",
                artifact_id=PLAN_ARTIFACT_ID,
                path=artifact_path,
                note="final plan artifact",
            )["artifact"]
        artifact_ref = artifact["artifact_id"]
        artifact_path = artifact["path"]
        evidence = {
            "evidence_key": "plan-final-artifact",
            "kind": "artifact",
            "summary": "Final executable plan artifact registered through the shared artifact path.",
            "artifact_refs": [artifact_ref],
        }
        residuals = {
            "blocking_remaining": [],
            "accepted_risks": [],
            "implementation_backlog": [],
            "deferred_scope": ["implementation requires promotion or explicit authorization"],
        }
        checkpoint = self.record_outcome(
            workflow_id,
            ModeOutcome(
                summary="Final plan artifact is ready for visible delivery.",
                status_after="completed_unreported",
                phase_after="terminal",
                checkpoint_type="terminal",
                event_type="complete",
                worklog_block_kind="terminal_summary",
                step_result="terminal",
                residuals=residuals,
                terminal_evidence=evidence,
                mode_state_update=draft.as_state(artifact_id=artifact_ref, artifact_path=artifact_path),
                recovery_lease_id=recovery_lease_id,
                recovery_lease_holder=recovery_lease_holder,
                final_status={
                    "result": "pass_with_risks",
                    "done": [
                        "Created a final plan artifact",
                        "Registered the artifact through the shared artifact path",
                        "Stopped before implementation work",
                    ],
                    "checked": [
                        "Plan mode used shared artifact and checkpoint contracts",
                        "Visible delivery remains gated by reserve-delivery/report-proof/complete-reported",
                    ],
                    "residuals": residuals,
                },
            ),
        )
        return {"workflow_id": workflow_id, "artifact": artifact, "checkpoint": checkpoint, "plan": draft.as_state(artifact_id=artifact_ref, artifact_path=artifact_path)}


def build_plan_draft(text: str) -> PlanDraft:
    objective = _compact(text) or "Clarify the requested work and produce an executable plan."
    return PlanDraft(
        objective=objective,
        intake_questions=[],
        answered_decisions=["Treat this command as planning output only until explicitly promoted."],
        deferred_decisions=["Implementation details are deferred to a promoted goal or explicit execution request."],
        non_goals=[
            "Do not perform implementation until the plan is promoted or explicitly authorized.",
            "Do not perform external, destructive, Gateway, deploy, release, or public actions from plan mode.",
        ],
        assumptions=[
            "The owner wants planning output first, not execution.",
            "Any unresolved operational decision can be deferred instead of blocking the final plan artifact.",
        ],
        approval_boundaries=[
            "External actions require explicit owner approval.",
            "Destructive local actions require explicit owner approval.",
            "Implementation requires promotion to a goal or a separate explicit instruction.",
        ],
        success_criteria=[
            "Objective, boundaries, risks, first slices, and next action are explicit.",
            "No separate report or workflow mutation path is used.",
            "The final artifact is durable and recoverable from workflow state.",
        ],
        risks=[
            "The source request may omit details that should be refined before implementation.",
            "Over-planning can delay small reversible work if the plan is not promoted promptly.",
        ],
        first_slices=[
            "Confirm or refine the plan.",
            "Promote the accepted plan to goal execution when implementation is desired.",
            "Run the smallest implementation slice with verification evidence.",
        ],
        next_action="Deliver the final plan, then wait for acceptance or promotion.",
        unresolved_questions=[],
        promotion_recommendation="promote_to_goal_after_owner_acceptance",
    )


def render_plan_markdown(plan: PlanDraft) -> str:
    sections = [
        ("Objective", [plan.objective]),
        ("Non-goals", plan.non_goals),
        ("Assumptions", plan.assumptions),
        ("Approval Boundaries", plan.approval_boundaries),
        ("Success Criteria", plan.success_criteria),
        ("Risks", plan.risks),
        ("First Slices", plan.first_slices),
        ("Next Action", [plan.next_action]),
        ("Unresolved Questions", plan.unresolved_questions or ["None"]),
    ]
    lines = ["# Plan", ""]
    for title, items in sections:
        lines.extend([f"## {title}", ""])
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    return "\n".join(lines)


def _compact(text: str) -> str:
    return " ".join(text.split())


def _existing_plan_artifact(workflow: dict[str, Any], *, expected_path: Path, rendered_plan: str) -> dict[str, Any] | None:
    matches = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == PLAN_ARTIFACT_ID
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(f"duplicate plan artifact id: {PLAN_ARTIFACT_ID}")
    artifact = matches[0]
    if artifact.get("kind") != "plan":
        raise ValueError(f"plan artifact id has wrong kind: {artifact.get('kind')!r}")
    path = artifact.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("plan artifact path is missing")
    if not Path(path).is_file():
        raise ValueError(f"plan artifact path is missing: {path}")
    if Path(path).expanduser().resolve() != expected_path:
        raise ValueError("plan artifact path is not the canonical plan output path")
    if Path(path).read_text(encoding="utf-8") != rendered_plan:
        raise ValueError("existing plan artifact does not match rendered final plan")
    return artifact
