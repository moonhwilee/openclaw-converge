"""Execution truth marker helpers for Converge mode records."""

from __future__ import annotations


PLAN_ONLY_PREFIXES = (
    "plan-only",
    "planning only",
    "scaffold-only",
    "dry-run only",
    "contract only",
    "계획만",
    "계획 문서 작성만",
    "이번 단계는 계획",
)

PLAN_ONLY_INSTRUCTION_PATTERNS = (
    "this request is plan-only",
    "treat this as plan-only",
    "plan-only로 해",
    "plan-only로 진행",
    "scaffold-only로 해",
    "scaffold-only로 진행",
    "dry-run only로 해",
    "dry-run only로 진행",
    "contract only로 해",
    "contract only로 진행",
    "이번 요청은 plan-only",
    "이번 요청은 계획만",
    "이번 요청은 계획 문서 작성만",
    "구현하지 말고 계획",
    "실행하지 말고 계획",
    "계획만 해",
    "계획만 작성",
)


def _is_explicit_plan_only(normalized: str) -> bool:
    if any(normalized.startswith(pattern) for pattern in PLAN_ONLY_PREFIXES):
        return True
    return any(pattern in normalized for pattern in PLAN_ONLY_INSTRUCTION_PATTERNS)


def classify_execution_markers(text: str, *, capability: str) -> dict[str, object]:
    """Return schema-compatible execution markers for a mode state."""

    normalized = " ".join((text or "").lower().split())
    if _is_explicit_plan_only(normalized):
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
