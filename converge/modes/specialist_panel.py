"""Bounded structured specialist finding adapter."""

from __future__ import annotations

import hashlib
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
    collection_state = _build_agent_collection_state(
        mode=mode,
        target=target,
        artifact_id=artifact_id,
        panel_id=_required_string(packet, "panel_id"),
        profiles=profiles,
        findings=findings,
    )
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
        **collection_state,
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
        "agent_request_refs",
        "agent_result_refs",
        "agent_result_idempotency_keys",
        "agent_result_collection_status",
        "recovery_resume_cursor",
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
    _validate_agent_collection_state(state, profile_ids=profile_ids, findings=findings)


def write_specialist_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _build_agent_collection_state(
    *,
    mode: str,
    target: str,
    artifact_id: str,
    panel_id: str,
    profiles: list[dict[str, Any]],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    findings_by_profile: dict[str, list[dict[str, Any]]] = {item["profile_id"]: [] for item in profiles}
    for finding in findings:
        findings_by_profile[finding["profile_id"]].append(finding)

    request_refs = []
    result_refs = []
    for profile in profiles:
        profile_ref = profile["profile_id"]
        request_id = f"{mode}-agent-request-{profile_ref}"
        context_hash = _stable_hash(
            {
                "artifact_id": artifact_id,
                "mode": mode,
                "panel_id": panel_id,
                "profile_ref": profile_ref,
                "target": target,
            }
        )
        profile_findings = findings_by_profile[profile_ref]
        result_ids = []
        for finding in profile_findings:
            result_id = f"{mode}-agent-result-{finding['finding_id']}"
            result_ids.append(result_id)
            result_refs.append(
                {
                    "result_id": result_id,
                    "request_id": request_id,
                    "profile_ref": profile_ref,
                    "attempt": 1,
                    "context_hash": context_hash,
                    "idempotency_key": _stable_hash(
                        {
                            "finding_id": finding["finding_id"],
                            "profile_ref": profile_ref,
                            "request_id": request_id,
                            "source_provenance": finding["source_provenance"],
                        }
                    ),
                    "received_at": "runner_packet",
                    "terminal_status": "accepted",
                    "evidence_refs": [finding["evidence"], artifact_id],
                    "acceptance_reason": "validated runner-provided structured specialist result",
                    "rejection_reason": None,
                }
            )
        request_refs.append(
            {
                "request_id": request_id,
                "profile_ref": profile_ref,
                "context_hash": context_hash,
                "requested_at": "runner_packet",
                "lease": {"status": "completed", "source": "runner_provided_packet"},
                "status": "completed",
                "expected_result_count": len(profile_findings),
                "result_ids": result_ids,
                "collection_cursor": f"{request_id}:complete:{len(profile_findings)}/{len(profile_findings)}",
                "terminal_decision": "accepted" if profile_findings else "accepted_no_findings",
            }
        )

    accepted_keys = [item["idempotency_key"] for item in result_refs]
    collection_cursor = f"{mode}:{artifact_id}:complete:{len(result_refs)}/{len(result_refs)}"
    return {
        "agent_request_refs": request_refs,
        "agent_result_refs": result_refs,
        "agent_result_idempotency_keys": accepted_keys,
        "agent_result_collection_status": {
            "status": "complete",
            "request_ids": [item["request_id"] for item in request_refs],
            "expected_result_count": len(result_refs),
            "accepted_result_count": len(result_refs),
            "ignored_duplicate_result_count": 0,
            "pending_request_ids": [],
            "collection_cursor": collection_cursor,
            "terminal_decision": "collection_complete",
            "relaunch_required": False,
            "replayed_side_effects": False,
        },
        "recovery_resume_cursor": collection_cursor,
    }


def _validate_agent_collection_state(
    state: dict[str, Any],
    *,
    profile_ids: list[str],
    findings: list[dict[str, Any]],
) -> None:
    requests = state["agent_request_refs"]
    results = state["agent_result_refs"]
    keys = state["agent_result_idempotency_keys"]
    status = state["agent_result_collection_status"]
    if not isinstance(requests, list) or len(requests) != len(profile_ids):
        raise ValueError("specialist agent_request_refs must contain one request per profile")
    if not isinstance(results, list):
        raise ValueError("specialist agent_result_refs must be an array")
    if not isinstance(keys, list) or any(not isinstance(item, str) or not item for item in keys):
        raise ValueError("specialist agent_result_idempotency_keys must be non-empty strings")
    if not isinstance(status, dict):
        raise ValueError("specialist agent_result_collection_status must be an object")

    finding_by_id = {item["finding_id"]: item for item in findings}
    request_by_id = {}
    expected_result_ids: set[str] = set()
    for request in requests:
        if not isinstance(request, dict):
            raise ValueError("specialist agent_request_refs entries must be objects")
        request_id = _required_string(request, "request_id")
        profile_ref = _required_string(request, "profile_ref")
        context_hash = _required_string(request, "context_hash")
        if profile_ref not in profile_ids:
            raise ValueError("specialist agent request profile_ref must reference a panel profile")
        if request.get("status") != "completed":
            raise ValueError("specialist completed structured packet must not leave agent requests pending")
        lease = request.get("lease")
        if not isinstance(lease, dict) or lease.get("status") != "completed":
            raise ValueError("specialist agent request lease must be completed")
        result_ids = request.get("result_ids")
        if not isinstance(result_ids, list) or any(not isinstance(item, str) or not item for item in result_ids):
            raise ValueError("specialist agent request result_ids must be a string array")
        expected_count = request.get("expected_result_count")
        if not isinstance(expected_count, int) or expected_count != len(result_ids):
            raise ValueError("specialist agent request expected_result_count must match result_ids")
        expected_cursor = f"{request_id}:complete:{len(result_ids)}/{len(result_ids)}"
        if request.get("collection_cursor") != expected_cursor:
            raise ValueError("specialist agent request collection_cursor must be complete and deterministic")
        if request.get("terminal_decision") not in {"accepted", "accepted_no_findings"}:
            raise ValueError("specialist agent request terminal_decision is invalid")
        request_by_id[request_id] = {"profile_ref": profile_ref, "context_hash": context_hash}
        expected_result_ids.update(result_ids)

    accepted_results = []
    ignored_duplicates = []
    seen_accepted_keys: set[str] = set()
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("specialist agent_result_refs entries must be objects")
        result_id = _required_string(result, "result_id")
        request_id = _required_string(result, "request_id")
        profile_ref = _required_string(result, "profile_ref")
        context_hash = _required_string(result, "context_hash")
        idempotency_key = _required_string(result, "idempotency_key")
        terminal_status = _required_string(result, "terminal_status")
        if request_id not in request_by_id:
            raise ValueError("specialist agent result request_id must reference agent_request_refs")
        request = request_by_id[request_id]
        if request["profile_ref"] != profile_ref or request["context_hash"] != context_hash:
            raise ValueError("specialist agent result must match request profile_ref and context_hash")
        if not isinstance(result.get("attempt"), int) or result["attempt"] < 1:
            raise ValueError("specialist agent result attempt must be positive")
        evidence_refs = result.get("evidence_refs")
        if not isinstance(evidence_refs, list) or not evidence_refs:
            raise ValueError("specialist agent result evidence_refs must be non-empty")
        if terminal_status == "accepted":
            accepted_results.append(result)
            if result_id not in expected_result_ids:
                raise ValueError("specialist accepted agent result must be listed by its request")
            finding_id = result_id.split("-agent-result-", 1)[-1]
            finding = finding_by_id.get(finding_id)
            if not finding or finding["profile_id"] != profile_ref:
                raise ValueError("specialist accepted agent result must map to a persisted finding")
            if idempotency_key in seen_accepted_keys:
                raise ValueError("specialist accepted agent result idempotency_key must be unique")
            seen_accepted_keys.add(idempotency_key)
        elif terminal_status == "ignored_duplicate":
            ignored_duplicates.append(result)
            if idempotency_key not in seen_accepted_keys and idempotency_key not in keys:
                raise ValueError("specialist ignored duplicate result must link to an accepted idempotency_key")
            if not result.get("rejection_reason"):
                raise ValueError("specialist ignored duplicate result must record a rejection_reason")
        else:
            raise ValueError("specialist agent result terminal_status is invalid")

    accepted_ids = {item["result_id"] for item in accepted_results}
    if accepted_ids != expected_result_ids:
        raise ValueError("specialist accepted agent results must exactly match request result_ids")
    accepted_keys = [item["idempotency_key"] for item in accepted_results]
    if keys != accepted_keys:
        raise ValueError("specialist agent_result_idempotency_keys must match accepted agent results")
    if status.get("status") != "complete":
        raise ValueError("specialist agent result collection status must be complete")
    if status.get("request_ids") != [item["request_id"] for item in requests]:
        raise ValueError("specialist agent result collection request_ids must match requests")
    if status.get("expected_result_count") != len(expected_result_ids):
        raise ValueError("specialist agent result collection expected_result_count must match requests")
    if status.get("accepted_result_count") != len(accepted_results):
        raise ValueError("specialist agent result collection accepted_result_count must match results")
    if status.get("ignored_duplicate_result_count") != len(ignored_duplicates):
        raise ValueError("specialist agent result collection ignored_duplicate_result_count must match results")
    if status.get("pending_request_ids") != []:
        raise ValueError("specialist agent result collection must not leave pending_request_ids")
    if status.get("relaunch_required") is not False:
        raise ValueError("specialist recovered collection must not require relaunch")
    if status.get("replayed_side_effects") is not False:
        raise ValueError("specialist recovered collection must not replay side effects")
    cursor = f"{state['review_panel_spec']['mode']}:{specialist_artifact_id(state['review_panel_spec']['mode'])}:complete:{len(accepted_results)}/{len(expected_result_ids)}"
    if status.get("collection_cursor") != cursor or state.get("recovery_resume_cursor") != cursor:
        raise ValueError("specialist recovery_resume_cursor must match complete collection cursor")


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


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
