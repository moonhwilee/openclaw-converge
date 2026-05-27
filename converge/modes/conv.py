"""Conv mode vertical slice."""

from __future__ import annotations

import json
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
    SPECIALIST_REVIEW_RUNNER_REF,
    build_specialist_review,
    specialist_artifact_id,
    validate_specialist_state,
    write_specialist_artifact,
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
        specialist_review = _record_specialist_review(self, workflow_id, target=text, packet=specialist_findings)
        specialist_state = specialist_review["state"] if specialist_review else None
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
            state.update(_specialist_execution_markers(artifact_id=specialist_artifact_id("conv")))
        state, residuals, block_reason = apply_execution_truth_block("conv", state, residuals=record.residuals)
        if specialist_state and (residuals.get("blocking_remaining") or state.get("follow_up_required")):
            specialist_block_reason = (
                "blocked_specialist_findings"
                if residuals.get("blocking_remaining")
                else "blocked_specialist_follow_up_required"
            )
            state["stop_condition"] = specialist_block_reason
            state["stop_reason"] = specialist_block_reason
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
        artifact = _existing_conv_artifact(workflow, expected_path=artifact_path, rendered_report=rendered_report)
        if artifact is None:
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(rendered_report, encoding="utf-8")
            artifact = self.record_artifact(
                workflow_id,
                kind="report",
                artifact_id=CONV_REPORT_ARTIFACT_ID,
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
        state["final_report_artifact_id"] = artifact_ref
        state["final_report_artifact_path"] = artifact_path
        state = attach_phase5a_evidence_contract(
            "conv",
            workflow=self.load_workflow(workflow_id),
            state=state,
            terminal_evidence=evidence,
        )
        checkpoint = self.record_outcome(
            workflow_id,
            ModeOutcome(
                summary=(
                    "Convergence terminal success blocked because execution evidence is missing."
                    if block_reason
                    else "Final convergence record is ready for visible delivery."
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
                    _specialist_blocked_final_status(residuals)
                    if block_reason in {"blocked_specialist_findings", "blocked_specialist_follow_up_required"}
                    else execution_blocked_final_status("conv", block_reason, residuals)
                    if block_reason
                    else {
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
                ),
                failure_reason=block_reason,
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
    validate_specialist_state(specialist_state)
    findings = [_conv_finding_from_arbitration(item) for item in specialist_state["finding_arbitration"]]
    material_changes = any(item["material_change_required"] for item in findings)
    follow_up_required = bool(specialist_state["follow_up_round_required"])
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
            if item["decision"] in {"fix", "defer"}
        ],
        "deferred_scope": [
            "Phase 4A binds runner-provided structured findings; native specialist launch and fix-runner application remain later slices.",
        ],
    }
    evidence_sufficient = not residuals["blocking_remaining"] and not follow_up_required
    return ConvRecord(
        target=target,
        max_rounds=specialist_state["max_rounds"],
        rounds=[
            ConvRound(
                round_index=specialist_state["round_index"],
                target_ref="original-target",
                original_target_gate=specialist_state["original_target_gate"],
                delta_gate=specialist_state["delta_regression_gate"],
                findings=findings,
                material_changes=material_changes,
                follow_up_required=follow_up_required,
                evidence_sufficient=evidence_sufficient,
                summary="Runner-provided specialist findings were deduped and arbitrated as a bounded convergence round.",
            )
        ],
        stop_condition="evidence_sufficient" if evidence_sufficient else "blocked_no_execution_evidence",
        stop_reason="structured_specialist_findings_bound",
        explicit_stop_proof=specialist_state["round_stop_proof"],
        residuals=residuals,
        final_report_summary="Convergence bound structured specialist findings without allowing specialist side effects.",
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
        validate_specialist_state(_specialist_state_from_conv(state))
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


def _existing_conv_artifact(workflow: dict[str, Any], *, expected_path: Path, rendered_report: str) -> dict[str, Any] | None:
    matches = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == CONV_REPORT_ARTIFACT_ID
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError(f"duplicate conv report artifact id: {CONV_REPORT_ARTIFACT_ID}")
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
                "note": "validated runner-provided specialist review evidence",
                "payload": {
                    "artifact_id": artifact_id,
                    "mode": "conv",
                    "runner_ref": SPECIALIST_REVIEW_RUNNER_REF,
                    "finding_count": len(review["state"]["agent_finding_refs"]),
                    "arbitration_count": len(review["state"]["finding_arbitration"]),
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


def _specialist_execution_markers(*, artifact_id: str) -> dict[str, Any]:
    return {
        "execution_capability": "delegated_agents",
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
        "agent_request_refs",
        "agent_result_refs",
        "agent_result_idempotency_keys",
        "agent_result_collection_status",
        "recovery_resume_cursor",
    )}


def _compact(text: str) -> str:
    return " ".join(text.split())
