"""Conv mode vertical slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import ModeHandler, ModeOutcome
from .execution_truth import classify_execution_markers
from ..messages import normalize_residuals


CONV_REPORT_ARTIFACT_ID = "conv-final-report"
CONV_STOP_CONDITIONS = {"evidence_sufficient", "max_round", "blocked_no_execution_evidence"}
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

    def as_state(self, *, artifact_id: str, artifact_path: str) -> dict[str, Any]:
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
        state.update(classify_execution_markers(self.target, capability="synthetic_round_only"))
        return state


class ConvHandler(ModeHandler):
    """Produces an iterative convergence record without owning delivery."""

    kind = "conv"

    def finalize_conv(
        self,
        workflow_id: str,
        *,
        recovery_lease_id: str | None = None,
        recovery_lease_holder: str | None = None,
    ) -> dict[str, Any]:
        self.validate_recovery_preflight(
            workflow_id,
            recovery_lease_id=recovery_lease_id,
            recovery_lease_holder=recovery_lease_holder,
        )
        workflow = self.load_workflow(workflow_id)
        record = build_conv_record(workflow.get("source_request") or workflow.get("objective") or "")
        rendered_report = render_conv_report(record)
        artifact_path = (self.store.workflow_dir(workflow_id) / "artifacts" / "conv-report.md").expanduser().resolve()
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
        state = record.as_state(artifact_id=artifact_ref, artifact_path=artifact_path)
        checkpoint = self.record_outcome(
            workflow_id,
            ModeOutcome(
                summary="Final convergence record is ready for visible delivery.",
                status_after="completed_unreported",
                phase_after="terminal",
                checkpoint_type="terminal",
                event_type="complete",
                worklog_block_kind="terminal_summary",
                step_result="terminal",
                residuals=record.residuals,
                terminal_evidence=evidence,
                mode_state_update=state,
                recovery_lease_id=recovery_lease_id,
                recovery_lease_holder=recovery_lease_holder,
                final_status={
                    "result": "pass_with_risks" if any(record.residuals.values()) else "pass",
                    "stop_reason": record.stop_condition,
                    "done": [
                        "Recorded bounded convergence round metadata",
                        "Classified findings through original-target and delta gates",
                        "Stopped through evidence sufficiency or max-round proof",
                    ],
                    "checked": [
                        "Conv mode used shared artifact and checkpoint contracts",
                        "Visible delivery remains gated by reserve-delivery/report-proof/complete-reported",
                    ],
                    "residuals": record.residuals,
                },
            ),
        )
        return {"workflow_id": workflow_id, "artifact": artifact, "checkpoint": checkpoint, "conv": state}


def build_conv_record(text: str) -> ConvRecord:
    target = _compact(text) or "Converge on the supplied target with bounded evidence."
    return _evidence_sufficient_record(target)


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


def _compact(text: str) -> str:
    return " ".join(text.split())
