#!/usr/bin/env python3
"""Smoke coverage for Phase 6 route parity evidence bundles."""

from __future__ import annotations

import json
import tempfile
from copy import deepcopy
from pathlib import Path

try:
    from smoke_helpers import VISIBLE_DELIVERY, assert_true, run, run_fail
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import VISIBLE_DELIVERY, assert_true, run, run_fail


OWNER_SESSION = "session:phase6-evidence"
VISIBLE = json.loads(VISIBLE_DELIVERY)
EXPECTED_MODES = {
    "/goal": "goal",
    "/verify": "verify",
    "/conv": "conv",
}


def valid_command_record(command: str, state_root: Path) -> dict:
    workflow_id = f"phase6-{command.strip('/')}-workflow"
    return {
        "command": command,
        "converge_mode": EXPECTED_MODES[command],
        "fresh_route_context": True,
        "owner_session_key": OWNER_SESSION,
        "visible_delivery": VISIBLE,
        "state_root": str(state_root),
        "workflow_id": workflow_id,
        "route_owner": "converge",
        "route_owner_refs": ["converge"],
        "legacy_handler_invoked": False,
        "report_proof_ref": f"report-proof:{workflow_id}:{command}",
        "complete_reported_ref": f"complete-reported:{workflow_id}:{command}",
        "duplicate_visible_report_detected": False,
    }


def valid_evidence(state_root: Path) -> dict:
    return {
        "evidence_source": "fresh-route",
        "proof_level": "fresh_route_evidence_bundle",
        "gateway_restart_performed": False,
        "route_change_performed": False,
        "deploy_or_install_performed": False,
        "external_action_performed": False,
        "cleanup_or_legacy_removal_performed": False,
        "commands": {
            "/goal": valid_command_record("/goal", state_root),
            "/verify": valid_command_record("/verify", state_root),
            "/conv": valid_command_record("/conv", state_root),
        },
        "aliases": {
            "/converge": {
                "promoted": False,
                "primary_route_owner": "none",
            }
        },
        "retained_skill_parity_matrix": {
            "audit": [
                {
                    "requirement": "deterministic evidence and report proof",
                    "converge_evidence_ref": "route:/verify:evidence-map",
                }
            ],
            "repair": [
                {
                    "requirement": "accepted change plus delta lane",
                    "converge_evidence_ref": "route:/conv:round-proof",
                }
            ],
            "improve": [
                {
                    "requirement": "objective-preserving improvement round",
                    "converge_evidence_ref": "route:/conv:improve-proof",
                }
            ],
        },
    }


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def route_parity_verify(state_root: Path, evidence_file: Path) -> dict:
    return run("route-parity-verify", "--evidence-file", str(evidence_file), state_root=state_root)


def route_parity_verify_fail(state_root: Path, evidence_file: Path) -> dict:
    return run_fail("route-parity-verify", "--evidence-file", str(evidence_file), state_root=state_root)


