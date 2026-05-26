"""Synthetic command dry-run adapter for C7.

This module deliberately does not register slash routes, observe live traffic,
or create workflows. It only converts a managed user-facing command into the
Converge CLI invocation that a later approved routing layer may use.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


COMMAND_RE = re.compile(r"^/(?P<command>goal|verify|conv|converge)(?:\s+(?P<text>[\s\S]*))?$")


@dataclass(frozen=True)
class CommandSurface:
    command: str
    current_owner: str
    c7_owner: str
    retirement_classification: str
    state_root: str
    delivery_behavior: str
    rollback_switch: str
    transitional_behavior: str
    final_behavior: str

    def as_dict(self) -> dict[str, str]:
        return {
            "command": self.command,
            "current_owner": self.current_owner,
            "c7_owner": self.c7_owner,
            "retirement_classification": self.retirement_classification,
            "state_root": self.state_root,
            "delivery_behavior": self.delivery_behavior,
            "rollback_switch": self.rollback_switch,
            "transitional_behavior": self.transitional_behavior,
            "final_behavior": self.final_behavior,
        }


COMMAND_INVENTORY: tuple[CommandSurface, ...] = (
    CommandSurface(
        command="/goal",
        current_owner="GoalFlow exact trigger plus scripts/goalflow_start_goal.py draft intake.",
        c7_owner="converge goal",
        retirement_classification="replace_default_after_owner_approved_live_routing",
        state_root="Legacy GoalFlow state during C7.0; future Converge workflow state after approved live routing.",
        delivery_behavior="Draft and confirmation first; visible completion remains bound to the original Telegram delivery route.",
        rollback_switch="Keep existing /goal route until owner-approved replacement; disable C7 adapter route to fall back.",
        transitional_behavior="Synthetic dry-run only; preserves draft/confirmation gates without live route changes.",
        final_behavior="New managed /goal work creates Converge goal workflows after separate live-routing approval.",
    ),
    CommandSurface(
        command="/verify",
        current_owner="verification-convergence skill audit path.",
        c7_owner="converge verify",
        retirement_classification="replace_default_after_owner_approved_live_routing",
        state_root="Legacy verification-convergence artifacts during C7.0; future Converge workflow state after approved live routing.",
        delivery_behavior="One visible audit report through the original delivery route after evidence/report material is reserved.",
        rollback_switch="Keep existing /verify handler until owner-approved replacement; disable C7 adapter route to fall back.",
        transitional_behavior="Synthetic dry-run only; no live observation, duplicate report, or shadow routing.",
        final_behavior="New managed /verify work records evidence, residuals, report material, and proof in Converge.",
    ),
    CommandSurface(
        command="/conv",
        current_owner="verification-convergence skill repair/improvement path.",
        c7_owner="converge conv",
        retirement_classification="replace_default_after_owner_approved_live_routing",
        state_root="Legacy verification-convergence artifacts during C7.0; future Converge workflow state after approved live routing.",
        delivery_behavior="Round summaries and final report through the original delivery route; material changes need follow-up proof.",
        rollback_switch="Keep existing /conv handler until owner-approved replacement; disable C7 adapter route to fall back.",
        transitional_behavior="Synthetic dry-run only; verifies round metadata route shape without live replacement.",
        final_behavior="New managed /conv work records convergence rounds and recovery cursor state in Converge.",
    ),
    CommandSurface(
        command="/converge",
        current_owner="legacy alias for /conv.",
        c7_owner="temporary alias to converge conv, or retirement message",
        retirement_classification="retire_or_keep_explicit_alias_message_only",
        state_root="No independent state root; alias must reuse /conv state or retire.",
        delivery_behavior="No independent delivery contract; alias maps to /conv dry-run and is marked deprecated.",
        rollback_switch="Retire alias or keep explicit message only; never make it the primary route.",
        transitional_behavior="Synthetic dry-run marks the alias deprecated and maps it to conv without promoting it.",
        final_behavior="Retired, or replaced with a clear /conv/Converge message.",
    ),
)

C7_1_CONTRACT_VERSION = "c7.1"
C7_3_PLAN_VERSION = "c7.3"
C7_4_PLAN_VERSION = "c7.4"

EXPECTED_ROUTE_CLASSIFICATIONS = {
    "/goal": "replace_default_after_owner_approved_live_routing",
    "/verify": "replace_default_after_owner_approved_live_routing",
    "/conv": "replace_default_after_owner_approved_live_routing",
    "/converge": "retire_or_keep_explicit_alias_message_only",
}

EXPECTED_APPROVAL_EVIDENCE = [
    "C7.3 dry-run packet",
    "command adapter smoke",
    "recovery/report-proof smoke",
    "rollback switch plan",
]

EXPECTED_APPROVAL_STOP_CONDITIONS = [
    "missing exact owner approval",
    "missing rollback expiry or log path",
    "live route change requested inside C7.3",
    "legacy state deletion requested inside C7.3",
]

EXPECTED_CONVERGE_SOURCE_OF_TRUTH = [
    "workflow state",
    "checkpoint cursor",
    "delivery reservation",
    "report-proof",
    "complete-reported",
]

EXPECTED_LEGACY_NON_AUTHORITATIVE_SOURCES = [
    "GoalFlow",
    "Work Ledger",
    "chat memory",
    "verification-convergence artifacts",
]

EXPECTED_BLOCKED_WITHOUT_APPROVAL = [
    "Gateway restart",
    "live traffic observation",
    "shadow routing",
    "live route replacement",
    "deploy/apply/install",
    "external action",
    "legacy data deletion",
    "push/PR/release",
]

EXPECTED_C7_4_ALLOWED_OUTPUTS = [
    "legacy scripts/docs/skills/aliases/state paths inventory",
    "retired/archived/still-active-for-non-Converge/requires-owner-approval classification",
    "cleanup/removal plan for later approved task",
    "verification criteria for later approved task",
]

EXPECTED_C7_4_PROHIBITED_ACTIONS = [
    "cleanup/removal execution",
    "Gateway restart",
    "live traffic observation",
    "shadow routing",
    "live route replacement",
    "live route removal",
    "deploy/apply/install",
    "external action",
    "legacy data deletion",
    "legacy file movement",
    "legacy file archival",
    "legacy skill disable/uninstall",
    "push/PR/release",
]

EXPECTED_C7_4_CLEANUP_CLASSIFICATIONS = [
    "retired",
    "archived",
    "still-active-for-non-Converge",
    "requires-owner-approval",
]

CLEANUP_REMOVAL_SURFACES: tuple[dict[str, Any], ...] = (
    {
        "category": "scripts",
        "surface": "workspace/scripts/goalflow_start_goal.py",
        "classification": "requires-owner-approval",
        "reason": "It remains the owner of exact /goal draft intake until a separate live-routing task replaces /goal with Converge.",
        "later_action_boundary": "Retire or narrow the script only after owner-approved live route replacement and migration evidence.",
    },
    {
        "category": "docs",
        "surface": "workspace/AGENTS.md and docs/context/goalflow.md exact /goal policy",
        "classification": "requires-owner-approval",
        "reason": "Workspace policy still defines the active /goal intake contract and cannot be rewritten by a plan-only cleanup slice.",
        "later_action_boundary": "Update policy only in the separately approved route replacement operation that actually changes the live owner.",
    },
    {
        "category": "skills",
        "surface": "workspace/skills/verification-convergence/SKILL.md",
        "classification": "still-active-for-non-Converge",
        "reason": "The skill may remain useful for non-Converge audits while managed /verify and /conv route ownership is migrated.",
        "later_action_boundary": "Remove managed-command ownership only after Converge handles live /verify and /conv with owner-approved routing proof.",
    },
    {
        "category": "aliases",
        "surface": "/converge legacy alias",
        "classification": "retired",
        "reason": "The alias has no independent state or delivery contract and must not become the primary product route.",
        "later_action_boundary": "Execute alias removal or replacement wording only in a later owner-approved live route removal task.",
    },
    {
        "category": "state paths",
        "surface": "workspace/state/goalflow/*",
        "classification": "archived",
        "reason": "Historical GoalFlow records remain readable, but they are not authoritative for Converge-owned workflow recovery or completion.",
        "later_action_boundary": "Archive, move, or delete records only after explicit retention approval and migration checks.",
    },
    {
        "category": "state paths",
        "surface": "workspace/state/work-ledger/*",
        "classification": "still-active-for-non-Converge",
        "reason": "Work Ledger remains valid for outer session recovery and non-Converge work, but not as Converge workflow source of truth.",
        "later_action_boundary": "Do not remove or narrow until non-Converge ledger use is separately inventoried and approved.",
    },
    {
        "category": "state paths",
        "surface": "verification-convergence artifacts and chat-derived records",
        "classification": "archived",
        "reason": "Past verification artifacts can support audit history but cannot drive Converge recovery, report-proof, or complete-reported state.",
        "later_action_boundary": "Retain as historical evidence unless a later retention task explicitly approves cleanup.",
    },
)

ROUTE_FREE_FLAGS = {
    "dry_run": True,
    "live_route_changed": False,
    "live_traffic_observed": False,
    "shadow_routing_enabled": False,
    "workflow_created": False,
    "external_action_performed": False,
    "gateway_restart_required": False,
    "legacy_data_deleted": False,
}


def inventory() -> list[dict[str, str]]:
    return [surface.as_dict() for surface in COMMAND_INVENTORY]


def build_dry_run_packet(
    *,
    raw_message: str,
    owner_session_key: str = "",
    visible_delivery: dict[str, Any] | None = None,
    workflow_id: str | None = None,
    state_root: Path | None = None,
) -> dict[str, Any]:
    command, text = parse_raw_message(raw_message)
    mode = "conv" if command == "converge" else command
    delivery = visible_delivery or {}
    converge_argv = build_converge_argv(
        mode=mode,
        text=text,
        owner_session_key=owner_session_key,
        visible_delivery=delivery,
        workflow_id=workflow_id,
        state_root=state_root,
    )
    packet: dict[str, Any] = {
        "schema_version": "converge.command_dry_run.v0.1",
        "ok": True,
        **ROUTE_FREE_FLAGS,
        "input": {
            "raw_message": raw_message,
            "command": f"/{command}",
            "text": text,
        },
        "route": {
            "current_command": f"/{command}",
            "converge_mode": mode,
            "alias_status": "deprecated_alias" if command == "converge" else "primary",
            "owner_session_key": owner_session_key,
            "visible_delivery": delivery,
            "state_root": str(state_root) if state_root else None,
        },
        "adapter_contract": build_adapter_contract(command=f"/{command}", mode=mode),
        "route_retirement_plan": build_route_retirement_plan(),
        "converge_invocation": {
            "argv": converge_argv,
            "display": " ".join(_shell_quote(part) for part in converge_argv),
        },
        "inventory": inventory(),
        "blocked_without_approval": list(EXPECTED_BLOCKED_WITHOUT_APPROVAL),
    }
    validate_dry_run_packet(packet)
    return packet


def build_adapter_contract(*, command: str, mode: str) -> dict[str, Any]:
    return {
        "version": C7_1_CONTRACT_VERSION,
        "route_free_flags": dict(ROUTE_FREE_FLAGS),
        "required_packet_fields": [
            "input.raw_message",
            "input.command",
            "input.text",
            "route.current_command",
            "route.converge_mode",
            "route.alias_status",
            "route.owner_session_key",
            "route.visible_delivery",
            "route.state_root",
            "adapter_contract.command_metadata",
            "route_retirement_plan.version",
            "route_retirement_plan.scope",
            "route_retirement_plan.route_classification",
            "route_retirement_plan.approval_gate",
            "route_retirement_plan.rollback_switch",
            "route_retirement_plan.logging_proof",
            "route_retirement_plan.cleanup_removal_boundary",
            "route_retirement_plan.cleanup_removal_plan",
            "converge_invocation.argv",
            "blocked_without_approval",
        ],
        "shared_metadata": {
            "state_root_field": "route.state_root",
            "delivery_field": "route.visible_delivery",
            "rollback_field": "route_retirement_plan.rollback_switch",
        },
        "command_metadata": build_command_metadata(command=command, mode=mode),
    }


def build_route_retirement_plan() -> dict[str, Any]:
    return {
        "version": C7_3_PLAN_VERSION,
        "scope": {
            "managed_commands": ["/goal", "/verify", "/conv"],
            "legacy_aliases": ["/converge"],
            "source_of_truth_after_gate": "converge.workflow",
            "execution_boundary": "plan_and_dry_run_only",
        },
        "route_classification": [
            {
                "command": item.command,
                "classification": item.retirement_classification,
                "current_owner": item.current_owner,
                "c7_owner": item.c7_owner,
            }
            for item in COMMAND_INVENTORY
        ],
        "approval_gate": {
            "required": True,
            "owner_approval_required": True,
            "approval_ref_required": True,
            "exact_route_scope_required": True,
            "evidence_required": list(EXPECTED_APPROVAL_EVIDENCE),
            "stop_conditions": list(EXPECTED_APPROVAL_STOP_CONDITIONS),
        },
        "rollback_switch": {
            "required": True,
            "explicit_owner_approval_required": True,
            "logged": True,
            "log_path_required": True,
            "time_bounded": True,
            "expires_at_required": True,
            "legacy_route_scope_required": True,
            "automatic_fallback_allowed": False,
            "valid_only_for": "separately approved live-routing operational task",
        },
        "logging_proof": {
            "dry_run_packet_required": True,
            "route_plan_record_required": True,
            "approval_record_required_before_live_change": True,
            "rollback_record_required_before_live_change": True,
            "converge_source_of_truth": list(EXPECTED_CONVERGE_SOURCE_OF_TRUTH),
            "legacy_sources_not_authoritative_for_converge_work": list(EXPECTED_LEGACY_NON_AUTHORITATIVE_SOURCES),
        },
        "cleanup_removal_boundary": {
            "next_slice": "C7.4 cleanup and removal plan",
            "plan_only": True,
            "classification_only": True,
            "execution_allowed": False,
            "allowed_outputs": list(EXPECTED_C7_4_ALLOWED_OUTPUTS),
            "prohibited_actions": list(EXPECTED_C7_4_PROHIBITED_ACTIONS),
            "legacy_deletion_allowed": False,
            "live_route_removal_allowed": False,
            "separate_owner_approval_required": True,
        },
        "cleanup_removal_plan": build_cleanup_removal_plan(),
    }


def build_cleanup_removal_plan() -> dict[str, Any]:
    return {
        "version": C7_4_PLAN_VERSION,
        "execution_boundary": "classification_and_plan_only",
        "classification_values": list(EXPECTED_C7_4_CLEANUP_CLASSIFICATIONS),
        "surfaces": [dict(surface) for surface in CLEANUP_REMOVAL_SURFACES],
        "source_of_truth_boundary": {
            "converge_authoritative_for_converge_work": list(EXPECTED_CONVERGE_SOURCE_OF_TRUTH),
            "legacy_not_authoritative_for_converge_work": list(EXPECTED_LEGACY_NON_AUTHORITATIVE_SOURCES),
        },
        "later_execution_requires": [
            "separate explicit owner approval",
            "exact surface list",
            "retention decision for historical state",
            "rollback switch with expiry and log path",
            "post-change smoke evidence",
        ],
        "prohibited_actions": list(EXPECTED_C7_4_PROHIBITED_ACTIONS),
    }


def build_command_metadata(*, command: str, mode: str) -> dict[str, Any]:
    if command == "/goal":
        return {
            "command": command,
            "mode": mode,
            "intent": "goal_intake",
            "draft_confirmation": {
                "draft_required": True,
                "confirmation_required": True,
                "accepted_plan_metadata_required": True,
            },
            "required_fields": [
                "objective",
                "non_goals",
                "success_criteria",
                "approval_boundaries",
            ],
        }
    if command == "/verify":
        return {
            "command": command,
            "mode": mode,
            "intent": "audit",
            "audit": {
                "default_intent": True,
                "target_required": True,
                "evidence_capture_required": True,
                "residuals_required": True,
            },
            "required_fields": [
                "target",
                "check_plan",
                "evidence",
                "verdict",
                "residuals",
            ],
        }
    if command in {"/conv", "/converge"}:
        return {
            "command": command,
            "mode": mode,
            "intent": "repair_or_improve",
            "rounds": {
                "round_metadata_required": True,
                "original_target_gate_required": True,
                "delta_gate_required": True,
                "material_change_followup_required": True,
            },
            "required_fields": [
                "original_target",
                "round_index",
                "findings",
                "accepted_changes",
                "stop_reason",
            ],
        }
    raise ValueError(f"unsupported command metadata for {command}")


def validate_dry_run_packet(packet: dict[str, Any]) -> None:
    for field, expected in ROUTE_FREE_FLAGS.items():
        if packet.get(field) is not expected:
            raise ValueError(f"C7.1 dry-run packet must keep {field}={expected!r}")
    if packet.get("blocked_without_approval") != EXPECTED_BLOCKED_WITHOUT_APPROVAL:
        raise ValueError("C7.3 dry-run packet must keep exact blocked_without_approval actions")

    route = _expect_mapping(packet, "route")
    contract = _expect_mapping(packet, "adapter_contract")
    metadata = _expect_mapping(contract, "command_metadata")
    route_plan = _expect_mapping(packet, "route_retirement_plan")

    if contract.get("version") != C7_1_CONTRACT_VERSION:
        raise ValueError(f"C7.1 contract version must be {C7_1_CONTRACT_VERSION!r}")
    if contract.get("route_free_flags") != ROUTE_FREE_FLAGS:
        raise ValueError("C7.1 contract route-free flags must match packet route-free flags")

    required_packet_fields = contract.get("required_packet_fields")
    if not isinstance(required_packet_fields, list) or not required_packet_fields:
        raise ValueError("C7.1 contract required_packet_fields must be a non-empty list")
    for field_path in required_packet_fields:
        if not isinstance(field_path, str):
            raise ValueError("C7.1 contract required_packet_fields entries must be strings")
        _get_path(packet, field_path)

    current_command = route.get("current_command")
    if metadata.get("command") != current_command:
        raise ValueError("C7.1 command metadata must match route.current_command")
    if metadata.get("mode") != route.get("converge_mode"):
        raise ValueError("C7.1 command metadata must match route.converge_mode")
    owner_session_key = route.get("owner_session_key")
    if not isinstance(owner_session_key, str):
        raise ValueError("C7.1 route.owner_session_key must be a string")
    expected_alias_status = "deprecated_alias" if current_command == "/converge" else "primary"
    if route.get("alias_status") != expected_alias_status:
        raise ValueError(f"C7.1 route.alias_status must be {expected_alias_status!r} for {current_command}")
    if not isinstance(route.get("visible_delivery"), dict):
        raise ValueError("C7.1 route.visible_delivery must be a JSON object")
    if "state_root" not in route:
        raise ValueError("C7.1 route.state_root field is required")

    shared = _expect_mapping(contract, "shared_metadata")
    if shared.get("state_root_field") != "route.state_root":
        raise ValueError("C7.1 contract must fix route.state_root as the state-root field")
    if shared.get("delivery_field") != "route.visible_delivery":
        raise ValueError("C7.1 contract must fix route.visible_delivery as the delivery field")
    if shared.get("rollback_field") != "route_retirement_plan.rollback_switch":
        raise ValueError("C7.3 contract must fix route_retirement_plan.rollback_switch as the rollback field")

    required_metadata_keys = {
        "/goal": "draft_confirmation",
        "/verify": "audit",
        "/conv": "rounds",
        "/converge": "rounds",
    }
    expected_key = required_metadata_keys.get(str(current_command))
    if not expected_key or expected_key not in metadata:
        raise ValueError(f"C7.1 command metadata missing {expected_key!r} for {current_command}")

    inventory_items = packet.get("inventory")
    if not isinstance(inventory_items, list) or not inventory_items:
        raise ValueError("C7.1 inventory must be a non-empty list")
    for item in inventory_items:
        if not isinstance(item, dict) or not item.get("rollback_switch"):
            raise ValueError("C7.1 inventory entries must include rollback_switch")
        if not item.get("retirement_classification"):
            raise ValueError("C7.3 inventory entries must include retirement_classification")

    validate_route_retirement_plan(route_plan)


def validate_route_retirement_plan(route_plan: dict[str, Any]) -> None:
    if route_plan.get("version") != C7_3_PLAN_VERSION:
        raise ValueError(f"C7.3 route retirement plan version must be {C7_3_PLAN_VERSION!r}")
    scope = _expect_mapping(route_plan, "scope")
    if scope.get("managed_commands") != ["/goal", "/verify", "/conv"]:
        raise ValueError("C7.3 route retirement plan must scope managed /goal, /verify, and /conv")
    if scope.get("legacy_aliases") != ["/converge"]:
        raise ValueError("C7.3 route retirement plan must classify /converge as legacy alias")
    if scope.get("source_of_truth_after_gate") != "converge.workflow":
        raise ValueError("C7.3 route retirement plan must keep Converge workflow as source of truth")
    if scope.get("execution_boundary") != "plan_and_dry_run_only":
        raise ValueError("C7.3 route retirement plan must stay plan_and_dry_run_only")

    classification = route_plan.get("route_classification")
    if not isinstance(classification, list):
        raise ValueError("C7.3 route retirement plan must classify all managed commands and aliases")
    observed_classifications: dict[str, str] = {}
    expected_owners = {item.command: item for item in COMMAND_INVENTORY}
    for item in classification:
        if not isinstance(item, dict):
            continue
        command = item.get("command")
        observed_classifications[command] = item.get("classification")
        expected_owner = expected_owners.get(str(command))
        if expected_owner is None:
            continue
        if item.get("current_owner") != expected_owner.current_owner or item.get("c7_owner") != expected_owner.c7_owner:
            raise ValueError("C7.3 route retirement plan must keep exact current_owner and c7_owner metadata")
    if observed_classifications != EXPECTED_ROUTE_CLASSIFICATIONS:
        raise ValueError("C7.3 route retirement plan must classify all managed commands and aliases exactly")

    approval_gate = _expect_mapping(route_plan, "approval_gate")
    if approval_gate.get("required") is not True or approval_gate.get("owner_approval_required") is not True:
        raise ValueError("C7.3 approval gate must require explicit owner approval")
    if approval_gate.get("approval_ref_required") is not True:
        raise ValueError("C7.3 approval gate must require approval reference")
    if approval_gate.get("exact_route_scope_required") is not True:
        raise ValueError("C7.3 approval gate must require exact route scope")
    if approval_gate.get("evidence_required") != EXPECTED_APPROVAL_EVIDENCE:
        raise ValueError("C7.3 approval gate must define exact evidence requirements")
    if approval_gate.get("stop_conditions") != EXPECTED_APPROVAL_STOP_CONDITIONS:
        raise ValueError("C7.3 approval gate must define exact stop conditions")

    rollback = _expect_mapping(route_plan, "rollback_switch")
    for key in (
        "required",
        "explicit_owner_approval_required",
        "logged",
        "log_path_required",
        "time_bounded",
        "expires_at_required",
        "legacy_route_scope_required",
    ):
        if rollback.get(key) is not True:
            raise ValueError(f"C7.3 rollback switch must set {key}=true")
    if rollback.get("automatic_fallback_allowed") is not False:
        raise ValueError("C7.3 rollback switch must never allow automatic fallback")
    if rollback.get("valid_only_for") != "separately approved live-routing operational task":
        raise ValueError("C7.3 rollback switch must be valid only for separately approved live routing")

    logging_proof = _expect_mapping(route_plan, "logging_proof")
    for key in (
        "dry_run_packet_required",
        "route_plan_record_required",
        "approval_record_required_before_live_change",
        "rollback_record_required_before_live_change",
    ):
        if logging_proof.get(key) is not True:
            raise ValueError(f"C7.3 logging/proof must set {key}=true")
    if logging_proof.get("converge_source_of_truth") != EXPECTED_CONVERGE_SOURCE_OF_TRUTH:
        raise ValueError("C7.3 logging/proof must preserve exact Converge source-of-truth authorities")
    if logging_proof.get("legacy_sources_not_authoritative_for_converge_work") != EXPECTED_LEGACY_NON_AUTHORITATIVE_SOURCES:
        raise ValueError("C7.3 logging/proof must keep all legacy sources non-authoritative for Converge work")

    cleanup_boundary = _expect_mapping(route_plan, "cleanup_removal_boundary")
    if cleanup_boundary.get("next_slice") != "C7.4 cleanup and removal plan":
        raise ValueError("C7.3 cleanup boundary must point to C7.4")
    for key in ("plan_only", "classification_only", "separate_owner_approval_required"):
        if cleanup_boundary.get(key) is not True:
            raise ValueError(f"C7.3 cleanup boundary must set {key}=true")
    if cleanup_boundary.get("execution_allowed") is not False:
        raise ValueError("C7.3 cleanup boundary must not allow execution")
    if cleanup_boundary.get("allowed_outputs") != EXPECTED_C7_4_ALLOWED_OUTPUTS:
        raise ValueError("C7.3 cleanup boundary must define exact C7.4 allowed outputs")
    if cleanup_boundary.get("prohibited_actions") != EXPECTED_C7_4_PROHIBITED_ACTIONS:
        raise ValueError("C7.3 cleanup boundary must define exact C7.4 prohibited actions")
    for key in ("legacy_deletion_allowed", "live_route_removal_allowed"):
        if cleanup_boundary.get(key) is not False:
            raise ValueError(f"C7.3 cleanup boundary must set {key}=false")

    validate_cleanup_removal_plan(_expect_mapping(route_plan, "cleanup_removal_plan"))


def validate_cleanup_removal_plan(cleanup_plan: dict[str, Any]) -> None:
    if cleanup_plan.get("version") != C7_4_PLAN_VERSION:
        raise ValueError(f"C7.4 cleanup/removal plan version must be {C7_4_PLAN_VERSION!r}")
    if cleanup_plan.get("execution_boundary") != "classification_and_plan_only":
        raise ValueError("C7.4 cleanup/removal plan must stay classification_and_plan_only")
    if cleanup_plan.get("classification_values") != EXPECTED_C7_4_CLEANUP_CLASSIFICATIONS:
        raise ValueError("C7.4 cleanup/removal plan must define exact classification values")
    if cleanup_plan.get("prohibited_actions") != EXPECTED_C7_4_PROHIBITED_ACTIONS:
        raise ValueError("C7.4 cleanup/removal plan must keep exact prohibited actions")

    source_boundary = _expect_mapping(cleanup_plan, "source_of_truth_boundary")
    if source_boundary.get("converge_authoritative_for_converge_work") != EXPECTED_CONVERGE_SOURCE_OF_TRUTH:
        raise ValueError("C7.4 cleanup/removal plan must preserve Converge source-of-truth authorities")
    if source_boundary.get("legacy_not_authoritative_for_converge_work") != EXPECTED_LEGACY_NON_AUTHORITATIVE_SOURCES:
        raise ValueError("C7.4 cleanup/removal plan must keep legacy sources non-authoritative for Converge work")

    surfaces = cleanup_plan.get("surfaces")
    expected_surfaces = [dict(surface) for surface in CLEANUP_REMOVAL_SURFACES]
    if surfaces != expected_surfaces:
        raise ValueError("C7.4 cleanup/removal plan must keep the exact legacy surface inventory")
    observed_categories = {surface["category"] for surface in surfaces}
    if observed_categories != {"scripts", "docs", "skills", "aliases", "state paths"}:
        raise ValueError("C7.4 cleanup/removal plan must cover scripts, docs, skills, aliases, and state paths")
    for surface in surfaces:
        if surface["classification"] not in EXPECTED_C7_4_CLEANUP_CLASSIFICATIONS:
            raise ValueError("C7.4 cleanup/removal plan has an invalid classification")
        if not surface.get("reason") or not surface.get("later_action_boundary"):
            raise ValueError("C7.4 cleanup/removal surfaces must include reason and later_action_boundary")

    later_execution_requires = cleanup_plan.get("later_execution_requires")
    if not isinstance(later_execution_requires, list) or "separate explicit owner approval" not in later_execution_requires:
        raise ValueError("C7.4 cleanup/removal execution must require separate explicit owner approval")


def _expect_mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"C7.1 packet field {key!r} must be an object")
    return value


def _get_path(parent: dict[str, Any], dotted_path: str) -> Any:
    current: Any = parent
    for part in dotted_path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"C7.1 required packet field missing: {dotted_path}")
        current = current[part]
    return current


def parse_raw_message(raw_message: str) -> tuple[str, str]:
    match = COMMAND_RE.match(raw_message)
    if not match:
        raise ValueError("raw message must start with /goal, /verify, /conv, or /converge")
    command = match.group("command")
    text = (match.group("text") or "").strip()
    if not text:
        raise ValueError(f"/{command} dry-run requires non-empty text")
    return command, text


def build_converge_argv(
    *,
    mode: str,
    text: str,
    owner_session_key: str,
    visible_delivery: dict[str, Any],
    workflow_id: str | None,
    state_root: Path | None,
) -> list[str]:
    argv = ["converge"]
    if state_root is not None:
        argv.extend(["--state-root", str(state_root)])
    argv.extend([mode, "--text", text])
    if workflow_id:
        argv.extend(["--workflow-id", workflow_id])
    if owner_session_key:
        argv.extend(["--owner-session-key", owner_session_key])
    if visible_delivery:
        argv.extend(["--visible-delivery", json.dumps(visible_delivery, ensure_ascii=False, sort_keys=True)])
    return argv


def _shell_quote(value: str) -> str:
    if not value:
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_./:=@+-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"
