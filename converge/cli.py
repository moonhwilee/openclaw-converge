#!/usr/bin/env python3
"""Converge local runtime CLI."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .acceptance import validate_acceptance_payload
from .checkpoint import record_checkpoint, validate_evidence_artifact_refs, validate_evidence_object
from .command_adapter import build_dry_run_packet
from .continuation import TERMINAL_CONTINUATION_TARGETS, current_cursor, default_continuation_plan
from .messages import VALID_VERDICTS, lint_verdict_residuals, normalize_residuals, progress_block
from .modes.conv import CONV_REPORT_ARTIFACT_ID, ConvHandler, ConvRecord, ConvRound, render_conv_report, validate_conv_state
from .modes.conv_execution import CONV_LOCAL_RUNNER_REF, CONV_ROUND_EXECUTION_ARTIFACT_ID
from .modes.evidence_contract import validate_phase5a_evidence_contract
from .modes.goal import (
    GOAL_PLAN_ARTIFACT_ID,
    PHASE5B_PARENT_SUMMARY_MODE,
    PHASE5B_OWNER_WAIVER_MODE,
    GoalHandler,
    GoalRecord,
    phase5b_child_delivery_mode,
    render_goal_plan,
    validate_goal_state,
    validate_phase5b_child_delivery_state,
)
from .modes.plan import PlanHandler
from .modes.verify import VERIFY_REPORT_ARTIFACT_ID, VerifyHandler, VerifyRecord, render_verify_report
from .modes.specialist_panel import SPECIALIST_REVIEW_RUNNER_REF, load_specialist_packet, specialist_artifact_id, validate_specialist_state
from .recovery import recover_workflow, scan_workflows, watchdog_check
from .artifacts import now_iso, record_workflow_artifact, sha256_file, validate_manifest_entry
from .schema import SchemaError, validate_bundled_schemas, validate_named, validate_next_safe_action
from .store import WorkflowStore, structured_next_action


SUPPORTED_MANUAL_EVENT_TYPES = ("owner_decision", "plan_accepted", "progress", "rescope")


class DeliveryValidationError(ValueError):
    """Expected pre-send delivery validation failure."""


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        print_json({"ok": False, "error": message})
        raise SystemExit(2)


def parse_json(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if not isinstance(payload, dict):
        raise argparse.ArgumentTypeError("expected JSON object")
    return payload


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def resolve_summary(args: argparse.Namespace) -> str:
    if getattr(args, "summary_file", None):
        return Path(args.summary_file).read_text(encoding="utf-8").strip()
    if not args.summary:
        raise ValueError("summary or summary_file is required")
    return args.summary


def cmd_start(args: argparse.Namespace) -> int:
    if args.visible_delivery:
        _validate_visible_delivery_arg(args.visible_delivery)
    store = WorkflowStore(args.state_root)
    workflow = store.create_workflow(
        kind=args.kind,
        text=args.text,
        workflow_id=args.workflow_id,
        owner_session_key=args.owner_session_key or "",
        visible_delivery=args.visible_delivery or {},
    )
    print_json({"ok": True, "workflow": workflow})
    return 0


def cmd_mode_start(args: argparse.Namespace) -> int:
    args.kind = args.mode_kind
    return cmd_start(args)


def cmd_plan(args: argparse.Namespace) -> int:
    if args.visible_delivery:
        _validate_visible_delivery_arg(args.visible_delivery)
    store = WorkflowStore(args.state_root)
    _validate_recovery_resume_target(args, store)
    try:
        workflow = store.create_workflow(
            kind="plan",
            text=args.text,
            workflow_id=args.workflow_id,
            owner_session_key=args.owner_session_key or "",
            visible_delivery=args.visible_delivery or {},
        )
    except FileExistsError:
        if not args.workflow_id:
            raise
        workflow = store.load_workflow(args.workflow_id)
        if workflow.get("kind") != "plan":
            raise ValueError(f"plan command cannot resume {workflow.get('kind')!r} workflow")
        if workflow.get("status") in {"completed_unreported", "reported"}:
            print_json({"ok": True, "workflow": workflow, "result": "already_finalized"})
            return 0
    plan = PlanHandler(store).finalize_plan(
        workflow["workflow_id"],
        recovery_lease_id=args.recovery_lease_id,
        recovery_lease_holder=args.recovery_lease_holder,
    )
    workflow = store.load_workflow(workflow["workflow_id"])
    print_json({"ok": True, "workflow": workflow, **plan})
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    if args.visible_delivery:
        _validate_visible_delivery_arg(args.visible_delivery)
    store = WorkflowStore(args.state_root)
    _validate_recovery_resume_target(args, store)
    try:
        workflow = store.create_workflow(
            kind="verify",
            text=args.text,
            workflow_id=args.workflow_id,
            owner_session_key=args.owner_session_key or "",
            visible_delivery=args.visible_delivery or {},
        )
    except FileExistsError:
        if not args.workflow_id:
            raise
        workflow = store.load_workflow(args.workflow_id)
        if workflow.get("kind") != "verify":
            raise ValueError(f"verify command cannot resume {workflow.get('kind')!r} workflow")
        if workflow.get("status") in {"completed_unreported", "reported"}:
            print_json({"ok": True, "workflow": workflow, "result": "already_finalized"})
            return 0
    verify = VerifyHandler(store).finalize_verify(
        workflow["workflow_id"],
        specialist_findings=load_specialist_packet(getattr(args, "structured_findings_file", None)),
        recovery_lease_id=args.recovery_lease_id,
        recovery_lease_holder=args.recovery_lease_holder,
    )
    workflow = store.load_workflow(workflow["workflow_id"])
    print_json({"ok": True, "workflow": workflow, **verify})
    return 0


def cmd_conv(args: argparse.Namespace) -> int:
    if args.visible_delivery:
        _validate_visible_delivery_arg(args.visible_delivery)
    store = WorkflowStore(args.state_root)
    _validate_recovery_resume_target(args, store)
    try:
        workflow = store.create_workflow(
            kind="conv",
            text=args.text,
            workflow_id=args.workflow_id,
            owner_session_key=args.owner_session_key or "",
            visible_delivery=args.visible_delivery or {},
        )
    except FileExistsError:
        if not args.workflow_id:
            raise
        workflow = store.load_workflow(args.workflow_id)
        if workflow.get("kind") != "conv":
            raise ValueError(f"conv command cannot resume {workflow.get('kind')!r} workflow")
        if workflow.get("status") in {"completed_unreported", "reported"}:
            print_json({"ok": True, "workflow": workflow, "result": "already_finalized"})
            return 0
    conv = ConvHandler(store).finalize_conv(
        workflow["workflow_id"],
        specialist_findings=load_specialist_packet(getattr(args, "structured_findings_file", None)),
        recovery_lease_id=args.recovery_lease_id,
        recovery_lease_holder=args.recovery_lease_holder,
    )
    workflow = store.load_workflow(workflow["workflow_id"])
    print_json({"ok": True, "workflow": workflow, **conv})
    return 0


def cmd_goal(args: argparse.Namespace) -> int:
    if args.visible_delivery:
        _validate_visible_delivery_arg(args.visible_delivery)
    store = WorkflowStore(args.state_root)
    _validate_recovery_resume_target(args, store)
    try:
        workflow = store.create_workflow(
            kind="goal",
            text=args.text,
            workflow_id=args.workflow_id,
            owner_session_key=args.owner_session_key or "",
            visible_delivery=args.visible_delivery or {},
        )
    except FileExistsError:
        if not args.workflow_id:
            raise
        workflow = store.load_workflow(args.workflow_id)
        if workflow.get("kind") != "goal":
            raise ValueError(f"goal command cannot resume {workflow.get('kind')!r} workflow")
        if workflow.get("status") in {"completed_unreported", "reported"}:
            print_json({"ok": True, "workflow": workflow, "result": "already_finalized"})
            return 0
    goal = GoalHandler(store).finalize_goal(
        workflow["workflow_id"],
        recovery_lease_id=args.recovery_lease_id,
        recovery_lease_holder=args.recovery_lease_holder,
    )
    workflow = store.load_workflow(workflow["workflow_id"])
    print_json({"ok": True, "workflow": workflow, **goal})
    return 0


def cmd_command_dry_run(args: argparse.Namespace) -> int:
    if args.visible_delivery:
        _validate_visible_delivery_arg(args.visible_delivery)
    print_json(
        build_dry_run_packet(
            raw_message=args.raw_message,
            owner_session_key=args.owner_session_key or "",
            visible_delivery=args.visible_delivery or {},
            workflow_id=args.workflow_id,
            state_root=args.state_root,
        )
    )
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    workflow = WorkflowStore(args.state_root).load_workflow(args.workflow_id)
    print_json({"ok": True, "workflow": workflow})
    return 0


def _validate_recovery_resume_target(args: argparse.Namespace, store: WorkflowStore) -> None:
    if not (args.recovery_lease_id or args.recovery_lease_holder):
        return
    if not args.workflow_id:
        raise ValueError("recovery lease args require an existing workflow_id")
    if not (store.workflow_dir(args.workflow_id) / "workflow.json").exists():
        raise ValueError("recovery lease args require an existing workflow")


def cmd_checkpoint(args: argparse.Namespace) -> int:
    result = record_checkpoint(
        WorkflowStore(args.state_root),
        workflow_id=args.workflow_id,
        checkpoint_type=args.checkpoint_type,
        state_update=args.state_update,
        summary=resolve_summary(args),
        next_action=args.next_action,
        evidence=args.evidence,
    )
    print_json({"ok": True, "checkpoint": result})
    return 0


def cmd_append_round(args: argparse.Namespace) -> int:
    store = WorkflowStore(args.state_root)
    summary = resolve_summary(args)
    event_id = f"evt-progress-{args.round}-{uuid.uuid4().hex[:8]}"
    with store.lock(args.workflow_id):
        store.require_no_pending_checkpoint(args.workflow_id)
        workflow = store.load_workflow(args.workflow_id)
        if workflow.get("status") in {"completed_unreported", "failed_unreported", "reported", "abandoned"}:
            raise ValueError(f"append-round cannot continue workflow in terminal status: {workflow.get('status')!r}")
        store.append_event(
            args.workflow_id,
            {
                "schema_version": 1,
                "event_id": event_id,
                "workflow_id": args.workflow_id,
                "event_type": "progress",
                "created_at": now_iso(),
                "note": summary,
            },
            locked=True,
        )
        store.append_worklog(args.workflow_id, progress_block(args.round, summary))
    print_json({"ok": True, "workflow_id": args.workflow_id, "round": args.round, "event_id": event_id})
    return 0


def cmd_advance(args: argparse.Namespace) -> int:
    store = WorkflowStore(args.state_root)
    workflow = store.load_workflow(args.workflow_id)
    cursor_before = current_cursor(workflow)
    plan = workflow.get("continuation_plan")
    if not isinstance(plan, dict):
        print_json({"ok": True, "result": "terminal_ready", "reason": "workflow has no continuation plan"})
        return 0
    if workflow.get("status") in {"completed_unreported", "failed_unreported"}:
        print_json({"ok": True, "result": "terminal_ready", "status": workflow["status"]})
        return 0
    if workflow.get("status") in {"reported", "abandoned"}:
        raise ValueError(f"advance cannot continue workflow in terminal status: {workflow.get('status')!r}")
    step = plan["steps"][plan["current_step_index"]]
    if step["step_id"] != cursor_before:
        raise ValueError("continuation cursor does not match current step")
    cursor_after = step["next_on_pass"]
    if cursor_after in TERMINAL_CONTINUATION_TARGETS:
        _validate_terminal_ready_gate(store, workflow, step, args.evidence)
        print_json(
            {
                "ok": True,
                "result": "terminal_ready",
                "workflow_id": args.workflow_id,
                "cursor": cursor_before,
                "terminal_target": cursor_after,
            }
        )
        return 0
    result = record_checkpoint(
        store,
        workflow_id=args.workflow_id,
        checkpoint_type="advance",
        state_update={
            "checkpoint_type": "advance",
            "status_after": "running",
            "phase_after": args.phase_after,
            "cursor_before": cursor_before,
            "cursor_after": cursor_after,
            "event_type": "advance",
            "worklog_block_kind": "slice_summary",
            "step_result": "passed",
            "residuals": args.residuals or {},
        },
        summary=resolve_summary(args),
        next_action=args.next_action,
        evidence=args.evidence,
    )
    print_json({"ok": True, "result": "advance_ready", "advance": result})
    return 0


def cmd_artifact(args: argparse.Namespace) -> int:
    result = record_workflow_artifact(
        WorkflowStore(args.state_root),
        workflow_id=args.workflow_id,
        artifact_id=args.artifact_id,
        kind=args.kind,
        path=Path(args.path),
        note=args.note or "",
    )
    print_json({"ok": True, **result})
    return 0


def cmd_reserve_delivery(args: argparse.Namespace) -> int:
    _validate_visible_delivery_arg(args.visible_delivery)
    _validate_delivery_lease_seconds(args.lease_seconds)
    store = WorkflowStore(args.state_root)
    workflow = store.load_workflow(args.workflow_id)
    child_report_block = _child_visible_report_block_reason(store, workflow)
    if child_report_block:
        print_json(_delivery_no_send_payload(args, reason="duplicate_child_report_guard", terminal_status=workflow.get("status"), error=child_report_block))
        return 0
    route_mismatch = _delivery_route_mismatch(workflow, args.visible_delivery)
    if route_mismatch:
        print_json(_delivery_no_send_payload(args, reason="visible_delivery_mismatch", error=route_mismatch))
        return 0
    if workflow.get("status") not in {"running", "waiting_user", "waiting_subagent", "blocked", "completed_unreported", "failed_unreported"}:
        print_json(_delivery_no_send_payload(args, reason="invalid_state", terminal_status=workflow.get("status")))
        return 0
    if workflow.get("status") in {"completed_unreported", "failed_unreported"}:
        expected_terminal_status = f"{args.terminal_status}_unreported"
        if workflow["status"] != expected_terminal_status:
            print_json(
                _delivery_no_send_payload(
                    args,
                    reason="terminal_status_mismatch",
                    terminal_status=workflow["status"],
                    error=f"reserve-delivery terminal_status must match workflow status {workflow['status']}",
                )
            )
            return 0
        active_reservation = workflow.get("active_delivery_reservation")
        if isinstance(active_reservation, dict):
            print_json(_active_delivery_reconcile_payload(args, active_reservation))
            return 0
        checkpoint_id = _latest_terminal_checkpoint_id(workflow)
        if not checkpoint_id:
            raise ValueError("terminal workflow has no terminal checkpoint")
        historical_reservations = [
            event
            for event in _workflow_events(store, args.workflow_id)
            if event.get("event_type") == "delivery_reserved" and event.get("checkpoint_id") == checkpoint_id
        ]
        if not historical_reservations:
            reservation_id = args.reservation_id or f"delivery-{uuid.uuid4().hex[:12]}"
            acquired_at = now_iso()
            expires_at = _iso_after(args.lease_seconds)
            event_id = f"evt-delivery-{uuid.uuid4().hex[:8]}"
            cursor = current_cursor(workflow)
            with store.lock(args.workflow_id):
                workflow = store.load_workflow(args.workflow_id)
                active_reservation = workflow.get("active_delivery_reservation")
                if isinstance(active_reservation, dict):
                    print_json(_active_delivery_reconcile_payload(args, active_reservation))
                    return 0
                if workflow.get("status") not in {"completed_unreported", "failed_unreported"}:
                    raise ValueError("reserve-delivery terminal recovery requires terminal unreported workflow")
                if workflow["status"] != expected_terminal_status:
                    print_json(
                        _delivery_no_send_payload(
                            args,
                            reason="terminal_status_mismatch",
                            terminal_status=workflow["status"],
                            error=f"reserve-delivery terminal_status must match workflow status {workflow['status']}",
                        )
                    )
                    return 0
                historical_reservations = [
                    event
                    for event in _workflow_events(store, args.workflow_id)
                    if event.get("event_type") == "delivery_reserved" and event.get("checkpoint_id") == checkpoint_id
                ]
                if historical_reservations:
                    reservation = historical_reservations[-1]
                    print_json(_historical_delivery_reconcile_payload(args, workflow["status"], checkpoint_id, reservation))
                    return 0
                terminal_status = workflow["status"]
                try:
                    _validate_terminal_final_status_arg(workflow, args.final_status)
                    _validate_workflow_integrity(store, workflow)
                except ValueError as exc:
                    raise DeliveryValidationError(str(exc)) from exc
                workflow["active_delivery_reservation"] = _delivery_reservation(
                    reservation_id=reservation_id,
                    terminal_status=terminal_status,
                    visible_delivery=args.visible_delivery,
                    acquired_at=acquired_at,
                    lease_expires_at=expires_at,
                    checkpoint_id=checkpoint_id,
                )
                store.append_event(
                    args.workflow_id,
                    _delivery_reserved_event(
                        workflow_id=args.workflow_id,
                        event_id=event_id,
                        created_at=acquired_at,
                        checkpoint_id=checkpoint_id,
                        terminal_status=terminal_status,
                        cursor=cursor,
                        reservation_id=reservation_id,
                        visible_delivery=args.visible_delivery,
                    ),
                    locked=True,
                )
                store.save_workflow(workflow)
            print_json(
                _delivery_authorized_payload(
                    args,
                    reservation_id=reservation_id,
                    terminal_status=terminal_status,
                    visible_delivery=args.visible_delivery,
                    checkpoint_id=checkpoint_id,
                    lease_expires_at=expires_at,
                    event_id=event_id,
                )
            )
            return 0
        reservation = historical_reservations[-1]
        print_json(_historical_delivery_reconcile_payload(args, workflow["status"], checkpoint_id, reservation))
        return 0
    active_reservation = workflow.get("active_delivery_reservation")
    if isinstance(active_reservation, dict):
        print_json(_active_delivery_reconcile_payload(args, active_reservation))
        return 0
    if workflow.get("kind") in {"plan", "verify"}:
        kind = workflow["kind"]
        raise ValueError(f"{kind} workflows must finalize through {kind} mode before reserve-delivery")
    if isinstance(workflow.get("continuation_plan"), dict):
        print_json(
            _delivery_no_send_payload(
                args,
                reason="invalid_state",
                terminal_status=workflow.get("status"),
                error="active continuation workflows must create a terminal checkpoint before reserve-delivery",
            )
        )
        return 0
    print_json(_delivery_no_send_payload(args, reason="invalid_state", terminal_status=workflow.get("status")))
    return 0


def _active_delivery_reconcile_payload(args: argparse.Namespace, active_reservation: dict[str, Any]) -> dict[str, Any]:
    _validate_delivery_authority(active_reservation, label="active_delivery_reservation")
    reason = "active_reservation_exists"
    if _is_expired(active_reservation.get("lease_expires_at")):
        reason = "expired_reservation_requires_reconcile"
    return {
        "ok": True,
        "workflow_id": args.workflow_id,
        "send_authorized": False,
        "reconcile_required": True,
        "reservation_id": active_reservation["reservation_id"],
        "terminal_status": active_reservation["terminal_status"],
        "visible_delivery": active_reservation["visible_delivery"],
        "checkpoint_id": active_reservation["checkpoint_id"],
        "lease_expires_at": active_reservation["lease_expires_at"],
        "reason": reason,
        "send_authority": "converge.reserve-delivery",
        "source_of_truth": "converge.workflow",
    }


def _historical_delivery_reconcile_payload(
    args: argparse.Namespace,
    terminal_status: str,
    checkpoint_id: str,
    reservation: dict[str, Any],
) -> dict[str, Any]:
    reservation_payload = reservation.get("payload") or {}
    _validate_delivery_authority(reservation_payload, label="delivery_reserved")
    return {
        "ok": True,
        "workflow_id": args.workflow_id,
        "send_authorized": False,
        "reconcile_required": True,
        "reservation_id": reservation_payload.get("reservation_id"),
        "terminal_status": terminal_status,
        "visible_delivery": reservation_payload.get("visible_delivery"),
        "checkpoint_id": checkpoint_id,
        "lease_expires_at": None,
        "reason": "expired_reservation_requires_reconcile",
        "send_authority": "converge.reserve-delivery",
        "source_of_truth": "converge.workflow",
    }


def _delivery_reservation(
    *,
    reservation_id: str,
    terminal_status: str,
    visible_delivery: dict[str, Any],
    acquired_at: str,
    lease_expires_at: str,
    checkpoint_id: str,
) -> dict[str, Any]:
    return {
        "reservation_id": reservation_id,
        "lease_type": "delivery",
        "terminal_status": terminal_status,
        "visible_delivery": visible_delivery,
        "acquired_at": acquired_at,
        "lease_expires_at": lease_expires_at,
        "checkpoint_id": checkpoint_id,
        "send_authority": "converge.reserve-delivery",
        "source_of_truth": "converge.workflow",
    }


def _delivery_reserved_event(
    *,
    workflow_id: str,
    event_id: str,
    created_at: str,
    checkpoint_id: str,
    terminal_status: str,
    cursor: str,
    reservation_id: str,
    visible_delivery: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "workflow_id": workflow_id,
        "event_type": "delivery_reserved",
        "created_at": created_at,
        "checkpoint_id": checkpoint_id,
        "status_after": terminal_status,
        "phase_after": "terminal",
        "cursor_before": cursor,
        "cursor_after": cursor,
        "payload": {
            "reservation_id": reservation_id,
            "visible_delivery": visible_delivery,
            "send_authority": "converge.reserve-delivery",
            "source_of_truth": "converge.workflow",
        },
    }


def _delivery_authorized_payload(
    args: argparse.Namespace,
    *,
    reservation_id: str,
    terminal_status: str,
    visible_delivery: dict[str, Any],
    checkpoint_id: str,
    lease_expires_at: str,
    event_id: str,
    checkpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "ok": True,
        "workflow_id": args.workflow_id,
        "send_authorized": True,
        "reconcile_required": False,
        "reservation_id": reservation_id,
        "terminal_status": terminal_status,
        "visible_delivery": visible_delivery,
        "checkpoint_id": checkpoint_id,
        "lease_expires_at": lease_expires_at,
        "reason": None,
        "event_id": event_id,
        "send_authority": "converge.reserve-delivery",
        "source_of_truth": "converge.workflow",
    }
    if checkpoint is not None:
        payload["checkpoint"] = checkpoint
    return payload


def _validate_terminal_final_status_arg(workflow: dict[str, Any], final_status: dict[str, Any]) -> None:
    if workflow.get("final_status") != final_status:
        raise ValueError("reserve-delivery final_status must match stored workflow final_status")


def _delivery_route_mismatch(workflow: dict[str, Any], visible_delivery: dict[str, Any]) -> str | None:
    workflow_delivery = workflow.get("visible_delivery")
    if not isinstance(workflow_delivery, dict) or not workflow_delivery:
        return None
    if workflow_delivery != visible_delivery:
        return "reserve-delivery visible_delivery must match workflow visible_delivery"
    return None


def _validate_terminal_ready_gate(
    store: WorkflowStore,
    workflow: dict[str, Any],
    step: dict[str, Any],
    evidence: dict[str, Any] | None,
) -> None:
    gate = step.get("gate")
    requires_evidence = isinstance(gate, dict) and bool(gate.get("requires_evidence"))
    if not requires_evidence:
        return
    if not evidence:
        raise ValueError("terminal_ready requires checkpoint evidence")
    validate_evidence_object(evidence)
    worklog_path = store.workflow_dir(workflow["workflow_id"]) / "worklog.md"
    validate_evidence_artifact_refs(
        workflow,
        evidence,
        worklog_text=worklog_path.read_text(encoding="utf-8") if worklog_path.exists() else "",
    )


def _delivery_no_send_payload(args: argparse.Namespace, *, reason: str, terminal_status: str | None = None, error: str | None = None) -> dict[str, Any]:
    payload = {
        "ok": True,
        "workflow_id": args.workflow_id,
        "send_authorized": False,
        "reconcile_required": reason != "invalid_state",
        "reservation_id": None,
        "terminal_status": terminal_status,
        "visible_delivery": getattr(args, "visible_delivery", None),
        "checkpoint_id": None,
        "lease_expires_at": None,
        "reason": reason,
        "send_authority": "converge.reserve-delivery",
        "source_of_truth": "converge.workflow",
    }
    if error:
        payload["error"] = error
    return payload


def cmd_report_proof(args: argparse.Namespace) -> int:
    _validate_delivery_message_id(args.delivery_message_id)
    _validate_visible_delivery_arg(args.visible_delivery)
    proof = _record_report_proof(
        WorkflowStore(args.state_root),
        workflow_id=args.workflow_id,
        reservation_id=args.reservation_id,
        delivery_message_id=args.delivery_message_id,
        visible_delivery=args.visible_delivery,
        manual_reconcile=args.manual_reconcile,
    )
    print_json({"ok": True, "proof": proof})
    return 0


def cmd_complete_reported(args: argparse.Namespace) -> int:
    _validate_delivery_message_id(args.delivery_message_id)
    _validate_visible_delivery_arg(args.visible_delivery)
    store = WorkflowStore(args.state_root)
    proof = _record_report_proof(
        store,
        workflow_id=args.workflow_id,
        reservation_id=args.reservation_id,
        delivery_message_id=args.delivery_message_id,
        visible_delivery=args.visible_delivery,
        manual_reconcile=args.manual_reconcile,
    )
    event_id = f"evt-report-{uuid.uuid4().hex[:8]}"
    with store.lock(args.workflow_id):
        workflow = store.load_workflow(args.workflow_id)
        if workflow.get("status") == "reported":
            visible_state = workflow.setdefault("visible_delivery_state", {})
            if not visible_state.get("reported"):
                events = _workflow_events(store, args.workflow_id)
                matching_report_sent = _matching_report_event(
                    events,
                    event_type="report_sent",
                    reservation_id=args.reservation_id,
                    delivery_message_id=args.delivery_message_id,
                    visible_delivery=args.visible_delivery,
                )
                if matching_report_sent:
                    if len(matching_report_sent) > 1:
                        raise ValueError("duplicate matching report_sent events")
                    delivery_event = _ensure_single_delivery_event(
                        events,
                        reservation_id=args.reservation_id,
                        visible_delivery=args.visible_delivery,
                        missing_error="workflow reported state has no matching delivery_reserved event",
                    )
                    _ensure_delivery_checkpoint_index(workflow, delivery_event)
                    if matching_report_sent[0].get("checkpoint_id") != delivery_event.get("checkpoint_id"):
                        raise ValueError("report_sent checkpoint_id does not match delivery_reserved checkpoint")
                    reported_payload = matching_report_sent[0].get("payload") or {}
                    _validate_report_payload(reported_payload, timestamp_key="reported_at", label="report_sent")
                    _apply_reported_transition(workflow, reported_payload)
                    _validate_workflow_integrity(store, workflow, validate_material=False)
                    store.save_workflow(workflow)
            _validate_workflow_integrity(store, workflow, validate_material=False)
            print_json({"ok": True, "workflow_id": args.workflow_id, "status": "reported", "proof": proof})
            return 0
        if workflow.get("status") not in {"completed_unreported", "failed_unreported"}:
            raise ValueError("complete-reported requires completed_unreported or failed_unreported workflow")
        report_context = _report_context(
            store,
            workflow,
            reservation_id=args.reservation_id,
            visible_delivery=args.visible_delivery,
            manual_reconcile=args.manual_reconcile,
        )
        matching_report_sent = _matching_report_event(
            _workflow_events(store, args.workflow_id),
            event_type="report_sent",
            reservation_id=args.reservation_id,
            delivery_message_id=args.delivery_message_id,
            visible_delivery=args.visible_delivery,
        )
        if matching_report_sent:
            if len(matching_report_sent) > 1:
                raise ValueError("duplicate matching report_sent events")
            if matching_report_sent[0].get("checkpoint_id") != report_context.get("checkpoint_id"):
                raise ValueError("report_sent checkpoint_id does not match delivery_reserved checkpoint")
            reported_payload = matching_report_sent[0].get("payload") or {}
            _validate_report_payload(reported_payload, timestamp_key="reported_at", label="report_sent")
            _apply_reported_transition(workflow, reported_payload)
            _validate_workflow_integrity(store, workflow, validate_material=False)
            store.save_workflow(workflow)
            print_json({"ok": True, "workflow_id": args.workflow_id, "status": "reported", "event_id": matching_report_sent[0]["event_id"], "proof": proof})
            return 0
        reported_payload = {
            "reservation_id": args.reservation_id,
            "delivery_message_id": args.delivery_message_id,
            "visible_delivery": args.visible_delivery,
            "reported_at": now_iso(),
            "report_authority": "converge.complete-reported",
            "source_of_truth": "converge.workflow",
        }
        _validate_report_payload(reported_payload, timestamp_key="reported_at", label="report_sent")
        _apply_reported_transition(workflow, reported_payload)
        store.append_event(
            args.workflow_id,
            _report_sent_event(
                workflow_id=args.workflow_id,
                event_id=event_id,
                checkpoint_id=report_context.get("checkpoint_id"),
                reported_payload=reported_payload,
            ),
            locked=True,
        )
        _validate_workflow_integrity(store, workflow, validate_material=False)
        store.save_workflow(workflow)
    print_json({"ok": True, "workflow_id": args.workflow_id, "status": "reported", "event_id": event_id, "proof": proof})
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    print_json(scan_workflows(args.state_root))
    return 0


def cmd_watchdog_check(args: argparse.Namespace) -> int:
    print_json(watchdog_check(args.state_root, include_clean=args.include_clean))
    return 0


def cmd_recover(args: argparse.Namespace) -> int:
    print_json(
        recover_workflow(
            args.state_root,
            args.workflow_id,
            holder=args.holder,
            lease_seconds=args.lease_seconds,
        )
    )
    return 0


def _apply_reported_transition(workflow: dict[str, Any], reported_payload: dict[str, Any]) -> None:
    workflow["status"] = "reported"
    workflow["phase"] = "reported"
    workflow["active_delivery_reservation"] = None
    workflow.setdefault("visible_delivery_state", {})["reported"] = reported_payload


def _report_sent_event(
    *,
    workflow_id: str,
    event_id: str,
    checkpoint_id: str | None,
    reported_payload: dict[str, Any],
) -> dict[str, Any]:
    event = {
        "schema_version": 1,
        "event_id": event_id,
        "workflow_id": workflow_id,
        "event_type": "report_sent",
        "created_at": reported_payload["reported_at"],
        "status_after": "reported",
        "phase_after": "reported",
        "payload": reported_payload,
    }
    if checkpoint_id:
        event["checkpoint_id"] = checkpoint_id
    return event


def cmd_event(args: argparse.Namespace) -> int:
    checkpoint_owned_events = {"checkpoint", "advance", "complete", "fail"}
    if args.type in checkpoint_owned_events:
        raise ValueError("checkpoint-owned event types must use checkpoint, not event")
    if args.type not in SUPPORTED_MANUAL_EVENT_TYPES:
        raise ValueError(f"manual event type is not currently supported: {args.type}")
    forbidden = {
        "status",
        "phase",
        "cursor",
        "cursor_before",
        "cursor_after",
        "current_resume_cursor",
        "next_safe_action",
        "checkpoint_index",
        "continuation_plan",
        "active_recovery_lease",
        "active_delivery_reservation",
        "visible_delivery_state",
        "final_status",
    }
    if args.payload and forbidden.intersection(args.payload):
        raise ValueError("state transitions must use checkpoint, not event")
    if args.type == "plan_accepted":
        _validate_plan_accepted_payload(args.payload)
    if args.type in {"owner_decision", "rescope"}:
        _validate_owner_decision_payload(args.type, args.payload)
    store = WorkflowStore(args.state_root)
    if not (store.workflow_dir(args.workflow_id) / "workflow.json").exists():
        raise FileNotFoundError(f"workflow not found: {args.workflow_id}")
    with store.lock(args.workflow_id):
        store.require_no_pending_checkpoint(args.workflow_id)
        workflow = store.load_workflow(args.workflow_id)
        if workflow.get("status") in {"completed_unreported", "failed_unreported", "reported", "abandoned"}:
            raise ValueError(f"event cannot append manual events to terminal workflow status: {workflow.get('status')!r}")
        store.append_event(
            args.workflow_id,
            {
                "schema_version": 1,
                "event_id": args.event_id,
                "workflow_id": args.workflow_id,
                "event_type": args.type,
                "created_at": now_iso(),
                "note": args.note or "",
                "payload": args.payload or {},
            },
            locked=True,
        )
    print_json({"ok": True, "workflow_id": args.workflow_id, "event_id": args.event_id})
    return 0


def _validate_plan_accepted_payload(payload: dict[str, Any] | None) -> None:
    validate_acceptance_payload("plan_accepted", payload, require_nonempty_objective=True)


def _validate_owner_decision_payload(event_type: str, payload: dict[str, Any] | None) -> None:
    validate_acceptance_payload(event_type, payload, require_nonempty_objective=True)


def _iso_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_delivery_lease_seconds(seconds: int) -> None:
    if seconds <= 0:
        raise ValueError("reserve-delivery lease-seconds must be positive")


def _parse_utc(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("delivery reservation requires lease_expires_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("delivery reservation has invalid lease_expires_at") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_expired(value: Any) -> bool:
    return _parse_utc(value) <= datetime.now(timezone.utc)


def _matching_reservation(workflow: dict[str, Any], reservation_id: str) -> dict[str, Any]:
    reservation = workflow.get("active_delivery_reservation")
    if not isinstance(reservation, dict):
        raise ValueError("workflow has no active delivery reservation")
    _validate_delivery_authority(reservation, label="active_delivery_reservation")
    if reservation.get("reservation_id") != reservation_id:
        raise ValueError("reservation_id does not match active delivery reservation")
    return reservation


def _workflow_events(store: WorkflowStore, workflow_id: str) -> list[dict[str, Any]]:
    events_path = store.workflow_dir(workflow_id) / "events.jsonl"
    if not events_path.exists():
        return []
    return [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _latest_terminal_checkpoint_id(workflow: dict[str, Any]) -> str | None:
    checkpoints = [
        item
        for item in workflow.get("checkpoint_index", {}).values()
        if isinstance(item, dict) and item.get("status_after") in {"completed_unreported", "failed_unreported"}
    ]
    if not checkpoints:
        return None
    checkpoints.sort(key=lambda item: item.get("checkpoint_seq", 0))
    checkpoint_id = checkpoints[-1].get("checkpoint_id")
    return checkpoint_id if isinstance(checkpoint_id, str) and checkpoint_id else None


def _delivery_event_matches(
    event: dict[str, Any],
    *,
    reservation_id: str,
    visible_delivery: dict[str, Any],
    checkpoint_id: str | None = None,
    terminal_status: str | None = None,
) -> bool:
    payload = event.get("payload") or {}
    if event.get("event_type") != "delivery_reserved":
        return False
    if payload.get("reservation_id") != reservation_id:
        return False
    if payload.get("visible_delivery") != visible_delivery:
        return False
    if checkpoint_id is not None and event.get("checkpoint_id") != checkpoint_id:
        return False
    if terminal_status is not None and event.get("status_after") != terminal_status:
        return False
    return True


def _matching_delivery_events(
    events: list[dict[str, Any]],
    *,
    reservation_id: str,
    visible_delivery: dict[str, Any],
    checkpoint_id: str | None = None,
    terminal_status: str | None = None,
) -> list[dict[str, Any]]:
    return [
        event
        for event in events
        if _delivery_event_matches(
            event,
            reservation_id=reservation_id,
            visible_delivery=visible_delivery,
            checkpoint_id=checkpoint_id,
            terminal_status=terminal_status,
        )
    ]


def _ensure_single_delivery_event(
    events: list[dict[str, Any]],
    *,
    reservation_id: str,
    visible_delivery: dict[str, Any],
    checkpoint_id: str | None = None,
    terminal_status: str | None = None,
    missing_error: str = "manual reconcile requires matching delivery_reserved event",
) -> dict[str, Any]:
    matches = _matching_delivery_events(
        events,
        reservation_id=reservation_id,
        visible_delivery=visible_delivery,
        checkpoint_id=checkpoint_id,
        terminal_status=terminal_status,
    )
    if not matches:
        raise ValueError(missing_error)
    if len(matches) > 1:
        raise ValueError("duplicate matching delivery_reserved events")
    _validate_delivery_authority(matches[0].get("payload") or {}, label="delivery_reserved")
    return matches[0]


def _historical_delivery_context(
    store: WorkflowStore,
    workflow: dict[str, Any],
    *,
    reservation_id: str,
    visible_delivery: dict[str, Any],
) -> dict[str, Any]:
    event = _ensure_single_delivery_event(
        _workflow_events(store, workflow["workflow_id"]),
        reservation_id=reservation_id,
        visible_delivery=visible_delivery,
    )
    checkpoint_id = event.get("checkpoint_id")
    if not checkpoint_id or checkpoint_id not in workflow.get("checkpoint_index", {}):
        raise ValueError("historical delivery reservation checkpoint missing from checkpoint_index")
    return {"checkpoint_id": checkpoint_id}


def _ensure_delivery_checkpoint_index(workflow: dict[str, Any], event: dict[str, Any]) -> None:
    checkpoint_id = event.get("checkpoint_id")
    if not checkpoint_id or checkpoint_id not in workflow.get("checkpoint_index", {}):
        raise ValueError("delivery_reserved checkpoint missing from checkpoint_index")


def _report_context(
    store: WorkflowStore,
    workflow: dict[str, Any],
    *,
    reservation_id: str,
    visible_delivery: dict[str, Any],
    manual_reconcile: str | None,
) -> dict[str, Any]:
    reservation = workflow.get("active_delivery_reservation")
    if isinstance(reservation, dict):
        reservation = _matching_reservation(workflow, reservation_id)
        if visible_delivery != reservation.get("visible_delivery"):
            raise ValueError("visible_delivery does not match reservation")
        if _is_expired(reservation.get("lease_expires_at")) and not manual_reconcile:
            raise ValueError("active delivery reservation expired; manual reconcile required")
        delivery_event = _ensure_single_delivery_event(
            _workflow_events(store, workflow["workflow_id"]),
            reservation_id=reservation_id,
            visible_delivery=visible_delivery,
            checkpoint_id=reservation.get("checkpoint_id"),
            terminal_status=reservation.get("terminal_status"),
            missing_error="active delivery reservation has no matching delivery_reserved event",
        )
        _ensure_delivery_checkpoint_index(workflow, delivery_event)
        return {"checkpoint_id": reservation.get("checkpoint_id")}
    if not manual_reconcile:
        raise ValueError("workflow has no active delivery reservation")
    if workflow.get("status") not in {"completed_unreported", "failed_unreported", "reported"}:
        raise ValueError("manual reconcile requires terminal unreported or reported workflow")
    return _historical_delivery_context(
        store,
        workflow,
        reservation_id=reservation_id,
        visible_delivery=visible_delivery,
    )


def _record_report_proof(
    store: WorkflowStore,
    *,
    workflow_id: str,
    reservation_id: str,
    delivery_message_id: str,
    visible_delivery: dict[str, Any],
    manual_reconcile: str | None,
) -> dict[str, Any]:
    event_id = f"evt-proof-{uuid.uuid4().hex[:8]}"
    recorded_at = now_iso()
    with store.lock(workflow_id):
        workflow = store.load_workflow(workflow_id)
        child_report_block = _child_visible_report_block_reason(store, workflow)
        if child_report_block:
            raise ValueError(child_report_block)
        existing = workflow.setdefault("visible_delivery_state", {}).get("report_proof")
        if existing:
            events = _workflow_events(store, workflow_id)
            _ensure_same_report_proof(
                existing,
                reservation_id=reservation_id,
                delivery_message_id=delivery_message_id,
                visible_delivery=visible_delivery,
                manual_reconcile=manual_reconcile,
            )
            matching_report_proof = _matching_report_event(
                events,
                event_type="report_proof",
                reservation_id=reservation_id,
                delivery_message_id=delivery_message_id,
                visible_delivery=visible_delivery,
                manual_reconcile=manual_reconcile,
            )
            if not matching_report_proof:
                raise ValueError("workflow report proof has no matching report_proof event")
            if len(matching_report_proof) > 1:
                raise ValueError("duplicate matching report_proof events")
            delivery_event = _ensure_single_delivery_event(
                events,
                reservation_id=reservation_id,
                visible_delivery=visible_delivery,
                missing_error="workflow report proof has no matching delivery_reserved event",
            )
            _ensure_delivery_checkpoint_index(workflow, delivery_event)
            if matching_report_proof[0].get("checkpoint_id") != delivery_event.get("checkpoint_id"):
                raise ValueError("report_proof checkpoint_id does not match delivery_reserved checkpoint")
            proof = dict(existing)
            _validate_report_payload(proof, timestamp_key="recorded_at", label="report_proof")
            proof["event_id"] = matching_report_proof[0]["event_id"]
            return proof
        events = _workflow_events(store, workflow_id)
        matching_report_proof = _matching_report_event(
            events,
            event_type="report_proof",
            reservation_id=reservation_id,
            delivery_message_id=delivery_message_id,
            visible_delivery=visible_delivery,
            manual_reconcile=manual_reconcile,
        )
        if matching_report_proof:
            if len(matching_report_proof) > 1:
                raise ValueError("duplicate matching report_proof events")
            delivery_event = _ensure_single_delivery_event(
                events,
                reservation_id=reservation_id,
                visible_delivery=visible_delivery,
                missing_error="workflow report proof has no matching delivery_reserved event",
            )
            _ensure_delivery_checkpoint_index(workflow, delivery_event)
            if matching_report_proof[0].get("checkpoint_id") != delivery_event.get("checkpoint_id"):
                raise ValueError("report_proof checkpoint_id does not match delivery_reserved checkpoint")
            proof = matching_report_proof[0].get("payload") or {}
            _validate_report_payload(proof, timestamp_key="recorded_at", label="report_proof")
            workflow["visible_delivery_state"]["report_proof"] = proof
            store.save_workflow(workflow)
            proof = dict(proof)
            proof["event_id"] = matching_report_proof[0]["event_id"]
            return proof
        report_context = _report_context(
            store,
            workflow,
            reservation_id=reservation_id,
            visible_delivery=visible_delivery,
            manual_reconcile=manual_reconcile,
        )
        proof = {
            "reservation_id": reservation_id,
            "delivery_message_id": delivery_message_id,
            "visible_delivery": visible_delivery,
            "recorded_at": recorded_at,
            "proof_authority": "converge.report-proof",
            "source_of_truth": "converge.workflow",
        }
        if manual_reconcile:
            proof["manual_reconcile"] = manual_reconcile
        _validate_report_payload(proof, timestamp_key="recorded_at", label="report_proof")
        workflow["visible_delivery_state"]["report_proof"] = proof
        event = {
            "schema_version": 1,
            "event_id": event_id,
            "workflow_id": workflow_id,
            "event_type": "report_proof",
            "created_at": recorded_at,
            "status_after": workflow["status"],
            "phase_after": workflow["phase"],
            "payload": proof,
        }
        if report_context.get("checkpoint_id"):
            event["checkpoint_id"] = report_context["checkpoint_id"]
        store.append_event(workflow_id, event, locked=True)
        store.save_workflow(workflow)
        proof = dict(proof)
        proof["event_id"] = event_id
        return proof


def _ensure_same_report_proof(
    existing: dict[str, Any],
    *,
    reservation_id: str,
    delivery_message_id: str,
    visible_delivery: dict[str, Any],
    manual_reconcile: str | None,
) -> None:
    expected = {
        "reservation_id": reservation_id,
        "delivery_message_id": delivery_message_id,
        "visible_delivery": visible_delivery,
    }
    for key, value in expected.items():
        if existing.get(key) != value:
            raise ValueError("workflow already has different report proof")
    if existing.get("manual_reconcile") != manual_reconcile:
        raise ValueError("workflow already has different report proof")


def _matching_report_event(
    events: list[dict[str, Any]],
    *,
    event_type: str,
    reservation_id: str,
    delivery_message_id: str,
    visible_delivery: dict[str, Any],
    manual_reconcile: str | None = None,
) -> list[dict[str, Any]]:
    matches = []
    for event in events:
        if event.get("event_type") != event_type:
            continue
        payload = event.get("payload") or {}
        if payload.get("reservation_id") != reservation_id:
            continue
        if payload.get("delivery_message_id") != delivery_message_id:
            continue
        if payload.get("visible_delivery") != visible_delivery:
            continue
        if event_type == "report_proof" and payload.get("manual_reconcile") != manual_reconcile:
            continue
        matches.append(event)
    return matches


def cmd_validate(args: argparse.Namespace) -> int:
    checked = validate_bundled_schemas()
    samples: list[str] = []
    if args.workflow_id:
        store = WorkflowStore(args.state_root)
        workflow = store.load_workflow(args.workflow_id)
        validate_named(workflow, "workflow.schema.json")
        _validate_workflow_integrity(store, workflow)
        samples.append(args.workflow_id)
    if args.sample_docs:
        sample_workflow = {
            "schema_version": 1,
            "workflow_id": "conv-sample-docs",
            "kind": "conv",
            "status": "running",
            "created_at": "2026-05-24T00:00:00Z",
            "updated_at": "2026-05-24T00:00:00Z",
            "last_activity_at": "2026-05-24T00:00:00Z",
            "last_visible_update_at": None,
            "stale_after_seconds": 7200,
            "reminder_after_seconds": 1800,
            "owner_session_key": "",
            "visible_delivery": {},
            "source_request": "demo",
            "objective": "demo",
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
            "continuation_plan": default_continuation_plan("conv"),
            "next_safe_action": structured_next_action(
                action_type="inspect_or_continue",
                summary="Inspect workflow state.",
                cursor="baseline",
                risk_class="read_only",
                side_effect_key="inspect:conv-sample-docs:start",
                idempotency_policy="repeatable",
                expected_artifacts=["workflow.json", "worklog.md"],
            ),
            "visible_delivery_state": {},
            "final_status": None,
            "conv_state": {},
        }
        validate_named(sample_workflow, "workflow.schema.json")
        sample_event = {
            "schema_version": 1,
            "event_id": "evt-sample-docs",
            "workflow_id": "conv-sample-docs",
            "event_type": "progress",
            "created_at": "2026-05-24T00:00:00Z",
            "note": "sample progress",
            "payload": {"round": 1},
        }
        validate_named(sample_event, "event.schema.json")
        sample_update = {
            "checkpoint_type": "checkpoint",
            "status_after": "running",
            "phase_after": "slice",
            "cursor_before": "baseline",
            "cursor_after": "baseline",
            "event_type": "checkpoint",
            "worklog_block_kind": "slice_summary",
            "step_result": "waiting",
            "residuals": {
                "blocking_remaining": [],
                "accepted_risks": [],
                "implementation_backlog": [],
                "deferred_scope": [],
            },
        }
        validate_named(sample_update, "checkpoint_state_update.schema.json")
        try:
            invalid = dict(sample_update)
            invalid["event_type"] = "completed_unreported"
            validate_named(invalid, "checkpoint_state_update.schema.json")
            raise AssertionError("invalid terminal status-as-event fixture unexpectedly passed")
        except SchemaError:
            pass
        samples.append("workflow/event sample fixtures")
        samples.append("checkpoint_state_update positive/negative")
    print_json({"ok": True, "schemas": checked, "samples": samples})
    return 0


def _validate_workflow_integrity(store: WorkflowStore, workflow: dict[str, Any], *, validate_material: bool = True) -> None:
    workflow_id = workflow["workflow_id"]
    store.require_no_pending_checkpoint(workflow_id)

    workflow_dir = store.workflow_dir(workflow_id)
    events_path = workflow_dir / "events.jsonl"
    worklog_path = workflow_dir / "worklog.md"
    event_ids: set[str] = set()
    checkpoint_events: dict[str, dict[str, Any]] = {}
    delivery_reserved_events: list[dict[str, Any]] = []
    report_proof_events: list[dict[str, Any]] = []
    report_sent_events: list[dict[str, Any]] = []
    unknown_checkpoint_refs: list[str] = []
    checkpoint_owned_events = {"checkpoint", "advance", "complete", "fail"}
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                event = json.loads(line)
                validate_named(event, "event.schema.json")
                if event["workflow_id"] != workflow_id:
                    raise ValueError(f"event {event['event_id']} workflow_id mismatch")
                if event["event_id"] in event_ids:
                    raise ValueError(f"duplicate event_id: {event['event_id']}")
                event_ids.add(event["event_id"])
                if event["event_type"] == "delivery_reserved":
                    delivery_reserved_events.append(event)
                if event["event_type"] == "report_proof":
                    report_proof_events.append(event)
                if event["event_type"] == "report_sent":
                    report_sent_events.append(event)
                checkpoint_id = event.get("checkpoint_id")
                if checkpoint_id:
                    if checkpoint_id not in workflow.get("checkpoint_index", {}):
                        unknown_checkpoint_refs.append(checkpoint_id)
                    elif event["event_type"] in checkpoint_owned_events:
                        if checkpoint_id in checkpoint_events:
                            raise ValueError(f"duplicate checkpoint event: {checkpoint_id}")
                        checkpoint_events[checkpoint_id] = event
                if event["event_type"] in checkpoint_owned_events and not event.get("checkpoint_id"):
                    raise ValueError(f"checkpoint-owned event {event['event_id']} missing checkpoint_id")
    if unknown_checkpoint_refs:
        raise ValueError(f"checkpoint event {unknown_checkpoint_refs[0]} missing from checkpoint_index")
    worklog = worklog_path.read_text(encoding="utf-8") if worklog_path.exists() else ""
    if validate_material:
        for artifact in workflow.get("artifacts", []):
            validate_named(artifact, "artifact.schema.json")
            path = Path(artifact["path"])
            if not path.is_file():
                raise ValueError(f"artifact path is missing: {artifact['path']}")
            if sha256_file(path) != artifact["sha256"]:
                raise ValueError(f"artifact hash is stale: {artifact['artifact_id']}")
    checkpoint_index = workflow.get("checkpoint_index", {})
    active_delivery = workflow.get("active_delivery_reservation")
    if isinstance(active_delivery, dict):
        if workflow.get("status") not in {"completed_unreported", "failed_unreported"}:
            raise ValueError("active_delivery_reservation requires terminal unreported workflow status")
        _validate_delivery_authority(active_delivery, label="active_delivery_reservation")
        checkpoint_id = active_delivery.get("checkpoint_id")
        if checkpoint_id not in checkpoint_index:
            raise ValueError(f"active_delivery_reservation checkpoint_id {checkpoint_id} missing from checkpoint_index")
        _ensure_single_delivery_event(
            delivery_reserved_events,
            reservation_id=active_delivery.get("reservation_id"),
            visible_delivery=active_delivery.get("visible_delivery"),
            checkpoint_id=checkpoint_id,
            terminal_status=active_delivery.get("terminal_status"),
            missing_error="active_delivery_reservation has no matching delivery_reserved event",
        )
    for checkpoint_id in checkpoint_events:
        if checkpoint_id not in checkpoint_index:
            raise ValueError(f"checkpoint event {checkpoint_id} missing from checkpoint_index")
    for checkpoint_id, checkpoint_meta in checkpoint_index.items():
        if checkpoint_meta.get("checkpoint_id") != checkpoint_id:
            raise ValueError(f"checkpoint_index key {checkpoint_id} does not match checkpoint_id {checkpoint_meta.get('checkpoint_id')}")
        if checkpoint_id not in checkpoint_events:
            raise ValueError(f"checkpoint {checkpoint_id} missing matching event")
        event = checkpoint_events[checkpoint_id]
        state_update = (event.get("payload") or {}).get("state_update") or {}
        if event.get("event_id") != checkpoint_meta.get("event_id"):
            raise ValueError(f"checkpoint {checkpoint_id} event_id mismatch")
        comparisons = {
            "checkpoint_type": checkpoint_meta.get("checkpoint_type"),
            "cursor_before": checkpoint_meta.get("cursor_before"),
            "cursor_after": checkpoint_meta.get("cursor_after"),
            "status_after": checkpoint_meta.get("status_after"),
            "phase_after": checkpoint_meta.get("phase_after"),
            "checkpoint_seq": checkpoint_meta.get("checkpoint_seq"),
            "worklog_block_id": checkpoint_meta.get("worklog_block_id"),
        }
        expected = {
            "checkpoint_type": state_update.get("checkpoint_type"),
            "cursor_before": state_update.get("cursor_before"),
            "cursor_after": state_update.get("cursor_after"),
            "status_after": state_update.get("status_after"),
            "phase_after": state_update.get("phase_after"),
            "checkpoint_seq": (event.get("payload") or {}).get("checkpoint_seq"),
            "worklog_block_id": (event.get("payload") or {}).get("worklog_block_id"),
        }
        for key, actual in comparisons.items():
            if actual != expected[key]:
                raise ValueError(f"checkpoint {checkpoint_id} {key} mismatch")
        if f"## Checkpoint {checkpoint_id}" not in worklog:
            raise ValueError(f"checkpoint {checkpoint_id} missing matching worklog block")
    _validate_terminal_checkpoint_integrity(workflow, checkpoint_index, checkpoint_events)
    for event in delivery_reserved_events:
        checkpoint_id = event.get("checkpoint_id")
        _validate_delivery_authority(event.get("payload") or {}, label="delivery_reserved")
        if checkpoint_id not in checkpoint_index:
            raise ValueError(f"delivery_reserved checkpoint_id {checkpoint_id} missing from checkpoint_index")
        if sum(1 for candidate in delivery_reserved_events if candidate.get("checkpoint_id") == checkpoint_id) > 1:
            raise ValueError(f"delivery_reserved checkpoint_id {checkpoint_id} has duplicate reservations")
        checkpoint_meta = checkpoint_index[checkpoint_id]
        if event.get("status_after") != checkpoint_meta.get("status_after"):
            raise ValueError(f"delivery_reserved {event['event_id']} status_after does not match checkpoint {checkpoint_id}")
        if checkpoint_meta.get("status_after") not in {"completed_unreported", "failed_unreported"}:
            raise ValueError(f"delivery_reserved {event['event_id']} must reference terminal unreported checkpoint")
    _validate_report_event_integrity(workflow, delivery_reserved_events, report_proof_events, report_sent_events)
    rolling = (workflow.get("continuation_plan") or {}).get("rolling_state") or {}
    last_checkpoint_id = rolling.get("last_checkpoint_id")
    if last_checkpoint_id and last_checkpoint_id not in workflow.get("checkpoint_index", {}):
        raise ValueError(f"last_checkpoint_id {last_checkpoint_id} missing from checkpoint_index")
    if checkpoint_index and last_checkpoint_id:
        latest_checkpoint_id = max(checkpoint_index, key=lambda item: checkpoint_index[item].get("checkpoint_seq", 0))
        if last_checkpoint_id != latest_checkpoint_id:
            raise ValueError(f"last_checkpoint_id {last_checkpoint_id} is not latest checkpoint {latest_checkpoint_id}")
    validate_next_safe_action(workflow.get("next_safe_action"), "$.next_safe_action")
    next_cursor = workflow.get("next_safe_action", {}).get("cursor")
    current_cursor = rolling.get("current_resume_cursor")
    if workflow.get("continuation_plan") is not None and not next_cursor:
        raise ValueError("next_safe_action cursor is required for continuation workflows")
    if next_cursor and current_cursor and next_cursor != current_cursor:
        raise ValueError(f"next_safe_action cursor {next_cursor!r} does not match current cursor {current_cursor!r}")
    if workflow.get("continuation_plan") is None:
        expected_cursor = "start"
        if checkpoint_index:
            latest_checkpoint_id = max(checkpoint_index, key=lambda item: checkpoint_index[item].get("checkpoint_seq", 0))
            expected_cursor = checkpoint_index[latest_checkpoint_id].get("cursor_after") or expected_cursor
        if next_cursor != expected_cursor:
            raise ValueError(f"next_safe_action cursor {next_cursor!r} does not match current cursor {expected_cursor!r}")
    if workflow.get("kind") == "goal" and workflow.get("status") == "reported":
        _validate_reported_goal_child_integrity(store, workflow)
    if validate_material:
        _validate_plan_state_integrity(workflow)
        _validate_goal_state_integrity(store, workflow)
        _validate_verify_state_integrity(store, workflow)
        _validate_conv_state_integrity(store, workflow)
        stale_context = [entry.get("ref", "<unknown>") for entry in workflow.get("context_manifest", []) if not validate_manifest_entry(entry)]
        if stale_context:
            raise ValueError(f"context manifest is stale: {stale_context!r}")
        worklog_text = worklog_path.read_text(encoding="utf-8") if worklog_path.exists() else ""
        for evidence in (workflow.get("verification") or {}).get("evidence") or []:
            if not isinstance(evidence, dict):
                raise ValueError("workflow verification evidence entries must be objects")
            validate_evidence_object(evidence)
            validate_evidence_artifact_refs(workflow, evidence, worklog_text=worklog_text)


def _validate_terminal_checkpoint_integrity(
    workflow: dict[str, Any],
    checkpoint_index: dict[str, Any],
    checkpoint_events: dict[str, dict[str, Any]],
) -> None:
    status = workflow.get("status")
    if status not in {"completed_unreported", "failed_unreported", "reported"}:
        return
    terminal = _terminal_checkpoint_context(workflow, checkpoint_index, checkpoint_events, status=status)
    _validate_terminal_final_status_snapshot(status, workflow, terminal["state_update"])
    _validate_terminal_mode_state_snapshot(status, workflow, terminal["state_update"])
    _validate_terminal_evidence_snapshot(status, workflow, checkpoint_index, checkpoint_events, terminal["state_update"])


def _terminal_checkpoint_context(
    workflow: dict[str, Any],
    checkpoint_index: dict[str, Any],
    checkpoint_events: dict[str, dict[str, Any]],
    *,
    status: str,
) -> dict[str, Any]:
    checkpoint_id = _latest_terminal_checkpoint_id(workflow)
    if not checkpoint_id:
        raise ValueError(f"{status} workflow requires a terminal checkpoint")
    if checkpoint_id not in checkpoint_index or checkpoint_id not in checkpoint_events:
        raise ValueError(f"{status} workflow terminal checkpoint is missing matching event")
    checkpoint_meta = checkpoint_index[checkpoint_id]
    event = checkpoint_events[checkpoint_id]
    terminal_status = checkpoint_meta.get("status_after")
    if status in {"completed_unreported", "failed_unreported"} and terminal_status != status:
        raise ValueError(f"{status} workflow terminal checkpoint status mismatch")
    if status == "reported" and terminal_status not in {"completed_unreported", "failed_unreported"}:
        raise ValueError("reported workflow terminal checkpoint must be terminal unreported")
    expected_event_type = "complete" if terminal_status == "completed_unreported" else "fail"
    if event.get("event_type") != expected_event_type:
        raise ValueError(f"{status} workflow terminal checkpoint event_type mismatch")
    state_update = (event.get("payload") or {}).get("state_update") or {}
    if state_update.get("checkpoint_type") != "terminal":
        raise ValueError(f"{status} workflow requires checkpoint_type=terminal")
    if state_update.get("status_after") != terminal_status:
        raise ValueError(f"{status} workflow terminal state_update status mismatch")
    return {
        "checkpoint_id": checkpoint_id,
        "checkpoint_meta": checkpoint_meta,
        "event": event,
        "state_update": state_update,
        "terminal_status": terminal_status,
    }


def _validate_terminal_final_status_snapshot(status: str, workflow: dict[str, Any], state_update: dict[str, Any]) -> None:
    if workflow.get("final_status") != state_update.get("final_status"):
        raise ValueError(f"{status} workflow final_status must match terminal checkpoint final_status")


def _validate_terminal_mode_state_snapshot(status: str, workflow: dict[str, Any], state_update: dict[str, Any]) -> None:
    mode_state_update = state_update.get("mode_state_update")
    state_key = f"{workflow.get('kind')}_state"
    if state_key not in workflow:
        return
    if not isinstance(mode_state_update, dict):
        raise ValueError(f"{status} workflow terminal checkpoint requires {state_key} snapshot")
    state = workflow.get(state_key)
    if not isinstance(state, dict) or state != mode_state_update:
        raise ValueError(f"{status} workflow {state_key} must match terminal checkpoint {state_key}")


def _validate_terminal_evidence_snapshot(
    status: str,
    workflow: dict[str, Any],
    checkpoint_index: dict[str, Any],
    checkpoint_events: dict[str, dict[str, Any]],
    state_update: dict[str, Any],
) -> None:
    terminal_evidence = _terminal_checkpoint_evidence(state_update, checkpoint_events)
    verification_evidence = (workflow.get("verification") or {}).get("evidence") or []
    checkpoint_evidence = _checkpoint_evidence_sequence(checkpoint_index, checkpoint_events)
    if verification_evidence != checkpoint_evidence:
        raise ValueError(f"{status} workflow verification evidence must match checkpoint-backed terminal evidence sequence")
    if state_update.get("status_after") == "completed_unreported":
        if not isinstance(terminal_evidence, dict) or not checkpoint_evidence or checkpoint_evidence[-1] != terminal_evidence:
            raise ValueError(f"{status} workflow verification evidence must match checkpoint-backed terminal evidence sequence")


def _terminal_checkpoint_evidence(state_update: dict[str, Any], checkpoint_events: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for event in checkpoint_events.values():
        payload = event.get("payload") or {}
        if payload.get("state_update") != state_update:
            continue
        item = payload.get("evidence")
        if item is None:
            item = state_update.get("terminal_evidence")
        if item is not None and not isinstance(item, dict):
            raise ValueError("terminal checkpoint evidence must be an object")
        return item
    return None


def _checkpoint_evidence_sequence(
    checkpoint_index: dict[str, Any],
    checkpoint_events: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    ordered_checkpoints = sorted(
        checkpoint_index.items(),
        key=lambda item: item[1].get("checkpoint_seq") if isinstance(item[1], dict) else 0,
    )
    for checkpoint_id, _checkpoint_meta in ordered_checkpoints:
        event = checkpoint_events.get(checkpoint_id)
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") or {}
        item = payload.get("evidence")
        if item is None:
            item = (payload.get("state_update") or {}).get("terminal_evidence")
        if item is not None:
            if not isinstance(item, dict):
                raise ValueError(f"checkpoint {checkpoint_id} evidence must be an object")
            evidence.append(item)
    return evidence


def _validate_plan_state_integrity(workflow: dict[str, Any]) -> None:
    if workflow.get("kind") != "plan":
        return
    state = workflow.get("plan_state")
    if not isinstance(state, dict):
        raise ValueError("plan workflow requires plan_state object")
    if not state:
        has_plan_artifact = any(
            isinstance(artifact, dict) and artifact.get("artifact_id") == "plan-final"
            for artifact in workflow.get("artifacts", [])
        )
        if workflow.get("status") in {"completed_unreported", "failed_unreported", "reported"} or has_plan_artifact:
            raise ValueError("terminal or artifact-backed plan workflow requires populated plan_state")
        return
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
    artifact_id = state.get("final_plan_artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError("plan_state final_plan_artifact_id must be a non-empty string")
    matches = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == artifact_id
    ]
    if len(matches) != 1:
        raise ValueError(f"plan_state final_plan_artifact_id must match exactly one artifact: {artifact_id!r}")
    artifact = matches[0]
    if artifact.get("kind") != "plan":
        raise ValueError("plan_state final_plan_artifact_id must reference a plan artifact")
    if state.get("final_plan_artifact_path") != artifact.get("path"):
        raise ValueError("plan_state final_plan_artifact_path must match registered artifact path")
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
    for key in ("objective", "next_action", "promotion_recommendation"):
        if not isinstance(state.get(key), str) or not state.get(key):
            raise ValueError(f"plan_state {key} must be a non-empty string")
    if not isinstance(state.get("promoted_to_goal"), bool):
        raise ValueError("plan_state promoted_to_goal must be a boolean")


def _validate_goal_state_integrity(store: WorkflowStore, workflow: dict[str, Any]) -> None:
    if workflow.get("kind") != "goal":
        return
    state = workflow.get("goal_state")
    if not isinstance(state, dict):
        raise ValueError("goal workflow requires goal_state object")
    terminal_goal = workflow.get("status") in {"completed_unreported", "failed_unreported", "reported"}
    has_plan_artifact = any(
        isinstance(artifact, dict) and artifact.get("artifact_id") == GOAL_PLAN_ARTIFACT_ID
        for artifact in workflow.get("artifacts", [])
    )
    if not state:
        if terminal_goal or has_plan_artifact:
            raise ValueError("terminal or plan-artifact-backed goal workflow requires populated goal_state")
        return
    residuals = validate_goal_state(
        state,
        workflow=workflow,
        terminal=terminal_goal,
        final_status=workflow.get("final_status") if isinstance(workflow.get("final_status"), dict) else None,
    )
    artifact_id = state.get("final_plan_artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError("goal_state final_plan_artifact_id must be a non-empty string")
    matches = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == artifact_id
    ]
    if len(matches) != 1:
        raise ValueError(f"goal_state final_plan_artifact_id must match exactly one artifact: {artifact_id!r}")
    artifact = matches[0]
    if artifact.get("kind") != "plan":
        raise ValueError("goal_state final_plan_artifact_id must reference a plan artifact")
    if state.get("final_plan_artifact_path") != artifact.get("path"):
        raise ValueError("goal_state final_plan_artifact_path must match registered artifact path")
    promotion = state.get("plan_artifact_promotion") or {}
    if promotion.get("plan_artifact_path") != artifact.get("path"):
        raise ValueError("goal_state promoted artifact path must match registered artifact path")
    if promotion.get("plan_artifact_hash") != artifact.get("sha256"):
        raise ValueError("goal_state promoted artifact hash must match registered artifact hash")
    if (state.get("plan_accepted") or {}).get("plan_artifact_hash") != artifact.get("sha256"):
        raise ValueError("goal_state plan_accepted hash must match registered artifact hash")
    if terminal_goal and artifact_id not in [
        ref
        for evidence in (workflow.get("verification") or {}).get("evidence") or []
        if isinstance(evidence, dict)
        for ref in evidence.get("artifact_refs") or []
    ]:
        raise ValueError("terminal goal workflow evidence must reference final_plan_artifact_id")
    events = _read_workflow_events(store, workflow["workflow_id"])
    if state.get("execution_performed") is True:
        _validate_goal_child_execution(store, workflow, state, events=events)
        validate_phase5b_child_delivery_state(state, terminal=terminal_goal)
        _validate_phase5b_owner_waiver_events(state, events)
    if terminal_goal:
        validate_phase5a_evidence_contract("goal", workflow=workflow, state=state)
    plan_accepted_events = [
        event
        for event in events
        if event.get("event_type") == "plan_accepted"
    ]
    matching_acceptance = [
        event
        for event in plan_accepted_events
        if event.get("payload") == state.get("plan_accepted")
    ]
    if terminal_goal and len(matching_acceptance) != 1:
        raise ValueError("terminal goal workflow requires exactly one matching plan_accepted event")
    if terminal_goal and any(event.get("payload") != state.get("plan_accepted") for event in plan_accepted_events):
        raise ValueError("terminal goal workflow has conflicting plan_accepted event")
    expected_plan = render_goal_plan(
        GoalRecord(
            objective=state["objective"],
            non_goals=state["non_goals"],
            success_criteria=state["success_criteria"],
            assumptions=state["assumptions"],
            approval_boundaries=state["approval_boundaries"],
            slice_queue=state["slice_queue"],
            plan_accepted=state["plan_accepted"],
            evidence_completion_check=state["evidence_completion_check"],
            plan_artifact_promotion=state["plan_artifact_promotion"],
            child_workflow_refs=state["child_workflow_refs"],
            residuals=residuals,
            final_report_summary=state["final_report_summary"],
        )
    )
    if Path(artifact["path"]).read_text(encoding="utf-8") != expected_plan:
        raise ValueError("goal plan artifact must match goal_state")


def _validate_goal_child_execution(
    store: WorkflowStore,
    workflow: dict[str, Any],
    state: dict[str, Any],
    *,
    events: list[dict[str, Any]],
) -> None:
    refs = state.get("execution_evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("goal execution_performed=true requires execution_evidence_refs")
    if state.get("execution_capability") != "child_workflows":
        raise ValueError("goal execution_performed=true requires child_workflows capability")
    if state.get("synthetic_report") is not False:
        raise ValueError("goal execution_performed=true requires synthetic_report=false")
    if state.get("runner_ref") != "trusted-goal-child-workflow-collector-v1":
        raise ValueError("goal execution_performed=true has untrusted runner_ref")
    child_refs = state.get("child_workflow_refs") or []
    child_ids = [item.get("workflow_id") for item in child_refs if isinstance(item, dict)]
    _require_unique_strings(refs, "goal execution_evidence_refs")
    _require_unique_strings(child_ids, "goal child_workflow_refs workflow_id")
    _require_unique_strings(workflow.get("child_workflow_ids") or [], "goal workflow child_workflow_ids")
    if sorted(refs) != sorted(child_ids):
        raise ValueError("goal execution evidence refs must match child_workflow_refs")
    if sorted(workflow.get("child_workflow_ids") or []) != sorted(child_ids):
        raise ValueError("goal workflow child_workflow_ids must match child_workflow_refs")
    linked_child_ids = _workflow_ids_with_parent(store, workflow["workflow_id"])
    if sorted(linked_child_ids) != sorted(child_ids):
        raise ValueError("goal parent linked child workflows must match child_workflow_refs")
    parent_child_event_ids = [
        (event.get("payload") or {}).get("child_workflow_id")
        for event in events
        if event.get("event_type") in {"child_creation_intent", "child_workflow_created", "child_workflow_collected"}
    ]
    if any(not isinstance(value, str) or not value for value in parent_child_event_ids):
        raise ValueError("goal parent child workflow event ids must contain non-empty strings")
    if not set(parent_child_event_ids).issubset(set(child_ids)):
        raise ValueError("goal parent child workflow events must match child_workflow_refs")
    collection = state.get("child_collection_status")
    if not isinstance(collection, dict) or collection.get("complete") is not True:
        raise ValueError("goal execution_performed=true requires complete child_collection_status")
    _require_unique_strings(collection.get("required_child_workflow_ids") or [], "goal child_collection_status required ids")
    _require_unique_strings(collection.get("collected_child_workflow_ids") or [], "goal child_collection_status collected ids")
    if sorted(collection.get("required_child_workflow_ids") or []) != sorted(child_ids):
        raise ValueError("goal child_collection_status required ids must match child refs")
    if sorted(collection.get("collected_child_workflow_ids") or []) != sorted(child_ids):
        raise ValueError("goal child_collection_status collected ids must match child refs")
    collection_children = collection.get("children") or []
    collection_child_ids = [
        item.get("workflow_id")
        for item in collection_children
        if isinstance(item, dict)
    ]
    _require_unique_strings(collection_child_ids, "goal child_collection_status children workflow_id")
    if sorted(collection_child_ids) != sorted(child_ids):
        raise ValueError("goal child_collection_status children must match child refs")
    for child_ref in child_refs:
        child_id = child_ref["workflow_id"]
        child = store.load_workflow(child_id)
        collection_child = next(
            item
            for item in collection_children
            if isinstance(item, dict) and item.get("workflow_id") == child_id
        )
        if child.get("parent_workflow_id") != workflow["workflow_id"]:
            raise ValueError("goal child workflow parent_workflow_id must point to parent")
        if child.get("kind") != child_ref["kind"]:
            raise ValueError("goal child workflow kind must match child_workflow_refs")
        if child.get("owner_session_key") != (workflow.get("owner_session_key") or ""):
            raise ValueError("goal child workflow owner_session_key must match parent")
        if child.get("visible_delivery") != (workflow.get("visible_delivery") or {}):
            raise ValueError("goal child workflow visible_delivery must match parent")
        if child.get("source_request") != _expected_goal_child_request(workflow, role=child_ref["kind"]):
            raise ValueError("goal child workflow source_request must match deterministic child request")
        if child.get("status") not in {"completed_unreported", "failed_unreported", "reported"}:
            raise ValueError("goal child workflow collection requires terminal child status")
        terminal_status = child_ref.get("terminal_status")
        if terminal_status not in {"completed_unreported", "failed_unreported", "reported"}:
            raise ValueError("goal child workflow terminal_status must be terminal")
        if child.get("status") != terminal_status and not (
            child.get("status") == "reported"
            and terminal_status in {"completed_unreported", "failed_unreported"}
        ):
            raise ValueError("goal child workflow terminal_status must match child workflow")
        result = (child.get("final_status") or {}).get("result")
        if result != child_ref.get("result"):
            raise ValueError("goal child workflow result must match child_workflow_refs")
        expected_status = "completed" if terminal_status in {"completed_unreported", "reported"} and result in {"pass", "pass_with_risks"} else "blocked"
        if child_ref.get("status") != expected_status:
            raise ValueError("goal child workflow ref status must match child terminal result")
        if child_ref.get("final_status") != child.get("final_status"):
            raise ValueError("goal child workflow ref final_status must match child workflow")
        expected_evidence_refs = [
            ref
            for evidence in (child.get("verification") or {}).get("evidence") or []
            if isinstance(evidence, dict)
            for ref in evidence.get("artifact_refs") or []
        ]
        if child_ref.get("evidence_refs") != expected_evidence_refs:
            raise ValueError("goal child workflow ref evidence_refs must match child workflow")
        intent_events = [
            event
            for event in events
            if event.get("event_type") == "child_creation_intent"
            and (event.get("payload") or {}).get("child_workflow_id") == child_id
        ]
        created_events = [
            event
            for event in events
            if event.get("event_type") == "child_workflow_created"
            and (event.get("payload") or {}).get("child_workflow_id") == child_id
        ]
        collected_events = [
            event
            for event in events
            if event.get("event_type") == "child_workflow_collected"
            and (event.get("payload") or {}).get("child_workflow_id") == child_id
        ]
        if len(intent_events) != 1:
            raise ValueError("goal requires exactly one child_creation_intent event per child")
        if len(created_events) != 1:
            raise ValueError("goal requires exactly one child_workflow_created event per child")
        if len(collected_events) != 1:
            raise ValueError("goal requires exactly one child_workflow_collected event per child")
        event_ids = [event.get("event_id") for event in events]
        if not (
            event_ids.index(intent_events[0]["event_id"])
            < event_ids.index(created_events[0]["event_id"])
            < event_ids.index(collected_events[0]["event_id"])
        ):
            raise ValueError("goal child workflow events must be ordered intent -> created -> collected")
        intent_payload = intent_events[0].get("payload") or {}
        created_payload = created_events[0].get("payload") or {}
        collected_payload = collected_events[0].get("payload") or {}
        if intent_payload.get("child_role") != child_ref["kind"]:
            raise ValueError("goal child_creation_intent child_role must match child ref")
        if intent_payload.get("required_for_parent_completion") is not True:
            raise ValueError("goal child_creation_intent must mark required_for_parent_completion=true")
        if created_payload.get("child_role") != child_ref["kind"]:
            raise ValueError("goal child_workflow_created child_role must match child ref")
        if created_payload.get("required_for_parent_completion") is not True:
            raise ValueError("goal child_workflow_created must mark required_for_parent_completion=true")
        if collected_payload.get("child_role") != child_ref["kind"]:
            raise ValueError("goal child_workflow_collected child_role must match child ref")
        if collected_payload.get("terminal_status") != terminal_status:
            raise ValueError("goal child_workflow_collected terminal_status must match child ref")
        if collected_payload.get("result") != result:
            raise ValueError("goal child_workflow_collected result must match child workflow")
        if collection_child.get("kind") != child_ref["kind"]:
            raise ValueError("goal child_collection_status child kind must match child ref")
        if collection_child.get("terminal_status") != terminal_status:
            raise ValueError("goal child_collection_status child terminal_status must match child ref")
        if collection_child.get("result") != result:
            raise ValueError("goal child_collection_status child result must match child workflow")
        child_linked_events = [
            event
            for event in _read_workflow_events(store, child_id)
            if event.get("event_type") == "parent_linked"
            and (event.get("payload") or {}).get("parent_workflow_id") == workflow["workflow_id"]
        ]
        if len(child_linked_events) != 1:
            raise ValueError("goal child workflow requires exactly one parent_linked event")
        child_linked_payload = child_linked_events[0].get("payload") or {}
        if child_linked_payload.get("child_role") != child_ref["kind"]:
            raise ValueError("goal child parent_linked child_role must match child ref")
        if child_linked_payload.get("required_for_parent_completion") is not True:
            raise ValueError("goal child parent_linked must mark required_for_parent_completion=true")
        child_delivery_mode = phase5b_child_delivery_mode(state, child_id)
        if workflow.get("status") == "reported":
            if child_delivery_mode == PHASE5B_PARENT_SUMMARY_MODE:
                if child.get("status") == "reported":
                    raise ValueError("reported goal parent_summary_only child must not be separately reported")
            elif child_delivery_mode == PHASE5B_OWNER_WAIVER_MODE:
                if child.get("status") == "reported":
                    raise ValueError("reported goal waived child must not be separately reported")
            elif child.get("status") != "reported":
                raise ValueError("reported goal cannot have unreported required child workflows")


def _expected_goal_child_request(parent: dict[str, Any], *, role: str) -> str:
    text = parent.get("source_request") or parent.get("objective") or ""
    if role == "verify":
        return f"Verify required goal child evidence for: {text}"
    return f"Converge required goal child execution for: {text}"


def _workflow_ids_with_parent(store: WorkflowStore, parent_id: str) -> list[str]:
    workflows_dir = store.root / "workflows"
    if not workflows_dir.exists():
        return []
    linked: list[str] = []
    for path in workflows_dir.glob("*/workflow.json"):
        try:
            workflow = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if workflow.get("parent_workflow_id") == parent_id:
            linked.append(workflow.get("workflow_id"))
    return linked


def _require_unique_strings(values: list[Any], label: str) -> None:
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")


def _validate_reported_goal_child_integrity(store: WorkflowStore, workflow: dict[str, Any]) -> None:
    state = workflow.get("goal_state")
    if not isinstance(state, dict) or state.get("execution_performed") is not True:
        return
    _validate_goal_child_execution(store, workflow, state, events=_read_workflow_events(store, workflow["workflow_id"]))


def _validate_phase5b_owner_waiver_events(state: dict[str, Any], events: list[dict[str, Any]]) -> None:
    transitions = state.get("child_delivery_mode_transitions") or []
    waiver_transitions = [
        transition
        for transition in transitions
        if isinstance(transition, dict) and transition.get("to_mode") == "waived_with_owner_proof"
    ]
    if not waiver_transitions:
        return
    events_by_id = {
        event.get("event_id"): event
        for event in events
        if isinstance(event, dict) and isinstance(event.get("event_id"), str)
    }
    for transition in waiver_transitions:
        waiver_ref = transition.get("owner_waiver_ref")
        event = events_by_id.get(waiver_ref)
        if not isinstance(event, dict) or event.get("event_type") != "owner_decision":
            raise ValueError("goal Phase 5B waived child delivery requires matching owner_decision event")
        payload = event.get("payload") or {}
        if payload.get("decision") != "waive_child_visible_report":
            raise ValueError("goal Phase 5B owner waiver decision must waive child visible report")
        if payload.get("child_workflow_id") != transition.get("workflow_id"):
            raise ValueError("goal Phase 5B owner waiver child_workflow_id must match transition")
        if not isinstance(payload.get("reason"), str) or not payload["reason"]:
            raise ValueError("goal Phase 5B owner waiver requires reason")
        if not isinstance(payload.get("residual_handling"), str) or not payload["residual_handling"]:
            raise ValueError("goal Phase 5B owner waiver requires residual_handling")


def _child_visible_report_block_reason(store: WorkflowStore, workflow: dict[str, Any]) -> str | None:
    parent_id = workflow.get("parent_workflow_id")
    if not isinstance(parent_id, str) or not parent_id:
        return None
    if workflow.get("status") not in {"completed_unreported", "failed_unreported"}:
        return None
    try:
        parent = store.load_workflow(parent_id)
    except FileNotFoundError:
        return None
    if parent.get("kind") != "goal" or parent.get("status") != "reported":
        return None
    state = parent.get("goal_state")
    if not isinstance(state, dict):
        return None
    if phase5b_child_delivery_mode(state, workflow["workflow_id"]) != PHASE5B_PARENT_SUMMARY_MODE:
        return None
    guard = state.get("duplicate_report_guard")
    if isinstance(guard, dict) and guard.get("parent_must_not_duplicate_child_reports") is True:
        return "Phase 5B duplicate_report_guard blocks child visible report after parent_summary_only parent report"
    return None


def _read_workflow_events(store: WorkflowStore, workflow_id: str) -> list[dict[str, Any]]:
    events_path = store.workflow_dir(workflow_id) / "events.jsonl"
    if not events_path.exists():
        return []
    return [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _validate_verify_state_integrity(store: WorkflowStore, workflow: dict[str, Any]) -> None:
    if workflow.get("kind") != "verify":
        return
    state = workflow.get("verify_state")
    if not isinstance(state, dict):
        raise ValueError("verify workflow requires verify_state object")
    terminal_verify = workflow.get("status") in {"completed_unreported", "failed_unreported", "reported"}
    if not state:
        has_report_artifact = any(
            isinstance(artifact, dict) and artifact.get("artifact_id") == VERIFY_REPORT_ARTIFACT_ID
            for artifact in workflow.get("artifacts", [])
        )
        if terminal_verify or has_report_artifact:
            raise ValueError("terminal or artifact-backed verify workflow requires populated verify_state")
        return
    required = {
        "final_report_artifact_id",
        "final_report_artifact_path",
        "target",
        "check_plan",
        "deterministic_checks",
        "reviewer_findings",
        "verdict",
        "evidence",
        "residuals",
        "final_report_summary",
    }
    missing = sorted(required - set(state))
    if missing:
        raise ValueError(f"verify_state is missing required fields: {missing!r}")
    verdict = state.get("verdict")
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"verify_state verdict is invalid: {verdict!r}")
    artifact_id = state.get("final_report_artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError("verify_state final_report_artifact_id must be a non-empty string")
    matches = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == artifact_id
    ]
    if len(matches) != 1:
        raise ValueError(f"verify_state final_report_artifact_id must match exactly one artifact: {artifact_id!r}")
    artifact = matches[0]
    if artifact.get("kind") != "report":
        raise ValueError("verify_state final_report_artifact_id must reference a report artifact")
    if state.get("final_report_artifact_path") != artifact.get("path"):
        raise ValueError("verify_state final_report_artifact_path must match registered artifact path")
    for key in ("check_plan", "deterministic_checks", "reviewer_findings", "evidence"):
        if not isinstance(state.get(key), list):
            raise ValueError(f"verify_state {key} must be an array")
    if terminal_verify and not state["evidence"]:
        raise ValueError("terminal verify workflow requires evidence")
    if terminal_verify and not any(artifact_id in (evidence.get("artifact_refs") or []) for evidence in state["evidence"] if isinstance(evidence, dict)):
        raise ValueError("terminal verify workflow evidence must reference final_report_artifact_id")
    if terminal_verify:
        validate_phase5a_evidence_contract("verify", workflow=workflow, state=state)
    for key in ("target", "verdict", "final_report_summary"):
        if not isinstance(state.get(key), str) or not state.get(key):
            raise ValueError(f"verify_state {key} must be a non-empty string")
    residuals = normalize_residuals(state.get("residuals"))
    lint_verdict_residuals(verdict, residuals)
    final_status = workflow.get("final_status")
    if isinstance(final_status, dict):
        if final_status.get("result") != verdict:
            raise ValueError("verify_state verdict must match final_status.result")
        final_status_residuals = normalize_residuals(final_status.get("residuals"))
        lint_verdict_residuals(final_status.get("result"), final_status_residuals)
        if final_status_residuals != residuals:
            raise ValueError("verify_state residuals must match final_status.residuals")
    worklog_path = store.workflow_dir(workflow["workflow_id"]) / "worklog.md"
    worklog_text = worklog_path.read_text(encoding="utf-8") if worklog_path.exists() else ""
    for evidence in state["evidence"]:
        if not isinstance(evidence, dict):
            raise ValueError("verify_state evidence entries must be objects")
        validate_evidence_object(evidence)
        validate_evidence_artifact_refs(workflow, evidence, worklog_text=worklog_text)
    if state.get("execution_performed") is True:
        _validate_verify_execution_evidence(store, workflow, state, worklog_text=worklog_text)
    if terminal_verify:
        report_evidence = [
            evidence
            for evidence in state["evidence"]
            if isinstance(evidence, dict) and artifact_id not in (evidence.get("artifact_refs") or [])
        ]
        expected_report = render_verify_report(
            VerifyRecord(
                target=state["target"],
                check_plan=state["check_plan"],
                deterministic_checks=state["deterministic_checks"],
                reviewer_findings=state["reviewer_findings"],
                verdict=state["verdict"],
                evidence_records=report_evidence,
                residuals=residuals,
                final_report_summary=state["final_report_summary"],
            )
        )
        if Path(artifact["path"]).read_text(encoding="utf-8") != expected_report:
            raise ValueError("verify report artifact must match verify_state")


def _validate_verify_execution_evidence(
    store: WorkflowStore,
    workflow: dict[str, Any],
    state: dict[str, Any],
    *,
    worklog_text: str,
) -> None:
    refs = state.get("execution_evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("verify execution_performed=true requires execution_evidence_refs")
    if state.get("synthetic_report") is not False:
        raise ValueError("verify execution_performed=true requires synthetic_report=false")
    if state.get("execution_capability") == "delegated_agents":
        _validate_specialist_execution_evidence(store, workflow, state, mode="verify", worklog_text=worklog_text)
        return
    if "verify-deterministic-checks" not in refs:
        raise ValueError("verify execution evidence refs must include verify-deterministic-checks")
    if state.get("execution_capability") != "local_checks":
        raise ValueError("verify execution_performed=true requires local_checks capability")
    deterministic_evidence = [
        evidence
        for evidence in state.get("evidence", [])
        if isinstance(evidence, dict) and "verify-deterministic-checks" in (evidence.get("artifact_refs") or [])
    ]
    if not deterministic_evidence:
        raise ValueError("verify execution_performed=true requires deterministic evidence record")
    for evidence in deterministic_evidence:
        validate_evidence_artifact_refs(workflow, evidence, worklog_text=worklog_text)
    events = _read_workflow_events(store, workflow["workflow_id"])
    deterministic_events = [
        event
        for event in events
        if event.get("event_type") == "deterministic_check_recorded"
        and (event.get("payload") or {}).get("artifact_id") == "verify-deterministic-checks"
    ]
    if not deterministic_events:
        raise ValueError("verify execution_performed=true requires deterministic_check_recorded event")
    if len(deterministic_events) != 1:
        raise ValueError("verify execution_performed=true requires exactly one deterministic_check_recorded event")
    payload = deterministic_events[0].get("payload") or {}
    if payload.get("runner_ref") != "trusted-local-verify-file-inspection-v1":
        raise ValueError("verify deterministic_check_recorded event has untrusted runner_ref")
    if payload.get("status") != "pass":
        raise ValueError("verify deterministic_check_recorded event must record pass status")
    artifact = next(
        (
            item
            for item in workflow.get("artifacts", [])
            if isinstance(item, dict) and item.get("artifact_id") == "verify-deterministic-checks"
        ),
        None,
    )
    if not artifact or artifact.get("kind") != "evidence":
        raise ValueError("verify execution evidence artifact must be registered as evidence")
    artifact_payload = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
    if artifact_payload.get("runner_ref") != payload.get("runner_ref"):
        raise ValueError("verify execution evidence artifact runner_ref must match event")
    checks = artifact_payload.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("verify execution evidence artifact must contain checks")
    if payload.get("check_count") != len(checks):
        raise ValueError("verify deterministic_check_recorded check_count must match evidence artifact")
    if any(not isinstance(check, dict) or check.get("status") != "pass" for check in checks):
        raise ValueError("verify deterministic evidence checks must all pass")


def _validate_specialist_execution_evidence(
    store: WorkflowStore,
    workflow: dict[str, Any],
    state: dict[str, Any],
    *,
    mode: str,
    worklog_text: str | None = None,
) -> None:
    artifact_id = specialist_artifact_id(mode)
    refs = state.get("execution_evidence_refs")
    if not isinstance(refs, list) or artifact_id not in refs:
        raise ValueError(f"{mode} specialist execution evidence refs must include {artifact_id}")
    if state.get("runner_ref") != SPECIALIST_REVIEW_RUNNER_REF:
        raise ValueError(f"{mode} specialist execution has untrusted runner_ref")
    specialist_state = _specialist_state_from_mode_state(state)
    validate_specialist_state(specialist_state)
    evidence = [
        item
        for item in state.get("evidence", [])
        if isinstance(item, dict) and artifact_id in (item.get("artifact_refs") or [])
    ]
    if len(evidence) != 1:
        raise ValueError(f"{mode} specialist execution requires exactly one specialist evidence record")
    if worklog_text is not None:
        validate_evidence_artifact_refs(workflow, evidence[0], worklog_text=worklog_text)
    artifacts = [
        item
        for item in workflow.get("artifacts", [])
        if isinstance(item, dict) and item.get("artifact_id") == artifact_id
    ]
    if len(artifacts) != 1 or artifacts[0].get("kind") != "evidence":
        raise ValueError(f"{mode} specialist execution artifact must be registered exactly once as evidence")
    artifact_payload = json.loads(Path(artifacts[0]["path"]).read_text(encoding="utf-8"))
    if artifact_payload.get("specialist_review") != specialist_state:
        raise ValueError(f"{mode} specialist artifact must match mode state")
    events = _read_workflow_events(store, workflow["workflow_id"])
    for event_type in ("agent_panel_requested", "agent_findings_recorded", "finding_arbitrated"):
        matches = [
            event
            for event in events
            if event.get("event_type") == event_type
            and (event.get("payload") or {}).get("artifact_id") == artifact_id
        ]
        if len(matches) != 1:
            raise ValueError(f"{mode} specialist execution requires exactly one {event_type} event")
        payload = matches[0].get("payload") or {}
        if payload.get("runner_ref") != SPECIALIST_REVIEW_RUNNER_REF:
            raise ValueError(f"{mode} specialist event has untrusted runner_ref")
        if payload.get("mode") != mode:
            raise ValueError(f"{mode} specialist event mode must match")
        if payload.get("finding_count") != len(specialist_state["agent_finding_refs"]):
            raise ValueError(f"{mode} specialist event finding_count must match state")
        if payload.get("arbitration_count") != len(specialist_state["finding_arbitration"]):
            raise ValueError(f"{mode} specialist event arbitration_count must match state")


def _specialist_state_from_mode_state(state: dict[str, Any]) -> dict[str, Any]:
    return {key: state[key] for key in (
        "review_panel_spec",
        "deterministic_check_results",
        "agent_finding_refs",
        "raw_finding_to_group_map",
        "finding_arbitration",
        "accepted_change_refs",
        "original_target_gate",
        "delta_regression_gate",
        "follow_up_round_required",
        "max_rounds_default",
        "max_rounds",
        "round_index",
        "stop_reason",
        "owner_stop_ref",
        "round_stop_proof",
    )}


def _validate_conv_state_integrity(store: WorkflowStore, workflow: dict[str, Any]) -> None:
    if workflow.get("kind") != "conv":
        return
    state = workflow.get("conv_state")
    if not isinstance(state, dict):
        raise ValueError("conv workflow requires conv_state object")
    terminal_conv = workflow.get("status") in {"completed_unreported", "failed_unreported", "reported"}
    has_report_artifact = any(
        isinstance(artifact, dict) and artifact.get("artifact_id") == CONV_REPORT_ARTIFACT_ID
        for artifact in workflow.get("artifacts", [])
    )
    if not state:
        if terminal_conv or has_report_artifact:
            raise ValueError("terminal or artifact-backed conv workflow requires populated conv_state")
        return
    residuals = validate_conv_state(state, terminal=terminal_conv, final_status=workflow.get("final_status"))
    artifact_id = state.get("final_report_artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise ValueError("conv_state final_report_artifact_id must be a non-empty string")
    matches = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == artifact_id
    ]
    if len(matches) != 1:
        raise ValueError(f"conv_state final_report_artifact_id must match exactly one artifact: {artifact_id!r}")
    artifact = matches[0]
    if artifact.get("kind") != "report":
        raise ValueError("conv_state final_report_artifact_id must reference a report artifact")
    if state.get("final_report_artifact_path") != artifact.get("path"):
        raise ValueError("conv_state final_report_artifact_path must match registered artifact path")
    if terminal_conv and artifact_id not in [
        ref
        for evidence in (workflow.get("verification") or {}).get("evidence") or []
        if isinstance(evidence, dict)
        for ref in evidence.get("artifact_refs") or []
    ]:
        raise ValueError("terminal conv workflow evidence must reference final_report_artifact_id")
    if terminal_conv:
        validate_phase5a_evidence_contract("conv", workflow=workflow, state=state)
    expected_report = render_conv_report(
        ConvRecord(
            target=state["target"],
            max_rounds=state["max_rounds"],
            rounds=[
                ConvRound(
                    round_index=item["round_index"],
                    target_ref=item["target_ref"],
                    original_target_gate=item["original_target_gate"],
                    delta_gate=item["delta_gate"],
                    findings=item["findings"],
                    material_changes=item["material_changes"],
                    follow_up_required=item["follow_up_required"],
                    evidence_sufficient=item["evidence_sufficient"],
                    summary=item["summary"],
                )
                for item in state["rounds"]
            ],
            stop_condition=state["stop_condition"],
            stop_reason=state["stop_reason"],
            explicit_stop_proof=state["explicit_stop_proof"],
            residuals=residuals,
            final_report_summary=state["final_report_summary"],
        )
    )
    if Path(artifact["path"]).read_text(encoding="utf-8") != expected_report:
        raise ValueError("conv report artifact must match conv_state")
    if state.get("execution_performed") is True:
        _validate_conv_execution_evidence(store, workflow, state)


def _validate_conv_execution_evidence(store: WorkflowStore, workflow: dict[str, Any], state: dict[str, Any]) -> None:
    refs = state.get("execution_evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("conv execution_performed=true requires execution_evidence_refs")
    if state.get("execution_capability") == "delegated_agents":
        _validate_specialist_execution_evidence(store, workflow, state, mode="conv")
        return
    if CONV_ROUND_EXECUTION_ARTIFACT_ID not in refs:
        raise ValueError("conv execution evidence refs must include conv-round-execution")
    if state.get("execution_capability") != "local_rounds":
        raise ValueError("conv execution_performed=true requires local_rounds capability")
    if state.get("synthetic_report") is not False:
        raise ValueError("conv execution_performed=true requires synthetic_report=false")
    if state.get("runner_ref") != CONV_LOCAL_RUNNER_REF:
        raise ValueError("conv execution_performed=true has untrusted runner_ref")
    artifacts = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == CONV_ROUND_EXECUTION_ARTIFACT_ID
    ]
    if len(artifacts) != 1:
        raise ValueError("conv execution evidence artifact must be registered exactly once")
    artifact = artifacts[0]
    if artifact.get("kind") != "evidence":
        raise ValueError("conv execution evidence artifact must be registered as evidence")
    artifact_payload = json.loads(Path(artifact["path"]).read_text(encoding="utf-8"))
    if artifact_payload.get("runner_ref") != CONV_LOCAL_RUNNER_REF:
        raise ValueError("conv execution evidence artifact has untrusted runner_ref")
    artifact_rounds = artifact_payload.get("rounds")
    if not isinstance(artifact_rounds, list) or not artifact_rounds:
        raise ValueError("conv execution evidence artifact must contain rounds")
    if len(artifact_rounds) != state.get("round_count"):
        raise ValueError("conv execution evidence round_count must match conv_state")
    for artifact_round, state_round in zip(artifact_rounds, state.get("rounds") or [], strict=True):
        for key in (
            "round_index",
            "target_ref",
            "original_target_gate",
            "delta_gate",
            "findings",
            "material_changes",
            "follow_up_required",
            "evidence_sufficient",
            "summary",
        ):
            if artifact_round.get(key) != state_round.get(key):
                raise ValueError("conv execution evidence rounds must match conv_state")
        checks = artifact_round.get("target_checks")
        if not isinstance(checks, list) or not checks:
            raise ValueError("conv execution evidence round must contain target_checks")
        if any(not isinstance(check, dict) or check.get("status") != "pass" for check in checks):
            raise ValueError("conv execution evidence target checks must all pass")
        for check in checks:
            if check.get("kind") != "file_inspection":
                raise ValueError("conv execution evidence target checks must be file inspections")
            path = check.get("path")
            if not isinstance(path, str) or not path:
                raise ValueError("conv execution evidence target check path must be non-empty")
            target_path = Path(path)
            if not target_path.is_file():
                raise ValueError("conv execution evidence target check path is missing")
            if sha256_file(target_path) != check.get("sha256"):
                raise ValueError("conv execution evidence target check hash is stale")
    events = _read_workflow_events(store, workflow["workflow_id"])
    for round_state in state.get("rounds") or []:
        round_index = round_state["round_index"]
        starts = [
            event
            for event in events
            if event.get("event_type") == "round_start"
            and (event.get("payload") or {}).get("artifact_id") == CONV_ROUND_EXECUTION_ARTIFACT_ID
            and (event.get("payload") or {}).get("round_index") == round_index
        ]
        summaries = [
            event
            for event in events
            if event.get("event_type") == "round_summary"
            and (event.get("payload") or {}).get("artifact_id") == CONV_ROUND_EXECUTION_ARTIFACT_ID
            and (event.get("payload") or {}).get("round_index") == round_index
        ]
        if len(starts) != 1:
            raise ValueError("conv execution_performed=true requires exactly one round_start event per round")
        if len(summaries) != 1:
            raise ValueError("conv execution_performed=true requires exactly one round_summary event per round")
        start_payload = starts[0].get("payload") or {}
        summary_payload = summaries[0].get("payload") or {}
        if start_payload.get("runner_ref") != CONV_LOCAL_RUNNER_REF:
            raise ValueError("conv round_start event has untrusted runner_ref")
        if start_payload.get("target_ref") != round_state["target_ref"]:
            raise ValueError("conv round_start target_ref must match conv_state")
        if start_payload.get("original_target_gate") != round_state["original_target_gate"]:
            raise ValueError("conv round_start original_target_gate must match conv_state")
        if summary_payload.get("runner_ref") != CONV_LOCAL_RUNNER_REF:
            raise ValueError("conv round_summary event has untrusted runner_ref")
        if summary_payload.get("status") != "pass":
            raise ValueError("conv round_summary event must record pass status")
        if summary_payload.get("material_changes") != round_state["material_changes"]:
            raise ValueError("conv round_summary material_changes must match conv_state")
        if summary_payload.get("follow_up_required") != round_state["follow_up_required"]:
            raise ValueError("conv round_summary follow_up_required must match conv_state")
        if summary_payload.get("evidence_sufficient") != round_state["evidence_sufficient"]:
            raise ValueError("conv round_summary evidence_sufficient must match conv_state")


def _validate_report_event_integrity(
    workflow: dict[str, Any],
    delivery_reserved_events: list[dict[str, Any]],
    report_proof_events: list[dict[str, Any]],
    report_sent_events: list[dict[str, Any]],
) -> None:
    visible_state = workflow.get("visible_delivery_state") or {}
    proof = visible_state.get("report_proof")
    reported = visible_state.get("reported")
    if proof is not None and not isinstance(proof, dict):
        raise ValueError("visible_delivery_state.report_proof must be an object")
    if reported is not None and not isinstance(reported, dict):
        raise ValueError("visible_delivery_state.reported must be an object")
    if proof is None and report_proof_events:
        raise ValueError("report_proof event exists without visible_delivery_state.report_proof")
    if proof is not None:
        _validate_report_payload(proof, timestamp_key="recorded_at", label="report_proof")
        if len(report_proof_events) != 1:
            raise ValueError("visible_delivery_state.report_proof requires exactly one report_proof event")
        matching_delivery = _matching_delivery_events(
            delivery_reserved_events,
            reservation_id=proof.get("reservation_id"),
            visible_delivery=proof.get("visible_delivery"),
        )
        if not matching_delivery:
            raise ValueError("visible_delivery_state.report_proof has no matching delivery_reserved event")
        if len(matching_delivery) > 1:
            raise ValueError("visible_delivery_state.report_proof has duplicate matching delivery_reserved events")
        delivery_checkpoint_id = matching_delivery[0].get("checkpoint_id")
        matching = [event for event in report_proof_events if (event.get("payload") or {}) == proof]
        if not matching:
            raise ValueError("visible_delivery_state.report_proof has no matching report_proof event")
        if len(matching) > 1:
            raise ValueError("visible_delivery_state.report_proof has duplicate matching report_proof events")
        if matching[0].get("checkpoint_id") != delivery_checkpoint_id:
            raise ValueError("report_proof checkpoint_id does not match delivery_reserved checkpoint")
    if workflow.get("status") == "reported":
        if proof is None:
            raise ValueError("reported workflow requires visible_delivery_state.report_proof")
        if reported is None:
            raise ValueError("reported workflow requires visible_delivery_state.reported")
    elif reported is not None or report_sent_events:
        raise ValueError("report_sent state requires reported workflow status")
    if reported is None and not report_sent_events:
        return
    if reported is None:
        raise ValueError("report_sent event exists without visible_delivery_state.reported")
    _validate_report_payload(reported, timestamp_key="reported_at", label="reported")
    if len(report_sent_events) != 1:
        raise ValueError("visible_delivery_state.reported requires exactly one report_sent event")
    matching_reported = [event for event in report_sent_events if (event.get("payload") or {}) == reported]
    if not matching_reported:
        raise ValueError("visible_delivery_state.reported has no matching report_sent event")
    if len(matching_reported) > 1:
        raise ValueError("visible_delivery_state.reported has duplicate matching report_sent events")
    if proof is not None:
        for key in ("reservation_id", "delivery_message_id", "visible_delivery"):
            if reported.get(key) != proof.get(key):
                raise ValueError(f"visible_delivery_state.reported {key} does not match report_proof")
        matching_delivery = _matching_delivery_events(
            delivery_reserved_events,
            reservation_id=proof.get("reservation_id"),
            visible_delivery=proof.get("visible_delivery"),
        )
        if len(matching_delivery) == 1 and matching_reported[0].get("checkpoint_id") != matching_delivery[0].get("checkpoint_id"):
            raise ValueError("report_sent checkpoint_id does not match delivery_reserved checkpoint")


def _validate_report_payload(payload: dict[str, Any], *, timestamp_key: str, label: str) -> None:
    for key in ("reservation_id", "delivery_message_id", timestamp_key):
        if not isinstance(payload.get(key), str) or not payload.get(key):
            raise ValueError(f"{label} requires non-empty {key}")
    if payload.get("source_of_truth") != "converge.workflow":
        raise ValueError(f"{label} requires source_of_truth=converge.workflow")
    if label == "report_proof":
        if payload.get("proof_authority") != "converge.report-proof":
            raise ValueError(f"{label} requires proof_authority=converge.report-proof")
    elif label in {"report_sent", "reported"}:
        if payload.get("report_authority") != "converge.complete-reported":
            raise ValueError(f"{label} requires report_authority=converge.complete-reported")
    visible_delivery = payload.get("visible_delivery")
    if not isinstance(visible_delivery, dict) or not visible_delivery:
        raise ValueError(f"{label} requires visible_delivery")
    manual_reconcile = payload.get("manual_reconcile")
    if manual_reconcile is not None and (not isinstance(manual_reconcile, str) or not manual_reconcile):
        raise ValueError(f"{label} manual_reconcile must be non-empty when present")


def _validate_delivery_authority(payload: dict[str, Any], *, label: str) -> None:
    if payload.get("send_authority") != "converge.reserve-delivery":
        raise ValueError(f"{label} requires send_authority=converge.reserve-delivery")
    if payload.get("source_of_truth") != "converge.workflow":
        raise ValueError(f"{label} requires source_of_truth=converge.workflow")


def _validate_delivery_message_id(delivery_message_id: str) -> None:
    if not isinstance(delivery_message_id, str) or not delivery_message_id.strip():
        raise ValueError("delivery_message_id must be non-empty")


def _validate_visible_delivery_arg(visible_delivery: dict[str, Any]) -> None:
    if not isinstance(visible_delivery, dict) or not visible_delivery:
        raise ValueError("visible_delivery must be a non-empty object")
    channel = visible_delivery.get("channel")
    target = visible_delivery.get("target")
    if not isinstance(channel, str) or not channel:
        raise ValueError("visible_delivery.channel must be a non-empty string")
    if not isinstance(target, str) or not target:
        raise ValueError("visible_delivery.target must be a non-empty string")


def build_parser() -> argparse.ArgumentParser:
    json_help = "Accepted for command consistency; output is always JSON."
    parser = JsonArgumentParser(prog="converge")
    parser.add_argument("--state-root", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help=json_help)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=JsonArgumentParser)

    start = sub.add_parser("start")
    start.add_argument("--kind", required=True, choices=["plan", "goal", "verify", "conv"])
    start.add_argument("--text", required=True)
    start.add_argument("--workflow-id")
    start.add_argument("--owner-session-key")
    start.add_argument("--visible-delivery", type=parse_json)
    start.add_argument("--json", action="store_true", help=json_help)
    start.set_defaults(func=cmd_start)

    plan = sub.add_parser("plan")
    plan.add_argument("--text", required=True)
    plan.add_argument("--workflow-id")
    plan.add_argument("--owner-session-key")
    plan.add_argument("--visible-delivery", type=parse_json)
    plan.add_argument("--recovery-lease-id")
    plan.add_argument("--recovery-lease-holder")
    plan.add_argument("--json", action="store_true", help=json_help)
    plan.set_defaults(func=cmd_plan)

    verify = sub.add_parser("verify")
    verify.add_argument("--text", required=True)
    verify.add_argument("--workflow-id")
    verify.add_argument("--owner-session-key")
    verify.add_argument("--visible-delivery", type=parse_json)
    verify.add_argument("--structured-findings-file")
    verify.add_argument("--recovery-lease-id")
    verify.add_argument("--recovery-lease-holder")
    verify.add_argument("--json", action="store_true", help=json_help)
    verify.set_defaults(func=cmd_verify)

    goal = sub.add_parser("goal")
    goal.add_argument("--text", required=True)
    goal.add_argument("--workflow-id")
    goal.add_argument("--owner-session-key")
    goal.add_argument("--visible-delivery", type=parse_json)
    goal.add_argument("--recovery-lease-id")
    goal.add_argument("--recovery-lease-holder")
    goal.add_argument("--json", action="store_true", help=json_help)
    goal.set_defaults(func=cmd_goal)

    conv = sub.add_parser("conv")
    conv.add_argument("--text", required=True)
    conv.add_argument("--workflow-id")
    conv.add_argument("--owner-session-key")
    conv.add_argument("--visible-delivery", type=parse_json)
    conv.add_argument("--structured-findings-file")
    conv.add_argument("--recovery-lease-id")
    conv.add_argument("--recovery-lease-holder")
    conv.add_argument("--json", action="store_true", help=json_help)
    conv.set_defaults(func=cmd_conv)

    command_dry_run = sub.add_parser("command-dry-run")
    command_dry_run.add_argument("--raw-message", required=True)
    command_dry_run.add_argument("--workflow-id")
    command_dry_run.add_argument("--owner-session-key")
    command_dry_run.add_argument("--visible-delivery", type=parse_json)
    command_dry_run.add_argument("--json", action="store_true", help=json_help)
    command_dry_run.set_defaults(func=cmd_command_dry_run)

    status = sub.add_parser("status")
    status.add_argument("--workflow-id", required=True)
    status.add_argument("--json", action="store_true", help=json_help)
    status.set_defaults(func=cmd_status)

    scan = sub.add_parser("scan")
    scan.add_argument("--json", action="store_true", help=json_help)
    scan.set_defaults(func=cmd_scan)

    watchdog_check_parser = sub.add_parser("watchdog-check")
    watchdog_check_parser.add_argument("--include-clean", action="store_true")
    watchdog_check_parser.add_argument("--json", action="store_true", help=json_help)
    watchdog_check_parser.set_defaults(func=cmd_watchdog_check)

    recover = sub.add_parser("recover")
    recover.add_argument("--workflow-id", required=True)
    recover.add_argument("--holder", default="converge-recover")
    recover.add_argument("--lease-seconds", type=int, default=1800)
    recover.add_argument("--json", action="store_true", help=json_help)
    recover.set_defaults(func=cmd_recover)

    advance = sub.add_parser("advance")
    advance.add_argument("--workflow-id", required=True)
    advance.add_argument("--summary")
    advance.add_argument("--summary-file")
    advance.add_argument("--phase-after", default="slice")
    advance.add_argument("--evidence", type=parse_json)
    advance.add_argument("--next-action", type=parse_json)
    advance.add_argument("--residuals", type=parse_json)
    advance.add_argument("--json", action="store_true", help=json_help)
    advance.set_defaults(func=cmd_advance)

    artifact = sub.add_parser("artifact")
    artifact.add_argument("--workflow-id", required=True)
    artifact.add_argument("--kind", required=True, choices=["plan", "evidence", "patch", "report", "context"])
    artifact.add_argument("--path", required=True)
    artifact.add_argument("--artifact-id")
    artifact.add_argument("--note")
    artifact.add_argument("--json", action="store_true", help=json_help)
    artifact.set_defaults(func=cmd_artifact)

    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("--workflow-id", required=True)
    checkpoint.add_argument("--checkpoint-type", required=True, choices=["checkpoint", "advance", "terminal"])
    checkpoint.add_argument("--state-update", required=True, type=parse_json)
    checkpoint.add_argument("--summary")
    checkpoint.add_argument("--summary-file")
    checkpoint.add_argument("--next-action", type=parse_json)
    checkpoint.add_argument("--evidence", type=parse_json)
    checkpoint.add_argument("--json", action="store_true", help=json_help)
    checkpoint.set_defaults(func=cmd_checkpoint)

    append_round = sub.add_parser("append-round")
    append_round.add_argument("--workflow-id", required=True)
    append_round.add_argument("--round", type=int, required=True)
    append_round.add_argument("--summary")
    append_round.add_argument("--summary-file")
    append_round.add_argument("--json", action="store_true", help=json_help)
    append_round.set_defaults(func=cmd_append_round)

    event = sub.add_parser("event")
    event.add_argument("--workflow-id", required=True)
    event.add_argument("--type", required=True)
    event.add_argument("--event-id", required=True)
    event.add_argument("--note")
    event.add_argument("--payload", type=parse_json)
    event.add_argument("--json", action="store_true", help=json_help)
    event.set_defaults(func=cmd_event)

    reserve_delivery = sub.add_parser("reserve-delivery")
    reserve_delivery.add_argument("--workflow-id", required=True)
    reserve_delivery.add_argument("--terminal-status", required=True, choices=["completed", "failed"])
    reserve_delivery.add_argument("--visible-delivery", required=True, type=parse_json)
    reserve_delivery.add_argument("--summary")
    reserve_delivery.add_argument("--summary-file")
    reserve_delivery.add_argument("--terminal-evidence", type=parse_json)
    reserve_delivery.add_argument("--final-status", required=True, type=parse_json)
    reserve_delivery.add_argument("--failure-reason")
    reserve_delivery.add_argument("--residuals", type=parse_json)
    reserve_delivery.add_argument("--reservation-id")
    reserve_delivery.add_argument("--lease-seconds", type=int, default=1800)
    reserve_delivery.add_argument("--json", action="store_true", help=json_help)
    reserve_delivery.set_defaults(func=cmd_reserve_delivery)

    report_proof = sub.add_parser("report-proof")
    report_proof.add_argument("--workflow-id", required=True)
    report_proof.add_argument("--reservation-id", required=True)
    report_proof.add_argument("--delivery-message-id", required=True)
    report_proof.add_argument("--visible-delivery", required=True, type=parse_json)
    report_proof.add_argument("--manual-reconcile")
    report_proof.add_argument("--json", action="store_true", help=json_help)
    report_proof.set_defaults(func=cmd_report_proof)

    complete_reported = sub.add_parser("complete-reported")
    complete_reported.add_argument("--workflow-id", required=True)
    complete_reported.add_argument("--reservation-id", required=True)
    complete_reported.add_argument("--delivery-message-id", required=True)
    complete_reported.add_argument("--visible-delivery", required=True, type=parse_json)
    complete_reported.add_argument("--manual-reconcile")
    complete_reported.add_argument("--json", action="store_true", help=json_help)
    complete_reported.set_defaults(func=cmd_complete_reported)

    validate = sub.add_parser("validate")
    validate.add_argument("--workflow-id")
    validate.add_argument("--sample-docs", action="store_true")
    validate.add_argument("--json", action="store_true", help=json_help)
    validate.set_defaults(func=cmd_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except DeliveryValidationError as exc:
        print_json(_delivery_no_send_payload(args, reason="validation_error", error=str(exc)))
        return 1
    except Exception as exc:
        print_json({"ok": False, "error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
