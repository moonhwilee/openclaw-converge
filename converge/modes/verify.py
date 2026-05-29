"""Verify mode vertical slice."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import ModeHandler, ModeOutcome, apply_execution_truth_block, execution_blocked_final_status
from .evidence_contract import attach_phase5a_evidence_contract
from .execution_truth import classify_execution_markers
from .verify_execution import (
    VERIFY_DETERMINISTIC_CHECK_ARTIFACT_ID,
    deterministic_check_summaries,
    deterministic_evidence_record,
    deterministic_execution_markers,
    run_verify_deterministic_checks,
    write_deterministic_check_artifact,
)
from .specialist_panel import (
    NATIVE_PANEL_RUNNER_REF,
    SOURCE_NATIVE_AGENT_PANEL,
    SOURCE_RUNNER_PROVIDED_PACKET,
    SPECIALIST_REVIEW_RUNNER_REF,
    build_native_specialist_review,
    build_native_agent_pending_collection_state,
    build_specialist_review,
    specialist_artifact_id,
    validate_native_specialist_state,
    validate_specialist_state,
    write_specialist_artifact,
)
from ..agents.openclaw_cli import NativePanelBlockedError
from ..agents.contracts import NativeLaunchRequest, stable_hash
from ..artifacts import now_iso
from ..messages import normalize_residuals
from ..target_refs import merge_inline_target_ref


VERIFY_REPORT_ARTIFACT_ID = "verify-final-report"
VERIFY_VERDICTS = {"pass", "pass_with_risks", "needs_fix", "blocked", "stopped"}


@dataclass(frozen=True)
class VerifyRecord:
    target: str
    check_plan: list[str]
    deterministic_checks: list[str]
    reviewer_findings: list[str]
    verdict: str
    evidence_records: list[dict[str, Any]]
    residuals: dict[str, list[str]]
    final_report_summary: str

    def as_state(self, *, artifact_id: str, artifact_path: str, specialist_state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = {
            "final_report_artifact_id": artifact_id,
            "final_report_artifact_path": artifact_path,
            "target": self.target,
            "check_plan": self.check_plan,
            "deterministic_checks": self.deterministic_checks,
            "reviewer_findings": self.reviewer_findings,
            "verdict": self.verdict,
            "evidence": self.evidence_records,
            "residuals": self.residuals,
            "final_report_summary": self.final_report_summary,
        }
        if specialist_state:
            state.update(specialist_state)
        state.update(classify_execution_markers(self.target, capability="report_scaffold_only"))
        return state


def validate_verify_state(
    state: dict[str, Any],
    *,
    terminal: bool,
    final_status: dict[str, Any] | None,
) -> dict[str, list[str]]:
    if not state:
        if terminal:
            raise ValueError("terminal or artifact-backed verify workflow requires populated verify_state")
        return {}
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
    if verdict not in VERIFY_VERDICTS:
        raise ValueError(f"verify_state verdict is invalid: {verdict!r}")
    for key in ("check_plan", "deterministic_checks", "reviewer_findings", "evidence"):
        if not isinstance(state.get(key), list):
            raise ValueError(f"verify_state {key} must be an array")
    if terminal and not state["evidence"]:
        raise ValueError("terminal verify workflow requires evidence")
    if "review_panel_spec" in state:
        if state.get("execution_source") == SOURCE_RUNNER_PROVIDED_PACKET:
            if state.get("satisfies_native_agent_panel") is not False:
                raise ValueError("runner-provided specialist verify evidence must not satisfy native_agent_panel parity")
            validate_specialist_state(_specialist_state_from_verify(state))
        elif state.get("execution_source") == SOURCE_NATIVE_AGENT_PANEL:
            if state.get("satisfies_native_agent_panel") is not True:
                raise ValueError("native specialist verify evidence must satisfy native_agent_panel parity")
            validate_native_specialist_state(_specialist_state_from_verify(state))
        else:
            raise ValueError("specialist verify evidence has unknown execution_source")
    for key in ("final_report_artifact_id", "final_report_artifact_path", "target", "verdict", "final_report_summary"):
        if not isinstance(state.get(key), str) or not state.get(key):
            raise ValueError(f"verify_state {key} must be a non-empty string")
    residuals = state.get("residuals")
    if not isinstance(residuals, dict):
        raise ValueError("verify_state residuals must be an object")
    if final_status is not None:
        if final_status.get("result") != verdict:
            raise ValueError("verify_state verdict must match final_status.result")
        if normalize_residuals(final_status.get("residuals")) != normalize_residuals(residuals):
            raise ValueError("verify_state residuals must match final_status.residuals")
    return residuals


class VerifyHandler(ModeHandler):
    """Produces an evidence-backed verdict record and final report artifact."""

    kind = "verify"

    def finalize_verify(
        self,
        workflow_id: str,
        *,
        specialist_findings: dict[str, Any] | None = None,
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
        artifact_path = (self.store.workflow_dir(workflow_id) / "artifacts" / "verify-report.md").expanduser().resolve()
        artifact_ref = VERIFY_REPORT_ARTIFACT_ID
        artifact_path_text = str(artifact_path)
        record = build_verify_record(workflow.get("source_request") or workflow.get("objective") or "")
        deterministic_result = run_verify_deterministic_checks(record.target, source_root=Path.cwd())
        deterministic_artifact = _record_deterministic_check_artifact(self, workflow_id, result=deterministic_result)
        deterministic_evidence = (
            deterministic_evidence_record(deterministic_result, artifact_id=deterministic_artifact["artifact_id"])
            if deterministic_artifact
            else None
        )
        evidence = {
            "evidence_key": "verify-final-report",
            "kind": "artifact",
            "summary": "Final verification report registered through the shared artifact path.",
            "artifact_refs": [artifact_ref],
        }
        evidence_records = [*record.evidence_records]
        if deterministic_evidence:
            evidence_records.append(deterministic_evidence)
        if specialist_findings is not None and native_agent_backend is not None:
            raise ValueError("verify cannot combine runner-provided and native panel findings")
        specialist_review = _record_specialist_review(self, workflow_id, mode="verify", target=record.target, packet=specialist_findings)
        if native_agent_backend is not None:
            specialist_review = _record_native_specialist_review(
                self,
                workflow_id,
                target=record.target,
                native_agent_backend=native_agent_backend,
                target_refs=target_refs,
            )
            if specialist_review.get("blocked"):
                return {
                    "workflow_id": workflow_id,
                    "checkpoint": specialist_review["checkpoint"],
                    "verify": specialist_review["state"],
                }
        specialist_state = None
        if specialist_review:
            specialist_state = specialist_review["state"]
            evidence_records.append(specialist_review["evidence"])
        evidence_records.append(evidence)
        deterministic_checks = [*record.deterministic_checks, *deterministic_check_summaries(deterministic_result)]
        residuals = _verify_residuals(record.residuals, deterministic_result)
        if specialist_state:
            residuals = _verify_specialist_residuals(residuals, specialist_state)
        verdict = record.verdict
        specialist_needs_fix = bool(
            specialist_state and any(item["decision"] in {"block", "fix"} for item in specialist_state["finding_arbitration"])
        )
        if specialist_needs_fix:
            verdict = "needs_fix"
        state_record = VerifyRecord(
            target=record.target,
            check_plan=record.check_plan,
            deterministic_checks=deterministic_checks,
            reviewer_findings=[*record.reviewer_findings, *(specialist_review or {}).get("reviewer_findings", [])],
            verdict=verdict,
            evidence_records=evidence_records,
            residuals=residuals,
            final_report_summary=_verify_report_summary(record.final_report_summary, deterministic_result),
        )
        state = state_record.as_state(artifact_id=artifact_ref, artifact_path=artifact_path_text, specialist_state=specialist_state)
        if deterministic_artifact and not specialist_state:
            state.update(deterministic_execution_markers(deterministic_result, artifact_id=deterministic_artifact["artifact_id"]))
        if specialist_state:
            state.update(_specialist_execution_markers(specialist_state, artifact_id=specialist_artifact_id("verify")))
        state, residuals, block_reason = apply_execution_truth_block("verify", state, residuals=state_record.residuals)
        report_evidence = [
            item
            for item in state["evidence"]
            if artifact_ref not in (item.get("artifact_refs") or [])
        ]
        rendered_report = render_verify_report(
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
        artifact = _existing_verify_artifact(workflow, expected_path=artifact_path, rendered_report=rendered_report)
        if artifact is None:
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(rendered_report, encoding="utf-8")
            artifact = self.record_artifact(
                workflow_id,
                kind="report",
                artifact_id=VERIFY_REPORT_ARTIFACT_ID,
                path=artifact_path,
                note="final verification report artifact",
            )["artifact"]
        artifact_ref = artifact["artifact_id"]
        artifact_path = artifact["path"]
        state["final_report_artifact_id"] = artifact_ref
        state["final_report_artifact_path"] = artifact_path
        state = attach_phase5a_evidence_contract(
            "verify",
            workflow=self.load_workflow(workflow_id),
            state=state,
            terminal_evidence=evidence,
        )
        checkpoint = self.record_outcome(
            workflow_id,
            ModeOutcome(
                summary=(
                    "Verification terminal success blocked because execution evidence is missing."
                    if block_reason
                    else "Verification stopped because specialist findings require fixes."
                    if specialist_needs_fix
                    else "Final verification verdict is ready for visible delivery."
                ),
                status_after="failed_unreported" if block_reason or specialist_needs_fix else "completed_unreported",
                phase_after="terminal",
                checkpoint_type="terminal",
                event_type="fail" if block_reason or specialist_needs_fix else "complete",
                worklog_block_kind="terminal_summary",
                step_result="terminal",
                residuals=residuals,
                terminal_evidence=evidence,
                mode_state_update=state,
                recovery_lease_id=recovery_lease_id,
                recovery_lease_holder=recovery_lease_holder,
                final_status=(
                    execution_blocked_final_status("verify", block_reason, residuals)
                    if block_reason
                    else {
                        "result": "needs_fix",
                        "done": [
                            "Recorded structured specialist findings",
                            "Registered the final verification report through the shared artifact path",
                        ],
                        "checked": [
                            "Specialist arbitration found blocking or fix-required findings",
                            "Visible delivery remains gated by reserve-delivery/report-proof/complete-reported",
                        ],
                        "residuals": residuals,
                    }
                    if specialist_needs_fix
                    else {
                        "result": state_record.verdict,
                        "done": [
                        "Recorded a structured verification verdict",
                        "Recorded trusted deterministic check evidence",
                        "Bound structured specialist findings when provided",
                        "Registered the final verification report through the shared artifact path",
                        "Stopped before adapters, recovery, or external actions",
                        ],
                        "checked": [
                            "Verify mode used shared artifact and checkpoint contracts",
                            "Visible delivery remains gated by reserve-delivery/report-proof/complete-reported",
                        ],
                        "residuals": residuals,
                    }
                ),
                failure_reason=block_reason or ("specialist_findings_need_fix" if specialist_needs_fix else None),
            ),
        )
        return {
            "workflow_id": workflow_id,
            "artifact": artifact,
            "checkpoint": checkpoint,
            "verify": state,
        }


def _record_deterministic_check_artifact(
    handler: ModeHandler,
    workflow_id: str,
    *,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    if not result.get("checks"):
        return None
    artifact_path = handler.store.workflow_dir(workflow_id) / "artifacts" / "verify-deterministic-checks.json"
    workflow = handler.load_workflow(workflow_id)
    matches = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == VERIFY_DETERMINISTIC_CHECK_ARTIFACT_ID
    ]
    if matches:
        if len(matches) > 1:
            raise ValueError(f"duplicate deterministic check artifact id: {VERIFY_DETERMINISTIC_CHECK_ARTIFACT_ID}")
        artifact = matches[0]
        if artifact.get("kind") != "evidence":
            raise ValueError("deterministic check artifact must use evidence kind")
        if not Path(str(artifact.get("path", ""))).is_file():
            raise ValueError("deterministic check artifact path is missing")
    else:
        write_deterministic_check_artifact(artifact_path, result)
        artifact = handler.record_artifact(
            workflow_id,
            kind="evidence",
            artifact_id=VERIFY_DETERMINISTIC_CHECK_ARTIFACT_ID,
            path=artifact_path,
            note="trusted deterministic verify check evidence",
        )["artifact"]
    if not _has_deterministic_check_event(handler, workflow_id, artifact_id=artifact["artifact_id"]):
        handler.store.append_event(
            workflow_id,
            {
                "schema_version": 1,
                "event_id": f"evt-deterministic-check-recorded-{workflow_id}",
                "workflow_id": workflow_id,
                "event_type": "deterministic_check_recorded",
                "created_at": now_iso(),
                "note": "trusted deterministic verify check evidence recorded",
                "payload": {
                    "runner_ref": result["runner_ref"],
                    "artifact_id": artifact["artifact_id"],
                    "check_count": len(result.get("checks") or []),
                    "status": "pass",
                },
            },
        )
    return artifact


def _has_deterministic_check_event(handler: ModeHandler, workflow_id: str, *, artifact_id: str) -> bool:
    path = handler.store.workflow_dir(workflow_id) / "events.jsonl"
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        payload = event.get("payload") or {}
        if event.get("event_type") == "deterministic_check_recorded" and payload.get("artifact_id") == artifact_id:
            return True
    return False


def _verify_residuals(base: dict[str, list[str]], result: dict[str, Any]) -> dict[str, list[str]]:
    if not result.get("checks"):
        return base
    return {
        "blocking_remaining": [],
        "accepted_risks": [
            "Phase 1 verify execution is limited to trusted local deterministic checks; specialist review remains later scope.",
        ],
        "implementation_backlog": [
            "Add runner-supplied command/log/status checks and optional specialist findings in later phases.",
        ],
        "deferred_scope": [
            "Agent review, repair loops, Gateway integration, and live slash-route deployment are outside Phase 1.",
        ],
    }


def _verify_report_summary(base: str, result: dict[str, Any]) -> str:
    if not result.get("checks"):
        return base
    return "Verification record is backed by trusted deterministic local check evidence."


def _record_specialist_review(
    handler: ModeHandler,
    workflow_id: str,
    *,
    mode: str,
    target: str,
    packet: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if packet is None:
        return None
    artifact_id = specialist_artifact_id(mode)
    review = build_specialist_review(packet, mode=mode, target=target, artifact_id=artifact_id)
    artifact_path = handler.store.workflow_dir(workflow_id) / "artifacts" / f"{mode}-specialist-findings.json"
    workflow = handler.load_workflow(workflow_id)
    matches = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == artifact_id
    ]
    if matches:
        if len(matches) > 1:
            raise ValueError(f"duplicate specialist findings artifact id: {artifact_id}")
        artifact = matches[0]
        if artifact.get("kind") != "evidence":
            raise ValueError("specialist findings artifact must use evidence kind")
        if not Path(str(artifact.get("path", ""))).is_file():
            raise ValueError("specialist findings artifact path is missing")
    else:
        write_specialist_artifact(artifact_path, {"packet": packet, "specialist_review": review["state"]})
        artifact = handler.record_artifact(
            workflow_id,
            kind="evidence",
            artifact_id=artifact_id,
            path=artifact_path,
            note="validated runner-provided specialist findings",
        )["artifact"]
    _record_specialist_events(handler, workflow_id, mode=mode, review=review, artifact_id=artifact["artifact_id"])
    return review


def _record_native_specialist_review(
    handler: ModeHandler,
    workflow_id: str,
    *,
    target: str,
    native_agent_backend: Any,
    target_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    artifact_id = specialist_artifact_id("verify")
    requests = _native_verify_requests(workflow_id=workflow_id, target=target, target_refs=target_refs, source_root=Path.cwd())
    _record_native_panel_launch_requested(handler, workflow_id, mode="verify", artifact_id=artifact_id, target=target, requests=requests)
    try:
        results = native_agent_backend.run_panel(requests)
    except NativePanelBlockedError as exc:
        return _record_native_panel_blocked(
            handler,
            workflow_id,
            mode="verify",
            artifact_id=artifact_id,
            target=target,
            requests=requests,
            error=exc,
        )
    review = build_native_specialist_review(
        results,
        mode="verify",
        target=target,
        artifact_id=artifact_id,
        panel_id=f"native-verify-panel-{workflow_id}",
    )
    artifact_path = handler.store.workflow_dir(workflow_id) / "artifacts" / "verify-native-specialist-findings.json"
    workflow = handler.load_workflow(workflow_id)
    matches = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == artifact_id
    ]
    if matches:
        if len(matches) > 1:
            raise ValueError(f"duplicate specialist findings artifact id: {artifact_id}")
        artifact = matches[0]
    else:
        write_specialist_artifact(artifact_path, {"native_results": [item.as_dict() for item in results], "specialist_review": review["state"]})
        artifact = handler.record_artifact(
            workflow_id,
            kind="evidence",
            artifact_id=artifact_id,
            path=artifact_path,
            note="validated native OpenClaw specialist findings",
        )["artifact"]
    _record_specialist_events(handler, workflow_id, mode="verify", review=review, artifact_id=artifact["artifact_id"])
    return review


def _record_native_panel_launch_requested(
    handler: ModeHandler,
    workflow_id: str,
    *,
    mode: str,
    artifact_id: str,
    target: str,
    requests: list[NativeLaunchRequest],
) -> None:
    state = _native_panel_verify_state(
        handler,
        workflow_id,
        target=target,
        verdict="stopped",
        native_panel_state=build_native_agent_pending_collection_state(mode=mode, artifact_id=artifact_id, requests=requests),
    )
    handler.record_outcome(
        workflow_id,
        ModeOutcome(
            summary="Native specialist panel child requests recorded before launch/collection.",
            status_after="running",
            phase_after="native_panel_launch_requested",
            checkpoint_type="checkpoint",
            event_type="checkpoint",
            worklog_block_kind="checkpoint_summary",
            step_result="waiting",
            mode_state_update=state,
        ),
    )


def _record_native_panel_blocked(
    handler: ModeHandler,
    workflow_id: str,
    *,
    mode: str,
    artifact_id: str,
    target: str,
    requests: list[NativeLaunchRequest],
    error: NativePanelBlockedError,
) -> dict[str, Any]:
    native_panel_state = build_native_agent_pending_collection_state(
        mode=mode,
        artifact_id=artifact_id,
        requests=requests,
        status="launch_blocked",
        blocked_reason=error.reason,
        blocked_request_id=error.blocked_request_id,
        blocked_session_key=error.blocked_session_key,
    )
    residuals = normalize_residuals(
        {
            "blocking_remaining": [
                f"Native specialist panel blocked before complete collection: {error.reason}.",
            ],
            "accepted_risks": [],
            "implementation_backlog": [
                "Retry native specialist panel after subagent capacity or CLI contract issue is resolved.",
            ],
            "deferred_scope": [
                "No automatic child session abort/delete was attempted.",
            ],
        }
    )
    state = _native_panel_verify_state(
        handler,
        workflow_id,
        target=target,
        verdict="blocked",
        native_panel_state=native_panel_state,
        residuals=residuals,
    )
    checkpoint = handler.record_outcome(
        workflow_id,
        ModeOutcome(
            summary=f"Native specialist panel blocked before complete collection: {error.reason}.",
            status_after="blocked",
            phase_after="native_panel_blocked",
            checkpoint_type="checkpoint",
            event_type="checkpoint",
            worklog_block_kind="checkpoint_summary",
            step_result="blocked",
            residuals=residuals,
            mode_state_update=state,
            failure_reason=None,
        ),
    )
    return {"blocked": True, "state": state, "checkpoint": checkpoint, "error": error.message}


def _native_panel_verify_state(
    handler: ModeHandler,
    workflow_id: str,
    *,
    target: str,
    verdict: str,
    native_panel_state: dict[str, Any],
    residuals: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    report_path = handler.store.workflow_dir(workflow_id) / "artifacts" / "verify-report.md"
    return {
        "final_report_artifact_id": VERIFY_REPORT_ARTIFACT_ID,
        "final_report_artifact_path": str(report_path),
        "target": target,
        "check_plan": ["Run native OpenClaw specialist panel."],
        "deterministic_checks": [],
        "reviewer_findings": [],
        "verdict": verdict,
        "evidence": [],
        "residuals": normalize_residuals(residuals or {}),
        "final_report_summary": "Native specialist panel is pending or blocked before complete collection.",
        **native_panel_state,
    }


def _native_verify_requests(
    *,
    workflow_id: str,
    target: str,
    target_refs: list[dict[str, Any]] | None = None,
    source_root: Path | None = None,
) -> list[NativeLaunchRequest]:
    profile_refs = ["native-verify-architecture", "native-verify-contracts", "native-verify-ops"]
    merged_target_refs = merge_inline_target_ref("verify", target, target_refs, source_root=source_root or Path.cwd())
    return [
        NativeLaunchRequest(
            mode="verify",
            objective=target,
            target_refs=[dict(item) for item in merged_target_refs],
            profile_ref=profile_ref,
            context_hash=stable_hash({"workflow_id": workflow_id, "target": target, "profile_ref": profile_ref, "target_refs": merged_target_refs}),
            idempotency_key=stable_hash({"workflow_id": workflow_id, "profile_ref": profile_ref, "round": 1}),
            output_schema={"schema_ref": "structured_specialist_finding.v1"},
            session_key=f"agent:main:converge-{workflow_id}-{index + 1}",
            request_id=f"verify-native-{workflow_id}-{index + 1}",
            profile_context_refs=[{"kind": "native_profile", "id": profile_ref}],
        )
        for index, profile_ref in enumerate(profile_refs)
    ]


def _record_specialist_events(handler: ModeHandler, workflow_id: str, *, mode: str, review: dict[str, Any], artifact_id: str) -> None:
    existing = _specialist_event_types(handler, workflow_id, artifact_id=artifact_id)
    for event_type in ("agent_panel_requested", "agent_findings_recorded", "finding_arbitrated"):
        if event_type in existing:
            continue
        handler.store.append_event(
            workflow_id,
            {
                "schema_version": 1,
                "event_id": f"evt-{mode}-{event_type}-{workflow_id}",
                "workflow_id": workflow_id,
                "event_type": event_type,
                "created_at": now_iso(),
                "note": "validated specialist review evidence",
                "payload": {
                    "runner_ref": review["state"]["review_panel_spec"].get("runner_ref") or SPECIALIST_REVIEW_RUNNER_REF,
                    "artifact_id": artifact_id,
                    "mode": mode,
                    "finding_count": len(review["state"]["agent_finding_refs"]),
                    "arbitration_count": len(review["state"]["finding_arbitration"]),
                    "profile_registry_ids": [item["profile_id"] for item in review["state"]["profile_registry_refs"]],
                    "profile_registry_hashes": [item["context_hash"] for item in review["state"]["profile_registry_refs"]],
                    "request_ids": [item["request_id"] for item in review["state"]["agent_request_refs"]],
                    "result_ids": [item["result_id"] for item in review["state"]["agent_result_refs"]],
                    "idempotency_keys": review["state"]["agent_result_idempotency_keys"],
                    "collection_status": review["state"]["agent_result_collection_status"]["status"],
                    "collection_cursor": review["state"]["agent_result_collection_status"]["collection_cursor"],
                    "recovery_resume_cursor": review["state"]["recovery_resume_cursor"],
                },
            },
        )


def _specialist_event_types(handler: ModeHandler, workflow_id: str, *, artifact_id: str) -> set[str]:
    path = handler.store.workflow_dir(workflow_id) / "events.jsonl"
    if not path.exists():
        return set()
    found = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        payload = event.get("payload") or {}
        if payload.get("artifact_id") == artifact_id:
            found.add(event.get("event_type"))
    return found


def _specialist_execution_markers(state: dict[str, Any], *, artifact_id: str) -> dict[str, Any]:
    if state["review_panel_spec"].get("runner_ref") == NATIVE_PANEL_RUNNER_REF:
        return {
            "execution_capability": "delegated_agents",
            "execution_source": SOURCE_NATIVE_AGENT_PANEL,
            "satisfies_native_agent_panel": True,
            "execution_performed": True,
            "synthetic_report": False,
            "runner_ref": NATIVE_PANEL_RUNNER_REF,
            "execution_evidence_refs": [artifact_id],
            "execution_started_at": now_iso(),
            "execution_completed_at": now_iso(),
            "execution_classification_reason": "native OpenClaw specialist child sessions collected",
        }
    return {
        "execution_capability": "delegated_agents",
        "execution_source": SOURCE_RUNNER_PROVIDED_PACKET,
        "satisfies_native_agent_panel": False,
        "execution_performed": True,
        "synthetic_report": False,
        "runner_ref": SPECIALIST_REVIEW_RUNNER_REF,
        "execution_evidence_refs": [artifact_id],
        "execution_started_at": now_iso(),
        "execution_completed_at": now_iso(),
        "execution_classification_reason": "trusted runner provided structured specialist findings",
    }


def _verify_specialist_residuals(base: dict[str, list[str]], state: dict[str, Any]) -> dict[str, list[str]]:
    residuals = normalize_residuals(base)
    if state["accepted_change_refs"]:
        residuals["blocking_remaining"] = [
            "Specialist findings require accepted fixes or explicit owner risk acceptance before pass.",
        ]
    if state["review_panel_spec"].get("runner_ref") == NATIVE_PANEL_RUNNER_REF:
        residuals["accepted_risks"] = []
        residuals["implementation_backlog"] = [
            "Wire the same native panel adapter into /conv after /verify native panel validation holds.",
        ]
    else:
        residuals["accepted_risks"] = [
            "Phase 4A accepts runner-provided structured findings; native agent launch is deferred.",
        ]
        residuals["implementation_backlog"] = [
            "Phase 4B adds native Converge specialist panel launch and recovery-safe result collection.",
        ]
    residuals["deferred_scope"] = [
        "Specialist agents cannot send visible messages, mutate workflow state, restart services, push, or open PRs.",
    ]
    return residuals


def _specialist_state_from_verify(state: dict[str, Any]) -> dict[str, Any]:
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
        "profile_registry_refs",
        "agent_request_refs",
        "agent_result_refs",
        "agent_result_idempotency_keys",
        "agent_result_collection_status",
        "recovery_resume_cursor",
    )}


def build_verify_record(text: str) -> VerifyRecord:
    target = _compact(text) or "Audit the supplied target and produce an evidence-backed verdict."
    residuals = {
        "blocking_remaining": [],
        "accepted_risks": [
            "C2 verify mode records verdict/report structure only; domain-specific audit execution belongs later integrations.",
        ],
        "implementation_backlog": [
            "Connect real check execution, specialist review, or slash adapters after recovery and install slices are ready.",
        ],
        "deferred_scope": [
            "Recovery daemon, Gateway integration, slash-command routing, and Ledger adapter migration are outside C2.",
        ],
    }
    evidence_records = [
        {
            "evidence_key": "verify-c2-contract",
            "kind": "contract",
            "summary": "C2 is limited to durable verdict, evidence, residual, and final report records.",
            "artifact_refs": [],
        }
    ]
    return VerifyRecord(
        target=target,
        check_plan=[
            "Restate the target to preserve verification scope.",
            "Record the verdict using the shared Converge verdict vocabulary.",
            "Classify residual items into blocking, accepted risk, backlog, or deferred scope.",
            "Register the final report as a shared artifact before terminal delivery.",
        ],
        deterministic_checks=[
            "No external action is performed by verify mode.",
            "No direct workflow mutation path is used outside ModeOutcome/checkpoint.",
            "Terminal visible delivery remains delegated to reserve-delivery/report-proof/complete-reported.",
        ],
        reviewer_findings=[],
        verdict="pass_with_risks",
        evidence_records=evidence_records,
        residuals=residuals,
        final_report_summary="Verification record produced with no blocking remaining item in the current C2 scope.",
    )


def render_verify_report(record: VerifyRecord) -> str:
    sections = [
        ("Target", [record.target]),
        ("Verdict", [record.verdict]),
        ("Check Plan", record.check_plan),
        ("Deterministic Checks", record.deterministic_checks),
        ("Reviewer Findings", record.reviewer_findings or ["None"]),
        ("Evidence", [item["summary"] for item in record.evidence_records]),
        ("Blocking Remaining", record.residuals["blocking_remaining"] or ["None"]),
        ("Accepted Risks", record.residuals["accepted_risks"] or ["None"]),
        ("Implementation Backlog", record.residuals["implementation_backlog"] or ["None"]),
        ("Deferred Scope", record.residuals["deferred_scope"] or ["None"]),
        ("Summary", [record.final_report_summary]),
    ]
    lines = ["# Verification Report", ""]
    for title, items in sections:
        lines.extend([f"## {title}", ""])
        lines.extend(f"- {item}" for item in items)
        lines.append("")
    return "\n".join(lines)


def _compact(text: str) -> str:
    return " ".join(text.split())


def _existing_verify_artifact(workflow: dict[str, Any], *, expected_path: Path, rendered_report: str) -> dict[str, Any] | None:
    matches = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == VERIFY_REPORT_ARTIFACT_ID
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(f"duplicate verify report artifact id: {VERIFY_REPORT_ARTIFACT_ID}")
    artifact = matches[0]
    if artifact.get("kind") != "report":
        raise ValueError(f"verify report artifact id has wrong kind: {artifact.get('kind')!r}")
    path = artifact.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("verify report artifact path is missing")
    if not Path(path).is_file():
        raise ValueError(f"verify report artifact path is missing: {path}")
    if Path(path).expanduser().resolve() != expected_path:
        raise ValueError("verify report artifact path is not the canonical verify output path")
    if Path(path).read_text(encoding="utf-8") != rendered_report:
        raise ValueError("existing verify report artifact does not match rendered final report")
    return artifact
