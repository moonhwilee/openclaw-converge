"""Acceptance scope payload helpers."""

from __future__ import annotations

from typing import Any


ACCEPTANCE_SCOPE_FIELDS = ("objective", "non_goals", "success_criteria", "assumptions", "approval_boundaries")
ACCEPTANCE_ARRAY_FIELDS = ("non_goals", "success_criteria", "assumptions", "approval_boundaries")
ACCEPTANCE_STRING_FIELDS = ("plan_artifact_ref", "plan_artifact_hash", "source_ref", "accepted_at")
ACCEPTANCE_REQUIRED_FIELDS = (*ACCEPTANCE_SCOPE_FIELDS, *ACCEPTANCE_STRING_FIELDS)


def validate_acceptance_payload(
    event_type: str,
    payload: dict[str, Any] | None,
    *,
    require_nonempty_objective: bool,
) -> None:
    if not payload:
        raise ValueError(f"{event_type} requires a payload")
    missing = sorted(set(ACCEPTANCE_REQUIRED_FIELDS) - set(payload))
    if missing:
        raise ValueError(f"{event_type} payload missing required fields: {missing!r}")
    objective = payload["objective"]
    if require_nonempty_objective:
        if not isinstance(objective, str) or not objective:
            raise ValueError(f"{event_type} objective must be a non-empty string")
    elif not isinstance(objective, str):
        raise ValueError(f"{event_type} objective must be a string")
    for key in ACCEPTANCE_ARRAY_FIELDS:
        if not isinstance(payload[key], list):
            raise ValueError(f"{event_type} {key} must be an array")
    for key in ACCEPTANCE_STRING_FIELDS:
        if not isinstance(payload[key], str) or not payload[key]:
            raise ValueError(f"{event_type} {key} must be a non-empty string")


def matches_workflow_scope(event: dict[str, Any], workflow: dict[str, Any]) -> bool:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return False
    return all(payload.get(field) == workflow.get(field) for field in ACCEPTANCE_SCOPE_FIELDS)
