"""Phase 6 route parity evidence validation.

This module validates recorded evidence only. It does not send messages,
restart Gateway, install packages, change routes, or create workflows.
"""

from __future__ import annotations

from typing import Any


MANAGED_COMMANDS = ["/goal", "/verify", "/conv"]
FORBIDDEN_SIDE_EFFECTS = [
    "gateway_restart_performed",
    "route_change_performed",
    "deploy_or_install_performed",
    "external_action_performed",
    "cleanup_or_legacy_removal_performed",
]
REQUIRED_PROOF_FIELDS = [
    "command",
    "converge_mode",
    "fresh_route_context",
    "owner_session_key",
    "visible_delivery",
    "state_root",
    "workflow_id",
    "route_owner",
    "route_owner_refs",
    "legacy_handler_invoked",
    "report_proof_ref",
    "complete_reported_ref",
    "duplicate_visible_report_detected",
]
ALLOWED_COMMAND_OWNER_FIELDS = {"owner_session_key", "route_owner", "route_owner_refs"}
FORBIDDEN_COMMAND_SIDE_EFFECT_FIELDS = [
    "gateway_restart_performed",
    "route_change_performed",
    "deploy_or_install_performed",
    "external_action_performed",
    "cleanup_or_legacy_removal_performed",
    "side_effects_performed",
    "live_route_changed",
    "legacy_data_deleted",
]

EXPECTED_MODES = {
    "/goal": "goal",
    "/verify": "verify",
    "/conv": "conv",
}


