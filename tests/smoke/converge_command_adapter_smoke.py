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


def assert_dry_run_maps_command_without_state_creation(state_root: Path, raw_message: str, expected_mode: str) -> None:
    result = run(
        "command-dry-run",
        "--raw-message",
        raw_message,
        "--workflow-id",
        "synthetic-c7",
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )
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
    assert_true(result["route"]["owner_session_key"] == "session:test", "owner session should be preserved")
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
    result = run("command-dry-run", "--raw-message", "/goal Implement dry-run adapter", state_root=state_root)
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
    goal = run("command-dry-run", "--raw-message", "/goal Implement accepted plan", state_root=state_root)
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

    verify = run("command-dry-run", "--raw-message", "/verify Audit C7 docs", state_root=state_root)
    verify_metadata = verify["adapter_contract"]["command_metadata"]
    assert_true(verify_metadata["intent"] == "audit", "/verify should expose audit intent")
    assert_true(verify_metadata["audit"]["default_intent"] is True, "/verify should default to audit intent")
    assert_true(verify_metadata["audit"]["evidence_capture_required"] is True, "/verify should require evidence capture")
    assert_true("residuals" in verify_metadata["required_fields"], "/verify should require residual fields")

    conv = run("command-dry-run", "--raw-message", "/conv Improve C7 plan", state_root=state_root)
    conv_metadata = conv["adapter_contract"]["command_metadata"]
    assert_true(conv_metadata["intent"] == "repair_or_improve", "/conv should expose repair/improve intent")
    assert_true(conv_metadata["rounds"]["round_metadata_required"] is True, "/conv should require round metadata")
    assert_true(conv_metadata["rounds"]["original_target_gate_required"] is True, "/conv should require original-target gate")
    assert_true(conv_metadata["rounds"]["delta_gate_required"] is True, "/conv should require delta gate")
    assert_true("round_index" in conv_metadata["required_fields"], "/conv should require round index")


def assert_c7_3_route_retirement_plan_contract(state_root: Path) -> None:
    result = run("command-dry-run", "--raw-message", "/goal Implement route retirement plan", state_root=state_root)
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
    assert_true(cleanup_boundary["next_slice"] == "C7.4 cleanup and removal plan", "C7.3 should point cleanup to C7.4")
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


def assert_c7_1_contract_validation_rejects_drift(state_root: Path) -> None:
    packet = run("command-dry-run", "--raw-message", "/goal Implement accepted plan", state_root=state_root)

    missing_required = json.loads(json.dumps(packet))
    del missing_required["route"]["state_root"]
    try:
        validate_dry_run_packet(missing_required)
    except ValueError as exc:
        assert_true("route.state_root" in str(exc), "validator should reject missing required packet fields")
    else:
        raise AssertionError("validator should reject missing route.state_root")

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


def assert_rejects_non_managed_or_empty_commands(state_root: Path) -> None:
    missing_text = run_fail("command-dry-run", "--raw-message", "/goal", state_root=state_root)
    assert_true("requires non-empty text" in missing_text["error"], "empty command should fail deterministically")
    unmanaged = run_fail("command-dry-run", "--raw-message", "/plan demo", state_root=state_root)
    assert_true("must start with" in unmanaged["error"], "unmanaged slash command should fail deterministically")
    leading_space = run_fail("command-dry-run", "--raw-message", " /goal demo", state_root=state_root)
    assert_true("must start with" in leading_space["error"], "leading whitespace should not match exact slash scope")
    leading_tab = run_fail("command-dry-run", "--raw-message", "\t/conv demo", state_root=state_root)
    assert_true("must start with" in leading_tab["error"], "leading tab should not match exact slash scope")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        state_root = Path(tmp) / "state"
        assert_inventory_covers_managed_commands(state_root)
        assert_c7_1_command_metadata_contract(state_root)
        assert_c7_3_route_retirement_plan_contract(state_root)
        assert_c7_1_contract_validation_rejects_drift(state_root)
        assert_dry_run_maps_command_without_state_creation(state_root, "/goal Build accepted plan", "goal")
        assert_dry_run_maps_command_without_state_creation(state_root, "/verify Audit docs", "verify")
        assert_dry_run_maps_command_without_state_creation(state_root, "/conv Improve plan", "conv")
        alias = run("command-dry-run", "--raw-message", "/converge Improve plan", state_root=state_root)
        assert_true(alias["route"]["converge_mode"] == "conv", "/converge should map to conv")
        assert_true(alias["route"]["alias_status"] == "deprecated_alias", "/converge should be marked deprecated")
        assert_rejects_non_managed_or_empty_commands(state_root)


if __name__ == "__main__":
    main()
