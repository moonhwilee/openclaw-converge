"""Shared mode handler primitives for Converge workflows."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..checkpoint import record_checkpoint
from ..continuation import current_cursor
from ..artifacts import record_workflow_artifact
from ..messages import normalize_residuals
from ..schema import validate_named
from ..store import WorkflowStore


WORKFLOW_KINDS = {"plan", "goal", "verify", "conv"}
CHECKPOINT_TYPES = {"checkpoint", "advance", "terminal"}
EVENT_TYPES = {"checkpoint", "advance", "complete", "fail"}
WORKLOG_BLOCK_KINDS = {
    "checkpoint_summary",
    "round_summary",
    "slice_summary",
    "terminal_summary",
    "recovery_summary",
}
STEP_RESULTS = {"none", "passed", "failed", "blocked", "waiting", "terminal"}
STATUSES = {
    "draft",
    "running",
    "waiting_user",
    "waiting_subagent",
    "verifying",
    "completed_unreported",
    "failed_unreported",
    "reported",
    "blocked",
    "abandoned",
}


@dataclass(frozen=True)
class ModeOutcome:
    """A mode-owned state transition request.

    Mode handlers convert this small object into the existing checkpoint
    primitive instead of mutating workflow JSON or event logs directly.
    """

    summary: str
    status_after: str
    phase_after: str
    cursor_after: str | None = None
    checkpoint_type: str = "checkpoint"
    event_type: str = "checkpoint"
    worklog_block_kind: str = "slice_summary"
    step_result: str = "waiting"
    residuals: dict[str, Any] = field(default_factory=dict)
    next_action: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    mode_state_update: dict[str, Any] | None = None
    side_effects: list[dict[str, Any]] = field(default_factory=list)
    context_manifest_updates: list[dict[str, Any]] = field(default_factory=list)
    terminal_evidence: dict[str, Any] | None = None
    final_status: dict[str, Any] | None = None
    recovery_lease_id: str | None = None
    recovery_lease_holder: str | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("mode outcome summary is required")
        _validate_member("status_after", self.status_after, STATUSES)
        _validate_member("checkpoint_type", self.checkpoint_type, CHECKPOINT_TYPES)
        _validate_member("event_type", self.event_type, EVENT_TYPES)
        _validate_member("worklog_block_kind", self.worklog_block_kind, WORKLOG_BLOCK_KINDS)
        _validate_member("step_result", self.step_result, STEP_RESULTS)
        self._validate_checkpoint_matrix()
        normalize_residuals(self.residuals)

    def _validate_checkpoint_matrix(self) -> None:
        if self.status_after in {"reported", "abandoned"}:
            raise ValueError("mode outcomes cannot set reported or abandoned status; use dedicated flow")
        if self.checkpoint_type == "checkpoint" and self.event_type != "checkpoint":
            raise ValueError("checkpoint mode outcomes must use checkpoint event_type")
        if self.checkpoint_type == "advance" and self.event_type != "advance":
            raise ValueError("advance mode outcomes must use advance event_type")
        if self.checkpoint_type != "terminal" and self.status_after in {"completed_unreported", "failed_unreported"}:
            raise ValueError("terminal statuses require terminal checkpoint_type")
        if self.checkpoint_type != "terminal":
            return
        if self.event_type not in {"complete", "fail"}:
            raise ValueError("terminal mode outcomes must use complete or fail event_type")
        if self.step_result != "terminal":
            raise ValueError("terminal mode outcomes must use terminal step_result")
        if self.worklog_block_kind != "terminal_summary":
            raise ValueError("terminal mode outcomes must use terminal_summary worklog block")
        if self.event_type == "complete" and self.status_after != "completed_unreported":
            raise ValueError("complete terminal outcomes must set completed_unreported")
        if self.event_type == "fail" and self.status_after != "failed_unreported":
            raise ValueError("fail terminal outcomes must set failed_unreported")
        if not self.final_status:
            raise ValueError("terminal mode outcomes require final_status")
        if self.event_type == "complete" and not (self.evidence or self.terminal_evidence):
            raise ValueError("complete terminal mode outcomes require evidence")
        if self.event_type == "fail" and not self.failure_reason:
            raise ValueError("failed terminal mode outcomes require failure_reason")

    def state_update(self, cursor_before: str) -> dict[str, Any]:
        cursor_after = self.cursor_after or cursor_before
        update: dict[str, Any] = {
            "checkpoint_type": self.checkpoint_type,
            "status_after": self.status_after,
            "phase_after": self.phase_after,
            "cursor_before": cursor_before,
            "cursor_after": cursor_after,
            "event_type": self.event_type,
            "worklog_block_kind": self.worklog_block_kind,
            "step_result": self.step_result,
            "residuals": normalize_residuals(self.residuals),
        }
        if self.side_effects:
            update["side_effects"] = self.side_effects
        if self.mode_state_update:
            update["mode_state_update"] = self.mode_state_update
        if self.context_manifest_updates:
            update["context_manifest_updates"] = self.context_manifest_updates
        if self.terminal_evidence:
            update["terminal_evidence"] = self.terminal_evidence
        if self.final_status:
            update["final_status"] = self.final_status
        if self.recovery_lease_id:
            update["recovery_lease_id"] = self.recovery_lease_id
        if self.recovery_lease_holder:
            update["recovery_lease_holder"] = self.recovery_lease_holder
        if self.failure_reason:
            update["failure_reason"] = self.failure_reason
        validate_named(update, "checkpoint_state_update.schema.json")
        return update


class ModeHandler:
    """Base class for mode helpers.

    Subclasses should keep mode-specific decisions outside the persistence
    layer and call :meth:`record_outcome` for every state transition.
    """

    kind: str

    def __init__(self, store: WorkflowStore):
        self.store = store
        _validate_member("kind", self.kind, WORKFLOW_KINDS)

    def load_workflow(self, workflow_id: str) -> dict[str, Any]:
        workflow = self.store.load_workflow(workflow_id)
        if workflow.get("kind") != self.kind:
            raise ValueError(f"{self.kind} handler cannot operate on {workflow.get('kind')!r} workflow")
        return workflow

    def current_cursor(self, workflow_id: str) -> str:
        return current_cursor(self.load_workflow(workflow_id))

    def record_outcome(self, workflow_id: str, outcome: ModeOutcome) -> dict[str, Any]:
        workflow = self.load_workflow(workflow_id)
        outcome = _apply_execution_terminal_gate(workflow, outcome)
        _validate_current_status(workflow, outcome)
        cursor_before = current_cursor(workflow)
        return record_checkpoint(
            self.store,
            workflow_id=workflow_id,
            checkpoint_type=outcome.checkpoint_type,
            state_update=outcome.state_update(cursor_before),
            summary=outcome.summary,
            next_action=outcome.next_action,
            evidence=outcome.evidence,
        )

    def validate_recovery_preflight(
        self,
        workflow_id: str,
        *,
        recovery_lease_id: str | None = None,
        recovery_lease_holder: str | None = None,
    ) -> None:
        self.store.require_no_pending_recovery(workflow_id)
        workflow = self.load_workflow(workflow_id)
        lease = workflow.get("active_recovery_lease")
        has_recovery_args = bool(recovery_lease_id or recovery_lease_holder)
        if not isinstance(lease, dict):
            if has_recovery_args:
                raise ValueError("recovery lease args require an active recovery lease")
            return
        if not recovery_lease_id or not recovery_lease_holder:
            raise ValueError("active recovery lease requires matching recovery_lease_id and recovery_lease_holder")
        if recovery_lease_id != lease.get("lease_id"):
            raise ValueError("active recovery lease requires matching recovery_lease_id")
        if recovery_lease_holder != lease.get("holder"):
            raise ValueError("active recovery lease requires matching recovery_lease_holder")
        if lease.get("cursor") != current_cursor(workflow):
            raise ValueError("active recovery lease does not match current cursor")
        if _parse_utc(lease.get("lease_expires_at")) <= datetime.now(timezone.utc):
            raise ValueError("active recovery lease expired; run recover again")

    def record_artifact(
        self,
        workflow_id: str,
        *,
        kind: str,
        path: Path,
        artifact_id: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        self.load_workflow(workflow_id)
        return record_workflow_artifact(
            self.store,
            workflow_id=workflow_id,
            artifact_id=artifact_id,
            kind=kind,
            path=path,
            note=note,
        )


def _validate_member(name: str, value: str, allowed: set[str]) -> None:
    if value not in allowed:
        raise ValueError(f"invalid {name}: {value!r}")


def _validate_current_status(workflow: dict[str, Any], outcome: ModeOutcome) -> None:
    status = workflow.get("status")
    if status in {"completed_unreported", "failed_unreported", "reported", "abandoned"}:
        raise ValueError(f"mode outcomes cannot continue workflow in terminal status: {status!r}")


EXECUTION_GATED_KINDS = {"goal", "verify", "conv"}
SUCCESS_FINAL_RESULTS = {"pass", "pass_with_risks"}
EXECUTION_TRUTH_FIELDS = ("execution_required", "execution_performed", "synthetic_report")


def apply_execution_truth_block(
    kind: str,
    state: dict[str, Any],
    *,
    residuals: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, list[str]], str | None]:
    """Return a blocked mode state when terminal success lacks execution proof."""

    blocked_state = deepcopy(state)
    blockers = _execution_gate_blockers(kind, blocked_state)
    normalized_residuals = normalize_residuals(residuals or blocked_state.get("residuals"))
    if not blockers:
        return blocked_state, normalized_residuals, None

    reason = _execution_block_reason(kind, blocked_state)
    normalized_residuals["blocking_remaining"] = _unique([*normalized_residuals["blocking_remaining"], *blockers])
    blocked_state["residuals"] = normalized_residuals
    _mark_mode_state_blocked(kind, blocked_state, reason)
    return blocked_state, normalized_residuals, reason


def execution_blocked_final_status(kind: str, reason: str, residuals: dict[str, list[str]]) -> dict[str, Any]:
    return {
        "result": "blocked",
        "stop_reason": reason,
        "done": [
            "Recorded scaffold or synthetic mode output",
            "Applied the shared execution truth gate before terminal success",
        ],
        "checked": [
            "execution_required/execution_performed/synthetic_report markers",
            "No trusted runner evidence recorded for this workflow",
            "Visible delivery remains gated by reserve-delivery/report-proof/complete-reported",
        ],
        "residuals": residuals,
    }


def _apply_execution_terminal_gate(workflow: dict[str, Any], outcome: ModeOutcome) -> ModeOutcome:
    """Block scaffold or synthetic terminal success for execution-required modes."""

    kind = workflow.get("kind")
    if kind not in EXECUTION_GATED_KINDS:
        return outcome
    if outcome.checkpoint_type != "terminal" or outcome.event_type != "complete":
        return outcome
    final_status = outcome.final_status or {}
    if final_status.get("result") not in SUCCESS_FINAL_RESULTS:
        return outcome

    active_state = outcome.mode_state_update or workflow.get(f"{kind}_state") or {}
    has_mode_state = bool(active_state)
    state, residuals, reason = apply_execution_truth_block(kind, active_state, residuals=outcome.residuals)
    if reason is None:
        return outcome
    mode_state_update = None
    if has_mode_state:
        mode_state_update = state

    return replace(
        outcome,
        summary=f"{kind} terminal success blocked because execution evidence is missing.",
        status_after="failed_unreported",
        event_type="fail",
        residuals=residuals,
        mode_state_update=mode_state_update,
        final_status=execution_blocked_final_status(kind, reason, residuals),
        failure_reason=reason,
    )


def _execution_gate_blockers(kind: str, state: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    missing = [field for field in EXECUTION_TRUTH_FIELDS if field not in state]
    if missing:
        blockers.append(f"Missing execution truth markers: {', '.join(missing)}.")
        return blockers

    execution_required = state.get("execution_required")
    execution_performed = state.get("execution_performed")
    synthetic_report = state.get("synthetic_report")
    if execution_required is not False and execution_required is not True:
        blockers.append("execution_required must be explicit true or false.")
    if execution_performed is not False and execution_performed is not True:
        blockers.append("execution_performed must be explicit true or false.")
    if synthetic_report is not False and synthetic_report is not True:
        blockers.append("synthetic_report must be explicit true or false.")
    if blockers:
        return blockers

    if execution_required is True and execution_performed is not True:
        blockers.append("Execution is required but no trusted runner evidence set execution_performed=true.")
    if execution_required is True and execution_performed is True and not state.get("execution_evidence_refs"):
        blockers.append("Execution was marked performed without execution evidence refs.")
    if synthetic_report is True and execution_required is not False:
        blockers.append("Synthetic or scaffold report cannot complete an execution-required workflow.")
    if kind == "goal" and execution_required is True:
        planned_refs = [
            item.get("workflow_id", "<unknown>")
            for item in state.get("child_workflow_refs", [])
            if isinstance(item, dict) and item.get("status") == "planned_reference"
        ]
        if planned_refs:
            blockers.append(f"Goal child workflows are planned references only: {', '.join(planned_refs)}.")
    if kind == "conv" and execution_required is True and state.get("evidence_sufficient") is True:
        blockers.append("Convergence evidence sufficiency is synthetic until a real round runner records execution proof.")
    return blockers


def _execution_block_reason(kind: str, state: dict[str, Any]) -> str:
    if any(field not in state for field in EXECUTION_TRUTH_FIELDS):
        return "blocked_missing_execution_truth_markers"
    if kind == "goal" and any(
        isinstance(item, dict) and item.get("status") == "planned_reference"
        for item in state.get("child_workflow_refs", [])
    ):
        return "blocked_child_workflows_not_run"
    return "blocked_no_execution_evidence"


def _mark_mode_state_blocked(kind: str, state: dict[str, Any], reason: str) -> None:
    state["execution_blocked"] = True
    state["execution_block_reason"] = reason
    if kind == "verify":
        state["verdict"] = "blocked"
        state["final_report_summary"] = "Verification is blocked because required execution evidence is missing."
    elif kind == "conv":
        state["stop_condition"] = reason
        state["stop_reason"] = reason
        state["evidence_sufficient"] = False
        state["final_report_summary"] = "Convergence is blocked because required round execution evidence is missing."
    elif kind == "goal":
        if reason == "blocked_child_workflows_not_run":
            state["final_report_summary"] = "Goal execution is blocked because child workflows were not run."
        else:
            state["final_report_summary"] = "Goal execution is blocked because required execution truth markers are missing."


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("active recovery lease requires lease_expires_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("active recovery lease has invalid lease_expires_at") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
