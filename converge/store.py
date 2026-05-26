"""Local file-backed workflow store."""

from __future__ import annotations

import json
import os
import re
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .artifacts import now_iso
from .continuation import default_continuation_plan
from .schema import validate_named


WORKLOG_TEMPLATE = "# Converge Worklog\n\n"
ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{2,96}$")


def state_root(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    workspace = Path(os.environ.get("OPENCLAW_WORKSPACE", Path.home() / ".openclaw" / "workspace"))
    return workspace / "state" / "converge"


def safe_workflow_id(value: str | None = None) -> str:
    candidate = value or f"conv-{uuid.uuid4().hex[:12]}"
    if not ID_RE.match(candidate):
        raise ValueError(f"unsafe workflow id: {candidate!r}")
    return candidate


class WorkflowStore:
    def __init__(self, root: Path | None = None):
        self.root = state_root(root)

    def workflow_dir(self, workflow_id: str) -> Path:
        return self.root / "workflows" / safe_workflow_id(workflow_id)

    def global_events_path(self) -> Path:
        return self.root / "events.jsonl"

    def pending_checkpoint_path(self, workflow_id: str, checkpoint_id: str) -> Path:
        return self.workflow_dir(workflow_id) / f".pending-{safe_workflow_id(checkpoint_id)}.json"

    def pending_recovery_path(self, workflow_id: str, lease_id: str) -> Path:
        return self.workflow_dir(workflow_id) / f".pending-{safe_workflow_id(lease_id)}.json"

    @contextmanager
    def lock(self, workflow_id: str) -> Iterator[None]:
        directory = self.workflow_dir(workflow_id)
        directory.mkdir(parents=True, exist_ok=True)
        lock_path = directory / ".lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def create_workflow(
        self,
        *,
        kind: str,
        text: str,
        workflow_id: str | None = None,
        owner_session_key: str = "",
        visible_delivery: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        workflow_id = safe_workflow_id(workflow_id)
        directory = self.workflow_dir(workflow_id)
        created_at = now_iso()
        visible_delivery = visible_delivery or {}
        continuation_plan = default_continuation_plan(kind)
        resume_cursor = "start"
        if isinstance(continuation_plan, dict):
            resume_cursor = str((continuation_plan.get("rolling_state") or {}).get("current_resume_cursor") or "baseline")
        workflow: dict[str, Any] = {
            "schema_version": 1,
            "workflow_id": workflow_id,
            "kind": kind,
            "status": "running",
            "created_at": created_at,
            "updated_at": created_at,
            "last_activity_at": created_at,
            "last_visible_update_at": None,
            "stale_after_seconds": 7200,
            "reminder_after_seconds": 1800,
            "owner_session_key": owner_session_key,
            "visible_delivery": visible_delivery,
            "source_request": text,
            "objective": text,
            "non_goals": [],
            "success_criteria": [],
            "assumptions": [],
            "approval_boundaries": [],
            "approvals": [],
            "phase": "start",
            "parent_workflow_id": None,
            "child_workflow_ids": [],
            "artifacts": [],
            "context_manifest": [],
            "context_artifacts": [],
            "decisions": [],
            "side_effects_performed": [],
            "verification": {},
            "active_recovery_lease": None,
            "active_delivery_reservation": None,
            "checkpoint_index": {},
            "continuation_plan": continuation_plan,
            "next_safe_action": structured_next_action(
                action_type="inspect_or_continue",
                summary="Inspect workflow state and continue from the current cursor.",
                cursor=resume_cursor,
                risk_class="read_only",
                side_effect_key=f"inspect:{workflow_id}:start",
                idempotency_policy="repeatable",
                expected_artifacts=["workflow.json", "worklog.md"],
            ),
            "visible_delivery_state": {},
            "final_status": None,
        }
        workflow[f"{kind}_state"] = {}
        validate_named(workflow, "workflow.schema.json")
        with self.lock(workflow_id):
            if (directory / "workflow.json").exists():
                raise FileExistsError(f"workflow already exists: {workflow_id}")
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "artifacts").mkdir(exist_ok=True)
            (directory / "worklog.md").write_text(WORKLOG_TEMPLATE, encoding="utf-8")
            self._write_json_atomic(directory / "workflow.json", workflow)
            self.append_event(
                workflow_id,
                {
                    "schema_version": 1,
                    "event_id": f"evt-{uuid.uuid4().hex[:12]}",
                    "workflow_id": workflow_id,
                    "event_type": "start",
                    "created_at": created_at,
                    "note": "workflow created",
                },
                locked=True,
            )
        return workflow

    def load_workflow(self, workflow_id: str) -> dict[str, Any]:
        path = self.workflow_dir(workflow_id) / "workflow.json"
        if not path.exists():
            raise FileNotFoundError(f"workflow not found: {workflow_id}")
        workflow = json.loads(path.read_text(encoding="utf-8"))
        validate_named(workflow, "workflow.schema.json")
        return workflow

    def save_workflow(self, workflow: dict[str, Any]) -> None:
        workflow["updated_at"] = now_iso()
        workflow["last_activity_at"] = workflow["updated_at"]
        validate_named(workflow, "workflow.schema.json")
        self._write_json_atomic(self.workflow_dir(workflow["workflow_id"]) / "workflow.json", workflow)

    def append_event(self, workflow_id: str, event: dict[str, Any], *, locked: bool = False) -> None:
        validate_named(event, "event.schema.json")

        def write() -> None:
            directory = self.workflow_dir(workflow_id)
            directory.mkdir(parents=True, exist_ok=True)
            events_path = directory / "events.jsonl"
            if events_path.exists():
                for line in events_path.read_text(encoding="utf-8").splitlines():
                    if line.strip() and json.loads(line).get("event_id") == event["event_id"]:
                        raise ValueError(f"duplicate event_id for workflow {workflow_id}: {event['event_id']}")
            line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
            with events_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
            self.root.mkdir(parents=True, exist_ok=True)
            with self.global_events_path().open("a", encoding="utf-8") as fh:
                fh.write(line)

        if locked:
            write()
        else:
            with self.lock(workflow_id):
                write()

    def append_worklog(self, workflow_id: str, block: str) -> None:
        with (self.workflow_dir(workflow_id) / "worklog.md").open("a", encoding="utf-8") as fh:
            fh.write(block)

    def write_pending_checkpoint(self, workflow_id: str, checkpoint_id: str, payload: dict[str, Any]) -> None:
        self._write_json_atomic(self.pending_checkpoint_path(workflow_id, checkpoint_id), payload)

    def clear_pending_checkpoint(self, workflow_id: str, checkpoint_id: str) -> None:
        path = self.pending_checkpoint_path(workflow_id, checkpoint_id)
        if path.exists():
            path.unlink()

    def pending_checkpoints(self, workflow_id: str) -> list[Path]:
        return sorted(self.workflow_dir(workflow_id).glob(".pending-chk-*.json"))

    def write_pending_recovery(self, workflow_id: str, lease_id: str, payload: dict[str, Any]) -> None:
        self._write_json_atomic(self.pending_recovery_path(workflow_id, lease_id), payload)

    def clear_pending_recovery(self, workflow_id: str, lease_id: str) -> None:
        path = self.pending_recovery_path(workflow_id, lease_id)
        if path.exists():
            path.unlink()

    def pending_recoveries(self, workflow_id: str) -> list[Path]:
        return sorted(self.workflow_dir(workflow_id).glob(".pending-recovery-*.json"))

    def require_no_pending_checkpoint(self, workflow_id: str) -> None:
        pending = self.pending_checkpoints(workflow_id)
        if pending:
            raise ValueError(f"pending checkpoint transaction requires reconcile: {[item.name for item in pending]}")

    def require_no_pending_recovery(self, workflow_id: str) -> None:
        pending = self.pending_recoveries(workflow_id)
        if pending:
            raise ValueError(f"pending recovery lease transaction requires reconcile: {[item.name for item in pending]}")

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, path)


def structured_next_action(
    *,
    action_type: str,
    summary: str,
    cursor: str,
    risk_class: str,
    side_effect_key: str,
    idempotency_policy: str,
    expected_artifacts: list[str],
    requires_approval: bool = False,
    approval_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "action_type": action_type,
        "summary": summary,
        "risk_class": risk_class,
        "requires_approval": requires_approval,
        "approval_ref": approval_ref,
        "side_effect_key": side_effect_key,
        "idempotency_policy": idempotency_policy,
        "expected_artifacts": expected_artifacts,
        "cursor": cursor,
    }
