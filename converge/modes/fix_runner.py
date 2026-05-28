"""Coordinator-owned bounded local fix runner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from converge.agents.contracts import (
    SOURCE_FIX_RUNNER,
    STATUS_COMPLETED,
    stable_hash,
    validate_fix_runner_request,
    validate_fix_runner_result,
)


def run_bounded_local_fix_runner(workflow: dict[str, Any], *, source_root: Path) -> dict[str, Any]:
    state = workflow.get("conv_state")
    if not isinstance(state, dict):
        raise ValueError("fix runner requires a conv workflow with conv_state")
    requests = state.get("fix_runner_request_refs")
    if not isinstance(requests, list) or len(requests) != 1:
        raise ValueError("fix runner requires exactly one pending fix_runner request")
    if state.get("fix_runner_result_refs"):
        raise ValueError("fix runner refuses workflows that already have a result")
    request = requests[0]
    validate_fix_runner_request(request)
    if request.get("status") != "pending":
        raise ValueError("fix runner request must be pending")
    workflow_id = workflow.get("workflow_id")
    if request.get("workflow_id") != workflow_id:
        raise ValueError("fix runner request workflow_id must match workflow")

    accepted_changes = request["accepted_change_refs"]
    prepared_edits = []
    focused_checks = []
    root = source_root.resolve()
    for change in accepted_changes:
        change_ref = change["change_ref"]
        edits = change.get("local_file_edits")
        if not isinstance(edits, list) or not edits:
            raise ValueError(f"accepted change {change_ref!r} has no bounded local_file_edits")
        change_prepared = [_prepare_local_edit(root, edit, change_ref=change_ref) for edit in edits]
        prepared_edits.extend(change_prepared)
        focused_checks.append(
            {
                "check_id": f"focused-check-{change_ref}",
                "change_ref": change_ref,
                "status": "pass",
                "summary": f"Validated and applied {len(change_prepared)} bounded local file edit(s).",
            }
        )

    mutations = _apply_prepared_edits(prepared_edits)
    runner_id = request["runner_id"]
    result = {
        "result_id": f"{runner_id}-result",
        "runner_id": runner_id,
        "mode": "conv",
        "workflow_id": workflow_id,
        "source_root": str(root),
        "source_classification": SOURCE_FIX_RUNNER,
        "status": STATUS_COMPLETED,
        "accepted_change_refs": accepted_changes,
        "applied_change_refs": accepted_changes,
        "focused_check_results": focused_checks,
        "material_change_applied": bool(mutations),
        "artifact_refs": [f"{runner_id}-result"],
        "tool_policy": request["tool_policy"],
        "agent_session_ref": None,
        "side_effects_performed": [],
        "file_mutations": mutations,
        "idempotency_key": stable_hash(
            {
                "runner_id": runner_id,
                "workflow_id": workflow_id,
                "source_root": str(root),
                "accepted_change_refs": accepted_changes,
                "file_mutations": mutations,
            }
        ),
    }
    validate_fix_runner_result(result)
    return result


def write_fix_runner_result(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _prepare_local_edit(root: Path, edit: Any, *, change_ref: str) -> dict[str, Any]:
    if not isinstance(edit, dict):
        raise ValueError("local_file_edits entries must be objects")
    rel_path = edit.get("path")
    old = edit.get("old")
    new = edit.get("new")
    if not isinstance(rel_path, str) or not rel_path or rel_path.startswith("/") or ".." in Path(rel_path).parts:
        raise ValueError("local_file_edits path must be a safe relative path")
    if not isinstance(old, str) or old == "":
        raise ValueError("local_file_edits old must be a non-empty string")
    if not isinstance(new, str) or new == old:
        raise ValueError("local_file_edits new must be a changed string")
    target = (root / rel_path).resolve()
    if root != target and root not in target.parents:
        raise ValueError("local_file_edits path escapes source root")
    if not target.is_file():
        raise ValueError(f"local_file_edits target does not exist: {rel_path}")
    before = target.read_text(encoding="utf-8")
    if before.count(old) != 1:
        raise ValueError(f"local_file_edits old text must match exactly once in {rel_path}")
    after = before.replace(old, new, 1)
    return {
        "change_ref": change_ref,
        "path": rel_path,
        "target": target,
        "before": before,
        "after": after,
        "before_sha256": _sha256_text(before),
        "after_sha256": _sha256_text(after),
    }


def _apply_prepared_edits(prepared_edits: list[dict[str, Any]]) -> list[dict[str, str]]:
    applied: list[dict[str, Any]] = []
    try:
        for edit in prepared_edits:
            edit["target"].write_text(edit["after"], encoding="utf-8")
            applied.append(edit)
    except Exception:
        for edit in reversed(applied):
            edit["target"].write_text(edit["before"], encoding="utf-8")
        raise
    return [
        {
            "change_ref": edit["change_ref"],
            "path": edit["path"],
            "before_sha256": edit["before_sha256"],
            "after_sha256": edit["after_sha256"],
        }
        for edit in prepared_edits
    ]


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
