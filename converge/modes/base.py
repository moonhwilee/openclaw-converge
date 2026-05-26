"""Shared mode handler primitives for Converge workflows."""

from __future__ import annotations

from dataclasses import dataclass, field
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
