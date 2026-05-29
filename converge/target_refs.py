"""Target reference manifest support for native child inspections."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .agents.contracts import DEFAULT_BUDGET_POLICY, NATIVE_INLINE_TARGET_MAX_BYTES, NATIVE_INLINE_TARGET_MAX_LINES


MAX_TARGET_REFS = 50
INLINE_TARGET_KINDS = {"verify_target", "conv_target"}
MANIFEST_TARGET_KINDS = {"file"}
DEFAULT_CONVERGE_TARGET_REF_PATHS = {
    "conv": [
        ("converge/modes/conv.py", "mode"),
        ("converge/agents/openclaw_cli.py", "native-launch"),
        ("converge/target_refs.py", "target-refs"),
        ("tests/smoke/converge_agent_contracts_smoke.py", "native-proof-smoke"),
    ],
    "verify": [
        ("converge/modes/verify.py", "mode"),
        ("converge/modes/specialist_panel.py", "native-panel"),
        ("converge/target_refs.py", "target-refs"),
    ],
}
CONVERGE_COMPARISON_TARGET_REF_PATHS = [
    ("package", "converge/agents/openclaw_cli.py", "native-launch"),
    ("package", "converge/recovery.py", "recovery"),
    ("package", "converge/target_refs.py", "target-refs"),
    ("package", "converge/modes/verify.py", "verify-mode"),
    ("package", "converge/modes/conv.py", "conv-mode"),
    ("package", "converge/modes/goal.py", "goal-mode"),
    ("package", "converge/modes/specialist_panel.py", "native-panel"),
    ("package", "converge/modes/evidence_contract.py", "evidence-contract"),
    ("package", "tests/smoke/converge_terminal_finalization_smoke.py", "report-proof-smoke"),
    ("workspace", "docs/context/goalflow.md", "legacy-goalflow"),
    ("workspace", "skills/verification-convergence/SKILL.md", "legacy-verify-conv"),
]


def load_target_refs_file(path: str | Path | None, *, source_root: Path | None = None) -> list[dict[str, Any]]:
    if not path:
        return []
    manifest_path = Path(path).expanduser()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("target refs manifest must be a JSON object")
    if payload.get("schema_version") != 1:
        raise ValueError("target refs manifest schema_version must be 1")
    refs = payload.get("target_refs")
    if not isinstance(refs, list) or not refs:
        raise ValueError("target refs manifest requires non-empty target_refs")
    return validate_target_refs(refs, source_root=source_root)


def validate_target_refs(refs: list[Any], *, source_root: Path | None = None) -> list[dict[str, Any]]:
    if len(refs) > MAX_TARGET_REFS:
        raise ValueError(f"target refs manifest cannot contain more than {MAX_TARGET_REFS} refs")
    root = (source_root or Path.cwd()).expanduser().resolve()
    normalized: list[dict[str, Any]] = []
    total_bytes = 0
    for item in refs:
        if not isinstance(item, dict):
            raise ValueError("target refs manifest entries must be objects")
        if item.get("kind") != "file":
            raise ValueError("target refs manifest currently supports only kind=file")
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("target refs file entries require non-empty path")
        rel_path = Path(raw_path)
        if rel_path.is_absolute() or ".." in rel_path.parts:
            raise ValueError("target refs file paths must be relative and cannot contain '..'")
        resolved = (root / rel_path).resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ValueError("target refs file path escapes source_root") from exc
        if not resolved.is_file():
            raise ValueError(f"target refs file path does not exist: {raw_path}")
        total_bytes += resolved.stat().st_size
        if total_bytes > int(DEFAULT_BUDGET_POLICY["max_input_bytes"]):
            raise ValueError("target refs manifest exceeds native panel max_input_bytes")
        normalized_ref: dict[str, Any] = {
            "kind": "file",
            "path": rel_path.as_posix(),
            "source_root": str(root),
        }
        role = item.get("role")
        if isinstance(role, str) and role:
            normalized_ref["role"] = role
        normalized.append(normalized_ref)
    return normalized


def default_converge_target_refs(mode: str, *, source_root: Path | None = None, target: str | None = None) -> list[dict[str, Any]]:
    """Return a bounded default file-ref set for broad Converge self-review."""
    if mode not in DEFAULT_CONVERGE_TARGET_REF_PATHS:
        raise ValueError("default converge target refs mode must be verify or conv")
    root = resolve_converge_source_root(source_root)
    if target and _is_legacy_comparison_target(target):
        return _comparison_target_refs(package_root=root)
    return _target_refs_for_paths(DEFAULT_CONVERGE_TARGET_REF_PATHS[mode], source_root=root)


def resolve_converge_source_root(preferred: Path | None = None) -> Path:
    """Resolve the installed/source Converge root instead of trusting caller cwd."""
    candidates: list[Path] = []
    env_root = os.environ.get("OPENCLAW_CONVERGE_SOURCE_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    if preferred is not None:
        candidates.append(preferred)
    candidates.append(Path.cwd())
    candidates.append(Path(__file__).resolve().parents[1])
    for candidate in candidates:
        root = candidate.expanduser().resolve()
        if (root / "converge" / "target_refs.py").is_file():
            return root
    return (preferred or Path.cwd()).expanduser().resolve()


def _target_refs_for_paths(paths: list[tuple[str, str]], *, source_root: Path) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for rel_path, role in paths:
        if (source_root / rel_path).is_file():
            refs.append({"kind": "file", "path": rel_path, "source_root": str(source_root), "role": role})
    if not refs:
        return []
    return validate_target_refs(refs, source_root=source_root)


def _comparison_target_refs(*, package_root: Path) -> list[dict[str, Any]]:
    workspace_root = Path(os.environ.get("OPENCLAW_WORKSPACE_ROOT", Path.home() / ".openclaw" / "workspace")).expanduser().resolve()
    refs: list[dict[str, Any]] = []
    total_bytes = 0
    for root_kind, rel_path, role in CONVERGE_COMPARISON_TARGET_REF_PATHS:
        root = package_root if root_kind == "package" else workspace_root
        path = root / rel_path
        if not path.is_file():
            continue
        total_bytes += path.stat().st_size
        if total_bytes > int(DEFAULT_BUDGET_POLICY["max_input_bytes"]):
            break
        refs.append({"kind": "file", "path": rel_path, "source_root": str(root), "role": role})
    return refs


def _is_legacy_comparison_target(target: str) -> bool:
    lowered = target.lower()
    return any(token in lowered for token in ("verification-convergence", "legacy", "과거 verify", "과거 verification", "goalflow"))


def merge_inline_target_ref(
    mode: str,
    text: str,
    refs: list[dict[str, Any]] | None = None,
    *,
    source_root: Path | None = None,
) -> list[dict[str, Any]]:
    if mode not in {"verify", "conv"}:
        raise ValueError("inline target mode must be verify or conv")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("inline target requires non-empty text")
    _validate_inline_target_text(text)
    root = (source_root or Path.cwd()).expanduser().resolve()
    merged: list[dict[str, Any]] = [{"kind": f"{mode}_target", "text": text, "source_root": str(root)}]
    for item in refs or []:
        if not isinstance(item, dict):
            raise ValueError("target refs entries must be objects")
        kind = item.get("kind")
        if kind in INLINE_TARGET_KINDS:
            raise ValueError("manifest target refs must not contain inline target refs")
        if kind not in MANIFEST_TARGET_KINDS:
            raise ValueError("manifest target refs currently supports only kind=file")
        raw_path = item.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("manifest file refs require non-empty path")
        ref = dict(item)
        source_root_value = ref.get("source_root")
        if not isinstance(source_root_value, str) or not source_root_value:
            ref["source_root"] = str(root)
        merged.append(ref)
    return merged


def _validate_inline_target_text(text: str) -> None:
    if len(text.encode("utf-8")) > NATIVE_INLINE_TARGET_MAX_BYTES:
        raise ValueError("inline target is too large; store documents as files and pass refs")
    if text.count("\n") + 1 > NATIVE_INLINE_TARGET_MAX_LINES:
        raise ValueError("inline target has too many lines; store documents as files and pass refs")
