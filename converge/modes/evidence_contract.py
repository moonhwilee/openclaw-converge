"""Phase 5A terminal evidence contract helpers."""

from __future__ import annotations

from typing import Any

from ..artifacts import now_iso


def attach_phase5a_evidence_contract(
    kind: str,
    *,
    workflow: dict[str, Any],
    state: dict[str, Any],
    terminal_evidence: dict[str, Any] | None,
    terminal_status_override: str | None = None,
) -> dict[str, Any]:
    state = dict(state)
    evidence_entries = [item for item in state.get("evidence") or [] if isinstance(item, dict)]
    if terminal_evidence:
        evidence_entries.append(terminal_evidence)
    evidence_map = _evidence_map(workflow, evidence_entries, state)
    required = _required_gates(kind, state, terminal_evidence)
    accepted_changes = _accepted_change_ids(state)
    state["required_evidence_contract"] = {
        "contract_id": f"{kind}-phase5a-terminal-contract",
        "terminal_status": terminal_status_override or _terminal_status(kind, state, workflow=workflow),
        "required": required,
    }
    state["evidence_map"] = evidence_map
    state["evidence_freshness_status"] = {
        "fresh": True,
        "stale_evidence_refs": [],
        "accepted_change_ids": accepted_changes,
        "last_validated_at": now_iso(),
    }
    return state


def validate_phase5a_evidence_contract(kind: str, *, workflow: dict[str, Any], state: dict[str, Any]) -> None:
    for key in ("required_evidence_contract", "evidence_map", "evidence_freshness_status"):
        if key not in state:
            raise ValueError(f"{kind} state is missing Phase 5A {key}")
    contract = state["required_evidence_contract"]
    evidence_map = state["evidence_map"]
    freshness = state["evidence_freshness_status"]
    if not isinstance(contract, dict) or not isinstance(evidence_map, dict) or not isinstance(freshness, dict):
        raise ValueError(f"{kind} Phase 5A evidence contract fields are invalid")
    if contract.get("terminal_status") != _terminal_status(kind, state, workflow=workflow):
        raise ValueError(f"{kind} Phase 5A terminal status contract is stale")
    current_accepted_changes = _accepted_change_ids(state)
    if freshness.get("accepted_change_ids") != current_accepted_changes:
        raise ValueError(f"{kind} Phase 5A evidence freshness accepted changes are stale")
    if freshness.get("fresh") is not True:
        raise ValueError(f"{kind} Phase 5A evidence freshness is stale")
    if freshness.get("stale_evidence_refs"):
        raise ValueError(f"{kind} Phase 5A stale evidence refs are present")
    for item in contract.get("required") or []:
        if not isinstance(item, dict) or not item.get("required"):
            raise ValueError(f"{kind} Phase 5A required evidence contract item is invalid")
        gate_id = item.get("gate_id")
        entry = evidence_map.get(gate_id) if isinstance(gate_id, str) else None
        if not entry:
            raise ValueError(f"{kind} Phase 5A required evidence gate is missing: {gate_id!r}")
        if entry.get("valid_for_stop_status") is not True:
            raise ValueError(f"{kind} Phase 5A required evidence is not valid for stop: {gate_id!r}")
        if not _required_kind_matches_entry(item.get("evidence_kind"), entry):
            raise ValueError(f"{kind} Phase 5A required evidence kind is stale: {gate_id!r}")
        produced_after = entry.get("produced_after_change_refs") or []
        if any(change_id not in produced_after for change_id in current_accepted_changes):
            raise ValueError(f"{kind} Phase 5A evidence predates accepted material changes: {gate_id!r}")
        stale_if = entry.get("stale_if_change_refs") or []
        if any(change_id in stale_if for change_id in current_accepted_changes):
            raise ValueError(f"{kind} Phase 5A evidence is invalidated by accepted material changes: {gate_id!r}")
        _validate_entry_current(kind, workflow, entry)
    for ref in state.get("execution_evidence_refs") or []:
        if not any(
            isinstance(entry, dict) and (entry.get("artifact_ref") == ref or entry.get("workflow_ref") == ref)
            for entry in evidence_map.values()
        ):
            raise ValueError(f"{kind} Phase 5A execution evidence ref is missing from evidence_map: {ref!r}")


def _required_gates(kind: str, state: dict[str, Any], terminal_evidence: dict[str, Any] | None) -> list[dict[str, Any]]:
    required = []
    if terminal_evidence:
        for ref in terminal_evidence.get("artifact_refs") or []:
            required.append(_required_gate(f"terminal:{ref}", "terminal_evidence"))
    for ref in state.get("execution_evidence_refs") or []:
        required.append(_required_gate(f"execution:{ref}", "execution_evidence"))
    if state.get("review_panel_spec"):
        for ref in state.get("execution_evidence_refs") or []:
            if str(ref).endswith("specialist-findings"):
                required.append(_required_gate(f"specialist:{ref}", "specialist_arbitration"))
    if kind == "goal" and state.get("execution_performed") is True:
        for child in state.get("child_workflow_refs") or []:
            if isinstance(child, dict) and child.get("workflow_id"):
                required.append(_required_gate(f"child:{child['workflow_id']}", "child_workflow"))
    return _dedupe_required(required)


