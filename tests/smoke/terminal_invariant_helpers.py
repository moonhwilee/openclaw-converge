"""Reusable smoke helpers for terminal finalization invariants."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from smoke_helpers import VISIBLE_DELIVERY, assert_true, events, run, run_fail, workflow, write_workflow
except ModuleNotFoundError:
    from tests.smoke.smoke_helpers import VISIBLE_DELIVERY, assert_true, events, run, run_fail, workflow, write_workflow


def finalize_mode(state_root: Path, *, kind: str, workflow_id: str) -> dict[str, Any]:
    return run(
        kind,
        "--text",
        f"Terminal invariant fixture for {kind}",
        "--workflow-id",
        workflow_id,
        "--owner-session-key",
        "session:test",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        state_root=state_root,
    )["workflow"]


def reserve_delivery(state_root: Path, workflow_payload: dict[str, Any]) -> dict[str, Any]:
    return run(
        "reserve-delivery",
        "--workflow-id",
        workflow_payload["workflow_id"],
        "--terminal-status",
        "completed",
        "--visible-delivery",
        VISIBLE_DELIVERY,
        "--summary",
        "reserve terminal invariant delivery",
        "--final-status",
        json.dumps(workflow_payload["final_status"]),
        state_root=state_root,
    )


def artifact_path(workflow_payload: dict[str, Any], *, state_key: str, path_key: str) -> Path:
    return Path(workflow_payload[state_key][path_key])
