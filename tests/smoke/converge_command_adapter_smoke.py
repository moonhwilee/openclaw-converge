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
        result["adapter_contract"]["shared_metadata"]["rollback_field"] == "inventory.rollback_switch",
        "C7.1 contract should fix rollback field",
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
    assert_true(route_plan["version"] == "c7.3", "route retirement plan should expose C7.3 version")
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
    assert_true("live route change requested inside C7.3" in approval_gate["stop_conditions"], "C7.3 should stop on live route change")

    rollback = route_plan["rollback_switch"]
    assert_true(rollback["explicit_owner_approval_required"] is True, "rollback should require explicit owner approval")
    assert_true(rollback["logged"] is True, "rollback should be logged")
    assert_true(rollback["time_bounded"] is True, "rollback should be time bounded")
    assert_true(rollback["expires_at_required"] is True, "rollback should require expiry")
    assert_true(rollback["automatic_fallback_allowed"] is False, "rollback should never be automatic fallback")

    logging_proof = route_plan["logging_proof"]
    assert_true(logging_proof["dry_run_packet_required"] is True, "C7.3 should require dry-run packet proof")
    assert_true("report-proof" in logging_proof["converge_source_of_truth"], "C7.3 should preserve report-proof authority")
    assert_true(
        "GoalFlow" in logging_proof["legacy_sources_not_authoritative_for_converge_work"],
        "GoalFlow should not remain authoritative for Converge-owned work",
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


def assert_rejects_non_managed_or_empty_commands(state_root: Path) -> None:
    missing_text = run_fail("command-dry-run", "--raw-message", "/goal", state_root=state_root)
    assert_true("requires non-empty text" in missing_text["error"], "empty command should fail deterministically")
    unmanaged = run_fail("command-dry-run", "--raw-message", "/plan demo", state_root=state_root)
    assert_true("must start with" in unmanaged["error"], "unmanaged slash command should fail deterministically")


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
