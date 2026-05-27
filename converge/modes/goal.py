"""Goal mode vertical slice."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..acceptance import validate_acceptance_payload
from ..artifacts import now_iso
from ..messages import normalize_residuals
from .base import ModeHandler, ModeOutcome, apply_execution_truth_block, execution_blocked_final_status
from .execution_truth import classify_execution_markers


GOAL_PLAN_ARTIFACT_ID = "goal-promoted-plan"


@dataclass(frozen=True)
class GoalRecord:
    objective: str
    non_goals: list[str]
    success_criteria: list[str]
    assumptions: list[str]
    approval_boundaries: list[str]
    slice_queue: list[dict[str, Any]]
    plan_accepted: dict[str, Any]
    evidence_completion_check: dict[str, Any]
    plan_artifact_promotion: dict[str, Any]
    child_workflow_refs: list[dict[str, Any]]
    residuals: dict[str, list[str]]
    final_report_summary: str

    def as_state(self, *, artifact_id: str, artifact_path: str, artifact_hash: str) -> dict[str, Any]:
        promotion = dict(self.plan_artifact_promotion)
        promotion.update(
            {
                "plan_artifact_id": artifact_id,
                "plan_artifact_path": artifact_path,
                "plan_artifact_hash": artifact_hash,
            }
        )
        plan_accepted = dict(self.plan_accepted)
        plan_accepted.update(
            {
                "plan_artifact_ref": artifact_id,
                "plan_artifact_hash": artifact_hash,
            }
        )
        state = {
            "final_plan_artifact_id": artifact_id,
            "final_plan_artifact_path": artifact_path,
            "objective": self.objective,
            "non_goals": self.non_goals,
            "success_criteria": self.success_criteria,
            "assumptions": self.assumptions,
            "approval_boundaries": self.approval_boundaries,
            "slice_queue": self.slice_queue,
            "plan_accepted": plan_accepted,
            "evidence_completion_check": self.evidence_completion_check,
            "plan_artifact_promotion": promotion,
            "child_workflow_refs": self.child_workflow_refs,
            "residuals": self.residuals,
            "final_report_summary": self.final_report_summary,
        }
        state.update(classify_execution_markers(self.objective, capability="planned_child_refs_only"))
        return state


class GoalHandler(ModeHandler):
    """Promotes an accepted goal plan without owning delivery or child execution."""

    kind = "goal"

    def finalize_goal(
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
        existing_plan_accepted = _existing_plan_accepted_payload(self.store, workflow_id)
        record = build_goal_record(workflow, accepted_at=existing_plan_accepted.get("accepted_at") if existing_plan_accepted else None)
        artifact_path = (self.store.workflow_dir(workflow_id) / "artifacts" / "goal-plan.md").expanduser().resolve()
        artifact_ref = GOAL_PLAN_ARTIFACT_ID
        artifact_path_text = str(artifact_path)
        pre_state = record.as_state(artifact_id=artifact_ref, artifact_path=artifact_path_text, artifact_hash="pending")
        pre_state, residuals, block_reason = apply_execution_truth_block("goal", pre_state, residuals=record.residuals)
        render_record = GoalRecord(
            objective=pre_state["objective"],
            non_goals=pre_state["non_goals"],
            success_criteria=pre_state["success_criteria"],
            assumptions=pre_state["assumptions"],
            approval_boundaries=pre_state["approval_boundaries"],
            slice_queue=pre_state["slice_queue"],
            plan_accepted=pre_state["plan_accepted"],
            evidence_completion_check=pre_state["evidence_completion_check"],
            plan_artifact_promotion=pre_state["plan_artifact_promotion"],
            child_workflow_refs=pre_state["child_workflow_refs"],
            residuals=residuals,
            final_report_summary=pre_state["final_report_summary"],
        )
        rendered_plan = render_goal_plan(render_record)
        artifact = _existing_goal_artifact(workflow, expected_path=artifact_path, rendered_plan=rendered_plan)
        if existing_plan_accepted is not None and artifact is None:
            _validate_existing_plan_acceptance_scope(workflow, existing_plan_accepted, record)
            raise ValueError("existing goal plan_accepted event requires registered goal artifact before retry")
        if artifact is None:
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(rendered_plan, encoding="utf-8")
            artifact = self.record_artifact(
                workflow_id,
                kind="plan",
                artifact_id=GOAL_PLAN_ARTIFACT_ID,
                path=artifact_path,
                note="promoted goal plan artifact",
            )["artifact"]
        artifact_ref = artifact["artifact_id"]
        artifact_path = artifact["path"]
        blocked_record = GoalRecord(
            objective=pre_state["objective"],
            non_goals=pre_state["non_goals"],
            success_criteria=pre_state["success_criteria"],
            assumptions=pre_state["assumptions"],
            approval_boundaries=pre_state["approval_boundaries"],
            slice_queue=pre_state["slice_queue"],
            plan_accepted=pre_state["plan_accepted"],
            evidence_completion_check=pre_state["evidence_completion_check"],
            plan_artifact_promotion=pre_state["plan_artifact_promotion"],
            child_workflow_refs=pre_state["child_workflow_refs"],
            residuals=residuals,
            final_report_summary=pre_state["final_report_summary"],
        )
        state = blocked_record.as_state(artifact_id=artifact_ref, artifact_path=artifact_path, artifact_hash=artifact["sha256"])
        state, residuals, block_reason = apply_execution_truth_block("goal", state, residuals=residuals)
        if existing_plan_accepted is not None and existing_plan_accepted != state["plan_accepted"]:
            raise ValueError("existing goal plan_accepted event payload does not match current accepted plan")
        validate_goal_state(state, workflow=workflow, terminal=True, final_status=None)
        self._record_plan_acceptance(workflow_id, state["plan_accepted"])
        evidence = {
            "evidence_key": "goal-evidence-completion",
            "kind": "artifact",
            "summary": "Goal evidence completion check passed against the promoted plan artifact.",
            "artifact_refs": [artifact_ref],
        }
        checkpoint = self.record_outcome(
            workflow_id,
            ModeOutcome(
                summary=(
                    "Goal terminal success blocked because execution evidence is missing."
                    if block_reason
                    else "Goal slice queue and accepted plan are ready for visible delivery."
                ),
                status_after="failed_unreported" if block_reason else "completed_unreported",
                phase_after="terminal",
                checkpoint_type="terminal",
                event_type="fail" if block_reason else "complete",
                worklog_block_kind="terminal_summary",
                step_result="terminal",
                residuals=residuals,
                terminal_evidence=evidence,
                mode_state_update=state,
                recovery_lease_id=recovery_lease_id,
                recovery_lease_holder=recovery_lease_holder,
                final_status=(
                    execution_blocked_final_status("goal", block_reason, residuals)
                    if block_reason
                    else {
                        "result": "pass_with_risks" if any(record.residuals.values()) else "pass",
                        "done": [
                            "Recorded objective and success-criteria gates",
                            "Represented goal slices in continuation_plan.steps",
                            "Validated the scoped plan_accepted payload",
                            "Promoted the goal plan artifact through the shared artifact path",
                            "Recorded child workflow reference fields without creating child workflows",
                        ],
                        "checked": [
                            "Goal mode used shared artifact and checkpoint contracts",
                            "Terminal evidence references the promoted plan artifact",
                            "Visible delivery remains gated by reserve-delivery/report-proof/complete-reported",
                        ],
                        "residuals": record.residuals,
                    }
                ),
                failure_reason=block_reason,
            ),
        )
        return {"workflow_id": workflow_id, "artifact": artifact, "checkpoint": checkpoint, "goal": state}

    def _record_plan_acceptance(self, workflow_id: str, payload: dict[str, Any]) -> None:
        event_id = f"evt-plan-accepted-{workflow_id}"
        existing = _existing_plan_accepted_payload(self.store, workflow_id)
        if existing is not None:
            if existing != payload:
                raise ValueError("existing goal plan_accepted event payload does not match current accepted plan")
            return
        try:
            self.store.append_event(
                workflow_id,
                {
                    "schema_version": 1,
                    "event_id": event_id,
                    "workflow_id": workflow_id,
                    "event_type": "plan_accepted",
                    "created_at": now_iso(),
                    "note": "goal plan accepted before terminal continuation",
                    "payload": payload,
                },
            )
        except ValueError as exc:
            if "duplicate event_id" not in str(exc):
                raise
            existing = _existing_plan_accepted_payload(self.store, workflow_id)
            if existing != payload:
                raise ValueError("existing goal plan_accepted event payload does not match current accepted plan") from exc


def build_goal_record(workflow: dict[str, Any], *, accepted_at: str | None = None) -> GoalRecord:
    objective = _compact(workflow.get("source_request") or workflow.get("objective") or "") or "Execute the accepted goal safely."
    non_goals = [
        "Do not perform external, Gateway, adapter, slash routing, push, PR, or release work from this C4 slice.",
        "Do not create child workflows until a later adapter or orchestration slice owns that behavior.",
    ]
    success_criteria = [
        "Goal execution is gated by an explicit objective and success criteria.",
        "The durable slice queue is represented in continuation_plan.steps.",
        "A scoped plan_accepted payload validates before automatic continuation is considered accepted.",
        "Terminal success requires artifact-backed evidence completion.",
        "Future child verify and conv workflows are referenced by durable ids, not copied state.",
    ]
    assumptions = [
        "The current /goal request is the owner authorization for this local C4 behavior slice.",
        "Real child workflow creation remains outside C4.",
    ]
    approval_boundaries = [
        "External actions require explicit owner approval.",
        "Gateway restart, adapter work, slash routing, push, PR, and release remain forbidden for this slice.",
    ]
    continuation = workflow.get("continuation_plan") or {}
    steps = continuation.get("steps") if isinstance(continuation, dict) else []
    slice_queue = [
        {
            "step_id": step["step_id"],
            "objective": step["objective"],
            "gate": step["gate"],
            "next_on_pass": step["next_on_pass"],
            "expected_artifacts": step["expected_artifacts"],
            "status": "pending",
        }
        for step in steps
        if isinstance(step, dict)
    ]
    plan_accepted = {
        "objective": objective,
        "non_goals": non_goals,
        "success_criteria": success_criteria,
        "assumptions": assumptions,
        "approval_boundaries": approval_boundaries,
        "plan_artifact_ref": GOAL_PLAN_ARTIFACT_ID,
        "plan_artifact_hash": "pending-registration",
        "source_ref": workflow.get("workflow_id", "goal-workflow"),
        "accepted_at": accepted_at or now_iso(),
    }
    validate_acceptance_payload("plan_accepted", plan_accepted, require_nonempty_objective=True)
    required_evidence = ["objective_gate", "success_criteria_gate", "plan_accepted", "promoted_plan_artifact"]
    evidence_completion_check = {
        "required_evidence": required_evidence,
        "completed_evidence": list(required_evidence),
        "complete": True,
    }
    plan_artifact_promotion = {
        "promoted": True,
        "source_ref": workflow.get("workflow_id", "goal-workflow"),
        "promotion_reason": "accepted goal plan is durable and artifact-backed",
    }
    child_workflow_refs = [
        {
            "workflow_id": f"{workflow.get('workflow_id', 'goal')}-verify-child",
            "kind": "verify",
            "purpose": "future evidence verification child reference",
            "status": "planned_reference",
        },
        {
            "workflow_id": f"{workflow.get('workflow_id', 'goal')}-conv-child",
            "kind": "conv",
            "purpose": "future convergence child reference",
            "status": "planned_reference",
        },
    ]
    residuals = {
        "blocking_remaining": [],
        "accepted_risks": [
            "C4 records child workflow reference fields but does not create child workflows.",
        ],
        "implementation_backlog": [],
        "deferred_scope": [
            "C4.5 helper consolidation, C5 recovery, adapters, slash routing, and release wiring remain outside C4.",
        ],
    }
    return GoalRecord(
        objective=objective,
        non_goals=non_goals,
        success_criteria=success_criteria,
        assumptions=assumptions,
        approval_boundaries=approval_boundaries,
        slice_queue=slice_queue,
        plan_accepted=plan_accepted,
        evidence_completion_check=evidence_completion_check,
        plan_artifact_promotion=plan_artifact_promotion,
        child_workflow_refs=child_workflow_refs,
        residuals=residuals,
        final_report_summary="Goal mode captured a durable accepted-plan slice queue with artifact-backed completion evidence.",
    )


def _existing_plan_accepted_payload(store: Any, workflow_id: str) -> dict[str, Any] | None:
    events_path = store.workflow_dir(workflow_id) / "events.jsonl"
    matches: list[dict[str, Any]] = []
    for line in events_path.read_text(encoding="utf-8").splitlines() if events_path.exists() else []:
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event_type") == "plan_accepted":
            payload = event.get("payload")
            if not isinstance(payload, dict):
                raise ValueError("existing goal plan_accepted event payload must be an object")
            matches.append(payload)
    if not matches:
        return None
    first = matches[0]
    if any(item != first for item in matches[1:]):
        raise ValueError("existing goal plan_accepted event payloads conflict")
    if len(matches) > 1:
        raise ValueError("existing goal plan_accepted event payloads are duplicated")
    return first


def _validate_existing_plan_acceptance_scope(workflow: dict[str, Any], existing: dict[str, Any], record: GoalRecord) -> None:
    for key in ("objective", "non_goals", "success_criteria", "assumptions", "approval_boundaries"):
        if existing.get(key) != getattr(record, key):
            raise ValueError("existing goal plan_accepted event payload does not match current accepted plan")
    if existing.get("plan_artifact_ref") != GOAL_PLAN_ARTIFACT_ID:
        raise ValueError("existing goal plan_accepted event payload does not reference the goal plan artifact")
    if existing.get("source_ref") != workflow.get("workflow_id", "goal-workflow"):
        raise ValueError("existing goal plan_accepted event payload does not match current accepted plan")


def render_goal_plan(record: GoalRecord) -> str:
    lines = [
        "# Goal Plan",
        "",
        "## Objective",
        "",
        f"- {record.objective}",
        "",
        "## Success Criteria",
        "",
        *[f"- {item}" for item in record.success_criteria],
        "",
        "## Slice Queue",
        "",
    ]
    for item in record.slice_queue:
        lines.extend(
            [
                f"### {item['step_id']}",
                "",
                f"- Objective: {item['objective']}",
                f"- Next on pass: {item['next_on_pass']}",
                f"- Status: {item['status']}",
                "",
            ]
        )
    lines.extend(
        [
            "## Evidence Completion",
            "",
            f"- Complete: {record.evidence_completion_check['complete']}",
            "",
            "## Child Workflow References",
            "",
        ]
    )
    lines.extend(
        f"- {item['workflow_id']} ({item['kind']}): {item['status']}"
        for item in record.child_workflow_refs
    )
    lines.extend(["", "## Summary", "", f"- {record.final_report_summary}", ""])
    return "\n".join(lines)


def validate_goal_state(
    state: dict[str, Any],
    *,
    workflow: dict[str, Any] | None,
    terminal: bool,
    final_status: dict[str, Any] | None,
) -> dict[str, list[str]]:
    if not state:
        if terminal:
            raise ValueError("terminal or artifact-backed goal workflow requires populated goal_state")
        return normalize_residuals({})
    required = {
        "final_plan_artifact_id",
        "final_plan_artifact_path",
        "objective",
        "non_goals",
        "success_criteria",
        "assumptions",
        "approval_boundaries",
        "slice_queue",
        "plan_accepted",
        "evidence_completion_check",
        "plan_artifact_promotion",
        "child_workflow_refs",
        "residuals",
        "final_report_summary",
    }
    missing = sorted(required - set(state))
    if missing:
        raise ValueError(f"goal_state is missing required fields: {missing!r}")
    for key in ("objective", "final_plan_artifact_id", "final_plan_artifact_path", "final_report_summary"):
        if not isinstance(state.get(key), str) or not state[key]:
            raise ValueError(f"goal_state {key} must be a non-empty string")
    for key in ("non_goals", "success_criteria", "assumptions", "approval_boundaries", "slice_queue", "child_workflow_refs"):
        if not isinstance(state.get(key), list):
            raise ValueError(f"goal_state {key} must be an array")
    if not state["success_criteria"]:
        raise ValueError("goal_state success_criteria must not be empty")
    if not state["slice_queue"]:
        raise ValueError("goal_state slice_queue must not be empty")
    plan_accepted = state["plan_accepted"]
    if not isinstance(plan_accepted, dict):
        raise ValueError("goal_state plan_accepted must be an object")
    validate_acceptance_payload("plan_accepted", plan_accepted, require_nonempty_objective=True)
    for key in ("objective", "non_goals", "success_criteria", "assumptions", "approval_boundaries"):
        if plan_accepted.get(key) != state.get(key):
            raise ValueError(f"goal_state plan_accepted {key} must match goal_state {key}")
    completion = state["evidence_completion_check"]
    if not isinstance(completion, dict):
        raise ValueError("goal_state evidence_completion_check must be an object")
    required_evidence = completion.get("required_evidence")
    completed_evidence = completion.get("completed_evidence")
    if not isinstance(required_evidence, list) or not isinstance(completed_evidence, list):
        raise ValueError("goal_state evidence completion lists must be arrays")
    if terminal and (not completion.get("complete") or set(required_evidence) - set(completed_evidence)):
        raise ValueError("terminal goal workflow requires complete evidence completion check")
    promotion = state["plan_artifact_promotion"]
    if not isinstance(promotion, dict) or promotion.get("promoted") is not True:
        raise ValueError("goal_state plan_artifact_promotion must mark promoted=true")
    if promotion.get("plan_artifact_id") != state["final_plan_artifact_id"]:
        raise ValueError("goal_state plan_artifact_promotion artifact id must match final_plan_artifact_id")
    if plan_accepted.get("plan_artifact_ref") != state["final_plan_artifact_id"]:
        raise ValueError("goal_state plan_accepted plan_artifact_ref must match final_plan_artifact_id")
    if plan_accepted.get("plan_artifact_hash") == "pending-registration":
        raise ValueError("goal_state plan_accepted plan_artifact_hash must be materialized")
    if promotion.get("plan_artifact_hash") != plan_accepted.get("plan_artifact_hash"):
        raise ValueError("goal_state promoted artifact hash must match plan_accepted hash")
    for item in state["slice_queue"]:
        _validate_slice_queue_item(item)
    for item in state["child_workflow_refs"]:
        _validate_child_workflow_ref(item)
    if workflow is not None:
        plan = workflow.get("continuation_plan") or {}
        steps = plan.get("steps") if isinstance(plan, dict) else []
        step_ids = [step.get("step_id") for step in steps if isinstance(step, dict)]
        queue_ids = [item.get("step_id") for item in state["slice_queue"] if isinstance(item, dict)]
        if step_ids != queue_ids:
            raise ValueError("goal_state slice_queue must match continuation_plan.steps")
    residuals = normalize_residuals(state["residuals"])
    if final_status is not None and normalize_residuals(final_status.get("residuals")) != residuals:
        raise ValueError("goal_state residuals must match final_status.residuals")
    return residuals


def _validate_slice_queue_item(item: dict[str, Any]) -> None:
    required = {"step_id", "objective", "gate", "next_on_pass", "expected_artifacts", "status"}
    missing = sorted(required - set(item))
    if missing:
        raise ValueError(f"goal_state slice_queue item is missing required fields: {missing!r}")
    for key in ("step_id", "objective", "next_on_pass", "status"):
        if not isinstance(item.get(key), str) or not item[key]:
            raise ValueError(f"goal_state slice_queue {key} must be a non-empty string")
    if item["status"] not in {"pending", "satisfied", "blocked"}:
        raise ValueError("goal_state slice_queue status is invalid")
    if not isinstance(item.get("gate"), dict):
        raise ValueError("goal_state slice_queue gate must be an object")
    if not isinstance(item.get("expected_artifacts"), list):
        raise ValueError("goal_state slice_queue expected_artifacts must be an array")


def _validate_child_workflow_ref(item: dict[str, Any]) -> None:
    required = {"workflow_id", "kind", "purpose", "status"}
    missing = sorted(required - set(item))
    if missing:
        raise ValueError(f"goal_state child workflow ref is missing required fields: {missing!r}")
    if item["kind"] not in {"verify", "conv"}:
        raise ValueError("goal_state child workflow ref kind must be verify or conv")
    if item["status"] not in {"planned_reference", "running", "completed", "blocked"}:
        raise ValueError("goal_state child workflow ref status is invalid")
    for key in ("workflow_id", "purpose"):
        if not isinstance(item.get(key), str) or not item[key]:
            raise ValueError(f"goal_state child workflow ref {key} must be a non-empty string")


def _existing_goal_artifact(workflow: dict[str, Any], *, expected_path: Path, rendered_plan: str) -> dict[str, Any] | None:
    matches = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == GOAL_PLAN_ARTIFACT_ID
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(f"duplicate goal plan artifact id: {GOAL_PLAN_ARTIFACT_ID}")
    artifact = matches[0]
    if artifact.get("kind") != "plan":
        raise ValueError(f"goal plan artifact id has wrong kind: {artifact.get('kind')!r}")
    path = artifact.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("goal plan artifact path is missing")
    if not Path(path).is_file():
        raise ValueError(f"goal plan artifact path is missing: {path}")
    if Path(path).expanduser().resolve() != expected_path:
        raise ValueError("goal plan artifact path is not the canonical goal output path")
    if Path(path).read_text(encoding="utf-8") != rendered_plan:
        raise ValueError("existing goal artifact does not match rendered goal plan")
    return artifact


def _compact(text: str) -> str:
    return " ".join(text.split())
