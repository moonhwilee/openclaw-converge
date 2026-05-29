"""Small risk and intent classifier for native panel selection."""

from __future__ import annotations

from dataclasses import dataclass


HIGH_RISK_TERMS = {
    "deploy",
    "release",
    "gateway",
    "restart",
    "external",
    "email",
    "push",
    "pr",
    "delete",
    "remove",
    "credential",
    "secret",
    "finance",
    "payment",
}
SPECIALIST_INTENT_TERMS = {
    "specialist",
    "panel",
    "review",
    "검증",
    "수렴",
    "꼼꼼",
}
MATERIAL_CHANGE_TERMS = {
    "fix",
    "repair",
    "improve",
    "implement",
    "mutation",
    "change",
    "고쳐",
    "수정",
    "개선",
    "구현",
}


@dataclass(frozen=True)
class PanelDecision:
    requires_panel: bool
    panel_size: int
    risk_level: str
    material_change_intent: bool
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "requires_panel": self.requires_panel,
            "panel_size": self.panel_size,
            "risk_level": self.risk_level,
            "material_change_intent": self.material_change_intent,
            "reason": self.reason,
        }


def classify_panel_decision(text: str, *, mode: str) -> PanelDecision:
    lower = text.lower()
    tokens = {token.strip(".,:;!?()[]{}\"'") for token in lower.split()}
    high_risk = any(_matches(term, lower, tokens) for term in HIGH_RISK_TERMS)
    explicit_panel = any(_matches(term, lower, tokens) for term in SPECIALIST_INTENT_TERMS)
    material_change = any(_matches(term, lower, tokens) for term in MATERIAL_CHANGE_TERMS)
    if high_risk:
        return PanelDecision(True, 5, "high", material_change, "high-risk terms require a five-specialist panel")
    if explicit_panel or (mode == "conv" and material_change):
        return PanelDecision(True, 3, "medium", material_change, "specialist or material-change intent requires native panel evidence")
    return PanelDecision(False, 3, "low", material_change, "deterministic local checks may satisfy this request")


def _matches(term: str, lower: str, tokens: set[str]) -> bool:
    if term.isascii() and term.isalpha() and len(term) <= 3:
        return term in tokens
    return term in lower