def assert_rejects(state_root: Path, evidence_file: Path, payload: dict, expected_error: str) -> None:
    write_json(evidence_file, payload)
    result = route_parity_verify_fail(state_root, evidence_file)
    assert_true(expected_error in result["error"], f"expected route parity evidence rejection containing {expected_error!r}")


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        state_root = root / "state"
        evidence_file = root / "phase6-evidence.json"
        evidence = valid_evidence(state_root)
        write_json(evidence_file, evidence)
        result = route_parity_verify(state_root, evidence_file)
        assert_true(result["ok"] is True, "valid fresh-route evidence should pass")
        assert_true(
            result["production_route_parity_proven"] is True,
            "fresh-route evidence bundle should be allowed to prove Phase 6 parity",
        )

        cli_only = deepcopy(evidence)
        cli_only["evidence_source"] = "command-dry-run"
        assert_rejects(state_root, evidence_file, cli_only, "CLI-only or command-adapter")

        relabeled_dry_run = deepcopy(evidence)
        relabeled_dry_run["proof_level"] = "route_dry_run_gate"
        assert_rejects(state_root, evidence_file, relabeled_dry_run, "fresh_route_evidence_bundle")

        duplicate_owner = deepcopy(evidence)
        duplicate_owner["commands"]["/verify"]["route_owner"] = "converge+verification-convergence"
        assert_rejects(state_root, evidence_file, duplicate_owner, "exactly one Converge route owner")

        extra_owner_field = deepcopy(evidence)
        extra_owner_field["commands"]["/verify"]["secondary_route_owner"] = "verification-convergence"
        assert_rejects(state_root, evidence_file, extra_owner_field, "unexpected route owner field")

        duplicate_owner_ref = deepcopy(evidence)
        duplicate_owner_ref["commands"]["/verify"]["route_owner_refs"] = ["converge", "verification-convergence"]
        assert_rejects(state_root, evidence_file, duplicate_owner_ref, "exactly one route owner ref")

        command_drift = deepcopy(evidence)
        command_drift["commands"]["/goal"]["command"] = "/verify"
        assert_rejects(state_root, evidence_file, command_drift, "bind command exactly")

        mode_drift = deepcopy(evidence)
        mode_drift["commands"]["/verify"]["converge_mode"] = "conv"
        assert_rejects(state_root, evidence_file, mode_drift, "expected Converge mode")

        owner_session_drift = deepcopy(evidence)
        owner_session_drift["commands"]["/conv"]["owner_session_key"] = "session:other"
        assert_rejects(state_root, evidence_file, owner_session_drift, "one owner session")

        visible_delivery_drift = deepcopy(evidence)
        visible_delivery_drift["commands"]["/conv"]["visible_delivery"] = {"channel": "telegram", "target": "other"}
        assert_rejects(state_root, evidence_file, visible_delivery_drift, "one visible delivery target")

        mixed_state_root = deepcopy(evidence)
        mixed_state_root["commands"]["/conv"]["state_root"] = str(root / "other-state")
        assert_rejects(state_root, evidence_file, mixed_state_root, "one state root")

        invoked_state_root_drift = valid_evidence(root / "stale-state")
        assert_rejects(state_root, evidence_file, invoked_state_root_drift, "invoked state root")

        duplicate_workflow_id = deepcopy(evidence)
        duplicate_workflow_id["commands"]["/conv"]["workflow_id"] = evidence["commands"]["/verify"]["workflow_id"]
        duplicate_workflow_id["commands"]["/conv"]["report_proof_ref"] = (
            f"report-proof:{evidence['commands']['/verify']['workflow_id']}:/conv"
        )
        duplicate_workflow_id["commands"]["/conv"]["complete_reported_ref"] = (
            f"complete-reported:{evidence['commands']['/verify']['workflow_id']}:/conv"
        )
        assert_rejects(state_root, evidence_file, duplicate_workflow_id, "distinct workflow ids")

        cross_command_report_proof = deepcopy(evidence)
        cross_command_report_proof["commands"]["/conv"]["report_proof_ref"] = evidence["commands"]["/verify"]["report_proof_ref"]
        assert_rejects(state_root, evidence_file, cross_command_report_proof, "bind report_proof_ref")

        cross_command_complete_proof = deepcopy(evidence)
        cross_command_complete_proof["commands"]["/conv"]["complete_reported_ref"] = evidence["commands"]["/verify"][
            "complete_reported_ref"
        ]
        assert_rejects(state_root, evidence_file, cross_command_complete_proof, "bind complete_reported_ref")

        command_side_effect = deepcopy(evidence)
        command_side_effect["commands"]["/verify"]["side_effects_performed"] = ["gateway_restart"]
        assert_rejects(state_root, evidence_file, command_side_effect, "command side effects")

        legacy_replay = deepcopy(evidence)
        legacy_replay["commands"]["/conv"]["legacy_handler_invoked"] = True
        assert_rejects(state_root, evidence_file, legacy_replay, "legacy handler was not invoked")

        missing_report_proof = deepcopy(evidence)
        missing_report_proof["commands"]["/goal"].pop("report_proof_ref")
        assert_rejects(state_root, evidence_file, missing_report_proof, "missing report_proof_ref")

        duplicate_report = deepcopy(evidence)
        duplicate_report["commands"]["/goal"]["duplicate_visible_report_detected"] = True
        assert_rejects(state_root, evidence_file, duplicate_report, "no duplicate visible report")

        converge_promoted = deepcopy(evidence)
        converge_promoted["aliases"]["/converge"]["promoted"] = True
        assert_rejects(state_root, evidence_file, converge_promoted, "/converge was not promoted")

        side_effect = deepcopy(evidence)
        side_effect["gateway_restart_performed"] = True
        assert_rejects(state_root, evidence_file, side_effect, "gateway_restart_performed=false")

        missing_matrix = deepcopy(evidence)
        missing_matrix["retained_skill_parity_matrix"]["repair"] = []
        assert_rejects(state_root, evidence_file, missing_matrix, "repair mappings")


if __name__ == "__main__":
    main()
