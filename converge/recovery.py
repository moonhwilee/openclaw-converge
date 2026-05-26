"""Recovery inspection and lease helpers for the Converge local runtime."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .approvals import approval_matches
from .artifacts import now_iso, validate_manifest_entry
from .continuation import current_cursor
from .schema import RISK_CLASSES
from .store import WorkflowStore


ACTIVE_STATUSES = {"draft", "running", "waiting_subagent", "verifying", "blocked"}
TERMINAL_UNREPORTED_STATUSES = {"completed_unreported", "failed_unreported"}
RECOVERY_CANDIDATE_STATUSES = ACTIVE_STATUSES | TERMINAL_UNREPORTED_STATUSES | {"waiting_user"}
RISKY_CLASSES = {"external", "destructive", "gateway_runtime", "public"}


def inspect_workflow(root: Path, workflow_id: str) -> dict[str, Any]:
    store = WorkflowStore(root)
    workflow = store.load_workflow(workflow_id)
    events_path = store.workflow_dir(workflow_id) / "events.jsonl"
    worklog_path = store.workflow_dir(workflow_id) / "worklog.md"
    return {
        "workflow_id": workflow_id,
        "status": workflow.get("status"),
        "phase": workflow.get("phase"),
        "next_safe_action": workflow.get("next_safe_action"),
        "events_exists": events_path.exists(),
        "worklog_exists": worklog_path.exists(),
    }


def scan_workflows(root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    store = WorkflowStore(root)
    records = [_classify_workflow(store, workflow, now=now) for workflow in _load_workflows(store)]
    records.sort(key=lambda item: (item["severity"], item["workflow_id"]))
    return {
        "ok": True,
        "status": "clean" if not any(record["needs_recovery"] for record in records) else "needs_recovery",
        "workflows": records,
    }


def watchdog_check(root: Path, *, include_clean: bool = False, now: datetime | None = None) -> dict[str, Any]:
    scan = scan_workflows(root, now=now)
    packets = [
        _recovery_packet(record)
        for record in scan["workflows"]
        if record["needs_recovery"]
    ]
    return {
        "ok": True,
        "status": "clean" if not packets else "needs_wake",
        "needs_wake": bool(packets),
        "recoveries": packets,
        "clean": [record for record in scan["workflows"] if not record["needs_recovery"]] if include_clean else [],
    }


def recover_workflow(
    root: Path,
    workflow_id: str,
    *,
    holder: str,
    lease_seconds: int = 1800,
    now: datetime | None = None,
) -> dict[str, Any]:
    if lease_seconds <= 0:
        raise ValueError("lease_seconds must be positive")
    store = WorkflowStore(root)
    observed_at = now or datetime.now(timezone.utc)
    with store.lock(workflow_id):
        workflow = store.load_workflow(workflow_id)
        record = _classify_workflow(store, workflow, now=observed_at)
        if record["blocked"]:
            return {
                "ok": True,
                "workflow_id": workflow_id,
                "recovered": False,
                "blocked": True,
                "reason": record["reason"],
                "classification": record,
            }
        lease = _active_recovery_lease(workflow, now=observed_at)
        if lease is not None:
            return {
                "ok": True,
                "workflow_id": workflow_id,
                "recovered": False,
                "blocked": True,
                "reason": "active_recovery_lease_exists",
                "lease": lease,
                "classification": record,
            }
        if not record["needs_recovery"]:
            return {
                "ok": True,
                "workflow_id": workflow_id,
                "recovered": False,
                "blocked": False,
                "reason": "no_recovery_needed",
                "classification": record,
            }
        if workflow.get("status") in TERMINAL_UNREPORTED_STATUSES:
            return {
                "ok": True,
                "workflow_id": workflow_id,
                "recovered": False,
                "blocked": True,
                "reason": "terminal_delivery_requires_reserve_delivery",
                "classification": record,
                "recovery_packet": _recovery_packet(record),
            }
        lease_id = f"recovery-{uuid.uuid4().hex[:12]}"
        acquired_at = now_iso()
        lease_expires_at = _iso_after(lease_seconds, now=observed_at)
        cursor = record["cursor"]
        checkpoint_id = record["checkpoint_id"] or "no-checkpoint"
        expired_lease = _expired_recovery_lease(workflow, now=observed_at)
        if expired_lease is not None:
            store.append_event(
                workflow_id,
                {
                    "schema_version": 1,
                    "event_id": f"evt-lease-released-{uuid.uuid4().hex[:8]}",
                    "workflow_id": workflow_id,
                    "event_type": "lease_released",
                    "created_at": acquired_at,
                    "status_after": workflow.get("status"),
                    "phase_after": workflow.get("phase"),
                    "cursor_before": cursor,
                    "cursor_after": cursor,
                    "payload": {
                        "lease_id": expired_lease["lease_id"],
                        "reason": "expired_recovery_lease_superseded",
                    },
                },
                locked=True,
            )
        lease_payload = {
            "lease_id": lease_id,
            "lease_type": "recovery",
            "cursor": cursor,
            "holder": holder,
            "acquired_at": acquired_at,
            "lease_expires_at": lease_expires_at,
            "checkpoint_id": checkpoint_id,
        }
        recovery_event_id = f"evt-recovery-{lease_id.removeprefix('recovery-')[:8]}"
        matched_approval = _matching_approval(workflow, record.get("next_safe_action") or {}, now=observed_at)
        if matched_approval is not None:
            matched_approval["consumed_by_event_id"] = recovery_event_id
        workflow["active_recovery_lease"] = lease_payload
        store.write_pending_recovery(
            workflow_id,
            lease_id,
            {
                "schema_version": 1,
                "lease_id": lease_id,
                "event_id": recovery_event_id,
                "workflow_id": workflow_id,
                "recovery_action": "block_and_reconcile",
            },
        )
        store.append_event(
            workflow_id,
            {
                "schema_version": 1,
                "event_id": recovery_event_id,
                "workflow_id": workflow_id,
                "event_type": "recovery_lease_acquired",
                "created_at": acquired_at,
                "status_after": workflow.get("status"),
                "phase_after": workflow.get("phase"),
                "cursor_before": cursor,
                "cursor_after": cursor,
                "payload": {
                    "lease_id": lease_id,
                    "holder": holder,
                    "reason": record["reason"],
                    "next_safe_action": record["next_safe_action"],
                },
            },
            locked=True,
        )
        store.save_workflow(workflow)
        store.clear_pending_recovery(workflow_id, lease_id)
        return {
            "ok": True,
            "workflow_id": workflow_id,
            "recovered": True,
            "blocked": False,
            "reason": record["reason"],
            "lease": workflow["active_recovery_lease"],
            "recovery_packet": _recovery_packet(record),
        }


def _load_workflows(store: WorkflowStore) -> list[dict[str, Any]]:
    workflows_dir = store.root / "workflows"
    if not workflows_dir.exists():
        return []
    workflows: list[dict[str, Any]] = []
    for path in sorted(workflows_dir.glob("*/workflow.json")):
        try:
            workflows.append(store.load_workflow(path.parent.name))
        except Exception as exc:  # read-only scan should surface corrupt records.
            workflows.append(
                {
                    "workflow_id": path.parent.name,
                    "status": "blocked",
                    "phase": "unknown",
                    "last_activity_at": None,
                    "stale_after_seconds": 0,
                    "next_safe_action": {},
                    "context_manifest": [],
                    "side_effects_performed": [],
                    "approvals": [],
                    "active_recovery_lease": None,
                    "checkpoint_index": {},
                    "_load_error": str(exc),
                }
            )
    return workflows


def _classify_workflow(store: WorkflowStore, workflow: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    observed_at = now or datetime.now(timezone.utc)
    workflow_id = str(workflow.get("workflow_id"))
    status = workflow.get("status")
    action = workflow.get("next_safe_action") if isinstance(workflow.get("next_safe_action"), dict) else {}
    cursor = _cursor_for(workflow, action)
    last_checkpoint = _latest_checkpoint_id(workflow)
    reason = "clean"
    blocked = False
    needs_recovery = False
    severity = 9
    stale = _is_stale(workflow, now=observed_at)
    context_stale = _stale_context_refs(workflow)
    cursor_mismatch = _cursor_mismatch(workflow, action)
    checkpoint_mismatch = _checkpoint_mismatch_reason(store, workflow)
    recovery_mismatch = _recovery_transaction_mismatch_reason(store, workflow)
    pending_checkpoint = _has_pending_checkpoint(store, workflow_id)
    active_lease = _active_recovery_lease(workflow, now=observed_at)
    load_error = workflow.get("_load_error")
    if load_error:
        reason = "invalid_workflow"
        blocked = True
        needs_recovery = True
        severity = 0
    elif status not in RECOVERY_CANDIDATE_STATUSES:
        pass
    elif pending_checkpoint:
        reason = "pending_checkpoint"
        blocked = True
        needs_recovery = True
        severity = 0
    elif cursor_mismatch:
        reason = cursor_mismatch
        blocked = True
        needs_recovery = True
        severity = 0
    elif checkpoint_mismatch:
        reason = checkpoint_mismatch
        blocked = True
        needs_recovery = True
        severity = 0
    elif recovery_mismatch:
        reason = recovery_mismatch
        blocked = True
        needs_recovery = True
        severity = 0
    elif context_stale:
        reason = "context_manifest_stale"
        blocked = True
        needs_recovery = True
        severity = 0
    elif active_lease is not None:
        reason = "active_recovery_lease_exists"
        severity = 8
    elif status in TERMINAL_UNREPORTED_STATUSES:
        reason = "terminal_unreported"
        needs_recovery = True
        severity = 1
    elif (side_effect_block := _side_effect_block_reason(workflow, action, now=observed_at)):
        reason = side_effect_block
        blocked = True
        needs_recovery = True
        severity = 0
    elif status == "waiting_user" and _waiting_reminder_due(workflow, now=observed_at):
        reason = "waiting_user_reminder_due"
        needs_recovery = True
        severity = 2
    elif status in ACTIVE_STATUSES and stale:
        reason = "stale_active"
        needs_recovery = True
        severity = 3
    return {
        "workflow_id": workflow_id,
        "kind": workflow.get("kind"),
        "status": status,
        "phase": workflow.get("phase"),
        "cursor": cursor,
        "checkpoint_id": last_checkpoint,
        "last_activity_at": workflow.get("last_activity_at"),
        "stale": stale,
        "needs_recovery": needs_recovery,
        "blocked": blocked,
        "reason": reason,
        "severity": severity,
        "next_safe_action": action,
        "context_manifest_stale_refs": context_stale,
        "active_recovery_lease": workflow.get("active_recovery_lease"),
    }


def _recovery_packet(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "workflow_id": record["workflow_id"],
        "status": record["status"],
        "reason": record["reason"],
        "blocked": record["blocked"],
        "cursor": record["cursor"],
        "checkpoint_id": record["checkpoint_id"],
        "next_safe_action": record["next_safe_action"],
    }


def _cursor_for(workflow: dict[str, Any], action: dict[str, Any]) -> str:
    return str(action.get("cursor") or current_cursor(workflow) or "start")


def _latest_checkpoint_id(workflow: dict[str, Any]) -> str | None:
    rolling = (workflow.get("continuation_plan") or {}).get("rolling_state") or {}
    if isinstance(rolling.get("last_checkpoint_id"), str):
        return rolling["last_checkpoint_id"]
    checkpoint_index = workflow.get("checkpoint_index") or {}
    if checkpoint_index:
        return max(checkpoint_index, key=lambda item: checkpoint_index[item].get("checkpoint_seq", 0))
    return None


def _checkpoint_mismatch_reason(store: WorkflowStore, workflow: dict[str, Any]) -> str | None:
    workflow_id = str(workflow.get("workflow_id"))
    checkpoint_index = workflow.get("checkpoint_index") or {}
    rolling = (workflow.get("continuation_plan") or {}).get("rolling_state") or {}
    last_checkpoint_id = rolling.get("last_checkpoint_id")
    if isinstance(last_checkpoint_id, str) and last_checkpoint_id and last_checkpoint_id not in checkpoint_index:
        return "checkpoint_state_mismatch"
    if checkpoint_index and isinstance(last_checkpoint_id, str) and last_checkpoint_id:
        latest_checkpoint_id = max(checkpoint_index, key=lambda item: checkpoint_index[item].get("checkpoint_seq", 0))
        if latest_checkpoint_id != last_checkpoint_id:
            return "checkpoint_state_mismatch"
    events_path = store.workflow_dir(workflow_id) / "events.jsonl"
    events_by_checkpoint: dict[str, dict[str, Any]] = {}
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return "event_log_mismatch"
            checkpoint_id = event.get("checkpoint_id")
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if checkpoint_id and isinstance(payload.get("state_update"), dict):
                events_by_checkpoint[str(checkpoint_id)] = event
    worklog_path = store.workflow_dir(workflow_id) / "worklog.md"
    worklog_text = worklog_path.read_text(encoding="utf-8") if worklog_path.exists() else ""
    for checkpoint_id, checkpoint in checkpoint_index.items():
        event = events_by_checkpoint.get(checkpoint_id)
        if event is None:
            return "event_log_mismatch"
        if event.get("event_id") != checkpoint.get("event_id"):
            return "event_log_mismatch"
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if payload.get("worklog_block_id") != checkpoint.get("worklog_block_id"):
            return "event_log_mismatch"
        if f"## Checkpoint {checkpoint_id}" not in worklog_text or f"- Event: {checkpoint.get('event_id')}" not in worklog_text:
            return "worklog_mismatch"
    if checkpoint_index and not worklog_text:
        return "worklog_mismatch"
    if any(checkpoint_id not in events_by_checkpoint for checkpoint_id in checkpoint_index):
        return "event_log_mismatch"
    return None


def _cursor_mismatch(workflow: dict[str, Any], action: dict[str, Any]) -> str | None:
    action_cursor = action.get("cursor")
    if not action_cursor:
        return "missing_next_safe_action_cursor"
    expected = current_cursor(workflow)
    if expected and action_cursor != expected:
        return "next_safe_action_cursor_mismatch"
    return None


def _stale_context_refs(workflow: dict[str, Any]) -> list[str]:
    stale = []
    for entry in workflow.get("context_manifest") or []:
        if not isinstance(entry, dict) or not validate_manifest_entry(entry):
            stale.append(str(entry.get("ref") if isinstance(entry, dict) else entry))
    return stale


def _matching_approval(workflow: dict[str, Any], action: dict[str, Any], *, now: datetime) -> dict[str, Any] | None:
    side_effect_key = action.get("side_effect_key")
    scope = str(action.get("approval_ref") or action.get("cursor") or "")
    for approval in workflow.get("approvals") or []:
        if isinstance(approval, dict) and approval_matches(
            approval,
            side_effect_key=side_effect_key,
            scope=scope,
            now=now,
        ):
            return approval
    return None


def _side_effect_block_reason(workflow: dict[str, Any], action: dict[str, Any], *, now: datetime) -> str | None:
    risk = action.get("risk_class")
    if risk not in RISK_CLASSES:
        return "invalid_risk_class"
    side_effect_key = action.get("side_effect_key")
    if not isinstance(side_effect_key, str) or not side_effect_key:
        return "missing_side_effect_key"
    if risk in RISKY_CLASSES or action.get("requires_approval"):
        if _matching_approval(workflow, action, now=now) is None:
            return "risky_side_effect_requires_approval"
    duplicate = any(
        isinstance(item, dict) and item.get("side_effect_key") == side_effect_key
        for item in workflow.get("side_effects_performed") or []
    )
    if duplicate and action.get("idempotency_policy") != "repeatable":
        return "side_effect_reconcile_required"
    return None


def _has_pending_checkpoint(store: WorkflowStore, workflow_id: str) -> bool:
    return bool(store.pending_checkpoints(workflow_id))


def _recovery_transaction_mismatch_reason(store: WorkflowStore, workflow: dict[str, Any]) -> str | None:
    workflow_id = str(workflow.get("workflow_id"))
    if store.pending_recoveries(workflow_id):
        return "pending_recovery_lease"
    active_lease = workflow.get("active_recovery_lease")
    active_lease_id = active_lease.get("lease_id") if isinstance(active_lease, dict) else None
    acquired: set[str] = set()
    consumed: set[str] = set()
    events_path = store.workflow_dir(workflow_id) / "events.jsonl"
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return "event_log_mismatch"
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if event.get("event_type") == "recovery_lease_acquired":
                lease_id = payload.get("lease_id")
                if isinstance(lease_id, str) and lease_id:
                    acquired.add(lease_id)
            if event.get("event_type") == "lease_released":
                lease_id = payload.get("lease_id")
                if isinstance(lease_id, str) and lease_id:
                    consumed.add(lease_id)
            state_update = payload.get("state_update") if isinstance(payload.get("state_update"), dict) else {}
            lease_id = state_update.get("recovery_lease_id")
            if isinstance(lease_id, str) and lease_id:
                consumed.add(lease_id)
    if consumed - acquired:
        return "recovery_lease_transaction_mismatch"
    if active_lease_id and active_lease_id not in acquired:
        return "recovery_lease_transaction_mismatch"
    unbound = acquired - consumed
    if active_lease_id:
        unbound.discard(active_lease_id)
    if unbound:
        return "recovery_lease_transaction_mismatch"
    return None


def _active_recovery_lease(workflow: dict[str, Any], *, now: datetime) -> dict[str, Any] | None:
    lease = workflow.get("active_recovery_lease")
    if isinstance(lease, dict) and not _is_expired(lease.get("lease_expires_at"), now=now):
        return lease
    return None


def _expired_recovery_lease(workflow: dict[str, Any], *, now: datetime) -> dict[str, Any] | None:
    lease = workflow.get("active_recovery_lease")
    if not isinstance(lease, dict):
        return None
    lease_id = lease.get("lease_id")
    if not isinstance(lease_id, str) or not lease_id:
        return None
    if _is_expired(lease.get("lease_expires_at"), now=now):
        return lease
    return None


def _is_stale(workflow: dict[str, Any], *, now: datetime) -> bool:
    last = _parse_iso(workflow.get("last_activity_at"))
    if last is None:
        return True
    return now - last > timedelta(seconds=int(workflow.get("stale_after_seconds") or 0))


def _waiting_reminder_due(workflow: dict[str, Any], *, now: datetime) -> bool:
    last_visible = _parse_iso(workflow.get("last_visible_update_at")) or _parse_iso(workflow.get("last_activity_at"))
    if last_visible is None:
        return True
    return now - last_visible > timedelta(seconds=int(workflow.get("reminder_after_seconds") or 0))


def _is_expired(value: str | None, *, now: datetime) -> bool:
    expires_at = _parse_iso(value)
    if expires_at is None:
        return True
    return expires_at <= now


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso_after(seconds: int, *, now: datetime) -> str:
    return (now + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
