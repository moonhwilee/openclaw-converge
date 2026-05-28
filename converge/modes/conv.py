"""Conv mode vertical slice."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import ModeHandler, ModeOutcome, apply_execution_truth_block, execution_blocked_final_status
from .conv_execution import (
    CONV_LOCAL_RUNNER_REF,
    CONV_ROUND_EXECUTION_ARTIFACT_ID,
    conv_execution_markers,
    run_conv_round_execution,
    write_conv_round_execution_artifact,
)
from .evidence_contract import attach_phase5a_evidence_contract
from .execution_truth import classify_execution_markers
from .specialist_panel import (
    NATIVE_PANEL_RUNNER_REF,
    SOURCE_NATIVE_AGENT_PANEL,
    SOURCE_RUNNER_PROVIDED_PACKET,
    SPECIALIST_REVIEW_RUNNER_REF,
    build_native_specialist_review,
    build_specialist_review,
    specialist_artifact_id,
    validate_native_specialist_state,
    validate_specialist_state,
    write_specialist_artifact,
)
from ..agents.contracts import (
    DEFAULT_TOOL_POLICY,
    NativeLaunchRequest,
    SOURCE_FIX_RUNNER,
    stable_hash,
    validate_fix_runner_request,
    validate_fix_runner_result,
)
from ..artifacts import now_iso
from ..messages import normalize_residuals


CONV_REPORT_ARTIFACT_ID = "conv-final-report"
CONV_STOP_CONDITIONS = {
    "evidence_sufficient",
    "max_round",
    "blocked_no_execution_evidence",
    "blocked_missing_execution_truth_markers",
    "blocked_specialist_findings",
    "blocked_specialist_follow_up_required",
}
CONV_NOVELTY = {"new", "repeated", "none"}
CONV_SEVERITY = {"p0", "p1", "p2", "p3", "none"}
CONV_OBJECTIVE_IMPACT = {"changes_objective", "preserves_objective", "none"}
CONV_EVIDENCE_QUALITY = {"direct", "inferred", "insufficient", "none"}
CONV_DISPOSITIONS = {"accepted_change", "accepted_risk", "implementation_backlog", "deferred_scope", "no_action"}
FIX_RUNNER_TOOL_POLICY = {
    "policy_id": "conv-fix-runner-bounded-v1",
    "filesystem": "workspace_write",
    "target_mutation": "bounded_by_accepted_change_refs",
    "visible_messages": "forbidden",
    "workflow_state_mutation": "forbidden",
    "service_restart": "forbidden",
    "external_actions": "forbidden",
    "push_or_pr": "forbidden",
    "release": "forbidden",
    "allowed_actions": ["apply_accepted_changes", "run_focused_checks"],
}


@dataclass(frozen=True)
class ConvRound:
    round_index: int
    target_ref: str
    original_target_gate: str
    delta_gate: str
    findings: list[dict[str, Any]]
    material_changes: bool
    follow_up_required: bool
    evidence_sufficient: bool
    summary: str

    def as_state(self) -> dict[str, Any]:
        return {
            "round_index": self.round_index,
            "target_ref": self.target_ref,
            "original_target_gate": self.original_target_gate,
            "delta_gate": self.delta_gate,
            "findings": self.findings,
            "material_changes": self.material_changes,
            "follow_up_required": self.follow_up_required,
            "evidence_sufficient": self.evidence_sufficient,
            "summary": self.summary,
        }


@dataclass(frozen=True)
class ConvRecord:
    target: str
    max_rounds: int
    rounds: list[ConvRound]
    stop_condition: str
    stop_reason: str
    explicit_stop_proof: str
    residuals: dict[str, list[str]]
    final_report_summary: str

    def as_state(
        self,
        *,
        artifact_id: str,
        artifact_path: str,
        specialist_state: dict[str, Any] | None = None,
        specialist_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        state = {
            "final_report_artifact_id": artifact_id,
            "final_report_artifact_path": artifact_path,
            "target": self.target,
            "max_rounds": self.max_rounds,
            "round_count": len(self.rounds),
            "rounds": [item.as_state() for item in self.rounds],
            "stop_condition": self.stop_condition,
            "stop_reason": self.stop_reason,
            "explicit_stop_proof": self.explicit_stop_proof,
            "material_change_accepted": any(item.material_changes for item in self.rounds),
            "follow_up_required": any(item.follow_up_required for item in self.rounds),
            "evidence_sufficient": self.stop_condition == "evidence_sufficient",
            "residuals": self.residuals,
            "final_report_summary": self.final_report_summary,
        }
        if specialist_state:
            state.update(specialist_state)
            state["specialist_max_rounds"] = specialist_state["max_rounds"]
            state["max_rounds"] = self.max_rounds
            state["evidence"] = [specialist_evidence] if specialist_evidence else []
        state.update(classify_execution_markers(self.target, capability="synthetic_round_only"))
        return state


class ConvHandler(ModeHandler):
    """Produces an iterative convergence record without owning delivery."""

    kind = "conv"

    def finalize_conv(
        self,
        workflow_id: str,
        *,
        specialist_findings: dict[str, Any] | None = None,
        native_agent_backend: Any | None = None,
        fix_runner_result: dict[str, Any] | None = None,
        fix_runner_source_root: Path | None = None,
        recovery_lease_id: str | None = None,
        recovery_lease_holder: str | None = None,
    ) -> dict[str, Any]:
        self.validate_recovery_preflight(
            workflow_id,
            recovery_lease_id=recovery_lease_id,
            recovery_lease_holder=recovery_lease_holder,
        )
        workflow = self.load_workflow(workflow_id)
        artifact_path = (self.store.workflow_dir(workflow_id) / "artifacts" / "conv-report.md").expanduser().resolve()
        text = workflow.get("source_request") or workflow.get("objective") or ""
        if specialist_findings is not None and native_agent_backend is not None:
            raise ValueError("conv cannot combine runner-provided and native panel findings")
        specialist_review = _record_specialist_review(self, workflow_id, target=text, packet=specialist_findings)
        if native_agent_backend is not None:
            specialist_review = _record_native_specialist_review(
                self,
                workflow_id,
                target=text,
                native_agent_backend=native_agent_backend,
            )
        specialist_state = specialist_review["state"] if specialist_review else None
        if specialist_state:
            specialist_state = _attach_fix_runner_state(
                specialist_state,
                workflow_id=workflow_id,
                target=text,
                result_packet=fix_runner_result,
                source_root=fix_runner_source_root,
            )
        elif fix_runner_result is not None:
            raise ValueError("fix_runner result requires specialist accepted changes")
        execution_result = run_conv_round_execution(text, source_root=Path.cwd()) if not specialist_state else {}
        execution_artifact = _record_conv_round_execution_artifact(self, workflow_id, result=execution_result) if not specialist_state else None
        if specialist_state:
            record = build_conv_record_from_specialist(text, specialist_state)
        else:
            record = build_conv_record_from_execution(text, execution_result) if execution_artifact else build_conv_record(text)
        artifact_ref = CONV_REPORT_ARTIFACT_ID
        artifact_path_text = str(artifact_path)
        state = record.as_state(
            artifact_id=artifact_ref,
            artifact_path=artifact_path_text,
            specialist_state=specialist_state,
            specialist_evidence=(specialist_review or {}).get("evidence"),
        )
        if execution_artifact:
            state.update(conv_execution_markers(execution_result, artifact_id=execution_artifact["artifact_id"]))
        if specialist_state and not execution_artifact:
            state.update(_specialist_execution_markers(specialist_state, artifact_id=specialist_artifact_id("conv")))
            _record_fix_runner_events(self, workflow_id, state=state)
            _append_fix_runner_evidence(state)
        state, residuals, block_reason = apply_execution_truth_block("conv", state, residuals=record.residuals)
        if specialist_state and (residuals.get("blocking_remaining") or _fix_runner_follow_up_pending(state)):
            specialist_block_reason = (
                "blocked_specialist_findings"
                if residuals.get("blocking_remaining")
                else "blocked_specialist_follow_up_required"
            )
            state["stop_condition"] = specialist_block_reason
            state["evidence_sufficient"] = False
            state["final_report_summary"] = (
                "Convergence is blocked by high-severity specialist findings."
                if residuals.get("blocking_remaining")
                else "Convergence is blocked because accepted specialist fixes require a follow-up round."
            )
            block_reason = specialist_block_reason
        render_record = ConvRecord(
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
        rendered_report = render_conv_report(render_record)
        pending_fix_runner_follow_up = block_reason == "blocked_specialist_follow_up_required"
        report_artifact_id = (
            f"{CONV_REPORT_ARTIFACT_ID}-pending-follow-up" if pending_fix_runner_follow_up else CONV_REPORT_ARTIFACT_ID
        )
        if pending_fix_runner_follow_up:
            artifact_path = (self.store.workflow_dir(workflow_id) / "artifacts" / "conv-pending-follow-up-report.md").expanduser().resolve()
        artifact = _existing_conv_artifact(
            workflow,
            artifact_id=report_artifact_id,
            expected_path=artifact_path,
            rendered_report=rendered_report,
        )
        if artifact is None:
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(rendered_report, encoding="utf-8")
            artifact = self.record_artifact(
                workflow_id,
                kind="report",
                artifact_id=report_artifact_id,
                path=artifact_path,
                note="final convergence report artifact",
            )["artifact"]
        artifact_ref = artifact["artifact_id"]
        artifact_path = artifact["path"]
        evidence = {
            "evidence_key": "conv-final-report",
            "kind": "artifact",
            "summary": "Final convergence report registered through the shared artifact path.",
            "artifact_refs": [artifact_ref],
        }
        if state.get("accepted_change_refs") and not _fix_runner_follow_up_pending(state):
            evidence["produced_after_change_refs"] = [
                item["change_ref"]
                for item in state["accepted_change_refs"]
                if isinstance(item, dict) and item.get("change_ref")
            ]
        state["final_report_artifact_id"] = artifact_ref
        state["final_report_artifact_path"] = artifact_path
        state = attach_phase5a_evidence_contract(
            "conv",
            workflow=self.load_workflow(workflow_id),
            state=state,
            terminal_evidence=evidence,
            terminal_status_override="blocked" if block_reason else None,
        )
        terminal = not pending_fix_runner_follow_up
        success_final_status = {
            "result": "pass_with_risks" if any(record.residuals.values()) else "pass",
            "stop_reason": record.stop_condition,
            "done": [
                "Recorded bounded convergence round metadata",
                "Classified findings through original-target and delta gates",
                "Bound structured specialist findings when provided",
                "Stopped through evidence sufficiency or max-round proof",
            ],
            "checked": [
                "Conv mode used shared artifact and checkpoint contracts",
                "Visible delivery remains gated by reserve-delivery/report-proof/complete-reported",
            ],
            "residuals": record.residuals,
        }
        checkpoint = self.record_outcome(
            workflow_id,
            ModeOutcome(
                summary=(
                    "Convergence is blocked pending bounded fix runner follow-up."
                    if pending_fix_runner_follow_up
                    else "Convergence terminal success blocked because execution evidence is missing."
                    if block_reason
                    else "Final convergence record is ready for visible delivery."
                ),
                status_after="running" if pending_fix_runner_follow_up else "failed_unreported" if block_reason else "completed_unreported",
                phase_after="fix_runner_pending" if pending_fix_runner_follow_up else "terminal",
                checkpoint_type="checkpoint" if pending_fix_runner_follow_up else "terminal",
                event_type="checkpoint" if pending_fix_runner_follow_up else "fail" if block_reason else "complete",
                worklog_block_kind="checkpoint_summary" if pending_fix_runner_follow_up else "terminal_summary",
                step_result="blocked" if pending_fix_runner_follow_up else "terminal",
                residuals=residuals,
                evidence=evidence if pending_fix_runner_follow_up else None,
                terminal_evidence=None if pending_fix_runner_follow_up else evidence,
                mode_state_update=state,
                recovery_lease_id=recovery_lease_id,
                recovery_lease_holder=recovery_lease_holder,
                final_status=(
                    None
                    if pending_fix_runner_follow_up
                    else _specialist_blocked_final_status(residuals)
                    if block_reason == "blocked_specialist_findings"
                    else execution_blocked_final_status("conv", block_reason, residuals)
                    if block_reason
                    else success_final_status
                ),
                failure_reason=None if not terminal else block_reason,
            ),
        )
        return {"workflow_id": workflow_id, "artifact": artifact, "checkpoint": checkpoint, "conv": state}


def build_conv_record(text: str) -> ConvRecord:
    target = _compact(text) or "Converge on the supplied target with bounded evidence."
    return _evidence_sufficient_record(target)


def build_conv_record_from_execution(text: str, result: dict[str, Any]) -> ConvRecord:
    target = _compact(text) or "Converge on the supplied target with bounded evidence."
    rounds = [
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
        for item in result.get("rounds") or []
    ]
    if not rounds:
        return build_conv_record(text)
    residuals = {
        "blocking_remaining": [],
        "accepted_risks": [
            "Phase 2 uses deterministic local round evidence only; delegated specialist/agent execution remains outside scope.",
        ],
        "implementation_backlog": [
            "Future phases add delegated reviewers, accepted-change application, and child workflow collection.",
        ],
        "deferred_scope": [],
    }
    return ConvRecord(
        target=target,
        max_rounds=3,
        rounds=rounds,
        stop_condition="evidence_sufficient",
        stop_reason="trusted_local_round_evidence_sufficient",
        explicit_stop_proof="Trusted local conv runner recorded original-target evidence and no accepted material delta.",
        residuals=residuals,
        final_report_summary="Convergence stopped on trusted deterministic round evidence with no material follow-up required.",
    )


def build_conv_record_from_specialist(text: str, specialist_state: dict[str, Any]) -> ConvRecord:
    target = _compact(text) or "Converge on the supplied target with bounded specialist evidence."
    native_panel = specialist_state["review_panel_spec"].get("runner_ref") == NATIVE_PANEL_RUNNER_REF
    if native_panel:
        validate_native_specialist_state(specialist_state)
    else:
        validate_specialist_state(specialist_state)
    findings = [_conv_finding_from_arbitration(item) for item in specialist_state["finding_arbitration"]]
    material_changes = any(item["material_change_required"] for item in findings)
    follow_up_required = bool(specialist_state["follow_up_round_required"])
    fix_runner_status = specialist_state.get("fix_runner_collection_status") or {}
    follow_up_closed = (
        bool(follow_up_required)
        and fix_runner_status.get("status") == "complete"
        and bool(specialist_state.get("fix_runner_result_refs"))
    )
    residuals = {
        "blocking_remaining": [
            item["reason"]
            for item in specialist_state["finding_arbitration"]
            if item["decision"] == "block"
        ],
        "accepted_risks": [
            item["reason"]
            for item in specialist_state["finding_arbitration"]
            if item["decision"] == "accept_risk"
        ],
        "implementation_backlog": [
            item["reason"]
            for item in specialist_state["finding_arbitration"]
            if item["decision"] == "defer" or (item["decision"] == "fix" and not follow_up_closed)
        ],
        "deferred_scope": [
            (
                "Native specialist panel was collected; fix-runner application and multi-round material-change follow-up remain later slices."
                if native_panel and not follow_up_closed
                else "Native specialist panel was collected; coordinator fix-runner result and follow-up round were bounded to accepted changes."
                if native_panel
                else "Runner-provided structured findings were bound; coordinator fix-runner result and follow-up round were bounded to accepted changes."
                if follow_up_closed
                else "Phase 4A binds runner-provided structured findings; native specialist launch and fix-runner application remain later slices."
            ),
        ],
    }
    evidence_sufficient = not residuals["blocking_remaining"] and (not follow_up_required or follow_up_closed)
    rounds = [
        ConvRound(
            round_index=specialist_state["round_index"],
            target_ref="original-target",
            original_target_gate=specialist_state["original_target_gate"],
            delta_gate=specialist_state["delta_regression_gate"],
            findings=findings,
            material_changes=material_changes,
            follow_up_required=follow_up_required,
            evidence_sufficient=not follow_up_required and evidence_sufficient,
            summary=(
                "Native specialist findings were collected, deduped, and arbitrated as a bounded convergence round."
                if native_panel
                else "Runner-provided specialist findings were deduped and arbitrated as a bounded convergence round."
            ),
        )
    ]
    if follow_up_closed:
        rounds.append(
            ConvRound(
                round_index=specialist_state["round_index"] + 1,
                target_ref="original-target",
                original_target_gate="within_original_target",
                delta_gate="no_delta",
                findings=[],
                material_changes=False,
                follow_up_required=False,
                evidence_sufficient=True,
                summary="Follow-up round verified the bounded fix-runner result and found no remaining material delta.",
            )
        )
    return ConvRecord(
        target=target,
        max_rounds=max(specialist_state["max_rounds"], len(rounds)),
        rounds=rounds,
        stop_condition="evidence_sufficient" if evidence_sufficient else "blocked_no_execution_evidence",
        stop_reason=(
            "fix_runner_follow_up_evidence_sufficient"
            if follow_up_closed
            else "native_specialist_panel_collected"
            if native_panel
            else "structured_specialist_findings_bound"
        ),
        explicit_stop_proof=(
            "Coordinator fix-runner result is complete and round 2 follow-up verified no remaining material delta."
            if follow_up_closed
            else specialist_state["round_stop_proof"]
        ),
        residuals=residuals,
        final_report_summary=(
            "Convergence applied accepted local changes through the coordinator fix-runner and verified the follow-up round."
            if follow_up_closed
            else "Convergence collected native specialist findings without allowing child side effects."
            if native_panel
            else "Convergence bound structured specialist findings without allowing specialist side effects."
        ),
    )


def render_conv_report(record: ConvRecord) -> str:
    lines = [
        "# Convergence Report",
        "",
        "## Target",
        "",
        f"- {record.target}",
        "",
        "## Stop",
        "",
        f"- Condition: {record.stop_condition}",
        f"- Reason: {record.stop_reason}",
        f"- Proof: {record.explicit_stop_proof}",
        "",
        "## Rounds",
        "",
    ]
    for round_record in record.rounds:
        lines.extend(
            [
                f"### Round {round_record.round_index}",
                "",
                f"- Target gate: {round_record.original_target_gate}",
                f"- Delta gate: {round_record.delta_gate}",
                f"- Material changes: {round_record.material_changes}",
                f"- Follow-up required: {round_record.follow_up_required}",
                f"- Evidence sufficient: {round_record.evidence_sufficient}",
                f"- Summary: {round_record.summary}",
                "",
            ]
        )
        if round_record.findings:
            lines.extend("- " + finding["summary"] for finding in round_record.findings)
        else:
            lines.append("- No findings")
        lines.append("")
    residuals = normalize_residuals(record.residuals)
    lines.extend(["## Residuals", ""])
    for key in ("blocking_remaining", "accepted_risks", "implementation_backlog", "deferred_scope"):
        values = residuals[key] or ["None"]
        lines.append(f"### {key}")
        lines.append("")
        lines.extend(f"- {value}" for value in values)
        lines.append("")
    lines.extend(["## Summary", "", f"- {record.final_report_summary}", ""])
    return "\n".join(lines)


def validate_conv_state(state: dict[str, Any], *, terminal: bool, final_status: dict[str, Any] | None = None) -> dict[str, list[str]]:
    if not state:
        if terminal:
            raise ValueError("terminal or artifact-backed conv workflow requires populated conv_state")
        return normalize_residuals({})
    required = {
        "final_report_artifact_id",
        "final_report_artifact_path",
        "target",
        "max_rounds",
        "round_count",
        "rounds",
        "stop_condition",
        "stop_reason",
        "explicit_stop_proof",
        "material_change_accepted",
        "follow_up_required",
        "evidence_sufficient",
        "residuals",
        "final_report_summary",
    }
    missing = sorted(required - set(state))
    if missing:
        raise ValueError(f"conv_state is missing required fields: {missing!r}")
    if "review_panel_spec" in state:
        if state.get("execution_source") == SOURCE_RUNNER_PROVIDED_PACKET:
            if state.get("satisfies_native_agent_panel") is not False:
                raise ValueError("runner-provided specialist conv evidence must not satisfy native_agent_panel parity")
            validate_specialist_state(_specialist_state_from_conv(state))
        elif state.get("execution_source") == SOURCE_NATIVE_AGENT_PANEL:
            if state.get("satisfies_native_agent_panel") is not True:
                raise ValueError("native specialist conv evidence must satisfy native_agent_panel parity")
            validate_native_specialist_state(_specialist_state_from_conv(state))
        else:
            raise ValueError("specialist conv evidence has unknown execution_source")
    _validate_fix_runner_state(state)
    max_rounds = state["max_rounds"]
    round_count = state["round_count"]
    rounds = state["rounds"]
    if not isinstance(max_rounds, int) or max_rounds < 1:
        raise ValueError("conv_state max_rounds must be a positive integer")
    if not isinstance(round_count, int) or round_count < 1:
        raise ValueError("conv_state round_count must be a positive integer")
    if not isinstance(rounds, list) or len(rounds) != round_count:
        raise ValueError("conv_state round_count must match rounds")
    if round_count > max_rounds:
        raise ValueError("conv_state round_count cannot exceed max_rounds")
    stop_condition = state["stop_condition"]
    if stop_condition not in CONV_STOP_CONDITIONS:
        raise ValueError(f"conv_state stop_condition is invalid: {stop_condition!r}")
    material_change_seen = False
    for index, round_state in enumerate(rounds):
        _validate_round(round_state)
        if round_state["round_index"] != index + 1:
            raise ValueError("conv_state round_index values must be sequential")
        material_change_seen = material_change_seen or bool(round_state["material_changes"])
        if round_state["material_changes"] and index < len(rounds) - 1 and not round_state["follow_up_required"]:
            raise ValueError("conv_state material changes require follow-up or explicit stop proof")
        if round_state["material_changes"] and index == len(rounds) - 1 and not state["explicit_stop_proof"]:
            raise ValueError("conv_state terminal material changes require explicit stop proof")
    if bool(state["material_change_accepted"]) != material_change_seen:
        raise ValueError("conv_state material_change_accepted must reflect rounds")
    if bool(state["follow_up_required"]) != any(bool(item["follow_up_required"]) for item in rounds):
        raise ValueError("conv_state follow_up_required must reflect rounds")
    if stop_condition == "evidence_sufficient" and not rounds[-1]["evidence_sufficient"]:
        raise ValueError("conv_state evidence_sufficient stop requires final round evidence sufficiency")
    if stop_condition == "max_round" and round_count != max_rounds:
        raise ValueError("conv_state max_round stop requires round_count to equal max_rounds")
    if bool(state["evidence_sufficient"]) != (stop_condition == "evidence_sufficient"):
        raise ValueError("conv_state evidence_sufficient must match stop_condition")
    residuals = normalize_residuals(state["residuals"])
    if final_status is not None:
        if normalize_residuals(final_status.get("residuals")) != residuals:
            raise ValueError("conv_state residuals must match final_status.residuals")
        if final_status.get("stop_reason") != stop_condition:
            raise ValueError("conv_state stop_condition must match final_status.stop_reason")
    return residuals


def _validate_round(round_state: dict[str, Any]) -> None:
    required = {
        "round_index",
        "target_ref",
        "original_target_gate",
        "delta_gate",
        "findings",
        "material_changes",
        "follow_up_required",
        "evidence_sufficient",
        "summary",
    }
    missing = sorted(required - set(round_state))
    if missing:
        raise ValueError(f"conv_state round is missing required fields: {missing!r}")
    if not isinstance(round_state["round_index"], int) or round_state["round_index"] < 1:
        raise ValueError("conv_state round_index must be positive")
    if round_state["original_target_gate"] not in {"within_original_target", "outside_original_target_rejected"}:
        raise ValueError("conv_state original_target_gate is invalid")
    if round_state["delta_gate"] not in {"new_material_delta", "non_material_delta", "no_delta"}:
        raise ValueError("conv_state delta_gate is invalid")
    findings = round_state["findings"]
    if not isinstance(findings, list):
        raise ValueError("conv_state findings must be an array")
    material_findings = False
    for finding in findings:
        _validate_finding(finding)
        material_findings = material_findings or (
            finding["material_change_required"] and finding["disposition"] == "accepted_change"
        )
    if round_state["original_target_gate"] == "outside_original_target_rejected" and material_findings:
        raise ValueError("conv_state original target gate cannot accept material changes outside target")
    if round_state["delta_gate"] == "no_delta" and material_findings:
        raise ValueError("conv_state delta_gate no_delta cannot carry accepted material changes")
    if round_state["delta_gate"] == "new_material_delta" and not material_findings:
        raise ValueError("conv_state delta_gate new_material_delta requires accepted material changes")
    if bool(round_state["material_changes"]) != material_findings:
        raise ValueError("conv_state round material_changes must reflect findings")
    for key in ("target_ref", "summary"):
        if not isinstance(round_state[key], str) or not round_state[key]:
            raise ValueError(f"conv_state round {key} must be non-empty")


def _validate_finding(finding: dict[str, Any]) -> None:
    required = {
        "finding_id",
        "summary",
        "novelty",
        "severity",
        "objective_impact",
        "evidence_quality",
        "disposition",
        "material_change_required",
    }
    missing = sorted(required - set(finding))
    if missing:
        raise ValueError(f"conv_state finding is missing required fields: {missing!r}")
    if finding["novelty"] not in CONV_NOVELTY:
        raise ValueError("conv_state finding novelty is invalid")
    if finding["severity"] not in CONV_SEVERITY:
        raise ValueError("conv_state finding severity is invalid")
    if finding["objective_impact"] not in CONV_OBJECTIVE_IMPACT:
        raise ValueError("conv_state finding objective_impact is invalid")
    if finding["evidence_quality"] not in CONV_EVIDENCE_QUALITY:
        raise ValueError("conv_state finding evidence_quality is invalid")
    if finding["disposition"] not in CONV_DISPOSITIONS:
        raise ValueError("conv_state finding disposition is invalid")
    if not isinstance(finding["material_change_required"], bool):
        raise ValueError("conv_state finding material_change_required must be boolean")
    for key in ("finding_id", "summary"):
        if not isinstance(finding[key], str) or not finding[key]:
            raise ValueError(f"conv_state finding {key} must be non-empty")


def _validate_fix_runner_state(state: dict[str, Any]) -> None:
    if "review_panel_spec" not in state:
        return
    accepted_changes = state.get("accepted_change_refs") or []
    required = bool(accepted_changes)
    if state.get("fix_runner_required", required) is not required:
        raise ValueError("conv_state fix_runner_required must reflect accepted changes")
    requests = state.get("fix_runner_request_refs") or []
    results = state.get("fix_runner_result_refs") or []
    status = state.get("fix_runner_collection_status") or {
        "status": "not_required",
        "pending_request_ids": [],
        "completed_result_count": 0,
        "source": SOURCE_FIX_RUNNER,
        "requires_follow_up_round": False,
    }
    if not isinstance(requests, list) or not isinstance(results, list) or not isinstance(status, dict):
        raise ValueError("conv_state fix_runner refs and status must be structured")
    if not required:
        if requests or results or status.get("status") not in {"not_required", None}:
            raise ValueError("conv_state must not carry fix_runner work without accepted changes")
        return
    if not requests:
        raise ValueError("conv_state accepted changes require fix_runner_request_refs")
    accepted_ids = [item.get("change_ref") for item in accepted_changes if isinstance(item, dict)]
    for request in requests:
        validate_fix_runner_request(request)
        request_changes = request.get("accepted_change_refs") or []
        request_ids = [item.get("change_ref") for item in request_changes if isinstance(item, dict)]
        if request_ids != accepted_ids:
            raise ValueError("conv_state fix_runner request must bind exactly the accepted change refs")
        if request.get("status") not in {"pending", "completed"}:
            raise ValueError("conv_state fix_runner request status is invalid")
    if status.get("source") != SOURCE_FIX_RUNNER:
        raise ValueError("conv_state fix_runner status must use fix_runner source")
    if not results:
        if status.get("status") != "pending":
            raise ValueError("conv_state accepted changes require pending fix_runner status without results")
        if status.get("pending_request_ids") != [item["runner_id"] for item in requests]:
            raise ValueError("conv_state fix_runner pending ids must match request refs")
        if status.get("completed_result_count") != 0 or status.get("requires_follow_up_round") is not True:
            raise ValueError("conv_state fix_runner status must require follow-up before completion")
        return
    request_by_id = {item["runner_id"]: item for item in requests}
    if len(results) != len(requests):
        raise ValueError("conv_state fix_runner results must match request count")
    for result in results:
        validate_fix_runner_result(result)
        request = request_by_id.get(result["runner_id"])
        if not request:
            raise ValueError("conv_state fix_runner result runner_id must match request refs")
        result_ids = [item.get("change_ref") for item in result["accepted_change_refs"] if isinstance(item, dict)]
        if result_ids != accepted_ids:
            raise ValueError("conv_state fix_runner result must bind exactly the accepted change refs")
        if result.get("tool_policy") != request.get("tool_policy"):
            raise ValueError("conv_state fix_runner result tool_policy must match request")
    if status.get("status") != "complete":
        raise ValueError("conv_state completed fix_runner results require complete status")
    if status.get("pending_request_ids") != []:
        raise ValueError("conv_state completed fix_runner results require no pending request ids")
    if status.get("completed_result_count") != len(results):
        raise ValueError("conv_state fix_runner completed_result_count must match results")
    if status.get("completed_result_ids") != [item["result_id"] for item in results]:
        raise ValueError("conv_state fix_runner completed_result_ids must match result refs")
    if status.get("requires_follow_up_round") is not True or status.get("follow_up_completed") is not True:
        raise ValueError("conv_state completed fix_runner status must prove follow-up completion")


def _fix_runner_follow_up_pending(state: dict[str, Any]) -> bool:
    if not state.get("follow_up_required"):
        return False
    if not state.get("fix_runner_required"):
        return True
    status = state.get("fix_runner_collection_status") or {}
    return status.get("status") != "complete" or status.get("follow_up_completed") is not True


def _conv_finding_from_arbitration(item: dict[str, Any]) -> dict[str, Any]:
    decision = item["decision"]
    disposition = {
        "block": "implementation_backlog",
        "fix": "accepted_change",
        "accept_risk": "accepted_risk",
        "defer": "deferred_scope",
        "reject": "no_action",
    }[decision]
    material_change_required = decision == "fix"
    return _finding(
        item["group_id"],
        item["reason"],
        novelty="new" if decision in {"block", "fix", "accept_risk"} else "none",
        severity=_severity_from_arbitration(item),
        objective_impact="changes_objective" if material_change_required else "preserves_objective",
        evidence_quality="direct" if decision != "reject" else "insufficient",
        disposition=disposition,
        material_change_required=material_change_required,
    )


def _severity_from_arbitration(item: dict[str, Any]) -> str:
    reason = item.get("reason", "")
    for severity in ("p0", "p1", "p2", "p3"):
        if severity in reason:
            return severity
    return "none"


def _evidence_sufficient_record(target: str) -> ConvRecord:
    residuals = {
        "blocking_remaining": [],
        "accepted_risks": [
            "C3 conv mode records convergence semantics only; real specialist execution belongs later adapters.",
        ],
        "implementation_backlog": [],
        "deferred_scope": [
            "Goal-mode child workflow references, recovery daemon integration, install wiring, and slash routing are outside C3.",
        ],
    }
    return ConvRecord(
        target=target,
        max_rounds=3,
        rounds=[
            ConvRound(
                round_index=1,
                target_ref="original-target",
                original_target_gate="within_original_target",
                delta_gate="no_delta",
                findings=[
                    _finding(
                        "conv-c3-contract",
                        "No material delta remains inside the requested C3 behavior slice.",
                        novelty="none",
                        severity="none",
                        objective_impact="none",
                        evidence_quality="direct",
                        disposition="accepted_risk",
                        material_change_required=False,
                    )
                ],
                material_changes=False,
                follow_up_required=False,
                evidence_sufficient=True,
                summary="Evidence is sufficient for the bounded C3 convergence record.",
            )
        ],
        stop_condition="evidence_sufficient",
        stop_reason="No material accepted change requires another round in the current C3 scope.",
        explicit_stop_proof="Final round evidence is sufficient and no material accepted change remains.",
        residuals=residuals,
        final_report_summary="Convergence record stopped on evidence sufficiency with no blocking remaining item in C3 scope.",
    )


def _material_change_record(target: str) -> ConvRecord:
    residuals = {
        "blocking_remaining": [],
        "accepted_risks": [],
        "implementation_backlog": [],
        "deferred_scope": ["External adapter execution remains outside C3."],
    }
    return ConvRecord(
        target=target,
        max_rounds=3,
        rounds=[
            ConvRound(
                round_index=1,
                target_ref="original-target",
                original_target_gate="within_original_target",
                delta_gate="new_material_delta",
                findings=[
                    _finding(
                        "conv-material-follow-up",
                        "A material accepted change requires a follow-up round.",
                        novelty="new",
                        severity="p2",
                        objective_impact="changes_objective",
                        evidence_quality="direct",
                        disposition="accepted_change",
                        material_change_required=True,
                    )
                ],
                material_changes=True,
                follow_up_required=True,
                evidence_sufficient=False,
                summary="Material accepted change captured; follow-up is required.",
            ),
            ConvRound(
                round_index=2,
                target_ref="original-target",
                original_target_gate="within_original_target",
                delta_gate="no_delta",
                findings=[],
                material_changes=False,
                follow_up_required=False,
                evidence_sufficient=True,
                summary="Follow-up round found no remaining material delta.",
            ),
        ],
        stop_condition="evidence_sufficient",
        stop_reason="Follow-up after material change found no remaining material delta.",
        explicit_stop_proof="Round 2 is the follow-up proof for the material accepted change from round 1.",
        residuals=residuals,
        final_report_summary="Convergence record enforced follow-up after material change and stopped on evidence sufficiency.",
    )


def _max_round_record(target: str) -> ConvRecord:
    residuals = {
        "blocking_remaining": [],
        "accepted_risks": ["Stopped at configured max rounds before additional specialist execution exists."],
        "implementation_backlog": ["Future integrations can add real reviewer execution before max-round judgment."],
        "deferred_scope": [],
    }
    return ConvRecord(
        target=target,
        max_rounds=1,
        rounds=[
            ConvRound(
                round_index=1,
                target_ref="original-target",
                original_target_gate="within_original_target",
                delta_gate="non_material_delta",
                findings=[
                    _finding(
                        "conv-max-round-risk",
                        "Non-material residual is accepted because max rounds were reached.",
                        novelty="repeated",
                        severity="p3",
                        objective_impact="preserves_objective",
                        evidence_quality="inferred",
                        disposition="accepted_risk",
                        material_change_required=False,
                    )
                ],
                material_changes=False,
                follow_up_required=False,
                evidence_sufficient=False,
                summary="Configured max round was reached.",
            )
        ],
        stop_condition="max_round",
        stop_reason="Configured maximum round count reached.",
        explicit_stop_proof="round_count equals max_rounds and no material accepted change remains.",
        residuals=residuals,
        final_report_summary="Convergence record stopped at max rounds with non-material risk carried forward.",
    )


def _finding(
    finding_id: str,
    summary: str,
    *,
    novelty: str,
    severity: str,
    objective_impact: str,
    evidence_quality: str,
    disposition: str,
    material_change_required: bool,
) -> dict[str, Any]:
    return {
        "finding_id": finding_id,
        "summary": summary,
        "novelty": novelty,
        "severity": severity,
        "objective_impact": objective_impact,
        "evidence_quality": evidence_quality,
        "disposition": disposition,
        "material_change_required": material_change_required,
    }


def _existing_conv_artifact(
    workflow: dict[str, Any],
    *,
    artifact_id: str,
    expected_path: Path,
    rendered_report: str,
) -> dict[str, Any] | None:
    matches = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == artifact_id
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(f"duplicate conv report artifact id: {artifact_id}")
    artifact = matches[0]
    if artifact.get("kind") != "report":
        raise ValueError(f"conv report artifact id has wrong kind: {artifact.get('kind')!r}")
    path = artifact.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("conv report artifact path is missing")
    if not Path(path).is_file():
        raise ValueError(f"conv report artifact path is missing: {path}")
    if Path(path).expanduser().resolve() != expected_path:
        raise ValueError("conv report artifact path is not the canonical conv output path")
    if Path(path).read_text(encoding="utf-8") != rendered_report:
        raise ValueError("existing conv artifact does not match rendered final report")
    return artifact


def _record_conv_round_execution_artifact(
    handler: ModeHandler,
    workflow_id: str,
    *,
    result: dict[str, Any],
) -> dict[str, Any] | None:
    if not result.get("rounds"):
        return None
    artifact_path = handler.store.workflow_dir(workflow_id) / "artifacts" / "conv-round-execution.json"
    workflow = handler.load_workflow(workflow_id)
    matches = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == CONV_ROUND_EXECUTION_ARTIFACT_ID
    ]
    if matches:
        if len(matches) > 1:
            raise ValueError(f"duplicate conv round execution artifact id: {CONV_ROUND_EXECUTION_ARTIFACT_ID}")
        artifact = matches[0]
        if artifact.get("kind") != "evidence":
            raise ValueError("conv round execution artifact must use evidence kind")
        if not Path(str(artifact.get("path", ""))).is_file():
            raise ValueError("conv round execution artifact path is missing")
    else:
        write_conv_round_execution_artifact(artifact_path, result)
        artifact = handler.record_artifact(
            workflow_id,
            kind="evidence",
            artifact_id=CONV_ROUND_EXECUTION_ARTIFACT_ID,
            path=artifact_path,
            note="trusted deterministic conv round execution evidence",
        )["artifact"]
    _record_conv_round_events(handler, workflow_id, result=result, artifact_id=artifact["artifact_id"])
    return artifact


def _record_conv_round_events(
    handler: ModeHandler,
    workflow_id: str,
    *,
    result: dict[str, Any],
    artifact_id: str,
) -> None:
    existing = _conv_round_event_keys(handler, workflow_id, artifact_id=artifact_id)
    for round_state in result.get("rounds") or []:
        round_index = round_state["round_index"]
        for event_type in ("round_start", "round_summary"):
            key = (event_type, round_index)
            if key in existing:
                continue
            payload = {
                "runner_ref": result["runner_ref"],
                "artifact_id": artifact_id,
                "round_index": round_index,
            }
            if event_type == "round_start":
                payload.update(
                    {
                        "target_ref": round_state["target_ref"],
                        "original_target_gate": round_state["original_target_gate"],
                    }
                )
            else:
                payload.update(
                    {
                        "status": "pass",
                        "finding_count": len(round_state.get("findings") or []),
                        "material_changes": round_state["material_changes"],
                        "follow_up_required": round_state["follow_up_required"],
                        "evidence_sufficient": round_state["evidence_sufficient"],
                    }
                )
            handler.store.append_event(
                workflow_id,
                {
                    "schema_version": 1,
                    "event_id": f"evt-conv-{event_type}-{round_index}-{workflow_id}",
                    "workflow_id": workflow_id,
                    "event_type": event_type,
                    "created_at": now_iso(),
                    "note": "trusted deterministic conv round evidence recorded",
                    "payload": payload,
                },
            )


def _conv_round_event_keys(handler: ModeHandler, workflow_id: str, *, artifact_id: str) -> set[tuple[str, int]]:
    path = handler.store.workflow_dir(workflow_id) / "events.jsonl"
    if not path.exists():
        return set()
    keys: set[tuple[str, int]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        payload = event.get("payload") or {}
        event_type = event.get("event_type")
        if event_type in {"round_start", "round_summary"} and payload.get("artifact_id") == artifact_id:
            keys.add((event_type, int(payload.get("round_index") or 0)))
    return keys


def _record_specialist_review(
    handler: ModeHandler,
    workflow_id: str,
    *,
    target: str,
    packet: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not packet:
        return None
    artifact_id = specialist_artifact_id("conv")
    review = build_specialist_review(packet, mode="conv", target=target, artifact_id=artifact_id)
    artifact_path = handler.store.workflow_dir(workflow_id) / "artifacts" / "conv-specialist-findings.json"
    workflow = handler.load_workflow(workflow_id)
    existing = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == artifact_id
    ]
    if existing:
        if len(existing) > 1:
            raise ValueError(f"duplicate specialist findings artifact id: {artifact_id}")
        artifact = existing[0]
        if artifact.get("kind") != "evidence":
            raise ValueError("specialist findings artifact must use evidence kind")
    else:
        write_specialist_artifact(artifact_path, {"packet": packet, "specialist_review": review["state"]})
        artifact = handler.record_artifact(
            workflow_id,
            kind="evidence",
            artifact_id=artifact_id,
            path=artifact_path,
            note="validated runner-provided conv specialist findings",
        )["artifact"]
    _record_specialist_events(handler, workflow_id, review=review, artifact_id=artifact["artifact_id"])
    return review


def _record_native_specialist_review(
    handler: ModeHandler,
    workflow_id: str,
    *,
    target: str,
    native_agent_backend: Any,
) -> dict[str, Any]:
    artifact_id = specialist_artifact_id("conv")
    requests = _native_conv_requests(workflow_id=workflow_id, target=target)
    results = native_agent_backend.run_panel(requests)
    review = build_native_specialist_review(
        results,
        mode="conv",
        target=target,
        artifact_id=artifact_id,
        panel_id=f"native-conv-panel-{workflow_id}",
    )
    artifact_path = handler.store.workflow_dir(workflow_id) / "artifacts" / "conv-native-specialist-findings.json"
    workflow = handler.load_workflow(workflow_id)
    existing = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == artifact_id
    ]
    if existing:
        if len(existing) > 1:
            raise ValueError(f"duplicate specialist findings artifact id: {artifact_id}")
        artifact = existing[0]
        if artifact.get("kind") != "evidence":
            raise ValueError("native specialist findings artifact must use evidence kind")
    else:
        write_specialist_artifact(artifact_path, {"native_results": [item.as_dict() for item in results], "specialist_review": review["state"]})
        artifact = handler.record_artifact(
            workflow_id,
            kind="evidence",
            artifact_id=artifact_id,
            path=artifact_path,
            note="validated native OpenClaw conv specialist findings",
        )["artifact"]
    _record_specialist_events(handler, workflow_id, review=review, artifact_id=artifact["artifact_id"])
    return review


def _native_conv_requests(*, workflow_id: str, target: str) -> list[NativeLaunchRequest]:
    profile_refs = ["native-conv-integrity", "native-conv-regression", "native-conv-ops"]
    return [
        NativeLaunchRequest(
            mode="conv",
            objective=target,
            target_refs=[{"kind": "conv_target", "text": target}],
            profile_ref=profile_ref,
            context_hash=stable_hash({"workflow_id": workflow_id, "target": target, "profile_ref": profile_ref}),
            idempotency_key=stable_hash({"workflow_id": workflow_id, "profile_ref": profile_ref, "round": 1}),
            output_schema={"schema_ref": "structured_specialist_finding.v1"},
            tool_policy=dict(DEFAULT_TOOL_POLICY),
            session_key=f"agent:main:converge-{workflow_id}-{index + 1}",
            request_id=f"conv-native-{workflow_id}-{index + 1}",
            profile_context_refs=[{"kind": "native_profile", "id": profile_ref}],
        )
        for index, profile_ref in enumerate(profile_refs)
    ]


def _attach_fix_runner_state(
    specialist_state: dict[str, Any],
    *,
    workflow_id: str,
    target: str,
    result_packet: dict[str, Any] | None = None,
    source_root: Path | None = None,
) -> dict[str, Any]:
    state = dict(specialist_state)
    accepted_changes = list(state.get("accepted_change_refs") or [])
    if not accepted_changes:
        state.update(
            {
                "fix_runner_required": False,
                "fix_runner_request_refs": [],
                "fix_runner_result_refs": [],
                "fix_runner_collection_status": {
                    "status": "not_required",
                    "pending_request_ids": [],
                    "completed_result_count": 0,
                    "source": SOURCE_FIX_RUNNER,
                    "requires_follow_up_round": False,
                },
            }
        )
        return state
    for change in accepted_changes:
        if not isinstance(change.get("local_file_edits"), list) or not change["local_file_edits"]:
            raise ValueError("fix_runner accepted changes require bounded local_file_edits")
    runner_id = f"conv-fix-runner-{workflow_id}-round-{state['round_index']}"
    request_status = "completed" if result_packet is not None else "pending"
    request = {
        "runner_id": runner_id,
        "mode": "conv",
        "objective": "Apply only accepted local convergence changes, then run focused checks.",
        "workflow_id": workflow_id,
        "target_ref": "original-target",
        "target": target,
        "source_classification": SOURCE_FIX_RUNNER,
        "status": request_status,
        "accepted_change_refs": accepted_changes,
        "artifact_refs": [specialist_artifact_id("conv")],
        "tool_policy": dict(FIX_RUNNER_TOOL_POLICY),
        "agent_session_ref": None,
        "idempotency_key": stable_hash(
            {
                "workflow_id": workflow_id,
                "round_index": state["round_index"],
                "accepted_change_refs": accepted_changes,
                "source": SOURCE_FIX_RUNNER,
            }
        ),
    }
    validate_fix_runner_request(request)
    result_refs = []
    collection_status = {
        "status": "pending",
        "pending_request_ids": [runner_id],
        "completed_result_count": 0,
        "source": SOURCE_FIX_RUNNER,
        "requires_follow_up_round": True,
        "follow_up_completed": False,
        "relaunch_required": False,
        "collection_cursor": stable_hash(
            {
                "workflow_id": workflow_id,
                "runner_id": runner_id,
                "accepted_change_refs": accepted_changes,
            }
        ),
    }
    if result_packet is not None:
        result = _normalize_fix_runner_result(
            result_packet,
            request=request,
            workflow_id=workflow_id,
            accepted_changes=accepted_changes,
            source_root=source_root,
        )
        validate_fix_runner_result(result)
        result_refs = [result]
        collection_status = {
            "status": "complete",
            "pending_request_ids": [],
            "completed_result_count": 1,
            "completed_result_ids": [result["result_id"]],
            "source": SOURCE_FIX_RUNNER,
            "requires_follow_up_round": True,
            "follow_up_completed": True,
            "relaunch_required": False,
            "collection_cursor": stable_hash(
                {
                    "workflow_id": workflow_id,
                    "runner_id": runner_id,
                    "result_id": result["result_id"],
                    "accepted_change_refs": accepted_changes,
                }
            ),
        }
    state.update(
        {
            "fix_runner_required": True,
            "fix_runner_request_refs": [request],
            "fix_runner_result_refs": result_refs,
            "fix_runner_collection_status": collection_status,
        }
    )
    return state


def _normalize_fix_runner_result(
    result: dict[str, Any],
    *,
    request: dict[str, Any],
    workflow_id: str,
    accepted_changes: list[dict[str, Any]],
    source_root: Path | None,
) -> dict[str, Any]:
    payload = dict(result)
    if payload.get("runner_id") != request["runner_id"]:
        raise ValueError("fix_runner result runner_id must match request")
    if payload.get("workflow_id") != workflow_id:
        raise ValueError("fix_runner result workflow_id must match workflow")
    if payload.get("accepted_change_refs") != accepted_changes:
        raise ValueError("fix_runner result accepted_change_refs must match request")
    if source_root is None:
        raise ValueError("fix_runner result requires source_root validation")
    _validate_fix_runner_mutation_proof(payload, source_root=source_root)
    return payload


def _validate_fix_runner_mutation_proof(result: dict[str, Any], *, source_root: Path) -> None:
    root = source_root.resolve()
    if result.get("source_root") != str(root):
        raise ValueError("fix_runner result source_root must match supplied source root")
    mutations = result.get("file_mutations")
    if not isinstance(mutations, list) or not mutations:
        raise ValueError("fix_runner result requires file_mutations proof")
    expected_key = stable_hash(
        {
            "runner_id": result["runner_id"],
            "workflow_id": result["workflow_id"],
            "source_root": result["source_root"],
            "accepted_change_refs": result["accepted_change_refs"],
            "file_mutations": mutations,
        }
    )
    if result.get("idempotency_key") != expected_key:
        raise ValueError("fix_runner result idempotency_key must match mutation proof")
    if _expected_fix_runner_mutations(result["accepted_change_refs"]) != mutations:
        raise ValueError("fix_runner mutation proof must match accepted local_file_edits before/after hashes")
    for mutation in mutations:
        if not isinstance(mutation, dict):
            raise ValueError("fix_runner mutation proof entries must be objects")
        rel_path = mutation.get("path")
        after_sha = mutation.get("after_sha256")
        if not isinstance(rel_path, str) or not rel_path or rel_path.startswith("/") or ".." in Path(rel_path).parts:
            raise ValueError("fix_runner mutation proof path must be safe relative")
        if not isinstance(after_sha, str) or not after_sha:
            raise ValueError("fix_runner mutation proof requires after_sha256")
        target = (root / rel_path).resolve()
        if root != target and root not in target.parents:
            raise ValueError("fix_runner mutation proof path escapes source root")
        if not target.is_file():
            raise ValueError(f"fix_runner mutation proof target does not exist: {rel_path}")
        current_sha = hashlib.sha256(target.read_bytes()).hexdigest()
        if current_sha != after_sha:
            raise ValueError("fix_runner mutation proof after_sha256 must match current source root")


def _expected_fix_runner_mutations(accepted_change_refs: list[dict[str, Any]]) -> list[dict[str, str]]:
    expected: list[dict[str, str]] = []
    for change in accepted_change_refs:
        edits = change.get("local_file_edits") or []
        for edit in edits:
            expected.append(
                {
                    "change_ref": change["change_ref"],
                    "path": edit["path"],
                    "before_sha256": hashlib.sha256(edit["old"].encode("utf-8")).hexdigest(),
                    "after_sha256": hashlib.sha256(edit["new"].encode("utf-8")).hexdigest(),
                }
            )
    return expected


def _record_fix_runner_events(handler: ModeHandler, workflow_id: str, *, state: dict[str, Any]) -> None:
    if not state.get("fix_runner_required"):
        return
    event_types = _fix_runner_event_types(handler, workflow_id)
    collection_status = state["fix_runner_collection_status"]
    if "fix_runner_requested" not in event_types:
        handler.store.append_event(
            workflow_id,
            {
                "schema_version": 1,
                "event_id": f"evt-conv-fix-runner-requested-{workflow_id}",
                "workflow_id": workflow_id,
                "event_type": "fix_runner_requested",
                "created_at": now_iso(),
                "note": "coordinator-owned fix runner request recorded for accepted convergence changes",
                "payload": {
                    "mode": "conv",
                    "source": SOURCE_FIX_RUNNER,
                    "request_ids": [item["runner_id"] for item in state["fix_runner_request_refs"]],
                    "accepted_change_refs": [
                        item["change_ref"]
                        for request in state["fix_runner_request_refs"]
                        for item in request["accepted_change_refs"]
                    ],
                    "tool_policy_id": state["fix_runner_request_refs"][0]["tool_policy"]["policy_id"],
                    "collection_status": collection_status["status"],
                    "collection_cursor": collection_status["collection_cursor"],
                    "requires_follow_up_round": collection_status["requires_follow_up_round"],
                },
            },
        )
    if not state.get("fix_runner_result_refs"):
        return
    _record_fix_runner_result_artifacts(handler, workflow_id, state=state)
    if "fix_runner_completed" in event_types:
        return
    handler.store.append_event(
        workflow_id,
        {
            "schema_version": 1,
            "event_id": f"evt-conv-fix-runner-completed-{workflow_id}",
            "workflow_id": workflow_id,
            "event_type": "fix_runner_completed",
            "created_at": now_iso(),
            "note": "coordinator-owned fix runner completed bounded accepted changes before follow-up",
            "payload": {
                "mode": "conv",
                "source": SOURCE_FIX_RUNNER,
                "request_ids": [item["runner_id"] for item in state["fix_runner_request_refs"]],
                "result_ids": [item["result_id"] for item in state["fix_runner_result_refs"]],
                "artifact_refs": [
                    artifact_id
                    for result in state["fix_runner_result_refs"]
                    for artifact_id in result["artifact_refs"]
                ],
                "completed_result_count": collection_status["completed_result_count"],
                "collection_cursor": collection_status["collection_cursor"],
                "requires_follow_up_round": collection_status["requires_follow_up_round"],
                "follow_up_completed": collection_status["follow_up_completed"],
            },
        },
    )


def _append_fix_runner_evidence(state: dict[str, Any]) -> None:
    evidence = state.setdefault("evidence", [])
    for result in state.get("fix_runner_result_refs") or []:
        artifact_refs = result.get("artifact_refs") if isinstance(result, dict) else None
        if not artifact_refs:
            continue
        evidence.append(
            {
                "evidence_key": f"fix-runner:{result['result_id']}",
                "kind": "fix_runner_result",
                "summary": "Coordinator-owned fix runner result registered through bounded artifact path.",
                "artifact_refs": artifact_refs,
                "produced_after_change_refs": [
                    item["change_ref"]
                    for item in result.get("applied_change_refs", [])
                    if isinstance(item, dict) and isinstance(item.get("change_ref"), str) and item["change_ref"]
                ],
            }
        )


def _record_fix_runner_result_artifacts(handler: ModeHandler, workflow_id: str, *, state: dict[str, Any]) -> None:
    workflow = handler.load_workflow(workflow_id)
    existing = {
        artifact.get("artifact_id"): artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict)
    }
    for request, result in zip(state["fix_runner_request_refs"], state["fix_runner_result_refs"], strict=True):
        artifact_id = result["artifact_refs"][0]
        artifact_path = handler.store.workflow_dir(workflow_id) / "artifacts" / f"{artifact_id}.json"
        payload = {"request": request, "result": result}
        if artifact_id in existing:
            artifact = existing[artifact_id]
            if artifact.get("kind") != "evidence":
                raise ValueError("fix_runner result artifact must use evidence kind")
            if json.loads(Path(str(artifact["path"])).read_text(encoding="utf-8")) != payload:
                raise ValueError("fix_runner result artifact must match state")
            continue
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        handler.record_artifact(
            workflow_id,
            kind="evidence",
            artifact_id=artifact_id,
            path=artifact_path,
            note="coordinator-owned fix runner bounded result evidence",
        )


def _fix_runner_event_types(handler: ModeHandler, workflow_id: str) -> set[str]:
    path = handler.store.workflow_dir(workflow_id) / "events.jsonl"
    if not path.exists():
        return set()
    return {
        event.get("event_type")
        for event in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        if isinstance(event, dict)
    }


def _record_specialist_events(handler: ModeHandler, workflow_id: str, *, review: dict[str, Any], artifact_id: str) -> None:
    existing = _specialist_event_types(handler, workflow_id, artifact_id=artifact_id)
    for event_type in ("agent_panel_requested", "agent_findings_recorded", "finding_arbitrated"):
        if event_type in existing:
            continue
        handler.store.append_event(
            workflow_id,
            {
                "schema_version": 1,
                "event_id": f"evt-conv-{event_type}-{workflow_id}",
                "workflow_id": workflow_id,
                "event_type": event_type,
                "created_at": now_iso(),
                "note": "validated specialist review evidence",
                "payload": {
                    "artifact_id": artifact_id,
                    "mode": "conv",
                    "runner_ref": review["state"]["review_panel_spec"].get("runner_ref") or SPECIALIST_REVIEW_RUNNER_REF,
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
    return {
        event.get("event_type")
        for event in (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        if isinstance(event, dict) and (event.get("payload") or {}).get("artifact_id") == artifact_id
    }


def _specialist_execution_markers(state: dict[str, Any], *, artifact_id: str) -> dict[str, Any]:
    if state["review_panel_spec"].get("runner_ref") == NATIVE_PANEL_RUNNER_REF:
        return {
            "execution_capability": "delegated_agents",
            "execution_source": SOURCE_NATIVE_AGENT_PANEL,
            "satisfies_native_agent_panel": True,
            "execution_required": True,
            "execution_performed": True,
            "execution_evidence_refs": [artifact_id],
            "synthetic_report": False,
            "runner_ref": NATIVE_PANEL_RUNNER_REF,
            "execution_started_at": now_iso(),
            "execution_completed_at": now_iso(),
            "execution_classification_reason": "native OpenClaw specialist child sessions collected",
        }
    return {
        "execution_capability": "delegated_agents",
        "execution_source": SOURCE_RUNNER_PROVIDED_PACKET,
        "satisfies_native_agent_panel": False,
        "execution_required": True,
        "execution_performed": True,
        "execution_evidence_refs": [artifact_id],
        "synthetic_report": False,
        "runner_ref": SPECIALIST_REVIEW_RUNNER_REF,
        "execution_started_at": now_iso(),
        "execution_completed_at": now_iso(),
        "execution_classification_reason": "trusted runner provided structured specialist findings",
    }


def _specialist_blocked_final_status(residuals: dict[str, list[str]]) -> dict[str, Any]:
    stop_reason = (
        "blocked_specialist_findings"
        if residuals.get("blocking_remaining")
        else "blocked_specialist_follow_up_required"
    )
    return {
        "result": "blocked",
        "stop_reason": stop_reason,
        "done": [
            "Recorded bounded convergence round metadata",
            "Classified specialist findings through original-target and delta gates",
            "Blocked pass-style completion on high-severity specialist findings",
        ],
        "checked": [
            "Structured specialist evidence was bound to artifact and event proof",
            "Visible delivery remains gated by reserve-delivery/report-proof/complete-reported",
        ],
        "residuals": residuals,
    }


def _specialist_state_from_conv(state: dict[str, Any]) -> dict[str, Any]:
    specialist_state = {key: state[key] for key in (
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
    if "specialist_max_rounds" in state:
        specialist_state["max_rounds"] = state["specialist_max_rounds"]
    return specialist_state


def _compact(text: str) -> str:
    return " ".join(text.split())