def validate_phase6_route_parity_evidence(evidence: dict[str, Any], *, expected_state_root: str | None = None) -> dict[str, Any]:
    if not isinstance(evidence, dict):
        raise ValueError("Phase 6 route parity evidence must be a JSON object")
    source_type = evidence.get("evidence_source")
    if source_type in {"command-dry-run", "cli-only", "command-adapter"}:
        raise ValueError("Phase 6 production route parity cannot be proven by CLI-only or command-adapter evidence")
    if source_type != "fresh-route":
        raise ValueError("Phase 6 route parity evidence_source must be fresh-route")
    if evidence.get("proof_level") != "fresh_route_evidence_bundle":
        raise ValueError("Phase 6 route parity evidence proof_level must be fresh_route_evidence_bundle")
    for field in FORBIDDEN_SIDE_EFFECTS:
        if evidence.get(field) is not False:
            raise ValueError(f"Phase 6 route parity evidence must set {field}=false")

    commands = evidence.get("commands")
    if not isinstance(commands, dict):
        raise ValueError("Phase 6 route parity evidence must include commands object")
    if set(commands) != set(MANAGED_COMMANDS):
        raise ValueError("Phase 6 route parity evidence must cover exactly /goal, /verify, and /conv")

    common_owner_session: str | None = None
    common_visible_delivery: dict[str, Any] | None = None
    common_state_root: str | None = None
    workflow_ids: set[str] = set()
    for command in MANAGED_COMMANDS:
        record = commands.get(command)
        if not isinstance(record, dict):
            raise ValueError(f"Phase 6 route parity evidence for {command} must be an object")
        for field in REQUIRED_PROOF_FIELDS:
            if field not in record:
                raise ValueError(f"Phase 6 route parity evidence for {command} missing {field}")
        unexpected_owner_fields = sorted(
            field for field in record if "owner" in field and field not in ALLOWED_COMMAND_OWNER_FIELDS
        )
        if unexpected_owner_fields:
            raise ValueError(f"Phase 6 route parity evidence for {command} has unexpected route owner field")
        for field in FORBIDDEN_COMMAND_SIDE_EFFECT_FIELDS:
            if field in record and record[field] not in (False, None, [], {}):
                raise ValueError(f"Phase 6 route parity evidence for {command} must not include command side effects")
        if record["command"] != command:
            raise ValueError(f"Phase 6 route parity evidence for {command} must bind command exactly")
        if record["converge_mode"] != EXPECTED_MODES[command]:
            raise ValueError(f"Phase 6 route parity evidence for {command} must bind the expected Converge mode")
        if record["fresh_route_context"] is not True:
            raise ValueError(f"Phase 6 route parity evidence for {command} must come from a fresh route context")
        if record["route_owner"] != "converge":
            raise ValueError(f"Phase 6 route parity evidence for {command} must have exactly one Converge route owner")
        if record["route_owner_refs"] != ["converge"]:
            raise ValueError(f"Phase 6 route parity evidence for {command} must prove exactly one route owner ref")
        if record["legacy_handler_invoked"] is not False:
            raise ValueError(f"Phase 6 route parity evidence for {command} must prove legacy handler was not invoked")
        if record["duplicate_visible_report_detected"] is not False:
            raise ValueError(f"Phase 6 route parity evidence for {command} must prove no duplicate visible report")
        for string_field in ("owner_session_key", "state_root", "workflow_id", "report_proof_ref", "complete_reported_ref"):
            value = record.get(string_field)
            if not isinstance(value, str) or not value:
                raise ValueError(f"Phase 6 route parity evidence for {command} has invalid {string_field}")
        workflow_id = record["workflow_id"]
        for proof_field in ("report_proof_ref", "complete_reported_ref"):
            proof_ref = record[proof_field]
            if command not in proof_ref or workflow_id not in proof_ref:
                raise ValueError(f"Phase 6 route parity evidence for {command} must bind {proof_field} to command and workflow")
        visible_delivery = record.get("visible_delivery")
        if not isinstance(visible_delivery, dict) or not visible_delivery.get("channel") or not visible_delivery.get("target"):
            raise ValueError(f"Phase 6 route parity evidence for {command} has invalid visible_delivery")
        if common_owner_session is None:
            common_owner_session = record["owner_session_key"]
        elif common_owner_session != record["owner_session_key"]:
            raise ValueError("Phase 6 route parity evidence must keep one owner session across managed commands")
        if common_visible_delivery is None:
            common_visible_delivery = visible_delivery
        elif common_visible_delivery != visible_delivery:
            raise ValueError("Phase 6 route parity evidence must keep one visible delivery target across managed commands")
        if common_state_root is None:
            common_state_root = record["state_root"]
        elif common_state_root != record["state_root"]:
            raise ValueError("Phase 6 route parity evidence must keep one state root across managed commands")
        if expected_state_root is not None and record["state_root"] != expected_state_root:
            raise ValueError("Phase 6 route parity evidence must match the invoked state root")
        if record["workflow_id"] in workflow_ids:
            raise ValueError("Phase 6 route parity evidence must use distinct workflow ids per managed command")
        workflow_ids.add(record["workflow_id"])

    aliases = evidence.get("aliases", {})
    if not isinstance(aliases, dict):
        raise ValueError("Phase 6 route parity evidence aliases must be an object")
    converge_alias = aliases.get("/converge")
    if not isinstance(converge_alias, dict):
        raise ValueError("Phase 6 route parity evidence must include /converge alias boundary")
    if converge_alias.get("promoted") is not False:
        raise ValueError("Phase 6 route parity evidence must prove /converge was not promoted")
    if converge_alias.get("primary_route_owner") not in {None, "", "none"}:
        raise ValueError("Phase 6 route parity evidence must not assign /converge a primary route owner")

    parity_matrix = evidence.get("retained_skill_parity_matrix")
    if not isinstance(parity_matrix, dict):
        raise ValueError("Phase 6 route parity evidence must include retained_skill_parity_matrix")
    for mode in ("audit", "repair", "improve"):
        checks = parity_matrix.get(mode)
        if not isinstance(checks, list) or not checks:
            raise ValueError(f"Phase 6 retained skill parity matrix missing {mode} mappings")
        for item in checks:
            if not isinstance(item, dict):
                raise ValueError(f"Phase 6 retained skill parity matrix item for {mode} must be an object")
            if not item.get("requirement") or not item.get("converge_evidence_ref"):
                raise ValueError(f"Phase 6 retained skill parity matrix item for {mode} needs requirement and evidence ref")

    return {
        "ok": True,
        "phase": "phase6_production_route_parity",
        "proof_level": "fresh_route_evidence_bundle",
        "managed_commands": MANAGED_COMMANDS,
        "production_route_parity_proven": True,
    }