def _evidence_map(workflow: dict[str, Any], evidence_entries: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts = {
        item.get("artifact_id"): item
        for item in workflow.get("artifacts") or []
        if isinstance(item, dict) and isinstance(item.get("artifact_id"), str)
    }
    entries: dict[str, dict[str, Any]] = {}
    for evidence in evidence_entries:
        produced_after = _accepted_change_ids(state)
        for ref in evidence.get("artifact_refs") or []:
            artifact = artifacts.get(ref)
            gate_id = f"terminal:{ref}" if evidence.get("kind") in {"artifact", "report"} else f"evidence:{ref}"
            entries[gate_id] = {
                "gate_id": gate_id,
                "evidence_kind": evidence.get("kind") or "artifact",
                "artifact_ref": ref,
                "artifact_hash_or_revision": artifact.get("sha256") if artifact else f"workflow-ref:{ref}",
                "round_id": state.get("round_index") or state.get("round_count"),
                "produced_after_change_refs": produced_after,
                "valid_for_stop_status": True,
                "stale_if_change_refs": [],
            }
    for ref in state.get("execution_evidence_refs") or []:
        gate_id = f"execution:{ref}"
        if gate_id not in entries:
            artifact = artifacts.get(ref)
            entries[gate_id] = {
                "gate_id": gate_id,
                "evidence_kind": "execution",
                "artifact_ref": ref if artifact else None,
                "workflow_ref": None if artifact else ref,
                "artifact_hash_or_revision": artifact.get("sha256") if artifact else f"workflow-ref:{ref}",
                "round_id": state.get("round_index") or state.get("round_count"),
                "produced_after_change_refs": _accepted_change_ids(state),
                "valid_for_stop_status": True,
                "stale_if_change_refs": [],
            }
        if str(ref).endswith("specialist-findings") and f"specialist:{ref}" not in entries:
            artifact = artifacts.get(ref)
            entries[f"specialist:{ref}"] = {
                "gate_id": f"specialist:{ref}",
                "evidence_kind": "specialist_arbitration",
                "artifact_ref": ref,
                "artifact_hash_or_revision": artifact.get("sha256") if artifact else f"workflow-ref:{ref}",
                "round_id": state.get("round_index") or state.get("round_count"),
                "produced_after_change_refs": _accepted_change_ids(state),
                "valid_for_stop_status": True,
                "stale_if_change_refs": [],
            }
    for child in state.get("child_workflow_refs") or []:
        if isinstance(child, dict) and child.get("workflow_id"):
            gate_id = f"child:{child['workflow_id']}"
            entries[gate_id] = {
                "gate_id": gate_id,
                "evidence_kind": "child_workflow",
                "workflow_ref": child["workflow_id"],
                "artifact_hash_or_revision": child.get("status") or "unknown",
                "round_id": None,
                "produced_after_change_refs": [],
                "valid_for_stop_status": child.get("status") in {"completed", "completed_unreported", "failed_unreported", "reported", "blocked"},
                "stale_if_change_refs": [],
            }
    return entries


def _validate_entry_current(kind: str, workflow: dict[str, Any], entry: dict[str, Any]) -> None:
    artifacts = {
        item.get("artifact_id"): item
        for item in workflow.get("artifacts") or []
        if isinstance(item, dict) and isinstance(item.get("artifact_id"), str)
    }
    artifact_ref = entry.get("artifact_ref")
    if artifact_ref:
        artifact = artifacts.get(artifact_ref)
        if artifact is None:
            raise ValueError(f"{kind} Phase 5A evidence artifact is missing: {artifact_ref!r}")
        if artifact.get("sha256") != entry.get("artifact_hash_or_revision"):
            raise ValueError(f"{kind} Phase 5A evidence artifact hash is stale: {artifact_ref!r}")
    elif entry.get("workflow_ref"):
        if entry.get("artifact_hash_or_revision") == "unknown":
            raise ValueError(f"{kind} Phase 5A workflow evidence revision is unknown: {entry.get('workflow_ref')!r}")
    else:
        raise ValueError(f"{kind} Phase 5A evidence entry lacks artifact_ref or workflow_ref")


def _accepted_change_ids(state: dict[str, Any]) -> list[str]:
    return [
        item.get("change_ref") or item.get("accepted_change_id")
        for item in state.get("accepted_change_refs") or []
        if isinstance(item, dict) and (item.get("change_ref") or item.get("accepted_change_id"))
    ]


def _terminal_status(kind: str, state: dict[str, Any], *, workflow: dict[str, Any]) -> str:
    final_status = workflow.get("final_status")
    if isinstance(final_status, dict) and isinstance(final_status.get("result"), str) and final_status["result"]:
        return final_status["result"]
    if kind == "verify" and state.get("verdict"):
        return str(state["verdict"])
    residuals = state.get("residuals") if isinstance(state.get("residuals"), dict) else {}
    if residuals.get("blocking_remaining"):
        return "blocked"
    if any(residuals.get(key) for key in ("accepted_risks", "implementation_backlog", "deferred_scope")):
        return "pass_with_risks"
    return "pass"


def _required_gate(gate_id: str, evidence_kind: str) -> dict[str, Any]:
    return {"gate_id": gate_id, "evidence_kind": evidence_kind, "required": True}


def _required_kind_matches_entry(required_kind: Any, entry: dict[str, Any]) -> bool:
    if required_kind == "terminal_evidence":
        return isinstance(entry.get("artifact_ref"), str)
    if required_kind == "execution_evidence":
        return entry.get("evidence_kind") in {"execution", "child_workflow"}
    if required_kind == "child_workflow":
        return entry.get("evidence_kind") == "child_workflow"
    if required_kind == "specialist_arbitration":
        return entry.get("evidence_kind") == "specialist_arbitration"
    return False


def _dedupe_required(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for item in items:
        gate_id = item["gate_id"]
        if gate_id not in seen:
            result.append(item)
            seen.add(gate_id)
    return result
