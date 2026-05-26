"""Synthetic command dry-run adapter for C7.

This module deliberately does not register slash routes, observe live traffic,
or create workflows. It only converts a managed user-facing command into the
Converge CLI invocation that a later approved routing layer may use.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


COMMAND_RE = re.compile(r"^/(?P<command>goal|verify|conv|converge)(?:\s+(?P<text>[\s\S]*))?$")


@dataclass(frozen=True)
class CommandSurface:
    command: str
    current_owner: str
    c7_owner: str
    state_root: str
    delivery_behavior: str
    rollback_switch: str
    transitional_behavior: str
    final_behavior: str

    def as_dict(self) -> dict[str, str]:
        return {
            "command": self.command,
            "current_owner": self.current_owner,
            "c7_owner": self.c7_owner,
            "state_root": self.state_root,
            "delivery_behavior": self.delivery_behavior,
            "rollback_switch": self.rollback_switch,
            "transitional_behavior": self.transitional_behavior,
            "final_behavior": self.final_behavior,
        }


COMMAND_INVENTORY: tuple[CommandSurface, ...] = (
    CommandSurface(
        command="/goal",
        current_owner="GoalFlow exact trigger plus scripts/goalflow_start_goal.py draft intake.",
        c7_owner="converge goal",
        state_root="Legacy GoalFlow state during C7.0; future Converge workflow state after approved live routing.",
        delivery_behavior="Draft and confirmation first; visible completion remains bound to the original Telegram delivery route.",
        rollback_switch="Keep existing /goal route until owner-approved replacement; disable C7 adapter route to fall back.",
        transitional_behavior="Synthetic dry-run only; preserves draft/confirmation gates without live route changes.",
        final_behavior="New managed /goal work creates Converge goal workflows after separate live-routing approval.",
    ),
    CommandSurface(
        command="/verify",
        current_owner="verification-convergence skill audit path.",
        c7_owner="converge verify",
        state_root="Legacy verification-convergence artifacts during C7.0; future Converge workflow state after approved live routing.",
        delivery_behavior="One visible audit report through the original delivery route after evidence/report material is reserved.",
        rollback_switch="Keep existing /verify handler until owner-approved replacement; disable C7 adapter route to fall back.",
        transitional_behavior="Synthetic dry-run only; no live observation, duplicate report, or shadow routing.",
        final_behavior="New managed /verify work records evidence, residuals, report material, and proof in Converge.",
    ),
    CommandSurface(
        command="/conv",
        current_owner="verification-convergence skill repair/improvement path.",
        c7_owner="converge conv",
        state_root="Legacy verification-convergence artifacts during C7.0; future Converge workflow state after approved live routing.",
        delivery_behavior="Round summaries and final report through the original delivery route; material changes need follow-up proof.",
        rollback_switch="Keep existing /conv handler until owner-approved replacement; disable C7 adapter route to fall back.",
        transitional_behavior="Synthetic dry-run only; verifies round metadata route shape without live replacement.",
        final_behavior="New managed /conv work records convergence rounds and recovery cursor state in Converge.",
    ),
    CommandSurface(
        command="/converge",
        current_owner="legacy alias for /conv.",
        c7_owner="temporary alias to converge conv, or retirement message",
        state_root="No independent state root; alias must reuse /conv state or retire.",
        delivery_behavior="No independent delivery contract; alias maps to /conv dry-run and is marked deprecated.",
        rollback_switch="Retire alias or keep explicit message only; never make it the primary route.",
        transitional_behavior="Synthetic dry-run marks the alias deprecated and maps it to conv without promoting it.",
        final_behavior="Retired, or replaced with a clear /conv/Converge message.",
    ),
)


def inventory() -> list[dict[str, str]]:
    return [surface.as_dict() for surface in COMMAND_INVENTORY]


def build_dry_run_packet(
    *,
    raw_message: str,
    owner_session_key: str = "",
    visible_delivery: dict[str, Any] | None = None,
    workflow_id: str | None = None,
    state_root: Path | None = None,
) -> dict[str, Any]:
    command, text = parse_raw_message(raw_message)
    mode = "conv" if command == "converge" else command
    delivery = visible_delivery or {}
    converge_argv = build_converge_argv(
        mode=mode,
        text=text,
        owner_session_key=owner_session_key,
        visible_delivery=delivery,
        workflow_id=workflow_id,
        state_root=state_root,
    )
    return {
        "schema_version": "converge.command_dry_run.v0.1",
        "ok": True,
        "dry_run": True,
        "live_route_changed": False,
        "live_traffic_observed": False,
        "shadow_routing_enabled": False,
        "workflow_created": False,
        "external_action_performed": False,
        "gateway_restart_required": False,
        "legacy_data_deleted": False,
        "input": {
            "raw_message": raw_message,
            "command": f"/{command}",
            "text": text,
        },
        "route": {
            "current_command": f"/{command}",
            "converge_mode": mode,
            "alias_status": "deprecated_alias" if command == "converge" else "primary",
            "owner_session_key": owner_session_key,
            "visible_delivery": delivery,
            "state_root": str(state_root) if state_root else None,
        },
        "converge_invocation": {
            "argv": converge_argv,
            "display": " ".join(_shell_quote(part) for part in converge_argv),
        },
        "inventory": inventory(),
        "blocked_without_approval": [
            "Gateway restart",
            "live traffic observation",
            "shadow routing",
            "live route replacement",
            "deploy/apply/install",
            "external action",
            "legacy data deletion",
            "push/PR/release",
        ],
    }


def parse_raw_message(raw_message: str) -> tuple[str, str]:
    match = COMMAND_RE.match(raw_message.strip())
    if not match:
        raise ValueError("raw message must start with /goal, /verify, /conv, or /converge")
    command = match.group("command")
    text = (match.group("text") or "").strip()
    if not text:
        raise ValueError(f"/{command} dry-run requires non-empty text")
    return command, text


def build_converge_argv(
    *,
    mode: str,
    text: str,
    owner_session_key: str,
    visible_delivery: dict[str, Any],
    workflow_id: str | None,
    state_root: Path | None,
) -> list[str]:
    argv = ["converge"]
    if state_root is not None:
        argv.extend(["--state-root", str(state_root)])
    argv.extend([mode, "--text", text])
    if workflow_id:
        argv.extend(["--workflow-id", workflow_id])
    if owner_session_key:
        argv.extend(["--owner-session-key", owner_session_key])
    if visible_delivery:
        argv.extend(["--visible-delivery", json.dumps(visible_delivery, ensure_ascii=False, sort_keys=True)])
    return argv


def _shell_quote(value: str) -> str:
    if not value:
        return "''"
    if re.fullmatch(r"[A-Za-z0-9_./:=@+-]+", value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"
