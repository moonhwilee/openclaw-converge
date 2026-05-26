#!/usr/bin/env python3
"""Smoke coverage for C7 synthetic command dry-run adapter."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

try:
    from smoke_helpers import VISIBLE_DELIVERY, assert_true, run, run_fail
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import VISIBLE_DELIVERY, assert_true, run, run_fail

from converge.command_adapter import validate_dry_run_packet


OWNER_SESSION = "session:test"


def dry_run(state_root: Path, raw_message: str, *extra: str) -> dict:
    return run(
        "command-dry-run",
        "--raw-message",
        raw_message,
        "--owner-session-key",
        OWNER_SESSION,
        "--visible-delivery",
        VISIBLE_DELIVERY,
        *extra,
        state_root=state_root,
    )


def dry_run_fail(state_root: Path, raw_message: str, *extra: str) -> dict:
    return run_fail(
        "command-dry-run",
        "--raw-message",
        raw_message,
        "--owner-session-key",
        OWNER_SESSION,
        "--visible-delivery",
        VISIBLE_DELIVERY,
        *extra,
        state_root=state_root,
    )


def assert_dry_run_maps_command_without_state_creation(state_root: Path, raw_message: str, expected_mode: str) -> None:
    result = dry_run(state_root, raw_message, "--workflow-id", "synthetic-c7")
    assert_true(result["ok"] is True, "command-dry-run should return ok")
    assert_true(result["dry_run"] is True, "command-dry-run should mark dry_run")
    assert_true(result["workflow_created"] is False, "command-dry-run should not create workflows")
    assert_true(result["live_route_changed"] is False, "command-dry-run should not change live routes")
    assert_true(result["live_traffic_observed"] is False, "command-dry-run should not observe live traffic")
    assert_true(result["shadow_routing_enabled"] is False, "command-dry-run should not enable shadow routing")
    assert_true(result["external_action_performed"] is False, "command-dry-run should not perform external action")
    assert_true(result["gateway_restart_required"] is False, "command-dry-run should not require Gateway restart")
    assert_true(result["legacy_data_deleted"] is False, "command-dry-run should not delete legacy data")
    assert_true(result["route"]["converge_mode"] == expected_mode, "command should map to expected Converge mode")
    assert_true(result["route"]["alias_status"] == "primary", "primary commands should not be marked as aliases")
    assert_true(result["route"]["owner_session_key"] == OWNER_SESSION, "owner session should be preserved")
    assert_true(result["route"]["visible_delivery"] == json.loads(VISIBLE_DELIVERY), "visible delivery should be preserved")
    assert_true(result["route"]["state_root"] == str(state_root), "state root should be exposed in route metadata")
    assert_true(result["adapter_contract"]["version"] == "c7.1", "adapter contract should expose C7.1 version")
    assert_true(
        result["adapter_contract"]["shared_metadata"]["state_root_field"] == "route.state_root",
        "C7.1 contract should fix state-root field",
    )
    assert_true(
        result["adapter_contract"]["shared_metadata"]["delivery_field"] == "route.visible_delivery",
        "C7.1 contract should fix delivery field",
    )
    assert_true(
        result["adapter_contract"]["shared_metadata"]["rollback_field"] == "route_retirement_plan.rollback_switch",
        "C7.3 contract should fix structured rollback field",
    )
    assert_true(result["converge_invocation"]["argv"][0] == "converge", "dry-run should produce converge invocation")
    assert_true("--state-root" in result["converge_invocation"]["argv"], "invocation should include state root")
    assert_true(expected_mode in result["converge_invocation"]["argv"], "invocation should include target mode")
    assert_true(not (state_root / "workflows").exists(), "dry-run should not materialize workflow state")


def assert_inventory_covers_managed_commands(state_root: Path) -> None:
    result = dry_run(state_root, "/goal Implement dry-run adapter")
    commands = {item["command"] for item in result["inventory"]}
    assert_true(commands == {"/goal", "/verify", "/conv", "/converge"}, "inventory should cover managed commands")
    owners = {item["command"]: item["c7_owner"] for item in result["inventory"]}
    assert_true(owners["/goal"] == "converge goal", "inventory should assign /goal to converge goal")
    assert_true(owners["/verify"] == "converge verify", "inventory should assign /verify to converge verify")
    assert_true(owners["/conv"] == "converge conv", "inventory should assign /conv to converge conv")
    assert_true("temporary alias" in owners["/converge"], "inventory should not promote /converge as primary")
    required_fields = {"state_root", "delivery_behavior", "rollback_switch"}
    for item in result["inventory"]:
        assert_true(
            required_fields.issubset(item),
            f"{item['command']} inventory should expose routing ownership fields",
        )
        assert_true(item["retirement_classification"], f"{item['command']} should document retirement classification")
        assert_true(item["state_root"], f"{item['command']} should document state root")
        assert_true(item["delivery_behavior"], f"{item['command']} should document delivery behavior")
        assert_true(item["rollback_switch"], f"{item['command']} should document rollback switch")


def assert_c7_1_command_metadata_contract(state_root: Path) -> None:
    goal = dry_run(state_root, "/goal Implement accepted plan")
    goal_metadata = goal["adapter_contract"]["command_metadata"]
    assert_true(goal_metadata["intent"] == "goal_intake", "/goal should expose goal intake intent")
    assert_true(
        goal_metadata["draft_confirmation"]["draft_required"] is True,
        "/goal should require draft metadata",
    )
    assert_true(
        goal_metadata["draft_confirmation"]["confirmation_required"] is True,
        "/goal should require confirmation metadata",
    )
    assert_true("approval_boundaries" in goal_metadata["required_fields"], "/goal should require approval boundaries")

    verify = dry_run(state_root, "/verify Audit C7 docs")
    verify_metadata = verify["adapter_contract"]["command_metadata"]
    assert_true(verify_metadata["intent"] == "audit", "/verify should expose audit intent")
    assert_true(verify_metadata["audit"]["default_intent"] is True, "/verify should default to audit intent")
    assert_true(verify_metadata["audit"]["evidence_capture_required"] is True, "/verify should require evidence capture")
    assert_true("residuals" in verify_metadata["required_fields"], "/verify should require residual fields")

    conv = dry_run(state_root, "/conv Improve C7 plan")
    conv_metadata = conv["adapter_contract"]["command_metadata"]
    assert_true(conv_metadata["intent"] == "repair_or_improve", "/conv should expose repair/improve intent")
    assert_true(conv_metadata["rounds"]["round_metadata_required"] is True, "/conv should require round metadata")
    assert_true(conv_metadata["rounds"]["original_target_gate_required"] is True, "/conv should require original-target gate")
    assert_true(conv_metadata["rounds"]["delta_gate_required"] is True, "/conv should require delta gate")
    assert_true("round_index" in conv_metadata["required_fields"], "/conv should require round index")


def assert_c7_3_route_retirement_plan_contract(state_root: Path) -> None:
    result = dry_run(state_root, "/goal Implement route retirement plan")
    route_plan = result["route_retirement_plan"]
    expected_prohibited_actions = [
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
    expected_sources = {
        "workspace/scripts/goalflow_start_goal.py": "requires-owner-approval",
        "workspace/AGENTS.md and docs/context/goalflow.md exact /goal policy": "requires-owner-approval",
        "workspace/skills/verification-convergence/SKILL.md": "still-active-for-non-Converge",
        "/converge legacy alias": "retired",
        "workspace/state/goalflow/*": "archived",
        "workspace/state/work-ledger/*": "still-active-for-non-Converge",
        "verification-convergence artifacts and chat-derived records": "requires-owner-approval",
    }
    expected_authoritative = [
        "workflow state",
        "checkpoint cursor",
        "delivery reservation",
        "report-proof",
        "complete-reported",
    ]
    expected_non_authoritative = [
        "GoalFlow",
        "Work Ledger",
        "chat memory",
        "verification-convergence artifacts",
    ]
    expected_later_execution_requires = [
        "separate explicit owner approval",
        "exact surface list",
        "retention decision for historical state",
        "rollback switch with expiry and log path",
        "post-change smoke evidence",
    ]
    assert_true(route_plan["version"] == "c7.3", "route retirement plan should expose C7.3 version")
    assert_true(
        result["blocked_without_approval"] == expected_prohibited_actions,
        "top-level blocked_without_approval should include the full C7.4 prohibited-action set",
    )
    required_packet_fields = result["adapter_contract"]["required_packet_fields"]
    assert_true("route_retirement_plan.version" in required_packet_fields, "C7.3 version should be a required field")
    assert_true("route_retirement_plan.scope" in required_packet_fields, "C7.3 scope should be a required field")
    assert_true(
        "route_retirement_plan.route_classification" in required_packet_fields,
        "C7.3 route classification should be a required field",
    )
    assert_true(
        "route_retirement_plan.cleanup_removal_boundary" in required_packet_fields,
        "C7.4 cleanup boundary should be a required field",
    )
    assert_true(
        "route_retirement_plan.cleanup_removal_plan" in required_packet_fields,
        "C7.4 cleanup/removal plan should be a required field",
    )
    assert_true(
        "route_retirement_plan.live_route_replacement_readiness_plan" in required_packet_fields,
        "C7 live route readiness plan should be a required field",
    )
    assert_true(route_plan["scope"]["managed_commands"] == ["/goal", "/verify", "/conv"], "C7.3 should scope managed commands")
    assert_true(route_plan["scope"]["legacy_aliases"] == ["/converge"], "C7.3 should classify /converge as legacy alias")
    assert_true(
        route_plan["scope"]["source_of_truth_after_gate"] == "converge.workflow",
        "C7.3 should preserve Converge workflow as source of truth",
    )
    assert_true(
        route_plan["scope"]["execution_boundary"] == "plan_and_dry_run_only",
        "C7.3 should stay plan/dry-run only",
    )

    classification = {item["command"]: item["classification"] for item in route_plan["route_classification"]}
    assert_true(
        classification["/goal"] == "replace_default_after_owner_approved_live_routing",
        "/goal should be planned for owner-approved default replacement",
    )
    assert_true(
        classification["/converge"] == "retire_or_keep_explicit_alias_message_only",
        "/converge should not become a primary product route",
    )

    approval_gate = route_plan["approval_gate"]
    assert_true(approval_gate["owner_approval_required"] is True, "C7.3 should require owner approval")
    assert_true(approval_gate["approval_ref_required"] is True, "C7.3 should require approval reference")
    assert_true(approval_gate["exact_route_scope_required"] is True, "C7.3 should require exact route scope")
    assert_true("command adapter smoke" in approval_gate["evidence_required"], "C7.3 should require smoke evidence")
    assert_true("rollback switch plan" in approval_gate["evidence_required"], "C7.3 should require rollback plan evidence")
    assert_true("live route change requested inside C7.3" in approval_gate["stop_conditions"], "C7.3 should stop on live route change")
    assert_true(
        "missing rollback expiry or log path" in approval_gate["stop_conditions"],
        "C7.3 should stop on incomplete rollback metadata",
    )

    rollback = route_plan["rollback_switch"]
    assert_true(rollback["explicit_owner_approval_required"] is True, "rollback should require explicit owner approval")
    assert_true(rollback["logged"] is True, "rollback should be logged")
    assert_true(rollback["time_bounded"] is True, "rollback should be time bounded")
    assert_true(rollback["expires_at_required"] is True, "rollback should require expiry")
    assert_true(rollback["automatic_fallback_allowed"] is False, "rollback should never be automatic fallback")

    logging_proof = route_plan["logging_proof"]
    assert_true(logging_proof["dry_run_packet_required"] is True, "C7.3 should require dry-run packet proof")
    assert_true("report-proof" in logging_proof["converge_source_of_truth"], "C7.3 should preserve report-proof authority")
    legacy_non_authoritative = logging_proof["legacy_sources_not_authoritative_for_converge_work"]
    for legacy_source in ("GoalFlow", "Work Ledger", "chat memory", "verification-convergence artifacts"):
        assert_true(
            legacy_source in legacy_non_authoritative,
            f"{legacy_source} should not remain authoritative for Converge-owned work",
        )

    cleanup_boundary = route_plan["cleanup_removal_boundary"]
    assert_true(cleanup_boundary["status"] == "completed", "C7.4 cleanup boundary should be completed")
    assert_true(cleanup_boundary["completed_slice"] == "C7.4 cleanup and removal plan", "C7.4 boundary should name the completed slice")
    assert_true(
        cleanup_boundary["next_operational_slice"] == "C7 live route replacement readiness plan",
        "C7.4 boundary should point to live route replacement readiness",
    )
    assert_true(cleanup_boundary["plan_only"] is True, "C7.4 boundary should remain plan-only")
    assert_true(cleanup_boundary["classification_only"] is True, "C7.4 boundary should remain classification-only")
    assert_true(cleanup_boundary["execution_allowed"] is False, "C7.4 boundary should not allow execution")
    assert_true(cleanup_boundary["legacy_deletion_allowed"] is False, "C7.3 should not allow legacy deletion")
    assert_true(cleanup_boundary["live_route_removal_allowed"] is False, "C7.3 should not allow live route removal")
    assert_true(
        "legacy scripts/docs/skills/aliases/state paths inventory" in cleanup_boundary["allowed_outputs"],
        "C7.4 boundary should define inventory as an allowed output",
    )
    assert_true(
        "cleanup/removal execution" in cleanup_boundary["prohibited_actions"],
        "C7.4 boundary should prohibit cleanup/removal execution",
    )
    assert_true(
        "legacy file deletion" in cleanup_boundary["prohibited_actions"],
        "C7.4 boundary should prohibit legacy file deletion",
    )
    assert_true(
        "legacy file movement" in cleanup_boundary["prohibited_actions"],
        "C7.4 boundary should prohibit legacy file movement",
    )
    assert_true(
        cleanup_boundary["separate_owner_approval_required"] is True,
        "C7.4 cleanup/removal should require separate owner approval",
    )

    cleanup_plan = route_plan["cleanup_removal_plan"]
    assert_true(cleanup_plan["version"] == "c7.4", "C7.4 cleanup/removal plan should expose C7.4 version")
    assert_true(
        cleanup_plan["execution_boundary"] == "classification_and_plan_only",
        "C7.4 cleanup/removal plan should stay classification/plan only",
    )
    assert_true(
        cleanup_plan["classification_values"]
        == ["retired", "archived", "still-active-for-non-Converge", "requires-owner-approval"],
        "C7.4 should fix exact cleanup classification values",
    )
    surfaces_by_name = {surface["surface"]: surface for surface in cleanup_plan["surfaces"]}
    assert_true(
        {surface: item["classification"] for surface, item in surfaces_by_name.items()} == expected_sources,
        "C7.4 should keep exact cleanup surface names and classifications",
    )
    categories = {surface["category"] for surface in cleanup_plan["surfaces"]}
    assert_true(categories == {"scripts", "docs", "skills", "aliases", "state paths"}, "C7.4 should cover all legacy surface categories")
    classifications = {surface["classification"] for surface in cleanup_plan["surfaces"]}
    assert_true("retired" in classifications, "C7.4 should include retired surfaces")
    assert_true("archived" in classifications, "C7.4 should include archived surfaces")
    assert_true("still-active-for-non-Converge" in classifications, "C7.4 should include non-Converge active surfaces")
    assert_true("requires-owner-approval" in classifications, "C7.4 should include owner-approval surfaces")
    assert_true(
        surfaces_by_name["/converge legacy alias"]["classification"] == "retired",
        "C7.4 should mark /converge alias as retired in the later plan",
    )
    assert_true(
        surfaces_by_name["workspace/state/goalflow/*"]["classification"] == "archived",
        "C7.4 should keep GoalFlow state historical/readable, not authoritative",
    )
    assert_true(
        surfaces_by_name["verification-convergence artifacts and chat-derived records"]["exact_path_discovery_required"]
        is True,
        "C7.4 should flag descriptive state-path buckets for exact path discovery",
    )
    for surface in cleanup_plan["surfaces"]:
        assert_true(surface["reason"], "C7.4 cleanup surface should include a reason")
        assert_true(surface["later_action_boundary"], "C7.4 cleanup surface should include later action boundary")
    source_boundary = cleanup_plan["source_of_truth_boundary"]
    assert_true(
        source_boundary["converge_authoritative_for_converge_work"] == expected_authoritative,
        "C7.4 should preserve exact Converge source-of-truth authorities",
    )
    assert_true(
        source_boundary["legacy_not_authoritative_for_converge_work"] == expected_non_authoritative,
        "C7.4 should preserve exact non-authoritative legacy sources",
    )
    assert_true(
        cleanup_boundary["prohibited_actions"] == expected_prohibited_actions,
        "C7.4 cleanup boundary should fix the exact prohibited-action list",
    )
    assert_true(
        cleanup_plan["prohibited_actions"] == expected_prohibited_actions,
        "C7.4 cleanup plan should fix the exact prohibited-action list",
    )
    assert_true(
        cleanup_plan["later_execution_requires"] == expected_later_execution_requires,
        "C7.4 later execution requirements should be exact",
    )

    readiness_plan = route_plan["live_route_replacement_readiness_plan"]
    assert_true(
        readiness_plan["version"] == "c7-live-route-readiness",
        "live route readiness plan should expose a stable version",
    )
    assert_true(
        readiness_plan["execution_boundary"] == "readiness_validation_only",
        "live route readiness should stay validation-only",
    )
    assert_true(
        readiness_plan["readiness_authorizes_live_change"] is False,
        "live route readiness should not authorize live changes",
    )
    approval_schema = readiness_plan["owner_approval_record_schema"]
    expected_approval_fields = [
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
    assert_true(approval_schema["required"] is True, "live route readiness should require an owner approval record")
    assert_true(
        approval_schema["required_fields"] == expected_approval_fields,
        "live route readiness should fix approval record fields",
    )
    assert_true(
        approval_schema["approval_kind"] == "operational_live_route_replacement",
        "live route readiness should require operational approval kind",
    )
    expected_approval_text = (
        "I explicitly approve the operational live route replacement for exact commands "
        "/goal, /verify, and /conv to the Converge canonical backend. I do not approve "
        "/converge promotion, cleanup/removal execution, legacy deletion/movement/archive, "
        "deploy/apply/install, external action, push/PR/release, or Gateway restart unless "
        "separately stated with preflight evidence."
    )
    assert_true(
        approval_schema["approval_text_template"] == expected_approval_text,
        "live route readiness should pin exact approval text",
    )
    assert_true(
        "/goal, /verify, and /conv" in approval_schema["approval_text_template"],
        "live route readiness should provide exact approval text",
    )
    assert_true(
        "/converge promotion" in approval_schema["approval_text_template"],
        "live route readiness approval text should exclude /converge promotion",
    )
    assert_true(
        approval_schema["must_bind_exact_commands"] == ["/goal", "/verify", "/conv"],
        "live route readiness approval should bind exact managed commands",
    )
    assert_true(
        approval_schema["must_name_explicit_exclusions"] == ["/converge"],
        "live route readiness approval should explicitly exclude /converge promotion",
    )
    exact_scope = readiness_plan["exact_route_scope"]
    assert_true(
        exact_scope["managed_commands"] == ["/goal", "/verify", "/conv"],
        "live route readiness should scope only managed commands",
    )
    assert_true(
        exact_scope["legacy_aliases_excluded_from_primary_route"] == ["/converge"],
        "live route readiness should keep /converge out of primary routing",
    )
    assert_true(
        exact_scope["implementation_scope_required"] is True,
        "live route readiness should require implementation route inventory",
    )
    assert_true(
        exact_scope["source_of_truth_after_gate"] == "converge.workflow",
        "live route readiness should preserve Converge workflow source of truth",
    )
    gateway_preflight = readiness_plan["gateway_restart_preflight"]
    assert_true(gateway_preflight["decision_required"] is True, "Gateway preflight decision should be required")
    assert_true(
        gateway_preflight["preflight_required_if_gateway_restart_or_route_config_reload"] is True,
        "Gateway preflight should be required if restart/config reload is needed",
    )
    assert_true(
        gateway_preflight["command"] == "python3 /Users/moon/.openclaw/workspace/scripts/gateway_restart_preflight.py",
        "Gateway preflight command should be exact",
    )
    assert_true(
        gateway_preflight["run_during_readiness_validation"] is False,
        "readiness validation should not run Gateway preflight",
    )
    assert_true(
        gateway_preflight["restart_authorized_by_readiness"] is False,
        "readiness validation should not authorize restart",
    )
    assert_true(gateway_preflight["blocks_if_failed"] is True, "failed Gateway preflight should block later execution")
    rollback_record = readiness_plan["rollback_record"]
    assert_true(rollback_record["automatic_fallback_allowed"] is False, "rollback should not be automatic fallback")
    assert_true(rollback_record["expires_at_required"] is True, "rollback should require expiry")
    assert_true(rollback_record["log_path_required"] is True, "rollback should require log path")
    assert_true(rollback_record["max_duration_hours"] == 24, "rollback max duration should be bounded")
    assert_true(
        rollback_record["log_path_template"]
        == "/Users/moon/.openclaw/state/converge/route-replacement/rollback-{approval_ref}-{yyyyMMddTHHmmssZ}.jsonl",
        "rollback log path should use an exact artifact template",
    )
    retention = readiness_plan["retention_decision"]
    assert_true(retention["required"] is True, "live route readiness should require retention decision")
    assert_true("delete" not in retention["allowed_readiness_decisions"], "readiness should not allow delete decision")
    assert_true(retention["later_cleanup_decisions"] == ["delete"], "delete should stay a later cleanup decision")
    assert_true(
        retention["deletion_authorized_by_readiness"] is False,
        "live route readiness should not authorize legacy deletion",
    )
    for source in ("GoalFlow state", "Work Ledger state", "verification-convergence artifacts", "chat-derived records"):
        assert_true(source in retention["covered_sources"], f"retention decision should cover {source}")
    assert_true(
        readiness_plan["pre_change_readiness_smoke"]
        == [
            "command adapter smoke",
            "recovery/report-proof smoke",
            "C7 route retirement dry-run packet",
            "C7 cleanup/removal dry-run packet",
            "synthetic duplicate visible report guard",
        ],
        "live route readiness should fix pre-change smoke evidence",
    )
    assert_true(
        "reserve-delivery/report-proof/complete-reported remains single-owner"
        in readiness_plan["post_change_smoke_plan"],
        "live route readiness should require post-change duplicate report smoke",
    )
    assert_true(
        readiness_plan["post_change_smoke_evidence_required_before_completion"] is True,
        "live route readiness should require post-change smoke evidence before completion",
    )
    duplicate_guard = readiness_plan["duplicate_visible_report_guard"]
    assert_true(duplicate_guard["exactly_one_route_owner_required"] is True, "readiness should require one route owner")
    assert_true(
        duplicate_guard["legacy_handler_must_be_suppressed_or_rollback_only"] is True,
        "legacy handler should be suppressed or rollback-only",
    )
    assert_true(
        duplicate_guard["no_replay_from_goalflow_work_ledger_or_chat_memory"] is True,
        "legacy records should not replay visible reports",
    )
    assert_true(
        duplicate_guard["no_replay_from_verification_convergence_artifacts"] is True,
        "verification-convergence artifacts should not replay visible reports",
    )
    for stop_condition in (
        "missing exact owner approval record",
        "Gateway restart preflight decision missing",
        "Gateway restart preflight failed",
        "Gateway restart requested without explicit restart approval",
        "route config reload preflight decision missing",
        "route config reload preflight failed",
        "route config reload requested without explicit reload approval",
        "attempted /converge alias promotion",
        "duplicate visible report risk unresolved",
        "pre-change readiness smoke failed",
        "post-change smoke plan missing",
        "post-change smoke evidence missing before completion",
        "post-change smoke failed",
        "unexpected live traffic observed",
        "live routing requested during readiness",
        "live route replacement requested during readiness",
        "live route removal requested during readiness",
        "cleanup/removal execution requested",
        "deploy/apply/install requested during readiness",
    ):
        assert_true(
            stop_condition in readiness_plan["stop_conditions"],
            f"live route readiness should stop on {stop_condition}",
        )


def assert_readiness_policy_output_is_isolated(state_root: Path) -> None:
    first = dry_run(state_root, "/goal mutate readiness packet")
    first_readiness = first["route_retirement_plan"]["live_route_replacement_readiness_plan"]
    first_readiness["exact_route_scope"]["managed_commands"].append("/mutated")
    first_readiness["retention_decision"]["covered_sources"].append("mutated source")

    second = dry_run(state_root, "/goal rebuild readiness packet")
    second_readiness = second["route_retirement_plan"]["live_route_replacement_readiness_plan"]
    assert_true(
        second_readiness["exact_route_scope"]["managed_commands"] == ["/goal", "/verify", "/conv"],
        "readiness route scope should not share mutable constants with prior packets",
    )
    assert_true(
        "mutated source" not in second_readiness["retention_decision"]["covered_sources"],
        "readiness retention decision should not share mutable constants with prior packets",
    )


def assert_c7_1_contract_validation_rejects_drift(state_root: Path) -> None:
    packet = dry_run(state_root, "/goal Implement accepted plan")

    missing_required = json.loads(json.dumps(packet))
    del missing_required["route"]["state_root"]
    try:
        validate_dry_run_packet(missing_required)
    except ValueError as exc:
        assert_true("route.state_root" in str(exc), "validator should reject missing required packet fields")
    else:
        raise AssertionError("validator should reject missing route.state_root")

    narrowed_required = json.loads(json.dumps(packet))
    narrowed_required["adapter_contract"]["required_packet_fields"].remove("route_retirement_plan.cleanup_removal_plan")
    try:
        validate_dry_run_packet(narrowed_required)
    except ValueError as exc:
        assert_true("required_packet_fields" in str(exc), "validator should reject narrowed required packet fields")
    else:
        raise AssertionError("validator should reject narrowed required packet fields")

    raw_message_drift = json.loads(json.dumps(packet))
    raw_message_drift["input"]["command"] = "/conv"
    try:
        validate_dry_run_packet(raw_message_drift)
    except ValueError as exc:
        assert_true("input fields" in str(exc), "validator should reject parsed command drift")
    else:
        raise AssertionError("validator should reject parsed command drift")

    route_command_drift = json.loads(json.dumps(packet))
    route_command_drift["route"]["current_command"] = "/verify"
    try:
        validate_dry_run_packet(route_command_drift)
    except ValueError as exc:
        assert_true("current_command" in str(exc), "validator should reject route command drift")
    else:
        raise AssertionError("validator should reject route command drift")

    argv_drift = json.loads(json.dumps(packet))
    argv_drift["converge_invocation"]["argv"].remove("--owner-session-key")
    try:
        validate_dry_run_packet(argv_drift)
    except ValueError as exc:
        assert_true("argv" in str(exc), "validator should reject invocation argv drift")
    else:
        raise AssertionError("validator should reject invocation argv drift")

    stale_flags = json.loads(json.dumps(packet))
    stale_flags["adapter_contract"]["route_free_flags"]["live_route_changed"] = True
    try:
        validate_dry_run_packet(stale_flags)
    except ValueError as exc:
        assert_true("route-free flags" in str(exc), "validator should reject stale route-free contract flags")
    else:
        raise AssertionError("validator should reject route-free flag drift")

    invalid_owner_session = json.loads(json.dumps(packet))
    invalid_owner_session["route"]["owner_session_key"] = {"session": "test"}
    try:
        validate_dry_run_packet(invalid_owner_session)
    except ValueError as exc:
        assert_true("owner_session_key" in str(exc), "validator should reject invalid owner session metadata")
    else:
        raise AssertionError("validator should reject invalid owner session metadata")

    empty_owner_session = json.loads(json.dumps(packet))
    empty_owner_session["route"]["owner_session_key"] = ""
    try:
        validate_dry_run_packet(empty_owner_session)
    except ValueError as exc:
        assert_true("owner_session_key" in str(exc), "validator should reject empty owner session metadata")
    else:
        raise AssertionError("validator should reject empty owner session metadata")

    invalid_visible_delivery = json.loads(json.dumps(packet))
    invalid_visible_delivery["route"]["visible_delivery"] = {"channel": "telegram"}
    try:
        validate_dry_run_packet(invalid_visible_delivery)
    except ValueError as exc:
        assert_true("visible_delivery.target" in str(exc), "validator should reject incomplete visible delivery metadata")
    else:
        raise AssertionError("validator should reject incomplete visible delivery metadata")

    empty_state_root = json.loads(json.dumps(packet))
    empty_state_root["route"]["state_root"] = ""
    try:
        validate_dry_run_packet(empty_state_root)
    except ValueError as exc:
        assert_true("state_root" in str(exc), "validator should reject empty state root metadata")
    else:
        raise AssertionError("validator should reject empty state root metadata")

    alias_drift = json.loads(json.dumps(packet))
    alias_drift["route"]["alias_status"] = "deprecated_alias"
    try:
        validate_dry_run_packet(alias_drift)
    except ValueError as exc:
        assert_true("alias_status" in str(exc), "validator should reject alias status drift")
    else:
        raise AssertionError("validator should reject alias status drift")

    missing_rollback = json.loads(json.dumps(packet))
    missing_rollback["inventory"][0]["rollback_switch"] = ""
    try:
        validate_dry_run_packet(missing_rollback)
    except ValueError as exc:
        assert_true("rollback_switch" in str(exc), "validator should reject missing rollback metadata")
    else:
        raise AssertionError("validator should reject missing rollback metadata")

    missing_route_plan = json.loads(json.dumps(packet))
    missing_route_plan["route_retirement_plan"]["rollback_switch"]["expires_at_required"] = False
    try:
        validate_dry_run_packet(missing_route_plan)
    except ValueError as exc:
        assert_true("rollback" in str(exc), "validator should reject rollback metadata without expiry")
    else:
        raise AssertionError("validator should reject rollback metadata without expiry")

    automatic_fallback = json.loads(json.dumps(packet))
    automatic_fallback["route_retirement_plan"]["rollback_switch"]["automatic_fallback_allowed"] = True
    try:
        validate_dry_run_packet(automatic_fallback)
    except ValueError as exc:
        assert_true("automatic fallback" in str(exc), "validator should reject automatic rollback fallback")
    else:
        raise AssertionError("validator should reject automatic rollback fallback")

    classification_drift = json.loads(json.dumps(packet))
    classification_drift["route_retirement_plan"]["route_classification"][0]["classification"] = "legacy_primary"
    try:
        validate_dry_run_packet(classification_drift)
    except ValueError as exc:
        assert_true("classify" in str(exc), "validator should reject route classification drift")
    else:
        raise AssertionError("validator should reject route classification drift")

    owner_drift = json.loads(json.dumps(packet))
    owner_drift["route_retirement_plan"]["route_classification"][0]["c7_owner"] = "GoalFlow"
    try:
        validate_dry_run_packet(owner_drift)
    except ValueError as exc:
        assert_true("c7_owner" in str(exc), "validator should reject route owner drift")
    else:
        raise AssertionError("validator should reject route owner drift")

    blocked_boundary_drift = json.loads(json.dumps(packet))
    blocked_boundary_drift["blocked_without_approval"].remove("live route replacement")
    try:
        validate_dry_run_packet(blocked_boundary_drift)
    except ValueError as exc:
        assert_true("blocked_without_approval" in str(exc), "validator should reject blocked action drift")
    else:
        raise AssertionError("validator should reject blocked action drift")

    blocked_cleanup_drift = json.loads(json.dumps(packet))
    blocked_cleanup_drift["blocked_without_approval"].remove("legacy skill disable/uninstall")
    try:
        validate_dry_run_packet(blocked_cleanup_drift)
    except ValueError as exc:
        assert_true("blocked_without_approval" in str(exc), "validator should reject C7.4 blocked action drift")
    else:
        raise AssertionError("validator should reject C7.4 blocked action drift")

    missing_evidence = json.loads(json.dumps(packet))
    missing_evidence["route_retirement_plan"]["approval_gate"]["evidence_required"].remove("rollback switch plan")
    try:
        validate_dry_run_packet(missing_evidence)
    except ValueError as exc:
        assert_true("evidence" in str(exc), "validator should reject missing approval evidence")
    else:
        raise AssertionError("validator should reject missing approval evidence")

    missing_stop_condition = json.loads(json.dumps(packet))
    missing_stop_condition["route_retirement_plan"]["approval_gate"]["stop_conditions"].remove(
        "missing rollback expiry or log path"
    )
    try:
        validate_dry_run_packet(missing_stop_condition)
    except ValueError as exc:
        assert_true("stop" in str(exc), "validator should reject missing approval stop condition")
    else:
        raise AssertionError("validator should reject missing approval stop condition")

    missing_legacy_source = json.loads(json.dumps(packet))
    missing_legacy_source["route_retirement_plan"]["logging_proof"][
        "legacy_sources_not_authoritative_for_converge_work"
    ].remove("Work Ledger")
    try:
        validate_dry_run_packet(missing_legacy_source)
    except ValueError as exc:
        assert_true("legacy sources" in str(exc), "validator should reject missing non-authoritative legacy source")
    else:
        raise AssertionError("validator should reject missing non-authoritative legacy source")

    cleanup_drift = json.loads(json.dumps(packet))
    cleanup_drift["route_retirement_plan"]["cleanup_removal_boundary"]["live_route_removal_allowed"] = True
    try:
        validate_dry_run_packet(cleanup_drift)
    except ValueError as exc:
        assert_true("cleanup boundary" in str(exc), "validator should reject cleanup boundary drift")
    else:
        raise AssertionError("validator should reject cleanup boundary drift")

    cleanup_output_drift = json.loads(json.dumps(packet))
    cleanup_output_drift["route_retirement_plan"]["cleanup_removal_boundary"]["allowed_outputs"].remove(
        "legacy scripts/docs/skills/aliases/state paths inventory"
    )
    try:
        validate_dry_run_packet(cleanup_output_drift)
    except ValueError as exc:
        assert_true("allowed outputs" in str(exc), "validator should reject cleanup allowed-output drift")
    else:
        raise AssertionError("validator should reject cleanup allowed-output drift")

    cleanup_action_drift = json.loads(json.dumps(packet))
    cleanup_action_drift["route_retirement_plan"]["cleanup_removal_boundary"]["prohibited_actions"].remove(
        "cleanup/removal execution"
    )
    try:
        validate_dry_run_packet(cleanup_action_drift)
    except ValueError as exc:
        assert_true("prohibited actions" in str(exc), "validator should reject cleanup prohibited-action drift")
    else:
        raise AssertionError("validator should reject cleanup prohibited-action drift")

    cleanup_file_delete_drift = json.loads(json.dumps(packet))
    cleanup_file_delete_drift["route_retirement_plan"]["cleanup_removal_plan"]["prohibited_actions"].remove(
        "legacy file deletion"
    )
    try:
        validate_dry_run_packet(cleanup_file_delete_drift)
    except ValueError as exc:
        assert_true("prohibited actions" in str(exc), "validator should reject legacy file deletion action drift")
    else:
        raise AssertionError("validator should reject legacy file deletion action drift")

    cleanup_gateway_action_drift = json.loads(json.dumps(packet))
    cleanup_gateway_action_drift["route_retirement_plan"]["cleanup_removal_plan"]["prohibited_actions"].remove(
        "Gateway restart"
    )
    try:
        validate_dry_run_packet(cleanup_gateway_action_drift)
    except ValueError as exc:
        assert_true("prohibited actions" in str(exc), "validator should reject cleanup Gateway action drift")
    else:
        raise AssertionError("validator should reject cleanup Gateway action drift")

    cleanup_deploy_action_drift = json.loads(json.dumps(packet))
    cleanup_deploy_action_drift["route_retirement_plan"]["cleanup_removal_boundary"]["prohibited_actions"].remove(
        "deploy/apply/install"
    )
    try:
        validate_dry_run_packet(cleanup_deploy_action_drift)
    except ValueError as exc:
        assert_true("prohibited actions" in str(exc), "validator should reject cleanup deploy action drift")
    else:
        raise AssertionError("validator should reject cleanup deploy action drift")

    cleanup_plan_surface_drift = json.loads(json.dumps(packet))
    cleanup_plan_surface_drift["route_retirement_plan"]["cleanup_removal_plan"]["surfaces"][0][
        "classification"
    ] = "archived"
    try:
        validate_dry_run_packet(cleanup_plan_surface_drift)
    except ValueError as exc:
        assert_true("surface inventory" in str(exc), "validator should reject C7.4 surface inventory drift")
    else:
        raise AssertionError("validator should reject C7.4 surface inventory drift")

    cleanup_plan_category_drift = json.loads(json.dumps(packet))
    cleanup_plan_category_drift["route_retirement_plan"]["cleanup_removal_plan"]["surfaces"] = [
        surface
        for surface in cleanup_plan_category_drift["route_retirement_plan"]["cleanup_removal_plan"]["surfaces"]
        if surface["category"] != "skills"
    ]
    try:
        validate_dry_run_packet(cleanup_plan_category_drift)
    except ValueError as exc:
        assert_true(
            "surface inventory" in str(exc) or "scripts, docs, skills" in str(exc),
            "validator should reject missing C7.4 surface category",
        )
    else:
        raise AssertionError("validator should reject missing C7.4 surface category")

    cleanup_plan_missing_surface_drift = json.loads(json.dumps(packet))
    cleanup_plan_missing_surface_drift["route_retirement_plan"]["cleanup_removal_plan"]["surfaces"] = [
        surface
        for surface in cleanup_plan_missing_surface_drift["route_retirement_plan"]["cleanup_removal_plan"][
            "surfaces"
        ]
        if surface["surface"] != "workspace/state/work-ledger/*"
    ]
    try:
        validate_dry_run_packet(cleanup_plan_missing_surface_drift)
    except ValueError as exc:
        assert_true("surface inventory" in str(exc), "validator should reject missing C7.4 same-category surface")
    else:
        raise AssertionError("validator should reject missing C7.4 same-category surface")

    cleanup_plan_extra_surface_drift = json.loads(json.dumps(packet))
    cleanup_plan_extra_surface_drift["route_retirement_plan"]["cleanup_removal_plan"]["surfaces"].append(
        {
            "category": "state paths",
            "surface": "workspace/state/unreviewed-extra/*",
            "classification": "retired",
            "reason": "drift fixture",
            "later_action_boundary": "drift fixture",
            "source_of_truth_boundary": "drift fixture",
        }
    )
    try:
        validate_dry_run_packet(cleanup_plan_extra_surface_drift)
    except ValueError as exc:
        assert_true("surface inventory" in str(exc), "validator should reject extra C7.4 same-category surface")
    else:
        raise AssertionError("validator should reject extra C7.4 same-category surface")

    cleanup_plan_source_drift = json.loads(json.dumps(packet))
    cleanup_plan_source_drift["route_retirement_plan"]["cleanup_removal_plan"]["source_of_truth_boundary"][
        "converge_authoritative_for_converge_work"
    ].remove("complete-reported")
    try:
        validate_dry_run_packet(cleanup_plan_source_drift)
    except ValueError as exc:
        assert_true(
            "source-of-truth" in str(exc),
            "validator should reject missing C7.4 Converge source-of-truth authority",
        )
    else:
        raise AssertionError("validator should reject missing C7.4 source-of-truth authority")

    cleanup_plan_extra_source_drift = json.loads(json.dumps(packet))
    cleanup_plan_extra_source_drift["route_retirement_plan"]["cleanup_removal_plan"]["source_of_truth_boundary"][
        "converge_authoritative_for_converge_work"
    ].append("GoalFlow")
    try:
        validate_dry_run_packet(cleanup_plan_extra_source_drift)
    except ValueError as exc:
        assert_true(
            "source-of-truth" in str(exc),
            "validator should reject extra C7.4 Converge source-of-truth authority",
        )
    else:
        raise AssertionError("validator should reject extra C7.4 source-of-truth authority")

    cleanup_plan_legacy_source_drift = json.loads(json.dumps(packet))
    cleanup_plan_legacy_source_drift["route_retirement_plan"]["cleanup_removal_plan"]["source_of_truth_boundary"][
        "legacy_not_authoritative_for_converge_work"
    ].remove("chat memory")
    try:
        validate_dry_run_packet(cleanup_plan_legacy_source_drift)
    except ValueError as exc:
        assert_true(
            "legacy sources" in str(exc),
            "validator should reject missing C7.4 non-authoritative legacy source",
        )
    else:
        raise AssertionError("validator should reject missing C7.4 non-authoritative legacy source")

    cleanup_plan_extra_legacy_source_drift = json.loads(json.dumps(packet))
    cleanup_plan_extra_legacy_source_drift["route_retirement_plan"]["cleanup_removal_plan"][
        "source_of_truth_boundary"
    ]["legacy_not_authoritative_for_converge_work"].append("unreviewed legacy source")
    try:
        validate_dry_run_packet(cleanup_plan_extra_legacy_source_drift)
    except ValueError as exc:
        assert_true(
            "legacy sources" in str(exc),
            "validator should reject extra C7.4 non-authoritative legacy source",
        )
    else:
        raise AssertionError("validator should reject extra C7.4 non-authoritative legacy source")

    cleanup_plan_later_requirement_drift = json.loads(json.dumps(packet))
    cleanup_plan_later_requirement_drift["route_retirement_plan"]["cleanup_removal_plan"][
        "later_execution_requires"
    ].remove("retention decision for historical state")
    try:
        validate_dry_run_packet(cleanup_plan_later_requirement_drift)
    except ValueError as exc:
        assert_true(
            "later execution requirements" in str(exc),
            "validator should reject missing C7.4 later execution requirement",
        )
    else:
        raise AssertionError("validator should reject missing C7.4 later execution requirement")

    cleanup_plan_extra_requirement_drift = json.loads(json.dumps(packet))
    cleanup_plan_extra_requirement_drift["route_retirement_plan"]["cleanup_removal_plan"][
        "later_execution_requires"
    ].append("implicit cleanup permission")
    try:
        validate_dry_run_packet(cleanup_plan_extra_requirement_drift)
    except ValueError as exc:
        assert_true(
            "later execution requirements" in str(exc),
            "validator should reject extra C7.4 later execution requirement",
        )
    else:
        raise AssertionError("validator should reject extra C7.4 later execution requirement")

    cleanup_plan_path_discovery_drift = json.loads(json.dumps(packet))
    cleanup_plan_path_discovery_drift["route_retirement_plan"]["cleanup_removal_plan"]["surfaces"][-1].pop(
        "exact_path_discovery_required"
    )
    try:
        validate_dry_run_packet(cleanup_plan_path_discovery_drift)
    except ValueError as exc:
        assert_true("surface inventory" in str(exc), "validator should reject missing exact path discovery flag")
    else:
        raise AssertionError("validator should reject missing exact path discovery flag")

    readiness_live_change_drift = json.loads(json.dumps(packet))
    readiness_live_change_drift["route_retirement_plan"]["live_route_replacement_readiness_plan"][
        "readiness_authorizes_live_change"
    ] = True
    try:
        validate_dry_run_packet(readiness_live_change_drift)
    except ValueError as exc:
        assert_true("must not authorize live changes" in str(exc), "validator should reject live-change authorization")
    else:
        raise AssertionError("validator should reject live route authorization from readiness plan")

    readiness_scope_drift = json.loads(json.dumps(packet))
    readiness_scope_drift["route_retirement_plan"]["live_route_replacement_readiness_plan"]["exact_route_scope"][
        "managed_commands"
    ].remove("/conv")
    try:
        validate_dry_run_packet(readiness_scope_drift)
    except ValueError as exc:
        assert_true("exact route scope" in str(exc), "validator should reject narrowed live readiness route scope")
    else:
        raise AssertionError("validator should reject narrowed live readiness route scope")

    readiness_preflight_drift = json.loads(json.dumps(packet))
    readiness_preflight_drift["route_retirement_plan"]["live_route_replacement_readiness_plan"][
        "gateway_restart_preflight"
    ]["run_during_readiness_validation"] = True
    try:
        validate_dry_run_packet(readiness_preflight_drift)
    except ValueError as exc:
        assert_true("Gateway preflight policy" in str(exc), "validator should reject readiness preflight execution drift")
    else:
        raise AssertionError("validator should reject running Gateway preflight during readiness validation")

    readiness_rollback_drift = json.loads(json.dumps(packet))
    readiness_rollback_drift["route_retirement_plan"]["live_route_replacement_readiness_plan"]["rollback_record"][
        "automatic_fallback_allowed"
    ] = True
    try:
        validate_dry_run_packet(readiness_rollback_drift)
    except ValueError as exc:
        assert_true("automatic fallback" in str(exc), "validator should reject automatic rollback fallback drift")
    else:
        raise AssertionError("validator should reject automatic rollback fallback drift")

    readiness_retention_drift = json.loads(json.dumps(packet))
    readiness_retention_drift["route_retirement_plan"]["live_route_replacement_readiness_plan"][
        "retention_decision"
    ]["deletion_authorized_by_readiness"] = True
    try:
        validate_dry_run_packet(readiness_retention_drift)
    except ValueError as exc:
        assert_true("retention decision" in str(exc), "validator should reject readiness retention deletion drift")
    else:
        raise AssertionError("validator should reject readiness retention deletion drift")

    readiness_retention_delete_drift = json.loads(json.dumps(packet))
    readiness_retention_delete_drift["route_retirement_plan"]["live_route_replacement_readiness_plan"][
        "retention_decision"
    ]["allowed_readiness_decisions"].append("delete")
    try:
        validate_dry_run_packet(readiness_retention_delete_drift)
    except ValueError as exc:
        assert_true("retention decision" in str(exc), "validator should reject readiness delete decision drift")
    else:
        raise AssertionError("validator should reject readiness delete decision drift")

    readiness_approval_kind_drift = json.loads(json.dumps(packet))
    readiness_approval_kind_drift["route_retirement_plan"]["live_route_replacement_readiness_plan"][
        "owner_approval_record_schema"
    ]["approval_kind"] = "readiness_plan_only"
    try:
        validate_dry_run_packet(readiness_approval_kind_drift)
    except ValueError as exc:
        assert_true("approval kind" in str(exc), "validator should reject ambiguous approval kind")
    else:
        raise AssertionError("validator should reject ambiguous approval kind")

    readiness_approval_text_drift = json.loads(json.dumps(packet))
    readiness_approval_text_drift["route_retirement_plan"]["live_route_replacement_readiness_plan"][
        "owner_approval_record_schema"
    ]["approval_text_template"] = "I approve the readiness plan."
    try:
        validate_dry_run_packet(readiness_approval_text_drift)
    except ValueError as exc:
        assert_true("approval text" in str(exc), "validator should reject ambiguous approval text")
    else:
        raise AssertionError("validator should reject ambiguous approval text")

    readiness_rollback_path_drift = json.loads(json.dumps(packet))
    readiness_rollback_path_drift["route_retirement_plan"]["live_route_replacement_readiness_plan"][
        "rollback_record"
    ]["log_path_template"] = "approved Converge or OpenClaw state/log root"
    try:
        validate_dry_run_packet(readiness_rollback_path_drift)
    except ValueError as exc:
        assert_true("log path template" in str(exc), "validator should reject generic rollback log path")
    else:
        raise AssertionError("validator should reject generic rollback log path")

    readiness_duplicate_guard_drift = json.loads(json.dumps(packet))
    readiness_duplicate_guard_drift["route_retirement_plan"]["live_route_replacement_readiness_plan"][
        "duplicate_visible_report_guard"
    ].pop("no_replay_from_verification_convergence_artifacts")
    try:
        validate_dry_run_packet(readiness_duplicate_guard_drift)
    except ValueError as exc:
        assert_true("duplicate report guard" in str(exc), "validator should reject missing verification artifact replay guard")
    else:
        raise AssertionError("validator should reject missing verification artifact replay guard")

    readiness_post_smoke_evidence_drift = json.loads(json.dumps(packet))
    readiness_post_smoke_evidence_drift["route_retirement_plan"]["live_route_replacement_readiness_plan"][
        "post_change_smoke_evidence_required_before_completion"
    ] = False
    try:
        validate_dry_run_packet(readiness_post_smoke_evidence_drift)
    except ValueError as exc:
        assert_true("post-change smoke evidence" in str(exc), "validator should reject missing post-change smoke evidence gate")
    else:
        raise AssertionError("validator should reject missing post-change smoke evidence gate")

    readiness_stop_drift = json.loads(json.dumps(packet))
    readiness_stop_drift["route_retirement_plan"]["live_route_replacement_readiness_plan"]["stop_conditions"].remove(
        "duplicate visible report risk unresolved"
    )
    try:
        validate_dry_run_packet(readiness_stop_drift)
    except ValueError as exc:
        assert_true("stop conditions" in str(exc), "validator should reject missing live readiness stop condition")
    else:
        raise AssertionError("validator should reject missing live readiness stop condition")


def assert_rejects_non_managed_or_empty_commands(state_root: Path) -> None:
    missing_text = dry_run_fail(state_root, "/goal")
    assert_true("requires non-empty text" in missing_text["error"], "empty command should fail deterministically")
    unmanaged = dry_run_fail(state_root, "/plan demo")
    assert_true("must start with" in unmanaged["error"], "unmanaged slash command should fail deterministically")
    leading_space = dry_run_fail(state_root, " /goal demo")
    assert_true("must start with" in leading_space["error"], "leading whitespace should not match exact slash scope")
    leading_tab = dry_run_fail(state_root, "\t/conv demo")
    assert_true("must start with" in leading_tab["error"], "leading tab should not match exact slash scope")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_root = Path(tmp) / "state"
        assert_inventory_covers_managed_commands(state_root)
        assert_c7_1_command_metadata_contract(state_root)
        assert_c7_3_route_retirement_plan_contract(state_root)
        assert_readiness_policy_output_is_isolated(state_root)
        assert_c7_1_contract_validation_rejects_drift(state_root)
        assert_dry_run_maps_command_without_state_creation(state_root, "/goal Build accepted plan", "goal")
        assert_dry_run_maps_command_without_state_creation(state_root, "/verify Audit docs", "verify")
        assert_dry_run_maps_command_without_state_creation(state_root, "/conv Improve plan", "conv")
        alias = dry_run(state_root, "/converge Improve plan")
        assert_true(alias["route"]["converge_mode"] == "conv", "/converge should map to conv")
        assert_true(alias["route"]["alias_status"] == "deprecated_alias", "/converge should be marked deprecated")
        assert_rejects_non_managed_or_empty_commands(state_root)


if __name__ == "__main__":
    main()
