"""Atomic checkpoint primitive."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import re
import uuid
from pathlib import Path
from typing import Any

from .acceptance import matches_workflow_scope
from .artifacts import manifest_entry, now_iso, sha256_file, validate_manifest_entry
from .continuation import apply_cursor_transition, validate_cursor_transition
from .messages import checkpoint_block, lint_verdict_residuals, normalize_residuals
from .schema import validate_named, validate_next_safe_action
from .store import WorkflowStore, structured_next_action


IDEMPOTENCY_POLICIES = {"repeatable", "reconcile_first", "never_repeat_without_approval"}
RISKY_SIDE_EFFECT_PREFIXES = (
    "external:",
    "external-",
    "gateway:",
    "gateway-",
    "public:",
    "public-",
    "destructive:",
    "destructive-",
)
ALLOWED_SIDE_EFFECT_PREFIXES = ("local:", "inspect:", "visible-report:", "owner-decision:")
TERMINAL_STATUSES = {"completed_unreported", "failed_unreported", "reported", "abandoned"}
RESERVED_OUTPUT_STATUSES = {"reported", "abandoned"}
ACTIVE_WAIT_STATUSES = {"waiting_user", "waiting_subagent", "verifying"}


def validate_evidence_object(evidence: dict[str, Any]) -> None:
    required = {"evidence_key", "kind", "summary", "artifact_refs"}
    missing = sorted(required - set(evidence))
    if missing:
        raise ValueError(f"checkpoint evidence is missing required fields: {missing!r}")
    for key in ("evidence_key", "kind", "summary"):
        if not isinstance(evidence[key], str) or not evidence[key]:
            raise ValueError(f"checkpoint evidence {key} must be a non-empty string")
    if not isinstance(evidence["artifact_refs"], list):
        raise ValueError("checkpoint evidence artifact_refs must be an array")
    if not all(isinstance(item, str) and item for item in evidence["artifact_refs"]):
        raise ValueError("checkpoint evidence artifact_refs must contain non-empty strings")


def _effective_evidence(state_update: dict[str, Any], evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    terminal_evidence = state_update.get("terminal_evidence")
    if terminal_evidence is not None and not isinstance(terminal_evidence, dict):
        raise ValueError("terminal_evidence must be an object")
    if evidence and terminal_evidence:
        raise ValueError("provide terminal evidence either in state_update.terminal_evidence or --evidence, not both")
    return evidence or terminal_evidence


def _validate_evidence(state_update: dict[str, Any], evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    effective_evidence = _effective_evidence(state_update, evidence)
    if state_update["checkpoint_type"] == "terminal" and state_update["event_type"] == "fail":
        failure_reason = state_update.get("failure_reason")
        if not isinstance(failure_reason, str) or not failure_reason:
            raise ValueError("failed terminal checkpoint requires failure_reason")
        if effective_evidence:
            validate_evidence_object(effective_evidence)
        return effective_evidence
    if state_update["step_result"] in {"waiting", "blocked", "failed"}:
        if effective_evidence:
            validate_evidence_object(effective_evidence)
        return effective_evidence
    if not effective_evidence:
        raise ValueError("checkpoint evidence is required unless the step is waiting, blocked, or failed")
    validate_evidence_object(effective_evidence)
    return effective_evidence


def validate_evidence_artifact_refs(workflow: dict[str, Any], evidence: dict[str, Any] | None, *, worklog_text: str | None = None) -> None:
    if not evidence:
        return
    artifacts = {
        item.get("artifact_id"): item
        for item in workflow.get("artifacts", [])
        if isinstance(item, dict) and isinstance(item.get("artifact_id"), str)
    }
    for ref in evidence.get("artifact_refs") or []:
        artifact = artifacts.get(ref)
        if artifact is not None:
            _validate_materialized_artifact_ref(artifact, ref)
            continue
        if ref.startswith("worklog.md#"):
            _validate_worklog_ref(ref, worklog_text)
            continue
        if "/" in ref:
            artifact = artifacts.get(ref.split("/", 1)[0])
            if artifact is not None:
                _validate_materialized_artifact_ref(artifact, ref)
                continue
        raise ValueError(f"checkpoint evidence artifact_ref is not registered: {ref!r}")


def _validate_worklog_ref(ref: str, worklog_text: str | None) -> None:
    anchor = ref.split("#", 1)[1]
    if not anchor:
        raise ValueError(f"checkpoint evidence worklog artifact_ref has empty anchor: {ref!r}")
    if not worklog_text:
        raise ValueError(f"checkpoint evidence worklog artifact_ref is not found: {ref!r}")
    anchors = {
        _markdown_anchor(match.group(1))
        for match in re.finditer(r"^#{1,6}\s+(.+?)\s*$", worklog_text, flags=re.MULTILINE)
    }
    if anchor not in anchors:
        raise ValueError(f"checkpoint evidence worklog artifact_ref is not found: {ref!r}")


def _markdown_anchor(heading: str) -> str:
    anchor = heading.strip().lower()
    anchor = re.sub(r"[^\w\s-]", "", anchor)
    anchor = re.sub(r"[\s_]+", "-", anchor)
    anchor = re.sub(r"-+", "-", anchor)
    return anchor.strip("-")


def _validate_materialized_artifact_ref(artifact: dict[str, Any], ref: str) -> None:
    artifact_hash = artifact.get("sha256")
    artifact_path = artifact.get("path")
    if not isinstance(artifact_hash, str) or not artifact_hash:
        raise ValueError(f"checkpoint evidence artifact_ref is not materialized: {ref!r}")
    if not isinstance(artifact_path, str) or not artifact_path:
        raise ValueError(f"checkpoint evidence artifact_ref has no path: {ref!r}")
    path = Path(artifact_path)
    if not path.is_file():
        raise ValueError(f"checkpoint evidence artifact_ref is not materialized: {ref!r}")
    if sha256_file(path) != artifact_hash:
        raise ValueError(f"checkpoint evidence artifact_ref is stale: {ref!r}")


def _validate_final_status_object(final_status: dict[str, Any], state_update: dict[str, Any]) -> None:
    verdict = final_status.get("result")
    if not isinstance(verdict, str) or not verdict:
        raise ValueError("terminal checkpoint final_status requires result")
    update_residuals = normalize_residuals(state_update.get("residuals") or {})
    final_residuals = final_status.get("residuals")
    if final_residuals is None and not any(update_residuals.values()):
        final_residuals = {}
    elif final_residuals is None:
        raise ValueError("terminal checkpoint final_status.residuals must match checkpoint residuals")
    final_residuals = normalize_residuals(final_residuals)
    if final_residuals != update_residuals:
        raise ValueError("terminal checkpoint final_status.residuals must match checkpoint residuals")
    lint_verdict_residuals(verdict, final_residuals)
    if state_update["event_type"] == "complete" and verdict not in {"pass", "pass_with_risks"}:
        raise ValueError("complete terminal checkpoints require pass or pass_with_risks final_status")
    if state_update["event_type"] == "fail" and verdict not in {"needs_fix", "blocked", "stopped"}:
        raise ValueError("failed terminal checkpoints require needs_fix, blocked, or stopped final_status")


def _validate_workflow_can_checkpoint(workflow: dict[str, Any], state_update: dict[str, Any]) -> None:
    status = workflow.get("status")
    status_after = state_update["status_after"]
    if status in TERMINAL_STATUSES:
        raise ValueError(f"checkpoint cannot continue workflow in terminal status: {status!r}")
    if status_after in RESERVED_OUTPUT_STATUSES:
        raise ValueError("reported and abandoned statuses require dedicated report or abandon flow")
    if status != "draft" and status_after == "draft":
        raise ValueError("active workflows cannot return to draft through checkpoint")
    if status == "draft" and status_after != "running":
        raise ValueError("draft workflows can only move to running through checkpoint")
    if status in ACTIVE_WAIT_STATUSES and status_after not in {status, "running", "failed_unreported"}:
        raise ValueError(f"{status} workflows can only remain waiting, resume running, or fail through checkpoint")


def _validate_next_action(workflow: dict[str, Any], state_update: dict[str, Any], next_action: dict[str, Any] | None) -> None:
    if next_action is not None:
        validate_next_safe_action(next_action, "$.next_action")
        if next_action.get("cursor") != state_update["cursor_after"]:
            raise ValueError("next_action.cursor must match cursor_after")
        if state_update["checkpoint_type"] == "terminal" and next_action.get("action_type") != "report_terminal_status":
            raise ValueError("terminal checkpoints require report_terminal_status next_action")
        if state_update["status_after"] == "blocked" and next_action.get("action_type") != "owner_decision_or_rescope":
            raise ValueError("blocked checkpoints require owner_decision_or_rescope next_action")
    if not isinstance(workflow.get("continuation_plan"), dict):
        return
    if state_update["step_result"] == "passed":
        if state_update["checkpoint_type"] != "advance":
            raise ValueError("passed continuation steps require advance checkpoint_type")
        if state_update["cursor_after"] == state_update["cursor_before"]:
            raise ValueError("passed continuation steps must advance cursor")
    if next_action is None:
        return
    next_cursor = next_action.get("cursor")
    if next_cursor != state_update["cursor_after"]:
        raise ValueError("next_action.cursor must match cursor_after")


def _default_next_action(workflow_id: str, checkpoint_id: str, state_update: dict[str, Any]) -> dict[str, Any]:
    cursor = state_update["cursor_after"]
    status_after = state_update["status_after"]
    if state_update["checkpoint_type"] == "terminal":
        return structured_next_action(
            action_type="report_terminal_status",
            summary="Send the terminal visible report and record report proof before marking reported.",
            cursor=cursor,
            risk_class="external",
            requires_approval=False,
            side_effect_key=f"visible-report:{workflow_id}:{checkpoint_id}",
            idempotency_policy="never_repeat_without_approval",
            expected_artifacts=["workflow.json", "worklog.md"],
        )
    if status_after == "blocked":
        return structured_next_action(
            action_type="owner_decision_or_rescope",
            summary="Wait for an explicit owner decision or rescope event before continuing.",
            cursor=cursor,
            risk_class="read_only",
            requires_approval=True,
            side_effect_key=f"owner-decision:{workflow_id}:{cursor}",
            idempotency_policy="reconcile_first",
            expected_artifacts=["events.jsonl", "workflow.json"],
        )
    if status_after == "waiting_user":
        action_type = "wait_for_owner"
        summary = "Wait for owner input before continuing."
    elif status_after == "waiting_subagent":
        action_type = "wait_for_subagent"
        summary = "Wait for subagent result before continuing."
    elif status_after == "verifying":
        action_type = "run_verification"
        summary = "Run the verification gate for the current cursor."
    else:
        action_type = "continue"
        summary = "Continue from the current cursor."
    return structured_next_action(
        action_type=action_type,
        summary=summary,
        cursor=cursor,
        risk_class="read_only",
        requires_approval=False,
        side_effect_key=f"{action_type}:{workflow_id}:{cursor}",
        idempotency_policy="repeatable",
        expected_artifacts=["workflow.json", "worklog.md"],
    )


def _apply_context_manifest_updates(workflow: dict[str, Any], state_update: dict[str, Any]) -> None:
    for update in state_update.get("context_manifest_updates") or []:
        if not isinstance(update, dict):
            raise ValueError("context manifest updates must be objects")
        unsupported = sorted(set(update) - {"action", "path", "recovery_policy"})
        if unsupported:
            raise ValueError(f"unsupported context manifest update fields: {unsupported!r}")
        action = update.get("action", "capture")
        if action not in {"capture", "refresh", "remove"}:
            raise ValueError(f"unsupported context manifest action: {action!r}")
        path_value = update.get("path")
        if not isinstance(path_value, str) or not path_value:
            raise ValueError("context manifest update requires path")
        manifest = workflow.setdefault("context_manifest", [])
        resolved = str(Path(path_value).expanduser().resolve())
        manifest[:] = [item for item in manifest if item.get("ref") != resolved]
        if action != "remove":
            manifest.append(manifest_entry(Path(path_value), recovery_policy=update.get("recovery_policy", "block_on_change")))


def _apply_mode_state_update(workflow: dict[str, Any], state_update: dict[str, Any]) -> None:
    mode_update = state_update.get("mode_state_update")
    if mode_update is None:
        return
    if not isinstance(mode_update, dict):
        raise ValueError("mode_state_update must be an object")
    state_key = f"{workflow.get('kind')}_state"
    if state_key not in workflow:
        raise ValueError(f"workflow is missing active mode state: {state_key}")
    current = workflow[state_key]
    if not isinstance(current, dict):
        raise ValueError(f"workflow {state_key} must be an object")
    if state_update.get("checkpoint_type") == "terminal":
        if not mode_update:
            return
        workflow[state_key] = dict(mode_update)
        return
    current.update(mode_update)


def _apply_side_effects(workflow: dict[str, Any], state_update: dict[str, Any]) -> None:
    registry = workflow.setdefault("side_effects_performed", [])
    for side_effect in state_update.get("side_effects") or []:
        _validate_side_effect(side_effect)
        identity = side_effect["side_effect_key"]
        duplicate = any(isinstance(item, dict) and item.get("side_effect_key") == identity for item in registry)
        if duplicate and side_effect["idempotency_policy"] != "repeatable":
            raise ValueError(f"repeated side_effect_key requires repeatable policy: {identity!r}")
        registry.append(side_effect)


def _validate_side_effect(side_effect: Any) -> None:
    if not isinstance(side_effect, dict):
        raise ValueError("side_effects entries must be objects")
    key = side_effect.get("side_effect_key")
    if not isinstance(key, str) or not key:
        raise ValueError("side_effects entries require non-empty side_effect_key")
    if key.startswith(RISKY_SIDE_EFFECT_PREFIXES):
        raise ValueError("risky side_effect_key prefixes require an explicit approval contract in a later slice")
    if not key.startswith(ALLOWED_SIDE_EFFECT_PREFIXES):
        raise ValueError("side_effect_key must use an approved local/reporting prefix until approval contracts exist")
    policy = side_effect.get("idempotency_policy")
    if policy not in IDEMPOTENCY_POLICIES:
        raise ValueError(f"side_effects entries require idempotency_policy in {sorted(IDEMPOTENCY_POLICIES)!r}")


def _context_update_refs(state_update: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for update in state_update.get("context_manifest_updates") or []:
        path_value = update.get("path") if isinstance(update, dict) else None
        if isinstance(path_value, str) and path_value:
            refs.add(str(Path(path_value).expanduser().resolve()))
    return refs


def _validate_context_manifest(workflow: dict[str, Any], allowed_stale_refs: set[str] | None = None) -> None:
    allowed_stale_refs = allowed_stale_refs or set()
    stale = [
        entry.get("ref", "<unknown>")
        for entry in workflow.get("context_manifest", [])
        if entry.get("ref") not in allowed_stale_refs and not validate_manifest_entry(entry)
    ]
    if stale:
        raise ValueError(f"context manifest is stale: {stale!r}")


def _validate_recovery_lease(workflow: dict[str, Any], state_update: dict[str, Any]) -> None:
    lease = workflow.get("active_recovery_lease")
    has_recovery_args = bool(state_update.get("recovery_lease_id") or state_update.get("recovery_lease_holder"))
    if not isinstance(lease, dict):
        if has_recovery_args:
            raise ValueError("recovery lease args require an active recovery lease")
        return
    if not state_update.get("recovery_lease_id") or not state_update.get("recovery_lease_holder"):
        raise ValueError("active recovery lease requires matching recovery_lease_id and recovery_lease_holder")
    expires_at = _parse_utc(lease.get("lease_expires_at"))
    if expires_at <= datetime.now(timezone.utc):
        raise ValueError("active recovery lease expired; run recover again")
    lease_cursor = lease.get("cursor")
    if lease_cursor != state_update["cursor_before"]:
        raise ValueError("active recovery lease does not match current cursor")
    if state_update.get("recovery_lease_id") != lease.get("lease_id"):
        raise ValueError("active recovery lease requires matching recovery_lease_id")
    if state_update.get("recovery_lease_holder") != lease.get("holder"):
        raise ValueError("active recovery lease requires matching recovery_lease_holder")


def _validate_terminal_mode_state_update(workflow: dict[str, Any], state_update: dict[str, Any]) -> None:
    if state_update.get("checkpoint_type") != "terminal":
        return
    kind = workflow.get("kind")
    if kind not in {"plan", "verify", "conv", "goal"}:
        return
    if "mode_state_update" not in state_update:
        return
    state = state_update.get("mode_state_update")
    if not isinstance(state, dict):
        raise ValueError(f"terminal {kind} checkpoint mode_state_update must be an object")
    current_state = workflow.get(f"{kind}_state")
    if not isinstance(current_state, dict):
        raise ValueError(f"workflow is missing active mode state: {kind}_state")
    candidate_state = state if state else current_state
    final_status = state_update.get("final_status")
    if not isinstance(final_status, dict):
        raise ValueError(f"terminal {kind} checkpoint requires final_status object")
    terminal_workflow = deepcopy(workflow)
    terminal_workflow[f"{kind}_state"] = candidate_state
    terminal_workflow["final_status"] = final_status
    terminal_workflow["status"] = state_update.get("status_after")
    if kind == "plan":
        from .modes.plan import validate_plan_state

        validate_plan_state(candidate_state, terminal=True, final_status=final_status)
    elif kind == "verify":
        from .modes.verify import validate_verify_state

        validate_verify_state(candidate_state, terminal=True, final_status=final_status)
    elif kind == "conv":
        from .modes.conv import validate_conv_state

        validate_conv_state(candidate_state, terminal=True, final_status=final_status)
    elif kind == "goal":
        from .modes.goal import validate_goal_state

        validate_goal_state(candidate_state, workflow=terminal_workflow, terminal=True, final_status=final_status)


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


def _validate_blocked_unblock(store: WorkflowStore, workflow_id: str, workflow: dict[str, Any], state_update: dict[str, Any]) -> None:
    if workflow.get("status") != "blocked" or state_update["status_after"] == "blocked":
        return
    events_path = store.workflow_dir(workflow_id) / "events.jsonl"
    if not events_path.exists():
        raise ValueError("blocked workflows require owner_decision or rescope before progress")
    decision_after_latest_block = False
    for line in events_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("event_type") in {"checkpoint", "advance", "complete", "fail"} and event.get("status_after") == "blocked":
            decision_after_latest_block = False
            continue
        if event.get("event_type") in {"checkpoint", "advance", "complete", "fail"} and event.get("status_after") != "blocked":
            decision_after_latest_block = False
            continue
        if event.get("event_type") == "owner_decision" and matches_workflow_scope(event, workflow):
            decision_after_latest_block = True
        if event.get("event_type") == "rescope":
            decision_after_latest_block = True
    if decision_after_latest_block:
        return
    raise ValueError("blocked workflows require matching owner_decision or rescope before progress")


def record_checkpoint(
    store: WorkflowStore,
    *,
    workflow_id: str,
    checkpoint_type: str,
    state_update: dict[str, Any],
    summary: str,
    next_action: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    validate_named(state_update, "checkpoint_state_update.schema.json")
    if checkpoint_type != state_update["checkpoint_type"]:
        raise ValueError("CLI checkpoint_type does not match state_update.checkpoint_type")
    effective_evidence = _validate_evidence(state_update, evidence)

    with store.lock(workflow_id):
        store.require_no_pending_checkpoint(workflow_id)
        store.require_no_pending_recovery(workflow_id)
        workflow = store.load_workflow(workflow_id)
        worklog_path = store.workflow_dir(workflow_id) / "worklog.md"
        worklog_text = worklog_path.read_text(encoding="utf-8") if worklog_path.exists() else ""
        _validate_workflow_can_checkpoint(workflow, state_update)
        _validate_blocked_unblock(store, workflow_id, workflow, state_update)
        _validate_context_manifest(workflow, _context_update_refs(state_update))
        _validate_recovery_lease(workflow, state_update)
        validate_cursor_transition(
            workflow,
            state_update["cursor_before"],
            state_update["cursor_after"],
            state_update["step_result"],
        )
        _validate_next_action(workflow, state_update, next_action)
        validate_evidence_artifact_refs(workflow, effective_evidence, worklog_text=worklog_text)
        _validate_terminal_mode_state_update(workflow, state_update)

        checkpoint_id = f"chk-{uuid.uuid4().hex[:12]}"
        event_id = f"evt-{uuid.uuid4().hex[:12]}"
        created_at = now_iso()
        checkpoint_seq = len(workflow.get("checkpoint_index", {})) + 1
        worklog_block_id = f"worklog-{checkpoint_id}"

        workflow["status"] = state_update["status_after"]
        workflow["phase"] = state_update["phase_after"]
        workflow["next_safe_action"] = next_action or _default_next_action(workflow_id, checkpoint_id, state_update)
        if state_update["checkpoint_type"] == "terminal":
            final_status = state_update.get("final_status")
            if not isinstance(final_status, dict):
                raise ValueError("terminal checkpoint requires final_status")
            _validate_final_status_object(final_status, state_update)
            workflow["final_status"] = final_status
        if effective_evidence:
            workflow.setdefault("verification", {}).setdefault("evidence", []).append(effective_evidence)
        _apply_mode_state_update(workflow, state_update)
        _apply_context_manifest_updates(workflow, state_update)
        _apply_side_effects(workflow, state_update)
        apply_cursor_transition(
            workflow,
            checkpoint_id,
            state_update["cursor_after"],
            state_update["step_result"],
        )
        lease = workflow.get("active_recovery_lease")
        if isinstance(lease, dict) and lease.get("cursor") == state_update["cursor_before"]:
            workflow["active_recovery_lease"] = None
        workflow.setdefault("checkpoint_index", {})[checkpoint_id] = {
            "checkpoint_id": checkpoint_id,
            "checkpoint_seq": checkpoint_seq,
            "checkpoint_type": checkpoint_type,
            "cursor_before": state_update["cursor_before"],
            "cursor_after": state_update["cursor_after"],
            "event_id": event_id,
            "worklog_block_id": worklog_block_id,
            "created_at": created_at,
            "status_after": state_update["status_after"],
            "phase_after": state_update["phase_after"],
        }
        event = {
            "schema_version": 1,
            "event_id": event_id,
            "workflow_id": workflow_id,
            "event_type": state_update["event_type"],
            "created_at": created_at,
            "checkpoint_id": checkpoint_id,
            "status_after": state_update["status_after"],
            "phase_after": state_update["phase_after"],
            "cursor_before": state_update["cursor_before"],
            "cursor_after": state_update["cursor_after"],
            "payload": {
                "checkpoint_seq": checkpoint_seq,
                "worklog_block_id": worklog_block_id,
                "state_update": state_update,
            },
        }
        if effective_evidence:
            event["payload"]["evidence"] = effective_evidence
        validate_named(workflow, "workflow.schema.json")
        validate_named(event, "event.schema.json")
        worklog_block = checkpoint_block(summary, state_update, checkpoint_id, event_id)
        store.write_pending_checkpoint(
            workflow_id,
            checkpoint_id,
            {
                "schema_version": 1,
                "checkpoint_id": checkpoint_id,
                "event_id": event_id,
                "created_at": created_at,
                "workflow_id": workflow_id,
                "recovery_action": "block_and_reconcile",
            },
        )
        store.append_event(workflow_id, event, locked=True)
        store.append_worklog(workflow_id, worklog_block)
        store.save_workflow(workflow)
        if store.pending_checkpoint_path(workflow_id, checkpoint_id).exists():
            store.clear_pending_checkpoint(workflow_id, checkpoint_id)

    return {
        "checkpoint_id": checkpoint_id,
        "event_id": event_id,
        "checkpoint_seq": checkpoint_seq,
        "workflow_id": workflow_id,
    }
