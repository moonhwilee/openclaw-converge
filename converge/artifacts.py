"""Artifact and context-manifest helpers."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import validate_named


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_entry(path: Path, *, recovery_policy: str = "block_on_change") -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return {
        "kind": "file",
        "ref": str(resolved),
        "captured_at": now_iso(),
        "hash": sha256_file(resolved),
        "recovery_policy": recovery_policy,
    }


def validate_manifest_entry(entry: dict[str, Any]) -> bool:
    if not isinstance(entry, dict):
        return False
    kind = entry.get("kind")
    ref = entry.get("ref")
    recovery_policy = entry.get("recovery_policy")
    if kind not in {"file", "url", "artifact", "user_text", "external_ref"}:
        return False
    if not isinstance(ref, str) or not ref:
        return False
    if not isinstance(entry.get("captured_at"), str) or not entry["captured_at"]:
        return False
    if recovery_policy not in {"block_on_change", "revalidate_on_change", "accept_mutable"}:
        return False
    if recovery_policy == "accept_mutable":
        return True
    if not isinstance(entry.get("hash"), str) or not entry["hash"]:
        return False
    if kind != "file":
        return True
    path = Path(ref)
    return path.exists() and sha256_file(path) == entry["hash"]


def workflow_artifact(
    *,
    artifact_id: str | None,
    kind: str,
    path: Path,
    created_at: str | None = None,
) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"artifact path must be an existing file: {resolved}")
    artifact = {
        "artifact_id": artifact_id or f"art-{uuid.uuid4().hex[:12]}",
        "kind": kind,
        "path": str(resolved),
        "created_at": created_at or now_iso(),
        "sha256": sha256_file(resolved),
    }
    validate_named(artifact, "artifact.schema.json")
    return artifact


def register_workflow_artifact(
    workflow: dict[str, Any],
    *,
    artifact_id: str | None,
    kind: str,
    path: Path,
    created_at: str | None = None,
) -> dict[str, Any]:
    artifact = workflow_artifact(
        artifact_id=artifact_id,
        kind=kind,
        path=path,
        created_at=created_at,
    )
    artifacts = workflow.setdefault("artifacts", [])
    if any(item.get("artifact_id") == artifact["artifact_id"] for item in artifacts):
        raise ValueError(f"artifact already exists: {artifact['artifact_id']}")
    artifacts.append(artifact)
    return artifact


def record_workflow_artifact(
    store: Any,
    *,
    workflow_id: str,
    artifact_id: str | None,
    kind: str,
    path: Path,
    note: str = "",
) -> dict[str, Any]:
    """Register a materialized artifact through the shared workflow write path."""

    event_id = f"evt-artifact-{uuid.uuid4().hex[:8]}"
    artifact_path = path.expanduser().resolve()
    with store.lock(workflow_id):
        store.require_no_pending_checkpoint(workflow_id)
        store.require_no_pending_recovery(workflow_id)
        workflow = store.load_workflow(workflow_id)
        if workflow.get("status") in {"completed_unreported", "failed_unreported", "reported", "abandoned"}:
            raise ValueError(f"artifact cannot update workflow in terminal status: {workflow.get('status')!r}")
        artifact = register_workflow_artifact(
            workflow,
            artifact_id=artifact_id,
            kind=kind,
            path=artifact_path,
        )
        store.append_event(
            workflow_id,
            {
                "schema_version": 1,
                "event_id": event_id,
                "workflow_id": workflow_id,
                "event_type": "artifact",
                "created_at": artifact["created_at"],
                "note": note,
                "payload": {"artifact": artifact},
            },
            locked=True,
        )
        store.save_workflow(workflow)
    return {"workflow_id": workflow_id, "artifact": artifact, "event_id": event_id}
