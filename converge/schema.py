"""Small JSON-schema subset validator for Converge runtime state.

The MVP keeps dependencies at zero. This validator intentionally supports only
the schema keywords used by the bundled schemas and fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parent
SCHEMA_DIR = PACKAGE_ROOT / "schemas"
MODE_STATE_KEYS = {"plan_state", "goal_state", "verify_state", "conv_state"}
NEXT_SAFE_ACTION_REQUIRED = {
    "action_type",
    "summary",
    "risk_class",
    "requires_approval",
    "approval_ref",
    "side_effect_key",
    "idempotency_policy",
    "expected_artifacts",
    "cursor",
}
RISK_CLASSES = {"read_only", "local_files", "repo_changes", "external", "destructive", "gateway_runtime", "public"}
IDEMPOTENCY_POLICIES = {"repeatable", "reconcile_first", "never_repeat_without_approval"}
TERMINAL_CONTINUATION_TARGETS = {"blocked", "complete", "terminal", "stopped"}
ACTIVE_CONTINUATION_STATUSES = {"draft", "running", "waiting_user", "waiting_subagent", "verifying", "blocked"}


class SchemaError(ValueError):
    """Raised when a document does not match a bundled schema."""


def load_schema(name: str) -> dict[str, Any]:
    path = SCHEMA_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"schema not found: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    raise SchemaError(f"unsupported schema type {expected!r}")


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> None:
    expected_type = schema.get("type")
    if isinstance(expected_type, list):
        if not any(_type_matches(instance, item) for item in expected_type):
            raise SchemaError(f"{path}: expected one of {expected_type}, got {type(instance).__name__}")
    elif isinstance(expected_type, str):
        if not _type_matches(instance, expected_type):
            raise SchemaError(f"{path}: expected {expected_type}, got {type(instance).__name__}")

    if "enum" in schema and instance not in schema["enum"]:
        raise SchemaError(f"{path}: {instance!r} is not one of {schema['enum']!r}")

    if isinstance(instance, str) and "minLength" in schema and len(instance) < int(schema["minLength"]):
        raise SchemaError(f"{path}: string is shorter than minLength {schema['minLength']}")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                raise SchemaError(f"{path}: missing required property {key!r}")

        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            extra = sorted(set(instance) - set(properties))
            if extra:
                raise SchemaError(f"{path}: unknown properties {extra!r}")

        for key, value in instance.items():
            if key in properties:
                validate(value, properties[key], f"{path}.{key}")

    if isinstance(instance, list) and "items" in schema:
        item_schema = schema["items"]
        for index, value in enumerate(instance):
            validate(value, item_schema, f"{path}[{index}]")


def validate_named(instance: Any, schema_name: str) -> None:
    validate(instance, load_schema(schema_name))
    if schema_name == "workflow.schema.json":
        _validate_workflow_contract(instance)
    elif schema_name == "checkpoint_state_update.schema.json":
        _validate_checkpoint_state_update_contract(instance)


def _validate_workflow_contract(workflow: dict[str, Any]) -> None:
    kind = workflow.get("kind")
    state_key = f"{kind}_state"
    if state_key not in workflow:
        raise SchemaError(f"$: missing mode state block {state_key!r}")
    foreign_states = sorted(key for key in MODE_STATE_KEYS - {state_key} if key in workflow)
    if foreign_states:
        raise SchemaError(f"$: workflow contains foreign mode state blocks {foreign_states!r}")
    if kind in {"goal", "conv"} and not isinstance(workflow.get("continuation_plan"), dict):
        raise SchemaError("$: goal and conv workflows require continuation_plan")
    if workflow.get("continuation_plan") is not None:
        _validate_continuation_plan(workflow["continuation_plan"], workflow.get("status"))
    validate_next_safe_action(workflow.get("next_safe_action"), "$.next_safe_action")
    _validate_optional_lease(
        workflow.get("active_recovery_lease"),
        "active_recovery_lease",
        {"lease_id", "lease_type", "cursor", "holder", "acquired_at", "lease_expires_at", "checkpoint_id"},
    )
    _validate_optional_lease(
        workflow.get("active_delivery_reservation"),
        "active_delivery_reservation",
        {"reservation_id", "lease_type", "terminal_status", "visible_delivery", "acquired_at", "lease_expires_at", "checkpoint_id"},
    )


def _validate_continuation_plan(plan: Any, status: Any) -> None:
    if not isinstance(plan, dict):
        raise SchemaError("$.continuation_plan: expected object or null")
    required = {
        "plan_id",
        "current_step_index",
        "steps",
        "budgets",
        "stop_conditions",
        "rolling_state",
    }
    missing = sorted(required - set(plan))
    if missing:
        raise SchemaError(f"$.continuation_plan: missing required properties {missing!r}")
    if not isinstance(plan["plan_id"], str) or not plan["plan_id"]:
        raise SchemaError("$.continuation_plan.plan_id: expected non-empty string")
    if not isinstance(plan["current_step_index"], int) or isinstance(plan["current_step_index"], bool):
        raise SchemaError("$.continuation_plan.current_step_index: expected integer")
    if not isinstance(plan["steps"], list) or not plan["steps"]:
        raise SchemaError("$.continuation_plan.steps: expected non-empty array")
    for index, step in enumerate(plan["steps"]):
        _validate_continuation_step(step, index)
    step_ids = [step["step_id"] for step in plan["steps"]]
    if len(step_ids) != len(set(step_ids)):
        raise SchemaError("$.continuation_plan.steps: step_id values must be unique")
    for index, step in enumerate(plan["steps"]):
        next_on_pass = step["next_on_pass"]
        if next_on_pass not in step_ids and next_on_pass not in TERMINAL_CONTINUATION_TARGETS:
            raise SchemaError(f"$.continuation_plan.steps[{index}].next_on_pass: target is not a known step or terminal target")
    budgets = plan["budgets"]
    if not isinstance(budgets, dict):
        raise SchemaError("$.continuation_plan.budgets: expected object")
    for key in ("max_steps_per_wake", "max_rounds", "max_retries_per_step"):
        if not isinstance(budgets.get(key), int) or isinstance(budgets.get(key), bool):
            raise SchemaError(f"$.continuation_plan.budgets.{key}: expected integer")
    if not isinstance(plan["stop_conditions"], list) or not all(isinstance(item, str) and item for item in plan["stop_conditions"]):
        raise SchemaError("$.continuation_plan.stop_conditions: expected array of non-empty strings")
    _validate_rolling_state(plan["rolling_state"])
    if status in ACTIVE_CONTINUATION_STATUSES:
        current_index = plan["current_step_index"]
        if current_index < 0 or current_index >= len(plan["steps"]):
            raise SchemaError("$.continuation_plan.current_step_index: active workflow index is out of range")
        expected_cursor = plan["steps"][current_index]["step_id"]
        actual_cursor = plan["rolling_state"]["current_resume_cursor"]
        if actual_cursor != expected_cursor:
            raise SchemaError("$.continuation_plan.rolling_state.current_resume_cursor: does not match current_step_index step_id")


def _validate_continuation_step(step: Any, index: int) -> None:
    path = f"$.continuation_plan.steps[{index}]"
    if not isinstance(step, dict):
        raise SchemaError(f"{path}: expected object")
    required = {
        "step_id",
        "objective",
        "expected_artifacts",
        "gate",
        "allowed_risk_classes",
        "verification_commands",
        "next_on_pass",
        "next_on_fail",
    }
    missing = sorted(required - set(step))
    if missing:
        raise SchemaError(f"{path}: missing required properties {missing!r}")
    for key in ("step_id", "objective", "next_on_pass", "next_on_fail"):
        if not isinstance(step.get(key), str) or not step[key]:
            raise SchemaError(f"{path}.{key}: expected non-empty string")
    if step["next_on_pass"] == step["step_id"]:
        raise SchemaError(f"{path}.next_on_pass: cannot target the same step")
    for key in ("expected_artifacts", "allowed_risk_classes", "verification_commands"):
        if not isinstance(step.get(key), list) or not all(isinstance(item, str) and item for item in step[key]):
            raise SchemaError(f"{path}.{key}: expected array of non-empty strings")
    if not isinstance(step.get("gate"), dict):
        raise SchemaError(f"{path}.gate: expected object")


def _validate_rolling_state(rolling: Any) -> None:
    if not isinstance(rolling, dict):
        raise SchemaError("$.continuation_plan.rolling_state: expected object")
    required = {
        "completed_steps",
        "open_decisions",
        "evidence_map",
        "residuals",
        "active_child_workflows",
        "current_resume_cursor",
        "last_checkpoint_id",
    }
    missing = sorted(required - set(rolling))
    if missing:
        raise SchemaError(f"$.continuation_plan.rolling_state: missing required properties {missing!r}")
    for key in ("completed_steps", "open_decisions", "active_child_workflows"):
        if not isinstance(rolling.get(key), list):
            raise SchemaError(f"$.continuation_plan.rolling_state.{key}: expected array")
    if not isinstance(rolling.get("evidence_map"), dict):
        raise SchemaError("$.continuation_plan.rolling_state.evidence_map: expected object")
    residuals = rolling.get("residuals")
    if not isinstance(residuals, dict):
        raise SchemaError("$.continuation_plan.rolling_state.residuals: expected object")
    for key in ("blocking_remaining", "accepted_risks", "implementation_backlog", "deferred_scope"):
        if not isinstance(residuals.get(key), list):
            raise SchemaError(f"$.continuation_plan.rolling_state.residuals.{key}: expected array")
    if not isinstance(rolling.get("current_resume_cursor"), str) or not rolling["current_resume_cursor"]:
        raise SchemaError("$.continuation_plan.rolling_state.current_resume_cursor: expected non-empty string")
    if rolling.get("last_checkpoint_id") is not None and not isinstance(rolling["last_checkpoint_id"], str):
        raise SchemaError("$.continuation_plan.rolling_state.last_checkpoint_id: expected string or null")


def _validate_optional_lease(value: Any, field: str, required: set[str]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise SchemaError(f"$.{field}: expected object or null")
    missing = sorted(required - set(value))
    if missing:
        raise SchemaError(f"$.{field}: missing required properties {missing!r}")
    for key in sorted(required):
        if key in {"lease_id", "lease_type", "cursor", "holder", "acquired_at", "lease_expires_at", "checkpoint_id", "reservation_id", "terminal_status"}:
            if not isinstance(value.get(key), str) or not value[key]:
                raise SchemaError(f"$.{field}.{key}: expected non-empty string")
    if field == "active_recovery_lease" and value.get("lease_type") != "recovery":
        raise SchemaError(f"$.{field}.lease_type: expected 'recovery'")
    if field == "active_delivery_reservation" and value.get("lease_type") != "delivery":
        raise SchemaError(f"$.{field}.lease_type: expected 'delivery'")
    if field == "active_delivery_reservation" and not isinstance(value.get("visible_delivery"), dict):
        raise SchemaError(f"$.{field}.visible_delivery: expected object")


def validate_next_safe_action(value: Any, path: str = "$.next_safe_action") -> None:
    if not isinstance(value, dict):
        raise SchemaError(f"{path}: expected object")
    missing = sorted(NEXT_SAFE_ACTION_REQUIRED - set(value))
    if missing:
        raise SchemaError(f"{path}: missing required properties {missing!r}")
    extra = sorted(set(value) - NEXT_SAFE_ACTION_REQUIRED)
    if extra:
        raise SchemaError(f"{path}: unknown properties {extra!r}")
    for key in ("action_type", "summary", "side_effect_key", "idempotency_policy", "cursor"):
        if not isinstance(value.get(key), str) or not value[key]:
            raise SchemaError(f"{path}.{key}: expected non-empty string")
    if value["risk_class"] not in RISK_CLASSES:
        raise SchemaError(f"{path}.risk_class: {value['risk_class']!r} is not a valid risk class")
    if not isinstance(value.get("requires_approval"), bool):
        raise SchemaError(f"{path}.requires_approval: expected boolean")
    if value.get("approval_ref") is not None and not isinstance(value["approval_ref"], str):
        raise SchemaError(f"{path}.approval_ref: expected string or null")
    if value["idempotency_policy"] not in IDEMPOTENCY_POLICIES:
        raise SchemaError(f"{path}.idempotency_policy: {value['idempotency_policy']!r} is not a valid idempotency policy")
    expected_artifacts = value.get("expected_artifacts")
    if not isinstance(expected_artifacts, list) or not all(isinstance(item, str) and item for item in expected_artifacts):
        raise SchemaError(f"{path}.expected_artifacts: expected array of non-empty strings")


def _validate_checkpoint_state_update_contract(update: dict[str, Any]) -> None:
    checkpoint_type = update.get("checkpoint_type")
    event_type = update.get("event_type")
    status_after = update.get("status_after")

    if status_after in {"reported", "abandoned"}:
        raise SchemaError("$.status_after: reported and abandoned require dedicated flow")
    if checkpoint_type == "terminal":
        if event_type not in {"complete", "fail"}:
            raise SchemaError("$.event_type: terminal checkpoints require complete or fail")
        if status_after not in {"completed_unreported", "failed_unreported"}:
            raise SchemaError("$.status_after: terminal checkpoints require an unreported terminal status")
        if event_type == "complete" and status_after != "completed_unreported":
            raise SchemaError("$.status_after: complete checkpoints require completed_unreported")
        if event_type == "fail" and status_after != "failed_unreported":
            raise SchemaError("$.status_after: fail checkpoints require failed_unreported")
        final_status = update.get("final_status")
        if not isinstance(final_status, dict):
            raise SchemaError("$.final_status: terminal checkpoints require final_status")
        verdict = final_status.get("result")
        if not isinstance(verdict, str) or not verdict:
            raise SchemaError("$.final_status: final_status requires result")
        if verdict not in {"pass", "pass_with_risks", "needs_fix", "blocked", "stopped"}:
            raise SchemaError("$.final_status: final_status result has invalid verdict")
        if event_type == "complete" and verdict not in {"pass", "pass_with_risks"}:
            raise SchemaError("$.final_status: complete checkpoints require pass or pass_with_risks")
        if event_type == "fail" and verdict not in {"needs_fix", "blocked", "stopped"}:
            raise SchemaError("$.final_status: fail checkpoints require needs_fix, blocked, or stopped")
    elif event_type in {"complete", "fail"} or status_after in {"completed_unreported", "failed_unreported"}:
        raise SchemaError("$: terminal event/status combinations require checkpoint_type=terminal")
    elif checkpoint_type == "advance" and event_type != "advance":
        raise SchemaError("$.event_type: advance checkpoints require advance event_type")
    elif checkpoint_type == "checkpoint" and event_type != "checkpoint":
        raise SchemaError("$.event_type: checkpoint checkpoints require checkpoint event_type")


def validate_json_file(path: Path, schema_name: str) -> None:
    validate_named(json.loads(path.read_text(encoding="utf-8")), schema_name)


def validate_bundled_schemas() -> list[str]:
    checked: list[str] = []
    for path in sorted(SCHEMA_DIR.glob("*.json")):
        json.loads(path.read_text(encoding="utf-8"))
        checked.append(path.name)
    return checked
