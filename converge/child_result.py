"""Child result collection helpers.

The collection layer should be forgiving about child output shape while the
existing proof validators remain strict about accepting completed results.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .agents.openclaw_cli import NativePanelBlockedError
from .artifacts import now_iso


def record_blocked_child_result_artifact(
    handler: Any,
    workflow_id: str,
    *,
    mode: str,
    error: NativePanelBlockedError,
) -> dict[str, Any]:
    """Persist raw blocked child output and a minimal normalized envelope."""

    request_id = error.blocked_request_id
    artifact_id = f"{mode}-blocked-child-result-{_safe_fragment(request_id)}"
    artifact_path = handler.store.workflow_dir(workflow_id) / "artifacts" / f"{artifact_id}.json"
    envelope = build_blocked_child_result_envelope(
        mode=mode,
        request_id=request_id,
        session_key=error.blocked_session_key,
        reason=error.reason,
        message=error.message,
        raw_stdout=error.raw_stdout,
        raw_stderr=error.raw_stderr,
        partial_results=[item.as_dict() for item in error.partial_results],
    )
    workflow = handler.load_workflow(workflow_id)
    existing = [
        artifact
        for artifact in workflow.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("artifact_id") == artifact_id
    ]
    if existing:
        if len(existing) > 1:
            raise ValueError(f"duplicate blocked child result artifact id: {artifact_id}")
        artifact = existing[0]
        path = Path(str(artifact.get("path", ""))).expanduser().resolve()
        if path != artifact_path.expanduser().resolve() or not path.is_file():
            raise ValueError("blocked child result artifact path is invalid")
    else:
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        artifact = handler.record_artifact(
            workflow_id,
            kind="evidence",
            artifact_id=artifact_id,
            path=artifact_path,
            note="raw blocked child result collection envelope",
        )["artifact"]
    return {
        "status": envelope["status"],
        "parse_status": envelope["parse_status"],
        "blocked_reason": error.reason,
        "blocked_message": error.message,
        "request_id": request_id,
        "session_key": error.blocked_session_key,
        "raw_ref": artifact["artifact_id"],
        "artifact_path": artifact["path"],
        "preliminary_finding_count": len(envelope["preliminary_findings"]),
    }


def build_blocked_child_result_envelope(
    *,
    mode: str,
    request_id: str,
    session_key: str,
    reason: str,
    message: str,
    raw_stdout: str,
    raw_stderr: str,
    partial_results: list[dict[str, Any]],
) -> dict[str, Any]:
    parsed = _extract_jsonish_payload(raw_stdout)
    preliminary_findings = []
    if isinstance(parsed, dict) and isinstance(parsed.get("findings"), list):
        preliminary_findings = [item for item in parsed["findings"] if isinstance(item, dict)]
    parse_status = "parsed" if isinstance(parsed, dict) else "raw_only"
    return {
        "schema_version": 1,
        "kind": "child_result_collection_envelope",
        "status": "unaccepted_preliminary",
        "mode": mode,
        "request_id": request_id,
        "session_key": session_key,
        "blocked_reason": reason,
        "blocked_message": message,
        "parse_status": parse_status,
        "summary": "Child output was captured but not accepted because strict proof validation failed.",
        "evidence": [],
        "risks": [
            "Preliminary child output is advisory only until existing proof validators accept it.",
        ],
        "remaining_or_blockers": [message or reason],
        "preliminary_findings": preliminary_findings,
        "partial_results": partial_results,
        "raw": {
            "stdout": raw_stdout,
            "stderr": raw_stderr,
        },
        "captured_at": now_iso(),
    }


def _extract_jsonish_payload(text: str) -> dict[str, Any] | None:
    if not text or not text.strip():
        return None
    candidates = [text.strip()]
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    candidates.extend(item.strip() for item in fenced)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            for key in ("response", "reply", "message", "content", "text", "output"):
                value = payload.get(key)
                if isinstance(value, dict):
                    return value
                if isinstance(value, str):
                    nested = _extract_jsonish_payload(value)
                    if nested is not None:
                        return nested
            result = payload.get("result")
            if isinstance(result, dict):
                for key in ("finalAssistantRawText", "finalAssistantVisibleText"):
                    value = result.get(key)
                    if isinstance(value, str):
                        nested = _extract_jsonish_payload(value)
                        if nested is not None:
                            return nested
            return payload
    return None


def _safe_fragment(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    safe = safe.strip("-") or "unknown"
    return safe[:96]
