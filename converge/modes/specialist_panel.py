"""Bounded structured specialist finding adapter."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from converge.agents.contracts import (
    DEFAULT_BUDGET_POLICY,
    DEFAULT_TOOL_POLICY,
    SOURCE_NATIVE_AGENT_PANEL,
    SOURCE_RUNNER_PROVIDED_PACKET,
    STATUS_COMPLETED,
    TOOL_SMOKE_NOT_RUN,
    TOOL_SMOKE_PASSED,
    NativeChildResult,
    NativeLaunchRequest,
    build_runner_packet_request_ref,
    build_runner_packet_result_ref,
    stable_hash,
    validate_native_child_result,
)
from converge.artifacts import now_iso


SPECIALIST_REVIEW_RUNNER_REF = "trusted-runner-provided-specialist-findings-v1"
NATIVE_PANEL_RUNNER_REF = "openclaw_session_native_panel-v1"
NATIVE_SESSION_PROOF_KIND = "coordinator_verified_child_tool_smoke_session_and_trajectory_binding"
SEVERITIES = {"p0", "p1", "p2", "p3"}
DECISIONS = {"block", "fix", "accept_risk", "defer", "reject"}
PROFILE_KINDS = {"reviewer", "check", "runner"}
PROFILE_VERSION = "1.0.0"
SPECIALIST_CHECK_PROFILE_ID = "structured-specialist-finding-validator"
BASELINE_PROHIBITED_ACTIONS = [
    "visible_messages",
    "external_actions",
    "service_restart",
    "push_or_pr",
    "target_mutation",
    "workflow_state_mutation",
]
FORBIDDEN_PROFILE_CAPABILITIES = set(BASELINE_PROHIBITED_ACTIONS)
FORBIDDEN_SIDE_EFFECT_FIELDS = {
    "visible_message_sent",
    "external_action_performed",
    "target_mutations",
    "workflow_state_mutations",
    "restarted_services",
    "push_performed",
    "pr_opened",
}
ALLOWED_SOURCE_PROVENANCE = {"runner_provided", "trusted_runner", "native_openclaw_session"}
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
    if any(item["source_provenance"] == "native_openclaw_session" for item in findings):
        raise ValueError("runner-provided specialist findings must not claim native_openclaw_session provenance")
    risk_level = _optional_string(packet, "risk_level", default="medium")
    raw_map, groups = _dedupe_findings(findings)
    arbitration = [_arbitrate_group(group_id, items) for group_id, items in groups.items()]
    accepted_changes = [
        {
            "change_ref": f"accepted-change-{item['group_id']}",
            "finding_ids": item["finding_ids"],
            "fix": item["minimal_fix_or_test"],
            "evidence": item["evidence"],
            "check": item["minimal_fix_or_test"],
            "local_file_edits": item["local_file_edits"],
        }
        for item in arbitration
        if item["decision"] == "fix"
    ]
    follow_up = bool(accepted_changes)
    profile_registry_refs = _build_profile_registry_refs(
        profiles=profiles,
        mode=mode,
        risk_level=risk_level,
    )
    collection_state = _build_agent_collection_state(
        mode=mode,
        target=target,
        artifact_id=artifact_id,
        panel_id=_required_string(packet, "panel_id"),
        profiles=profiles,
        findings=findings,
        profile_registry_refs=profile_registry_refs,
    )
    state = {
        "review_panel_spec": {
            "panel_id": _required_string(packet, "panel_id"),
            "mode": mode,
            "target": target,
            "risk_level": risk_level,
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
        "max_rounds_default": DEFAULT_BUDGET_POLICY["max_rounds_default"],
        "max_rounds": int(packet.get("max_rounds") or DEFAULT_BUDGET_POLICY["max_rounds_default"]),
        "round_index": int(packet.get("round_index") or 1),
        "stop_reason": "structured_specialist_findings_bound",
        "owner_stop_ref": packet.get("owner_stop_ref"),
        "round_stop_proof": (
            "Runner-provided specialist findings were validated, deduped by failure mode, "
            "and arbitrated without allowing specialists to mutate state or send visible reports."
        ),
        "profile_registry_refs": profile_registry_refs,
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


def build_native_specialist_review(
    results: list[NativeChildResult],
    *,
    mode: str,
    target: str,
    artifact_id: str,
    panel_id: str,
) -> dict[str, Any]:
    if not 3 <= len(results) <= 5:
        raise ValueError("native specialist review requires 3-5 child results")
    profiles = [_native_profile(result.profile_ref) for result in results]
    findings = _native_findings(results)
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
    profile_registry_refs = _build_profile_registry_refs(profiles=profiles, mode=mode, risk_level="medium")
    profile_registry_refs.append(_native_runner_profile(mode=mode))
    state = {
        "review_panel_spec": {
            "panel_id": panel_id,
            "mode": mode,
            "target": target,
            "risk_level": "medium",
            "profiles": profiles,
            "runner_ref": NATIVE_PANEL_RUNNER_REF,
            "prohibited_actions": BASELINE_PROHIBITED_ACTIONS,
        },
        "deterministic_check_results": [],
        "agent_finding_refs": findings,
        "raw_finding_to_group_map": raw_map,
        "finding_arbitration": arbitration,
        "accepted_change_refs": accepted_changes,
        "original_target_gate": "within_original_target",
        "delta_regression_gate": "new_material_delta" if accepted_changes else "no_delta",
        "follow_up_round_required": bool(accepted_changes),
        "max_rounds_default": DEFAULT_BUDGET_POLICY["max_rounds_default"],
        "max_rounds": DEFAULT_BUDGET_POLICY["max_rounds_default"],
        "round_index": 1,
        "stop_reason": "native_specialist_panel_collected",
        "owner_stop_ref": None,
        "round_stop_proof": "Native OpenClaw child sessions returned structured findings with explicit session and tool-smoke evidence.",
        "profile_registry_refs": profile_registry_refs,
        **_build_native_agent_collection_state(mode=mode, artifact_id=artifact_id, results=results),
    }
    validate_native_specialist_state(state)
    return {
        "state": state,
        "reviewer_findings": [
            f"{item['finding_id']}: {item['finding']} ({item['severity']}, {item['confidence']:.2f})"
            for item in findings
        ],
        "evidence": {
            "evidence_key": f"{mode}-native-specialist-findings",
            "kind": "native_specialist_findings",
            "summary": f"Collected {len(results)} OpenClaw native child session result(s).",
            "artifact_refs": [artifact_id],
        },
    }


def validate_native_specialist_state(state: dict[str, Any]) -> None:
    _validate_specialist_core_state(state)
    profiles = state["review_panel_spec"].get("profiles")
    if not isinstance(profiles, list) or not 3 <= len(profiles) <= 5:
        raise ValueError("native specialist review panel requires 3-5 profiles")
    _validate_profile_registry_refs(state, profiles=profiles)
    requests = state["agent_request_refs"]
    results = state["agent_result_refs"]
    if not isinstance(requests, list) or len(requests) != len(profiles):
        raise ValueError("native specialist state requires one request per profile")
    if not isinstance(results, list) or len(results) != len(profiles):
        raise ValueError("native specialist state requires one result per profile")
    if any(item.get("execution_source") != SOURCE_NATIVE_AGENT_PANEL for item in requests + results):
        raise ValueError("native specialist state requires native_agent_panel execution_source")
    if any(item.get("satisfies_native_agent_panel") is not True for item in requests + results):
        raise ValueError("native specialist state must satisfy native_agent_panel")
    if any(not item.get("session_key") or not item.get("agent_session_ref") for item in requests + results):
        raise ValueError("native specialist state requires explicit session refs")
    if any(item.get("tool_smoke_status") != TOOL_SMOKE_PASSED for item in requests + results):
        raise ValueError("native specialist state requires passed tool-smoke status")
    if any(not isinstance(item.get("tool_smoke_evidence"), dict) for item in results):
        raise ValueError("native specialist results require tool-smoke evidence")
    for item in requests + results:
        _validate_native_session_store_evidence(item)
    if state["agent_result_collection_status"].get("source") != SOURCE_NATIVE_AGENT_PANEL:
        raise ValueError("native specialist collection status must carry native source")
    status = state["agent_result_collection_status"]
    if status.get("status") != "complete" or status.get("pending_request_ids") != []:
        raise ValueError("native specialist collection must be complete")
    if status.get("accepted_result_count") != len(results):
        raise ValueError("native specialist collection accepted count must match results")


def _validate_specialist_core_state(state: dict[str, Any]) -> None:
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
        "profile_registry_refs",
        "agent_request_refs",
        "agent_result_refs",
        "agent_result_idempotency_keys",
        "agent_result_collection_status",
        "recovery_resume_cursor",
    }
    missing = sorted(required - set(state))
    if missing:
        raise ValueError(f"specialist review state is missing required fields: {missing!r}")
    findings = state["agent_finding_refs"]
    if not isinstance(findings, list) or not findings:
        raise ValueError("specialist agent_finding_refs must be a non-empty array")
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
    if bool(state["accepted_change_refs"]) != bool(state["follow_up_round_required"]):
        raise ValueError("specialist follow_up_round_required must reflect accepted changes")
    if state["max_rounds_default"] != DEFAULT_BUDGET_POLICY["max_rounds_default"]:
        raise ValueError("specialist max_rounds_default must match native adapter budget policy")
    if not isinstance(state["max_rounds"], int) or state["max_rounds"] < 1:
        raise ValueError("specialist max_rounds must be a positive integer")
    if state["max_rounds"] > DEFAULT_BUDGET_POLICY["max_rounds_default"] + DEFAULT_BUDGET_POLICY["max_follow_up_rounds_after_material_change"]:
        raise ValueError("specialist max_rounds must stay within bounded material follow-up budget")
    if state["round_index"] < 1:
        raise ValueError("specialist round_index must be positive")


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
        "profile_registry_refs",
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
    if state["max_rounds_default"] != DEFAULT_BUDGET_POLICY["max_rounds_default"]:
        raise ValueError("specialist max_rounds_default must match native adapter budget policy")
    if not isinstance(state["max_rounds"], int) or state["max_rounds"] < 1:
        raise ValueError("specialist max_rounds must be a positive integer")
    if state["max_rounds"] > DEFAULT_BUDGET_POLICY["max_rounds_default"] + DEFAULT_BUDGET_POLICY["max_follow_up_rounds_after_material_change"]:
        raise ValueError("specialist max_rounds must stay within bounded material follow-up budget")
    if state["round_index"] < 1:
        raise ValueError("specialist round_index must be positive")
    _validate_profile_registry_refs(state, profiles=profiles)
    _validate_agent_collection_state(state, profile_ids=profile_ids, findings=findings)


def write_specialist_artifact(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _build_profile_registry_refs(
    *,
    profiles: list[dict[str, Any]],
    mode: str,
    risk_level: str,
) -> list[dict[str, Any]]:
    registry = [
        _profile_spec(
            profile_id=profile["profile_id"],
            kind="reviewer",
            capabilities=profile["expertise"],
            artifact_types=["verify_report", "conv_round", "goal_child_summary"],
            risk_levels=[risk_level],
            required_context=["target", "artifact_id", "mode", "evidence_refs"],
            prohibited_actions=sorted(set(BASELINE_PROHIBITED_ACTIONS) | set(profile["prohibited_actions"])),
            output_schema={
                "schema_ref": "structured_specialist_finding.v1",
                "required": [
                    "finding_id",
                    "profile_id",
                    "finding",
                    "severity",
                    "evidence",
                    "why_it_matters",
                    "minimal_fix_or_test",
                    "scope_risk",
                    "confidence",
                    "failure_mode",
                    "source_provenance",
                ],
            },
            selection_reason=(
                f"Reviewer profile selected for {mode} structured specialist panel; "
                f"likely failure modes: {', '.join(profile['likely_failure_modes'])}."
            ),
        )
        for profile in profiles
    ]
    registry.append(
        _profile_spec(
            profile_id=SPECIALIST_CHECK_PROFILE_ID,
            kind="check",
            capabilities=["schema_validation", "dedupe", "arbitration", "evidence_anchor_validation"],
            artifact_types=["specialist_findings_packet"],
            risk_levels=[risk_level],
            required_context=["profiles", "findings", "side_effects_performed"],
            prohibited_actions=BASELINE_PROHIBITED_ACTIONS,
            output_schema={
                "schema_ref": "validated_specialist_review_state.v1",
                "required": [
                    "agent_finding_refs",
                    "raw_finding_to_group_map",
                    "finding_arbitration",
                    "accepted_change_refs",
                ],
            },
            selection_reason="Validator profile reused to normalize runner-provided specialist findings into bounded Converge state.",
        )
    )
    registry.append(
        _profile_spec(
            profile_id=SPECIALIST_REVIEW_RUNNER_REF,
            kind="runner",
            capabilities=["runner_packet_ingest", "request_result_correlation", "idempotency_binding"],
            artifact_types=["specialist_findings_artifact"],
            risk_levels=[risk_level],
            required_context=["panel_id", "mode", "target", "artifact_id"],
            prohibited_actions=BASELINE_PROHIBITED_ACTIONS,
            output_schema={
                "schema_ref": "runner_provided_specialist_findings.v1",
                "required": ["profiles", "findings", "deterministic_check_results"],
            },
            selection_reason="Trusted runner profile reused for adapter-safe structured specialist finding ingestion.",
        )
    )
    return registry


def _profile_spec(
    *,
    profile_id: str,
    kind: str,
    capabilities: list[str],
    artifact_types: list[str],
    risk_levels: list[str],
    required_context: list[str],
    prohibited_actions: list[str],
    output_schema: dict[str, Any],
    selection_reason: str,
) -> dict[str, Any]:
    spec = {
        "profile_id": profile_id,
        "version": PROFILE_VERSION,
        "kind": kind,
        "capabilities": capabilities,
        "artifact_types": artifact_types,
        "risk_levels": risk_levels,
        "required_context": required_context,
        "prohibited_actions": prohibited_actions,
        "output_schema": output_schema,
        "selection_reason": selection_reason,
    }
    spec["context_hash"] = _stable_hash(spec)
    return spec


def _validate_profile_registry_refs(state: dict[str, Any], *, profiles: list[dict[str, Any]]) -> None:
    registry = state["profile_registry_refs"]
    if not isinstance(registry, list) or not registry:
        raise ValueError("specialist profile_registry_refs must be a non-empty array")
    registry_ids = [item.get("profile_id") for item in registry if isinstance(item, dict)]
    _require_unique(registry_ids, "specialist profile registry ids")
    registry_by_id = {item["profile_id"]: item for item in registry if isinstance(item, dict)}
    profile_ids = [item["profile_id"] for item in profiles]
    missing_reviewers = sorted(set(profile_ids) - set(registry_by_id))
    if missing_reviewers:
        raise ValueError(f"specialist profile_registry_refs missing reviewer profiles: {missing_reviewers!r}")
    if SPECIALIST_CHECK_PROFILE_ID not in registry_by_id:
        raise ValueError("specialist profile_registry_refs must include check profile")
    runner_ref = state["review_panel_spec"].get("runner_ref")
    if runner_ref not in registry_by_id:
        raise ValueError("specialist profile_registry_refs must include runner profile")
    if registry_by_id[runner_ref].get("kind") != "runner":
        raise ValueError("specialist runner profile kind must be runner")
    if registry_by_id[SPECIALIST_CHECK_PROFILE_ID].get("kind") != "check":
        raise ValueError("specialist check profile kind must be check")
    for profile_id in profile_ids:
        if registry_by_id[profile_id].get("kind") != "reviewer":
            raise ValueError("specialist reviewer profile kind must be reviewer")
    profiles_by_id = {item["profile_id"]: item for item in profiles}
    for item in registry:
        _validate_profile_spec(item)
        if item["kind"] == "reviewer":
            panel_profile = profiles_by_id[item["profile_id"]]
            if item["capabilities"] != panel_profile["expertise"]:
                raise ValueError("specialist reviewer profile capabilities must match panel expertise")
            if not set(panel_profile["prohibited_actions"]).issubset(set(item["prohibited_actions"])):
                raise ValueError("specialist reviewer profile prohibited_actions must include panel prohibitions")
        if not set(BASELINE_PROHIBITED_ACTIONS).issubset(set(item["prohibited_actions"])):
            raise ValueError("specialist profile prohibited_actions must include baseline forbidden actions")
        if set(item["capabilities"]) & FORBIDDEN_PROFILE_CAPABILITIES:
            raise ValueError("specialist profile capabilities must not include forbidden actions")
    allowed_refs = set(registry_by_id)
    for request in state["agent_request_refs"]:
        if request.get("profile_ref") not in allowed_refs:
            raise ValueError("specialist agent request profile_ref must reference profile_registry_refs")
    for result in state["agent_result_refs"]:
        if result.get("profile_ref") not in allowed_refs:
            raise ValueError("specialist agent result profile_ref must reference profile_registry_refs")


def _validate_profile_spec(item: Any) -> None:
    if not isinstance(item, dict):
        raise ValueError("specialist profile registry entry must be an object")
    profile_id = _required_string(item, "profile_id")
    version = _required_string(item, "version")
    kind = _required_string(item, "kind")
    if version != PROFILE_VERSION:
        raise ValueError("specialist profile version is invalid")
    if kind not in PROFILE_KINDS:
        raise ValueError("specialist profile kind is invalid")
    for key in ("capabilities", "artifact_types", "risk_levels", "required_context", "prohibited_actions"):
        values = item.get(key)
        if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value for value in values):
            raise ValueError(f"specialist profile {key} must be a non-empty string array")
    if not isinstance(item.get("output_schema"), dict) or not item["output_schema"].get("schema_ref"):
        raise ValueError("specialist profile output_schema must include schema_ref")
    required_fields = item["output_schema"].get("required")
    if not isinstance(required_fields, list) or any(not isinstance(value, str) or not value for value in required_fields):
        raise ValueError("specialist profile output_schema must include required fields")
    expected_schema_refs = {
        "reviewer": "structured_specialist_finding.v1",
        "check": "validated_specialist_review_state.v1",
        "runner": "runner_provided_specialist_findings.v1",
    }
    expected_required = {
        "reviewer": {
            "finding_id",
            "profile_id",
            "finding",
            "severity",
            "evidence",
            "why_it_matters",
            "minimal_fix_or_test",
            "scope_risk",
            "confidence",
            "failure_mode",
            "source_provenance",
        },
        "check": {
            "agent_finding_refs",
            "raw_finding_to_group_map",
            "finding_arbitration",
            "accepted_change_refs",
        },
        "runner": {"profiles", "findings", "deterministic_check_results"},
    }
    if item["output_schema"]["schema_ref"] != expected_schema_refs[kind]:
        raise ValueError("specialist profile output_schema schema_ref is invalid")
    if set(required_fields) != expected_required[kind]:
        raise ValueError("specialist profile output_schema required fields are invalid")
    _required_string(item, "selection_reason")
    context_hash = _required_string(item, "context_hash")
    comparable = {key: value for key, value in item.items() if key != "context_hash"}
    if context_hash != _stable_hash(comparable):
        raise ValueError(f"specialist profile context_hash is stale for {profile_id}")


def _native_profile(profile_ref: str) -> dict[str, Any]:
    return {
        "profile_id": profile_ref,
        "role": "native OpenClaw specialist reviewer",
        "expertise": ["native_session_review", "evidence_validation", "boundary_review"],
        "likely_failure_modes": ["missing_child_evidence", "false_native_parity", "overbroad_side_effects"],
        "prohibited_actions": BASELINE_PROHIBITED_ACTIONS,
    }


def _native_runner_profile(*, mode: str) -> dict[str, Any]:
    return _profile_spec(
        profile_id=NATIVE_PANEL_RUNNER_REF,
        kind="runner",
        capabilities=["openclaw_session_launch", "tool_smoke_validation", "result_collection"],
        artifact_types=["native_specialist_findings_artifact"],
        risk_levels=["medium"],
        required_context=["panel_id", "mode", "target", "artifact_id"],
        prohibited_actions=BASELINE_PROHIBITED_ACTIONS,
        output_schema={
            "schema_ref": "runner_provided_specialist_findings.v1",
            "required": ["profiles", "findings", "deterministic_check_results"],
        },
        selection_reason=f"Native OpenClaw session runner profile selected for {mode} specialist panel collection.",
    )


def _native_findings(results: list[NativeChildResult]) -> list[dict[str, Any]]:
    profiles = [_native_profile(result.profile_ref) for result in results]
    findings: list[dict[str, Any]] = []
    for result in results:
        validate_native_child_result(result.as_dict())
        if result.status != STATUS_COMPLETED:
            raise ValueError("native specialist result must be completed")
        if result.tool_smoke_status != TOOL_SMOKE_PASSED or result.satisfies_native_agent_panel is not True:
            raise ValueError("native specialist result must pass tool-smoke and satisfy native panel parity")
        _validate_native_session_store_evidence(result.as_dict())
        if not result.findings:
            raise ValueError("native specialist result must include at least one structured finding")
        for finding in result.findings:
            item = dict(finding)
            item["profile_id"] = result.profile_ref
            item["source_provenance"] = "native_openclaw_session"
            item.setdefault("evidence", f"agent_session_ref:{result.agent_session_ref}")
            item["finding_id"] = stable_hash({"request_id": result.request_id, "finding": item})
            findings.append(item)
    return _findings(findings, profiles=profiles)


def _build_native_agent_collection_state(
    *,
    mode: str,
    artifact_id: str,
    results: list[NativeChildResult],
) -> dict[str, Any]:
    request_refs = []
    result_refs = []
    for result in results:
        request_refs.append(
            {
                "request_id": result.request_id,
                "profile_ref": result.profile_ref,
                "context_hash": result.context_hash,
                "status": STATUS_COMPLETED,
                "expected_result_count": 1,
                "result_ids": [result.result_id],
                "session_key": result.session_key,
                "agent_session_ref": result.agent_session_ref,
                "target_refs": [dict(item) for item in result.target_refs],
                "tool_smoke_status": result.tool_smoke_status,
                "tool_smoke_evidence": result.tool_smoke_evidence,
                "tool_policy": dict(DEFAULT_TOOL_POLICY),
                "requested_at": result.started_at,
                "lease": {"status": "completed", "source": SOURCE_NATIVE_AGENT_PANEL},
                "collection_cursor": f"{result.request_id}:complete:1/1",
                "terminal_decision": "accepted",
                "execution_source": SOURCE_NATIVE_AGENT_PANEL,
                "satisfies_native_agent_panel": True,
                "advisory_only": False,
            }
        )
        result_refs.append(
            {
                "result_id": result.result_id,
                "request_id": result.request_id,
                "profile_ref": result.profile_ref,
                "context_hash": result.context_hash,
                "idempotency_key": stable_hash(
                    {
                        "request_id": result.request_id,
                        "result_id": result.result_id,
                        "session_key": result.session_key,
                    }
                ),
                "status": STATUS_COMPLETED,
                "evidence_refs": [result.agent_session_ref, artifact_id],
                "session_key": result.session_key,
                "agent_session_ref": result.agent_session_ref,
                "tool_smoke_status": result.tool_smoke_status,
                "tool_smoke_evidence": result.tool_smoke_evidence,
                "attempt": 1,
                "received_at": result.completed_at,
                "terminal_status": "accepted",
                "acceptance_reason": "validated native OpenClaw child session result",
                "rejection_reason": None,
                "execution_source": SOURCE_NATIVE_AGENT_PANEL,
                "satisfies_native_agent_panel": True,
                "advisory_only": False,
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
            "source": SOURCE_NATIVE_AGENT_PANEL,
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


def build_native_agent_pending_collection_state(
    *,
    mode: str,
    artifact_id: str,
    requests: list[NativeLaunchRequest],
    status: str = "launch_requested",
    blocked_reason: str | None = None,
    blocked_request_id: str | None = None,
    blocked_session_key: str | None = None,
    resume_next_action: str | None = None,
) -> dict[str, Any]:
    """Build non-terminal native request/collection state before results exist."""

    if not requests:
        raise ValueError("native pending collection state requires at least one request")
    request_refs = []
    now = now_iso()
    for request in requests:
        request_id = request.request_id or request.idempotency_key
        request_refs.append(
            {
                "request_id": request_id,
                "profile_ref": request.profile_ref,
                "context_hash": request.context_hash,
                "status": status,
                "expected_result_count": 1,
                "result_ids": [],
                "session_key": request.session_key,
                "agent_session_ref": request.session_key,
                "target_refs": [dict(item) for item in request.target_refs],
                "tool_smoke_status": TOOL_SMOKE_NOT_RUN,
                "tool_smoke_evidence": None,
                "tool_policy": dict(request.tool_policy),
                "requested_at": now,
                "lease": {"status": status, "source": SOURCE_NATIVE_AGENT_PANEL},
                "collection_cursor": f"{request_id}:{status}:0/1",
                "terminal_decision": None,
                "execution_source": SOURCE_NATIVE_AGENT_PANEL,
                "satisfies_native_agent_panel": False,
                "advisory_only": False,
            }
        )
    pending_request_ids = [item["request_id"] for item in request_refs]
    collection_cursor = f"{mode}:{artifact_id}:{status}:0/{len(request_refs)}"
    collection_status = {
        "status": status,
        "source": SOURCE_NATIVE_AGENT_PANEL,
        "request_ids": pending_request_ids,
        "expected_result_count": len(request_refs),
        "accepted_result_count": 0,
        "ignored_duplicate_result_count": 0,
        "pending_request_ids": pending_request_ids,
        "collection_cursor": collection_cursor,
        "terminal_decision": "waiting_for_native_panel",
        "relaunch_required": False,
        "replayed_side_effects": False,
    }
    state: dict[str, Any] = {
        "agent_request_refs": request_refs,
        "agent_result_refs": [],
        "agent_result_idempotency_keys": [],
        "agent_result_collection_status": collection_status,
        "recovery_resume_cursor": collection_cursor,
        "native_panel_launch_status": status,
    }
    if blocked_reason:
        blocked_status = _native_blocked_collection_status(blocked_reason)
        state["blocked_reason"] = blocked_reason
        state["blocked_request_id"] = blocked_request_id
        state["blocked_session_key"] = blocked_session_key
        state["pending_request_ids"] = pending_request_ids
        state["resume_next_action"] = resume_next_action or _native_blocked_resume_next_action(blocked_reason)
        state["agent_result_collection_status"] = {
            **collection_status,
            "status": blocked_status,
            "blocked_reason": blocked_reason,
            "blocked_request_id": blocked_request_id,
            "blocked_session_key": blocked_session_key,
            "terminal_decision": f"blocked_{blocked_status}",
            "relaunch_required": True,
            "collection_cursor": f"{mode}:{artifact_id}:{blocked_status}:0/{len(request_refs)}",
        }
        state["native_panel_launch_status"] = blocked_status
        state["recovery_resume_cursor"] = state["agent_result_collection_status"]["collection_cursor"]
    return state


def _native_blocked_collection_status(blocked_reason: str) -> str:
    if blocked_reason in {"subagent_capacity_exhausted", "subagent_spawn_timeout"}:
        return "waiting_subagent_capacity"
    return "blocked_native_panel_contract"


def _native_blocked_resume_next_action(blocked_reason: str) -> str:
    if _native_blocked_collection_status(blocked_reason) == "waiting_subagent_capacity":
        return "retry_native_panel_after_capacity_available"
    return "inspect_native_panel_contract_failure"


def _validate_native_session_store_evidence(item: dict[str, Any]) -> None:
    evidence = item.get("tool_smoke_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("native specialist result requires tool-smoke evidence")
    if evidence.get("kind") != NATIVE_SESSION_PROOF_KIND:
        raise ValueError("native specialist evidence requires coordinator session-store proof kind")
    if evidence.get("session_key") != item.get("session_key"):
        raise ValueError("native specialist evidence session_key must match native item")
    if evidence.get("agent_session_ref") != item.get("agent_session_ref"):
        raise ValueError("native specialist evidence agent_session_ref must match native item")
    if evidence.get("child_read_action") not in {"read_files", "read_artifacts"}:
        raise ValueError("native specialist evidence requires child_read_action")
    if evidence.get("child_status_action") != "shell_status":
        raise ValueError("native specialist evidence requires child_status_action=shell_status")
    read_manifest = evidence.get("target_ref_read_manifest")
    if not isinstance(read_manifest, dict):
        raise ValueError("native specialist evidence requires target_ref_read_manifest")
    if not isinstance(read_manifest.get("required_count"), int):
        raise ValueError("native specialist target_ref_read_manifest requires required_count")
    if read_manifest["required_count"] > 0 and not isinstance(read_manifest.get("read_target_refs"), list):
        raise ValueError("native specialist target_ref_read_manifest requires read_target_refs")
    if read_manifest.get("missing") not in ([], None):
        raise ValueError("native specialist target_ref_read_manifest must not have missing refs")
    if evidence.get("policy_enforcement") != "prompt_and_coordinator_validation_only":
        raise ValueError("native specialist evidence requires explicit policy_enforcement scope")
    if evidence.get("lifecycle_model") != "synchronous_serial_openclaw_agent_child_process":
        raise ValueError("native specialist evidence requires explicit lifecycle_model")
    action_binding = evidence.get("trajectory_action_binding")
    if not isinstance(action_binding, dict):
        raise ValueError("native specialist evidence requires trajectory_action_binding")
    if action_binding.get("read_action_bound_by_tool_names") is not True:
        raise ValueError("native specialist evidence requires read_action tool-name binding")
    if action_binding.get("status_action_bound_by_tool_names") is not True:
        raise ValueError("native specialist evidence requires status_action tool-name binding")
    proof = evidence.get("session_store_proof")
    if not isinstance(proof, dict):
        raise ValueError("native specialist evidence requires session_store_proof")
    if proof.get("session_key") != item.get("session_key"):
        raise ValueError("native specialist session_store_proof.session_key must match native item")
    session_id = proof.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("native specialist session_store_proof requires session_id")
    trajectory_proof = evidence.get("trajectory_proof")
    if not isinstance(trajectory_proof, dict):
        raise ValueError("native specialist evidence requires trajectory_proof")
    if trajectory_proof.get("session_key") != item.get("session_key"):
        raise ValueError("native specialist trajectory_proof.session_key must match native item")
    if not isinstance(trajectory_proof.get("output_dir"), str) or not trajectory_proof["output_dir"].strip():
        raise ValueError("native specialist trajectory_proof requires output_dir")
    if not isinstance(trajectory_proof.get("tool_call_count"), int) or trajectory_proof["tool_call_count"] < 1:
        raise ValueError("native specialist trajectory_proof requires tool_call_count")
    if not isinstance(trajectory_proof.get("tool_result_count"), int) or trajectory_proof["tool_result_count"] < 1:
        raise ValueError("native specialist trajectory_proof requires tool_result_count")


def _build_agent_collection_state(
    *,
    mode: str,
    target: str,
    artifact_id: str,
    panel_id: str,
    profiles: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    profile_registry_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    findings_by_profile: dict[str, list[dict[str, Any]]] = {item["profile_id"]: [] for item in profiles}
    for finding in findings:
        findings_by_profile[finding["profile_id"]].append(finding)
    profile_registry_by_id = {item["profile_id"]: item for item in profile_registry_refs}

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
                "profile_spec_hash": profile_registry_by_id[profile_ref]["context_hash"],
                "target": target,
            }
        )
        profile_findings = findings_by_profile[profile_ref]
        result_ids = []
        for finding in profile_findings:
            result_id = f"{mode}-agent-result-{finding['finding_id']}"
            result_ids.append(result_id)
            result_ref = build_runner_packet_result_ref(
                result_id=result_id,
                request_id=request_id,
                profile_ref=profile_ref,
                context_hash=context_hash,
                finding_id=finding["finding_id"],
                source_provenance=finding["source_provenance"],
                evidence_refs=[finding["evidence"], artifact_id],
            )
            result_refs.append(
                {
                    **result_ref,
                    "attempt": 1,
                    "received_at": "runner_packet",
                    "terminal_status": "accepted",
                    "acceptance_reason": "validated runner-provided structured specialist result",
                    "rejection_reason": None,
                }
            )
        request_ref = build_runner_packet_request_ref(
            request_id=request_id,
            profile_ref=profile_ref,
            context_hash=context_hash,
            expected_result_count=len(profile_findings),
            result_ids=result_ids,
        )
        request_refs.append(
            {
                **request_ref,
                "requested_at": "runner_packet",
                "lease": {"status": "completed", "source": SOURCE_RUNNER_PROVIDED_PACKET},
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
    registry_by_id = {item["profile_id"]: item for item in state["profile_registry_refs"] if isinstance(item, dict)}
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
        expected_context_hash = _stable_hash(
            {
                "artifact_id": specialist_artifact_id(state["review_panel_spec"]["mode"]),
                "mode": state["review_panel_spec"]["mode"],
                "panel_id": state["review_panel_spec"]["panel_id"],
                "profile_ref": profile_ref,
                "profile_spec_hash": registry_by_id[profile_ref]["context_hash"],
                "target": state["review_panel_spec"]["target"],
            }
        )
        if context_hash != expected_context_hash:
            raise ValueError("specialist agent request context_hash must include selected profile spec hash")
        if request.get("status") != "completed":
            raise ValueError("specialist completed structured packet must not leave agent requests pending")
        if request.get("execution_source") != SOURCE_RUNNER_PROVIDED_PACKET:
            raise ValueError("specialist runner packet request must carry runner_provided_packet execution_source")
        if request.get("satisfies_native_agent_panel") is not False or request.get("advisory_only") is not True:
            raise ValueError("specialist runner packet request must be advisory and not satisfy native agent panels")
        if request.get("tool_smoke_status") != "not_applicable":
            raise ValueError("specialist runner packet request tool_smoke_status must be not_applicable")
        if request.get("session_key") is not None or request.get("agent_session_ref") is not None:
            raise ValueError("specialist runner packet request must not invent native session refs")
        lease = request.get("lease")
        if not isinstance(lease, dict) or lease.get("status") != "completed" or lease.get("source") != SOURCE_RUNNER_PROVIDED_PACKET:
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
        if result.get("execution_source") != SOURCE_RUNNER_PROVIDED_PACKET:
            raise ValueError("specialist runner packet result must carry runner_provided_packet execution_source")
        if result.get("satisfies_native_agent_panel") is not False or result.get("advisory_only") is not True:
            raise ValueError("specialist runner packet result must be advisory and not satisfy native agent panels")
        if result.get("tool_smoke_status") != "not_applicable":
            raise ValueError("specialist runner packet result tool_smoke_status must be not_applicable")
        if result.get("session_key") is not None or result.get("agent_session_ref") is not None:
            raise ValueError("specialist runner packet result must not invent native session refs")
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
                "local_file_edits": _local_file_edits(item.get("local_file_edits")),
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
        "local_file_edits": list(strongest.get("local_file_edits") or []),
    }


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"specialist {key} must be a non-empty string")
    return value


def _local_file_edits(raw: Any) -> list[dict[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("specialist local_file_edits must be an array")
    edits = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("specialist local_file_edits entries must be objects")
        path = item.get("path")
        old = item.get("old")
        new = item.get("new")
        if not isinstance(path, str) or not path or path.startswith("/") or ".." in Path(path).parts:
            raise ValueError("specialist local_file_edits path must be a safe relative path")
        if not isinstance(old, str) or old == "":
            raise ValueError("specialist local_file_edits old must be a non-empty string")
        if not isinstance(new, str) or new == old:
            raise ValueError("specialist local_file_edits new must be a changed string")
        edits.append({"path": path, "old": old, "new": new})
    return edits


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
    _local_file_edits(finding.get("local_file_edits"))


def _validate_evidence_anchor(evidence: str) -> None:
    if not evidence.startswith(("events.jsonl ", "worklog.md#", "verify_state.", "conv_state.", "agent_session_ref:")):
        raise ValueError("specialist finding evidence must reference a concrete state field or event anchor")


def _validate_source_provenance(source_provenance: str) -> None:
    if source_provenance not in ALLOWED_SOURCE_PROVENANCE:
        raise ValueError("specialist finding source_provenance must be runner_provided, trusted_runner, or native_openclaw_session")


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
