"""Trusted deterministic execution helpers for conv mode."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..artifacts import now_iso, sha256_file


CONV_ROUND_EXECUTION_ARTIFACT_ID = "conv-round-execution"
CONV_LOCAL_RUNNER_REF = "trusted-local-conv-round-runner-v1"
MAX_TARGETS = 5
ABSOLUTE_PATH_RE = re.compile(r"/[^\s'\"`<>]+")
RELATIVE_PATH_RE = re.compile(r"(?:\.{1,2}/)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
TRAILING_PUNCTUATION = ".,;:)］】}>\"'"
MATERIAL_WORK_TERMS = (
    "repair",
    "fix",
    "improve",
    "implement",
    "apply",
    "modify",
    "edit",
    "change",
    "patch",
    "개선",
    "구현",
    "수정",
    "고쳐",
    "적용",
)
READ_ONLY_TERMS = (
    "read-only",
    "readonly",
    "audit",
    "inspect",
    "review",
    "검토",
    "점검",
    "감사",
)


def run_conv_round_execution(text: str, *, source_root: Path) -> dict[str, Any]:
    """Inspect concrete local targets and record a minimal real conv round."""

    started_at = now_iso()
    if _requires_material_runner(text):
        completed_at = now_iso()
        return {
            "runner_ref": CONV_LOCAL_RUNNER_REF,
            "execution_started_at": started_at,
            "execution_completed_at": completed_at,
            "rounds": [],
            "summary": "Material repair/improve convergence requires specialist or fix-runner evidence, not local file inspection only.",
        }
    target_checks = []
    for path in _referenced_files(text, source_root=source_root):
        stat = path.stat()
        target_checks.append(
            {
                "check_id": f"target-inspection-{len(target_checks) + 1}",
                "kind": "file_inspection",
                "status": "pass",
                "path": str(path),
                "sha256": sha256_file(path),
                "size_bytes": stat.st_size,
            }
        )
        if len(target_checks) >= MAX_TARGETS:
            break
    rounds: list[dict[str, Any]] = []
    if target_checks:
        rounds.append(
            {
                "round_index": 1,
                "target_ref": "original-target",
                "original_target_gate": "within_original_target",
                "delta_gate": "no_delta",
                "findings": [
                    {
                        "finding_id": "conv-local-target-inspection",
                        "summary": f"Trusted local conv runner inspected {len(target_checks)} concrete target file(s).",
                        "novelty": "none",
                        "severity": "none",
                        "objective_impact": "none",
                        "evidence_quality": "direct",
                        "disposition": "accepted_risk",
                        "material_change_required": False,
                    }
                ],
                "material_changes": False,
                "follow_up_required": False,
                "evidence_sufficient": True,
                "summary": "Round 1 inspected the original target and found no accepted material delta.",
                "target_checks": target_checks,
            }
        )
    completed_at = now_iso()
    return {
        "runner_ref": CONV_LOCAL_RUNNER_REF,
        "execution_started_at": started_at,
        "execution_completed_at": completed_at,
        "rounds": rounds,
        "summary": (
            f"Ran {len(rounds)} trusted local conv round(s) against {len(target_checks)} concrete file target(s)."
            if rounds
            else "No concrete local file reference was available for conv round execution."
        ),
    }


def conv_execution_markers(result: dict[str, Any], *, artifact_id: str) -> dict[str, Any]:
    if not result.get("rounds"):
        return {}
    return {
        "execution_capability": "local_rounds",
        "execution_performed": True,
        "synthetic_report": False,
        "runner_ref": result["runner_ref"],
        "execution_evidence_refs": [artifact_id],
        "execution_started_at": result["execution_started_at"],
        "execution_completed_at": result["execution_completed_at"],
        "execution_classification_reason": "trusted deterministic conv runner recorded round evidence",
    }


def conv_round_evidence_record(result: dict[str, Any], *, artifact_id: str) -> dict[str, Any] | None:
    if not result.get("rounds"):
        return None
    return {
        "evidence_key": "conv-round-execution",
        "kind": "round_execution",
        "summary": result["summary"],
        "artifact_refs": [artifact_id],
    }


def write_conv_round_execution_artifact(path: Path, result: dict[str, Any]) -> None:
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


def _requires_material_runner(text: str) -> bool:
    normalized = (text or "").casefold()
    if any(term in normalized for term in READ_ONLY_TERMS):
        return False
    return any(term in normalized for term in MATERIAL_WORK_TERMS)


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


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
