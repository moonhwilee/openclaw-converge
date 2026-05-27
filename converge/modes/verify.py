"""Verify mode vertical slice."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .base import ModeHandler, ModeOutcome
from .execution_truth import classify_execution_markers
from ..messages import normalize_residuals


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

    def as_state(self, *, artifact_id: str, artifact_path: str) -> dict[str, Any]:
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
        recovery_lease_id: str | None = None,
        recovery_lease_holder: str | None = None,
    ) -> dict[str, Any]:
        self.validate_recovery_preflight(
            workflow_id,
            recovery_lease_id=recovery_lease_id,
            recovery_lease_holder=recovery_lease_holder,
        )
        workflow = self.load_workflow(workflow_id)
        record = build_verify_record(workflow.get("source_request") or workflow.get("objective") or "")
        rendered_report = render_verify_report(record)
        artifact_path = (self.store.workflow_dir(workflow_id) / "artifacts" / "verify-report.md").expanduser().resolve()
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
        evidence = {
            "evidence_key": "verify-final-report",
            "kind": "artifact",
            "summary": "Final verification report registered through the shared artifact path.",
            "artifact_refs": [artifact_ref],
        }
        evidence_records = [*record.evidence_records, evidence]
        state_record = VerifyRecord(
            target=record.target,
            check_plan=record.check_plan,
            deterministic_checks=record.deterministic_checks,
            reviewer_findings=record.reviewer_findings,
            verdict=record.verdict,
            evidence_records=evidence_records,
            residuals=record.residuals,
            final_report_summary=record.final_report_summary,
        )
        checkpoint = self.record_outcome(
            workflow_id,
            ModeOutcome(
                summary="Final verification verdict is ready for visible delivery.",
                status_after="completed_unreported",
                phase_after="terminal",
                checkpoint_type="terminal",
                event_type="complete",
                worklog_block_kind="terminal_summary",
                step_result="terminal",
                residuals=state_record.residuals,
                terminal_evidence=evidence,
                mode_state_update=state_record.as_state(artifact_id=artifact_ref, artifact_path=artifact_path),
                recovery_lease_id=recovery_lease_id,
                recovery_lease_holder=recovery_lease_holder,
                final_status={
                    "result": state_record.verdict,
                    "done": [
                        "Recorded a structured verification verdict",
                        "Registered the final verification report through the shared artifact path",
                        "Stopped before adapters, recovery, or external actions",
                    ],
                    "checked": [
                        "Verify mode used shared artifact and checkpoint contracts",
                        "Visible delivery remains gated by reserve-delivery/report-proof/complete-reported",
                    ],
                    "residuals": state_record.residuals,
                },
            ),
        )
        return {
            "workflow_id": workflow_id,
            "artifact": artifact,
            "checkpoint": checkpoint,
            "verify": state_record.as_state(artifact_id=artifact_ref, artifact_path=artifact_path),
        }


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
