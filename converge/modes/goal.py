"""Goal mode vertical slice."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..acceptance import validate_acceptance_payload
from ..artifacts import now_iso
from ..messages import normalize_residuals
from .base import ModeHandler, ModeOutcome, apply_execution_truth_block, execution_blocked_final_status
from .evidence_contract import attach_phase5a_evidence_contract
from .execution_truth import classify_execution_markers


GOAL_PLAN_ARTIFACT_ID = "goal-promoted-plan"
PHASE5B_PARENT_SUMMARY_MODE = "parent_summary_only"
PHASE5B_VISIBLE_CHILD_REPORT_MODE = "visible_child_report_required"
PHASE5B_OWNER_WAIVER_MODE = "waived_with_owner_proof"
PHASE5B_CHILD_DELIVERY_MODES = {
    PHASE5B_PARENT_SUMMARY_MODE,
    PHASE5B_VISIBLE_CHILD_REPORT_MODE,
    PHASE5B_OWNER_WAIVER_MODE,
}


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
    """Promotes accepted goals and owns source-local child workflow collection."""

    kind = "goal"

    def finalize_goal(
        self,
        workflow_id: str,
        *,
        native_agent_backend: Any | None = None,
        target_refs: list[dict[str, Any]] | None = None,
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
        child_collection = _ensure_goal_children(self, workflow, record, native_agent_backend=native_agent_backend, target_refs=target_refs)
        if child_collection is not None:
            record = _record_with_child_collection(record, child_collection)
        artifact_path = (self.store.workflow_dir(workflow_id) / "artifacts" / "goal-plan.md").expanduser().resolve()
        artifact_ref = GOAL_PLAN_ARTIFACT_ID
        artifact_path_text = str(artifact_path)
        pre_state = record.as_state(artifact_id=artifact_ref, artifact_path=artifact_path_text, artifact_hash="pending")
        if child_collection is not None:
            pre_state.update(child_collection["execution_markers"])
            pre_state["child_collection_status"] = child_collection["collection_status"]
        pre_state, residuals, block_reason = apply_execution_truth_block("goal", pre_state, residuals=record.residuals)
        child_block_reason = _child_block_reason(child_collection)
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
        if child_collection is not None:
            state.update(child_collection["execution_markers"])
            state["child_collection_status"] = child_collection["collection_status"]
        state, residuals, block_reason = apply_execution_truth_block("goal", state, residuals=residuals)
        if child_collection is not None:
            state = attach_phase5b_child_delivery_state(state, residuals=residuals)
        child_block_reason = child_block_reason or _child_block_reason(child_collection)
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
        state = attach_phase5a_evidence_contract(
            "goal",
            workflow=self.load_workflow(workflow_id),
            state=state,
            terminal_evidence=evidence,
        )
        checkpoint = self.record_outcome(
            workflow_id,
            ModeOutcome(
                summary=(
                    "Goal terminal success blocked because execution evidence is missing."
                    if block_reason or child_block_reason
                    else "Goal slice queue and accepted plan are ready for visible delivery."
                ),
                status_after="failed_unreported" if block_reason or child_block_reason else "completed_unreported",
                phase_after="terminal",
                checkpoint_type="terminal",
                event_type="fail" if block_reason or child_block_reason else "complete",
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
                    else _child_blocked_final_status(child_block_reason, residuals)
                    if child_block_reason
                    else {
                        "result": "pass_with_risks" if any(record.residuals.values()) else "pass",
                        "done": [
                            "Recorded objective and success-criteria gates",
                            "Represented goal slices in continuation_plan.steps",
                            "Validated the scoped plan_accepted payload",
                            "Promoted the goal plan artifact through the shared artifact path",
                            "Created and collected required child workflows",
                        ],
                        "checked": [
                            "Goal mode used shared artifact and checkpoint contracts",
                            "Terminal evidence references the promoted plan artifact",
                            "Visible delivery remains gated by reserve-delivery/report-proof/complete-reported",
                        ],
                        "residuals": record.residuals,
                    }
                ),
                failure_reason=block_reason or child_block_reason,
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


def _ensure_goal_children(
    handler: GoalHandler,
    workflow: dict[str, Any],
    record: GoalRecord,
    *,
    native_agent_backend: Any | None = None,
    target_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    markers = classify_execution_markers(record.objective, capability="planned_child_refs_only")
    if markers.get("execution_required") is not True:
        return None
    from .conv import ConvHandler
    from .verify import VerifyHandler

    parent_id = workflow["workflow_id"]
    child_refs: list[dict[str, Any]] = []
    for role, child_handler in (("verify", VerifyHandler(handler.store)), ("conv", ConvHandler(handler.store))):
        child_id = _child_workflow_id(parent_id, role=role, objective=record.objective)
        child = _create_or_link_child(handler, workflow, child_id=child_id, role=role)
        if child.get("status") == "running":
            if role == "verify":
                child_handler.finalize_verify(child_id, native_agent_backend=native_agent_backend, target_refs=target_refs)
            else:
                child_handler.finalize_conv(child_id, native_agent_backend=native_agent_backend, target_refs=target_refs)
        child = handler.store.load_workflow(child_id)
        if child.get("status") not in {"completed_unreported", "failed_unreported", "reported", "blocked"}:
            raise ValueError(f"required child workflow is not terminal: {child_id}")
        _record_child_collected(handler, parent_id, child)
        child_refs.append(_child_ref_from_workflow(child, role=role))
    child_ids = [item["workflow_id"] for item in child_refs]
    collection_status = {
        "required_child_workflow_ids": child_ids,
        "collected_child_workflow_ids": child_ids,
        "children": [
            {
                "workflow_id": item["workflow_id"],
                "kind": item["kind"],
                "status": item["status"],
                "terminal_status": item["terminal_status"],
                "result": item["result"],
                "stop_reason": (item.get("final_status") or {}).get("stop_reason"),
                "residuals": (item.get("final_status") or {}).get("residuals"),
                "evidence_refs": item.get("evidence_refs") or [],
            }
            for item in child_refs
        ],
        "complete": True,
    }
    return {
        "child_workflow_refs": child_refs,
        "collection_status": collection_status,
        "execution_markers": {
            "execution_required": True,
            "execution_capability": "child_workflows",
            "execution_performed": True,
            "synthetic_report": False,
            "runner_ref": "trusted-goal-child-workflow-collector-v1",
            "execution_evidence_refs": child_ids,
            "execution_started_at": min((item["created_at"] for item in child_refs if item.get("created_at")), default=now_iso()),
            "execution_completed_at": now_iso(),
            "execution_classification_reason": "goal parent created and collected required child workflows",
        },
    }


def _create_or_link_child(handler: GoalHandler, parent: dict[str, Any], *, child_id: str, role: str) -> dict[str, Any]:
    child_text = _child_request_text(parent, role=role)
    _append_idempotent_event(
        handler,
        parent["workflow_id"],
        event_type="child_creation_intent",
        event_id=f"evt-child-intent-{child_id}",
        note="goal child workflow deterministic id reserved",
        payload={"child_workflow_id": child_id, "child_role": role, "required_for_parent_completion": True},
    )
    try:
        child = handler.store.create_workflow(
            kind=role,
            text=child_text,
            workflow_id=child_id,
            owner_session_key=parent.get("owner_session_key") or "",
            visible_delivery=parent.get("visible_delivery") or {},
        )
    except FileExistsError:
        child = handler.store.load_workflow(child_id)
        if child.get("kind") != role:
            raise ValueError("existing child workflow kind does not match deterministic role")
        if child.get("owner_session_key") != (parent.get("owner_session_key") or ""):
            raise ValueError("existing child workflow owner_session_key does not match parent")
        if child.get("visible_delivery") != (parent.get("visible_delivery") or {}):
            raise ValueError("existing child workflow visible_delivery does not match parent")
        if child.get("source_request") != child_text:
            raise ValueError("existing child workflow source_request does not match deterministic child request")
    if child.get("parent_workflow_id") not in {None, parent["workflow_id"]}:
        raise ValueError("child workflow is already linked to a different parent")
    if child.get("parent_workflow_id") != parent["workflow_id"]:
        child["parent_workflow_id"] = parent["workflow_id"]
        handler.store.save_workflow(child)
    _append_idempotent_event(
        handler,
        child_id,
        event_type="parent_linked",
        event_id=f"evt-parent-linked-{child_id}",
        note="child workflow linked to goal parent",
        payload={"parent_workflow_id": parent["workflow_id"], "child_role": role, "required_for_parent_completion": True},
    )
    parent_workflow = handler.store.load_workflow(parent["workflow_id"])
    child_ids = list(parent_workflow.get("child_workflow_ids") or [])
    if child_id not in child_ids:
        child_ids.append(child_id)
        parent_workflow["child_workflow_ids"] = child_ids
        handler.store.save_workflow(parent_workflow)
    _append_idempotent_event(
        handler,
        parent["workflow_id"],
        event_type="child_workflow_created",
        event_id=f"evt-child-created-{child_id}",
        note="goal child workflow created or linked",
        payload={"child_workflow_id": child_id, "child_role": role, "required_for_parent_completion": True},
    )
    return handler.store.load_workflow(child_id)


def _record_child_collected(handler: GoalHandler, parent_id: str, child: dict[str, Any]) -> None:
    terminal_status = child.get("status")
    result = (child.get("final_status") or {}).get("result") or "none"
    _append_idempotent_event(
        handler,
        parent_id,
        event_type="child_workflow_collected",
        event_id=f"evt-child-collected-{child['workflow_id']}-{terminal_status}-{result}",
        note="goal child workflow terminal status collected",
        payload={
            "child_workflow_id": child["workflow_id"],
            "child_role": child.get("kind"),
            "terminal_status": terminal_status,
            "result": result if result != "none" else None,
        },
    )


def _append_idempotent_event(
    handler: GoalHandler,
    workflow_id: str,
    *,
    event_type: str,
    event_id: str,
    note: str,
    payload: dict[str, Any],
) -> None:
    events_path = handler.store.workflow_dir(workflow_id) / "events.jsonl"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            existing = json.loads(line)
            if existing.get("event_id") == event_id:
                if existing.get("event_type") != event_type or (existing.get("payload") or {}) != payload:
                    raise ValueError(f"existing idempotent event payload mismatch: {event_id}")
                return
    handler.store.append_event(
        workflow_id,
        {
            "schema_version": 1,
            "event_id": event_id,
            "workflow_id": workflow_id,
            "event_type": event_type,
            "created_at": now_iso(),
            "note": note,
            "payload": payload,
        },
    )


def _record_with_child_collection(record: GoalRecord, child_collection: dict[str, Any]) -> GoalRecord:
    child_refs = child_collection["child_workflow_refs"]
    child_blocked = any(item["status"] == "blocked" for item in child_refs)
    child_risks = [
        f"{item['workflow_id']}: {risk}"
        for item in child_refs
        for risk in ((item.get("final_status") or {}).get("residuals") or {}).get("accepted_risks", [])
    ]
    child_backlog = [
        f"{item['workflow_id']}: {entry}"
        for item in child_refs
        for entry in ((item.get("final_status") or {}).get("residuals") or {}).get("implementation_backlog", [])
    ]
    child_deferred = [
        f"{item['workflow_id']}: {entry}"
        for item in child_refs
        for entry in ((item.get("final_status") or {}).get("residuals") or {}).get("deferred_scope", [])
    ]
    residuals = {
        "blocking_remaining": (
            [
                f"Required child workflow {item['workflow_id']} ended with {item['terminal_status']}"
                f" ({(item.get('final_status') or {}).get('stop_reason') or 'no_stop_reason'})."
            ]
            for item in child_refs
            if item["status"] == "blocked"
        ),
        "accepted_risks": [
            "Phase 3 collects child workflow terminal status; child visible report proof remains gated before parent reported completion.",
            *child_risks,
        ],
        "implementation_backlog": [
            "Future phases add delegated specialist adapters and visible child report-proof collection.",
            *child_backlog,
        ],
        "deferred_scope": child_deferred,
    }
    residuals["blocking_remaining"] = [item for group in residuals["blocking_remaining"] for item in group]
    return GoalRecord(
        objective=record.objective,
        non_goals=record.non_goals,
        success_criteria=record.success_criteria,
        assumptions=record.assumptions,
        approval_boundaries=record.approval_boundaries,
        slice_queue=record.slice_queue,
        plan_accepted=record.plan_accepted,
        evidence_completion_check=record.evidence_completion_check,
        plan_artifact_promotion=record.plan_artifact_promotion,
        child_workflow_refs=child_refs,
        residuals=residuals,
        final_report_summary=(
            "Goal child workflow collection blocked parent completion."
            if child_blocked
            else "Goal created and collected required verify/conv child workflows."
        ),
    )


def attach_phase5b_child_delivery_state(state: dict[str, Any], *, residuals: dict[str, list[str]]) -> dict[str, Any]:
    """Attach Phase 5B child delivery bookkeeping without launching external agents."""
    child_refs = [dict(item) for item in state.get("child_workflow_refs") or []]
    if not child_refs:
        return state
    child_ids = [item["workflow_id"] for item in child_refs]
    collection = state.get("child_collection_status") or {}
    collection_children = collection.get("children") or []
    child_collection_by_id = {
        item.get("workflow_id"): item
        for item in collection_children
        if isinstance(item, dict) and isinstance(item.get("workflow_id"), str)
    }
    transitions = []
    child_rollups = []
    child_report_proof_refs: list[str] = []
    for child in child_refs:
        child_id = child["workflow_id"]
        mode = child.get("delivery_mode") or PHASE5B_PARENT_SUMMARY_MODE
        if mode not in PHASE5B_CHILD_DELIVERY_MODES:
            mode = PHASE5B_PARENT_SUMMARY_MODE
        child["delivery_mode"] = mode
        child["delivery_mode_reason"] = (
            "parent owns visible delivery while preserving child residual rollup"
            if mode == PHASE5B_PARENT_SUMMARY_MODE
            else "child visible report proof is required before parent reported completion"
        )
        proof_ref = child.get("report_proof_ref")
        proof_ref_id = _report_proof_ref_id(proof_ref)
        if proof_ref_id:
            child_report_proof_refs.append(proof_ref_id)
        collection_child = child_collection_by_id.get(child_id) or {}
        child_rollups.append(
            {
                "workflow_id": child_id,
                "kind": child.get("kind"),
                "status": child.get("status"),
                "terminal_status": child.get("terminal_status"),
                "result": child.get("result"),
                "stop_reason": (child.get("final_status") or {}).get("stop_reason") or collection_child.get("stop_reason"),
                "residuals": normalize_residuals((child.get("final_status") or {}).get("residuals")),
                "evidence_refs": child.get("evidence_refs") or [],
                "delivery_mode": mode,
                "report_proof_ref": proof_ref,
            }
        )
        transitions.append(
            {
                "workflow_id": child_id,
                "kind": child.get("kind"),
                "from_mode": "unassigned",
                "to_mode": mode,
                "reason": child["delivery_mode_reason"],
                "report_proof_ref": proof_ref,
            }
        )
    updated = dict(state)
    updated["child_workflow_refs"] = child_refs
    updated["child_residual_rollup"] = {
        "required_child_workflow_ids": child_ids,
        "collected_child_workflow_ids": list(collection.get("collected_child_workflow_ids") or child_ids),
        "children": child_rollups,
        "parent_residuals": normalize_residuals(residuals),
        "complete": collection.get("complete") is True,
    }
    updated["child_delivery_mode_transitions"] = transitions
    updated["workflow_graph"] = _phase5_workflow_graph(updated, child_refs=child_refs, child_rollups=child_rollups)
    updated["duplicate_report_guard"] = {
        "guard_id": f"duplicate-child-visible-report-guard:{updated.get('final_plan_artifact_id')}",
        "parent_owns_visible_delivery": True,
        "parent_may_summarize_children": True,
        "parent_must_not_duplicate_child_reports": True,
        "child_workflow_ids": child_ids,
        "child_report_proof_refs": child_report_proof_refs,
        "duplicate_child_report_attempts": [],
        "valid": True,
    }
    return updated


def _phase5_workflow_graph(
    state: dict[str, Any],
    *,
    child_refs: list[dict[str, Any]],
    child_rollups: list[dict[str, Any]],
) -> dict[str, Any]:
    parent_id = state.get("plan_accepted", {}).get("source_ref") or "goal-parent"
    owner_sessions = sorted(
        {
            child.get("owner_session_key")
            for child in child_refs
            if isinstance(child.get("owner_session_key"), str) and child.get("owner_session_key")
        }
    )
    visible_delivery_policies = [
        child.get("visible_delivery_policy")
        for child in child_refs
        if isinstance(child.get("visible_delivery_policy"), dict)
    ]
    parent_owner = owner_sessions[0] if len(owner_sessions) == 1 else ""
    parent_visible_delivery = visible_delivery_policies[0] if visible_delivery_policies else {}
    rollup_by_id = {item["workflow_id"]: item for item in child_rollups}
    nodes = [
        {
            "workflow_id": parent_id,
            "parent_id": None,
            "role": "goal",
            "required": True,
            "state_root": "current_workflow_store",
            "owner_session": parent_owner,
            "visible_delivery_policy": parent_visible_delivery,
            "terminal_status": None,
            "report_proof_ref": None,
        }
    ]
    edges = []
    for child in child_refs:
        child_id = child["workflow_id"]
        rollup = rollup_by_id[child_id]
        nodes.append(
            {
                "workflow_id": child_id,
                "parent_id": parent_id,
                "role": child.get("kind"),
                "required": True,
                "state_root": "current_workflow_store",
                "owner_session": child.get("owner_session_key") or parent_owner,
                "visible_delivery_policy": child.get("visible_delivery_policy") or parent_visible_delivery,
                "terminal_status": child.get("terminal_status"),
                "report_proof_ref": child.get("report_proof_ref"),
            }
        )
        edges.append(
            {
                "parent_id": parent_id,
                "child_id": child_id,
                "role": child.get("kind"),
                "required": True,
                "collection_status": "collected" if rollup.get("workflow_id") == child_id else "missing",
            }
        )
    return {
        "graph_id": f"phase5-goal-child-graph:{parent_id}",
        "nodes": nodes,
        "edges": edges,
        "acyclic": True,
        "owner_session_consistent": len(owner_sessions) <= 1,
        "state_root_consistent": True,
    }


def phase5b_child_delivery_mode(state: dict[str, Any], child_id: str) -> str | None:
    for transition in state.get("child_delivery_mode_transitions") or []:
        if isinstance(transition, dict) and transition.get("workflow_id") == child_id:
            return transition.get("to_mode")
    for child in state.get("child_workflow_refs") or []:
        if isinstance(child, dict) and child.get("workflow_id") == child_id:
            return child.get("delivery_mode")
    return None


def validate_phase5b_child_delivery_state(
    state: dict[str, Any],
    *,
    terminal: bool,
    parent_workflow_id: str | None = None,
) -> None:
    if state.get("execution_performed") is not True or state.get("execution_capability") != "child_workflows":
        return
    child_refs = state.get("child_workflow_refs") or []
    child_ids = [item.get("workflow_id") for item in child_refs if isinstance(item, dict)]
    if any(not isinstance(child_id, str) or not child_id for child_id in child_ids) or len(child_ids) != len(set(child_ids)):
        raise ValueError("goal Phase 5B child workflow ids must be unique non-empty strings")
    rollup = state.get("child_residual_rollup")
    if not isinstance(rollup, dict) or rollup.get("complete") is not True:
        raise ValueError("goal Phase 5B requires complete child_residual_rollup")
    if sorted(rollup.get("required_child_workflow_ids") or []) != sorted(child_ids):
        raise ValueError("goal Phase 5B child_residual_rollup required ids must match child refs")
    if sorted(rollup.get("collected_child_workflow_ids") or []) != sorted(child_ids):
        raise ValueError("goal Phase 5B child_residual_rollup collected ids must match child refs")
    rollup_children = rollup.get("children") or []
    rollup_ids = [item.get("workflow_id") for item in rollup_children if isinstance(item, dict)]
    if sorted(rollup_ids) != sorted(child_ids):
        raise ValueError("goal Phase 5B child_residual_rollup children must match child refs")
    transitions = state.get("child_delivery_mode_transitions")
    if not isinstance(transitions, list) or len(transitions) != len(child_ids):
        raise ValueError("goal Phase 5B requires one child_delivery_mode transition per child")
    transition_ids = [item.get("workflow_id") for item in transitions if isinstance(item, dict)]
    if sorted(transition_ids) != sorted(child_ids) or len(transition_ids) != len(set(transition_ids)):
        raise ValueError("goal Phase 5B child_delivery_mode transitions must match child refs uniquely")
    transition_by_id = {item["workflow_id"]: item for item in transitions if isinstance(item, dict)}
    rollup_by_id = {item["workflow_id"]: item for item in rollup_children if isinstance(item, dict) and item.get("workflow_id") in child_ids}
    for child in child_refs:
        child_id = child["workflow_id"]
        transition = transition_by_id[child_id]
        mode = transition.get("to_mode")
        if mode not in PHASE5B_CHILD_DELIVERY_MODES:
            raise ValueError("goal Phase 5B child delivery mode is invalid")
        if transition.get("from_mode") != "unassigned":
            raise ValueError("goal Phase 5B child delivery mode transition must record explicit from_mode")
        if child.get("delivery_mode") != mode:
            raise ValueError("goal Phase 5B child delivery mode transition must match child ref")
        rollup_child = rollup_by_id[child_id]
        if rollup_child.get("delivery_mode") != mode:
            raise ValueError("goal Phase 5B child residual rollup delivery mode must match transition")
        if normalize_residuals(rollup_child.get("residuals")) != normalize_residuals((child.get("final_status") or {}).get("residuals")):
            raise ValueError("goal Phase 5B child residual rollup must preserve child residuals")
        proof_ref = child.get("report_proof_ref")
        proof_ref_id = _report_proof_ref_id(proof_ref)
        if mode == PHASE5B_VISIBLE_CHILD_REPORT_MODE:
            if child.get("terminal_status") != "reported" or not proof_ref_id:
                raise ValueError("goal Phase 5B visible_child_report_required requires child reported proof")
        elif mode == PHASE5B_PARENT_SUMMARY_MODE:
            if proof_ref not in {None, ""}:
                raise ValueError("goal Phase 5B parent_summary_only must not carry child report proof")
        elif mode == PHASE5B_OWNER_WAIVER_MODE:
            if not isinstance(transition.get("owner_waiver_ref"), str) or not transition["owner_waiver_ref"]:
                raise ValueError("goal Phase 5B waived child delivery requires owner_waiver_ref")
    guard = state.get("duplicate_report_guard")
    if not isinstance(guard, dict) or guard.get("valid") is not True:
        raise ValueError("goal Phase 5B requires valid duplicate_report_guard")
    if sorted(guard.get("child_workflow_ids") or []) != sorted(child_ids):
        raise ValueError("goal Phase 5B duplicate_report_guard child ids must match child refs")
    if guard.get("parent_must_not_duplicate_child_reports") is not True:
        raise ValueError("goal Phase 5B duplicate_report_guard must block child report duplication")
    proof_refs = guard.get("child_report_proof_refs") or []
    if any(not isinstance(ref, str) or not ref for ref in proof_refs) or len(proof_refs) != len(set(proof_refs)):
        raise ValueError("goal Phase 5B duplicate_report_guard child report proofs must be unique")
    expected_proof_refs = sorted(
        _report_proof_ref_id(child.get("report_proof_ref"))
        for child in child_refs
        if _report_proof_ref_id(child.get("report_proof_ref"))
    )
    if sorted(proof_refs) != expected_proof_refs:
        raise ValueError("goal Phase 5B duplicate_report_guard proof refs must match child refs")
    duplicate_attempts = guard.get("duplicate_child_report_attempts")
    if duplicate_attempts not in ([], None):
        raise ValueError("goal Phase 5B duplicate child visible report attempts are blocked")
    if terminal and normalize_residuals(rollup.get("parent_residuals")) != normalize_residuals(state.get("residuals")):
        raise ValueError("goal Phase 5B child residual rollup parent residuals must match goal residuals")
    _validate_phase5_workflow_graph(
        state,
        child_ids=child_ids,
        rollup_ids=rollup_ids,
        child_refs=child_refs,
        parent_workflow_id=parent_workflow_id,
    )


def _validate_phase5_workflow_graph(
    state: dict[str, Any],
    *,
    child_ids: list[str],
    rollup_ids: list[str],
    child_refs: list[dict[str, Any]],
    parent_workflow_id: str | None,
) -> None:
    graph = state.get("workflow_graph")
    if not isinstance(graph, dict):
        raise ValueError("goal Phase 5 workflow_graph must be present")
    nodes = graph.get("nodes")
    edges = graph.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("goal Phase 5 workflow_graph nodes and edges must be arrays")
    node_ids = [node.get("workflow_id") for node in nodes if isinstance(node, dict)]
    if len(node_ids) != len(nodes) or len(node_ids) != len(set(node_ids)):
        raise ValueError("goal Phase 5 workflow_graph node ids must be unique")
    parent_nodes = [
        node
        for node in nodes
        if isinstance(node, dict) and node.get("role") == "goal" and node.get("parent_id") is None
    ]
    if len(parent_nodes) != 1:
        raise ValueError("goal Phase 5 workflow_graph requires exactly one parent goal node")
    parent_id = parent_nodes[0]["workflow_id"]
    expected_parent_id = parent_workflow_id or (state.get("plan_accepted") or {}).get("source_ref")
    if not isinstance(expected_parent_id, str) or not expected_parent_id:
        raise ValueError("goal Phase 5 workflow_graph requires parent workflow identity")
    if parent_id != expected_parent_id:
        raise ValueError("goal Phase 5 workflow_graph parent must match workflow id")
    if graph.get("graph_id") != f"phase5-goal-child-graph:{expected_parent_id}":
        raise ValueError("goal Phase 5 workflow_graph graph_id must match parent workflow id")
    expected_node_ids = sorted([parent_id, *child_ids])
    if sorted(node_ids) != expected_node_ids:
        raise ValueError("goal Phase 5 workflow_graph nodes must match parent and child refs")
    if graph.get("acyclic") is not True or graph.get("state_root_consistent") is not True:
        raise ValueError("goal Phase 5 workflow_graph must be acyclic and state-root consistent")
    if graph.get("owner_session_consistent") is not True:
        raise ValueError("goal Phase 5 workflow_graph owner sessions must be consistent")
    node_by_id = {node["workflow_id"]: node for node in nodes if isinstance(node, dict)}
    if parent_nodes[0].get("required") is not True or parent_nodes[0].get("state_root") != "current_workflow_store":
        raise ValueError("goal Phase 5 workflow_graph parent node is invalid")
    child_ref_by_id = {item["workflow_id"]: item for item in child_refs if isinstance(item, dict)}
    edge_child_ids = [edge.get("child_id") for edge in edges if isinstance(edge, dict)]
    if sorted(edge_child_ids) != sorted(child_ids) or len(edge_child_ids) != len(set(edge_child_ids)):
        raise ValueError("goal Phase 5 workflow_graph edges must match child refs")
    for child_id in child_ids:
        node = node_by_id[child_id]
        child = child_ref_by_id[child_id]
        if node.get("parent_id") != expected_parent_id:
            raise ValueError("goal Phase 5 workflow_graph child parent_id must match parent workflow id")
        if node.get("role") != child.get("kind") or node.get("required") is not True:
            raise ValueError("goal Phase 5 workflow_graph child node role/required must match child ref")
        if node.get("state_root") != "current_workflow_store":
            raise ValueError("goal Phase 5 workflow_graph child state_root must match workflow store")
        if node.get("owner_session") != child.get("owner_session_key"):
            raise ValueError("goal Phase 5 workflow_graph child owner_session must match child ref")
        if node.get("visible_delivery_policy") != child.get("visible_delivery_policy"):
            raise ValueError("goal Phase 5 workflow_graph child visible_delivery_policy must match child ref")
        if node.get("terminal_status") != child.get("terminal_status"):
            raise ValueError("goal Phase 5 workflow_graph child terminal_status must match child ref")
        if node.get("report_proof_ref") != child.get("report_proof_ref"):
            raise ValueError("goal Phase 5 workflow_graph child report_proof_ref must match child ref")
    for edge in edges:
        if not isinstance(edge, dict):
            raise ValueError("goal Phase 5 workflow_graph edge must be an object")
        if edge.get("parent_id") != parent_id:
            raise ValueError("goal Phase 5 workflow_graph edge parent must match goal node")
        if edge.get("child_id") not in child_ids or edge.get("child_id") == parent_id:
            raise ValueError("goal Phase 5 workflow_graph edge child must match a child node")
        child = child_ref_by_id[edge["child_id"]]
        if edge.get("role") != child.get("kind"):
            raise ValueError("goal Phase 5 workflow_graph edge role must match child ref")
        if edge.get("required") is not True or edge.get("collection_status") != "collected":
            raise ValueError("goal Phase 5 workflow_graph edge must be required and collected")
    if sorted(edge_child_ids) != sorted(rollup_ids):
        raise ValueError("goal Phase 5 workflow_graph edges must match child residual rollup")


def _report_proof_ref_id(proof_ref: Any) -> str | None:
    if isinstance(proof_ref, str) and proof_ref:
        return proof_ref
    if isinstance(proof_ref, dict):
        event_id = proof_ref.get("event_id")
        if isinstance(event_id, str) and event_id:
            return event_id
        delivery_message_id = proof_ref.get("delivery_message_id")
        if isinstance(delivery_message_id, str) and delivery_message_id:
            return delivery_message_id
    return None


def _child_block_reason(child_collection: dict[str, Any] | None) -> str | None:
    if child_collection is None:
        return None
    if any(item["status"] == "blocked" for item in child_collection["child_workflow_refs"]):
        return "blocked_child_workflow_failed"
    return None


def _child_blocked_final_status(reason: str | None, residuals: dict[str, list[str]]) -> dict[str, Any] | None:
    if reason is None:
        return None
    return {
        "result": "blocked",
        "stop_reason": reason,
        "done": [
            "Created required goal child workflows",
            "Collected child workflow terminal states",
        ],
        "checked": [
            "At least one required child workflow did not produce passing execution evidence",
            "Visible delivery remains gated by reserve-delivery/report-proof/complete-reported",
        ],
        "residuals": residuals,
    }


def _child_ref_from_workflow(child: dict[str, Any], *, role: str) -> dict[str, Any]:
    result = (child.get("final_status") or {}).get("result")
    status = "completed" if child.get("status") in {"completed_unreported", "reported"} and result in {"pass", "pass_with_risks"} else "blocked"
    native_proof = _native_child_panel_proof(child, role=role)
    return {
        "workflow_id": child["workflow_id"],
        "kind": role,
        "purpose": f"required {role} child workflow execution evidence",
        "status": status,
        "terminal_status": child.get("status"),
        "result": result,
        "final_status": child.get("final_status"),
        "evidence_refs": [
            ref
            for evidence in (child.get("verification") or {}).get("evidence") or []
            if isinstance(evidence, dict)
            for ref in evidence.get("artifact_refs") or []
        ],
        "report_proof_ref": (child.get("visible_delivery_state") or {}).get("report_proof"),
        "owner_session_key": child.get("owner_session_key") or "",
        "visible_delivery_policy": child.get("visible_delivery") or {},
        "parent_id": child.get("parent_workflow_id"),
        "created_at": child.get("created_at"),
        "native_agent_panel_proof": native_proof,
    }


def _native_child_panel_proof(child: dict[str, Any], *, role: str) -> dict[str, Any] | None:
    state = child.get(f"{role}_state")
    if not isinstance(state, dict) or state.get("execution_source") != "native_agent_panel":
        return None
    requests = state.get("agent_request_refs") or []
    results = state.get("agent_result_refs") or []
    if not isinstance(requests, list) or not isinstance(results, list) or not requests or len(requests) != len(results):
        return None
    if not all(isinstance(item, dict) for item in [*requests, *results]):
        return None
    session_keys = [item.get("session_key") for item in results if isinstance(item, dict)]
    request_ids = [item.get("request_id") for item in requests if isinstance(item, dict)]
    if any(not isinstance(value, str) or not value for value in [*session_keys, *request_ids]):
        return None
    if any((item.get("tool_smoke_status") != "passed" or not item.get("tool_smoke_evidence")) for item in results if isinstance(item, dict)):
        return None
    tool_smoke_proofs = [
        {
            "result_id": item.get("result_id"),
            "session_key": item.get("session_key"),
            "agent_session_ref": item.get("agent_session_ref"),
            "tool_smoke_evidence": item.get("tool_smoke_evidence"),
        }
        for item in results
    ]
    return {
        "source": "native_agent_panel",
        "satisfies_native_agent_panel": state.get("satisfies_native_agent_panel") is True,
        "agent_session_refs": [item.get("agent_session_ref") for item in results],
        "session_keys": session_keys,
        "request_ids": request_ids,
        "result_ids": [item.get("result_id") for item in results],
        "profile_refs": [item.get("profile_ref") for item in requests],
        "tool_smoke_status": "passed",
        "tool_smoke_proofs": tool_smoke_proofs,
        "finding_count": len(state.get("agent_finding_refs") or []),
        "evidence_refs": list(state.get("execution_evidence_refs") or []),
        "started_at": state.get("execution_started_at"),
        "completed_at": state.get("execution_completed_at"),
    }


def _child_request_text(parent: dict[str, Any], *, role: str) -> str:
    text = parent.get("source_request") or parent.get("objective") or ""
    if role == "verify":
        return f"Verify required goal child evidence for: {text}"
    return f"Converge required goal child execution for: {text}"


def _child_workflow_id(parent_id: str, *, role: str, objective: str) -> str:
    scope = json.dumps({"parent": parent_id, "role": role, "objective": objective, "attempt": 0}, sort_keys=True)
    digest = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:12]
    return f"goal-child-{role}-{digest}"


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
    validate_phase5b_child_delivery_state(
        state,
        terminal=terminal,
        parent_workflow_id=workflow.get("workflow_id") if workflow else None,
    )
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
