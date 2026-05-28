"""Synthetic command dry-run adapter for C7.

This module deliberately does not register slash routes, observe live traffic,
or create workflows. It only converts a managed user-facing command into the
Converge CLI invocation that a later approved routing layer may use.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
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
        current_owner="workspace-contract exact trigger routes to Converge goal.",
        c7_owner="converge goal",
        retirement_classification="active_converge_managed_route",
        state_root="Converge workflow state for new managed /goal work.",
        delivery_behavior="Draft and confirmation first; visible completion remains bound to the original Telegram delivery route.",
        rollback_switch="Rollback is owner-approved, logged, time-bounded, and scoped; no automatic legacy fallback.",
        transitional_behavior="Exact /goal trigger context is translated to Converge goal with owner session, visible delivery, and state root preserved.",
        final_behavior="New managed /goal work creates Converge goal workflows.",
    ),
    CommandSurface(
        command="/verify",
        current_owner="workspace skill boundary routes exact /verify to Converge verify.",
        c7_owner="converge verify",
        retirement_classification="active_converge_managed_route",
        state_root="Converge workflow state for new managed /verify work.",
        delivery_behavior="One visible audit report through the original delivery route after evidence/report material is reserved.",
        rollback_switch="Rollback is owner-approved, logged, time-bounded, and scoped; no automatic legacy fallback.",
        transitional_behavior="Exact /verify trigger context is translated to Converge verify with owner session, visible delivery, and state root preserved.",
        final_behavior="New managed /verify work records evidence, residuals, report material, and proof in Converge.",
    ),
    CommandSurface(
        command="/conv",
        current_owner="workspace skill boundary routes exact /conv to Converge conv.",
        c7_owner="converge conv",
        retirement_classification="active_converge_managed_route",
        state_root="Converge workflow state for new managed /conv work.",
        delivery_behavior="Round summaries and final report through the original delivery route; material changes need follow-up proof.",
        rollback_switch="Rollback is owner-approved, logged, time-bounded, and scoped; no automatic legacy fallback.",
        transitional_behavior="Exact /conv trigger context is translated to Converge conv with owner session, visible delivery, and state root preserved.",
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
C7_READINESS_PLAN_VERSION = "c7-live-route-readiness"

EXPECTED_ROUTE_CLASSIFICATIONS = {
    "/goal": "active_converge_managed_route",
    "/verify": "active_converge_managed_route",
    "/conv": "active_converge_managed_route",
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
    "cleanup/removal execution requested inside C7.3",
    "legacy deletion requested inside C7.3",
    "legacy file movement or archival requested inside C7.3",
    "legacy skill disable/uninstall requested inside C7.3",
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
    "retired local Work Ledger layer",
    "chat memory",
    "verification-convergence artifacts",
]

EXPECTED_BLOCKED_WITHOUT_APPROVAL = [
    "cleanup/removal execution",
    "Gateway restart",
    "live traffic observation",
    "shadow routing",
    "live route replacement",
    "live route removal",
    "deploy/apply/install",
    "external action",
    "legacy data deletion",
    "legacy file deletion",
    "legacy file movement",
    "legacy file archival",
    "legacy skill disable/uninstall",
    "push/PR/release",
]

EXPECTED_C7_4_ALLOWED_OUTPUTS = [
    "legacy scripts/docs/skills/aliases/state paths inventory",
    "retired/archived/requires-owner-approval classification",
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
    "legacy file deletion",
    "legacy file movement",
    "legacy file archival",
    "legacy skill disable/uninstall",
    "push/PR/release",
]

EXPECTED_C7_4_CLEANUP_CLASSIFICATIONS = [
    "retired",
    "archived",
    "requires-owner-approval",
]

EXPECTED_C7_4_LATER_EXECUTION_REQUIRES = [
    "separate explicit owner approval",
    "exact surface list",
    "retention decision for historical state",
    "rollback switch with expiry and log path",
    "post-change smoke evidence",
]

EXPECTED_LIVE_READINESS_APPROVAL_RECORD_FIELDS = [
    "approval_kind",
    "approval_text",
    "approver",
    "approved_at",
    "approval_ref",
    "exact_route_scope",
    "explicit_exclusions",
    "rollback_expires_at",
    "rollback_log_path",
    "retention_decision_ref",
    "pre_change_smoke_evidence",
    "post_change_smoke_plan",
    "stop_condition_acknowledgement",
]

EXPECTED_LIVE_READINESS_APPROVAL_KIND = "operational_live_route_replacement"
EXPECTED_LIVE_READINESS_APPROVAL_TEXT_TEMPLATE = (
    "I explicitly approve the operational live route replacement for exact commands "
    "/goal, /verify, and /conv to the Converge canonical backend. I do not approve "
    "/converge promotion, cleanup/removal execution, legacy deletion/movement/archive, "
    "deploy/apply/install, external action, push/PR/release, or Gateway restart unless "
    "separately stated with preflight evidence."
)

EXPECTED_LIVE_READINESS_ROUTE_SCOPE = {
    "managed_commands": ["/goal", "/verify", "/conv"],
    "legacy_aliases_excluded_from_primary_route": ["/converge"],
    "forbidden_scope_expansion": ["/plan", "/cplan", "/cgoal", "/cverify", "/cconv", "unlisted slash commands"],
    "implementation_scope_required": True,
    "source_of_truth_after_gate": "converge.workflow",
}

EXPECTED_LIVE_READINESS_GATEWAY_PREFLIGHT = {
    "decision_required": True,
    "preflight_required_if_gateway_restart_or_route_config_reload": True,
    "command": "python3 /Users/moon/.openclaw/workspace/scripts/gateway_restart_preflight.py",
    "required_success_output": "Gateway restart preflight: OK",
    "run_during_readiness_validation": False,
    "restart_authorized_by_readiness": False,
    "explicit_restart_approval_required": True,
    "blocks_if_failed": True,
}

EXPECTED_LIVE_READINESS_RETENTION_DECISION = {
    "required": True,
    "exact_paths_required_before_move_archive_delete": True,
    "allowed_readiness_decisions": ["retain", "archive", "migrate/import", "freeze"],
    "later_cleanup_decisions": ["delete"],
    "deletion_authorized_by_readiness": False,
    "covered_sources": [
        "GoalFlow state",
        "verification-convergence artifacts",
        "chat-derived records",
        "/converge alias history",
    ],
}

EXPECTED_LIVE_READINESS_PRE_CHANGE_SMOKE = [
    "command adapter smoke",
    "recovery/report-proof smoke",
    "C7 route retirement dry-run packet",
    "C7 cleanup/removal dry-run packet",
    "synthetic duplicate visible report guard",
]

EXPECTED_LIVE_READINESS_POST_CHANGE_SMOKE = [
    "/goal route packet reaches Converge only",
    "/verify route packet reaches Converge only",
    "/conv route packet reaches Converge only",
    "legacy route suppressed or rollback-only",
    "reserve-delivery/report-proof/complete-reported remains single-owner",
    "rollback activation and rollback deactivation records are logged",
]

EXPECTED_LIVE_READINESS_STOP_CONDITIONS = [
    "missing exact owner approval record",
    "missing exact route scope",
    "missing implementation route inventory",
    "attempted /converge alias promotion",
    "missing rollback expiry or log path",
    "automatic fallback requested",
    "missing retention decision",
    "Gateway restart preflight decision missing",
    "Gateway restart preflight failed",
    "Gateway restart requested without explicit restart approval",
    "route config reload preflight decision missing",
    "route config reload preflight failed",
    "route config reload requested without explicit reload approval",
    "post-change smoke plan missing",
    "post-change smoke evidence missing before completion",
    "post-change smoke failed",
    "pre-change readiness smoke failed",
    "owner/session/delivery/state-root propagation mismatch",
    "duplicate visible report risk unresolved",
    "unexpected live traffic observed",
    "live routing requested during readiness",
    "live route replacement requested during readiness",
    "live route removal requested during readiness",
    "cleanup/removal execution requested",
    "legacy deletion/movement/archive requested",
    "deploy/apply/install requested during readiness",
    "external action requested",
    "push/PR/release requested",
]

EXPECTED_REQUIRED_PACKET_FIELDS = [
    "input.raw_message",
    "input.command",
    "input.text",
    "route.current_command",
    "route.converge_mode",
    "route.alias_status",
    "route.owner_session_key",
    "route.visible_delivery",
    "route.workflow_id",
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
    "route_retirement_plan.live_route_replacement_readiness_plan",
    "production_route_parity.status",
    "production_route_parity.command_adapter_only_evidence_allowed",
    "production_route_parity.requires_installed_or_fresh_route_context",
    "converge_invocation.argv",
    "blocked_without_approval",
]

EXPECTED_PRODUCTION_ROUTE_PARITY = {
    "status": "not_proven_by_command_adapter",
    "cli_only_evidence_allowed": False,
    "command_adapter_only_evidence_allowed": False,
    "requires_installed_or_fresh_route_context": True,
    "requires_visible_delivery_proof": True,
    "requires_single_route_owner_proof": True,
    "requires_no_duplicate_legacy_report_proof": True,
}

CLEANUP_REMOVAL_SURFACES: tuple[dict[str, Any], ...] = (
    {
        "category": "scripts",
        "surface": "workspace/scripts/goalflow_start_goal.py",
        "classification": "retired",
        "reason": "Exact /goal is Converge-managed. The helper remains only for explicit historical inspection or migration/debug work.",
        "later_action_boundary": "Do not use as an active /goal route owner.",
    },
    {
        "category": "docs",
        "surface": "workspace/AGENTS.md and docs/context/goalflow.md exact /goal policy",
        "classification": "retired",
        "reason": "Workspace policy now routes exact /goal to Converge.",
        "later_action_boundary": "Keep docs aligned with Converge ownership.",
    },
    {
        "category": "skills",
        "surface": "workspace/skills/verification-convergence/SKILL.md",
        "classification": "retired",
        "reason": "Exact /verify and /conv are Converge-managed. The skill remains only as legacy reference material unless explicitly invoked for historical review.",
        "later_action_boundary": "Do not use as active /verify or /conv route owner.",
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
        "classification": "retired",
        "reason": "Work Ledger was locally retired and is not valid for new recovery or completion proof.",
        "later_action_boundary": "Remove leftover local state after explicit cleanup approval.",
    },
    {
        "category": "state paths",
        "surface": "verification-convergence artifacts and chat-derived records",
        "classification": "requires-owner-approval",
        "reason": "Past verification artifacts can support audit history but their exact storage roots are not fixed by C7.4.",
        "later_action_boundary": "Discover exact paths before any retention, archive, move, or delete decision.",
        "exact_path_discovery_required": True,
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
    if not owner_session_key:
        raise ValueError("command dry-run requires non-empty owner_session_key")
    _validate_visible_delivery(delivery)
    if state_root is None:
        raise ValueError("command dry-run requires explicit state_root")
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
            "workflow_id": workflow_id,
            "state_root": str(state_root),
        },
        "adapter_contract": build_adapter_contract(command=f"/{command}", mode=mode),
        "route_retirement_plan": build_route_retirement_plan(),
        "production_route_parity": dict(EXPECTED_PRODUCTION_ROUTE_PARITY),
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
        "required_packet_fields": list(EXPECTED_REQUIRED_PACKET_FIELDS),
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
            "status": "completed",
            "completed_slice": "C7.4 cleanup and removal plan",
            "next_operational_slice": "C7 live route replacement readiness plan",
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
        "live_route_replacement_readiness_plan": build_live_route_replacement_readiness_plan(),
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
        "later_execution_requires": list(EXPECTED_C7_4_LATER_EXECUTION_REQUIRES),
        "prohibited_actions": list(EXPECTED_C7_4_PROHIBITED_ACTIONS),
    }


def build_live_route_replacement_readiness_plan() -> dict[str, Any]:
    return {
        "version": C7_READINESS_PLAN_VERSION,
        "execution_boundary": "readiness_validation_only",
        "readiness_authorizes_live_change": False,
        "owner_approval_record_schema": {
            "required": True,
            "required_fields": list(EXPECTED_LIVE_READINESS_APPROVAL_RECORD_FIELDS),
            "approval_kind": EXPECTED_LIVE_READINESS_APPROVAL_KIND,
            "approval_text_template": EXPECTED_LIVE_READINESS_APPROVAL_TEXT_TEMPLATE,
            "must_bind_exact_commands": ["/goal", "/verify", "/conv"],
            "must_name_explicit_exclusions": ["/converge"],
        },
        "exact_route_scope": deepcopy(EXPECTED_LIVE_READINESS_ROUTE_SCOPE),
        "gateway_restart_preflight": deepcopy(EXPECTED_LIVE_READINESS_GATEWAY_PREFLIGHT),
        "rollback_record": {
            "required": True,
            "automatic_fallback_allowed": False,
            "expires_at_required": True,
            "expires_at_format": "ISO-8601 UTC timestamp",
            "max_duration_hours": 24,
            "log_path_required": True,
            "log_path_template": "/Users/moon/.openclaw/state/converge/route-replacement/rollback-{approval_ref}-{yyyyMMddTHHmmssZ}.jsonl",
            "legacy_route_scope_required": True,
            "activation_and_deactivation_entries_required": True,
            "post_rollback_smoke_required": True,
        },
        "retention_decision": deepcopy(EXPECTED_LIVE_READINESS_RETENTION_DECISION),
        "pre_change_readiness_smoke": list(EXPECTED_LIVE_READINESS_PRE_CHANGE_SMOKE),
        "post_change_smoke_plan": list(EXPECTED_LIVE_READINESS_POST_CHANGE_SMOKE),
        "post_change_smoke_evidence_required_before_completion": True,
        "duplicate_visible_report_guard": {
            "required": True,
            "exactly_one_route_owner_required": True,
            "legacy_handler_must_be_suppressed_or_rollback_only": True,
            "reserve_delivery_required": True,
            "report_proof_required": True,
            "complete_reported_required": True,
            "no_replay_from_goalflow_work_ledger_or_chat_memory": True,
            "no_replay_from_verification_convergence_artifacts": True,
        },
        "stop_conditions": list(EXPECTED_LIVE_READINESS_STOP_CONDITIONS),
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
    production_route_parity = _expect_mapping(packet, "production_route_parity")

    if contract.get("version") != C7_1_CONTRACT_VERSION:
        raise ValueError(f"C7.1 contract version must be {C7_1_CONTRACT_VERSION!r}")
    if contract.get("route_free_flags") != ROUTE_FREE_FLAGS:
        raise ValueError("C7.1 contract route-free flags must match packet route-free flags")
    if production_route_parity != EXPECTED_PRODUCTION_ROUTE_PARITY:
        raise ValueError("Phase 6 production route parity must not be claimed by command-adapter evidence")

    required_packet_fields = contract.get("required_packet_fields")
    if required_packet_fields != EXPECTED_REQUIRED_PACKET_FIELDS:
        raise ValueError("C7.1 contract required_packet_fields must match the exact canonical list")
    for field_path in EXPECTED_REQUIRED_PACKET_FIELDS:
        if not isinstance(field_path, str):
            raise ValueError("C7.1 contract required_packet_fields entries must be strings")
        _get_path(packet, field_path)

    input_fields = _expect_mapping(packet, "input")
    parsed_command, parsed_text = parse_raw_message(str(input_fields.get("raw_message", "")))
    if input_fields.get("command") != f"/{parsed_command}" or input_fields.get("text") != parsed_text:
        raise ValueError("C7.1 input fields must match exact slash parsing of input.raw_message")

    current_command = route.get("current_command")
    if current_command != input_fields.get("command"):
        raise ValueError("C7.1 route.current_command must match input.command")
    if metadata.get("command") != current_command:
        raise ValueError("C7.1 command metadata must match route.current_command")
    if metadata.get("mode") != route.get("converge_mode"):
        raise ValueError("C7.1 command metadata must match route.converge_mode")
    owner_session_key = route.get("owner_session_key")
    if not isinstance(owner_session_key, str) or not owner_session_key:
        raise ValueError("C7.1 route.owner_session_key must be a non-empty string")
    expected_alias_status = "deprecated_alias" if current_command == "/converge" else "primary"
    if route.get("alias_status") != expected_alias_status:
        raise ValueError(f"C7.1 route.alias_status must be {expected_alias_status!r} for {current_command}")
    visible_delivery = route.get("visible_delivery")
    _validate_visible_delivery(visible_delivery)
    state_root = route.get("state_root")
    if not isinstance(state_root, str) or not state_root:
        raise ValueError("C7.1 route.state_root must be a non-empty string")
    workflow_id = route.get("workflow_id")
    if workflow_id is not None and not isinstance(workflow_id, str):
        raise ValueError("C7.1 route.workflow_id must be a string or null")
    invocation = _expect_mapping(packet, "converge_invocation")
    expected_argv = build_converge_argv(
        mode=str(route.get("converge_mode")),
        text=parsed_text,
        owner_session_key=owner_session_key,
        visible_delivery=visible_delivery,
        workflow_id=workflow_id,
        state_root=Path(state_root),
    )
    if invocation.get("argv") != expected_argv:
        raise ValueError("C7.1 converge_invocation.argv must match route metadata and exact slash parsing")

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
    if cleanup_boundary.get("status") != "completed":
        raise ValueError("C7.4 cleanup boundary must mark the C7.4 plan completed")
    if cleanup_boundary.get("completed_slice") != "C7.4 cleanup and removal plan":
        raise ValueError("C7.4 cleanup boundary must identify the completed C7.4 slice")
    if cleanup_boundary.get("next_operational_slice") != "C7 live route replacement readiness plan":
        raise ValueError("C7.4 cleanup boundary must point to live route replacement readiness")
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
    validate_live_route_replacement_readiness_plan(_expect_mapping(route_plan, "live_route_replacement_readiness_plan"))


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
        if surface["category"] == "state paths" and "/" not in surface["surface"] and "*" not in surface["surface"]:
            if surface.get("exact_path_discovery_required") is not True:
                raise ValueError("C7.4 descriptive state-path surfaces must require exact path discovery")

    if cleanup_plan.get("later_execution_requires") != EXPECTED_C7_4_LATER_EXECUTION_REQUIRES:
        raise ValueError("C7.4 cleanup/removal plan must keep exact later execution requirements")


def validate_live_route_replacement_readiness_plan(readiness_plan: dict[str, Any]) -> None:
    if readiness_plan.get("version") != C7_READINESS_PLAN_VERSION:
        raise ValueError(f"C7 live route readiness plan version must be {C7_READINESS_PLAN_VERSION!r}")
    if readiness_plan.get("execution_boundary") != "readiness_validation_only":
        raise ValueError("C7 live route readiness must stay readiness_validation_only")
    if readiness_plan.get("readiness_authorizes_live_change") is not False:
        raise ValueError("C7 live route readiness must not authorize live changes")

    approval_schema = _expect_mapping(readiness_plan, "owner_approval_record_schema")
    if approval_schema.get("required") is not True:
        raise ValueError("C7 live route readiness must require an owner approval record")
    if approval_schema.get("required_fields") != EXPECTED_LIVE_READINESS_APPROVAL_RECORD_FIELDS:
        raise ValueError("C7 live route readiness must keep exact approval record fields")
    if approval_schema.get("approval_kind") != EXPECTED_LIVE_READINESS_APPROVAL_KIND:
        raise ValueError("C7 live route readiness must keep exact approval kind")
    if approval_schema.get("approval_text_template") != EXPECTED_LIVE_READINESS_APPROVAL_TEXT_TEMPLATE:
        raise ValueError("C7 live route readiness must keep exact approval text template")
    if approval_schema.get("must_bind_exact_commands") != ["/goal", "/verify", "/conv"]:
        raise ValueError("C7 live route readiness approval must bind exact managed commands")
    if approval_schema.get("must_name_explicit_exclusions") != ["/converge"]:
        raise ValueError("C7 live route readiness approval must explicitly exclude /converge promotion")

    if _expect_mapping(readiness_plan, "exact_route_scope") != EXPECTED_LIVE_READINESS_ROUTE_SCOPE:
        raise ValueError("C7 live route readiness must keep exact route scope")
    if _expect_mapping(readiness_plan, "gateway_restart_preflight") != EXPECTED_LIVE_READINESS_GATEWAY_PREFLIGHT:
        raise ValueError("C7 live route readiness must keep exact Gateway preflight policy")

    rollback = _expect_mapping(readiness_plan, "rollback_record")
    for key in (
        "required",
        "expires_at_required",
        "log_path_required",
        "legacy_route_scope_required",
        "activation_and_deactivation_entries_required",
        "post_rollback_smoke_required",
    ):
        if rollback.get(key) is not True:
            raise ValueError(f"C7 live route readiness rollback record must set {key}=true")
    if rollback.get("automatic_fallback_allowed") is not False:
        raise ValueError("C7 live route readiness rollback must not allow automatic fallback")
    if rollback.get("expires_at_format") != "ISO-8601 UTC timestamp":
        raise ValueError("C7 live route readiness rollback expiry must use ISO-8601 UTC")
    if rollback.get("max_duration_hours") != 24:
        raise ValueError("C7 live route readiness rollback max duration must be fixed")
    if (
        rollback.get("log_path_template")
        != "/Users/moon/.openclaw/state/converge/route-replacement/rollback-{approval_ref}-{yyyyMMddTHHmmssZ}.jsonl"
    ):
        raise ValueError("C7 live route readiness rollback log path template must be fixed")

    if _expect_mapping(readiness_plan, "retention_decision") != EXPECTED_LIVE_READINESS_RETENTION_DECISION:
        raise ValueError("C7 live route readiness must keep exact retention decision requirements")
    if readiness_plan.get("pre_change_readiness_smoke") != EXPECTED_LIVE_READINESS_PRE_CHANGE_SMOKE:
        raise ValueError("C7 live route readiness must keep exact pre-change smoke requirements")
    if readiness_plan.get("post_change_smoke_plan") != EXPECTED_LIVE_READINESS_POST_CHANGE_SMOKE:
        raise ValueError("C7 live route readiness must keep exact post-change smoke plan")
    if readiness_plan.get("post_change_smoke_evidence_required_before_completion") is not True:
        raise ValueError("C7 live route readiness must require post-change smoke evidence before completion")

    duplicate_guard = _expect_mapping(readiness_plan, "duplicate_visible_report_guard")
    for key in (
        "required",
        "exactly_one_route_owner_required",
        "legacy_handler_must_be_suppressed_or_rollback_only",
        "reserve_delivery_required",
        "report_proof_required",
        "complete_reported_required",
        "no_replay_from_goalflow_work_ledger_or_chat_memory",
        "no_replay_from_verification_convergence_artifacts",
    ):
        if duplicate_guard.get(key) is not True:
            raise ValueError(f"C7 live route readiness duplicate report guard must set {key}=true")
    if readiness_plan.get("stop_conditions") != EXPECTED_LIVE_READINESS_STOP_CONDITIONS:
        raise ValueError("C7 live route readiness must keep exact stop conditions")


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


def _validate_visible_delivery(visible_delivery: Any) -> None:
    if not isinstance(visible_delivery, dict) or not visible_delivery:
        raise ValueError("C7.1 route.visible_delivery must be a non-empty JSON object")
    channel = visible_delivery.get("channel")
    target = visible_delivery.get("target")
    if not isinstance(channel, str) or not channel:
        raise ValueError("C7.1 route.visible_delivery.channel must be a non-empty string")
    if not isinstance(target, str) or not target:
        raise ValueError("C7.1 route.visible_delivery.target must be a non-empty string")


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
