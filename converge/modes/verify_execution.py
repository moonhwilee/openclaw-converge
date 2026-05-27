"""Trusted deterministic execution helpers for verify mode."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..artifacts import now_iso, sha256_file


VERIFY_DETERMINISTIC_CHECK_ARTIFACT_ID = "verify-deterministic-checks"
VERIFY_LOCAL_RUNNER_REF = "trusted-local-verify-file-inspection-v1"
MAX_FILE_CHECKS = 5
ABSOLUTE_PATH_RE = re.compile(r"/[^\s'\"`<>]+")
RELATIVE_PATH_RE = re.compile(r"(?:\.{1,2}/)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
TRAILING_PUNCTUATION = ".,;:)］】}>\"'"


def run_verify_deterministic_checks(text: str, *, source_root: Path) -> dict[str, Any]:
    """Inspect concrete local files referenced by a verify request."""

    started_at = now_iso()
    checks = []
    for path in _referenced_files(text, source_root=source_root):
        stat = path.stat()
        checks.append(
            {
                "check_id": f"file-inspection-{len(checks) + 1}",
                "kind": "file_inspection",
                "status": "pass",
                "path": str(path),
                "sha256": sha256_file(path),
                "size_bytes": stat.st_size,
            }
        )
        if len(checks) >= MAX_FILE_CHECKS:
            break
    completed_at = now_iso()
    return {
        "runner_ref": VERIFY_LOCAL_RUNNER_REF,
        "execution_started_at": started_at,
        "execution_completed_at": completed_at,
        "checks": checks,
        "summary": (
            f"Inspected {len(checks)} local file reference(s)."
            if checks
            else "No concrete local file reference was available for deterministic inspection."
        ),
    }


def deterministic_check_summaries(result: dict[str, Any]) -> list[str]:
    checks = result.get("checks") or []
    if not checks:
        return []
    return [
        f"{item['kind']} passed for {item['path']} (sha256={item['sha256']}, size={item['size_bytes']} bytes)"
        for item in checks
    ]


def deterministic_evidence_record(result: dict[str, Any], *, artifact_id: str) -> dict[str, Any] | None:
    checks = result.get("checks") or []
    if not checks:
        return None
    return {
        "evidence_key": "verify-deterministic-checks",
        "kind": "deterministic_check",
        "summary": result["summary"],
        "artifact_refs": [artifact_id],
    }


def deterministic_execution_markers(result: dict[str, Any], *, artifact_id: str) -> dict[str, Any]:
    checks = result.get("checks") or []
    if not checks:
        return {}
    return {
        "execution_capability": "local_checks",
        "execution_performed": True,
        "synthetic_report": False,
        "runner_ref": result["runner_ref"],
        "execution_evidence_refs": [artifact_id],
        "execution_started_at": result["execution_started_at"],
        "execution_completed_at": result["execution_completed_at"],
        "execution_classification_reason": "trusted deterministic verify runner recorded target-specific evidence",
    }


def write_deterministic_check_artifact(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _referenced_files(text: str, *, source_root: Path) -> list[Path]:
    source_root = source_root.expanduser().resolve()
    seen: set[Path] = set()
    paths: list[Path] = []
    for token in _path_tokens(text):
        candidate = _resolve_candidate(token, source_root=source_root)
        if candidate is None or candidate in seen:
            continue
        seen.add(candidate)
        paths.append(candidate)
    return paths


def _path_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for pattern in (ABSOLUTE_PATH_RE, RELATIVE_PATH_RE):
        tokens.extend(match.group(0).rstrip(TRAILING_PUNCTUATION) for match in pattern.finditer(text or ""))
    return [token for token in tokens if token]


def _resolve_candidate(token: str, *, source_root: Path) -> Path | None:
    path = Path(token).expanduser()
    if not path.is_absolute():
        path = source_root / path
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.is_file():
        return None
    if _is_relative_to(resolved, source_root) or Path(token).expanduser().is_absolute():
        return resolved
    return None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False
