"""Approval boundary helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def approval_matches(approval: dict[str, Any], *, side_effect_key: str, scope: str, now: datetime | None = None) -> bool:
    if approval.get("side_effect_key") != side_effect_key or approval.get("scope") != scope:
        return False
    if approval.get("consumed_by_event_id") is not None:
        return False
    expires_at = _parse_utc(approval.get("expires_at"))
    if expires_at is None:
        return False
    return expires_at > (now or datetime.now(timezone.utc))


def assert_no_risky_side_effect(action: dict[str, Any]) -> None:
    if action.get("risk") in {"external", "destructive", "gateway", "public"}:
        raise ValueError("risky side effects require explicit approval in a later slice")
