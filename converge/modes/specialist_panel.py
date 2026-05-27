"""Bounded structured specialist finding adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SPECIALIST_REVIEW_RUNNER_REF = "trusted-runner-provided-specialist-findings-v1"
SEVERITIES = {"p0", "p1", "p2", "p3"}
DECISIONS = {"block", "fix", "accept_risk", "defer", "reject"}
FORBIDDEN_SIDE_EFFECT_FIELDS = {
    "visible_message_sent",
    "external_action_performed",
    "target_mutations",
    "workflow_state_mutations",
    "restarted_services",
    "push_performed",
    "pr_opened",
}
ALLOWED_SOURCE_PROVENANCE = {"runner_provided", "trusted_runner"}
ALLOWED_PACKET_FIELDS = {
    "panel_id",
    "risk_level",
    "profiles",
    "findings",
    "side_effects_performed",
    "deterministic_check_results",
    "max_rounds",
    "round_index",
    "owner_stop_ref",
}


def load_specialist_packet(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("specialist findings file must contain a JSON object")
    return payload


def specialist_artifact_id(mode: str) -> str:
    return f"{mode}-specialist-findings"


def build_specialist_review(
    packet: dict[str, Any],
    *,
    mode: str,
    target: str,
    artifact_id: str,
) -> dict[str, Any]:
    _validate_allowed_packet_fields(packet)
    _validate_no_forbidden_side_effect_fields(packet)
    side_effects = packet.get("side_effects_performed") or []
    if not isinstance(side_effects, list):
        raise ValueError("specialist side_effects_performed must be an array")
    if side_effects:
        raise ValueError("specialist findings packet must not report side effects")
    profiles = _profiles(packet.get("profiles"))
    findings = _findings(packet.get("findings"), profiles=profiles)
    raw_map, groups = _dedupe_findings(findings)
    arbitration = [_arbitrate_group(group_id, items) for group_id, items in groups.items()]
    accepted_changes = [
        {
            "change_ref": f"accepted-change-{item['group_id']}",
            "finding_ids": item["finding_ids"],
            "fix": item["minimal_fix_or_test"],
            "evidence": item["evidence"],
            "check": item["minimal_fix_or_test"],
        }
        for item in arbitration
        if item["decision"] == "fix"
    ]
    follow_up = bool(accepted_changes)
    state = {
        "review_panel_spec": {
            "panel_id": _required_string(packet, "panel_id"),
            "mode": mode,
            "target": target,
            "risk_level": _optional_string(packet, "risk_level", default="medium"),
            "profiles": profiles,
            "runner_ref": SPECIALIST_REVIEW_RUNNER_REF,
            "prohibited_actions": [
                "visible_messages",
                "external_actions",
                "service_restart",
                "push_or_pr",
                "target_mutation",
                "workflow_state_mutation",
            ],
        },
        "deterministic_check_results": packet.get("deterministic_check_results") or [],
        "agent_finding_refs": findings,
        "raw_finding_to_group_map": raw_map,
        "finding_arbitration": arbitration,
        "accepted_change_refs": accepted_changes,
        "original_target_gate": "within_original_target",
        "delta_regression_gate": "new_material_delta" if follow_up else "no_delta",
        "follow_up_round_required": follow_up,
        "max_rounds_default": 5,
        "max_rounds": int(packet.get("max_rounds") or 5),
        "round_index": int(packet.get("round_index") or 1),
        "stop_reason": "structured_specialist_findings_bound",
        "owner_stop_ref": packet.get("owner_stop_ref"),
        "round_stop_proof": (
            "Runner-provided specialist findings were validated, deduped by failure mode, "
            "and arbitrated without allowing specialists to mutate state or send visible reports."
        ),
    }
    validate_specialist_state(state)
    return {
        "state": state,
        "reviewer_findings": [
            f"{item['finding_id']}: {item['finding']} ({item['severity']}, {item['confidence']:.2f})"
            for item in findings
        ],
        "evidence": {
            "evidence_key": f"{mode}-specialist-findings",
            "kind": "specialist_findings",
            "summary": f"Validated {len(findings)} runner-provided structured specialist finding(s).",
            "artifact_refs": [artifact_id],
        },
    }


def validate_specialist_state(state: dict[str, Any]) -> None:
    required = {
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
    }
    missing = sorted(required - set(state))
    if missing:
        raise ValueError(f"specialist review state is missing required fields: {missing!r}")
    profiles = state["review_panel_spec"].get("profiles")
    if not isinstance(profiles, list) or not 3 <= len(profiles) <= 5:
        raise ValueError("specialist review panel requires 3-5 profiles")
    profile_ids = [item.get("profile_id") for item in profiles if isinstance(item, dict)]
    _require_unique(profile_ids, "specialist profile ids")
    findings = state["agent_finding_refs"]
    if not isinstance(findings, list):
        raise ValueError("specialist agent_finding_refs must be an array")
    for finding in findings:
        _validate_persisted_finding(finding)
    finding_ids = [item.get("finding_id") for item in findings if isinstance(item, dict)]
    _require_unique(finding_ids, "specialist finding ids")
    finding_id_set = set(finding_ids)
    map_items = state["raw_finding_to_group_map"]
    if not isinstance(map_items, list) or {item.get("finding_id") for item in map_items if isinstance(item, dict)} != finding_id_set:
        raise ValueError("specialist raw_finding_to_group_map must map every finding exactly once")
    arbitration = state["finding_arbitration"]
    if not isinstance(arbitration, list):
        raise ValueError("specialist finding_arbitration must be an array")
    arbitrated = set()
    for item in arbitration:
        if not isinstance(item, dict) or item.get("decision") not in DECISIONS:
            raise ValueError("specialist finding_arbitration decision is invalid")
        ids = item.get("finding_ids")
        if not isinstance(ids, list) or not ids:
            raise ValueError("specialist finding_arbitration requires finding_ids")
        for finding_id in ids:
            if finding_id in arbitrated:
                raise ValueError("specialist finding arbitration duplicated a finding")
            arbitrated.add(finding_id)
    if arbitrated != finding_id_set:
        raise ValueError("specialist finding_arbitration must cover every finding exactly once")
    accepted_changes = state["accepted_change_refs"]
    if not isinstance(accepted_changes, list):
        raise ValueError("specialist accepted_change_refs must be an array")
    if bool(accepted_changes) != bool(state["follow_up_round_required"]):
        raise ValueError("specialist follow_up_round_required must reflect accepted changes")
    if state["max_rounds_default"] != 5:
        raise ValueError("specialist max_rounds_default must be 5")
    if not isinstance(state["max_rounds"], int) or state["max_rounds"] < 1:
        raise ValueError("specialist max_rounds must be a positive integer")
    if state["round_index"] < 1:
        raise ValueError("specialist round_index must be positive")


def write_specialist_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _profiles(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not 3 <= len(raw) <= 5:
        raise ValueError("specialist findings require 3-5 reviewer profiles")
    profiles = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("specialist profile must be an object")
        profiles.append(
            {
                "profile_id": _required_string(item, "profile_id"),
                "role": _required_string(item, "role"),
                "expertise": _string_list(item, "expertise"),
                "likely_failure_modes": _string_list(item, "likely_failure_modes"),
                "prohibited_actions": _string_list(item, "prohibited_actions"),
            }
        )
    _require_unique([item["profile_id"] for item in profiles], "specialist profile ids")
    return profiles


def _findings(raw: Any, *, profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise ValueError("specialist findings require at least one structured finding")
    profile_ids = {item["profile_id"] for item in profiles}
    findings = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("specialist finding must be an object")
        profile_id = _required_string(item, "profile_id")
        if profile_id not in profile_ids:
            raise ValueError("specialist finding profile_id must reference a panel profile")
        severity = _required_string(item, "severity")
        if severity not in SEVERITIES:
            raise ValueError("specialist finding severity is invalid")
        confidence = item.get("confidence")
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise ValueError("specialist finding confidence must be between 0 and 1")
        evidence = _required_string(item, "evidence")
        source_provenance = _required_string(item, "source_provenance")
        _validate_evidence_anchor(evidence)
        _validate_source_provenance(source_provenance)
        findings.append(
            {
                "finding_id": _required_string(item, "finding_id"),
                "profile_id": profile_id,
                "finding": _required_string(item, "finding"),
                "severity": severity,
                "evidence": evidence,
                "why_it_matters": _required_string(item, "why_it_matters"),
                "minimal_fix_or_test": _required_string(item, "minimal_fix_or_test"),
                "scope_risk": _required_string(item, "scope_risk"),
                "confidence": float(confidence),
                "failure_mode": _required_string(item, "failure_mode"),
                "source_provenance": source_provenance,
            }
        )
    _require_unique([item["finding_id"] for item in findings], "specialist finding ids")
    return findings


def _dedupe_findings(findings: list[dict[str, Any]]) -> tuple[list[dict[str, str]], dict[str, list[dict[str, Any]]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    raw_map = []
    for item in findings:
        group_id = "failure-mode-" + item["failure_mode"].strip().lower().replace(" ", "-")
        groups.setdefault(group_id, []).append(item)
        raw_map.append({"finding_id": item["finding_id"], "group_id": group_id})
    return raw_map, groups


def _arbitrate_group(group_id: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(findings, key=lambda item: ("p0", "p1", "p2", "p3").index(item["severity"]))
    strongest = ordered[0]
    if strongest["confidence"] < 0.4:
        decision = "reject"
    elif strongest["severity"] in {"p0", "p1"}:
        decision = "block"
    elif strongest["severity"] == "p2":
        decision = "fix"
    else:
        decision = "accept_risk"
    return {
        "group_id": group_id,
        "finding_ids": [item["finding_id"] for item in findings],
        "decision": decision,
        "reason": f"Arbitrated by strongest severity {strongest['severity']} and confidence {strongest['confidence']:.2f}.",
        "minimal_fix_or_test": strongest["minimal_fix_or_test"],
        "evidence": strongest["evidence"],
    }


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"specialist {key} must be a non-empty string")
    return value


def _validate_no_forbidden_side_effect_fields(packet: dict[str, Any]) -> None:
    present = sorted(key for key in FORBIDDEN_SIDE_EFFECT_FIELDS if packet.get(key))
    if present:
        raise ValueError(f"specialist findings packet reports forbidden side effects: {present!r}")


def _validate_allowed_packet_fields(packet: dict[str, Any]) -> None:
    unknown = sorted(set(packet) - ALLOWED_PACKET_FIELDS)
    if unknown:
        raise ValueError(f"specialist findings packet has unsupported fields: {unknown!r}")


def _validate_persisted_finding(finding: Any) -> None:
    if not isinstance(finding, dict):
        raise ValueError("specialist persisted finding must be an object")
    for key in (
        "finding_id",
        "profile_id",
        "finding",
        "severity",
        "evidence",
        "why_it_matters",
        "minimal_fix_or_test",
        "scope_risk",
        "failure_mode",
        "source_provenance",
    ):
        _required_string(finding, key)
    if finding["severity"] not in SEVERITIES:
        raise ValueError("specialist persisted finding severity is invalid")
    confidence = finding.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        raise ValueError("specialist persisted finding confidence must be between 0 and 1")
    _validate_evidence_anchor(finding["evidence"])
    _validate_source_provenance(finding["source_provenance"])


def _validate_evidence_anchor(evidence: str) -> None:
    if not evidence.startswith(("events.jsonl ", "worklog.md#", "verify_state.", "conv_state.")):
        raise ValueError("specialist finding evidence must reference a concrete state field or event anchor")


def _validate_source_provenance(source_provenance: str) -> None:
    if source_provenance not in ALLOWED_SOURCE_PROVENANCE:
        raise ValueError("specialist finding source_provenance must be runner_provided or trusted_runner")


def _optional_string(payload: dict[str, Any], key: str, *, default: str) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str) or not value:
        raise ValueError(f"specialist {key} must be a non-empty string")
    return value


def _string_list(payload: dict[str, Any], key: str) -> list[str]:
    value = payload.get(key)
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"specialist {key} must be a non-empty string array")
    return value


def _require_unique(values: list[Any], label: str) -> None:
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
