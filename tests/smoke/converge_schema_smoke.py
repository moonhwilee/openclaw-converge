#!/usr/bin/env python3
"""Smoke coverage for bundled schemas and sample fixtures."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from converge.continuation import default_continuation_plan
from converge.schema import SchemaError, validate_named


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="converge-schema-smoke-") as tmp:
        result = subprocess.run(
            [sys.executable, "-m", "converge.cli", "--state-root", tmp, "validate", "--sample-docs"],
            cwd=str(ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"validate failed\nstdout={result.stdout}\nstderr={result.stderr}")
        payload = json.loads(result.stdout)
        if not payload.get("ok"):
            raise AssertionError(payload)
        required = {
            "artifact.schema.json",
            "checkpoint_state_update.schema.json",
            "event.schema.json",
            "workflow.schema.json",
        }
        if not required.issubset(set(payload["schemas"])):
            raise AssertionError(f"missing schemas: {required - set(payload['schemas'])}")
        workflow = {
            "schema_version": 1,
            "workflow_id": "goal-schema",
            "kind": "goal",
            "status": "running",
            "created_at": "2026-05-24T00:00:00Z",
            "updated_at": "2026-05-24T00:00:00Z",
            "last_activity_at": "2026-05-24T00:00:00Z",
            "last_visible_update_at": None,
            "stale_after_seconds": 7200,
            "reminder_after_seconds": 1800,
            "owner_session_key": "",
            "visible_delivery": {},
            "source_request": "demo",
            "objective": "demo",
            "non_goals": [],
            "success_criteria": [],
            "assumptions": [],
            "approval_boundaries": [],
            "approvals": [],
            "phase": "start",
            "parent_workflow_id": None,
            "child_workflow_ids": [],
            "artifacts": [],
            "context_manifest": [],
            "context_artifacts": [],
            "decisions": [],
            "side_effects_performed": [],
            "verification": {},
            "active_recovery_lease": None,
            "active_delivery_reservation": None,
            "checkpoint_index": {},
            "continuation_plan": default_continuation_plan("goal"),
            "next_safe_action": {
                "action_type": "inspect_or_continue",
                "summary": "Inspect workflow state.",
                "risk_class": "read_only",
                "requires_approval": False,
                "approval_ref": None,
                "side_effect_key": "inspect:goal-schema:start",
                "idempotency_policy": "repeatable",
                "expected_artifacts": ["workflow.json"],
                "cursor": "baseline",
            },
            "visible_delivery_state": {},
            "final_status": None,
            "goal_state": {},
        }
        validate_named(workflow, "workflow.schema.json")
        invalid = dict(workflow)
        invalid["final_status"] = "pass"
        try:
            validate_named(invalid, "workflow.schema.json")
            raise AssertionError("string final_status unexpectedly passed")
        except SchemaError:
            pass
        invalid = dict(workflow)
        invalid["final_status"] = {"verdict": "pass"}
        try:
            validate_named(invalid, "workflow.schema.json")
            raise AssertionError("verdict alias final_status unexpectedly passed")
        except SchemaError:
            pass
        valid = dict(workflow)
        valid["final_status"] = {
            "result": "pass",
            "done": ["schema final_status object"],
            "checked": ["workflow.schema.json"],
            "residuals": {},
        }
        validate_named(valid, "workflow.schema.json")
        invalid = dict(workflow)
        invalid["continuation_plan"] = {"rolling_state": {"current_resume_cursor": "baseline"}}
        try:
            validate_named(invalid, "workflow.schema.json")
            raise AssertionError("malformed continuation_plan unexpectedly passed")
        except SchemaError:
            pass
        invalid = dict(workflow)
        invalid["continuation_plan"] = default_continuation_plan("goal")
        invalid["continuation_plan"]["current_step_index"] = 1
        try:
            validate_named(invalid, "workflow.schema.json")
            raise AssertionError("out-of-range current_step_index unexpectedly passed")
        except SchemaError:
            pass
        invalid = dict(workflow)
        invalid["continuation_plan"] = default_continuation_plan("goal")
        invalid["continuation_plan"]["rolling_state"]["current_resume_cursor"] = "slice-2"
        try:
            validate_named(invalid, "workflow.schema.json")
            raise AssertionError("cursor/index mismatch unexpectedly passed")
        except SchemaError:
            pass
        invalid = dict(workflow)
        invalid["continuation_plan"] = default_continuation_plan("goal")
        invalid["continuation_plan"]["steps"][0]["next_on_pass"] = "slice-missing"
        try:
            validate_named(invalid, "workflow.schema.json")
            raise AssertionError("unknown next_on_pass target unexpectedly passed")
        except SchemaError:
            pass
        invalid = dict(workflow)
        invalid["continuation_plan"] = None
        try:
            validate_named(invalid, "workflow.schema.json")
            raise AssertionError("goal workflow without continuation_plan unexpectedly passed")
        except SchemaError:
            pass
        invalid = dict(workflow)
        invalid["conv_state"] = {}
        try:
            validate_named(invalid, "workflow.schema.json")
            raise AssertionError("goal workflow with foreign mode state unexpectedly passed")
        except SchemaError:
            pass
        invalid = dict(workflow)
        del invalid["goal_state"]
        try:
            validate_named(invalid, "workflow.schema.json")
            raise AssertionError("goal workflow without goal_state unexpectedly passed")
        except SchemaError:
            pass
        invalid = dict(workflow)
        del invalid["last_visible_update_at"]
        try:
            validate_named(invalid, "workflow.schema.json")
            raise AssertionError("workflow without last_visible_update_at unexpectedly passed")
        except SchemaError:
            pass
        long_plan = dict(workflow)
        long_plan["kind"] = "plan"
        long_plan["workflow_id"] = "plan-schema"
        long_plan["plan_state"] = {}
        del long_plan["goal_state"]
        validate_named(long_plan, "workflow.schema.json")
    print(json.dumps({"ok": True, "checked": sorted(required)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
