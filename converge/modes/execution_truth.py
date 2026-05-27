"""Execution truth marker helpers for Converge mode records."""

from __future__ import annotations


PLAN_ONLY_PATTERNS = (
    "plan-only",
    "planning only",
    "scaffold-only",
    "dry-run only",
    "contract only",
    "계획만",
    "계획 문서 작성만",
    "이번 단계는 계획",
)


def classify_execution_markers(text: str, *, capability: str) -> dict[str, object]:
    """Return schema-compatible execution markers for a mode state."""

    normalized = " ".join((text or "").lower().split())
    explicit_plan_only = any(pattern in normalized for pattern in PLAN_ONLY_PATTERNS)
    if explicit_plan_only:
        return {
            "execution_required": False,
            "execution_capability": "plan_only",
            "execution_performed": False,
            "synthetic_report": True,
            "execution_classification_reason": "explicit plan-only/scaffold-only request",
            "execution_evidence_refs": [],
        }
    return {
        "execution_required": True,
        "execution_capability": capability,
        "execution_performed": False,
        "synthetic_report": True,
        "execution_classification_reason": "execution required by default for managed mode request",
        "execution_evidence_refs": [],
    }
